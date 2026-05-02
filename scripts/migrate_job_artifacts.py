from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from server.database import Database
from utils.job_paths import get_job_assets_root, get_job_fetch_dir, get_job_report_path

_ASSET_PATTERN = re.compile(r"\]\((assets/[^)]+)\)")
_PROJECT_NAME = _PROJECT_ROOT.name.lower()


@dataclass
class MigrationRecord:
    job_id: str
    old_report_path: str = ""
    new_report_path: str = ""
    old_pdf_path: str = ""
    new_pdf_path: str = ""
    copied_assets: list[str] = field(default_factory=list)
    updated_paper_id: str = ""
    notes: list[str] = field(default_factory=list)
    status: str = "pending"


def _extract_asset_paths(markdown: str) -> list[str]:
    matches = _ASSET_PATTERN.findall(markdown)
    return list(dict.fromkeys(matches))


def _safe_copy(src: Path, dst: Path, dry_run: bool) -> bool:
    if not src.exists() or not src.is_file():
        return False
    try:
        if src.resolve() == dst.resolve():
            return True
    except Exception:
        pass
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return True


def _rewrite_legacy_path(raw_path: str) -> Path:
    raw = str(raw_path or "").strip().replace("\\", "/")
    if not raw:
        return Path()

    lower = raw.lower()
    project_marker = f"/{_PROJECT_NAME}/"
    marker_index = lower.find(project_marker)
    if marker_index != -1:
        relative_part = raw[marker_index + len(project_marker):].lstrip("/")
        return _PROJECT_ROOT / Path(relative_part)

    if lower.startswith("/home/") or lower.startswith("/users/"):
        parts = [part for part in raw.split("/") if part]
        if _PROJECT_NAME in [part.lower() for part in parts]:
            project_idx = next(i for i, part in enumerate(parts) if part.lower() == _PROJECT_NAME)
            relative_parts = parts[project_idx + 1:]
            return _PROJECT_ROOT.joinpath(*relative_parts)

    path = Path(raw)
    if not path.is_absolute():
        return (_PROJECT_ROOT / path).resolve()
    return path


def _resolve_legacy_asset(report_path: Path, asset_ref: str) -> Path | None:
    candidate_paths = [
        report_path.parent / asset_ref,
        report_path.parent.parent / asset_ref,
        _PROJECT_ROOT / "results" / asset_ref,
    ]
    for candidate in candidate_paths:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def migrate_job_artifacts(*, dry_run: bool, limit: int | None = None) -> dict[str, Any]:
    db = Database()
    records: list[MigrationRecord] = []
    try:
        jobs = db.list_report_jobs(limit=limit or 100000)
        for job in jobs:
            job_id = str(job.get("id", "")).strip()
            if not job_id:
                continue

            old_report_raw = str(job.get("report_path", "")).strip()
            old_report_path = _rewrite_legacy_path(old_report_raw)
            record = MigrationRecord(job_id=job_id, old_report_path=old_report_raw)
            new_report_path = get_job_report_path(job_id)
            record.new_report_path = str(new_report_path)

            if not old_report_path.exists() or not old_report_path.is_file():
                record.status = "skipped"
                record.notes.append("Legacy report path is missing")
                records.append(record)
                continue

            report_content = old_report_path.read_text(encoding="utf-8")
            if old_report_path.resolve() != new_report_path.resolve():
                copied = _safe_copy(old_report_path, new_report_path, dry_run)
                if not copied:
                    record.status = "skipped"
                    record.notes.append("Failed to copy legacy report")
                    records.append(record)
                    continue
            else:
                record.notes.append("Report already stored in job layout")

            for asset_ref in _extract_asset_paths(report_content):
                asset_source = _resolve_legacy_asset(old_report_path, asset_ref)
                if asset_source is None:
                    record.notes.append(f"Missing asset: {asset_ref}")
                    continue
                asset_target = get_job_assets_root(job_id) / Path(asset_ref).relative_to("assets")
                if _safe_copy(asset_source, asset_target, dry_run):
                    record.copied_assets.append(str(asset_target))

            paper = db.get_paper_for_job(job_id, old_report_raw)
            if paper:
                paper_id = str(paper.get("paper_id", "")).strip()
                record.updated_paper_id = paper_id
                old_pdf_raw = str(paper.get("pdf_path", "")).strip()
                if old_pdf_raw:
                    old_pdf_path = _rewrite_legacy_path(old_pdf_raw)
                    record.old_pdf_path = old_pdf_raw
                    if old_pdf_path.exists() and old_pdf_path.is_file():
                        new_pdf_path = get_job_fetch_dir(job_id) / old_pdf_path.name
                        record.new_pdf_path = str(new_pdf_path)
                        _safe_copy(old_pdf_path, new_pdf_path, dry_run)
                    else:
                        record.notes.append("PDF path missing on disk")
                else:
                    record.notes.append("Paper has no stored pdf_path")
            else:
                record.notes.append("No paper row matched this job; report/assets only")

            if not dry_run:
                db.update_job(job_id, report_path=record.new_report_path)
                if record.updated_paper_id:
                    db.update_paper_paths(
                        record.updated_paper_id,
                        pdf_path=record.new_pdf_path or None,
                        report_path=record.new_report_path,
                    )

            record.status = "dry-run" if dry_run else "migrated"
            records.append(record)
    finally:
        db.close()

    return {
        "dry_run": dry_run,
        "jobs_processed": len(records),
        "jobs_migrated": sum(1 for record in records if record.status in {"dry-run", "migrated"}),
        "records": [asdict(record) for record in records],
    }


def validate_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest.get("records", []) if isinstance(manifest, dict) else []
    validated = 0
    missing_paths: list[str] = []

    for record in records:
        if not isinstance(record, dict):
            continue
        status = str(record.get("status", ""))
        if status not in {"dry-run", "migrated"}:
            continue
        report_path = Path(str(record.get("new_report_path", "")).strip())
        if report_path and not report_path.exists():
            missing_paths.append(str(report_path))
        pdf_path_raw = str(record.get("new_pdf_path", "")).strip()
        if pdf_path_raw and not Path(pdf_path_raw).exists():
            missing_paths.append(pdf_path_raw)
        for asset_path in record.get("copied_assets", []):
            asset_text = str(asset_path).strip()
            if asset_text and not Path(asset_text).exists():
                missing_paths.append(asset_text)
        validated += 1

    return {
        "manifest": str(manifest_path),
        "records_checked": validated,
        "missing_paths": missing_paths,
        "ok": len(missing_paths) == 0,
    }


def rollback_from_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest.get("records", []) if isinstance(manifest, dict) else []
    db = Database()
    rolled_back = 0
    try:
        for record in records:
            if not isinstance(record, dict):
                continue
            job_id = str(record.get("job_id", "")).strip()
            if not job_id:
                continue
            old_report_path = str(record.get("old_report_path", "")).strip()
            if old_report_path:
                db.update_job(job_id, report_path=old_report_path)
            paper_id = str(record.get("updated_paper_id", "")).strip()
            if paper_id:
                db.update_paper_paths(
                    paper_id,
                    pdf_path=str(record.get("old_pdf_path", "")).strip() or None,
                    report_path=old_report_path or None,
                )
            rolled_back += 1
    finally:
        db.close()

    return {
        "manifest": str(manifest_path),
        "records_rolled_back": rolled_back,
        "files_left_in_place": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy report artifacts into job-scoped directories")
    parser.add_argument("--apply", action="store_true", help="Perform file copies and database updates")
    parser.add_argument("--limit", type=int, default=None, help="Optional maximum number of jobs to inspect")
    parser.add_argument("--validate", action="store_true", help="Validate a previously generated manifest against the filesystem")
    parser.add_argument("--rollback", action="store_true", help="Rollback database path values from a manifest without deleting copied files")
    parser.add_argument(
        "--manifest",
        type=str,
        default="results/job-migration-manifest.json",
        help="Path to write the migration manifest JSON",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if args.validate:
        result = validate_manifest(manifest_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.rollback:
        result = rollback_from_manifest(manifest_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    result = migrate_job_artifacts(dry_run=not args.apply, limit=args.limit)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Migration manifest written to: {manifest_path}")
    print(json.dumps({
        "dry_run": result["dry_run"],
        "jobs_processed": result["jobs_processed"],
        "jobs_migrated": result["jobs_migrated"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
