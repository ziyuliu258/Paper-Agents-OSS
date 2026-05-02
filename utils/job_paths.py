from __future__ import annotations

import re
from pathlib import Path

from utils.config import CACHE_DIR, FETCH_PDF_DIR, RESULTS_DIR

_JOB_ID_RE = re.compile(r"[^A-Za-z0-9_-]")
_SAFE_FILENAME_RE = re.compile(r"[^\w.-]")
_JOBS_SEGMENT = "jobs"


def sanitize_job_id(job_id: str) -> str:
    normalized = _JOB_ID_RE.sub("_", str(job_id).strip())
    return normalized or "unknown-job"


def get_job_results_dir(job_id: str) -> Path:
    return RESULTS_DIR / _JOBS_SEGMENT / sanitize_job_id(job_id)


def get_job_report_path(job_id: str) -> Path:
    return get_job_results_dir(job_id) / "report.md"


def get_job_report_variants_dir(job_id: str) -> Path:
    return get_job_results_dir(job_id) / "variants"


def get_job_report_variant_path(job_id: str, variant_id: str) -> Path:
    safe_variant = _SAFE_FILENAME_RE.sub("_", str(variant_id).strip()) or "variant"
    return get_job_report_variants_dir(job_id) / f"{safe_variant}.md"


def get_job_report_variant_meta_path(job_id: str, variant_id: str) -> Path:
    safe_variant = _SAFE_FILENAME_RE.sub("_", str(variant_id).strip()) or "variant"
    return get_job_report_variants_dir(job_id) / f"{safe_variant}.json"


def get_job_assets_root(job_id: str) -> Path:
    return get_job_results_dir(job_id) / "assets"


def get_job_assets_dir(job_id: str, paper_name: str | None = None) -> Path:
    assets_root = get_job_assets_root(job_id)
    if not paper_name:
        return assets_root
    safe_name = re.sub(r"[^\w-]", "_", str(paper_name).strip()) or "paper"
    return assets_root / safe_name


def get_job_fetch_dir(job_id: str) -> Path:
    return FETCH_PDF_DIR / _JOBS_SEGMENT / sanitize_job_id(job_id)


def get_job_cache_dir(job_id: str) -> Path:
    return CACHE_DIR / _JOBS_SEGMENT / sanitize_job_id(job_id)


def get_job_pdf_path(job_id: str, original_name: str | None = None) -> Path:
    fetch_dir = get_job_fetch_dir(job_id)
    raw_name = Path(str(original_name or '')).name
    if raw_name.lower().endswith('.pdf'):
        stem = raw_name[:-4]
    else:
        stem = raw_name
    safe_stem = _SAFE_FILENAME_RE.sub('_', stem.strip()) or 'manual-upload'
    return fetch_dir / f"{safe_stem}.pdf"
