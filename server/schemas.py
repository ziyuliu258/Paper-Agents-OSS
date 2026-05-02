"""Pydantic request/response models for the API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# --- Config ---


class TopicConfig(BaseModel):
    name: str = ""
    query: str = ""
    keywords: list[str] = Field(default_factory=list)


class SelectionConfig(BaseModel):
    track: str = "auto"
    candidate_pool_size: int = 80
    date_range_days: int = 7
    classic_min_citations: int = 50
    semantic_top_k: int = 8
    min_semantic_score: float = 0.4
    topic_fit_gate_threshold: float = 0.72
    post_download_topic_fit_threshold: float = 0.55
    preferred_venues: list[str] = Field(default_factory=list)
    preferred_institutions: list[str] = Field(default_factory=list)


class ModelsConfig(BaseModel):
    fast: str = "gem_flash"
    primary: str = "gem_pro"
    secondary: str = "gpt_pro"
    merge_model: str = "gem_pro"
    reasoning_effort: str = "high"


class ReportConfig(BaseModel):
    structure_mode: str = "classic"


class AppConfig(BaseModel):
    topics: list[TopicConfig] = Field(default_factory=list)
    selection: SelectionConfig = Field(default_factory=SelectionConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)


class RuntimeConfigSecretState(BaseModel):
    configured: bool = False
    masked_value: str = ""


class RuntimeEnvModeStatus(BaseModel):
    guard_mode: str = "off"
    protected: bool = False
    password_configured: bool = False
    unlocked: bool = False
    auth_header_name: str = ""


class RuntimeConfigResponse(BaseModel):
    providers: dict[str, Any] = Field(default_factory=dict)
    model_aliases: dict[str, str] = Field(default_factory=dict)
    secret_status: dict[str, RuntimeConfigSecretState] = Field(default_factory=dict)


class RuntimeAccessResponse(BaseModel):
    browser_runtime_enabled: bool = False
    env_mode: RuntimeEnvModeStatus = Field(default_factory=RuntimeEnvModeStatus)


class RuntimeConfigUpdate(BaseModel):
    providers: dict[str, Any] = Field(default_factory=dict)
    model_aliases: dict[str, str] = Field(default_factory=dict)
    clear_secrets: list[str] = Field(default_factory=list)


class EnvRuntimeUnlockChallengeResponse(BaseModel):
    guard_mode: str = "off"
    protected: bool = False
    password_configured: bool = False
    algorithm: str = ""
    iterations: int = 0
    salt: str = ""
    challenge_id: str = ""
    nonce: str = ""
    expires_at: int = 0


class EnvRuntimeUnlockVerifyRequest(BaseModel):
    challenge_id: str
    proof: str


class EnvRuntimeUnlockVerifyResponse(BaseModel):
    guard_mode: str = "off"
    protected: bool = False
    password_configured: bool = False
    token: str = ""
    expires_at: int = 0


class ConfigSaveResponse(BaseModel):
    status: str = "ok"


# --- Jobs ---


class JobCreate(BaseModel):
    profile_id: int | None = None
    profile_mode: str = "auto"
    config_override: AppConfig | None = None
    replace_job_id: str | None = None


class JobResponse(BaseModel):
    id: str
    status: str
    mode: str
    profile_id: int | None = None
    profile_mode: str = "auto"
    profile_assignment_status: str = "pending"
    profile_assignment_note: str = ""
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    progress: int = 0
    current_step: str = ""
    paper_title: str = ""
    report_path: str = ""
    error: str | None = None
    created_at: float
    started_at: float | None = None
    completed_at: float | None = None
    has_selector_diagnostics: bool = False
    has_working_memory: bool = False
    has_distilled_memory_summary: bool = False
    has_report_audit: bool = False


class JobForceStopPurgeResponse(BaseModel):
    job_id: str
    profile_id: int | None = None
    force_stopped: bool = False
    task_cancel_requested: bool = False
    task_cancel_timed_out: bool = False
    job_deleted: bool = False
    paper_record_deleted: bool = False
    memory_deleted: bool = False
    memory_delete_summary: dict[str, Any] | None = None
    results_dir_removed: bool = False
    fetch_dir_removed: bool = False
    cache_dir_removed: bool = False


class JobDeleteResponse(BaseModel):
    job_id: str
    profile_id: int | None = None
    running_job_stopped: bool = False
    task_cancel_requested: bool = False
    task_cancel_timed_out: bool = False
    job_deleted: bool = False
    paper_record_deleted: bool = False
    memory_deleted: bool = False
    memory_delete_summary: dict[str, Any] | None = None
    results_dir_removed: bool = False
    fetch_dir_removed: bool = False
    cache_dir_removed: bool = False


class JobListResponse(BaseModel):
    jobs: list[JobResponse]


class TopicKeywordSuggestRequest(BaseModel):
    name: str = ""
    query: str = ""
    existing_keywords: list[str] = Field(default_factory=list)
    max_per_group: int = Field(default=5, ge=1, le=8)


class KeywordGroup(BaseModel):
    label: str
    keywords: list[str] = Field(default_factory=list)


class TopicKeywordSuggestResponse(BaseModel):
    groups: list[KeywordGroup] = Field(default_factory=list)


# --- Papers ---


class PaperResponse(BaseModel):
    id: int
    job_id: str | None = None
    paper_id: str
    title: str = ""
    venue: str = ""
    pub_date: str = ""
    authors: str = "[]"
    source: str = ""
    match_track: str = ""
    selection_reason: str = ""
    pdf_path: str = ""
    source_path: str = ""
    source_type: str = "pdf"
    report_path: str = ""
    created_at: float


# --- Profiles ---


class ProfileCreate(BaseModel):
    name: str
    description: str = ""


class ProfileResponse(BaseModel):
    id: int
    name: str
    description: str = ""
    created_at: float
    last_used_at: float
    paper_count: int = 0


class ProfileActivityItem(BaseModel):
    job_id: str
    job_status: str = ""
    job_mode: str = ""
    job_progress: int = 0
    job_current_step: str = ""
    job_paper_title: str = ""
    job_report_path: str = ""
    job_created_at: float = 0.0
    job_started_at: float | None = None
    job_completed_at: float | None = None
    paper_row_id: int | None = None
    paper_id: str = ""
    paper_title: str = ""
    paper_venue: str = ""
    paper_pub_date: str = ""
    paper_pdf_path: str = ""
    paper_source_path: str = ""
    paper_source_type: str = "pdf"
    paper_report_path: str = ""
    paper_created_at: float | None = None


class LocalizedTextResponse(BaseModel):
    en: str = ""
    zh: str = ""
    primary: str = ""


class ProfileKnowledgeItemResponse(BaseModel):
    id: int
    paper_id: str = ""
    category: str = "general"
    content: str = ""
    content_zh: str = ""
    content_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    relevance_score: float = 0.0
    created_at: float = 0.0


class ProfilePaperLinkResponse(BaseModel):
    id: int
    source_paper_id: str = ""
    target_paper_id: str = ""
    relation_type: str = "related_to"
    summary: str = ""
    summary_zh: str = ""
    summary_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    created_at: float = 0.0


class WorkspaceOverviewResponse(BaseModel):
    paper_source_count: int = 0
    entity_count: int = 0
    claim_count: int = 0
    synthesis_count: int = 0
    pending_review_count: int = 0
    revision_count: int = 0
    graph_node_count: int = 0
    graph_edge_count: int = 0
    theme_count: int = 0
    gap_count: int = 0
    high_priority_gap_count: int = 0
    opportunity_count: int = 0
    high_priority_opportunity_count: int = 0


class OpportunityItemResponse(BaseModel):
    opportunity_key: str = ""
    opportunity_type: str = "opportunity"
    priority: str = "medium"
    title: str = ""
    title_zh: str = ""
    title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    summary: str = ""
    summary_zh: str = ""
    summary_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    theme_keys: list[str] = Field(default_factory=list)
    theme_titles: list[str] = Field(default_factory=list)
    theme_titles_zh: list[str] = Field(default_factory=list)
    theme_titles_localized: list[LocalizedTextResponse] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    claim_ids: list[int] = Field(default_factory=list)
    supporting_claim_ids: list[int] = Field(default_factory=list)
    conflicting_claim_ids: list[int] = Field(default_factory=list)
    synthesis_ids: list[int] = Field(default_factory=list)
    review_ids: list[int] = Field(default_factory=list)
    paper_ids: list[str] = Field(default_factory=list)
    suggested_validation_steps: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    updated_at: float = 0.0


class OpportunitySnapshotResponse(BaseModel):
    profile_id: int = 0
    generated_at: float = 0.0
    item_count: int = 0
    high_priority_count: int = 0
    items: list[OpportunityItemResponse] = Field(default_factory=list)


class MemoryHealthSummaryResponse(BaseModel):
    unsupported_claim_count: int = 0
    thin_evidence_claim_count: int = 0
    contested_claim_count: int = 0
    pending_review_count: int = 0
    deprecated_claim_count: int = 0
    scope_incomplete_claim_count: int = 0
    orphan_evidence_count: int = 0
    stale_artifact_count: int = 0


class MemoryHealthIssueResponse(BaseModel):
    issue_type: str = ""
    severity: str = "low"
    title: str = ""
    title_zh: str = ""
    title_localized: LocalizedTextResponse = Field(default_factory=LocalizedTextResponse)
    count: int = 0
    target_type: str = "claim"
    target_ids: list[int] = Field(default_factory=list)


class MemoryHealthResponse(BaseModel):
    profile_id: int = 0
    generated_at: float = 0.0
    status: str = "good"
    score: float = 1.0
    summary: MemoryHealthSummaryResponse = Field(default_factory=MemoryHealthSummaryResponse)
    issues: list[MemoryHealthIssueResponse] = Field(default_factory=list)


class FieldMapClusterResponse(BaseModel):
    cluster_key: str = ""
    cluster_type: str = "problem"
    title: str = ""
    title_zh: str = ""
    title_localized: LocalizedTextResponse = Field(default_factory=LocalizedTextResponse)
    summary: str = ""
    summary_zh: str = ""
    summary_localized: LocalizedTextResponse = Field(default_factory=LocalizedTextResponse)
    maturity: str = "emerging"
    paper_count: int = 0
    claim_count: int = 0
    evidence_count: int = 0
    controversy_count: int = 0
    claim_ids: list[int] = Field(default_factory=list)
    paper_ids: list[str] = Field(default_factory=list)


class FieldMapLinkResponse(BaseModel):
    source_cluster_key: str = ""
    target_cluster_key: str = ""
    relation_type: str = "related"
    weight: int = 0
    claim_relation_ids: list[int] = Field(default_factory=list)
    claim_ids: list[int] = Field(default_factory=list)


class FieldMapEntryPointResponse(BaseModel):
    audience: str = "newcomer"
    title: str = ""
    title_zh: str = ""
    cluster_keys: list[str] = Field(default_factory=list)
    rationale: str = ""
    rationale_zh: str = ""


class FieldMapSnapshotResponse(BaseModel):
    profile_id: int = 0
    generated_at: float = 0.0
    cluster_count: int = 0
    link_count: int = 0
    clusters: list[FieldMapClusterResponse] = Field(default_factory=list)
    links: list[FieldMapLinkResponse] = Field(default_factory=list)
    entry_points: list[FieldMapEntryPointResponse] = Field(default_factory=list)


class EvidenceMatrixCellResponse(BaseModel):
    evidence_id: int = 0
    claim_id: int = 0
    claim_title: str = ""
    claim_title_zh: str = ""
    claim_title_localized: LocalizedTextResponse = Field(default_factory=LocalizedTextResponse)
    method: str = ""
    value: str = ""
    baseline: str = ""
    setting: str = ""
    limitation: str = ""
    scope_note: str = ""
    paper_id: str = ""
    section_key: str = "other"
    anchor_kind: str = "text"
    snippet: str = ""
    snippet_zh: str = ""
    snippet_localized: LocalizedTextResponse = Field(default_factory=LocalizedTextResponse)
    incomplete_fields: list[str] = Field(default_factory=list)


class EvidenceMatrixRowResponse(BaseModel):
    row_key: str = ""
    task: str = ""
    dataset: str = ""
    metric: str = ""
    cell_count: int = 0
    incomplete_count: int = 0
    cells: list[EvidenceMatrixCellResponse] = Field(default_factory=list)


class EvidenceMatrixSnapshotResponse(BaseModel):
    profile_id: int = 0
    generated_at: float = 0.0
    row_count: int = 0
    evidence_count: int = 0
    incomplete_count: int = 0
    rows: list[EvidenceMatrixRowResponse] = Field(default_factory=list)


class ProfileDetailResponse(BaseModel):
    profile: ProfileResponse
    overview: WorkspaceOverviewResponse = Field(
        default_factory=WorkspaceOverviewResponse
    )
    brief: ProfileBriefResponse | None = None
    theme_preview: list[ThemeItemResponse] = Field(default_factory=list)
    gap_preview: list[GapItemResponse] = Field(default_factory=list)
    opportunity_preview: list[OpportunityItemResponse] = Field(default_factory=list)
    health: MemoryHealthResponse | None = None
    field_map_preview: list[FieldMapClusterResponse] = Field(default_factory=list)
    survey_meta: SurveyMetaResponse | None = None
    knowledge: list[ProfileKnowledgeItemResponse] = Field(default_factory=list)
    curated_digest: list[CuratedDomainDigestSectionResponse] = Field(
        default_factory=list
    )
    style: dict[str, str] = Field(default_factory=dict)
    links: list[ProfilePaperLinkResponse] = Field(default_factory=list)
    activity: list[ProfileActivityItem] = Field(default_factory=list)


# --- Profile Brief ---


class BriefThemeResponse(BaseModel):
    theme_key: str = ""
    anchor: str = ""
    anchor_zh: str = ""
    anchor_type: str = "task"
    methods: list[str] = Field(default_factory=list)
    claim_count: int = 0
    paper_count: int = 0
    maturity: str = "emerging"
    summary: str = ""
    summary_zh: str = ""
    has_debate: bool = False
    has_open_question: bool = False


class BriefSynthesisPreview(BaseModel):
    title: str = ""
    title_zh: str = ""
    confidence: float = 0.5
    claim_count: int = 0
    paper_count: int = 0


class BriefDebatePreview(BaseModel):
    title: str = ""
    title_zh: str = ""
    summary: str = ""
    summary_zh: str = ""
    claim_count: int = 0
    paper_count: int = 0


class BriefOpenQuestion(BaseModel):
    title: str = ""
    title_zh: str = ""


class BriefConceptPreview(BaseModel):
    name: str = ""
    name_zh: str = ""
    type: str = "concept"
    claim_count: int = 0


class BriefFindingPreview(BaseModel):
    title: str = ""
    title_zh: str = ""
    body: str = ""
    body_zh: str = ""
    claim_type: str = "finding"
    importance: float = 0.5


class BriefDeltaResponse(BaseModel):
    paper_title: str = ""
    paper_id: str = ""
    new_entities: list[dict[str, Any]] = Field(default_factory=list)
    new_claims: list[dict[str, Any]] = Field(default_factory=list)
    reinforced_claims: list[dict[str, Any]] = Field(default_factory=list)
    challenged_claims: list[dict[str, Any]] = Field(default_factory=list)
    new_synthesis: list[dict[str, Any]] = Field(default_factory=list)
    updated_synthesis: list[dict[str, Any]] = Field(default_factory=list)
    new_debates: list[dict[str, Any]] = Field(default_factory=list)
    impact_score: float = 0.0


class BriefGapPreviewResponse(BaseModel):
    gap_key: str = ""
    gap_type: str = "gap"
    priority: str = "medium"
    title: str = ""
    title_zh: str = ""
    title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    summary: str = ""
    summary_zh: str = ""
    summary_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    theme_key: str = ""
    theme_title: str = ""
    theme_title_zh: str = ""


class ProfileBriefResponse(BaseModel):
    profile_name: str = ""
    paper_count: int = 0
    generated_at: float = 0.0
    stage: str = "empty"  # empty | initial | full
    # initial stage fields
    key_concepts: list[BriefConceptPreview] = Field(default_factory=list)
    core_findings: list[BriefFindingPreview] = Field(default_factory=list)
    # full stage fields
    key_themes: list[BriefThemeResponse] = Field(default_factory=list)
    top_consensus: list[BriefSynthesisPreview] = Field(default_factory=list)
    top_debates: list[BriefDebatePreview] = Field(default_factory=list)
    open_questions: list[BriefOpenQuestion] = Field(default_factory=list)
    gap_watchlist: list[BriefGapPreviewResponse] = Field(default_factory=list)
    recent_delta: BriefDeltaResponse | None = None


class ThemeEntityPreviewResponse(BaseModel):
    id: int = 0
    name: str = ""
    name_zh: str = ""
    entity_type: str = "concept"
    claim_count: int = 0


class ThemeRepresentativeClaimResponse(BaseModel):
    id: int = 0
    title: str = ""
    title_zh: str = ""
    title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    importance: float = 0.0
    evidence_count: int = 0
    paper_id: str = ""


class ThemeRepresentativeSynthesisResponse(BaseModel):
    id: int = 0
    item_type: str = "consensus"
    title: str = ""
    title_zh: str = ""
    title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    confidence: float = 0.0
    claim_count: int = 0


class ThemeItemResponse(BaseModel):
    theme_key: str = ""
    title: str = ""
    title_zh: str = ""
    title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    summary: str = ""
    summary_zh: str = ""
    summary_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    maturity: str = "emerging"
    paper_count: int = 0
    claim_count: int = 0
    evidence_count: int = 0
    consensus_count: int = 0
    debate_count: int = 0
    open_question_count: int = 0
    pending_review_count: int = 0
    anchor_entities: list[ThemeEntityPreviewResponse] = Field(default_factory=list)
    method_entities: list[ThemeEntityPreviewResponse] = Field(default_factory=list)
    representative_claims: list[ThemeRepresentativeClaimResponse] = Field(
        default_factory=list
    )
    representative_synthesis: list[ThemeRepresentativeSynthesisResponse] = Field(
        default_factory=list
    )
    paper_ids: list[str] = Field(default_factory=list)
    claim_ids: list[int] = Field(default_factory=list)
    synthesis_ids: list[int] = Field(default_factory=list)
    salience_score: float = 0.0


class ThemeSnapshotResponse(BaseModel):
    profile_id: int = 0
    generated_at: float = 0.0
    item_count: int = 0
    items: list[ThemeItemResponse] = Field(default_factory=list)


class GapItemResponse(BaseModel):
    gap_key: str = ""
    gap_type: str = "gap"
    priority: str = "medium"
    title: str = ""
    title_zh: str = ""
    title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    summary: str = ""
    summary_zh: str = ""
    summary_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    theme_key: str = ""
    theme_title: str = ""
    theme_title_zh: str = ""
    theme_title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    reason_codes: list[str] = Field(default_factory=list)
    claim_ids: list[int] = Field(default_factory=list)
    synthesis_ids: list[int] = Field(default_factory=list)
    review_ids: list[int] = Field(default_factory=list)
    paper_ids: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    updated_at: float = 0.0


class GapSnapshotResponse(BaseModel):
    profile_id: int = 0
    generated_at: float = 0.0
    item_count: int = 0
    high_priority_count: int = 0
    items: list[GapItemResponse] = Field(default_factory=list)


class SurveyMetaResponse(BaseModel):
    exists: bool = False
    stale: bool = True
    updated_at: float = 0.0
    section_count: int = 0
    artifact_version: str = ""


class LivingSurveyBlockResponse(BaseModel):
    block_key: str = ""
    title: str = ""
    title_zh: str = ""
    title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    summary: str = ""
    summary_zh: str = ""
    summary_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    badges: list[str] = Field(default_factory=list)
    theme_key: str = ""
    gap_key: str = ""
    claim_ids: list[int] = Field(default_factory=list)
    synthesis_ids: list[int] = Field(default_factory=list)
    review_ids: list[int] = Field(default_factory=list)
    paper_ids: list[str] = Field(default_factory=list)


class LivingSurveySectionResponse(BaseModel):
    section_key: str = ""
    title: str = ""
    title_zh: str = ""
    title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    summary: str = ""
    summary_zh: str = ""
    summary_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    blocks: list[LivingSurveyBlockResponse] = Field(default_factory=list)


class LivingSurveyResponse(BaseModel):
    profile_id: int = 0
    profile_name: str = ""
    generated_at: float = 0.0
    paper_count: int = 0
    theme_count: int = 0
    gap_count: int = 0
    overview_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    sections: list[LivingSurveySectionResponse] = Field(default_factory=list)


class ProfileJobMemoryDeleteResponse(BaseModel):
    profile_id: int
    job_id: str
    paper_id: str = ""
    deleted_job_ids: list[str] = Field(default_factory=list)
    deleted_writeback_count: int = 0
    deleted_knowledge_events: int = 0
    deleted_style_events: int = 0
    deleted_link_events: int = 0
    deleted_evidence: int = 0
    deleted_claims: int = 0
    deleted_synthesis: int = 0
    deleted_edges: int = 0
    deleted_orphaned_claims: int = 0
    deleted_orphaned_synthesis: int = 0
    deleted_orphaned_entities: int = 0
    provenance_mode: str = "exact"
    provenance_modes: list[str] = Field(default_factory=list)
    approximate: bool = False
    deleted_at: float = 0.0


class ProfilePaperMemoryDeleteResponse(ProfileJobMemoryDeleteResponse):
    pass


class ProfileDeleteResponse(BaseModel):
    profile_id: int
    deleted_profile: bool = False
    purged_job_count: int = 0
    deleted_report_count: int = 0
    deleted_paper_record_count: int = 0
    deleted_writeback_count: int = 0
    results_dirs_removed: int = 0
    fetch_dirs_removed: int = 0
    cache_dirs_removed: int = 0
    blocked_active_job_ids: list[str] = Field(default_factory=list)


class ProfilePaperMoveRequest(BaseModel):
    target_profile_id: int
    job_ids: list[str] = Field(default_factory=list)


class ProfilePaperMoveResponse(BaseModel):
    source_profile_id: int
    target_profile_id: int
    moved_job_ids: list[str] = Field(default_factory=list)
    moved_paper_ids: list[str] = Field(default_factory=list)
    moved_writeback_count: int = 0
    moved_claim_count: int = 0
    moved_synthesis_count: int = 0
    moved_edge_count: int = 0
    merged_edge_count: int = 0
    moved_entity_count: int = 0
    cloned_entity_count: int = 0
    relinked_entity_count: int = 0
    source_active_writeback_count: int = 0
    target_active_writeback_count: int = 0
    source_rebuilt_items: int = 0
    source_active_claims: int = 0
    target_rebuilt_items: int = 0
    target_active_claims: int = 0


# --- Memory Workspace ---


class MemoryEntityUpsertRequest(BaseModel):
    name: str
    entity_type: str = "concept"
    summary: str = ""
    aliases: list[str] = Field(default_factory=list)


class MemoryEntityResponse(BaseModel):
    id: int
    profile_id: int
    canonical_name: str
    canonical_name_zh: str = ""
    name_localized: LocalizedTextResponse = Field(default_factory=LocalizedTextResponse)
    normalized_name: str
    entity_type: str = "concept"
    summary: str = ""
    summary_zh: str = ""
    summary_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    manual_locked: bool = False
    status: str = "active"
    created_at: float = 0.0
    updated_at: float = 0.0
    deleted_at: float | None = None
    claim_count: int = 0


class MemoryClaimUpsertRequest(BaseModel):
    claim_key: str = ""
    title: str = ""
    body: str = ""
    claim_type: str = "finding"
    stance: str = "support"
    importance: float = 0.5
    status: str = "active"
    default_resolution: str = ""
    scope: dict[str, Any] = Field(default_factory=dict)
    entity_names: list[str] = Field(default_factory=list)


class MemoryClaimResponse(BaseModel):
    id: int
    profile_id: int
    origin_writeback_id: int | None = None
    claim_key: str
    title: str
    title_zh: str = ""
    title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    body: str
    body_zh: str = ""
    body_localized: LocalizedTextResponse = Field(default_factory=LocalizedTextResponse)
    claim_type: str = "finding"
    stance: str = "support"
    importance: float = 0.5
    status: str = "active"
    default_resolution: str = ""
    default_resolution_zh: str = ""
    default_resolution_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    scope: dict[str, Any] = Field(default_factory=dict)
    lifecycle_state: str = "emerging"
    lifecycle_reason: dict[str, Any] = Field(default_factory=dict)
    superseded_by_claim_id: int | None = None
    last_lifecycle_update_at: float | None = None
    stability_score: float = 0.5
    last_supported_at: float | None = None
    last_challenged_at: float | None = None
    review_status: str = "none"
    manual_locked: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0
    deleted_at: float | None = None
    evidence_count: int = 0
    job_id: str = ""
    paper_id: str = ""
    entity_names: list[str] = Field(default_factory=list)


class MemoryEvidenceUpsertRequest(BaseModel):
    claim_id: int
    section_key: str = "other"
    section_title: str = ""
    snippet: str
    evidence_summary: str = ""
    page_label: str = ""
    page_start: int | None = None
    page_end: int | None = None
    anchor_kind: str = "text"
    context_before: str = ""
    context_after: str = ""
    structured_signal: dict[str, Any] = Field(default_factory=dict)


class MemoryEvidenceResponse(BaseModel):
    id: int
    claim_id: int
    writeback_id: int | None = None
    section_key: str = "other"
    section_title: str = ""
    section_title_zh: str = ""
    section_title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    snippet: str
    snippet_zh: str = ""
    snippet_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    evidence_summary: str = ""
    evidence_summary_zh: str = ""
    evidence_summary_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    page_label: str = ""
    page_start: int | None = None
    page_end: int | None = None
    anchor_kind: str = "text"
    context_before: str = ""
    context_after: str = ""
    structured_signal: dict[str, Any] = Field(default_factory=dict)
    structured_signal_json: str = ""
    weight: float = 1.0
    manual_locked: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0
    deleted_at: float | None = None
    claim_title: str = ""
    claim_title_zh: str = ""
    claim_title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    claim_key: str = ""
    job_id: str = ""
    paper_id: str = ""


class MemorySynthesisUpsertRequest(BaseModel):
    synthesis_key: str = ""
    item_type: str = "consensus"
    title: str = ""
    summary: str = ""
    confidence: float = 0.5
    status: str = "active"
    default_resolution: str = ""
    claim_ids: list[int] = Field(default_factory=list)


class MemorySynthesisResponse(BaseModel):
    id: int
    profile_id: int
    origin_writeback_id: int | None = None
    synthesis_key: str
    item_type: str = "consensus"
    title: str
    title_zh: str = ""
    title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    summary: str
    summary_zh: str = ""
    summary_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    confidence: float = 0.5
    status: str = "active"
    default_resolution: str = ""
    default_resolution_zh: str = ""
    default_resolution_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    review_status: str = "none"
    manual_locked: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0
    deleted_at: float | None = None
    claim_ids: list[int] = Field(default_factory=list)


class MemoryGraphEdgeUpsertRequest(BaseModel):
    source_kind: str = "entity"
    source_ref: str
    target_kind: str = "entity"
    target_ref: str
    relation_type: str = "related_to"
    summary: str = ""
    weight: float = 1.0


class MemoryGraphNodeResponse(BaseModel):
    id: str
    label: str
    label_zh: str = ""
    label_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    node_type: str
    summary: str = ""
    summary_zh: str = ""
    summary_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    status: str = "active"
    ref: str = ""
    degree: int = 0


class MemoryGraphEdgeResponse(BaseModel):
    id: str | int
    source_id: str
    target_id: str
    relation_type: str
    summary: str = ""
    summary_zh: str = ""
    summary_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    weight: float = 1.0


class MemoryEditableGraphEdgeResponse(BaseModel):
    id: int
    profile_id: int
    origin_writeback_id: int | None = None
    source_kind: str
    source_ref: str
    target_kind: str
    target_ref: str
    relation_type: str
    summary: str = ""
    summary_zh: str = ""
    summary_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    weight: float = 1.0
    manual_locked: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0
    deleted_at: float | None = None


class MemoryGraphSnapshotResponse(BaseModel):
    nodes: list[MemoryGraphNodeResponse] = Field(default_factory=list)
    edges: list[MemoryGraphEdgeResponse] = Field(default_factory=list)


class CuratedTopEvidenceResponse(BaseModel):
    id: int = 0
    section_key: str = "other"
    section_title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    snippet_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    evidence_summary_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    page_label: str = ""


class CuratedSynthesisPreviewResponse(BaseModel):
    id: int
    title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    summary_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    confidence: float = 0.5
    claim_count: int = 0
    review_status: str = "none"
    manual_locked: bool = False


class CuratedDomainDigestSectionResponse(BaseModel):
    section_type: str
    section_label: str = ""
    section_label_zh: str = ""
    items: list[CuratedSynthesisPreviewResponse] = Field(default_factory=list)


class CuratedPriorityClaimResponse(BaseModel):
    id: int
    title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    summary_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    claim_type: str = "finding"
    stance: str = "support"
    importance: float = 0.5
    evidence_count: int = 0
    top_evidence: CuratedTopEvidenceResponse | None = None
    entity_names: list[str] = Field(default_factory=list)
    paper_id: str = ""
    review_status: str = "none"
    manual_locked: bool = False


class CuratedActiveConflictResponse(BaseModel):
    review_id: int
    target_type: str = ""
    target_id: int = 0
    title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    default_resolution_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    review_type: str = "candidate_update"
    has_suggested_payload: bool = False


class CuratedEntityPreviewResponse(BaseModel):
    id: int
    name_localized: LocalizedTextResponse = Field(default_factory=LocalizedTextResponse)
    claim_count: int = 0
    manual_locked: bool = False


class CuratedEntityClusterResponse(BaseModel):
    entity_type: str
    label_zh: str = ""
    count: int = 0
    top_entities: list[CuratedEntityPreviewResponse] = Field(default_factory=list)


class CuratedSourceBundleResponse(BaseModel):
    job_id: str
    paper_id: str = ""
    paper_title: str = ""
    created_at: float = 0.0
    claim_count: int = 0
    entity_count: int = 0
    synthesis_count: int = 0


class CuratedWorkspaceResponse(BaseModel):
    domain_digest: list[CuratedDomainDigestSectionResponse] = Field(
        default_factory=list
    )
    priority_claims: list[CuratedPriorityClaimResponse] = Field(default_factory=list)
    active_conflicts: list[CuratedActiveConflictResponse] = Field(default_factory=list)
    source_bundles: list[CuratedSourceBundleResponse] = Field(default_factory=list)
    entity_clusters: list[CuratedEntityClusterResponse] = Field(default_factory=list)


class MemoryReviewResolveRequest(BaseModel):
    resolution_note: str = ""
    adopt_suggested: bool = False
    dismiss: bool = False


class MemoryReviewItemResponse(BaseModel):
    id: int
    profile_id: int
    target_type: str
    target_id: int
    review_type: str
    title: str
    title_zh: str = ""
    title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    description: str = ""
    description_zh: str = ""
    description_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    default_resolution: str = ""
    default_resolution_zh: str = ""
    default_resolution_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    suggested_payload: Any = None
    status: str = "pending"
    reminder_active: bool = True
    resolution_note: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    resolved_at: float | None = None


class MemoryRevisionEntryResponse(BaseModel):
    id: int
    profile_id: int
    target_type: str
    target_id: str
    action: str
    actor_type: str
    summary: str = ""
    summary_zh: str = ""
    summary_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    before_json: Any = None
    after_json: Any = None
    writeback_id: int | None = None
    created_at: float = 0.0


class MemoryTimelineItemResponse(BaseModel):
    id: str
    item_type: str
    title: str
    title_zh: str = ""
    title_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    summary: str = ""
    summary_zh: str = ""
    summary_localized: LocalizedTextResponse = Field(
        default_factory=LocalizedTextResponse
    )
    timestamp: float = 0.0
    status: str = "active"
    target_type: str = ""
    target_id: str = ""
    source_job_id: str = ""
    source_paper_id: str = ""
    bundle_label: str = ""


class MemoryWorkspaceSnapshotResponse(BaseModel):
    profile: ProfileResponse | None = None
    overview: WorkspaceOverviewResponse = Field(
        default_factory=WorkspaceOverviewResponse
    )
    knowledge_items: list[ProfileKnowledgeItemResponse] = Field(default_factory=list)
    style: dict[str, str] = Field(default_factory=dict)
    links: list[ProfilePaperLinkResponse] = Field(default_factory=list)
    entities: list[MemoryEntityResponse] = Field(default_factory=list)
    claims: list[MemoryClaimResponse] = Field(default_factory=list)
    evidence_fragments: list[MemoryEvidenceResponse] = Field(default_factory=list)
    synthesis_items: list[MemorySynthesisResponse] = Field(default_factory=list)
    editable_edges: list[MemoryEditableGraphEdgeResponse] = Field(default_factory=list)
    graph: MemoryGraphSnapshotResponse = Field(
        default_factory=MemoryGraphSnapshotResponse
    )
    reviews: list[MemoryReviewItemResponse] = Field(default_factory=list)
    revisions: list[MemoryRevisionEntryResponse] = Field(default_factory=list)
    timeline: list[MemoryTimelineItemResponse] = Field(default_factory=list)
    curated: CuratedWorkspaceResponse = Field(default_factory=CuratedWorkspaceResponse)
    themes: ThemeSnapshotResponse | None = None
    gaps: GapSnapshotResponse | None = None
    opportunities: OpportunitySnapshotResponse | None = None
    health: MemoryHealthResponse | None = None
    field_map: FieldMapSnapshotResponse | None = None
    evidence_matrix: EvidenceMatrixSnapshotResponse | None = None


class ProfileMemoryRebuildResponse(BaseModel):
    profile_id: int
    rebuilt_items: int = 0
    active_claims: int = 0


# --- Reports ---


class JobReportSummary(BaseModel):
    job_id: str
    status: str = ""
    mode: str = ""
    profile_id: int | None = None
    profile_name: str = ""
    profile_mode: str = "auto"
    profile_assignment_status: str = "pending"
    profile_assignment_note: str = ""
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    diagnostic_snapshot: dict[str, Any] = Field(default_factory=dict)
    progress: float = 0.0
    current_step: str = ""
    paper_title: str = ""
    report_path: str = ""
    error: str | None = None
    created_at: float = 0.0
    started_at: float | None = None
    completed_at: float | None = None
    has_report: bool = False
    has_selector_diagnostics: bool = False
    has_working_memory: bool = False
    has_distilled_memory_summary: bool = False
    has_report_audit: bool = False
    title: str = ""
    size_bytes: int = 0
    modified_at: float = 0.0


class JobReportListResponse(BaseModel):
    reports: list[JobReportSummary] = Field(default_factory=list)


class ReportVariantSummaryResponse(BaseModel):
    variant_id: str = "original"
    label: str = "Original"
    kind: str = "original"
    instruction: str = ""
    structure_mode: str = "classic"
    detail_level: str = "balanced"
    source_variant_id: str = ""
    report_path: str = ""
    created_at: float = 0.0
    size_bytes: int = 0
    modified_at: float = 0.0


class JobReportResponse(BaseModel):
    job_id: str
    profile_id: int | None = None
    profile_name: str = ""
    profile_mode: str = "auto"
    profile_assignment_status: str = "pending"
    profile_assignment_note: str = ""
    title: str = ""
    paper_title: str = ""
    report_path: str = ""
    selector_diagnostics_path: str = ""
    working_memory_path: str = ""
    distilled_memory_summary_path: str = ""
    report_audit_path: str = ""
    has_selector_diagnostics: bool = False
    has_working_memory: bool = False
    has_distilled_memory_summary: bool = False
    has_report_audit: bool = False
    content: str = ""
    size_bytes: int = 0
    modified_at: float = 0.0
    variant_id: str = "original"
    variant_label: str = "Original"
    variant_kind: str = "original"
    source_variant_id: str = ""
    structure_mode: str = "classic"
    detail_level: str = "balanced"
    instruction: str = ""
    variants: list[ReportVariantSummaryResponse] = Field(default_factory=list)


class ReportRefineRequest(BaseModel):
    instruction: str = ""
    target_structure_mode: str = "preserve"
    detail_level: str = "balanced"
    base_variant_id: str = "original"


# --- Stats ---


class StatsResponse(BaseModel):
    jobs_total: int = 0
    jobs_running: int = 0
    papers_total: int = 0
    reports_total: int = 0
    profiles_total: int = 0
