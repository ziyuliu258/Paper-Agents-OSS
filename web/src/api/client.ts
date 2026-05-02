import {
  buildEnvRuntimeAuthHeaderValue,
  buildRuntimeOverrideHeaderValue,
  ENV_RUNTIME_AUTH_HEADER_NAME,
  RUNTIME_OVERRIDE_HEADER_NAME,
} from '@/lib/runtimeSettings'

const BASE = '/api'

function shouldAttachRuntimeHeader(path: string, options?: RequestInit): boolean {
  const method = String(options?.method || 'GET').toUpperCase()

  if (method === 'POST') {
    return (
      path === '/jobs' ||
      path === '/jobs/manual' ||
      /^\/jobs\/[^/]+\/(retry|rerun)$/.test(path) ||
      path === '/topics/keyword-candidates' ||
      /^\/reports\/jobs\/[^/]+\/refine$/.test(path)
    )
  }

  if (method === 'GET') {
    return (
      path === '/config/runtime' ||
      path === '/config/runtime/access' ||
      /^\/reports\/jobs\/[^/]+\/(working-memory-localized|distilled-summary-localized)/.test(path)
    )
  }

  return false
}

function applyRuntimeHeader(path: string, options?: RequestInit): Headers {
  const headers = new Headers(options?.headers)
  if (!shouldAttachRuntimeHeader(path, options)) {
    return headers
  }
  const runtimeHeader = buildRuntimeOverrideHeaderValue()
  if (runtimeHeader) {
    headers.set(RUNTIME_OVERRIDE_HEADER_NAME, runtimeHeader)
  }
  const envAuthHeader = buildEnvRuntimeAuthHeaderValue()
  if (envAuthHeader) {
    headers.set(ENV_RUNTIME_AUTH_HEADER_NAME, envAuthHeader)
  }
  return headers
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = applyRuntimeHeader(path, options)
  const isFormData = typeof FormData !== 'undefined' && options?.body instanceof FormData
  if (!isFormData && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status}: ${text}`)
  }
  return res.json()
}

async function requestJsonFromUrl<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status}: ${text}`)
  }
  return res.json()
}

async function requestText(path: string): Promise<string> {
  const headers = applyRuntimeHeader(path, { method: 'GET' })
  const res = await fetch(`${BASE}${path}`, { headers })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status}: ${text}`)
  }
  return res.text()
}

// Config
export interface AppRuntimeConfigSecretState {
  configured: boolean
  masked_value: string
}

export interface AppRuntimeConfig {
  providers: {
    openai?: {
      base_url?: string
      api_key?: string
      api_key_configured?: boolean
      api_key_masked?: string
    }
    lite?: {
      base_url?: string
      api_key?: string
      api_key_configured?: boolean
      api_key_masked?: string
    }
    embedding?: {
      base_url?: string
      api_key?: string
      api_key_configured?: boolean
      api_key_masked?: string
      model?: string
    }
    semantic_scholar?: {
      api_key?: string
      api_key_configured?: boolean
      api_key_masked?: string
    }
    mineru?: {
      api_key?: string
      api_key_configured?: boolean
      api_key_masked?: string
    }
    r2?: {
      endpoint?: string
      bucket?: string
      access_key_id?: string
      access_key_id_configured?: boolean
      access_key_id_masked?: string
      secret_access_key?: string
      secret_access_key_configured?: boolean
      secret_access_key_masked?: string
      public_base_url?: string
    }
    network?: {
      proxy_port?: number | null
    }
  }
  model_aliases: Record<string, string>
  secret_status: Record<string, AppRuntimeConfigSecretState>
}

export interface RuntimeAccessStatus {
  browser_runtime_enabled: boolean
  env_mode: {
    guard_mode: 'off' | 'password'
    protected: boolean
    password_configured: boolean
    unlocked: boolean
    auth_header_name: string
  }
}

export interface AppRuntimeConfigUpdate {
  providers?: Record<string, unknown>
  model_aliases?: Record<string, string>
  clear_secrets?: string[]
}

export interface EnvRuntimeUnlockChallengeResponse {
  guard_mode: 'off' | 'password'
  protected: boolean
  password_configured: boolean
  algorithm: string
  iterations: number
  salt: string
  challenge_id: string
  nonce: string
  expires_at: number
}

export interface EnvRuntimeUnlockVerifyResponse {
  guard_mode: 'off' | 'password'
  protected: boolean
  password_configured: boolean
  token: string
  expires_at: number
}

export const getConfig = () => request<Record<string, unknown>>('/config')
export const updateConfig = (config: Record<string, unknown>) =>
  request<{ status: string }>('/config', { method: 'PUT', body: JSON.stringify(config) })
export const getRuntimeConfig = () => request<AppRuntimeConfig>('/config/runtime')
export const getRuntimeAccessStatus = () => request<RuntimeAccessStatus>('/config/runtime/access')
export const updateRuntimeConfig = (config: AppRuntimeConfigUpdate) =>
  request<{ status: string }>('/config/runtime', { method: 'PUT', body: JSON.stringify(config) })
export const createEnvRuntimeUnlockChallenge = () =>
  request<EnvRuntimeUnlockChallengeResponse>('/config/runtime/env-unlock/challenge', { method: 'POST' })
export const verifyEnvRuntimeUnlock = (body: { challenge_id: string; proof: string }) =>
  request<EnvRuntimeUnlockVerifyResponse>('/config/runtime/env-unlock/verify', {
    method: 'POST',
    body: JSON.stringify(body),
  })

// Stats
export const getStats = () =>
  request<{ jobs_total: number; jobs_running: number; papers_total: number; reports_total: number; profiles_total: number }>('/stats')

// Jobs
export interface JobTopicConfig {
  name: string
  query: string
  keywords: string[]
}

export interface JobSelectionConfig {
  track?: string
  date_range_days?: number
  classic_min_citations?: number
  preferred_venues?: string[]
  topic_fit_gate_threshold?: number
  post_download_topic_fit_threshold?: number
}

export interface JobReportConfig {
  structure_mode?: string
}

export interface JobConfigSnapshot {
  topics?: JobTopicConfig[]
  selection?: JobSelectionConfig
  report?: JobReportConfig
}

export interface Job {
  id: string
  status: string
  mode: string
  profile_id: number | null
  profile_mode: 'auto' | 'explicit'
  profile_assignment_status: string
  profile_assignment_note: string
  config_snapshot: JobConfigSnapshot
  progress: number
  current_step: string
  paper_title: string
  report_path: string
  error: string | null
  created_at: number
  started_at: number | null
  completed_at: number | null
  has_selector_diagnostics?: boolean
  has_working_memory?: boolean
  has_distilled_memory_summary?: boolean
  has_report_audit?: boolean
}

export interface CancelJobResult {
  cancelled: boolean
}

export interface ForceStopJobResult {
  job_id: string
  profile_id: number | null
  force_stopped: boolean
  task_cancel_requested: boolean
  task_cancel_timed_out: boolean
  job_deleted: boolean
  paper_record_deleted: boolean
  memory_deleted: boolean
  memory_delete_summary: Record<string, unknown> | null
  results_dir_removed: boolean
  fetch_dir_removed: boolean
  cache_dir_removed: boolean
}

export interface DeleteJobResult {
  job_id: string
  profile_id: number | null
  running_job_stopped: boolean
  task_cancel_requested: boolean
  task_cancel_timed_out: boolean
  job_deleted: boolean
  paper_record_deleted: boolean
  memory_deleted: boolean
  memory_delete_summary: Record<string, unknown> | null
  results_dir_removed: boolean
  fetch_dir_removed: boolean
  cache_dir_removed: boolean
}

export interface JobConfigOverride {
  topics?: Array<{
    name: string
    query: string
    keywords: string[]
  }>
  selection?: {
    track?: string
    date_range_days?: number
    classic_min_citations?: number
    preferred_venues?: string[]
    topic_fit_gate_threshold?: number
    post_download_topic_fit_threshold?: number
  }
  report?: {
    structure_mode?: string
  }
}

export interface TopicKeywordSuggestRequest {
  name: string
  query: string
  existing_keywords?: string[]
  max_per_group?: number
}

export interface KeywordGroup {
  label: string
  keywords: string[]
}

export interface TopicKeywordSuggestResponse {
  groups: KeywordGroup[]
}

export const createJob = (body: {
  profile_id?: number
  profile_mode?: 'auto' | 'explicit'
  config_override?: JobConfigOverride
  replace_job_id?: string
}) =>
  request<Job>('/jobs', { method: 'POST', body: JSON.stringify(body) })

export const createManualJob = (body: {
  file: File
  profile_id?: number
  profile_mode?: 'auto' | 'explicit'
  replace_job_id?: string
  config_override?: JobConfigOverride
}) => {
  const formData = new FormData()
  formData.set('file', body.file)
  formData.set('profile_mode', body.profile_mode || 'auto')
  if (typeof body.profile_id === 'number') {
    formData.set('profile_id', String(body.profile_id))
  }
  if (body.replace_job_id) {
    formData.set('replace_job_id', body.replace_job_id)
  }
  if (body.config_override) {
    formData.set('config_override_json', JSON.stringify(body.config_override))
  }
  return request<Job>('/jobs/manual', { method: 'POST', body: formData })
}

export const getJobHistory = (limit = 100) =>
  request<{ reports: JobReportSummary[] }>(`/jobs/history?limit=${limit}`)
// Legacy raw job list endpoint; prefer getJobHistory for history views.
export const getJobs = (limit = 50) => request<{ jobs: Job[] }>(`/jobs?limit=${limit}`)
export const getJob = (id: string) => request<Job>(`/jobs/${id}`)
export const cancelJob = (id: string) => request<CancelJobResult>(`/jobs/${id}/cancel`, { method: 'POST' })
export const forceStopJob = (id: string) => request<ForceStopJobResult>(`/jobs/${id}/force-stop`, { method: 'POST' })
export const deleteJob = (id: string) => request<DeleteJobResult>(`/jobs/${id}`, { method: 'DELETE' })
export const rerunJob = (id: string) => request<Job>(`/jobs/${id}/rerun`, { method: 'POST' })
export const retryJob = (id: string) => request<Job>(`/jobs/${id}/retry`, { method: 'POST' })
export const generateTopicKeywords = (body: TopicKeywordSuggestRequest) =>
  request<TopicKeywordSuggestResponse>('/topics/keyword-candidates', { method: 'POST', body: JSON.stringify(body) })

// Reports
export interface JobReportSummary {
  job_id: string
  status: string
  mode: string
  profile_id: number | null
  profile_name: string
  profile_mode: 'auto' | 'explicit'
  profile_assignment_status: string
  profile_assignment_note: string
  config_snapshot: JobConfigSnapshot
  diagnostic_snapshot: Record<string, unknown>
  progress: number
  current_step: string
  paper_title: string
  report_path: string
  error: string | null
  created_at: number
  started_at: number | null
  completed_at: number | null
  has_report: boolean
  has_selector_diagnostics: boolean
  has_working_memory: boolean
  has_distilled_memory_summary: boolean
  has_report_audit: boolean
  title: string
  size_bytes: number
  modified_at: number
}

export interface JobReport {
  job_id: string
  profile_id: number | null
  profile_name: string
  profile_mode: 'auto' | 'explicit'
  profile_assignment_status: string
  profile_assignment_note: string
  title: string
  paper_title: string
  report_path: string
  selector_diagnostics_path: string
  working_memory_path: string
  distilled_memory_summary_path: string
  report_audit_path: string
  has_selector_diagnostics: boolean
  has_working_memory: boolean
  has_distilled_memory_summary: boolean
  has_report_audit: boolean
  content: string
  size_bytes: number
  modified_at: number
  variant_id: string
  variant_label: string
  variant_kind: string
  source_variant_id: string
  structure_mode: string
  detail_level: string
  instruction: string
  variants: JobReportVariantSummary[]
}

export const getJobReports = (limit = 100, options?: { include_all_jobs?: boolean }) => {
  const params = new URLSearchParams({ limit: String(limit) })
  if (options?.include_all_jobs) {
    params.set('include_all_jobs', 'true')
  }
  return request<{ reports: JobReportSummary[] }>(`/reports/jobs?${params.toString()}`)
}
export interface JobReportVariantSummary {
  variant_id: string
  label: string
  kind: string
  instruction: string
  structure_mode: string
  detail_level: string
  source_variant_id: string
  report_path: string
  created_at: number
  size_bytes: number
  modified_at: number
}

export interface ReportRefineRequest {
  instruction: string
  target_structure_mode?: 'preserve' | 'classic' | 'pmrc'
  detail_level?: 'auto' | 'concise' | 'balanced' | 'detailed'
  base_variant_id?: string
}

export const getJobReport = (jobId: string, options?: { variantId?: string }) => {
  const params = new URLSearchParams()
  if (options?.variantId) {
    params.set('variant_id', options.variantId)
  }
  const query = params.toString()
  return request<JobReport>(`/reports/jobs/${encodeURIComponent(jobId)}${query ? `?${query}` : ''}`)
}

export const refineJobReport = (jobId: string, body: ReportRefineRequest) =>
  request<JobReport>(`/reports/jobs/${encodeURIComponent(jobId)}/refine`, {
    method: 'POST',
    body: JSON.stringify(body),
  })

export function getJobMemoryArtifactUrl(
  jobId: string,
  artifactName: 'selector-diagnostics' | 'working-memory' | 'distilled-memory-summary' | 'report-audit',
) {
  return `${BASE}/reports/jobs/${encodeURIComponent(jobId)}/artifacts/${artifactName}`
}

export interface WorkingMemoryArtifact {
  artifact_stage?: string
  paper_title?: string
  job_id?: string
  translation_language?: 'zh' | 'en'
  translation_generated_at?: number
  metrics?: Record<string, number | string | boolean | null>
  observations?: Array<{
    source?: string
    section_key?: string
    summary?: string
    evidence_refs?: string[]
    confidence?: number
    kind?: string
  }>
  open_questions?: Array<{
    question?: string
    section_key?: string
    reason?: string
    status?: string
    resolution_note?: string
  }>
  draft_claims?: Array<{
    section_key?: string
    claim?: string
    evidence_refs?: string[]
    importance?: string
    confidence?: number
  }>
  promotion_candidates?: Array<{
    status?: string
    candidate_type?: string
    confidence?: number
    source_section?: string
    payload?: {
      title?: string
      body?: string
      summary?: string
    }
  }>
  terminology_map?: Record<string, string>
  retrieved_context?: {
    interpreter_bundle?: {
      priority_claims?: unknown[]
      relevant_evidence?: unknown[]
      active_conflicts?: unknown[]
      related_papers?: unknown[]
    }
    translation_bundle?: {
      terminology_hints?: unknown[]
    }
  }
}

export interface SelectorDiagnosticsArtifact {
  topics?: Array<Record<string, unknown>>
  raw_candidates?: unknown[]
  candidate_count?: number
  ranked_count?: number
  selection_memory?: string
  selection_memory_bundle?: {
    high_level_digest?: unknown[]
    priority_claims?: unknown[]
    related_papers?: unknown[]
    keywords?: unknown[]
  }
  ranked_candidates?: unknown[]
  fit_passed_candidates?: unknown[]
  fit_judgments?: Array<{
    paper_id?: string
    title?: string
    fit_label?: string
    topic_fit_score?: number
    matched_aspects?: string[]
    mismatch_reasons?: string[]
  }>
  rejected_candidates?: unknown[]
  failure_reason?: string
  selected_paper_topic_audit?: {
    fit_label?: string
    topic_fit_score?: number
    matched_aspects?: string[]
    mismatch_reasons?: string[]
  } | null
  selected?: {
    title?: string
    paper_id?: string
    source?: string
    match_track?: string
  }
}

export const getJobWorkingMemoryArtifact = (jobId: string) =>
  requestJsonFromUrl<WorkingMemoryArtifact>(getJobMemoryArtifactUrl(jobId, 'working-memory'))

export const getJobLocalizedWorkingMemoryArtifact = (jobId: string, language: 'zh' | 'en' = 'zh') =>
  request<WorkingMemoryArtifact>(`/reports/jobs/${encodeURIComponent(jobId)}/working-memory-localized?language=${encodeURIComponent(language)}`)

export const getJobLocalizedDistilledSummary = (jobId: string, language: 'zh' | 'en' = 'zh') =>
  requestText(`/reports/jobs/${encodeURIComponent(jobId)}/distilled-summary-localized?language=${encodeURIComponent(language)}`)

export const getJobSelectorDiagnosticsArtifact = (jobId: string) =>
  requestJsonFromUrl<SelectorDiagnosticsArtifact>(getJobMemoryArtifactUrl(jobId, 'selector-diagnostics'))

export interface ReportAuditIssue {
  issue_type?: string
  severity?: string
  status?: string
  section_key?: string
  claim?: string
  evidence_refs?: string[]
  reason?: string
  repair_action?: string
}

export interface ReportAuditArtifact {
  generated_at?: number
  status?: string
  warning?: boolean
  repaired?: boolean
  severity_counts?: Record<string, number>
  removed_claims_by_section?: Record<string, string[]>
  issues?: ReportAuditIssue[]
}

export const getJobReportAuditArtifact = (jobId: string) =>
  requestJsonFromUrl<ReportAuditArtifact>(getJobMemoryArtifactUrl(jobId, 'report-audit'))

// Profiles
export interface Profile {
  id: number
  name: string
  description: string
  created_at: number
  last_used_at: number
  paper_count: number
}

export interface LocalizedText {
  en: string
  zh: string
  primary: string
}

export interface ProfileKnowledgeItem {
  id: number
  paper_id: string
  category: string
  content: string
  content_zh: string
  content_localized: LocalizedText
  relevance_score: number
  created_at: number
}

export interface ProfileLinkItem {
  id: number
  source_paper_id: string
  target_paper_id: string
  relation_type: string
  summary: string
  summary_zh: string
  summary_localized: LocalizedText
  created_at: number
}

export interface ProfileActivityItem {
  job_id: string
  job_status: string
  job_mode: string
  job_progress: number
  job_current_step: string
  job_paper_title: string
  job_report_path: string
  job_created_at: number
  job_started_at: number | null
  job_completed_at: number | null
  paper_row_id: number | null
  paper_id: string
  paper_title: string
  paper_venue: string
  paper_pub_date: string
  paper_pdf_path: string
  paper_source_path: string
  paper_source_type: string
  paper_report_path: string
  paper_created_at: number | null
}

export interface ProfileOverview {
  paper_source_count: number
  entity_count: number
  claim_count: number
  synthesis_count: number
  pending_review_count: number
  revision_count: number
  graph_node_count: number
  graph_edge_count: number
  theme_count: number
  gap_count: number
  high_priority_gap_count: number
  opportunity_count: number
  high_priority_opportunity_count: number
}

// --- Profile Brief ---
export interface BriefTheme {
  theme_key: string
  anchor: string
  anchor_zh: string
  anchor_type: string
  methods: string[]
  claim_count: number
  paper_count: number
  maturity: string
  summary: string
  summary_zh: string
  has_debate: boolean
  has_open_question: boolean
}

export interface BriefSynthesisPreview {
  title: string
  title_zh: string
  confidence: number
  claim_count: number
  paper_count: number
}

export interface BriefDebatePreview {
  title: string
  title_zh: string
  summary: string
  summary_zh: string
  claim_count: number
  paper_count: number
}

export interface BriefGapPreview {
  gap_key: string
  gap_type: string
  priority: string
  title: string
  title_zh: string
  title_localized: LocalizedText
  summary: string
  summary_zh: string
  summary_localized: LocalizedText
  theme_key: string
  theme_title: string
  theme_title_zh: string
}

export interface BriefOpenQuestion {
  title: string
  title_zh: string
}

export interface BriefConceptPreview {
  name: string
  name_zh: string
  type: string
  claim_count: number
}

export interface BriefFindingPreview {
  title: string
  title_zh: string
  body: string
  body_zh: string
  claim_type: string
  importance: number
}

export interface BriefDelta {
  paper_title: string
  paper_id: string
  new_entities: Array<{ name: string; type: string }>
  new_claims: Array<{ title: string; claim_type: string; stance: string }>
  reinforced_claims: Array<{ title: string; now_supported_by?: number }>
  challenged_claims: Array<{ title: string; conflict_type: string; triggered_review: boolean }>
  new_synthesis: Array<{ title: string; type: string }>
  updated_synthesis: Array<{ title: string; what_changed: string }>
  new_debates: Array<{ title: string }>
  impact_score: number
}

export interface ProfileBrief {
  profile_name: string
  paper_count: number
  generated_at: number
  stage: 'empty' | 'initial' | 'full'
  // initial stage
  key_concepts?: BriefConceptPreview[]
  core_findings?: BriefFindingPreview[]
  // full stage
  key_themes?: BriefTheme[]
  top_consensus?: BriefSynthesisPreview[]
  top_debates?: BriefDebatePreview[]
  open_questions?: BriefOpenQuestion[]
  gap_watchlist?: BriefGapPreview[]
  recent_delta?: BriefDelta | null
}

export interface ThemeEntityPreview {
  id: number
  name: string
  name_zh: string
  entity_type: string
  claim_count: number
}

export interface ThemeRepresentativeClaim {
  id: number
  title: string
  title_zh: string
  title_localized: LocalizedText
  importance: number
  evidence_count: number
  paper_id: string
}

export interface ThemeRepresentativeSynthesis {
  id: number
  item_type: string
  title: string
  title_zh: string
  title_localized: LocalizedText
  confidence: number
  claim_count: number
}

export interface ThemeItem {
  theme_key: string
  title: string
  title_zh: string
  title_localized: LocalizedText
  summary: string
  summary_zh: string
  summary_localized: LocalizedText
  maturity: string
  paper_count: number
  claim_count: number
  evidence_count: number
  consensus_count: number
  debate_count: number
  open_question_count: number
  pending_review_count: number
  anchor_entities: ThemeEntityPreview[]
  method_entities: ThemeEntityPreview[]
  representative_claims: ThemeRepresentativeClaim[]
  representative_synthesis: ThemeRepresentativeSynthesis[]
  paper_ids: string[]
  claim_ids: number[]
  synthesis_ids: number[]
  salience_score: number
}

export interface ThemeSnapshot {
  profile_id: number
  generated_at: number
  item_count: number
  items: ThemeItem[]
}

export interface GapItem {
  gap_key: string
  gap_type: string
  priority: string
  title: string
  title_zh: string
  title_localized: LocalizedText
  summary: string
  summary_zh: string
  summary_localized: LocalizedText
  theme_key: string
  theme_title: string
  theme_title_zh: string
  theme_title_localized: LocalizedText
  reason_codes: string[]
  claim_ids: number[]
  synthesis_ids: number[]
  review_ids: number[]
  paper_ids: string[]
  evidence_count: number
  updated_at: number
}

export interface GapSnapshot {
  profile_id: number
  generated_at: number
  item_count: number
  high_priority_count: number
  items: GapItem[]
}

export interface OpportunityItem {
  opportunity_key: string
  opportunity_type: string
  priority: string
  title: string
  title_zh: string
  title_localized: LocalizedText
  summary: string
  summary_zh: string
  summary_localized: LocalizedText
  theme_keys: string[]
  theme_titles: string[]
  theme_titles_zh: string[]
  theme_titles_localized: LocalizedText[]
  reason_codes: string[]
  claim_ids: number[]
  supporting_claim_ids: number[]
  conflicting_claim_ids: number[]
  synthesis_ids: number[]
  review_ids: number[]
  paper_ids: string[]
  suggested_validation_steps: string[]
  risk_flags: string[]
  updated_at: number
}

export interface OpportunitySnapshot {
  profile_id: number
  generated_at: number
  item_count: number
  high_priority_count: number
  items: OpportunityItem[]
}

export interface MemoryHealthSummary {
  unsupported_claim_count: number
  thin_evidence_claim_count: number
  contested_claim_count: number
  pending_review_count: number
  deprecated_claim_count: number
  scope_incomplete_claim_count: number
  orphan_evidence_count: number
  stale_artifact_count: number
}

export interface MemoryHealthIssue {
  issue_type: string
  severity: string
  title: string
  title_zh: string
  title_localized: LocalizedText
  count: number
  target_type: string
  target_ids: number[]
}

export interface MemoryHealth {
  profile_id: number
  generated_at: number
  status: string
  score: number
  summary: MemoryHealthSummary
  issues: MemoryHealthIssue[]
}

export interface FieldMapCluster {
  cluster_key: string
  cluster_type: string
  title: string
  title_zh: string
  title_localized: LocalizedText
  summary: string
  summary_zh: string
  summary_localized: LocalizedText
  maturity: string
  paper_count: number
  claim_count: number
  evidence_count: number
  controversy_count: number
  claim_ids: number[]
  paper_ids: string[]
}

export interface FieldMapLink {
  source_cluster_key: string
  target_cluster_key: string
  relation_type: string
  weight: number
  claim_relation_ids: number[]
  claim_ids: number[]
}

export interface FieldMapEntryPoint {
  audience: string
  title: string
  title_zh: string
  cluster_keys: string[]
  rationale: string
  rationale_zh: string
}

export interface FieldMapSnapshot {
  profile_id: number
  generated_at: number
  cluster_count: number
  link_count: number
  clusters: FieldMapCluster[]
  links: FieldMapLink[]
  entry_points: FieldMapEntryPoint[]
}

export interface EvidenceMatrixCell {
  evidence_id: number
  claim_id: number
  claim_title: string
  claim_title_zh: string
  claim_title_localized: LocalizedText
  method: string
  value: string
  baseline: string
  setting: string
  limitation: string
  scope_note: string
  paper_id: string
  section_key: string
  anchor_kind: string
  snippet: string
  snippet_zh: string
  snippet_localized: LocalizedText
  incomplete_fields: string[]
}

export interface EvidenceMatrixRow {
  row_key: string
  task: string
  dataset: string
  metric: string
  cell_count: number
  incomplete_count: number
  cells: EvidenceMatrixCell[]
}

export interface EvidenceMatrixSnapshot {
  profile_id: number
  generated_at: number
  row_count: number
  evidence_count: number
  incomplete_count: number
  rows: EvidenceMatrixRow[]
}

export interface SurveyMeta {
  exists: boolean
  stale: boolean
  updated_at: number
  section_count: number
  artifact_version: string
}

export interface LivingSurveyBlock {
  block_key: string
  title: string
  title_zh: string
  title_localized: LocalizedText
  summary: string
  summary_zh: string
  summary_localized: LocalizedText
  badges: string[]
  theme_key: string
  gap_key: string
  claim_ids: number[]
  synthesis_ids: number[]
  review_ids: number[]
  paper_ids: string[]
}

export interface LivingSurveySection {
  section_key: string
  title: string
  title_zh: string
  title_localized: LocalizedText
  summary: string
  summary_zh: string
  summary_localized: LocalizedText
  blocks: LivingSurveyBlock[]
}

export interface LivingSurvey {
  profile_id: number
  profile_name: string
  generated_at: number
  paper_count: number
  theme_count: number
  gap_count: number
  overview_localized: LocalizedText
  sections: LivingSurveySection[]
}

export interface ProfileDetail {
  profile: Profile
  overview: ProfileOverview
  brief?: ProfileBrief | null
  theme_preview: ThemeItem[]
  gap_preview: GapItem[]
  opportunity_preview: OpportunityItem[]
  health?: MemoryHealth | null
  field_map_preview: FieldMapCluster[]
  survey_meta?: SurveyMeta | null
  knowledge: ProfileKnowledgeItem[]
  curated_digest: CuratedDomainDigestSection[]
  style: Record<string, string>
  links: ProfileLinkItem[]
  activity: ProfileActivityItem[]
}

export interface ProfileJobMemoryDeleteResult {
  profile_id: number
  job_id: string
  paper_id: string
  deleted_job_ids: string[]
  deleted_writeback_count: number
  deleted_knowledge_events: number
  deleted_style_events: number
  deleted_link_events: number
  deleted_evidence: number
  deleted_claims: number
  deleted_synthesis: number
  deleted_edges: number
  deleted_orphaned_claims: number
  deleted_orphaned_synthesis: number
  deleted_orphaned_entities: number
  provenance_mode: string
  provenance_modes: string[]
  approximate: boolean
  deleted_at: number
}

export type ProfilePaperMemoryDeleteResult = ProfileJobMemoryDeleteResult

export interface ProfileMemoryRebuildResult {
  profile_id: number
  rebuilt_items: number
  active_claims: number
}

export interface ProfileDeleteResult {
  profile_id: number
  deleted_profile: boolean
  purged_job_count: number
  deleted_report_count: number
  deleted_paper_record_count: number
  deleted_writeback_count: number
  results_dirs_removed: number
  fetch_dirs_removed: number
  cache_dirs_removed: number
  blocked_active_job_ids: string[]
}

export interface ProfilePaperMoveResult {
  source_profile_id: number
  target_profile_id: number
  moved_job_ids: string[]
  moved_paper_ids: string[]
  moved_writeback_count: number
  moved_claim_count: number
  moved_synthesis_count: number
  moved_edge_count: number
  merged_edge_count: number
  moved_entity_count: number
  cloned_entity_count: number
  relinked_entity_count: number
  source_active_writeback_count: number
  target_active_writeback_count: number
  source_rebuilt_items: number
  source_active_claims: number
  target_rebuilt_items: number
  target_active_claims: number
}

export const getProfiles = () => request<Profile[]>('/profiles')
export const getProfile = (profileId: number) => request<Profile>(`/profiles/${profileId}`)
export const getProfileBrief = (profileId: number) =>
  request<ProfileBrief>(`/profiles/${profileId}/brief`)
export const getProfileDetail = (profileId: number) =>
  request<ProfileDetail>(`/profiles/${profileId}/detail`)
export const getProfileSurvey = (profileId: number) =>
  request<LivingSurvey>(`/profiles/${profileId}/survey`)
export const rebuildProfileMemory = (profileId: number) =>
  request<ProfileMemoryRebuildResult>(`/profiles/${profileId}/rebuild`, { method: 'POST' })
export const deleteProfileJobMemory = (profileId: number, jobId: string) =>
  request<ProfileJobMemoryDeleteResult>(`/profiles/${profileId}/jobs/${encodeURIComponent(jobId)}/memory`, {
    method: 'DELETE',
  })
export const deleteProfilePaperMemory = (profileId: number, paperId: string) =>
  request<ProfilePaperMemoryDeleteResult>(`/profiles/${profileId}/papers/${encodeURIComponent(paperId)}/memory`, {
    method: 'DELETE',
  })
export const deleteProfile = (profileId: number) =>
  request<ProfileDeleteResult>(`/profiles/${profileId}`, { method: 'DELETE' })
export const moveProfilePapers = (sourceProfileId: number, body: { target_profile_id: number; job_ids: string[] }) =>
  request<ProfilePaperMoveResult>(`/profiles/${sourceProfileId}/move-papers`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
export const createProfile = (name: string, description = '') =>
  request<Profile>('/profiles', { method: 'POST', body: JSON.stringify({ name, description }) })

// Memory workspace
export interface MemoryEntity {
  id: number
  profile_id: number
  canonical_name: string
  canonical_name_zh: string
  name_localized: LocalizedText
  normalized_name: string
  entity_type: string
  summary: string
  summary_zh: string
  summary_localized: LocalizedText
  manual_locked: boolean
  status: string
  created_at: number
  updated_at: number
  deleted_at: number | null
  claim_count: number
}

export interface MemoryClaim {
  id: number
  profile_id: number
  origin_writeback_id: number | null
  claim_key: string
  title: string
  title_zh: string
  title_localized: LocalizedText
  body: string
  body_zh: string
  body_localized: LocalizedText
  claim_type: string
  stance: string
  importance: number
  status: string
  default_resolution: string
  default_resolution_zh: string
  default_resolution_localized: LocalizedText
  scope: Record<string, unknown>
  lifecycle_state: string
  lifecycle_reason: Record<string, unknown>
  superseded_by_claim_id: number | null
  last_lifecycle_update_at: number | null
  stability_score: number
  last_supported_at: number | null
  last_challenged_at: number | null
  review_status: string
  manual_locked: boolean
  created_at: number
  updated_at: number
  deleted_at: number | null
  evidence_count: number
  job_id: string
  paper_id: string
  entity_names: string[]
}

export interface MemoryEvidence {
  id: number
  claim_id: number
  writeback_id: number | null
  section_key: string
  section_title: string
  section_title_zh: string
  section_title_localized: LocalizedText
  snippet: string
  snippet_zh: string
  snippet_localized: LocalizedText
  evidence_summary: string
  evidence_summary_zh: string
  evidence_summary_localized: LocalizedText
  page_label: string
  page_start: number | null
  page_end: number | null
  anchor_kind: string
  context_before: string
  context_after: string
  structured_signal: Record<string, unknown>
  structured_signal_json: string
  weight: number
  manual_locked: boolean
  created_at: number
  updated_at: number
  deleted_at: number | null
  claim_title: string
  claim_title_zh: string
  claim_title_localized: LocalizedText
  claim_key: string
  job_id: string
  paper_id: string
}

export interface MemorySynthesisItem {
  id: number
  profile_id: number
  origin_writeback_id: number | null
  synthesis_key: string
  item_type: string
  title: string
  title_zh: string
  title_localized: LocalizedText
  summary: string
  summary_zh: string
  summary_localized: LocalizedText
  confidence: number
  status: string
  default_resolution: string
  default_resolution_zh: string
  default_resolution_localized: LocalizedText
  review_status: string
  manual_locked: boolean
  created_at: number
  updated_at: number
  deleted_at: number | null
  claim_ids: number[]
}

export interface MemoryEditableGraphEdge {
  id: number
  profile_id: number
  origin_writeback_id: number | null
  source_kind: string
  source_ref: string
  target_kind: string
  target_ref: string
  relation_type: string
  summary: string
  summary_zh: string
  summary_localized: LocalizedText
  weight: number
  manual_locked: boolean
  created_at: number
  updated_at: number
  deleted_at: number | null
}

export interface MemoryGraphNode {
  id: string
  label: string
  label_zh: string
  label_localized: LocalizedText
  node_type: string
  summary: string
  summary_zh: string
  summary_localized: LocalizedText
  status: string
  ref: string
  degree: number
}

export interface MemoryGraphEdge {
  id: string | number
  source_id: string
  target_id: string
  relation_type: string
  summary: string
  summary_zh: string
  summary_localized: LocalizedText
  weight: number
}

export interface MemoryGraphSnapshot {
  nodes: MemoryGraphNode[]
  edges: MemoryGraphEdge[]
}

export interface CuratedTopEvidence {
  id: number
  section_key: string
  section_title_localized: LocalizedText
  snippet_localized: LocalizedText
  evidence_summary_localized: LocalizedText
  page_label: string
}

export interface CuratedSynthesisPreview {
  id: number
  title_localized: LocalizedText
  summary_localized: LocalizedText
  confidence: number
  claim_count: number
  review_status: string
  manual_locked: boolean
}

export interface CuratedDomainDigestSection {
  section_type: string
  section_label: string
  section_label_zh: string
  items: CuratedSynthesisPreview[]
}

export interface CuratedPriorityClaim {
  id: number
  title_localized: LocalizedText
  summary_localized: LocalizedText
  claim_type: string
  stance: string
  importance: number
  evidence_count: number
  top_evidence: CuratedTopEvidence | null
  entity_names: string[]
  paper_id: string
  review_status: string
  manual_locked: boolean
}

export interface CuratedActiveConflict {
  review_id: number
  target_type: string
  target_id: number
  title_localized: LocalizedText
  default_resolution_localized: LocalizedText
  review_type: string
  has_suggested_payload: boolean
}

export interface CuratedEntityPreview {
  id: number
  name_localized: LocalizedText
  claim_count: number
  manual_locked: boolean
}

export interface CuratedEntityCluster {
  entity_type: string
  label_zh: string
  count: number
  top_entities: CuratedEntityPreview[]
}

export interface CuratedSourceBundle {
  job_id: string
  paper_id: string
  paper_title: string
  created_at: number
  claim_count: number
  entity_count: number
  synthesis_count: number
}

export interface CuratedWorkspace {
  domain_digest: CuratedDomainDigestSection[]
  priority_claims: CuratedPriorityClaim[]
  active_conflicts: CuratedActiveConflict[]
  source_bundles: CuratedSourceBundle[]
  entity_clusters: CuratedEntityCluster[]
}

export interface MemoryReviewItem {
  id: number
  profile_id: number
  target_type: string
  target_id: number
  review_type: string
  title: string
  title_zh: string
  title_localized: LocalizedText
  description: string
  description_zh: string
  description_localized: LocalizedText
  default_resolution: string
  default_resolution_zh: string
  default_resolution_localized: LocalizedText
  suggested_payload: unknown
  status: string
  reminder_active: boolean
  resolution_note: string
  created_at: number
  updated_at: number
  resolved_at: number | null
}

export interface MemoryRevisionEntry {
  id: number
  profile_id: number
  target_type: string
  target_id: string
  action: string
  actor_type: string
  summary: string
  summary_zh: string
  summary_localized: LocalizedText
  before_json: unknown
  after_json: unknown
  writeback_id: number | null
  created_at: number
}

export interface MemoryTimelineItem {
  id: string
  item_type: string
  title: string
  title_zh: string
  title_localized: LocalizedText
  summary: string
  summary_zh: string
  summary_localized: LocalizedText
  timestamp: number
  status: string
  target_type: string
  target_id: string
  source_job_id: string
  source_paper_id: string
  bundle_label: string
}

export interface MemoryWorkspaceSnapshot {
  profile: Profile | null
  overview: ProfileOverview
  knowledge_items: ProfileKnowledgeItem[]
  style: Record<string, string>
  links: ProfileLinkItem[]
  entities: MemoryEntity[]
  claims: MemoryClaim[]
  evidence_fragments: MemoryEvidence[]
  synthesis_items: MemorySynthesisItem[]
  editable_edges: MemoryEditableGraphEdge[]
  graph: MemoryGraphSnapshot
  reviews: MemoryReviewItem[]
  revisions: MemoryRevisionEntry[]
  timeline: MemoryTimelineItem[]
  curated: CuratedWorkspace
  themes?: ThemeSnapshot | null
  gaps?: GapSnapshot | null
  opportunities?: OpportunitySnapshot | null
  health?: MemoryHealth | null
  field_map?: FieldMapSnapshot | null
  evidence_matrix?: EvidenceMatrixSnapshot | null
}

export interface MemoryEntityInput {
  name: string
  entity_type?: string
  summary?: string
  aliases?: string[]
}

export interface MemoryClaimInput {
  claim_key?: string
  title?: string
  body?: string
  claim_type?: string
  stance?: string
  importance?: number
  status?: string
  default_resolution?: string
  scope?: {
    conditions?: string[]
    boundary?: string
    population?: string
    notes?: string
  }
  entity_names?: string[]
}

export interface MemoryEvidenceInput {
  claim_id: number
  section_key?: string
  section_title?: string
  snippet: string
  evidence_summary?: string
  page_label?: string
  page_start?: number | null
  page_end?: number | null
  anchor_kind?: string
  context_before?: string
  context_after?: string
  structured_signal?: Record<string, unknown>
}

export interface MemorySynthesisInput {
  synthesis_key?: string
  item_type?: string
  title?: string
  summary?: string
  confidence?: number
  status?: string
  default_resolution?: string
  claim_ids?: number[]
}

export interface MemoryGraphEdgeInput {
  source_kind?: string
  source_ref: string
  target_kind?: string
  target_ref: string
  relation_type?: string
  summary?: string
  weight?: number
}

export interface MemoryReviewResolveInput {
  resolution_note?: string
  adopt_suggested?: boolean
  dismiss?: boolean
}

export const getWorkspaceSnapshot = (profileId: number) =>
  request<MemoryWorkspaceSnapshot>(`/profiles/${profileId}/workspace`)
export const getWorkspaceOverview = (profileId: number) =>
  request<ProfileOverview>(`/profiles/${profileId}/workspace/overview`)
export const getWorkspaceCurated = (profileId: number) =>
  request<CuratedWorkspace>(`/profiles/${profileId}/workspace/curated`)
export const getWorkspaceThemes = (profileId: number) =>
  request<ThemeSnapshot>(`/profiles/${profileId}/workspace/themes`)
export const getWorkspaceGaps = (profileId: number) =>
  request<GapSnapshot>(`/profiles/${profileId}/workspace/gaps`)
export const getWorkspaceOpportunities = (profileId: number) =>
  request<OpportunitySnapshot>(`/profiles/${profileId}/workspace/opportunities`)
export const getWorkspaceHealth = (profileId: number) =>
  request<MemoryHealth>(`/profiles/${profileId}/workspace/health`)
export const getWorkspaceFieldMap = (profileId: number) =>
  request<FieldMapSnapshot>(`/profiles/${profileId}/workspace/field-map`)
export const getWorkspaceEvidenceMatrix = (profileId: number) =>
  request<EvidenceMatrixSnapshot>(`/profiles/${profileId}/workspace/evidence-matrix`)
export const getWorkspaceEntities = (profileId: number, limit = 200) =>
  request<MemoryEntity[]>(`/profiles/${profileId}/workspace/entities?limit=${limit}`)
export const getWorkspaceClaims = (profileId: number, limit = 200) =>
  request<MemoryClaim[]>(`/profiles/${profileId}/workspace/claims?limit=${limit}`)
export const getWorkspaceEvidence = (profileId: number, limit = 300) =>
  request<MemoryEvidence[]>(`/profiles/${profileId}/workspace/evidence?limit=${limit}`)
export const getWorkspaceSynthesis = (profileId: number, limit = 160) =>
  request<MemorySynthesisItem[]>(`/profiles/${profileId}/workspace/synthesis?limit=${limit}`)
export const getWorkspaceGraph = (profileId: number) =>
  request<MemoryGraphSnapshot>(`/profiles/${profileId}/workspace/graph`)
export const getWorkspaceGraphEdges = (profileId: number, limit = 200) =>
  request<MemoryEditableGraphEdge[]>(`/profiles/${profileId}/workspace/graph/edges?limit=${limit}`)
export const getWorkspaceReviews = (profileId: number, limit = 120) =>
  request<MemoryReviewItem[]>(`/profiles/${profileId}/workspace/reviews?limit=${limit}`)
export const getWorkspaceRevisions = (profileId: number, limit = 160) =>
  request<MemoryRevisionEntry[]>(`/profiles/${profileId}/workspace/revisions?limit=${limit}`)
export const getWorkspaceTimeline = (profileId: number) =>
  request<MemoryTimelineItem[]>(`/profiles/${profileId}/workspace/timeline`)
export const createWorkspaceEntity = (profileId: number, body: MemoryEntityInput) =>
  request<MemoryEntity>(`/profiles/${profileId}/workspace/entities`, { method: 'POST', body: JSON.stringify(body) })
export const updateWorkspaceEntity = (profileId: number, entityId: number, body: MemoryEntityInput) =>
  request<MemoryEntity>(`/profiles/${profileId}/workspace/entities/${entityId}`, { method: 'PUT', body: JSON.stringify(body) })
export const deleteWorkspaceEntity = (profileId: number, entityId: number) =>
  request<{ deleted: boolean }>(`/profiles/${profileId}/workspace/entities/${entityId}`, { method: 'DELETE' })

export const createWorkspaceClaim = (profileId: number, body: MemoryClaimInput) =>
  request<MemoryClaim>(`/profiles/${profileId}/workspace/claims`, { method: 'POST', body: JSON.stringify(body) })
export const updateWorkspaceClaim = (profileId: number, claimId: number, body: MemoryClaimInput) =>
  request<MemoryClaim>(`/profiles/${profileId}/workspace/claims/${claimId}`, { method: 'PUT', body: JSON.stringify(body) })
export const deleteWorkspaceClaim = (profileId: number, claimId: number) =>
  request<{ deleted: boolean }>(`/profiles/${profileId}/workspace/claims/${claimId}`, { method: 'DELETE' })

export const createWorkspaceEvidence = (profileId: number, body: MemoryEvidenceInput) =>
  request<MemoryEvidence>(`/profiles/${profileId}/workspace/evidence`, { method: 'POST', body: JSON.stringify(body) })
export const updateWorkspaceEvidence = (profileId: number, evidenceId: number, body: MemoryEvidenceInput) =>
  request<MemoryEvidence>(`/profiles/${profileId}/workspace/evidence/${evidenceId}`, { method: 'PUT', body: JSON.stringify(body) })
export const deleteWorkspaceEvidence = (profileId: number, evidenceId: number) =>
  request<{ deleted: boolean }>(`/profiles/${profileId}/workspace/evidence/${evidenceId}`, { method: 'DELETE' })

export const createWorkspaceSynthesis = (profileId: number, body: MemorySynthesisInput) =>
  request<MemorySynthesisItem>(`/profiles/${profileId}/workspace/synthesis`, { method: 'POST', body: JSON.stringify(body) })
export const updateWorkspaceSynthesis = (profileId: number, synthesisId: number, body: MemorySynthesisInput) =>
  request<MemorySynthesisItem>(`/profiles/${profileId}/workspace/synthesis/${synthesisId}`, { method: 'PUT', body: JSON.stringify(body) })
export const deleteWorkspaceSynthesis = (profileId: number, synthesisId: number) =>
  request<{ deleted: boolean }>(`/profiles/${profileId}/workspace/synthesis/${synthesisId}`, { method: 'DELETE' })

export const createWorkspaceGraphEdge = (profileId: number, body: MemoryGraphEdgeInput) =>
  request<MemoryEditableGraphEdge>(`/profiles/${profileId}/workspace/graph/edges`, { method: 'POST', body: JSON.stringify(body) })
export const updateWorkspaceGraphEdge = (profileId: number, edgeId: number, body: MemoryGraphEdgeInput) =>
  request<MemoryEditableGraphEdge>(`/profiles/${profileId}/workspace/graph/edges/${edgeId}`, { method: 'PUT', body: JSON.stringify(body) })
export const deleteWorkspaceGraphEdge = (profileId: number, edgeId: number) =>
  request<{ deleted: boolean }>(`/profiles/${profileId}/workspace/graph/edges/${edgeId}`, { method: 'DELETE' })

export const resolveWorkspaceReview = (profileId: number, reviewId: number, body: MemoryReviewResolveInput) =>
  request<MemoryReviewItem>(`/profiles/${profileId}/workspace/reviews/${reviewId}/resolve`, { method: 'POST', body: JSON.stringify(body) })

// Papers
export interface Paper {
  id: number
  job_id: string | null
  paper_id: string
  title: string
  venue: string
  pub_date: string
  source: string
  match_track: string
  pdf_path: string
  source_path: string
  source_type: string
  report_path: string
  created_at: number
}
export const getPaperPdfUrl = (paperDbId: number) => `${BASE}/papers/${paperDbId}/pdf`
export const getPapers = (limit = 50, search = '') =>
  request<Paper[]>(`/papers?limit=${limit}&search=${encodeURIComponent(search)}`)
