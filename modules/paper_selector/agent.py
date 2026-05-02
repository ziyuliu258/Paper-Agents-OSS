from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from modules.paper_selector.aic import enrich_candidate_with_aic
from modules.paper_selector.fetcher import _normalize_title
from modules.paper_selector.fetcher import fetch_candidates
from modules.paper_selector.fetcher import get_candidate_dedupe_key
from modules.paper_selector.reranker import rerank_candidates
from modules.paper_selector.selector import select_top_paper
from modules.paper_selector.topic_fit import judge_candidate_topic_fit
from server.database import Database
from utils.config import RESULTS_DIR, clear_cache_dir
from utils.logger import get_logger
from utils.memory import MemoryManager
from utils.pdf_sources import enrich_candidates_with_pdf_urls
from utils.repo_paths import resolve_repo_path, to_repo_relative_path
from utils.source_documents import download_source_document

log = get_logger(__name__)


class SelectionFailure(RuntimeError):
    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or {}


def _safe_pdf_stem(title: str, fallback: str) -> str:
    stem = str(title or "").strip().replace(" ", "_")
    stem = stem.replace("/", "_")
    stem = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in stem)
    stem = "_".join(part for part in stem.split("_") if part)
    stem = stem[:120].strip("_")
    return stem or fallback


def _load_processed_dedupe_keys(config: dict[str, Any]) -> set[str]:
    """Load already-processed dedupe keys from DB first, then filesystem fallback."""
    seen: set[str] = set()
    db_keys: set[str] = set()
    try:
        db = Database()
        try:
            db_keys = db.list_paper_dedupe_keys()
        finally:
            db.close()
    except Exception as exc:
        log.warning("Failed to load processed dedupe keys from DB: %s", exc)
    else:
        seen.update(db_keys)
        log.info("Loaded %d processed dedupe keys from database", len(db_keys))

    file_keys_before = len(seen)
    if RESULTS_DIR.exists():
        for md in RESULTS_DIR.glob("*.md"):
            normalized = _normalize_title(md.stem.replace("_", " "))
            if normalized:
                seen.add(normalized)
            seen.add(md.stem.strip().lower())
        for md in RESULTS_DIR.rglob("*.md"):
            normalized = _normalize_title(md.stem.replace("_", " "))
            if normalized:
                seen.add(normalized)
            seen.add(md.stem.strip().lower())

    fetch_dir = resolve_repo_path(config.get("storage", {}).get("fetch_dir", "data/fetch"))
    if fetch_dir.exists():
        for pdf in fetch_dir.glob("*.pdf"):
            normalized = _normalize_title(pdf.stem.replace("_", " "))
            if normalized:
                seen.add(normalized)
            seen.add(pdf.stem.strip().lower())

    file_added = len(seen) - file_keys_before
    if file_added > 0:
        log.info("Loaded %d additional processed dedupe keys from filesystem", file_added)
    return seen


class PaperSelectorAgent:
    def __init__(self, config: dict[str, Any], *, profile_id: int | None = None) -> None:
        self.config = config
        self.profile_id = profile_id

    def _build_selection_memory(self) -> tuple[str, dict[str, Any]]:
        if self.profile_id is None:
            return "", {}
        mm = MemoryManager()
        try:
            profile = mm.get_profile_by_id(self.profile_id)
            if profile is None:
                return "", {}
            bundle = mm.retrieve_for_selector(self.profile_id, topics=self.config.get("topics", []))
            return mm.render_selection_context(bundle), bundle
        except Exception as exc:
            log.warning("Selection memory preparation failed for profile %s: %s", self.profile_id, exc)
            return "", {}
        finally:
            mm.close()

    async def run(self) -> dict[str, Any]:
        topics = self.config.get("topics", [])
        selection = self.config.get("selection", {})
        topic_fit_gate_threshold = float(
            selection.get("topic_fit_gate_threshold", 0.72)
        )
        cache_root = resolve_repo_path(self.config.get("storage", {}).get("cache_dir", "data/cache"))
        keep_cache = bool(self.config.get("storage", {}).get("keep_cache", False))
        if not topics:
            raise ValueError("自动模式需要在 config.yaml 中提供 topics")
        if not keep_cache:
            clear_cache_dir(cache_root)
            log.info("Cache directory cleared: %s", cache_root)

        selection_memory, selection_memory_bundle = self._build_selection_memory()
        if selection_memory:
            log.info(
                "Prepared profile selection memory (%d chars, digest=%d claims=%d related=%d)",
                len(selection_memory),
                len(selection_memory_bundle.get("high_level_digest", [])),
                len(selection_memory_bundle.get("priority_claims", [])),
                len(selection_memory_bundle.get("related_papers", [])),
            )

        venues = selection.get("preferred_venues", [])
        if venues:
            log.info("Fetching candidates (venue-first: %s)...", venues)
        else:
            log.info("Fetching candidates (general search)...")
        candidates = await fetch_candidates(self.config)
        if not candidates:
            raise RuntimeError("未检索到符合条件的候选论文")
        log.info("Fetched %d candidates after filtering", len(candidates))

        processed_dedupe_keys = _load_processed_dedupe_keys(self.config)
        if processed_dedupe_keys:
            before = len(candidates)
            candidates = [c for c in candidates if get_candidate_dedupe_key(c) not in processed_dedupe_keys]
            excluded = before - len(candidates)
            if excluded > 0:
                log.info("Excluded %d already-processed candidates before reranking, %d remaining", excluded, len(candidates))
            if not candidates:
                raise RuntimeError("所有候选论文都已处理过，没有新论文可选。请调整 topics 或 venues 配置。")

        candidate_pool_size = max(
            int(selection.get("candidate_pool_size", 80)),
            int(selection.get("semantic_top_k", 8)),
        )
        top_k = int(selection.get("semantic_top_k", 8))
        min_score = float(selection.get("min_semantic_score", 0.0))
        candidate_count_before_selectable_filter = len(candidates)
        candidate_pool = await rerank_candidates(
            candidates,
            topics,
            top_k=candidate_pool_size,
            min_score=0.0,
            memory_context=selection_memory,
        )
        if not candidate_pool and candidates:
            log.warning(
                "Reranker returned no candidates; falling back to the first %d pre-rerank candidates",
                min(candidate_pool_size, len(candidates)),
            )
            candidate_pool = candidates[:candidate_pool_size]

        pdf_cache_dir = cache_root / "pdf_lookup"
        if candidate_pool:
            await enrich_candidates_with_pdf_urls(candidate_pool, cache_dir=pdf_cache_dir)

        selectable_candidates = [item for item in candidate_pool if item.get("pdf_url")]
        excluded_non_downloadable = len(candidate_pool) - len(selectable_candidates)
        if excluded_non_downloadable > 0:
            log.info(
                "Excluded %d non-downloadable candidates after candidate-pool reranking, %d selectable candidates remain",
                excluded_non_downloadable,
                len(selectable_candidates),
            )

        ranked = [item for item in selectable_candidates if float(item.get("semantic_score", 0.0)) >= min_score]
        if ranked:
            ranked = ranked[:top_k]
        elif selectable_candidates:
            log.warning(
                "All %d selectable candidates fell below min_score=%.3f after reranking; falling back to top %d selectable candidates from the pre-ranked pool",
                len(selectable_candidates),
                min_score,
                min(top_k, len(selectable_candidates)),
            )
            ranked = selectable_candidates[:top_k]
        elif candidate_pool:
            raise RuntimeError("高相关候选论文中没有可下载 PDF 的条目；请补充下载源或放宽 venues / track。")
        log.info("Kept top %d candidates after reranking", len(ranked))

        cache_dir = resolve_repo_path(self.config.get("storage", {}).get("cache_dir", "data/cache")) / "aic"
        enriched = []
        for item in ranked:
            enriched.append(await enrich_candidate_with_aic(item, cache_dir=cache_dir))

        fit_judgments: list[dict[str, Any]] = []
        fit_passed: list[dict[str, Any]] = []
        rejected_candidates: list[dict[str, Any]] = []
        for item in enriched:
            fit = judge_candidate_topic_fit(item, topics)
            fit_judgments.append(fit)
            item["topic_fit_score"] = fit["topic_fit_score"]
            item["topic_fit_label"] = fit["fit_label"]
            item["topic_fit_matched_aspects"] = fit["matched_aspects"]
            item["topic_fit_mismatch_reasons"] = fit["mismatch_reasons"]
            if (
                str(fit.get("fit_label") or "") != "mismatch"
                and float(fit.get("topic_fit_score") or 0.0)
                >= topic_fit_gate_threshold
            ):
                fit_passed.append(item)
            else:
                rejected_candidates.append(
                    {
                        "paper_id": item.get("paper_id"),
                        "title": item.get("title"),
                        "semantic_score": item.get("semantic_score", 0.0),
                        "topic_fit_score": fit.get("topic_fit_score", 0.0),
                        "fit_label": fit.get("fit_label", ""),
                        "mismatch_reasons": fit.get("mismatch_reasons", []),
                    }
                )

        if not fit_passed:
            raise SelectionFailure(
                "No candidate passed the topic-fit gate for the requested topic.",
                diagnostics={
                    "topics": topics,
                    "selection_memory": selection_memory,
                    "selection_memory_bundle": selection_memory_bundle,
                    "raw_candidates": candidates,
                    "candidate_count": len(candidates),
                    "candidate_count_before_selectable_filter": candidate_count_before_selectable_filter,
                    "candidate_pool_count": len(candidate_pool),
                    "selectable_candidate_count": len(selectable_candidates),
                    "ranked_count": len(ranked),
                    "candidate_pool": candidate_pool,
                    "ranked_candidates": enriched,
                    "fit_passed_candidates": fit_passed,
                    "fit_judgments": fit_judgments,
                    "rejected_candidates": rejected_candidates,
                    "topic_fit_gate_threshold": topic_fit_gate_threshold,
                    "failure_reason": "topic_fit_gate_rejected_all_candidates",
                    "selected_paper_topic_audit": None,
                },
            )

        selected, selector_meta = await select_top_paper(
            fit_passed,
            topics,
            model_alias=self.config.get("models", {}).get("fast", "gem_flash"),
            memory_context=selection_memory,
        )
        selected["dedupe_key"] = get_candidate_dedupe_key(selected)
        pdf_url = selected.get("pdf_url")
        if not pdf_url:
            raise RuntimeError("选中的论文没有可下载 PDF 链接")
        log.info(
            "Selected paper %s via track=%s source=%s",
            selected.get("paper_id"),
            selected.get("match_track", "unknown"),
            selected.get("source", "unknown"),
        )

        cache_root.mkdir(parents=True, exist_ok=True)
        (cache_root / "candidates_ranked.json").write_text(
            json.dumps(
                {
                    "selection_memory": selection_memory,
                    "selection_memory_bundle": selection_memory_bundle,
                    "candidate_pool": candidate_pool,
                    "candidates": enriched,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        paper_id = selected.get("paper_id") or "selected_paper"
        file_stem = _safe_pdf_stem(selected.get("title", ""), fallback=str(paper_id))
        dedupe_suffix = (selected.get("dedupe_key") or str(paper_id)).replace("/", "_")[:16]
        fetch_dir = resolve_repo_path(self.config.get("storage", {}).get("fetch_dir", "data/fetch"))
        output_path = fetch_dir / f"{file_stem}__{dedupe_suffix}"
        downloaded_source = await download_source_document(pdf_url, output_path)
        log.info(
            "Saved %s source as %s",
            downloaded_source.get("source_type", "unknown"),
            Path(str(downloaded_source.get("source_path") or "")).name,
        )

        (cache_root / "selected_paper.json").write_text(
            json.dumps(
                {
                    "selection_memory": selection_memory,
                    "selection_memory_bundle": selection_memory_bundle,
                    "selected": selected,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        return {
            "paper_id": paper_id,
            "dedupe_key": selected.get("dedupe_key", ""),
            "title": selected.get("title", ""),
            "venue": selected.get("venue", ""),
            "date": selected.get("date", ""),
            "authors": selected.get("authors", []),
            "pdf_path": str(downloaded_source.get("pdf_path") or ""),
            "source_path": str(downloaded_source.get("source_path") or ""),
            "source_type": str(downloaded_source.get("source_type") or "pdf"),
            "source_url": str(downloaded_source.get("source_url") or pdf_url),
            "url": selected.get("url", ""),
            "selection_reason": selected.get("selection_reason", ""),
            "source": selected.get("source", ""),
            "match_track": selected.get("match_track", "unknown"),
            "selector_diagnostics": {
                "topics": topics,
                "selection_memory": selection_memory,
                "selection_memory_bundle": selection_memory_bundle,
                "raw_candidates": candidates,
                "candidate_count": len(candidates),
                "candidate_count_before_selectable_filter": candidate_count_before_selectable_filter,
                "candidate_pool_count": len(candidate_pool),
                "selectable_candidate_count": len(selectable_candidates),
                "ranked_count": len(ranked),
                "candidate_pool": candidate_pool,
                "ranked_candidates": enriched,
                "fit_passed_candidates": fit_passed,
                "fit_judgments": fit_judgments,
                "rejected_candidates": rejected_candidates,
                "topic_fit_gate_threshold": topic_fit_gate_threshold,
                "selector": selector_meta,
                "selected": selected,
                "failure_reason": "",
                "selected_paper_topic_audit": None,
            },
        }
