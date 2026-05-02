"""Deterministic opportunity mining for Memory V3."""

from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def _localized(en: str, zh: str) -> dict[str, str]:
    primary = zh or en
    return {"en": en, "zh": zh, "primary": primary}


def build_opportunity_snapshot(
    profile_id: int,
    *,
    theme_snapshot: dict[str, Any],
    gap_snapshot: dict[str, Any],
    claim_relations: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    claim_by_id = {
        int(item.get("id", 0) or 0): item
        for item in claims
        if int(item.get("id", 0) or 0) > 0
    }
    theme_by_claim: dict[int, dict[str, Any]] = {}
    for theme in theme_snapshot.get("items", []):
        for claim_id in theme.get("claim_ids", []):
            normalized = int(claim_id or 0)
            if normalized > 0:
                theme_by_claim[normalized] = theme

    pending_review_by_claim: dict[int, list[int]] = {}
    for review in reviews:
        if str(review.get("status", "")) != "pending":
            continue
        if str(review.get("target_type", "")) != "claim":
            continue
        claim_id = int(review.get("target_id", 0) or 0)
        if claim_id > 0:
            pending_review_by_claim.setdefault(claim_id, []).append(
                int(review.get("id", 0) or 0)
            )

    items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    def append_item(payload: dict[str, Any]) -> None:
        key = _text(payload.get("opportunity_key", ""))
        if not key or key in seen_keys:
            return
        seen_keys.add(key)
        payload["title_localized"] = _localized(
            _text(payload.get("title", "")), _text(payload.get("title_zh", ""))
        )
        payload["summary_localized"] = _localized(
            _text(payload.get("summary", "")), _text(payload.get("summary_zh", ""))
        )
        payload["theme_titles_localized"] = [
            _localized(_text(en), _text(zh))
            for en, zh in zip(
                list(payload.get("theme_titles", [])),
                list(payload.get("theme_titles_zh", [])),
            )
        ]
        items.append(payload)

    for relation in claim_relations:
        if str(relation.get("relation_type", "")) != "contradicts":
            continue
        source_claim_id = int(relation.get("source_claim_id", 0) or 0)
        target_claim_id = int(relation.get("target_claim_id", 0) or 0)
        source_claim = claim_by_id.get(source_claim_id)
        target_claim = claim_by_id.get(target_claim_id)
        if source_claim is None or target_claim is None:
            continue
        source_theme = theme_by_claim.get(source_claim_id) or {}
        target_theme = theme_by_claim.get(target_claim_id) or {}
        review_ids = sorted(
            set(
                pending_review_by_claim.get(source_claim_id, [])
                + pending_review_by_claim.get(target_claim_id, [])
            )
        )
        title = (
            f"Resolve the contradiction between {_text(source_claim.get('title', 'Claim A'))} "
            f"and {_text(target_claim.get('title', 'Claim B'))}"
        )
        title_zh = (
            f"厘清“{_text(source_claim.get('title_zh') or source_claim.get('title', 'Claim A'))}”与“"
            f"{_text(target_claim.get('title_zh') or target_claim.get('title', 'Claim B'))}”之间的矛盾"
        )
        summary = (
            "These two claims overlap in scope but disagree in stance. "
            "This is a strong candidate for targeted replication, setting control, or benchmark reconciliation."
        )
        summary_zh = "这两条 claim 的作用范围高度重叠，但结论立场相反，适合作为复现实验、条件控制或基准统一的优先研究机会。"
        append_item(
            {
                "opportunity_key": f"contradiction:{min(source_claim_id, target_claim_id)}:{max(source_claim_id, target_claim_id)}",
                "opportunity_type": "persistent_contradiction",
                "priority": "high",
                "title": title,
                "title_zh": title_zh,
                "summary": summary,
                "summary_zh": summary_zh,
                "theme_keys": [
                    _text(source_theme.get("theme_key", "")),
                    _text(target_theme.get("theme_key", "")),
                ],
                "theme_titles": [
                    _text(source_theme.get("title", "")),
                    _text(target_theme.get("title", "")),
                ],
                "theme_titles_zh": [
                    _text(source_theme.get("title_zh", "")),
                    _text(target_theme.get("title_zh", "")),
                ],
                "reason_codes": ["persistent_contradiction", "claim_relation:contradicts"],
                "claim_ids": [source_claim_id, target_claim_id],
                "supporting_claim_ids": [],
                "conflicting_claim_ids": [source_claim_id, target_claim_id],
                "synthesis_ids": [],
                "review_ids": review_ids,
                "paper_ids": [
                    _text(source_claim.get("paper_id", "")),
                    _text(target_claim.get("paper_id", "")),
                ],
                "suggested_validation_steps": [
                    "Compare experimental settings and benchmark definitions side by side.",
                    "Prioritize a replication or ablation that isolates the disputed factor.",
                ],
                "risk_flags": ["contradictory_evidence", "human_review_pending"]
                if review_ids
                else ["contradictory_evidence"],
                "updated_at": float(relation.get("updated_at", 0.0) or 0.0),
            }
        )

    for gap in gap_snapshot.get("items", []):
        gap_type = _text(gap.get("gap_type", ""))
        if gap_type != "evidence_thin":
            continue
        claim_ids = [int(item) for item in gap.get("claim_ids", []) if int(item or 0) > 0]
        if not claim_ids:
            continue
        claim = claim_by_id.get(claim_ids[0])
        if claim is None:
            continue
        title = f"Stress-test the evidence behind {_text(claim.get('title', 'this claim'))}"
        title_zh = f"补强“{_text(claim.get('title_zh') or claim.get('title', '该 claim'))}”的证据基础"
        append_item(
            {
                "opportunity_key": f"thin-evidence:{claim_ids[0]}",
                "opportunity_type": "thin_evidence_high_impact",
                "priority": "high" if float(claim.get("importance", 0.5) or 0.5) >= 0.85 else "medium",
                "title": title,
                "title_zh": title_zh,
                "summary": "The claim appears important, but its current evidence coverage is still too thin for stable long-term domain cognition.",
                "summary_zh": "这条 claim 看起来很重要，但当前证据覆盖仍然偏薄，不足以支撑稳定的长期领域认知。",
                "theme_keys": [_text(gap.get("theme_key", ""))],
                "theme_titles": [_text(gap.get("theme_title", ""))],
                "theme_titles_zh": [_text(gap.get("theme_title_zh", ""))],
                "reason_codes": list(gap.get("reason_codes", [])) + ["high_impact_claim"],
                "claim_ids": claim_ids,
                "supporting_claim_ids": claim_ids,
                "conflicting_claim_ids": [],
                "synthesis_ids": [int(item) for item in gap.get("synthesis_ids", []) if int(item or 0) > 0],
                "review_ids": [int(item) for item in gap.get("review_ids", []) if int(item or 0) > 0],
                "paper_ids": [item for item in gap.get("paper_ids", []) if _text(item)],
                "suggested_validation_steps": [
                    "Add direct metric anchors or a stronger ablation for this claim.",
                    "Check whether the claim still holds across datasets, scales, or seeds.",
                ],
                "risk_flags": ["thin_evidence"],
                "updated_at": float(gap.get("updated_at", 0.0) or 0.0),
            }
        )

    themes = list(theme_snapshot.get("items", []))
    for theme in themes:
        if int(theme.get("consensus_count", 0) or 0) <= 0:
            continue
        if int(theme.get("open_question_count", 0) or 0) <= 0 and int(
            theme.get("pending_review_count", 0) or 0
        ) <= 0:
            continue
        title = f"Clarify the operating boundary of {_text(theme.get('title', 'this theme'))}"
        title_zh = f"厘清“{_text(theme.get('title_zh') or theme.get('title', '该主题'))}”的适用边界"
        append_item(
            {
                "opportunity_key": f"consensus-boundary:{_text(theme.get('theme_key', ''))}",
                "opportunity_type": "consensus_boundary_missing",
                "priority": "medium",
                "title": title,
                "title_zh": title_zh,
                "summary": "The theme already shows consensus signals, but its failure conditions or scope boundary remain underspecified.",
                "summary_zh": "该主题已经出现一定共识信号，但其失效条件或适用边界仍然定义不足。",
                "theme_keys": [_text(theme.get("theme_key", ""))],
                "theme_titles": [_text(theme.get("title", ""))],
                "theme_titles_zh": [_text(theme.get("title_zh", ""))],
                "reason_codes": ["consensus_boundary_missing"],
                "claim_ids": [int(item) for item in theme.get("claim_ids", []) if int(item or 0) > 0],
                "supporting_claim_ids": [int(item) for item in theme.get("claim_ids", []) if int(item or 0) > 0][:3],
                "conflicting_claim_ids": [],
                "synthesis_ids": [int(item) for item in theme.get("synthesis_ids", []) if int(item or 0) > 0],
                "review_ids": [],
                "paper_ids": [item for item in theme.get("paper_ids", []) if _text(item)],
                "suggested_validation_steps": [
                    "Identify the benchmark, data regime, or scale where the consensus might break.",
                    "Convert current open questions into explicit boundary-testing experiments.",
                ],
                "risk_flags": ["boundary_under_specified"],
                "updated_at": float(theme_snapshot.get("generated_at", 0.0) or 0.0),
            }
        )

    source_themes = [
        item
        for item in themes
        if item.get("method_entities") and str(item.get("maturity", "")) in {"growing", "mature"}
    ]
    target_themes = [item for item in themes if not item.get("method_entities")]
    for source_theme in source_themes[:2]:
        for target_theme in target_themes[:2]:
            if _text(source_theme.get("theme_key", "")) == _text(target_theme.get("theme_key", "")):
                continue
            method_name = _text(
                (source_theme.get("method_entities", [{}])[0] or {}).get("name", "")
            )
            if not method_name:
                continue
            title = f"Test whether {method_name} transfers into {_text(target_theme.get('title', 'a nearby theme'))}"
            title_zh = f"验证“{method_name}”能否迁移到“{_text(target_theme.get('title_zh') or target_theme.get('title', '相邻主题'))}”"
            append_item(
                {
                    "opportunity_key": f"cross-theme-transfer:{_text(source_theme.get('theme_key', ''))}:{_text(target_theme.get('theme_key', ''))}:{method_name}",
                    "opportunity_type": "cross_theme_transfer",
                    "priority": "medium",
                    "title": title,
                    "title_zh": title_zh,
                    "summary": "A method that already appears reusable in one theme has not yet formed explicit structure in a nearby theme, which makes transfer testing attractive.",
                    "summary_zh": "某方法已经在一个主题中表现出复用价值，但在相邻主题中仍未形成明确结构，因此适合做迁移验证。",
                    "theme_keys": [
                        _text(source_theme.get("theme_key", "")),
                        _text(target_theme.get("theme_key", "")),
                    ],
                    "theme_titles": [
                        _text(source_theme.get("title", "")),
                        _text(target_theme.get("title", "")),
                    ],
                    "theme_titles_zh": [
                        _text(source_theme.get("title_zh", "")),
                        _text(target_theme.get("title_zh", "")),
                    ],
                    "reason_codes": ["cross_theme_transfer", f"method:{method_name}"],
                    "claim_ids": [
                        int(item) for item in source_theme.get("claim_ids", []) if int(item or 0) > 0
                    ][:4],
                    "supporting_claim_ids": [
                        int(item) for item in source_theme.get("claim_ids", []) if int(item or 0) > 0
                    ][:3],
                    "conflicting_claim_ids": [],
                    "synthesis_ids": [],
                    "review_ids": [],
                    "paper_ids": [
                        item
                        for item in (
                            list(source_theme.get("paper_ids", []))
                            + list(target_theme.get("paper_ids", []))
                        )
                        if _text(item)
                    ][:8],
                    "suggested_validation_steps": [
                        "Translate the strongest method assumption into the target theme's benchmark or task setting.",
                        "Check whether the missing structure is caused by task mismatch, metric mismatch, or unexplored implementation cost.",
                    ],
                    "risk_flags": ["transfer_speculative"],
                    "updated_at": float(theme_snapshot.get("generated_at", 0.0) or 0.0),
                }
            )
            break

    priority_rank = {"high": 0, "medium": 1, "low": 2}
    items.sort(
        key=lambda item: (
            priority_rank.get(str(item.get("priority", "medium")), 1),
            -float(item.get("updated_at", 0.0) or 0.0),
            str(item.get("title", "")),
        )
    )
    return {
        "profile_id": profile_id,
        "generated_at": float(theme_snapshot.get("generated_at", 0.0) or 0.0),
        "item_count": len(items),
        "high_priority_count": sum(
            1 for item in items if str(item.get("priority", "")) == "high"
        ),
        "items": items[:16],
    }
