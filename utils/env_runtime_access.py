from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

ENV_RUNTIME_AUTH_HEADER = "x-paper-agent-env-auth"
ENV_RUNTIME_ACCESS_GUARD_KEY = "ENV_RUNTIME_ACCESS_GUARD"
ENV_RUNTIME_PASSWORD_HASH_KEY = "ENV_RUNTIME_PASSWORD_HASH"
ENV_RUNTIME_ACCESS_GUARD_OFF = "off"
ENV_RUNTIME_ACCESS_GUARD_PASSWORD = "password"
_ALLOWED_ENV_RUNTIME_ACCESS_GUARD_MODES = {
    ENV_RUNTIME_ACCESS_GUARD_OFF,
    ENV_RUNTIME_ACCESS_GUARD_PASSWORD,
}
_ENV_RUNTIME_PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
_ENV_RUNTIME_PASSWORD_HASH_BYTES = 32
_ENV_RUNTIME_CHALLENGE_TTL_SECONDS = 180
_ENV_RUNTIME_SESSION_TTL_SECONDS = 12 * 60 * 60

_env_runtime_auth_var: ContextVar[dict[str, Any] | None] = ContextVar(
    "paper_agent_env_runtime_auth",
    default=None,
)
_env_runtime_challenges: dict[str, dict[str, Any]] = {}


def get_env_runtime_auth_header_name() -> str:
    return ENV_RUNTIME_AUTH_HEADER


def _urlsafe_b64encode_bytes(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _urlsafe_b64decode_bytes(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return urlsafe_b64decode(f"{raw}{padding}".encode("ascii"))


def _json_dumps_compact(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _build_env_runtime_auth_payload_message(
    challenge_id: str, nonce: str, expires_at: int
) -> str:
    return f"{challenge_id}:{nonce}:{expires_at}"


def _first_env_value(env_values: dict[str, str], *names: str) -> str:
    for name in names:
        value = str(env_values.get(name) or "").strip()
        if value:
            return value
    return ""


def _load_guard_env_values(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if env_path.exists():
        loaded = dotenv_values(env_path)
        for key, value in loaded.items():
            if key and value is not None:
                values[str(key)] = str(value)

    relevant_names = {
        ENV_RUNTIME_ACCESS_GUARD_KEY,
        ENV_RUNTIME_PASSWORD_HASH_KEY,
    }
    for key in relevant_names:
        value = os.environ.get(key)
        if value is not None:
            values[key] = value
    return values


def parse_env_runtime_access_guard_mode(raw_value: str | None) -> str:
    value = str(raw_value or "").strip().lower() or ENV_RUNTIME_ACCESS_GUARD_OFF
    if value not in _ALLOWED_ENV_RUNTIME_ACCESS_GUARD_MODES:
        raise ValueError(
            "ENV_RUNTIME_ACCESS_GUARD 只能是 off 或 password"
        )
    return value


def get_env_runtime_access_guard_mode(*, env_path: Path) -> str:
    env_values = _load_guard_env_values(env_path)
    return parse_env_runtime_access_guard_mode(
        _first_env_value(env_values, ENV_RUNTIME_ACCESS_GUARD_KEY)
    )


def _parse_env_runtime_password_hash(hash_value: str) -> dict[str, Any] | None:
    raw = str(hash_value or "").strip()
    if not raw:
        return None
    parts = raw.split("$")
    if len(parts) != 4:
        raise ValueError(
            "ENV_RUNTIME_PASSWORD_HASH 格式错误，期望为 pbkdf2_sha256$<iterations>$<salt_b64>$<derived_key_b64>"
        )
    algorithm, raw_iterations, raw_salt, raw_key = parts
    if algorithm != _ENV_RUNTIME_PASSWORD_HASH_ALGORITHM:
        raise ValueError("ENV_RUNTIME_PASSWORD_HASH 目前只支持 pbkdf2_sha256")
    iterations = int(raw_iterations)
    if iterations <= 0:
        raise ValueError("ENV_RUNTIME_PASSWORD_HASH iterations 必须大于 0")
    salt = _urlsafe_b64decode_bytes(raw_salt)
    derived_key = _urlsafe_b64decode_bytes(raw_key)
    if not salt or not derived_key:
        raise ValueError("ENV_RUNTIME_PASSWORD_HASH salt/key 不能为空")
    return {
        "algorithm": algorithm,
        "iterations": iterations,
        "salt": salt,
        "salt_b64": raw_salt,
        "derived_key": derived_key,
        "derived_key_b64": raw_key,
    }


def create_env_runtime_password_hash(
    password: str,
    *,
    iterations: int = 390000,
    salt_bytes: int = 16,
) -> str:
    secret = str(password or "")
    if not secret:
        raise ValueError("password 不能为空")
    if iterations <= 0:
        raise ValueError("iterations 必须大于 0")
    if salt_bytes < 16:
        raise ValueError("salt_bytes 至少为 16")
    salt = secrets.token_bytes(salt_bytes)
    derived_key = hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode("utf-8"),
        salt,
        iterations,
        dklen=_ENV_RUNTIME_PASSWORD_HASH_BYTES,
    )
    return (
        f"{_ENV_RUNTIME_PASSWORD_HASH_ALGORITHM}"
        f"${iterations}"
        f"${_urlsafe_b64encode_bytes(salt)}"
        f"${_urlsafe_b64encode_bytes(derived_key)}"
    )


def _load_env_runtime_password_hash_value(*, env_path: Path) -> str:
    env_values = _load_guard_env_values(env_path)
    return _first_env_value(env_values, ENV_RUNTIME_PASSWORD_HASH_KEY)


def get_env_runtime_password_hash_metadata(
    *,
    env_path: Path,
) -> dict[str, Any] | None:
    hash_value = _load_env_runtime_password_hash_value(env_path=env_path)
    if not hash_value:
        return None
    return _parse_env_runtime_password_hash(hash_value)


def is_env_runtime_guard_enabled(*, env_path: Path) -> bool:
    return get_env_runtime_access_guard_mode(env_path=env_path) == ENV_RUNTIME_ACCESS_GUARD_PASSWORD


def is_env_runtime_password_configured(*, env_path: Path) -> bool:
    return get_env_runtime_password_hash_metadata(env_path=env_path) is not None


def is_env_runtime_protected(*, env_path: Path) -> bool:
    return is_env_runtime_guard_enabled(env_path=env_path)


def get_active_env_runtime_auth() -> dict[str, Any] | None:
    payload = _env_runtime_auth_var.get()
    if not isinstance(payload, dict) or not payload:
        return None
    return dict(payload)


def is_env_runtime_access_granted() -> bool:
    payload = get_active_env_runtime_auth()
    if not payload:
        return False
    try:
        return float(payload.get("expires_at") or 0.0) > time.time()
    except (TypeError, ValueError):
        return False


def is_env_runtime_access_allowed(*, env_path: Path) -> bool:
    if not is_env_runtime_guard_enabled(env_path=env_path):
        return True
    if not is_env_runtime_password_configured(env_path=env_path):
        return False
    return is_env_runtime_access_granted()


def _build_env_runtime_token_signing_key(metadata: dict[str, Any]) -> bytes:
    return hashlib.sha256(
        b"paper-agent-env-runtime-auth:" + bytes(metadata.get("derived_key") or b"")
    ).digest()


def _build_env_runtime_user_agent_fingerprint(user_agent: str) -> str:
    raw = str(user_agent or "").strip().encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _prune_expired_env_runtime_challenges() -> None:
    now = time.time()
    expired_ids = [
        challenge_id
        for challenge_id, payload in _env_runtime_challenges.items()
        if float(payload.get("expires_at") or 0.0) <= now
    ]
    for challenge_id in expired_ids:
        _env_runtime_challenges.pop(challenge_id, None)


def create_env_runtime_unlock_challenge(*, env_path: Path) -> dict[str, Any]:
    if not is_env_runtime_guard_enabled(env_path=env_path):
        return {
            "protected": False,
            "guard_mode": ENV_RUNTIME_ACCESS_GUARD_OFF,
            "password_configured": is_env_runtime_password_configured(env_path=env_path),
        }

    metadata = get_env_runtime_password_hash_metadata(env_path=env_path)
    if metadata is None:
        raise RuntimeError(
            "Env runtime access guard is enabled, but ENV_RUNTIME_PASSWORD_HASH is missing."
        )

    _prune_expired_env_runtime_challenges()
    challenge_id = secrets.token_urlsafe(18)
    nonce = secrets.token_urlsafe(24)
    expires_at = int(time.time() + _ENV_RUNTIME_CHALLENGE_TTL_SECONDS)
    _env_runtime_challenges[challenge_id] = {
        "nonce": nonce,
        "expires_at": expires_at,
    }
    return {
        "protected": True,
        "guard_mode": ENV_RUNTIME_ACCESS_GUARD_PASSWORD,
        "password_configured": True,
        "algorithm": metadata["algorithm"],
        "iterations": int(metadata["iterations"]),
        "salt": str(metadata["salt_b64"]),
        "challenge_id": challenge_id,
        "nonce": nonce,
        "expires_at": expires_at,
    }


def _build_env_runtime_auth_token(
    *,
    metadata: dict[str, Any],
    user_agent: str,
    expires_at: int,
) -> str:
    payload = {
        "kind": "env_runtime_auth",
        "ua": _build_env_runtime_user_agent_fingerprint(user_agent),
        "exp": int(expires_at),
        "iat": int(time.time()),
    }
    payload_bytes = _json_dumps_compact(payload).encode("utf-8")
    signature = hmac.new(
        _build_env_runtime_token_signing_key(metadata),
        payload_bytes,
        hashlib.sha256,
    ).digest()
    return (
        f"{_urlsafe_b64encode_bytes(payload_bytes)}."
        f"{_urlsafe_b64encode_bytes(signature)}"
    )


def verify_env_runtime_unlock_proof(
    *,
    env_path: Path,
    challenge_id: str,
    proof: str,
    user_agent: str,
) -> dict[str, Any]:
    if not is_env_runtime_guard_enabled(env_path=env_path):
        raise ValueError("Server env runtime access guard is disabled")

    metadata = get_env_runtime_password_hash_metadata(env_path=env_path)
    if metadata is None:
        raise RuntimeError(
            "Env runtime access guard is enabled, but ENV_RUNTIME_PASSWORD_HASH is missing."
        )

    _prune_expired_env_runtime_challenges()
    challenge = _env_runtime_challenges.pop(str(challenge_id or "").strip(), None)
    if not challenge:
        raise ValueError("Unlock challenge is missing or expired")

    expires_at = int(challenge.get("expires_at") or 0)
    if expires_at <= int(time.time()):
        raise ValueError("Unlock challenge expired")

    message = _build_env_runtime_auth_payload_message(
        str(challenge_id or "").strip(),
        str(challenge.get("nonce") or ""),
        expires_at,
    ).encode("utf-8")
    expected_proof = _urlsafe_b64encode_bytes(
        hmac.new(
            bytes(metadata["derived_key"]),
            message,
            hashlib.sha256,
        ).digest()
    )
    if not hmac.compare_digest(str(proof or "").strip(), expected_proof):
        raise ValueError("Invalid unlock proof")

    session_expires_at = int(time.time() + _ENV_RUNTIME_SESSION_TTL_SECONDS)
    return {
        "protected": True,
        "guard_mode": ENV_RUNTIME_ACCESS_GUARD_PASSWORD,
        "password_configured": True,
        "token": _build_env_runtime_auth_token(
            metadata=metadata,
            user_agent=user_agent,
            expires_at=session_expires_at,
        ),
        "expires_at": session_expires_at,
    }


def parse_env_runtime_auth_header(
    raw_header: str | None,
    *,
    env_path: Path,
    user_agent: str = "",
) -> dict[str, Any] | None:
    raw_value = str(raw_header or "").strip()
    if not raw_value:
        return None
    metadata = get_env_runtime_password_hash_metadata(env_path=env_path)
    if not is_env_runtime_guard_enabled(env_path=env_path) or metadata is None:
        return None
    try:
        payload_part, signature_part = raw_value.split(".", 1)
    except ValueError:
        return None
    try:
        payload_bytes = _urlsafe_b64decode_bytes(payload_part)
        signature = _urlsafe_b64decode_bytes(signature_part)
    except Exception:
        return None

    expected_signature = hmac.new(
        _build_env_runtime_token_signing_key(metadata),
        payload_bytes,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(signature, expected_signature):
        return None

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("kind") != "env_runtime_auth":
        return None

    try:
        expires_at = int(payload.get("exp") or 0)
    except (TypeError, ValueError):
        return None
    if expires_at <= int(time.time()):
        return None

    expected_fingerprint = _build_env_runtime_user_agent_fingerprint(user_agent)
    if not hmac.compare_digest(str(payload.get("ua") or ""), expected_fingerprint):
        return None

    return {
        "expires_at": expires_at,
        "user_agent_fingerprint": expected_fingerprint,
    }


@contextmanager
def env_runtime_auth_override(payload: dict[str, Any] | None):
    token = _env_runtime_auth_var.set(dict(payload) if isinstance(payload, dict) else None)
    try:
        yield
    finally:
        _env_runtime_auth_var.reset(token)


def get_env_runtime_access_denied_detail(*, env_path: Path) -> str:
    if not is_env_runtime_guard_enabled(env_path=env_path):
        return "Env runtime access is available."
    if not is_env_runtime_password_configured(env_path=env_path):
        return (
            "Env runtime access guard is enabled, but ENV_RUNTIME_PASSWORD_HASH is missing on the server. "
            "Set the password hash or disable the guard."
        )
    return (
        "Env runtime access is password-protected. Unlock it in /settings before using the server .env configuration."
    )


def get_env_runtime_access_snapshot(*, env_path: Path) -> dict[str, Any]:
    guard_mode = get_env_runtime_access_guard_mode(env_path=env_path)
    protected = guard_mode == ENV_RUNTIME_ACCESS_GUARD_PASSWORD
    password_configured = is_env_runtime_password_configured(env_path=env_path)
    return {
        "guard_mode": guard_mode,
        "protected": protected,
        "password_configured": password_configured,
        "unlocked": bool(
            protected and password_configured and is_env_runtime_access_granted()
        ),
        "auth_header_name": ENV_RUNTIME_AUTH_HEADER,
    }
