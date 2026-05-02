"""Deterministic claim-relation construction for Memory V3."""

from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any


def _normalize(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _normalized_tokens(text: Any) -> set[str]:
    normalized = _normalize(text).lower()
    if not normalized:
        return set()
    return {
        token
        for token in re.split(r"[^a-z0-9\u4e00-\u9fff]+", normalized)
        if len(token) >= 2
    }


def _sequence_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _localized(en: str, zh: str) -> dict[str, str]:
    primary = zh or en
    return {"en": en, "zh": zh, "primary": primary}


def _claim_text(claim: dict[str, Any]) -> str:
    return _normalize(
        claim.get("default_resolution")
        or claim.get("body")
        or claim.get("title")
        or claim.get("claim_key")
    )


def _claim_entities(claim: dict[str, Any]) -> set[str]:
    names = claim.get("entity_names", []) if isinstance(claim, dict) else []
    return {_normalize(name).lower() for name in names if _normalize(name)}


def _evidence_count_map(
    evidence_fragments: list[dict[str, Any]],
) -> dict[int, int]:
    counts: defaultdict[int, int] = defaultdict(int)
    for item in evidence_fragments:
        claim_id = int(item.get("claim_id", 0) or 0)
        if claim_id > 0:
            counts[claim_id] += 1
    return dict(counts)


def _pending_review_map(reviews: list[dict[str, Any]]) -> dict[int, int]:
    counts: defaultdict[int, int] = defaultdict(int)
    for item in reviews:
        if str(item.get("status", "")) != "pending":
            continue
        if str(item.get("target_type", "")) != "claim":
            continue
        target_id = int(item.get("target_id", 0) or 0)
        if target_id > 0:
            counts[target_id] += 1
    return dict(counts)


def build_claim_relations(
    claims: list[dict[str, Any]],
    evidence_fragments: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    evidence_counts = _evidence_count_map(evidence_fragments)
    pending_reviews = _pending_review_map(reviews)
    support_counts: defaultdict[int, int] = defaultdict(int)
    contradiction_counts: defaultdict[int, int] = defaultdict(int)
    last_supported_at: defaultdict[int, float] = defaultdict(float)
    last_challenged_at: defaultdict[int, float] = defaultdict(float)

    rows: list[dict[str, Any]] = []
    ordered_claims = [claim for claim in claims if int(claim.get("id", 0) or 0) > 0]
    ordered_claims.sort(key=lambda item: int(item.get("id", 0) or 0))

    for index, source in enumerate(ordered_claims):
        source_id = int(source.get("id", 0) or 0)
        source_text = _claim_text(source)
        source_entities = _claim_entities(source)
        source_tokens = _normalized_tokens(source_text)
        source_stance = _normalize(source.get("stance", "support")) or "support"
        source_time = float(source.get("updated_at", source.get("created_at", 0.0)) or 0.0)

        for target in ordered_claims[index + 1 :]:
            target_id = int(target.get("id", 0) or 0)
            target_text = _claim_text(target)
            target_entities = _claim_entities(target)
            target_tokens = _normalized_tokens(target_text)
            target_stance = _normalize(target.get("stance", "support")) or "support"
            target_time = float(target.get("updated_at", target.get("created_at", 0.0)) or 0.0)

            shared_entities = sorted(source_entities & target_entities)
            if source_tokens or target_tokens:
                token_overlap = len(source_tokens & target_tokens) / max(
                    len(source_tokens | target_tokens), 1
                )
            else:
                token_overlap = 0.0
            text_similarity = max(
                _sequence_similarity(source_text.lower(), target_text.lower()),
                _sequence_similarity(
                    _normalize(source.get("title", "")).lower(),
                    _normalize(target.get("title", "")).lower(),
                ),
            )
            overlap_score = round(
                min(1.0, token_overlap * 0.45 + text_similarity * 0.35 + (0.2 if shared_entities else 0.0)),
                4,
            )
            if overlap_score < 0.34 and not shared_entities:
                continue

            relation_type = ""
            confidence = round(min(0.98, 0.45 + overlap_score * 0.5), 4)
            relation_time = max(source_time, target_time)

            if source_stance == target_stance and source_stance in {"support", "oppose"}:
                entity_delta = len(source_entities ^ target_entities)
                if overlap_score >= 0.56 and entity_delta >= 1:
                    source_is_broader = len(source_entities) >= len(target_entities)
                    relation_type = "extends" if source_is_broader else "extends"
                    src_claim_id = source_id if source_is_broader else target_id
                    dst_claim_id = target_id if source_is_broader else source_id
                    src_title = _normalize(source.get("title", "")) if source_is_broader else _normalize(target.get("title", ""))
                    dst_title = _normalize(target.get("title", "")) if source_is_broader else _normalize(source.get("title", ""))
                    rationale = (
                        f"{src_title or 'This claim'} extends {dst_title or 'the nearby claim'} "
                        f"with overlapping evidence context and a broader entity scope."
                    )
                    rationale_zh = (
                        f"{src_title or '该 claim'} 与 {dst_title or '相邻 claim'} 共享相近证据语境，"
                        "并且覆盖了更宽的实体范围。"
                    )
                else:
                    relation_type = "reinforces"
                    src_claim_id = source_id
                    dst_claim_id = target_id
                    rationale = (
                        "Two nearby claims point in the same direction and reinforce the same domain conclusion."
                    )
                    rationale_zh = "两条相近的 claim 指向同一结论，彼此形成增强。"
            elif {source_stance, target_stance} <= {"support", "oppose"} and source_stance != target_stance:
                relation_type = "contradicts"
                src_claim_id = source_id
                dst_claim_id = target_id
                confidence = round(min(0.99, confidence + 0.08), 4)
                rationale = (
                    "Two nearby claims share overlapping scope but disagree in stance, which suggests a persistent contradiction."
                )
                rationale_zh = "两条相近的 claim 在作用范围上高度重叠，但结论立场相反，形成持续性矛盾。"
            else:
                continue

            rows.append(
                {
                    "source_claim_id": src_claim_id,
                    "target_claim_id": dst_claim_id,
                    "relation_type": relation_type,
                    "confidence": confidence,
                    "rationale": rationale,
                    "rationale_zh": rationale_zh,
                    "rationale_localized": _localized(rationale, rationale_zh),
                    "shared_entities": shared_entities,
                    "overlap_score": overlap_score,
                    "updated_at": relation_time,
                }
            )
            if relation_type in {"reinforces", "extends"}:
                support_counts[src_claim_id] += 1
                support_counts[dst_claim_id] += 1
                last_supported_at[src_claim_id] = max(
                    last_supported_at[src_claim_id], relation_time
                )
                last_supported_at[dst_claim_id] = max(
                    last_supported_at[dst_claim_id], relation_time
                )
            elif relation_type == "contradicts":
                contradiction_counts[src_claim_id] += 1
                contradiction_counts[dst_claim_id] += 1
                last_challenged_at[src_claim_id] = max(
                    last_challenged_at[src_claim_id], relation_time
                )
                last_challenged_at[dst_claim_id] = max(
                    last_challenged_at[dst_claim_id], relation_time
                )

    claim_stats: dict[int, dict[str, Any]] = {}
    for claim in ordered_claims:
        claim_id = int(claim.get("id", 0) or 0)
        importance = float(claim.get("importance", 0.5) or 0.5)
        evidence_count = int(evidence_counts.get(claim_id, 0) or 0)
        support_count = int(support_counts.get(claim_id, 0) or 0)
        contradiction_count = int(contradiction_counts.get(claim_id, 0) or 0)
        pending_count = int(pending_reviews.get(claim_id, 0) or 0)
        review_status = _normalize(claim.get("review_status", "none")) or "none"
        claim_status = _normalize(claim.get("status", "active")) or "active"

        score = 0.18
        score += min(max(importance, 0.0), 1.0) * 0.42
        score += min(evidence_count, 4) * 0.08
        score += min(support_count, 3) * 0.08
        score -= min(contradiction_count, 3) * 0.11
        score -= min(pending_count, 2) * 0.08
        if review_status == "pending":
            score -= 0.06
        if claim_status == "conflicted":
            score -= 0.1
        if contradiction_count > 0 or pending_count > 0 or review_status == "pending" or claim_status == "conflicted":
            lifecycle_state = "contested"
        elif evidence_count <= 0:
            lifecycle_state = "needs_review"
        elif evidence_count >= 2 or support_count > 0:
            lifecycle_state = "supported"
        else:
            lifecycle_state = "emerging"

        claim_stats[claim_id] = {
            "stability_score": round(min(max(score, 0.0), 1.0), 4),
            "last_supported_at": last_supported_at.get(claim_id) or None,
            "last_challenged_at": last_challenged_at.get(claim_id) or None,
            "evidence_count": evidence_count,
            "support_count": support_count,
            "contradiction_count": contradiction_count,
            "pending_review_count": pending_count,
            "lifecycle_state": lifecycle_state,
            "lifecycle_reason": {
                "evidence_count": evidence_count,
                "support_count": support_count,
                "contradiction_count": contradiction_count,
                "pending_review_count": pending_count,
                "review_status": review_status,
                "claim_status": claim_status,
            },
        }

    return rows, claim_stats
