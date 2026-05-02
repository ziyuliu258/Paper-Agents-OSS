"""Memory workspace API for knowledge base, graph, review, and manual edits."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from server.schemas import (
    EvidenceMatrixSnapshotResponse,
    FieldMapSnapshotResponse,
    GapSnapshotResponse,
    MemoryHealthResponse,
    MemoryClaimResponse,
    MemoryClaimUpsertRequest,
    MemoryEditableGraphEdgeResponse,
    MemoryEntityResponse,
    MemoryEntityUpsertRequest,
    MemoryEvidenceResponse,
    MemoryEvidenceUpsertRequest,
    MemoryGraphEdgeUpsertRequest,
    MemoryGraphSnapshotResponse,
    MemoryReviewItemResponse,
    MemoryReviewResolveRequest,
    MemoryRevisionEntryResponse,
    MemorySynthesisResponse,
    MemorySynthesisUpsertRequest,
    MemoryTimelineItemResponse,
    OpportunitySnapshotResponse,
    ThemeSnapshotResponse,
    WorkspaceOverviewResponse,
    MemoryWorkspaceSnapshotResponse,
    ProfileKnowledgeItemResponse,
    CuratedWorkspaceResponse,
)
from utils.memory import MemoryManager

router = APIRouter(tags=["memory-workspace"])


def _get_mm() -> MemoryManager:
    return MemoryManager()


def _ensure_profile(mm: MemoryManager, profile_id: int) -> dict:
    profile = mm.get_profile_by_id(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    mm.ensure_profile_memory_provenance(profile_id)
    return profile


@router.get(
    "/profiles/{profile_id}/workspace", response_model=MemoryWorkspaceSnapshotResponse
)
async def get_workspace_snapshot(profile_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.get_workspace_snapshot(profile_id)
    finally:
        mm.close()


@router.get(
    "/profiles/{profile_id}/workspace/overview",
    response_model=WorkspaceOverviewResponse,
)
async def get_workspace_overview(profile_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.get_workspace_overview(profile_id)
    finally:
        mm.close()


@router.get(
    "/profiles/{profile_id}/workspace/curated",
    response_model=CuratedWorkspaceResponse,
)
async def get_workspace_curated(profile_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.get_workspace_curated(profile_id)
    finally:
        mm.close()


@router.get(
    "/profiles/{profile_id}/workspace/themes",
    response_model=ThemeSnapshotResponse,
)
async def get_workspace_themes(profile_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.get_or_build_theme_snapshot(profile_id)
    finally:
        mm.close()


@router.get(
    "/profiles/{profile_id}/workspace/gaps",
    response_model=GapSnapshotResponse,
)
async def get_workspace_gaps(profile_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.get_or_build_gap_snapshot(profile_id)
    finally:
        mm.close()


@router.get(
    "/profiles/{profile_id}/workspace/opportunities",
    response_model=OpportunitySnapshotResponse,
)
async def get_workspace_opportunities(profile_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.get_or_build_opportunity_snapshot(profile_id)
    finally:
        mm.close()


@router.get(
    "/profiles/{profile_id}/workspace/health",
    response_model=MemoryHealthResponse,
)
async def get_workspace_health(profile_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.get_or_build_memory_health(profile_id)
    finally:
        mm.close()


@router.get(
    "/profiles/{profile_id}/workspace/field-map",
    response_model=FieldMapSnapshotResponse,
)
async def get_workspace_field_map(profile_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.get_or_build_field_map(profile_id)
    finally:
        mm.close()


@router.get(
    "/profiles/{profile_id}/workspace/evidence-matrix",
    response_model=EvidenceMatrixSnapshotResponse,
)
async def get_workspace_evidence_matrix(profile_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.get_or_build_evidence_matrix(profile_id)
    finally:
        mm.close()


@router.get(
    "/profiles/{profile_id}/workspace/knowledge",
    response_model=list[ProfileKnowledgeItemResponse],
)
async def get_workspace_knowledge(profile_id: int, keywords: str = "", top_k: int = 50):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        kw_list = (
            [item.strip() for item in keywords.split(",") if item.strip()]
            if keywords
            else None
        )
        return mm.query_domain_knowledge(profile_id, keywords=kw_list, top_k=top_k)
    finally:
        mm.close()


@router.get(
    "/profiles/{profile_id}/workspace/entities",
    response_model=list[MemoryEntityResponse],
)
async def list_entities(profile_id: int, limit: int = 200):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.list_entities(profile_id, limit=limit)
    finally:
        mm.close()


@router.post(
    "/profiles/{profile_id}/workspace/entities", response_model=MemoryEntityResponse
)
async def create_entity(profile_id: int, body: MemoryEntityUpsertRequest):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.save_entity(profile_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        mm.close()


@router.put(
    "/profiles/{profile_id}/workspace/entities/{entity_id}",
    response_model=MemoryEntityResponse,
)
async def update_entity(
    profile_id: int, entity_id: int, body: MemoryEntityUpsertRequest
):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.save_entity(profile_id, body.model_dump(), entity_id=entity_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        mm.close()


@router.delete("/profiles/{profile_id}/workspace/entities/{entity_id}")
async def delete_entity(profile_id: int, entity_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        mm.delete_entity(profile_id, entity_id)
        return {"deleted": True}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        mm.close()


@router.get(
    "/profiles/{profile_id}/workspace/claims", response_model=list[MemoryClaimResponse]
)
async def list_claims(profile_id: int, limit: int = 200):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.list_claims(profile_id, limit=limit)
    finally:
        mm.close()


@router.post(
    "/profiles/{profile_id}/workspace/claims", response_model=MemoryClaimResponse
)
async def create_claim(profile_id: int, body: MemoryClaimUpsertRequest):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.save_claim(profile_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        mm.close()


@router.put(
    "/profiles/{profile_id}/workspace/claims/{claim_id}",
    response_model=MemoryClaimResponse,
)
async def update_claim(profile_id: int, claim_id: int, body: MemoryClaimUpsertRequest):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.save_claim(profile_id, body.model_dump(), claim_id=claim_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        mm.close()


@router.delete("/profiles/{profile_id}/workspace/claims/{claim_id}")
async def delete_claim(profile_id: int, claim_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        mm.delete_claim(profile_id, claim_id)
        return {"deleted": True}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        mm.close()


@router.get(
    "/profiles/{profile_id}/workspace/evidence",
    response_model=list[MemoryEvidenceResponse],
)
async def list_evidence(profile_id: int, limit: int = 300):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.list_evidence(profile_id, limit=limit)
    finally:
        mm.close()


@router.post(
    "/profiles/{profile_id}/workspace/evidence", response_model=MemoryEvidenceResponse
)
async def create_evidence(profile_id: int, body: MemoryEvidenceUpsertRequest):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.save_evidence(profile_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        mm.close()


@router.put(
    "/profiles/{profile_id}/workspace/evidence/{evidence_id}",
    response_model=MemoryEvidenceResponse,
)
async def update_evidence(
    profile_id: int, evidence_id: int, body: MemoryEvidenceUpsertRequest
):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.save_evidence(profile_id, body.model_dump(), evidence_id=evidence_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        mm.close()


@router.delete("/profiles/{profile_id}/workspace/evidence/{evidence_id}")
async def delete_evidence(profile_id: int, evidence_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        mm.delete_evidence(profile_id, evidence_id)
        return {"deleted": True}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        mm.close()


@router.get(
    "/profiles/{profile_id}/workspace/synthesis",
    response_model=list[MemorySynthesisResponse],
)
async def list_synthesis_items(profile_id: int, limit: int = 160):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.list_synthesis_items(profile_id, limit=limit)
    finally:
        mm.close()


@router.post(
    "/profiles/{profile_id}/workspace/synthesis", response_model=MemorySynthesisResponse
)
async def create_synthesis_item(profile_id: int, body: MemorySynthesisUpsertRequest):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.save_synthesis_item(profile_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        mm.close()


@router.put(
    "/profiles/{profile_id}/workspace/synthesis/{synthesis_id}",
    response_model=MemorySynthesisResponse,
)
async def update_synthesis_item(
    profile_id: int, synthesis_id: int, body: MemorySynthesisUpsertRequest
):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.save_synthesis_item(
            profile_id, body.model_dump(), synthesis_id=synthesis_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        mm.close()


@router.delete("/profiles/{profile_id}/workspace/synthesis/{synthesis_id}")
async def delete_synthesis_item(profile_id: int, synthesis_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        mm.delete_synthesis_item(profile_id, synthesis_id)
        return {"deleted": True}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        mm.close()


@router.get(
    "/profiles/{profile_id}/workspace/graph", response_model=MemoryGraphSnapshotResponse
)
async def get_graph_snapshot(profile_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.build_graph_snapshot(profile_id)
    finally:
        mm.close()


@router.get(
    "/profiles/{profile_id}/workspace/graph/edges",
    response_model=list[MemoryEditableGraphEdgeResponse],
)
async def list_editable_edges(profile_id: int, limit: int = 200):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.list_graph_edges(profile_id, limit=limit)
    finally:
        mm.close()


@router.post(
    "/profiles/{profile_id}/workspace/graph/edges",
    response_model=MemoryEditableGraphEdgeResponse,
)
async def create_graph_edge(profile_id: int, body: MemoryGraphEdgeUpsertRequest):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.save_graph_edge(profile_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        mm.close()


@router.put(
    "/profiles/{profile_id}/workspace/graph/edges/{edge_id}",
    response_model=MemoryEditableGraphEdgeResponse,
)
async def update_graph_edge(
    profile_id: int, edge_id: int, body: MemoryGraphEdgeUpsertRequest
):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.save_graph_edge(profile_id, body.model_dump(), edge_id=edge_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        mm.close()


@router.delete("/profiles/{profile_id}/workspace/graph/edges/{edge_id}")
async def delete_graph_edge(profile_id: int, edge_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        mm.delete_graph_edge(profile_id, edge_id)
        return {"deleted": True}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        mm.close()


@router.get(
    "/profiles/{profile_id}/workspace/reviews",
    response_model=list[MemoryReviewItemResponse],
)
async def list_review_items(profile_id: int, limit: int = 120):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.list_review_items(profile_id, limit=limit)
    finally:
        mm.close()


@router.post(
    "/profiles/{profile_id}/workspace/reviews/{review_id}/resolve",
    response_model=MemoryReviewItemResponse,
)
async def resolve_review_item(
    profile_id: int, review_id: int, body: MemoryReviewResolveRequest
):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.resolve_review_item(
            profile_id,
            review_id,
            resolution_note=body.resolution_note,
            adopt_suggested=body.adopt_suggested,
            dismiss=body.dismiss,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        mm.close()


@router.get(
    "/profiles/{profile_id}/workspace/revisions",
    response_model=list[MemoryRevisionEntryResponse],
)
async def list_revision_history(profile_id: int, limit: int = 160):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.list_revision_history(profile_id, limit=limit)
    finally:
        mm.close()


@router.get(
    "/profiles/{profile_id}/workspace/timeline",
    response_model=list[MemoryTimelineItemResponse],
)
async def get_timeline(profile_id: int):
    mm = _get_mm()
    try:
        _ensure_profile(mm, profile_id)
        return mm.build_timeline(profile_id)
    finally:
        mm.close()
