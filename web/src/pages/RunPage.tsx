import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { Input } from '@/components/ui/input'
import { Brain, ChevronDown, ChevronRight, FileJson, Lock, Play, Square, FileText, Plus, Sparkles, Upload, X } from 'lucide-react'
import LogStream from '@/components/LogStream'
import { useJobWebSocket } from '@/hooks/useJobWebSocket'
import {
  cancelJob,
  createJob,
  createManualJob,
  forceStopJob,
  generateTopicKeywords,
  getConfig,
  getJob,
  getJobMemoryArtifactUrl,
  getJobWorkingMemoryArtifact,
  getProfiles,
  type Job,
  type KeywordGroup,
  type Profile,
  type WorkingMemoryArtifact,
} from '@/api/client'
import CreateProfileDialog from '@/components/CreateProfileDialog'
import KeywordTokenInput from '@/components/KeywordTokenInput'
import { mergeKeywordTokens, normalizeKeywordToken } from '@/components/keywordTokens'
import InfoHint from '@/components/ui/info-hint'
import { clipText } from '@/lib/formatters'
import { cn } from '@/lib/utils'
import {
  loadPersistedRunPageState,
  type ManualFileMeta,
  normalizeRunConfigSource,
  persistRunPageState,
  type PersistedRunPageState,
} from '@/lib/jobConfig'
import { mergeWithLocalDefaultRunConfig } from '@/lib/runtimeSettings'

const ALL_VENUES = ['ICLR', 'NeurIPS', 'ICML', 'AAAI', 'CVPR', 'IJCAI', 'ACL', 'ECCV', 'EMNLP']
const TRACKS = [
  { value: 'auto', label: 'Auto', desc: 'Balance recent papers and classic high-impact work.' },
  { value: 'recent', label: 'Recent', desc: 'Prioritize newer papers from your preferred venues.' },
  { value: 'classic', label: 'Classic', desc: 'Prioritize influential papers with higher citation counts.' },
  { value: 'goat', label: 'GOAT', desc: 'Mix recent momentum with established classics.' },
] as const
const REPORT_STRUCTURE_MODES = [
  {
    value: 'classic',
    label: 'Current Structure',
    desc: 'Keep the existing section recipe: background, method, experiments, ablation, limitations, and summary.',
  },
  {
    value: 'pmrc',
    label: 'PMRC Narrative',
    desc: 'Reframe the report into a presentation-style flow: problem and motivation -> method -> results -> conclusion.',
  },
] as const

const DEFAULT_PROFILE_NOTE = 'If you do not pick a profile, this run will auto-match an existing profile after reading the paper, or create a new one when none fits.'
const EMPTY_PROFILE_DESCRIPTION = 'No description yet. Add a short note so this profile is easier to recognize from the Run page.'
const MAX_MANUAL_FILE_SIZE_BYTES = 100 * 1024 * 1024
const MIN_DATE_RANGE_DAYS = 30
const DATE_RANGE_ERROR_MESSAGE = `Date Range must be at least ${MIN_DATE_RANGE_DAYS} days.`
const VENUE_ERROR_MESSAGE = 'Select at least one preferred venue.'
const WORKING_MEMORY_POLL_INTERVAL_MS = 2500
const TRACKED_JOB_POLL_INTERVAL_MS = 5000
const MAX_TRACKED_JOBS = 8
const TERMINAL_JOB_STATUSES = new Set(['completed', 'failed'])

const GROUP_LABELS: Record<string, string> = {
  'Core Tasks': 'Task Keywords',
  'Core Problems': 'Problem Keywords',
  'Representative Models': 'Model Keywords',
}

interface RetryDraftState {
  token: string
  jobId: string
  replaceJobId?: string | null
  profileId?: number | null
  profileMode?: 'auto' | 'explicit'
  mode: 'auto'
  structureMode: string
  topicName: string
  topicQuery: string
  topicKeywords: string[]
  topicFitGateThreshold: string
  postDownloadTopicFitThreshold: string
  track: string
  dateRangeDays: string
  classicMinCitations: string
  venues: string[]
}

function formatRequestError(error: unknown, fallbackMessage = 'Failed to complete the request. Please try again.') {
  if (!(error instanceof Error)) {
    return fallbackMessage
  }
  const raw = error.message.replace(/^\d+:\s*/, '').trim()
  try {
    const payload = JSON.parse(raw) as { detail?: string }
    if (typeof payload.detail === 'string' && payload.detail.trim()) {
      return payload.detail.trim()
    }
  } catch {
    return raw || fallbackMessage
  }
  return raw || fallbackMessage
}

function isSameTopicName(left: string, right: string) {
  const normalizedLeft = normalizeKeywordToken(left)
  const normalizedRight = normalizeKeywordToken(right)
  return Boolean(normalizedLeft) && normalizedLeft.toLocaleLowerCase() === normalizedRight.toLocaleLowerCase()
}

function resolveTopicQuery(topicName: string, topicQuery: string, defaultTopicName: string, defaultTopicQuery: string) {
  const normalizedTopicName = normalizeKeywordToken(topicName)
  const normalizedTopicQuery = normalizeKeywordToken(topicQuery)
  const normalizedDefaultTopicQuery = normalizeKeywordToken(defaultTopicQuery)

  if (isSameTopicName(topicName, defaultTopicName)) {
    return normalizedDefaultTopicQuery || normalizedTopicQuery || normalizedTopicName
  }

  return normalizedTopicQuery || normalizedTopicName
}

function formatFileSize(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return '0 B'
  }

  const units = ['B', 'KB', 'MB', 'GB']
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / 1024 ** exponent
  return `${value >= 10 || exponent === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[exponent]}`
}

function isPdfFile(file: File) {
  const lowerName = file.name.toLocaleLowerCase()
  return lowerName.endsWith('.pdf') || file.type === 'application/pdf'
}

function normalizePositiveInteger(value: string, fallback: number, min = 1) {
  const parsed = Number.parseInt(value, 10)
  if (!Number.isFinite(parsed)) {
    return fallback
  }
  return Math.max(min, parsed)
}

function validateDateRangeValue(value: string) {
  const trimmed = value.trim()
  if (!trimmed) {
    return DATE_RANGE_ERROR_MESSAGE
  }

  const parsed = Number.parseInt(trimmed, 10)
  if (!Number.isFinite(parsed) || parsed < MIN_DATE_RANGE_DAYS) {
    return DATE_RANGE_ERROR_MESSAGE
  }

  return ''
}

function sanitizeDateRangeValue(value: string) {
  return String(normalizePositiveInteger(value, MIN_DATE_RANGE_DAYS, MIN_DATE_RANGE_DAYS))
}

function sanitizeVenues(value: string[]) {
  return value.filter(Boolean)
}

function formatStageLabel(stage?: string) {
  switch (stage) {
    case 'paper_notes_ready':
      return 'Paper Notes Ready'
    case 'memory_context_ready':
      return 'Memory Context Ready'
    case 'tasks_complete':
      return 'Tasks Complete'
    case 'writeback_ready':
      return 'Writeback Ready'
    case 'report_assembled':
      return 'Report Assembled'
    default:
      return 'Preparing'
  }
}

function isMissingArtifactError(error: unknown) {
  return error instanceof Error && error.message.startsWith('404:')
}

function dedupeTrackedJobIds(jobIds: Array<string | null | undefined>) {
  const seen = new Set<string>()
  const deduped: string[] = []
  for (const jobId of jobIds) {
    const normalized = String(jobId || '').trim()
    if (!normalized || seen.has(normalized)) {
      continue
    }
    seen.add(normalized)
    deduped.push(normalized)
  }
  return deduped
}

function isTerminalJobStatus(status?: string) {
  return TERMINAL_JOB_STATUSES.has(String(status || '').trim())
}

function getTrackedJobTitle(job: Job) {
  if (job.paper_title.trim()) {
    return job.paper_title.trim()
  }

  if (job.mode === 'manual') {
    return 'Manual PDF job'
  }

  const normalized = normalizeRunConfigSource(job.config_snapshot)
  return normalized.topicName || normalized.topicQuery || 'Auto search job'
}

function getTrackedJobSubtitle(job: Job) {
  const normalized = normalizeRunConfigSource(job.config_snapshot)
  if (job.mode === 'manual') {
    return 'Manual upload pipeline'
  }
  if (normalized.topicQuery && normalized.topicQuery !== normalized.topicName) {
    return normalized.topicQuery
  }
  return normalized.topicName || 'Auto search pipeline'
}

function getStatusVariant(status?: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  switch (status) {
    case 'completed':
      return 'default'
    case 'failed':
      return 'destructive'
    case 'pending':
      return 'outline'
    default:
      return 'secondary'
  }
}

export default function RunPage() {
  const location = useLocation()
  const persistedState = useMemo(loadPersistedRunPageState, [])
  const retryDraft = (location.state as { retryDraft?: RetryDraftState } | null)?.retryDraft
  const initialTrackedJobIds = useMemo(
    () => dedupeTrackedJobIds(persistedState.trackedJobIds || []),
    [persistedState.trackedJobIds],
  )
  const initialSelectedJobId = useMemo(
    () => (
      typeof persistedState.selectedJobId === 'string' && persistedState.selectedJobId.trim()
        ? persistedState.selectedJobId.trim()
        : (initialTrackedJobIds[0] || null)
    ),
    [initialTrackedJobIds, persistedState.selectedJobId],
  )
  const hasPersistedSelection = typeof persistedState.track === 'string'
    || typeof persistedState.dateRangeDays === 'string'
    || typeof persistedState.classicMinCitations === 'string'
    || Array.isArray(persistedState.venues)
  const hasPersistedReportConfig = typeof persistedState.structureMode === 'string'
  const hasPersistedTopic = typeof persistedState.topicName === 'string' || typeof persistedState.topicQuery === 'string' || Array.isArray(persistedState.topicKeywords)
  const hasPersistedTopicDefaults = typeof persistedState.defaultTopicName === 'string' || typeof persistedState.defaultTopicQuery === 'string'

  const [track, setTrack] = useState(persistedState.track || 'auto')
  const [dateRangeDays, setDateRangeDays] = useState(sanitizeDateRangeValue(persistedState.dateRangeDays || '7'))
  const [classicMinCitations, setClassicMinCitations] = useState(persistedState.classicMinCitations || '50')
  const [venues, setVenues] = useState<string[]>(
    Array.isArray(persistedState.venues) ? sanitizeVenues(persistedState.venues) : [],
  )
  const [structureMode, setStructureMode] = useState(persistedState.structureMode || 'classic')
  const [topicName, setTopicName] = useState(persistedState.topicName || '')
  const [topicQuery, setTopicQuery] = useState(persistedState.topicQuery || '')
  const [defaultTopicName, setDefaultTopicName] = useState(persistedState.defaultTopicName || '')
  const [defaultTopicQuery, setDefaultTopicQuery] = useState(persistedState.defaultTopicQuery || '')
  const [topicKeywords, setTopicKeywords] = useState<string[]>(persistedState.topicKeywords || [])
  const [topicFitGateThreshold, setTopicFitGateThreshold] = useState(
    persistedState.topicFitGateThreshold || '0.72',
  )
  const [postDownloadTopicFitThreshold, setPostDownloadTopicFitThreshold] = useState(
    persistedState.postDownloadTopicFitThreshold || '0.55',
  )
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [profileId, setProfileId] = useState<number | undefined>(persistedState.profileId)
  const [profileMode, setProfileMode] = useState<'auto' | 'explicit'>(
    persistedState.profileMode === 'explicit' && typeof persistedState.profileId === 'number'
      ? 'explicit'
      : 'auto',
  )
  const [mode, setMode] = useState<'auto' | 'manual'>(persistedState.mode || 'auto')
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [selectedManualFileMeta, setSelectedManualFileMeta] = useState<ManualFileMeta | null>(persistedState.manualFileMeta || null)
  const [isDragActive, setIsDragActive] = useState(false)
  const [manualUploadError, setManualUploadError] = useState(persistedState.manualUploadError || '')
  const [dateRangeError, setDateRangeError] = useState('')
  const [venueError, setVenueError] = useState('')
  const [showVenueLockHint, setShowVenueLockHint] = useState(false)
  const [isLaunching, setIsLaunching] = useState(false)
  const [isForceStopping, setIsForceStopping] = useState(false)
  const [isGeneratingKeywords, setIsGeneratingKeywords] = useState(false)
  const [keywordGroups, setKeywordGroups] = useState<KeywordGroup[]>(persistedState.keywordGroups || [])
  const [keywordSuggestError, setKeywordSuggestError] = useState(persistedState.keywordSuggestError || '')
  const [draftSourceJobId, setDraftSourceJobId] = useState<string | null>(null)
  const [retrySourceJobId, setRetrySourceJobId] = useState<string | null>(null)
  const [trackedJobIds, setTrackedJobIds] = useState<string[]>(initialTrackedJobIds)
  const [trackedJobsById, setTrackedJobsById] = useState<Record<string, Job>>({})
  const [jobId, setJobId] = useState<string | null>(initialSelectedJobId)
  const [hasValidatedTrackedJobs, setHasValidatedTrackedJobs] = useState(false)
  const [runStateError, setRunStateError] = useState('')
  const [workingMemoryArtifact, setWorkingMemoryArtifact] = useState<WorkingMemoryArtifact | null>(null)
  const [wmExpanded, setWmExpanded] = useState(false)
  const { logs, job, isDone, isConnected } = useJobWebSocket(jobId)
  const manualFileInputRef = useRef<HTMLInputElement | null>(null)
  const appliedRetryTokenRef = useRef<string | null>(null)
  const trackedJobs = useMemo(
    () => trackedJobIds
      .map((trackedJobId) => trackedJobsById[trackedJobId])
      .filter((trackedJob): trackedJob is Job => Boolean(trackedJob)),
    [trackedJobIds, trackedJobsById],
  )
  const activeTrackedJobCount = trackedJobs.filter((trackedJob) => !isTerminalJobStatus(trackedJob.status)).length

  const refreshProfiles = () => getProfiles().then(setProfiles)

  useEffect(() => {
    if (!retryDraft) {
      getConfig().then((cfg: Record<string, unknown>) => {
        const normalized = normalizeRunConfigSource(mergeWithLocalDefaultRunConfig(cfg))
        if (!hasPersistedSelection) {
          setTrack(normalized.track)
          setDateRangeDays(sanitizeDateRangeValue(normalized.dateRangeDays))
          setClassicMinCitations(normalized.classicMinCitations)
          setVenues(sanitizeVenues(normalized.venues))
        }
        if (!hasPersistedReportConfig) {
          setStructureMode(normalized.structureMode)
        }

        if (!hasPersistedTopic) {
          setTopicName(normalized.topicName)
          setTopicQuery(normalized.topicQuery)
          setTopicKeywords(normalized.topicKeywords)
        }
        if (!hasPersistedTopicDefaults) {
          setDefaultTopicName(normalized.topicName)
          setDefaultTopicQuery(normalized.topicQuery)
        }
      }).catch(console.error)
    }

    refreshProfiles().catch(console.error)
  }, [hasPersistedReportConfig, hasPersistedSelection, hasPersistedTopic, hasPersistedTopicDefaults, retryDraft])

  useEffect(() => {
    let cancelled = false

    setHasValidatedTrackedJobs(false)

    if (initialTrackedJobIds.length === 0) {
      setTrackedJobsById({})
      setHasValidatedTrackedJobs(true)
      return
    }

    Promise.all(
      initialTrackedJobIds.map(async (trackedJobId) => {
        try {
          return await getJob(trackedJobId)
        } catch {
          return null
        }
      }),
    )
      .then((resolvedJobs) => {
        if (cancelled) {
          return
        }
        const validJobs = resolvedJobs.filter((trackedJob): trackedJob is Job => Boolean(trackedJob))
        const validJobIds = validJobs.map((trackedJob) => trackedJob.id)
        setTrackedJobsById(
          Object.fromEntries(validJobs.map((trackedJob) => [trackedJob.id, trackedJob])),
        )
        setTrackedJobIds(validJobIds)
        setJobId((currentJobId) => {
          if (currentJobId && validJobIds.includes(currentJobId)) {
            return currentJobId
          }
          if (initialSelectedJobId && validJobIds.includes(initialSelectedJobId)) {
            return initialSelectedJobId
          }
          return validJobIds[0] || null
        })
      })
      .finally(() => {
        if (!cancelled) {
          setHasValidatedTrackedJobs(true)
        }
      })

    return () => {
      cancelled = true
    }
  }, [initialSelectedJobId, initialTrackedJobIds])

  useEffect(() => {
    if (initialTrackedJobIds.length > 0 || !retryDraft || !retryDraft.token || appliedRetryTokenRef.current === retryDraft.token) {
      return
    }

    appliedRetryTokenRef.current = retryDraft.token
    setMode('auto')
    setTrack(retryDraft.track || 'auto')
    setDateRangeDays(sanitizeDateRangeValue(retryDraft.dateRangeDays || '7'))
    setClassicMinCitations(retryDraft.classicMinCitations || '50')
    setVenues(sanitizeVenues(retryDraft.venues || []))
    setStructureMode(retryDraft.structureMode || 'classic')
    setTopicName(retryDraft.topicName || '')
    setTopicQuery(retryDraft.topicQuery || '')
    setDefaultTopicName(retryDraft.topicName || '')
    setDefaultTopicQuery(retryDraft.topicQuery || '')
    setTopicKeywords(retryDraft.topicKeywords || [])
    setTopicFitGateThreshold(retryDraft.topicFitGateThreshold || '0.72')
    setPostDownloadTopicFitThreshold(retryDraft.postDownloadTopicFitThreshold || '0.55')
    setProfileId(typeof retryDraft.profileId === 'number' ? retryDraft.profileId : undefined)
    setProfileMode(
      retryDraft.profileMode === 'explicit' && typeof retryDraft.profileId === 'number'
        ? 'explicit'
        : 'auto',
    )
    setKeywordGroups([])
    setKeywordSuggestError('')
    setManualUploadError('')
    setSelectedFile(null)
    setSelectedManualFileMeta(null)
    setJobId(null)
    setRunStateError('')
    setDraftSourceJobId(retryDraft.jobId)
    setRetrySourceJobId(
      typeof retryDraft.replaceJobId === 'string' && retryDraft.replaceJobId.trim()
        ? retryDraft.replaceJobId.trim()
        : null,
    )
  }, [initialTrackedJobIds.length, retryDraft])

  useEffect(() => {
    if (!job) {
      return
    }
    setTrackedJobsById((current) => ({
      ...current,
      [job.id]: job,
    }))
  }, [job])

  useEffect(() => {
    if (!hasValidatedTrackedJobs || trackedJobIds.length === 0 || activeTrackedJobCount === 0) {
      return
    }

    let cancelled = false
    let timerId: number | null = null

    const pollTrackedJobs = async () => {
      const resolvedJobs = await Promise.all(
        trackedJobIds.map(async (trackedJobId) => {
          try {
            return await getJob(trackedJobId)
          } catch {
            return null
          }
        }),
      )
      if (cancelled) {
        return
      }

      const availableJobs = resolvedJobs.filter((trackedJob): trackedJob is Job => Boolean(trackedJob))
      const availableJobIds = trackedJobIds.filter((trackedJobId) =>
        availableJobs.some((trackedJob) => trackedJob.id === trackedJobId),
      )

      setTrackedJobsById((current) => {
        const next = { ...current }
        for (const trackedJob of availableJobs) {
          next[trackedJob.id] = trackedJob
        }
        for (const trackedJobId of trackedJobIds) {
          if (!availableJobIds.includes(trackedJobId)) {
            delete next[trackedJobId]
          }
        }
        return next
      })
      if (availableJobIds.length !== trackedJobIds.length) {
        setTrackedJobIds(availableJobIds)
        setJobId((currentJobId) => (
          currentJobId && availableJobIds.includes(currentJobId)
            ? currentJobId
            : (availableJobIds[0] || null)
        ))
      }
    }

    const scheduleNextPoll = () => {
      if (cancelled) {
        return
      }
      timerId = window.setTimeout(async () => {
        timerId = null
        await pollTrackedJobs()
        scheduleNextPoll()
      }, TRACKED_JOB_POLL_INTERVAL_MS)
    }

    void pollTrackedJobs().then(() => {
      if (!cancelled) {
        scheduleNextPoll()
      }
    })

    return () => {
      cancelled = true
      if (timerId !== null) {
        window.clearTimeout(timerId)
      }
    }
  }, [activeTrackedJobCount, hasValidatedTrackedJobs, trackedJobIds])

  const handleDateRangeChange = (value: string) => {
    setDateRangeDays(value)
    setDateRangeError(validateDateRangeValue(value))
  }

  const handleDateRangeBlur = () => {
    const nextValue = sanitizeDateRangeValue(dateRangeDays)
    setDateRangeDays(nextValue)
    setDateRangeError('')
  }

  const toggleVenue = (v: string) => {
    setVenues((prev) => {
      if (!prev.includes(v)) {
        setVenueError('')
        setShowVenueLockHint(false)
        return [...prev, v]
      }

      if (prev.length === 1) {
        setVenueError(VENUE_ERROR_MESSAGE)
        setShowVenueLockHint(true)
        return prev
      }

      setVenueError('')
      setShowVenueLockHint(false)
      return prev.filter(x => x !== v)
    })
  }

  const selectedProfile = useMemo(
    () => profiles.find((profile) => profile.id === profileId),
    [profileId, profiles],
  )
  const effectiveProfileMode = profileMode === 'explicit' && typeof profileId === 'number'
    ? 'explicit'
    : 'auto'

  const selectedKeywordKeys = useMemo(
    () => new Set(topicKeywords.map((keyword) => normalizeKeywordToken(keyword).toLocaleLowerCase())),
    [topicKeywords],
  )

  const hasAnyCandidates = keywordGroups.some((group) => group.keywords.length > 0)

  const effectiveTopicQuery = useMemo(
    () => resolveTopicQuery(topicName, topicQuery, defaultTopicName, defaultTopicQuery),
    [defaultTopicName, defaultTopicQuery, topicName, topicQuery],
  )

  const isSelectedJobRunning = Boolean(job && !isDone && !isTerminalJobStatus(job.status))
  const displayedManualFile = selectedFile
    ? { name: selectedFile.name, size: selectedFile.size }
    : selectedManualFileMeta

  useEffect(() => {
    if (mode !== 'auto') {
      if (dateRangeError) {
        setDateRangeError('')
      }
      if (venueError) {
        setVenueError('')
      }
      if (showVenueLockHint) {
        setShowVenueLockHint(false)
      }
      return
    }

    const nextDateRangeError = validateDateRangeValue(dateRangeDays)
    if (nextDateRangeError !== dateRangeError) {
      setDateRangeError(nextDateRangeError)
    }

    if (venues.length === 0) {
      if (venueError !== VENUE_ERROR_MESSAGE) {
        setVenueError(VENUE_ERROR_MESSAGE)
      }
      return
    }

    if (venueError) {
      setVenueError('')
    }
  }, [dateRangeDays, dateRangeError, mode, showVenueLockHint, venueError, venues])

  useEffect(() => {
    const payload: PersistedRunPageState = {
      track,
      dateRangeDays,
      classicMinCitations,
      venues,
      structureMode,
      topicName,
      topicQuery,
      defaultTopicName,
      defaultTopicQuery,
      topicKeywords,
      topicFitGateThreshold,
      postDownloadTopicFitThreshold,
      profileId,
      profileMode: effectiveProfileMode,
      mode,
      manualUploadError,
      keywordGroups,
      keywordSuggestError,
      trackedJobIds,
      selectedJobId: jobId,
      manualFileMeta: selectedManualFileMeta,
    }
    persistRunPageState(payload)
  }, [
    defaultTopicName,
    defaultTopicQuery,
    dateRangeDays,
    jobId,
    keywordGroups,
    keywordSuggestError,
    classicMinCitations,
    manualUploadError,
    mode,
    profileId,
    effectiveProfileMode,
    selectedManualFileMeta,
    structureMode,
    topicKeywords,
    topicFitGateThreshold,
    postDownloadTopicFitThreshold,
    topicName,
    topicQuery,
    track,
    trackedJobIds,
    venues,
  ])

  const setManualFile = (file: File | null) => {
    if (!file) {
      setSelectedFile(null)
      setSelectedManualFileMeta(null)
      setManualUploadError('')
      return false
    }

    if (!isPdfFile(file)) {
      setSelectedFile(null)
      setSelectedManualFileMeta(null)
      setManualUploadError('Please choose a PDF file.')
      return false
    }

    if (file.size <= 0) {
      setSelectedFile(null)
      setSelectedManualFileMeta(null)
      setManualUploadError('The selected PDF is empty.')
      return false
    }

    if (file.size > MAX_MANUAL_FILE_SIZE_BYTES) {
      setSelectedFile(null)
      setSelectedManualFileMeta(null)
      setManualUploadError(`PDF must be smaller than ${formatFileSize(MAX_MANUAL_FILE_SIZE_BYTES)}.`)
      return false
    }

    setSelectedFile(file)
    setSelectedManualFileMeta({ name: file.name, size: file.size })
    setManualUploadError('')
    return true
  }

  const openManualFilePicker = () => {
    if (isLaunching) {
      return
    }
    manualFileInputRef.current?.click()
  }

  const handleManualFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const nextFile = event.target.files?.[0] ?? null
    setManualFile(nextFile)
    event.target.value = ''
  }

  const handleManualDrop = (event: React.DragEvent<HTMLButtonElement>) => {
    event.preventDefault()
    if (isLaunching) {
      return
    }
    setIsDragActive(false)
    const nextFile = event.dataTransfer.files?.[0] ?? null
    setManualFile(nextFile)
  }

  const handleManualDragOver = (event: React.DragEvent<HTMLButtonElement>) => {
    event.preventDefault()
    if (!isLaunching) {
      setIsDragActive(true)
    }
  }

  const handleManualDragLeave = (event: React.DragEvent<HTMLButtonElement>) => {
    event.preventDefault()
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) {
      return
    }
    setIsDragActive(false)
  }

  const clearManualFile = () => {
    if (isLaunching) {
      return
    }
    setSelectedFile(null)
    setSelectedManualFileMeta(null)
    setManualUploadError('')
    if (manualFileInputRef.current) {
      manualFileInputRef.current.value = ''
    }
  }

  const jobHasWorkingMemory = Boolean(job?.has_working_memory)

  useEffect(() => {
    if (!jobId || !hasValidatedTrackedJobs) {
      setWorkingMemoryArtifact(null)
      return
    }

    // Do not poll until the backend confirms the artifact file exists.
    if (!jobHasWorkingMemory) {
      setWorkingMemoryArtifact(null)
      return
    }

    let cancelled = false
    let timerId: number | null = null

    const poll = async () => {
      try {
        const payload = await getJobWorkingMemoryArtifact(jobId)
        if (!cancelled) {
          setWorkingMemoryArtifact(payload)
        }
      } catch (error) {
        if (cancelled || isMissingArtifactError(error)) {
          return
        }
        console.error(error)
      }
    }

    const scheduleNext = () => {
      if (cancelled || isDone) {
        return
      }
      timerId = window.setTimeout(async () => {
        timerId = null
        await poll()
        scheduleNext()
      }, WORKING_MEMORY_POLL_INTERVAL_MS)
    }

    void poll().then(() => {
      if (!cancelled) {
        scheduleNext()
      }
    })

    return () => {
      cancelled = true
      if (timerId !== null) {
        window.clearTimeout(timerId)
      }
    }
  }, [hasValidatedTrackedJobs, isDone, jobHasWorkingMemory, jobId])

  const workingMemoryView = useMemo(() => {
    const observations = workingMemoryArtifact?.observations ?? []
    const openQuestions = (workingMemoryArtifact?.open_questions ?? []).filter((item) => item.status === 'open')
    const draftClaims = workingMemoryArtifact?.draft_claims ?? []
    const promotionCandidates = workingMemoryArtifact?.promotion_candidates ?? []
    const metrics = workingMemoryArtifact?.metrics ?? {}

    return {
      stageLabel: formatStageLabel(workingMemoryArtifact?.artifact_stage),
      observationCount: observations.length,
      openQuestionCount: openQuestions.length,
      draftClaimCount: draftClaims.length,
      promotionCount: promotionCandidates.length,
      retrievedClaimCount: typeof metrics.retrieved_claim_count === 'number' ? metrics.retrieved_claim_count : 0,
      retrievedEvidenceCount: typeof metrics.retrieved_evidence_count === 'number' ? metrics.retrieved_evidence_count : 0,
      promptChars: typeof metrics.memory_extraction_prompt_chars === 'number' ? metrics.memory_extraction_prompt_chars : 0,
      recentObservations: observations.slice(-4).reverse(),
      openQuestions: openQuestions.slice(0, 3),
      draftClaims: draftClaims.slice(0, 3),
      promotionCandidates: promotionCandidates.slice(0, 4),
      terminologyEntries: Object.entries(workingMemoryArtifact?.terminology_map ?? {}).slice(0, 4),
    }
  }, [workingMemoryArtifact])

  const trackJobResult = (nextJob: Job) => {
    setTrackedJobsById((current) => ({
      ...current,
      [nextJob.id]: nextJob,
    }))
    setTrackedJobIds((current) => dedupeTrackedJobIds([nextJob.id, ...current]).slice(0, MAX_TRACKED_JOBS))
    setJobId(nextJob.id)
  }

  const handleRemoveTrackedJob = (trackedJobId: string) => {
    const nextTrackedJobIds = trackedJobIds.filter((existingJobId) => existingJobId !== trackedJobId)
    setTrackedJobIds(nextTrackedJobIds)
    setTrackedJobsById((current) => {
      const next = { ...current }
      delete next[trackedJobId]
      return next
    })
    setJobId((currentJobId) => (currentJobId === trackedJobId ? (nextTrackedJobIds[0] || null) : currentJobId))
    if (jobId === trackedJobId) {
      setWorkingMemoryArtifact(null)
    }
  }

  const handleRun = async () => {
    if (!hasValidatedTrackedJobs || isLaunching) {
      return
    }

    if (mode === 'manual' && !selectedFile) {
      setManualUploadError('Please choose a PDF before starting the pipeline.')
      return
    }

    let autoSelectionVenues = venues

    if (mode === 'auto') {
      const normalizedDateRange = sanitizeDateRangeValue(dateRangeDays)
      const nextDateRangeError = validateDateRangeValue(normalizedDateRange)
      const nextVenues = sanitizeVenues(venues)
      const nextVenueError = nextVenues.length > 0 ? '' : VENUE_ERROR_MESSAGE

      autoSelectionVenues = nextVenues
      setDateRangeDays(normalizedDateRange)
      setDateRangeError(nextDateRangeError)
      setVenues(nextVenues)
      setVenueError(nextVenueError)
      setShowVenueLockHint(false)

      if (nextDateRangeError || nextVenueError) {
        setRunStateError('Please fix the highlighted search filters before starting the pipeline.')
        return
      }
    }

    setRunStateError('')
    setIsLaunching(true)
    try {
      const result = mode === 'manual'
        ? await createManualJob({
            file: selectedFile as File,
            profile_id: effectiveProfileMode === 'explicit' ? profileId : undefined,
            profile_mode: effectiveProfileMode,
            replace_job_id: retrySourceJobId || undefined,
            config_override: {
              selection: {
                topic_fit_gate_threshold: Number.parseFloat(topicFitGateThreshold) || 0.72,
                post_download_topic_fit_threshold: Number.parseFloat(postDownloadTopicFitThreshold) || 0.55,
              },
              report: {
                structure_mode: structureMode,
              },
            },
          })
        : await createJob({
            profile_id: effectiveProfileMode === 'explicit' ? profileId : undefined,
            profile_mode: effectiveProfileMode,
            replace_job_id: retrySourceJobId || undefined,
            config_override: {
              topics: [
                {
                  name: topicName.trim(),
                  query: effectiveTopicQuery,
                  keywords: topicKeywords,
                },
              ],
              selection: {
                track,
                date_range_days: normalizePositiveInteger(dateRangeDays, MIN_DATE_RANGE_DAYS, MIN_DATE_RANGE_DAYS),
                classic_min_citations: normalizePositiveInteger(classicMinCitations, 50, 0),
                preferred_venues: autoSelectionVenues,
                topic_fit_gate_threshold: Number.parseFloat(topicFitGateThreshold) || 0.72,
                post_download_topic_fit_threshold: Number.parseFloat(postDownloadTopicFitThreshold) || 0.55,
              },
              report: {
                structure_mode: structureMode,
              },
            },
          })
      trackJobResult(result)
      setRunStateError('')
      setRetrySourceJobId(null)
    } catch (e) {
      console.error(e)
      if (mode === 'manual') {
        setManualUploadError(formatRequestError(e, 'Failed to start the pipeline. Please try again.'))
      } else {
        setRunStateError(formatRequestError(e, 'Failed to start the pipeline. Please try again.'))
      }
    } finally {
      setIsLaunching(false)
    }
  }

  const handleCancel = async () => {
    if (!jobId) {
      return
    }

    try {
      const result = await cancelJob(jobId)
      if (result.cancelled) {
        setRunStateError('')
        return
      }

      const latestJob = await getJob(jobId)
      if (latestJob.status === 'completed' || latestJob.status === 'failed') {
        setTrackedJobsById((current) => ({
          ...current,
          [latestJob.id]: latestJob,
        }))
        setRunStateError('')
        return
      }

      setRunStateError('Unable to cancel the selected run because the server still reports it as active.')
    } catch (error) {
      if (error instanceof Error && error.message.startsWith('404:')) {
        handleRemoveTrackedJob(jobId)
        setRunStateError('The selected run no longer exists on the server, so it was removed from the tracked list.')
        return
      }
      setRunStateError('Failed to cancel the selected run. Please try again.')
    }
  }

  const handleForceStop = async () => {
    if (!jobId) {
      return
    }

    const confirmed = window.confirm(
      'Force stop this run and purge all artifacts generated for the current job?\n\nThis deletes the current job record, report outputs, fetched PDF, selector cache, and any memory written back by this job.',
    )
    if (!confirmed) {
      return
    }

    setIsForceStopping(true)
    try {
      const result = await forceStopJob(jobId)
      handleRemoveTrackedJob(jobId)
      setWorkingMemoryArtifact(null)
      setRunStateError(
        result.task_cancel_timed_out
          ? 'The server purged this job, but the running task did not acknowledge cancellation before the timeout. If more stale logs appear, refresh once.'
          : '',
      )
    } catch (error) {
      if (error instanceof Error && error.message.startsWith('404:')) {
        handleRemoveTrackedJob(jobId)
        setWorkingMemoryArtifact(null)
        setRunStateError('The selected run was already gone on the server, so it was removed from the tracked list.')
        return
      }
      setRunStateError('Failed to force stop and purge the selected run. Please try again.')
    } finally {
      setIsForceStopping(false)
    }
  }

  const handleGenerateKeywords = async () => {
    const normalizedTopicName = normalizeKeywordToken(topicName)
    const nextTopicQuery = effectiveTopicQuery
    if (!normalizedTopicName && !nextTopicQuery) {
      return
    }

    setIsGeneratingKeywords(true)
    setKeywordSuggestError('')
    try {
      const result = await generateTopicKeywords({
        name: normalizedTopicName,
        query: nextTopicQuery,
        existing_keywords: topicKeywords,
        max_per_group: 5,
      })
      setKeywordGroups(result.groups)
      const total = result.groups.reduce((sum, g) => sum + g.keywords.length, 0)
      if (total === 0) {
        setKeywordSuggestError('No new keyword candidates were generated. Try refining the topic name.')
      }
    } catch (error) {
      console.error(error)
      setKeywordGroups([])
      setKeywordSuggestError(formatRequestError(error))
    } finally {
      setIsGeneratingKeywords(false)
    }
  }

  const handleTopicNameChange = (value: string) => {
    setTopicName(value)
    setTopicQuery(isSameTopicName(value, defaultTopicName) ? defaultTopicQuery : '')
    setKeywordGroups([])
    setKeywordSuggestError('')
  }

  const handleKeywordSelect = (keyword: string) => {
    setTopicKeywords((current) => mergeKeywordTokens(current, [keyword]))
  }

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <h2 className="text-2xl font-bold">Run Pipeline</h2>

      {draftSourceJobId && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {retrySourceJobId
            ? (
              <>
                Loaded settings from failed job <span className="font-mono">{draftSourceJobId}</span>. Adjust the filters below, then start the pipeline again.
              </>
            )
            : (
              <>
                Loaded settings from failed job <span className="font-mono">{draftSourceJobId}</span>. Start the pipeline to create a fresh new job without reusing the old fetched file.
              </>
            )}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">Mode</CardTitle>
            </CardHeader>
            <CardContent className="flex gap-2">
              <Button variant={mode === 'auto' ? 'default' : 'outline'} size="sm" onClick={() => setMode('auto')}>Auto Search</Button>
              <Button variant={mode === 'manual' ? 'default' : 'outline'} size="sm" onClick={() => setMode('manual')}>Manual PDF</Button>
            </CardContent>
          </Card>

          {mode === 'manual' ? (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm">Upload PDF</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <input
                  ref={manualFileInputRef}
                  type="file"
                  accept="application/pdf,.pdf"
                  className="sr-only"
                  onChange={handleManualFileChange}
                />
                <button
                  type="button"
                  onClick={openManualFilePicker}
                  onDrop={handleManualDrop}
                  onDragOver={handleManualDragOver}
                  onDragEnter={handleManualDragOver}
                  onDragLeave={handleManualDragLeave}
                  disabled={isLaunching}
                  className={cn(
                    'flex w-full flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-6 py-10 text-center transition-colors',
                    isDragActive
                      ? 'border-primary bg-primary/5'
                      : 'border-border/70 bg-muted/15 hover:border-primary/60 hover:bg-muted/30',
                    isLaunching && 'cursor-not-allowed opacity-60',
                  )}
                >
                  <div className="rounded-full border border-border/60 bg-background p-3 text-muted-foreground">
                    <Upload className="h-5 w-5" />
                  </div>
                  <div className="space-y-1">
                    <p className="text-sm font-medium text-foreground">
                      {displayedManualFile ? 'PDF ready to upload with this run' : 'Click to choose a PDF or drag it here'}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      Your file will be copied into this job&apos;s folder when you start the pipeline.
                    </p>
                  </div>
                  {displayedManualFile && (
                    <div className="w-full max-w-md rounded-lg border bg-background px-3 py-2 text-left">
                      <p className="truncate text-sm font-medium text-foreground">{displayedManualFile.name}</p>
                      <p className="text-xs text-muted-foreground">{formatFileSize(displayedManualFile.size)}</p>
                    </div>
                  )}
                </button>
                <div className="flex flex-wrap items-center gap-2">
                  <Button type="button" variant="outline" size="sm" onClick={openManualFilePicker} disabled={isLaunching}>
                    <Upload className="mr-1 h-3.5 w-3.5" />
                    {displayedManualFile ? 'Replace PDF' : 'Choose PDF'}
                  </Button>
                  {displayedManualFile && (
                    <Button type="button" variant="ghost" size="sm" onClick={clearManualFile} disabled={isLaunching}>
                      <X className="mr-1 h-3.5 w-3.5" />
                      Clear
                    </Button>
                  )}
                  <span className="text-xs text-muted-foreground">PDF only, up to {formatFileSize(MAX_MANUAL_FILE_SIZE_BYTES)}.</span>
                </div>
                {manualUploadError && <p className="text-sm text-destructive">{manualUploadError}</p>}
              </CardContent>
            </Card>
          ) : (
            <>
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-sm">
                    <span>Topic</span>
                    <InfoHint
                      label="Topic help"
                      content={
                        <div className="space-y-1.5">
                          <p>These edits apply only to this run and will not overwrite your saved global config.</p>
                          <p>Use semicolons to split keywords into small pills automatically.</p>
                          <p>If you leave keywords empty, the backend will auto-generate English search hints before running the selector.</p>
                        </div>
                      }
                    />
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                    <Input placeholder="Topic name" value={topicName} onChange={(e: React.ChangeEvent<HTMLInputElement>) => handleTopicNameChange(e.target.value)} />
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="sm:self-stretch"
                      disabled={mode !== 'auto' || !topicName.trim() || isGeneratingKeywords || isLaunching}
                      onClick={handleGenerateKeywords}
                    >
                      <Sparkles className="mr-1 h-3.5 w-3.5" />
                      {isGeneratingKeywords ? 'Loading...' : 'Suggest'}
                    </Button>
                  </div>
                  <KeywordTokenInput value={topicKeywords} onChange={setTopicKeywords} placeholder="Type a keyword and end with ;" />
                  {(hasAnyCandidates || keywordSuggestError) && (
                    <div className="space-y-2.5 rounded-lg border bg-muted/20 px-3 py-2.5">
                      <p className="text-[11px] text-muted-foreground">Click to add keywords to the current Topic.</p>
                      {keywordGroups.map((group) => {
                        if (group.keywords.length === 0) return null
                        const groupLabel = GROUP_LABELS[group.label] || group.label
                        return (
                          <div key={group.label} className="space-y-1">
                            <p className="text-[11px] font-semibold text-foreground/80">{groupLabel}</p>
                            <div className="flex flex-wrap gap-1.5">
                              {group.keywords.map((candidate) => {
                                const candidateKey = normalizeKeywordToken(candidate).toLocaleLowerCase()
                                const isSelected = selectedKeywordKeys.has(candidateKey)
                                return (
                                  <button
                                    key={candidate}
                                    type="button"
                                    disabled={isSelected}
                                    onClick={() => handleKeywordSelect(candidate)}
                                    className={
                                      'inline-flex items-center rounded-md border px-2 py-0.5 text-[11px] transition-colors '
                                      + (isSelected
                                        ? 'border-transparent bg-muted text-muted-foreground'
                                        : 'border-border bg-background text-foreground hover:bg-muted hover:text-foreground')
                                    }
                                  >
                                    {candidate}
                                  </button>
                                )
                              })}
                            </div>
                          </div>
                        )
                      })}
                      {keywordSuggestError && <p className="text-[11px] text-destructive">{keywordSuggestError}</p>}
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-sm">
                    <span>Track</span>
                    <InfoHint
                      label="Track help"
                      content={
                        <div className="space-y-1.5">
                          {TRACKS.map((trackOption) => (
                            <p key={trackOption.value}>
                              <span className="font-medium text-foreground">{trackOption.label}:</span> {trackOption.desc}
                            </p>
                          ))}
                        </div>
                      }
                    />
                  </CardTitle>
                </CardHeader>
                <CardContent className="flex flex-wrap gap-2">
                  {TRACKS.map(t => (
                    <Button
                      key={t.value}
                      variant={track === t.value ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => setTrack(t.value)}
                      title={t.desc}
                    >
                      {t.label}
                    </Button>
                  ))}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-sm">
                    <span>Search Filters</span>
                    <InfoHint
                      label="Search Filters help"
                      content={
                        <div className="space-y-1.5">
                          <p>`Date Range` controls how many recent days count toward the `recent` part of track filtering.</p>
                          <p>`Classic Min Citations` sets the citation threshold for `classic` matching. `GOAT` requires both filters to pass.</p>
                        </div>
                      }
                    />
                  </CardTitle>
                </CardHeader>
                <CardContent className="grid gap-3 sm:grid-cols-2">
                  <label className="space-y-1.5">
                    <span className="text-xs font-medium text-muted-foreground">Date Range (days)</span>
                    <Input
                      type="number"
                      min={MIN_DATE_RANGE_DAYS}
                      step={1}
                      inputMode="numeric"
                      value={dateRangeDays}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => handleDateRangeChange(e.target.value)}
                      onBlur={handleDateRangeBlur}
                      aria-invalid={Boolean(dateRangeError)}
                    />
                    {dateRangeError && <p className="text-xs text-destructive">{dateRangeError}</p>}
                  </label>
                  <label className="space-y-1.5">
                    <span className="text-xs font-medium text-muted-foreground">Classic Min Citations</span>
                    <Input
                      type="number"
                      min={0}
                      step={1}
                      inputMode="numeric"
                      value={classicMinCitations}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => setClassicMinCitations(e.target.value)}
                    />
                  </label>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center justify-between gap-3 text-sm">
                    <span>Preferred Venues</span>
                    {venues.length === 1 && (
                      <span className="inline-flex items-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
                        <Lock className="h-3 w-3" />
                        Keep one
                      </span>
                    )}
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2.5">
                  <div className="flex flex-wrap gap-2">
                    {ALL_VENUES.map(v => {
                      const isSelected = venues.includes(v)
                      const isLocked = isSelected && venues.length === 1
                      return (
                        <button
                          key={v}
                          type="button"
                          onClick={() => toggleVenue(v)}
                          aria-pressed={isSelected}
                          aria-disabled={isLocked}
                          title={isLocked ? VENUE_ERROR_MESSAGE : undefined}
                          className={cn(
                            'inline-flex rounded-full transition-transform',
                            isLocked ? 'cursor-not-allowed' : 'cursor-pointer hover:-translate-y-0.5',
                          )}
                        >
                          <Badge
                            variant={isSelected ? 'default' : 'outline'}
                            className={cn(
                              'pointer-events-none select-none transition-all',
                              isLocked && 'border-amber-300 bg-amber-50 text-amber-900 shadow-none dark:border-amber-500/50 dark:bg-amber-500/10 dark:text-amber-200',
                            )}
                          >
                            {v}
                          </Badge>
                        </button>
                      )
                    })}
                  </div>
                  {showVenueLockHint && (
                    <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
                      The last preferred venue stays selected so the search scope never becomes empty.
                    </div>
                  )}
                  {venueError && !showVenueLockHint && <p className="text-xs text-destructive">{venueError}</p>}
                </CardContent>
              </Card>
            </>
          )}

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-sm">
                <span>Report Structure</span>
                <InfoHint
                  label="Report structure help"
                  content={
                    <div className="space-y-1.5">
                      <p>`Current Structure` keeps the project&apos;s existing reading-oriented layout.</p>
                      <p>`PMRC Narrative` borrows Auto-Slides&apos; presentation-style idea and reorganizes the final report toward problem/motivation -&gt; method -&gt; results -&gt; conclusion.</p>
                    </div>
                  }
                />
              </CardTitle>
            </CardHeader>
            <CardContent className="grid gap-2">
              {REPORT_STRUCTURE_MODES.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setStructureMode(option.value)}
                  className={cn(
                    'rounded-xl border px-4 py-3 text-left transition-colors',
                    structureMode === option.value
                      ? 'border-primary bg-primary/5'
                      : 'border-border bg-background hover:border-primary/40 hover:bg-muted/20',
                  )}
                >
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-medium text-foreground">{option.label}</p>
                    {structureMode === option.value ? <Badge variant="secondary">Active</Badge> : null}
                  </div>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">{option.desc}</p>
                </button>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-sm">
                <span>Memory Profile</span>
                <InfoHint
                  label="Memory Profile help"
                  content={
                    <div className="space-y-1.5">
                      <p>Profiles carry long-term research memory: high-level cognition, evidence-backed claims, conflict defaults, and source bundles.</p>
                      <p>Pick one when you want repeated runs in the same direction to share accumulated context; otherwise this run will auto-match a profile after reading the paper.</p>
                    </div>
                  }
                />
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap gap-2">
                <Badge
                  variant={effectiveProfileMode === 'auto' ? 'default' : 'outline'}
                  className="cursor-pointer select-none"
                  onClick={() => {
                    setProfileMode('auto')
                    setProfileId(undefined)
                  }}
                >
                  Auto assign
                </Badge>
                {profiles.map(p => (
                  <Badge
                    key={p.id}
                    variant={effectiveProfileMode === 'explicit' && profileId === p.id ? 'default' : 'outline'}
                    className="cursor-pointer select-none"
                    onClick={() => {
                      setProfileId(p.id)
                      setProfileMode('explicit')
                    }}
                  >
                    {p.name} ({p.paper_count})
                  </Badge>
                ))}
              </div>

              <div className="rounded-lg border bg-muted/30 p-3 text-sm">
                {effectiveProfileMode === 'explicit' && selectedProfile ? (
                  <div className="space-y-2">
                    <div className="flex items-center justify-between gap-3">
                      <p className="font-medium text-foreground">{selectedProfile.name}</p>
                      <Badge variant="secondary">{selectedProfile.paper_count} papers</Badge>
                    </div>
                    <p className="text-muted-foreground">{selectedProfile.description || EMPTY_PROFILE_DESCRIPTION}</p>
                    <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                      <span>共享高层认知与 Claims</span>
                      <span>•</span>
                      <span>Last used: {new Date(selectedProfile.last_used_at * 1000).toLocaleDateString()}</span>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2">
                    <p className="font-medium text-foreground">Auto assign</p>
                    <p className="text-muted-foreground">{DEFAULT_PROFILE_NOTE}</p>
                    <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                      <span>先尝试匹配已有 profile</span>
                      <span>•</span>
                      <span>匹配不到时自动新建 profile</span>
                    </div>
                  </div>
                )}
              </div>

              <div className="flex flex-wrap items-center gap-3">
                <CreateProfileDialog
                  trigger={
                    <Button variant="outline" size="sm">
                      <Plus className="mr-1 h-3.5 w-3.5" />
                      Create New
                    </Button>
                  }
                  title="Create Memory Profile"
                  description="Add a new profile for a specific domain, task preference, or writing style. It will be available immediately on this Run page."
                  onCreated={async (profile) => {
                    await refreshProfiles()
                    setProfileId(profile.id)
                    setProfileMode('explicit')
                  }}
                />
                <Link to="/profiles" className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
                  Go to Profiles to manage all profiles
                </Link>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-sm">
                <span>Advanced Similarity Controls</span>
                <InfoHint
                  label="Similarity thresholds help"
                  content={
                    <div className="space-y-1.5">
                      <p>Topic Fit Gate Threshold (default 0.72) controls how strict the selector is when filtering candidates during auto search.</p>
                      <p>Post-download Audit Threshold (default 0.55) controls the secondary check after the paper is downloaded. Manual uploads with an explicit profile skip this check entirely.</p>
                      <p>Lower values accept more papers; higher values are stricter.</p>
                    </div>
                  }
                />
              </CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1.5">
                <span className="text-xs font-medium text-muted-foreground">Topic Fit Gate Threshold</span>
                <Input
                  type="number"
                  min={0}
                  max={1}
                  step={0.01}
                  inputMode="decimal"
                  value={topicFitGateThreshold}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setTopicFitGateThreshold(e.target.value)}
                />
              </label>
              <label className="space-y-1.5">
                <span className="text-xs font-medium text-muted-foreground">Post-download Audit Threshold</span>
                <Input
                  type="number"
                  min={0}
                  max={1}
                  step={0.01}
                  inputMode="decimal"
                  value={postDownloadTopicFitThreshold}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setPostDownloadTopicFitThreshold(e.target.value)}
                />
              </label>
            </CardContent>
          </Card>

          <div className="flex gap-2">
            <Button onClick={handleRun} disabled={isLaunching || !hasValidatedTrackedJobs} className="flex-1">
              <Play className="mr-2 h-4 w-4" />{isLaunching ? 'Submitting...' : 'Start Pipeline'}
            </Button>
            {isSelectedJobRunning && jobId && (
              <>
                <Button variant="outline" onClick={handleCancel} disabled={isForceStopping}>
                  <Square className="mr-1 h-4 w-4" />
                  Cancel
                </Button>
                <Button variant="destructive" onClick={handleForceStop} disabled={isForceStopping}>
                  <X className="mr-1 h-4 w-4" />
                  {isForceStopping ? 'Purging...' : 'Force Stop + Purge'}
                </Button>
              </>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            New submissions start in the background. Use the tracked run list on the right to switch between jobs while earlier ones continue.
          </p>
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center justify-between text-sm">
                <span>Tracked Runs</span>
                <div className="flex items-center gap-2">
                  <Badge variant="secondary">Total {trackedJobs.length}</Badge>
                  <Badge variant="outline">Active {activeTrackedJobCount}</Badge>
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {!hasValidatedTrackedJobs ? (
                <p className="text-sm text-muted-foreground">Restoring tracked runs...</p>
              ) : trackedJobs.length === 0 ? (
                <p className="text-sm text-muted-foreground">Start a pipeline to keep it pinned here for quick switching.</p>
              ) : (
                trackedJobs.map((trackedJob) => (
                  <div
                    key={trackedJob.id}
                    className={cn(
                      'rounded-xl border px-3 py-3 transition-colors',
                      trackedJob.id === jobId
                        ? 'border-primary bg-primary/5'
                        : 'border-border/70 bg-background',
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 space-y-2">
                        <div className="flex flex-wrap gap-1.5">
                          <Badge variant={getStatusVariant(trackedJob.status)}>{trackedJob.status}</Badge>
                          <Badge variant="outline">{trackedJob.mode === 'manual' ? 'Manual' : 'Auto'}</Badge>
                          <Badge variant="outline">{Math.round(trackedJob.progress || 0)}%</Badge>
                        </div>
                        <div className="space-y-1">
                          <p className="text-sm font-medium text-foreground">{clipText(getTrackedJobTitle(trackedJob), 120)}</p>
                          <p className="text-xs text-muted-foreground">{clipText(getTrackedJobSubtitle(trackedJob), 140)}</p>
                          <p className="text-xs text-muted-foreground">{trackedJob.current_step || 'Waiting to start'}</p>
                        </div>
                        <Progress value={trackedJob.progress || 0} />
                      </div>

                      <div className="flex shrink-0 flex-col gap-2">
                        <Button
                          variant={trackedJob.id === jobId ? 'secondary' : 'outline'}
                          size="sm"
                          onClick={() => setJobId(trackedJob.id)}
                        >
                          {trackedJob.id === jobId ? 'Selected' : 'Monitor'}
                        </Button>
                        {trackedJob.status === 'completed' && trackedJob.report_path && (
                          <Link to={`/reports/job/${encodeURIComponent(trackedJob.id)}`}>
                            <Button variant="outline" size="sm">
                              <FileText className="mr-1 h-4 w-4" />
                              Report
                            </Button>
                          </Link>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleRemoveTrackedJob(trackedJob.id)}
                        >
                          Hide
                        </Button>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center justify-between text-sm">
                <span>Selected Run</span>
                {job && <Badge variant={getStatusVariant(job.status)}>{job.status}</Badge>}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {!hasValidatedTrackedJobs && <p className="text-sm text-muted-foreground">Restoring tracked runs...</p>}
              {runStateError && <p className="text-sm text-destructive">{runStateError}</p>}
              {!jobId && hasValidatedTrackedJobs && (
                <p className="text-sm text-muted-foreground">Pick a tracked run to inspect its progress, logs, and working memory.</p>
              )}
              {jobId && !isDone && !isConnected && hasValidatedTrackedJobs && (
                <p className="text-sm text-muted-foreground">Selected run connection lost. Reconnecting or checking the latest server state...</p>
              )}
              <Progress value={job?.progress || 0} />
              {job?.current_step && <p className="text-sm text-muted-foreground">{job.current_step}</p>}
              {job?.paper_title && <p className="text-sm font-medium">{job.paper_title}</p>}
              {job?.error && <p className="text-sm text-destructive">{job.error}</p>}
              {job?.status === 'completed' && jobId && job.report_path && (
                <Link to={`/reports/job/${encodeURIComponent(jobId)}`}>
                  <Button variant="outline" size="sm" className="mt-2"><FileText className="mr-1 h-4 w-4" />View Report</Button>
                </Link>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader
              className="cursor-pointer select-none pb-3"
              onClick={() => setWmExpanded((v) => !v)}
            >
              <CardTitle className="flex items-center justify-between text-sm">
                <span className="flex items-center gap-2">
                  {wmExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                  <Brain className="h-4 w-4" />
                  Live Working Memory
                </span>
                <span className="flex items-center gap-2">
                  {workingMemoryArtifact ? <Badge variant="secondary">{workingMemoryView.stageLabel}</Badge> : <Badge variant="outline">Waiting</Badge>}
                  {!wmExpanded && workingMemoryArtifact && (
                    <>
                      <Badge variant="secondary">Obs {workingMemoryView.observationCount}</Badge>
                      <Badge variant="outline">Claims {workingMemoryView.draftClaimCount}</Badge>
                    </>
                  )}
                </span>
              </CardTitle>
            </CardHeader>
            {wmExpanded && (
              <CardContent className="space-y-3">
                {!jobId ? (
                  <p className="text-sm text-muted-foreground">Start a run to inspect the interpreter working memory.</p>
                ) : !workingMemoryArtifact ? (
                  <p className="text-sm text-muted-foreground">Working memory becomes visible once the interpreter has built its first stable checkpoint.</p>
                ) : (
                  <>
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="secondary">Observations {workingMemoryView.observationCount}</Badge>
                      <Badge variant="outline">Open questions {workingMemoryView.openQuestionCount}</Badge>
                      <Badge variant="outline">Draft claims {workingMemoryView.draftClaimCount}</Badge>
                      <Badge variant="outline">Promotions {workingMemoryView.promotionCount}</Badge>
                      {workingMemoryView.promptChars > 0 && <Badge variant="outline">Writeback prompt {workingMemoryView.promptChars}</Badge>}
                    </div>

                    <div className="grid gap-3 sm:grid-cols-2">
                      <div className="rounded-lg border bg-muted/20 p-3">
                        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">Recent observations</p>
                        <div className="mt-2 space-y-2">
                          {workingMemoryView.recentObservations.length === 0 ? (
                            <p className="text-sm text-muted-foreground">No observations yet.</p>
                          ) : workingMemoryView.recentObservations.map((item, index) => (
                            <div key={`${item.section_key || 'section'}-${index}`} className="space-y-1">
                              <div className="flex flex-wrap gap-1.5">
                                {item.section_key && <Badge variant="outline">{item.section_key}</Badge>}
                                {item.kind && <Badge variant="secondary">{item.kind}</Badge>}
                              </div>
                              <p className="text-sm text-foreground">{clipText(item.summary || '', 150)}</p>
                            </div>
                          ))}
                        </div>
                      </div>

                      <div className="rounded-lg border bg-muted/20 p-3">
                        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">Draft claims</p>
                        <div className="mt-2 space-y-2">
                          {workingMemoryView.draftClaims.length === 0 ? (
                            <p className="text-sm text-muted-foreground">No draft claims yet.</p>
                          ) : workingMemoryView.draftClaims.map((item, index) => (
                            <div key={`${item.section_key || 'claim'}-${index}`} className="space-y-1">
                              <div className="flex flex-wrap gap-1.5">
                                {item.section_key && <Badge variant="outline">{item.section_key}</Badge>}
                                {item.importance && <Badge variant="secondary">{item.importance}</Badge>}
                              </div>
                              <p className="text-sm text-foreground">{clipText(item.claim || '', 150)}</p>
                            </div>
                          ))}
                        </div>
                      </div>

                      <div className="rounded-lg border bg-muted/20 p-3">
                        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">Open questions</p>
                        <div className="mt-2 space-y-2">
                          {workingMemoryView.openQuestions.length === 0 ? (
                            <p className="text-sm text-muted-foreground">No open questions in the current checkpoint.</p>
                          ) : workingMemoryView.openQuestions.map((item, index) => (
                            <div key={`${item.section_key || 'question'}-${index}`} className="space-y-1">
                              {item.section_key && <Badge variant="outline">{item.section_key}</Badge>}
                              <p className="text-sm text-foreground">{clipText(item.question || '', 150)}</p>
                              {item.reason && <p className="text-xs text-muted-foreground">{clipText(item.reason, 150)}</p>}
                            </div>
                          ))}
                        </div>
                      </div>

                      <div className="rounded-lg border bg-muted/20 p-3">
                        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">Memory diagnostics</p>
                        <div className="mt-2 space-y-2 text-sm text-foreground">
                          <p>Retrieved claims: {workingMemoryView.retrievedClaimCount}</p>
                          <p>Retrieved evidence: {workingMemoryView.retrievedEvidenceCount}</p>
                          {workingMemoryView.terminologyEntries.length > 0 && (
                            <div className="space-y-1">
                              <p className="text-xs text-muted-foreground">Terminology</p>
                              <div className="flex flex-wrap gap-1.5">
                                {workingMemoryView.terminologyEntries.map(([term], index) => (
                                  <Badge key={`${term}-${index}`} variant="outline">{clipText(term, 32)}</Badge>
                                ))}
                              </div>
                            </div>
                          )}
                          {workingMemoryView.promotionCandidates.length > 0 && (
                            <div className="space-y-1">
                              <p className="text-xs text-muted-foreground">Promotion candidates</p>
                              <div className="flex flex-wrap gap-1.5">
                                {workingMemoryView.promotionCandidates.map((item, index) => (
                                  <Badge key={`${item.status || 'candidate'}-${index}`} variant={item.status === 'accepted' ? 'secondary' : 'outline'}>
                                    {item.status || 'candidate'}
                                  </Badge>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>

                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => window.open(getJobMemoryArtifactUrl(jobId, 'working-memory'), '_blank', 'noopener,noreferrer')}
                    >
                      <FileJson className="mr-1 h-4 w-4" />
                      Open Working Memory JSON
                    </Button>
                  </>
                )}
              </CardContent>
            )}
          </Card>

          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-sm">Logs</CardTitle></CardHeader>
            <CardContent>
              {!jobId ? (
                <p className="text-sm text-muted-foreground">Select a tracked run to inspect its live logs.</p>
              ) : (
                <LogStream logs={logs} />
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
