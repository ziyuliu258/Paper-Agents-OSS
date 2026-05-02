import type { Job, JobConfigSnapshot, KeywordGroup } from '@/api/client'

export const RUN_PAGE_STORAGE_KEY = 'paper-agent.run-page-session'
export const LEGACY_ACTIVE_JOB_ID_KEY = 'activeJobId'

export interface ManualFileMeta {
  name: string
  size: number
}

export interface PersistedRunPageState {
  track: string
  dateRangeDays: string
  classicMinCitations: string
  venues: string[]
  structureMode: string
  topicName: string
  topicQuery: string
  defaultTopicName: string
  defaultTopicQuery: string
  topicKeywords: string[]
  topicFitGateThreshold: string
  postDownloadTopicFitThreshold: string
  profileId?: number
  profileMode?: 'auto' | 'explicit'
  mode: 'auto' | 'manual'
  manualUploadError: string
  keywordGroups: KeywordGroup[]
  keywordSuggestError: string
  trackedJobIds: string[]
  selectedJobId: string | null
  manualFileMeta: ManualFileMeta | null
}

export interface NormalizedRunConfig {
  track: string
  dateRangeDays: string
  classicMinCitations: string
  venues: string[]
  structureMode: string
  topicName: string
  topicQuery: string
  topicKeywords: string[]
  topicFitGateThreshold: string
  postDownloadTopicFitThreshold: string
}

type ConfigSource = Partial<JobConfigSnapshot> | Record<string, unknown> | null | undefined

function normalizeTrackedJobIds(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return []
  }
  const seen = new Set<string>()
  const jobIds: string[] = []
  for (const item of value) {
    const normalized = String(item || '').trim()
    if (!normalized || seen.has(normalized)) {
      continue
    }
    seen.add(normalized)
    jobIds.push(normalized)
  }
  return jobIds
}

function normalizeStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.map((item) => String(item).trim()).filter(Boolean)
}

export function normalizeRunConfigSource(source: ConfigSource): NormalizedRunConfig {
  const snapshot = source && typeof source === 'object' ? source : {}
  const selection = snapshot.selection && typeof snapshot.selection === 'object'
    ? snapshot.selection as Record<string, unknown>
    : undefined
  const topics = Array.isArray(snapshot.topics)
    ? snapshot.topics as Array<Record<string, unknown>>
    : []
  const report = snapshot.report && typeof snapshot.report === 'object'
    ? snapshot.report as Record<string, unknown>
    : undefined
  const topic = topics[0]

  return {
    track: String(selection?.track || 'auto'),
    dateRangeDays: String(selection?.date_range_days ?? 7),
    classicMinCitations: String(selection?.classic_min_citations ?? 50),
    venues: normalizeStringArray(selection?.preferred_venues),
    structureMode: String(report?.structure_mode || 'classic'),
    topicName: String(topic?.name || '').trim(),
    topicQuery: String(topic?.query || '').trim(),
    topicKeywords: normalizeStringArray(topic?.keywords),
    topicFitGateThreshold: String(selection?.topic_fit_gate_threshold ?? 0.72),
    postDownloadTopicFitThreshold: String(selection?.post_download_topic_fit_threshold ?? 0.55),
  }
}

export function buildPersistedRunPageStateFromJob(
  job: Pick<Job, 'id' | 'mode' | 'profile_id' | 'profile_mode' | 'config_snapshot'>,
): PersistedRunPageState {
  const normalized = normalizeRunConfigSource(job.config_snapshot)

  return {
    ...normalized,
    defaultTopicName: normalized.topicName,
    defaultTopicQuery: normalized.topicQuery,
    profileId: typeof job.profile_id === 'number' ? job.profile_id : undefined,
    profileMode: job.profile_mode === 'explicit' ? 'explicit' : 'auto',
    mode: job.mode === 'manual' ? 'manual' : 'auto',
    manualUploadError: '',
    keywordGroups: [],
    keywordSuggestError: '',
    trackedJobIds: [job.id],
    selectedJobId: job.id,
    manualFileMeta: null,
  }
}

export function loadPersistedRunPageState(): Partial<PersistedRunPageState> {
  if (typeof window === 'undefined') {
    return {}
  }

  try {
    const raw = window.localStorage.getItem(RUN_PAGE_STORAGE_KEY)
    const legacyActiveJobId = window.localStorage.getItem(LEGACY_ACTIVE_JOB_ID_KEY)
    if (!raw) {
      return legacyActiveJobId
        ? {
            trackedJobIds: [legacyActiveJobId],
            selectedJobId: legacyActiveJobId,
          }
        : {}
    }

    const parsed = JSON.parse(raw) as Partial<PersistedRunPageState> & {
      jobId?: string | null
      activeJobId?: string | null
    }
    if (typeof parsed !== 'object' || parsed === null) {
      return {}
    }

    const trackedJobIds = normalizeTrackedJobIds(parsed.trackedJobIds)
    const fallbackJobId = typeof parsed.selectedJobId === 'string' && parsed.selectedJobId.trim()
      ? parsed.selectedJobId.trim()
      : (
        typeof parsed.activeJobId === 'string' && parsed.activeJobId.trim()
          ? parsed.activeJobId.trim()
          : (
            typeof parsed.jobId === 'string' && parsed.jobId.trim()
              ? parsed.jobId.trim()
              : (legacyActiveJobId || null)
          )
      )
    const mergedTrackedJobIds = normalizeTrackedJobIds(
      fallbackJobId && !trackedJobIds.includes(fallbackJobId)
        ? [fallbackJobId, ...trackedJobIds]
        : trackedJobIds,
    )

    return {
      ...parsed,
      trackedJobIds: mergedTrackedJobIds,
      selectedJobId: fallbackJobId && mergedTrackedJobIds.includes(fallbackJobId)
        ? fallbackJobId
        : (mergedTrackedJobIds[0] || null),
    }
  } catch {
    return {}
  }
}

export function persistRunPageState(state: PersistedRunPageState) {
  if (typeof window === 'undefined') {
    return
  }

  window.localStorage.setItem(RUN_PAGE_STORAGE_KEY, JSON.stringify(state))
  const legacyJobId = state.selectedJobId || state.trackedJobIds[0] || null
  if (legacyJobId) {
    window.localStorage.setItem(LEGACY_ACTIVE_JOB_ID_KEY, legacyJobId)
  } else {
    window.localStorage.removeItem(LEGACY_ACTIVE_JOB_ID_KEY)
  }
}

export function clearPersistedRunPageState() {
  if (typeof window === 'undefined') {
    return
  }

  window.localStorage.removeItem(RUN_PAGE_STORAGE_KEY)
  window.localStorage.removeItem(LEGACY_ACTIVE_JOB_ID_KEY)
}
