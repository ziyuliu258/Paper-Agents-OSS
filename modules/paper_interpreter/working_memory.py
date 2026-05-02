"""Job-scoped short-term working memory for the interpreter pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ObservationKind = Literal[
    "paper_note",
    "task_output",
    "evidence",
    "adjudication",
    "memory_recall",
]
QuestionStatus = Literal["open", "resolved", "dismissed"]
PromotionStatus = Literal["candidate", "accepted", "rejected", "review_required"]
PromotionType = Literal["claim", "synthesis", "entity_link", "style_preference"]


@dataclass(slots=True)
class Observation:
    source: str
    section_key: str
    summary: str
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 0.5
    kind: ObservationKind = "task_output"


@dataclass(slots=True)
class OpenQuestion:
    question: str
    section_key: str
    reason: str = ""
    status: QuestionStatus = "open"
    resolution_note: str = ""


@dataclass(slots=True)
class DraftClaim:
    section_key: str
    claim: str
    evidence_refs: list[str] = field(default_factory=list)
    importance: str = "medium"
    confidence: float = 0.5


@dataclass(slots=True)
class PromotionCandidate:
    candidate_type: PromotionType
    payload: dict[str, Any]
    source_section: str
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 0.5
    status: PromotionStatus = "candidate"
    rejection_reason: str = ""


@dataclass(slots=True)
class WorkingMemory:
    job_id: str
    profile_id: int | None
    paper_id: str
    paper_title: str = ""
    paper_notes: dict[str, Any] = field(default_factory=dict)
    retrieved_context: dict[str, Any] = field(default_factory=dict)
    task_outputs: dict[str, Any] = field(default_factory=dict)
    observations: list[Observation] = field(default_factory=list)
    open_questions: list[OpenQuestion] = field(default_factory=list)
    draft_claims: list[DraftClaim] = field(default_factory=list)
    evidence_cache: dict[str, Any] = field(default_factory=dict)
    terminology_map: dict[str, str] = field(default_factory=dict)
    promotion_candidates: list[PromotionCandidate] = field(default_factory=list)
    adjudication_notes: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def set_paper_notes(self, notes: dict[str, Any]) -> None:
        self.paper_notes = dict(notes or {})
        metadata = self.paper_notes.get("metadata") or {}
        title = str(metadata.get("title_en", "")).strip()
        if title:
            self.paper_title = title

    def set_retrieved_context(self, name: str, payload: Any) -> None:
        self.retrieved_context[name] = payload

    def remember_task_output(self, task_key: str, payload: Any) -> None:
        self.task_outputs[task_key] = payload

    def add_observation(
        self,
        *,
        source: str,
        section_key: str,
        summary: str,
        evidence_refs: list[str] | None = None,
        confidence: float = 0.5,
        kind: ObservationKind = "task_output",
    ) -> None:
        cleaned = str(summary).strip()
        if not cleaned:
            return
        self.observations.append(
            Observation(
                source=source,
                section_key=section_key,
                summary=cleaned,
                evidence_refs=[str(item).strip() for item in evidence_refs or [] if str(item).strip()],
                confidence=max(0.0, min(float(confidence or 0.0), 1.0)),
                kind=kind,
            )
        )

    def add_open_question(
        self,
        *,
        question: str,
        section_key: str,
        reason: str = "",
    ) -> None:
        cleaned = str(question).strip()
        if not cleaned:
            return
        self.open_questions.append(
            OpenQuestion(
                question=cleaned,
                section_key=section_key,
                reason=str(reason).strip(),
            )
        )

    def resolve_open_question(
        self,
        *,
        question: str,
        resolution_note: str = "",
    ) -> None:
        target = str(question).strip()
        if not target:
            return
        for item in self.open_questions:
            if item.question == target and item.status == "open":
                item.status = "resolved"
                item.resolution_note = str(resolution_note).strip()
                return

    def add_draft_claim(
        self,
        *,
        section_key: str,
        claim: str,
        evidence_refs: list[str] | None = None,
        importance: str = "medium",
        confidence: float = 0.5,
    ) -> None:
        cleaned = str(claim).strip()
        if not cleaned:
            return
        self.draft_claims.append(
            DraftClaim(
                section_key=section_key,
                claim=cleaned,
                evidence_refs=[str(item).strip() for item in evidence_refs or [] if str(item).strip()],
                importance=str(importance).strip() or "medium",
                confidence=max(0.0, min(float(confidence or 0.0), 1.0)),
            )
        )

    def cache_evidence(self, key: str, payload: Any) -> None:
        normalized = str(key).strip()
        if normalized:
            self.evidence_cache[normalized] = payload

    def register_promotion_candidate(
        self,
        *,
        candidate_type: PromotionType,
        payload: dict[str, Any],
        source_section: str,
        evidence_refs: list[str] | None = None,
        confidence: float = 0.5,
        status: PromotionStatus = "candidate",
        rejection_reason: str = "",
    ) -> None:
        self.promotion_candidates.append(
            PromotionCandidate(
                candidate_type=candidate_type,
                payload=dict(payload or {}),
                source_section=str(source_section).strip(),
                evidence_refs=[str(item).strip() for item in evidence_refs or [] if str(item).strip()],
                confidence=max(0.0, min(float(confidence or 0.0), 1.0)),
                status=status,
                rejection_reason=str(rejection_reason).strip(),
            )
        )

    def add_adjudication_note(self, note: dict[str, Any]) -> None:
        if note:
            self.adjudication_notes.append(dict(note))

    def set_metric(self, key: str, value: Any) -> None:
        normalized = str(key).strip()
        if normalized:
            self.metrics[normalized] = value

    def snapshot_for_prompt(self, *, max_items: int = 6) -> dict[str, Any]:
        return {
            "paper_title": self.paper_title,
            "observation_count": len(self.observations),
            "open_question_count": len(
                [item for item in self.open_questions if item.status == "open"]
            ),
            "draft_claim_count": len(self.draft_claims),
            "recent_observations": [
                {
                    "section_key": item.section_key,
                    "summary": item.summary,
                    "evidence_refs": item.evidence_refs,
                    "kind": item.kind,
                }
                for item in self.observations[-max_items:]
            ],
            "open_questions": [
                {
                    "section_key": item.section_key,
                    "question": item.question,
                    "reason": item.reason,
                }
                for item in self.open_questions
                if item.status == "open"
            ][:max_items],
        }

    def build_distillation_input(self) -> dict[str, Any]:
        return {
            "paper_notes": self.paper_notes,
            "task_outputs": self.task_outputs,
            "observations": [
                {
                    "source": item.source,
                    "section_key": item.section_key,
                    "summary": item.summary,
                    "evidence_refs": item.evidence_refs,
                    "confidence": item.confidence,
                    "kind": item.kind,
                }
                for item in self.observations
            ],
            "open_questions": [
                {
                    "question": item.question,
                    "section_key": item.section_key,
                    "reason": item.reason,
                    "status": item.status,
                    "resolution_note": item.resolution_note,
                }
                for item in self.open_questions
            ],
            "draft_claims": [
                {
                    "section_key": item.section_key,
                    "claim": item.claim,
                    "evidence_refs": item.evidence_refs,
                    "importance": item.importance,
                    "confidence": item.confidence,
                }
                for item in self.draft_claims
            ],
            "adjudication_notes": list(self.adjudication_notes),
            "promotion_candidates": [
                {
                    "candidate_type": item.candidate_type,
                    "payload": item.payload,
                    "source_section": item.source_section,
                    "evidence_refs": item.evidence_refs,
                    "confidence": item.confidence,
                    "status": item.status,
                    "rejection_reason": item.rejection_reason,
                }
                for item in self.promotion_candidates
            ],
            "terminology_map": dict(self.terminology_map),
            "metrics": dict(self.metrics),
        }
