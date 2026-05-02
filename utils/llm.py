"""Unified LLM calling layer — OpenAI SDK only, PDF via Cloudflare R2 URL.

OpenAI models  -> responses API  (input_text / input_file + file_url)
Other models   -> chat API       (text / file + file_data URL)

Requests use the shared `PROXY_PORT` configuration from `.env`.
Automatic retry with exponential backoff + jitter (up to 20 attempts).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import mimetypes
import random
import time
import uuid
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any
from urllib.parse import quote

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from openai import (
    AsyncOpenAI,
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)

from utils.config import (
    get_botocore_proxies,
    get_httpx_client_kwargs,
    load_runtime_config,
    resolve_model,
)
from utils.logger import get_logger

log = get_logger(__name__)

_MAX_RETRIES = 20
_BASE_DELAY = 2.0
_MAX_DELAY = 30.0
_PDF_STEP_MAX_FAILURES = 5
_DEFAULT_PDF_STEP_TIMEOUT = 900.0
_RETRY_WARNING_START_ATTEMPT = 3
_RETRYABLE_HTTP_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_PDF_READINESS_POLL_INTERVAL = 12.0
_PDF_READINESS_MAX_WAIT = 180.0
_PDF_READINESS_MAX_ATTEMPTS = 3
_REMOTE_PDF_ERROR_MARKERS = (
    "timeout while downloading",
    "timed out while downloading",
    "cannot fetch content from the provided url",
    "url_unreachable",
    "failed to download",
    "unable to download",
    # API reports the PDF has zero pages — typically caused by a corrupted or
    # incomplete R2 object (e.g. after a force-stopped upload).  Re-uploading
    # with a fresh key resolves CDN / cache staleness.
    "the document has no pages",
    "document has no pages",
)
_TRANSIENT_PDF_PERMISSION_MARKERS = (
    "you have no permission to access this resource",
    "access_denied",
)

# Concurrency limiter: at most 2 heavy (PDF) calls at the same time
_pdf_semaphore = asyncio.Semaphore(2)

# ---------------------------------------------------------------------------
#  Cloudflare R2 upload — permanent public URL for the PDF
# ---------------------------------------------------------------------------

_ACTIVE_R2_JOB_ID: ContextVar[str | None] = ContextVar("r2_active_job_id", default=None)

_r2_cache: dict[str, str] = {}
_r2_upload_locks: dict[str, asyncio.Lock] = {}
_r2_provider_ready_urls: set[str] = set()
_r2_client: Any | None = None
_r2_client_signature: tuple[str, ...] | None = None


class ModelStepExhaustedError(RuntimeError):
    """A single model has failed too many times within one pipeline step."""

    def __init__(self, model_alias: str, failures: int, last_error: Exception) -> None:
        self.model_alias = model_alias
        self.failures = failures
        self.last_error = last_error
        super().__init__(
            f"{model_alias} 在当前步骤中连续失败 {failures} 次，已放弃该模型："
            f"{type(last_error).__name__}: {last_error}"
        )


def _ensure_r2_config() -> None:
    providers = load_runtime_config().get("providers", {})
    r2_provider = providers.get("r2", {})
    missing = [
        name
        for name, value in (
            ("R2_ENDPOINT", r2_provider.get("endpoint")),
            ("R2_BUCKET", r2_provider.get("bucket")),
            ("R2_ACCESS_KEY_ID", r2_provider.get("access_key_id")),
            ("R2_SECRET_ACCESS_KEY", r2_provider.get("secret_access_key")),
            ("R2_PUBLIC_BASE_URL", r2_provider.get("public_base_url")),
        )
        if not value
    ]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Cloudflare R2 配置缺失：{joined}")


def _normalized_r2_endpoint() -> str:
    r2_provider = load_runtime_config().get("providers", {}).get("r2", {})
    endpoint = str(r2_provider.get("endpoint") or "").rstrip("/")
    bucket = str(r2_provider.get("bucket") or "").strip("/")
    bucket_suffix = f"/{bucket}"
    if bucket and endpoint.endswith(bucket_suffix):
        return endpoint[: -len(bucket_suffix)]
    return endpoint


def _r2_bucket_name() -> str:
    return str(
        load_runtime_config().get("providers", {}).get("r2", {}).get("bucket") or ""
    )


def _get_r2_client() -> Any:
    global _r2_client, _r2_client_signature, _r2_cache
    r2_provider = load_runtime_config().get("providers", {}).get("r2", {})
    signature = (
        str(r2_provider.get("endpoint") or ""),
        str(r2_provider.get("bucket") or ""),
        str(r2_provider.get("access_key_id") or ""),
        str(r2_provider.get("secret_access_key") or ""),
        str(r2_provider.get("public_base_url") or ""),
        str(get_botocore_proxies()),
    )
    if _r2_client is None or _r2_client_signature != signature:
        _ensure_r2_config()
        _r2_cache = {}
        _r2_client = boto3.client(
            service_name="s3",
            endpoint_url=_normalized_r2_endpoint(),
            aws_access_key_id=str(r2_provider.get("access_key_id") or ""),
            aws_secret_access_key=str(r2_provider.get("secret_access_key") or ""),
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                proxies=get_botocore_proxies(),
                retries={"max_attempts": 5, "mode": "adaptive"},
                s3={"addressing_style": "path"},
            ),
        )
        _r2_client_signature = signature
    return _r2_client


def _sha256_file(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with file_path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def set_r2_job_context(job_id: str | None):
    return _ACTIVE_R2_JOB_ID.set(job_id)


def reset_r2_job_context(token: Token[str | None]) -> None:
    _ACTIVE_R2_JOB_ID.reset(token)


def _active_r2_job_id() -> str | None:
    return _ACTIVE_R2_JOB_ID.get()


def _job_r2_object_key(job_id: str) -> str:
    return f"jobs/{job_id}/paper.pdf"


def _legacy_r2_key_prefix(file_path: Path) -> str:
    digest = _sha256_file(file_path)[:16]
    return f"papers/{digest}"


def _r2_cache_key(file_path: Path) -> str:
    job_id = _active_r2_job_id()
    scope = job_id or "global"
    return f"{scope}::{file_path.resolve()}"


def _get_r2_upload_lock(cache_key: str) -> asyncio.Lock:
    lock = _r2_upload_locks.get(cache_key)
    if lock is None:
        lock = asyncio.Lock()
        _r2_upload_locks[cache_key] = lock
    return lock


def _build_r2_object_key(file_path: Path, *, force_reupload: bool) -> str:
    job_id = _active_r2_job_id()
    if job_id:
        # One remote PDF object per job. New jobs always get a new object key,
        # while in-place retry keeps the same job_id and therefore the same
        # remote slot.
        return _job_r2_object_key(job_id)

    digest = _sha256_file(file_path)[:16]
    suffix = file_path.suffix.lower() or ".pdf"
    if force_reupload:
        return f"papers/{digest}-{uuid.uuid4().hex[:8]}{suffix}"
    return f"papers/{digest}{suffix}"


def _build_r2_public_url(object_key: str) -> str:
    r2_provider = load_runtime_config().get("providers", {}).get("r2", {})
    public_base_url = str(r2_provider.get("public_base_url") or "").rstrip("/")
    return f"{public_base_url}/{quote(object_key, safe='/')}"


def _generate_presigned_url(object_key: str, expires_in: int = 3600) -> str:
    """Generate a pre-signed S3 URL that bypasses the public CDN."""
    return _get_r2_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": _r2_bucket_name(), "Key": object_key},
        ExpiresIn=expires_in,
    )


def _delete_r2_object(object_key: str) -> None:
    """Delete an R2 object, ignoring missing-key errors."""
    try:
        _get_r2_client().delete_object(Bucket=_r2_bucket_name(), Key=object_key)
    except ClientError as exc:
        error_code = str(exc.response.get("Error", {}).get("Code", "")).lower()
        if error_code in {"nosuchkey", "404", "notfound"}:
            return
        raise


def _list_incomplete_multipart_uploads(
    *, prefix: str | None = None
) -> list[dict[str, Any]]:
    """List incomplete multipart uploads, optionally filtered by key prefix."""
    client = _get_r2_client()
    uploads: list[dict[str, Any]] = []
    key_marker: str | None = None
    upload_id_marker: str | None = None

    while True:
        kwargs: dict[str, Any] = {"Bucket": _r2_bucket_name()}
        if prefix:
            kwargs["Prefix"] = prefix
        if key_marker:
            kwargs["KeyMarker"] = key_marker
        if upload_id_marker:
            kwargs["UploadIdMarker"] = upload_id_marker

        response = client.list_multipart_uploads(**kwargs)
        uploads.extend(
            upload
            for upload in response.get("Uploads", [])
            if upload.get("Key") and upload.get("UploadId")
        )
        if not response.get("IsTruncated"):
            break

        key_marker = response.get("NextKeyMarker")
        upload_id_marker = response.get("NextUploadIdMarker")
        if not key_marker:
            break

    return uploads


def _abort_incomplete_multipart_uploads(
    *, object_key: str | None = None, prefix: str | None = None
) -> int:
    """Abort incomplete multipart uploads for one key or a key prefix."""
    filter_prefix = object_key or prefix
    uploads = _list_incomplete_multipart_uploads(prefix=filter_prefix)
    if object_key is not None:
        uploads = [upload for upload in uploads if upload.get("Key") == object_key]

    client = _get_r2_client()
    aborted = 0
    for upload in uploads:
        key = upload.get("Key")
        upload_id = upload.get("UploadId")
        if not key or not upload_id:
            continue
        try:
            client.abort_multipart_upload(
                Bucket=_r2_bucket_name(),
                Key=key,
                UploadId=upload_id,
            )
            aborted += 1
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", "")).lower()
            if error_code in {"nosuchupload", "404", "notfound"}:
                continue
            raise

    return aborted


def _delete_legacy_r2_objects(file_path: Path) -> None:
    """Delete legacy content-hash-based R2 objects for the same PDF.

    Older versions stored uploads at keys like ``papers/<digest>.pdf`` and
    ``papers/<digest>-<nonce>.pdf``. New jobs should start fresh instead of
    inheriting those remote artifacts, so we delete the whole prefix before
    uploading the new job-scoped object.
    """
    prefix = _legacy_r2_key_prefix(file_path)
    client = _get_r2_client()
    continuation_token: str | None = None

    while True:
        kwargs: dict[str, Any] = {"Bucket": _r2_bucket_name(), "Prefix": prefix}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        response = client.list_objects_v2(**kwargs)
        contents = response.get("Contents", [])
        if contents:
            client.delete_objects(
                Bucket=_r2_bucket_name(),
                Delete={
                    "Objects": [
                        {"Key": item["Key"]} for item in contents if item.get("Key")
                    ],
                    "Quiet": True,
                },
            )
        if not response.get("IsTruncated"):
            break
        continuation_token = response.get("NextContinuationToken")


def prepare_r2_job_run(job_id: str) -> None:
    """Clear cached URLs for a job before starting a new pipeline run.

    New jobs naturally use a new object key because the key is scoped by
    ``job_id``. Retrying the same job must refresh the remote PDF instead of
    reusing a stale cached URL, so we clear only this job's local cache here.
    """
    prefix = f"{job_id}::"
    stale_keys = [key for key in _r2_cache if key.startswith(prefix)]
    for cache_key in stale_keys:
        _r2_cache.pop(cache_key, None)
    stale_ready_urls = [url for url in _r2_provider_ready_urls if f"/jobs/{job_id}/" in url]
    for url in stale_ready_urls:
        _r2_provider_ready_urls.discard(url)
    stale_locks = [key for key in _r2_upload_locks if key.startswith(prefix)]
    for cache_key in stale_locks:
        _r2_upload_locks.pop(cache_key, None)


def cleanup_r2_job_artifacts(job_id: str) -> None:
    """Delete the job-scoped remote PDF and clear local cache entries."""
    prepare_r2_job_run(job_id)
    object_key = _job_r2_object_key(job_id)
    try:
        aborted = _abort_incomplete_multipart_uploads(object_key=object_key)
        if aborted:
            log.warning(
                "Aborted %s incomplete R2 multipart upload(s) for job %s",
                aborted,
                job_id,
            )
    except Exception as exc:
        log.warning(
            "Failed to abort incomplete multipart uploads for %s: %s",
            job_id,
            exc,
        )

    try:
        _delete_r2_object(object_key)
    except Exception as exc:
        log.warning("Failed to delete job-scoped R2 object for %s: %s", job_id, exc)


def cleanup_stale_multipart_uploads() -> int:
    """Abort stale multipart uploads left behind by interrupted processes."""
    try:
        _ensure_r2_config()
    except RuntimeError as exc:
        log.info("Skipping stale R2 multipart cleanup during startup: %s", exc)
        return 0

    total_aborted = 0
    for prefix in ("jobs/", "papers/"):
        aborted = _abort_incomplete_multipart_uploads(prefix=prefix)
        if aborted:
            log.warning(
                "Aborted %s stale incomplete R2 multipart upload(s) under prefix %s",
                aborted,
                prefix,
            )
            total_aborted += aborted

    return total_aborted


# Whether to prefer pre-signed URLs.  Starts as ``None`` (unknown) and is
# set to ``True`` after the first public-URL probe failure, or ``False``
# after a successful probe.  This avoids probing every single upload.
_prefer_presigned: bool | None = None
_R2_PROBE_TIMEOUT = 15.0
_R2_PROBE_MAX_RETRIES = 2
_R2_PROBE_RETRY_DELAY = 2.0
_PDF_MAGIC = b"%PDF"


def _prefer_presigned_urls(reason: str) -> None:
    """Switch the current process to pre-signed R2 URLs.

    Local probing can only prove that *this* machine can read the public
    `r2.dev` URL. When the upstream model provider still reports remote PDF
    fetch failures (for example `The document has no pages`), the stronger
    signal is that the *provider* cannot read that public URL correctly.
    In that case we switch the whole process to pre-signed S3 URLs and clear
    any cached public URLs so the next retry uses the new transport.
    """
    global _prefer_presigned

    if _prefer_presigned is True:
        return

    _prefer_presigned = True
    _r2_cache.clear()
    log.warning(
        "Switching R2 transport to pre-signed URLs for this session: %s",
        reason,
    )


def _prefer_public_urls(reason: str) -> None:
    """Switch the current process to public R2 URLs.

    Some upstream providers can read the r2.dev public URL but fail on
    pre-signed `r2.cloudflarestorage.com` links due to network or policy
    differences. When we observe repeated permission-denied errors on
    pre-signed URLs, flip back to public transport for this process.
    """
    global _prefer_presigned

    if _prefer_presigned is False:
        return

    _prefer_presigned = False
    _r2_cache.clear()
    log.warning(
        "Switching R2 transport back to public URLs for this session: %s",
        reason,
    )


def _upload_pdf_to_r2(file_path: Path, object_key: str) -> None:
    local_size = file_path.stat().st_size
    if local_size == 0:
        raise ValueError(f"Refusing to upload empty PDF: {file_path}")

    client = _get_r2_client()
    with file_path.open("rb") as file_obj:
        client.put_object(
            Body=file_obj,
            Bucket=_r2_bucket_name(),
            Key=object_key,
            ContentLength=local_size,
            ContentType="application/pdf",
        )

    # Verify the upload: HEAD the object and compare sizes to catch
    # truncated / corrupted uploads early.
    try:
        head = client.head_object(Bucket=_r2_bucket_name(), Key=object_key)
        remote_size = head.get("ContentLength", 0)
        if remote_size != local_size:
            raise RuntimeError(
                f"R2 upload size mismatch for {object_key}: "
                f"local={local_size}, remote={remote_size}"
            )
    except (BotoCoreError, ClientError) as exc:
        log.warning("R2 head_object check failed (upload may still be valid): %s", exc)


async def _probe_public_url(url: str) -> bool:
    """Verify the R2 public URL actually serves a valid PDF.

    Makes a small-range GET (first 16 bytes) and checks for the ``%PDF``
    magic bytes.  Retries a few times to tolerate CDN propagation delay.
    Returns ``True`` if the URL serves a PDF, ``False`` otherwise.
    """
    for attempt in range(_R2_PROBE_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(_R2_PROBE_TIMEOUT, connect=10),
                **get_httpx_client_kwargs(),
            ) as probe_client:
                resp = await probe_client.get(
                    url,
                    headers={"Range": "bytes=0-15"},
                )
                if resp.status_code in (200, 206):
                    if resp.content[:4] == _PDF_MAGIC:
                        return True
                    log.warning(
                        "R2 public URL probe: unexpected content (first bytes: %r)",
                        resp.content[:16],
                    )
                    return False
                log.warning("R2 public URL probe: HTTP %d", resp.status_code)
        except Exception as exc:
            log.warning("R2 public URL probe failed (attempt %d): %s", attempt + 1, exc)
        if attempt < _R2_PROBE_MAX_RETRIES:
            await asyncio.sleep(_R2_PROBE_RETRY_DELAY)
    return False


def invalidate_r2_cache() -> None:
    """Clear the in-memory R2 URL cache.

    Useful after a force-stop to ensure the next pipeline run re-uploads
    PDFs instead of reusing potentially stale URLs.
    """
    global _prefer_presigned
    _r2_cache.clear()
    _r2_upload_locks.clear()
    _r2_provider_ready_urls.clear()
    _prefer_presigned = None


async def upload_to_r2(file_path: Path, *, force_reupload: bool = False) -> str:
    """Upload a PDF to Cloudflare R2 and return an accessible URL (cached).

    After uploading, probes the public R2 URL to verify the API proxy can
    actually fetch the PDF.  If the public URL is unreachable (e.g. CDN
    propagation delay, geo-restriction, domain blocked), automatically
    falls back to a pre-signed S3 URL that bypasses the public CDN.
    """
    global _prefer_presigned

    cache_key = _r2_cache_key(file_path)
    lock = _get_r2_upload_lock(cache_key)

    async with lock:
        if not force_reupload and cache_key in _r2_cache:
            return _r2_cache[cache_key]

        if force_reupload:
            log.info("Re-uploading %s to Cloudflare R2 ...", file_path.name)
        else:
            log.info("Uploading %s to Cloudflare R2 ...", file_path.name)

        object_key = _build_r2_object_key(file_path, force_reupload=force_reupload)
        active_job_id = _active_r2_job_id()
        if active_job_id is not None:
            # Job-scoped uploads must always start from a clean remote object so
            # a new job never reuses previous PDF data, while retry of the same
            # job refreshes the same remote slot explicitly.
            try:
                await asyncio.to_thread(_delete_legacy_r2_objects, file_path)
            except Exception as exc:
                log.warning(
                    "Failed to delete legacy R2 objects before upload for job %s: %s",
                    active_job_id,
                    exc,
                )
            try:
                await asyncio.to_thread(_delete_r2_object, object_key)
            except Exception as exc:
                log.warning(
                    "Failed to delete existing job-scoped R2 object before upload for job %s: %s",
                    active_job_id,
                    exc,
                )
        try:
            aborted = await asyncio.to_thread(
                _abort_incomplete_multipart_uploads,
                object_key=object_key,
            )
            if aborted:
                log.warning(
                    "Aborted %s incomplete R2 multipart upload(s) before uploading %s",
                    aborted,
                    object_key,
                )
        except Exception as exc:
            log.warning(
                "Failed to abort incomplete multipart uploads before uploading %s: %s",
                object_key,
                exc,
            )
        await asyncio.to_thread(_upload_pdf_to_r2, file_path, object_key)

        # Determine which URL type to use.
        if _prefer_presigned is True:
            # Previous probe already determined public URL is broken.
            url = _generate_presigned_url(object_key)
            log.info("Uploaded to R2 (pre-signed): %s", url[:80] + "...")
        else:
            public_url = _build_r2_public_url(object_key)
            if _prefer_presigned is None:
                # First upload — probe the public URL to check accessibility.
                if await _probe_public_url(public_url):
                    _prefer_presigned = False
                    url = public_url
                    log.info("Uploaded to R2 (public OK): %s", url)
                else:
                    _prefer_presigned = True
                    url = _generate_presigned_url(object_key)
                    log.warning(
                        "R2 public URL unreachable — switching to pre-signed URLs for this session. "
                        "Pre-signed: %s",
                        url[:80] + "...",
                    )
            else:
                # _prefer_presigned is False — public URL known to work.
                url = public_url
                log.info("Uploaded to R2: %s", url)

        _r2_cache[cache_key] = url
        return url


# ---------------------------------------------------------------------------
#  OpenAI SDK async client (shared, with proxy)
# ---------------------------------------------------------------------------

_client: AsyncOpenAI | None = None
_client_signature: tuple[str, ...] | None = None


def _build_client() -> AsyncOpenAI:
    providers = load_runtime_config().get("providers", {})
    openai_provider = providers.get("openai", {})
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(600, connect=30), **get_httpx_client_kwargs()
    )
    return AsyncOpenAI(
        base_url=str(openai_provider.get("base_url") or ""),
        api_key=str(openai_provider.get("api_key") or ""),
        http_client=http_client,
        max_retries=0,
    )


def _get_client() -> AsyncOpenAI:
    global _client, _client_signature
    providers = load_runtime_config().get("providers", {})
    openai_provider = providers.get("openai", {})
    signature = (
        str(openai_provider.get("base_url") or ""),
        str(openai_provider.get("api_key") or ""),
        str(get_httpx_client_kwargs()),
    )
    if _client is None or _client_signature != signature:
        _client = _build_client()
        _client_signature = signature
    return _client


_lite_client: AsyncOpenAI | None = None
_lite_client_signature: tuple[str, ...] | None = None


def _build_lite_client() -> AsyncOpenAI:
    providers = load_runtime_config().get("providers", {})
    lite_provider = providers.get("lite", {})
    base_url = str(lite_provider.get("base_url") or "")
    api_key = str(lite_provider.get("api_key") or "")
    if not base_url or not api_key:
        raise RuntimeError("LITE_BASE_URL / LITE_API_KEY 未配置，无法创建 Lite 客户端")
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(600, connect=30), **get_httpx_client_kwargs()
    )
    return AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        http_client=http_client,
        max_retries=0,
    )


def _get_lite_client() -> AsyncOpenAI:
    global _lite_client, _lite_client_signature
    providers = load_runtime_config().get("providers", {})
    lite_provider = providers.get("lite", {})
    base_url = str(lite_provider.get("base_url") or "")
    api_key = str(lite_provider.get("api_key") or "")
    signature = (base_url, api_key, str(get_httpx_client_kwargs()))
    if _lite_client is None or _lite_client_signature != signature:
        _lite_client = _build_lite_client()
        _lite_client_signature = signature
    return _lite_client


def _encode_image_to_data_uri(image_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(str(image_path))
    if not mime_type:
        mime_type = "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------


def _is_openai_model(model: str) -> bool:
    """Return True for models that should use the responses API."""
    return model.lower().startswith("openai/")


def _backoff(attempt: int) -> float:
    """Exponential backoff with full jitter, capped at _MAX_DELAY."""
    delay = min(_BASE_DELAY * (2**attempt), _MAX_DELAY)
    return random.uniform(0, delay)


def _error_text(exc: Exception) -> str:
    return str(exc).lower()


def _bad_request_body(exc: BadRequestError) -> Any:
    return getattr(exc, "body", None)


def _response_input_stats(input_items: list[dict[str, Any]]) -> dict[str, int]:
    item_count = len(input_items)
    text_block_count = 0
    total_text_chars = 0
    max_text_chars = 0
    for item in input_items:
        for content in item.get("content", []) if isinstance(item, dict) else []:
            if not isinstance(content, dict) or content.get("type") != "input_text":
                continue
            text = str(content.get("text", ""))
            text_block_count += 1
            total_text_chars += len(text)
            max_text_chars = max(max_text_chars, len(text))
    return {
        "item_count": item_count,
        "text_block_count": text_block_count,
        "total_text_chars": total_text_chars,
        "max_text_chars": max_text_chars,
    }


def _has_opaque_invalid_params(exc: Exception) -> bool:
    if not isinstance(exc, BadRequestError):
        return False
    joined = _error_text(exc)
    body = _bad_request_body(exc)
    if body is not None:
        joined = f"{joined} {str(body).lower()}"
    return "invalid_params" in joined


def _is_remote_pdf_error(exc: Exception) -> bool:
    text = _error_text(exc)
    return any(marker in text for marker in _REMOTE_PDF_ERROR_MARKERS)


def _is_transient_pdf_permission_error(exc: Exception) -> bool:
    if not isinstance(exc, PermissionDeniedError):
        return False
    text = _error_text(exc)
    return any(marker in text for marker in _TRANSIENT_PDF_PERMISSION_MARKERS)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
        return True
    if isinstance(exc, InternalServerError):
        return True
    return False


def _should_retry_pdf_call(exc: Exception) -> bool:
    if _is_retryable(exc):
        return True
    if _is_transient_pdf_permission_error(exc):
        return True
    if isinstance(exc, BotoCoreError):
        return True
    if isinstance(exc, ClientError):
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return status in _RETRYABLE_HTTP_STATUS or status is None
    if isinstance(exc, httpx.RequestError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_HTTP_STATUS
    if isinstance(exc, BadRequestError) and _is_remote_pdf_error(exc):
        return True
    return _is_remote_pdf_error(exc)


def _remaining_budget(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return deadline - time.monotonic()


def _ensure_budget(deadline: float | None, *, action: str) -> None:
    remaining = _remaining_budget(deadline)
    if remaining is not None and remaining <= 0:
        raise TimeoutError(f"{action} 已超出当前步骤预算")


def _build_pdf_readiness_probe_input(pdf_url: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Reply with exactly OK."},
                {"type": "input_file", "file_url": pdf_url},
            ],
        }
    ]


async def _wait_for_provider_pdf_readiness(
    pdf_url: str,
    *,
    deadline: float | None,
    client: AsyncOpenAI,
    probe_model: str,
) -> None:
    if pdf_url in _r2_provider_ready_urls:
        return

    if not _is_openai_model(probe_model):
        log.info(
            "Skipping PDF readiness probe for non-OpenAI model: %s",
            probe_model,
        )
        return

    remaining = _remaining_budget(deadline)
    if remaining is not None and remaining <= 0:
        raise TimeoutError("PDF readiness probe budget exhausted before probing")
    probe_budget = _PDF_READINESS_MAX_WAIT if remaining is None else min(_PDF_READINESS_MAX_WAIT, remaining)
    probe_deadline = time.monotonic() + max(probe_budget, 0.0)
    probe_input = _build_pdf_readiness_probe_input(pdf_url)
    last_error: Exception | None = None
    attempt = 0

    while attempt < _PDF_READINESS_MAX_ATTEMPTS:
        attempt += 1
        try:
            text = await _call_responses(
                probe_model,
                probe_input,
                temperature=0.0,
                max_tokens=8,
                client=client,
            )
            normalized = text.strip().upper()
            if normalized and normalized != "OK":
                log.info(
                    "PDF readiness probe returned non-OK content but remote read succeeded on attempt %d: %s",
                    attempt,
                    normalized[:40],
                )
            _r2_provider_ready_urls.add(pdf_url)
            if attempt > 1:
                log.info(
                    "Provider-side PDF readiness confirmed after %d attempts",
                    attempt,
                )
            return
        except Exception as exc:
            last_error = exc
            if not (
                _is_remote_pdf_error(exc)
                or _is_transient_pdf_permission_error(exc)
            ):
                raise

            if attempt >= _PDF_READINESS_MAX_ATTEMPTS:
                log.error(
                    "Provider-side PDF readiness failed after %d attempts: %s",
                    attempt,
                    exc,
                )
                break

            remaining_probe = probe_deadline - time.monotonic()
            if remaining_probe <= 0:
                break

            delay = min(_PDF_READINESS_POLL_INTERVAL, max(remaining_probe, 0.0))
            log.info(
                "Waiting for provider-side PDF readiness (attempt %d, retry in %.1fs): %s",
                attempt,
                delay,
                exc,
            )
            if delay > 0:
                await asyncio.sleep(delay)

    if last_error is not None:
        raise last_error
    raise TimeoutError("PDF readiness probe timed out without a provider error")


# ---------------------------------------------------------------------------
#  Core callers with retry
# ---------------------------------------------------------------------------


def _coerce_chat_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text_value = item.get("text")
                if isinstance(text_value, str):
                    parts.append(text_value)
        return "\n".join(part for part in parts if part)
    if content is None:
        return ""
    return str(content)


def _map_response_format_to_text_format(
    response_format: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if response_format is None:
        return None

    response_type = response_format.get("type")
    if response_type == "json_object":
        return {"type": "json_object"}
    if response_type != "json_schema":
        return None

    schema_payload = response_format.get("json_schema")
    if not isinstance(schema_payload, dict):
        schema_payload = response_format

    mapped: dict[str, Any] = {"type": "json_schema"}
    name = schema_payload.get("name")
    if isinstance(name, str) and name.strip():
        mapped["name"] = name.strip()
    schema = schema_payload.get("schema")
    if isinstance(schema, dict):
        mapped["schema"] = schema
    if "strict" in schema_payload:
        mapped["strict"] = bool(schema_payload.get("strict"))
    description = schema_payload.get("description")
    if isinstance(description, str) and description.strip():
        mapped["description"] = description.strip()
    return mapped


async def _call_chat(
    model: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.4,
    max_tokens: int = 16384,
    response_format: dict[str, Any] | None = None,
    client: AsyncOpenAI | None = None,
) -> str:
    """Call the chat completions API (for non-OpenAI models like Gemini)."""
    client = client or _get_client()
    last_err: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            request_kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if response_format is not None:
                request_kwargs["response_format"] = response_format
            resp = await client.chat.completions.create(**request_kwargs)
            return _coerce_chat_message_content(resp.choices[0].message.content)
        except Exception as e:
            last_err = e
            if _is_retryable(e):
                delay = _backoff(attempt)
                if attempt + 1 >= _RETRY_WARNING_START_ATTEMPT:
                    log.warning(
                        "[chat] %s (attempt %d/%d, retry in %.1fs)",
                        type(e).__name__,
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                    )
                await asyncio.sleep(delay)
            else:
                raise

    if last_err is not None:
        raise last_err
    raise RuntimeError(
        f"[chat] All {_MAX_RETRIES} attempts failed without exception details"
    )


async def _call_responses(
    model: str,
    input_items: list[dict[str, Any]],
    *,
    temperature: float = 0.4,
    max_tokens: int = 16384,
    text_format: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    client: AsyncOpenAI | None = None,
) -> str:
    """Call the responses API (for OpenAI models like GPT)."""
    client = client or _get_client()
    last_err: Exception | None = None
    invalid_params_retry_count = 0
    input_stats = _response_input_stats(input_items)

    for attempt in range(_MAX_RETRIES):
        try:
            request_kwargs: dict[str, Any] = {
                "model": model,
                "input": input_items,
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            }
            if text_format is not None:
                request_kwargs["text"] = {"format": text_format}
            if tools:
                request_kwargs["tools"] = tools
            if tool_choice is not None:
                request_kwargs["tool_choice"] = tool_choice
            resp = await client.responses.create(**request_kwargs)
            for item in resp.output:
                if item.type == "message":
                    for content in item.content:
                        if content.type == "output_text":
                            return content.text
            return ""
        except Exception as e:
            last_err = e
            if isinstance(e, BadRequestError):
                log.warning(
                    "[responses] BadRequestError for %s: %s | items=%d text_blocks=%d total_chars=%d max_block_chars=%d max_output_tokens=%d text_format=%s tools=%d tool_choice=%s body=%s",
                    model,
                    e,
                    input_stats["item_count"],
                    input_stats["text_block_count"],
                    input_stats["total_text_chars"],
                    input_stats["max_text_chars"],
                    max_tokens,
                    bool(text_format),
                    len(tools or []),
                    tool_choice,
                    _bad_request_body(e),
                )
                if _has_opaque_invalid_params(e) and invalid_params_retry_count < 1:
                    invalid_params_retry_count += 1
                    delay = min(_backoff(attempt), 2.0)
                    log.warning(
                        "[responses] Opaque invalid_params from %s; retrying once in %.1fs because the same payload may succeed on replay.",
                        model,
                        delay,
                    )
                    if delay > 0:
                        await asyncio.sleep(delay)
                    continue
            if _is_retryable(e):
                delay = _backoff(attempt)
                if attempt + 1 >= _RETRY_WARNING_START_ATTEMPT:
                    log.warning(
                        "[responses] %s (attempt %d/%d, retry in %.1fs)",
                        type(e).__name__,
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                    )
                await asyncio.sleep(delay)
            else:
                raise

    if last_err is not None:
        raise last_err
    raise RuntimeError(
        f"[responses] All {_MAX_RETRIES} attempts failed without exception details"
    )


# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------


async def call_llm(
    model_alias: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.4,
    max_tokens: int = 16384,
    deadline: float | None = None,
    response_format: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
) -> str:
    """Call an LLM with text-only messages (no PDF)."""
    model = resolve_model(model_alias)
    log.info("Calling %s (%s) ...", model_alias, model)
    _ensure_budget(deadline, action=f"{model_alias} 文本调用")

    if _is_openai_model(model):
        input_items: list[dict[str, Any]] = []
        for msg in messages:
            content = msg.get("content", "")
            if msg["role"] == "system":
                input_items.append(
                    {
                        "role": "developer",
                        "content": [{"type": "input_text", "text": content}],
                    }
                )
            else:
                input_items.append(
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": content}],
                    }
                )
        text_format = _map_response_format_to_text_format(response_format)
        coro = _call_responses(
            model,
            input_items,
            temperature=temperature,
            max_tokens=max_tokens,
            text_format=text_format,
            tools=tools,
            tool_choice=tool_choice,
        )
    else:
        if tools:
            raise RuntimeError(f"{model_alias} 不支持 Responses API tools")
        coro = _call_chat(
            model,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

    remaining = _remaining_budget(deadline)
    if remaining is not None:
        text = await asyncio.wait_for(coro, timeout=max(remaining, 0.1))
    else:
        text = await coro

    log.info("Received %d chars from %s", len(text), model_alias)
    return text


async def call_llm_fallback(
    model_aliases: list[str],
    messages: list[dict[str, Any]],
    *,
    step_label: str,
    temperature: float = 0.4,
    max_tokens: int = 16384,
    step_timeout: float | None = _DEFAULT_PDF_STEP_TIMEOUT,
    response_format: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
) -> str:
    """Try multiple text-only models in order within one step budget."""
    tried: list[tuple[str, Exception]] = []
    ordered_aliases = list(dict.fromkeys(model_aliases))
    deadline = None if step_timeout is None else time.monotonic() + step_timeout
    _MIN_PER_MODEL_BUDGET = 30.0

    for idx, model_alias in enumerate(ordered_aliases):
        if idx > 0:
            log.warning("%s 自动降级到 %s", step_label, model_alias)
        if deadline is not None:
            remaining_budget = max(0.0, deadline - time.monotonic())
            remaining_models = len(ordered_aliases) - idx
            per_model_budget = max(
                _MIN_PER_MODEL_BUDGET,
                remaining_budget / remaining_models,
            )
            model_deadline = time.monotonic() + per_model_budget
        else:
            model_deadline = None
        try:
            return await call_llm(
                model_alias,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                deadline=model_deadline,
                response_format=response_format,
                tools=tools,
                tool_choice=tool_choice,
            )
        except Exception as e:
            tried.append((model_alias, e))
            if idx < len(ordered_aliases) - 1:
                log.warning(
                    "%s: %s 不可用，切换到下一个模型。原因：%s",
                    step_label,
                    model_alias,
                    e,
                )
                continue
            summary = "; ".join(
                f"{alias} -> {type(err).__name__}: {err}" for alias, err in tried
            )
            raise RuntimeError(f"{step_label} 的所有候选模型均失败：{summary}") from e

    raise RuntimeError(f"{step_label} 未提供可用模型")


async def call_lite_model(
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.4,
    max_tokens: int = 16384,
    response_format: dict[str, Any] | None = None,
) -> str:
    """Call the Lite model using its dedicated client (LITE_BASE_URL / LITE_API_KEY)."""
    model = str(
        load_runtime_config().get("model_aliases", {}).get("lite_model") or ""
    ).strip()
    if not model:
        raise RuntimeError("LITE_MODEL / LITE_MODLE 未配置")
    client = _get_lite_client()
    log.info("Calling lite model (%s) ...", model)
    last_err: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            request_kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if response_format is not None:
                request_kwargs["response_format"] = response_format
            resp = await client.chat.completions.create(**request_kwargs)
            text = _coerce_chat_message_content(resp.choices[0].message.content)
            log.info("Received %d chars from lite model", len(text))
            return text
        except Exception as e:
            last_err = e
            if _is_retryable(e):
                delay = _backoff(attempt)
                if attempt + 1 >= _RETRY_WARNING_START_ATTEMPT:
                    log.warning(
                        "[lite] %s (attempt %d/%d, retry in %.1fs)",
                        type(e).__name__,
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                    )
                await asyncio.sleep(delay)
            else:
                raise

    if last_err is not None:
        raise last_err
    raise RuntimeError(
        f"[lite] All {_MAX_RETRIES} attempts failed without exception details"
    )


async def call_llm_with_image(
    model_alias: str,
    image_path: Path,
    text_prompt: str,
    *,
    system_prompt: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> str:
    """Call an LLM with a local image attached as a data URI."""
    model = resolve_model(model_alias)
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    image_data_uri = _encode_image_to_data_uri(image_path)
    log.info("Calling %s (%s) with image %s ...", model_alias, model, image_path.name)

    if _is_openai_model(model):
        input_items: list[dict[str, Any]] = []
        if system_prompt:
            input_items.append(
                {
                    "role": "developer",
                    "content": [{"type": "input_text", "text": system_prompt}],
                }
            )
        input_items.append(
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": text_prompt},
                    {"type": "input_image", "image_url": image_data_uri},
                ],
            }
        )
        text = await _call_responses(
            model,
            input_items,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    else:
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text_prompt},
                    {"type": "image_url", "image_url": {"url": image_data_uri}},
                ],
            }
        )
        text = await _call_chat(
            model,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    log.info("Received %d chars from %s", len(text), model_alias)
    return text


async def call_llm_with_pdf(
    model_alias: str,
    pdf_path: Path,
    text_prompt: str,
    *,
    system_prompt: str | None = None,
    temperature: float = 0.4,
    max_tokens: int = 16384,
    deadline: float | None = None,
    max_failures: int = _PDF_STEP_MAX_FAILURES,
) -> str:
    """Call LLM with a PDF attached via HTTP URL. Respects concurrency limit."""
    model = resolve_model(model_alias)
    log.info("Calling %s (%s) with PDF ...", model_alias, model)

    last_err: Exception | None = None
    force_reupload = False
    pdf_client = _build_client()
    pdf_url: str | None = None

    try:
        for failure_idx in range(max_failures):
            _ensure_budget(deadline, action=f"{model_alias} PDF 调用")
            try:
                pdf_url = await upload_to_r2(pdf_path, force_reupload=force_reupload)

                async with _pdf_semaphore:
                    remaining = _remaining_budget(deadline)
                    if remaining is not None and remaining <= 0:
                        raise TimeoutError(f"{model_alias} 在获取并发槽位后预算耗尽")
                    await _wait_for_provider_pdf_readiness(
                        pdf_url,
                        deadline=deadline,
                        client=pdf_client,
                        probe_model=model,
                    )
                    if _is_openai_model(model):
                        input_items: list[dict[str, Any]] = []
                        if system_prompt:
                            input_items.append(
                                {
                                    "role": "developer",
                                    "content": [
                                        {"type": "input_text", "text": system_prompt}
                                    ],
                                }
                            )
                        input_items.append(
                            {
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": text_prompt},
                                    {"type": "input_file", "file_url": pdf_url},
                                ],
                            }
                        )
                        coro = _call_responses(
                            model,
                            input_items,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            client=pdf_client,
                        )
                    else:
                        messages: list[dict[str, Any]] = []
                        if system_prompt:
                            messages.append({"role": "system", "content": system_prompt})
                        messages.append(
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "file",
                                        "file": {
                                            "filename": pdf_path.name,
                                            "file_data": pdf_url,
                                            "mime_type": "application/pdf",
                                        },
                                    },
                                    {"type": "text", "text": text_prompt},
                                ],
                            }
                        )
                        coro = _call_chat(
                            model,
                            messages,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            client=pdf_client,
                        )

                    if remaining is not None:
                        text = await asyncio.wait_for(coro, timeout=max(remaining, 0.1))
                    else:
                        text = await coro

                log.info("Received %d chars from %s", len(text), model_alias)
                return text
            except Exception as e:
                last_err = e
                if not _should_retry_pdf_call(e):
                    raise
                if failure_idx >= max_failures - 1:
                    break

                remote_pdf_failed = _is_remote_pdf_error(e)
                transient_permission_failed = _is_transient_pdf_permission_error(e)
                using_presigned = (
                    isinstance(pdf_url, str)
                    and "x-amz-signature=" in pdf_url.lower()
                )
                if transient_permission_failed:
                    if using_presigned:
                        _prefer_public_urls(
                            f"{model_alias} transient permission error on pre-signed URL: {type(e).__name__}: {e}"
                        )
                    else:
                        _prefer_presigned_urls(
                            f"{model_alias} transient permission error on public URL: {type(e).__name__}: {e}"
                        )

                # Re-upload only when remote content is likely corrupted.
                force_reupload = remote_pdf_failed
                if remote_pdf_failed:
                    _prefer_presigned_urls(
                        f"{model_alias} remote PDF error: {type(e).__name__}: {e}"
                    )
                delay = _backoff(failure_idx)
                if _is_transient_pdf_permission_error(e):
                    # Some providers briefly return 403 right after a fresh PDF
                    # becomes available from R2 even though the same URL works
                    # moments later for remote fetch. Give the remote side time
                    # to observe the object before downgrading models.
                    delay = max(delay, 6.0)
                remaining = _remaining_budget(deadline)
                if remaining is not None and remaining <= 0:
                    break
                if remaining is not None:
                    delay = min(delay, max(remaining, 0.0))
                if force_reupload:
                    action = "重新上传PDF并重试"
                elif transient_permission_failed:
                    action = "切换R2传输策略并重试"
                else:
                    action = "重试"
                if failure_idx + 1 >= _RETRY_WARNING_START_ATTEMPT:
                    log.warning(
                        "[pdf:%s] %s: %s (failure %d/%d, %s, %.1fs 后继续)",
                        model_alias,
                        type(e).__name__,
                        e,
                        failure_idx + 1,
                        max_failures,
                        action,
                        delay,
                    )
                if delay > 0:
                    await asyncio.sleep(delay)
    finally:
        await pdf_client.close()

    raise ModelStepExhaustedError(
        model_alias,
        max_failures,
        last_err or RuntimeError("Unknown PDF call failure"),
    )


async def call_llm_with_pdf_fallback(
    model_aliases: list[str],
    pdf_path: Path,
    text_prompt: str,
    *,
    step_label: str,
    system_prompt: str | None = None,
    temperature: float = 0.4,
    max_tokens: int = 16384,
    step_timeout: float | None = _DEFAULT_PDF_STEP_TIMEOUT,
) -> str:
    """Try multiple models in order and downgrade within the current step."""
    tried: list[tuple[str, Exception]] = []
    ordered_aliases = list(dict.fromkeys(model_aliases))
    deadline = None if step_timeout is None else time.monotonic() + step_timeout
    _MIN_PER_MODEL_BUDGET = 30.0

    for idx, model_alias in enumerate(ordered_aliases):
        if idx > 0:
            log.warning("%s 自动降级到 %s", step_label, model_alias)
        if deadline is not None:
            remaining_budget = max(0.0, deadline - time.monotonic())
            remaining_models = len(ordered_aliases) - idx
            per_model_budget = max(
                _MIN_PER_MODEL_BUDGET,
                remaining_budget / remaining_models,
            )
            model_deadline = time.monotonic() + per_model_budget
        else:
            model_deadline = None
        try:
            return await call_llm_with_pdf(
                model_alias,
                pdf_path,
                text_prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                deadline=model_deadline,
            )
        except Exception as e:
            tried.append((model_alias, e))
            if idx < len(ordered_aliases) - 1:
                log.warning(
                    "%s: %s 不可用，切换到下一个模型。原因：%s",
                    step_label,
                    model_alias,
                    e,
                )
                continue
            summary = "; ".join(
                f"{alias} -> {type(err).__name__}: {err}" for alias, err in tried
            )
            raise RuntimeError(f"{step_label} 的所有候选模型均失败：{summary}") from e

    raise RuntimeError(f"{step_label} 未提供可用模型")


async def call_dual_models_with_pdf(
    pdf_path: Path,
    text_prompt: str,
    *,
    system_prompt: str | None = None,
    model_a: str = "gem_pro",
    model_b: str = "gpt_pro",
    **kwargs: Any,
) -> tuple[str, str]:
    """Call two models in parallel, each receiving the PDF URL."""
    result_a, result_b = await asyncio.gather(
        call_llm_with_pdf(
            model_a, pdf_path, text_prompt, system_prompt=system_prompt, **kwargs
        ),
        call_llm_with_pdf(
            model_b, pdf_path, text_prompt, system_prompt=system_prompt, **kwargs
        ),
    )
    return result_a, result_b


async def call_dual_models(
    messages: list[dict[str, Any]],
    *,
    model_a: str = "gem_pro",
    model_b: str = "gpt_pro",
    **kwargs: Any,
) -> tuple[str, str]:
    """Call two models in parallel (text-only)."""
    result_a, result_b = await asyncio.gather(
        call_llm(model_a, messages, **kwargs),
        call_llm(model_b, messages, **kwargs),
    )
    return result_a, result_b
