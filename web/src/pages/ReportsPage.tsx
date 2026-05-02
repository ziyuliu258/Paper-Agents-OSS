import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import InfoHint from '@/components/ui/info-hint'
import { deleteJob, getJobHistory, retryJob, type JobReportSummary } from '@/api/client'
import { JobHistoryCard } from '@/components/JobHistoryCard'
import { buildPersistedRunPageStateFromJob, normalizeRunConfigSource, persistRunPageState } from '@/lib/jobConfig'
import { readDiagnosticNumber, readPromotionCount } from '@/lib/reportDiagnostics'

const TOGGLE_COOLDOWN_MS = 2000
const HIGH_PROMPT_THRESHOLD = 14000
const DELETE_CONFIRM_SECONDS = 5
type DiagnosticFilter = 'all' | 'selector' | 'working-memory' | 'heavy-prompt' | 'review-required'
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

export default function ReportsPage() {
  const navigate = useNavigate()
  const [items, setItems] = useState<JobReportSummary[]>([])
  const [pageError, setPageError] = useState<string | null>(null)
  const [activeFilter, setActiveFilter] = useState<DiagnosticFilter>('all')
  const [expandedJobId, setExpandedJobId] = useState<string | null>(null)
  const [isToggleCoolingDown, setIsToggleCoolingDown] = useState(false)
  const [pendingDeleteItem, setPendingDeleteItem] = useState<JobReportSummary | null>(null)
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false)
  const [deleteCountdown, setDeleteCountdown] = useState(DELETE_CONFIRM_SECONDS)
  const [isDeleting, setIsDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const cooldownRef = useRef(0)
  const cooldownTimerRef = useRef<number | null>(null)

  useEffect(() => {
    getJobHistory(100)
      .then((response) => {
        setItems(response.reports)
        setPageError(null)
      })
      .catch((err) => {
        console.error(err)
        setPageError(err instanceof Error ? err.message : 'Failed to load reports history.')
      })

    return () => {
      if (cooldownTimerRef.current !== null) {
        window.clearTimeout(cooldownTimerRef.current)
      }
    }
  }, [])

  useEffect(() => {
    if (!isDeleteDialogOpen) {
      setDeleteCountdown(DELETE_CONFIRM_SECONDS)
      return
    }
    const timer = window.setInterval(() => {
      setDeleteCountdown((current) => {
        if (current <= 1) {
          window.clearInterval(timer)
          return 0
        }
        return current - 1
      })
    }, 1000)
    return () => window.clearInterval(timer)
  }, [isDeleteDialogOpen])

  const diagnosticsOverview = useMemo(() => {
    const selectorJobs = items.filter((item) => item.has_selector_diagnostics).length
    const workingMemoryJobs = items.filter((item) => item.has_working_memory).length
    const heavyPromptJobs = items.filter(
      (item) => readDiagnosticNumber(item, 'memory_extraction_prompt_chars') >= HIGH_PROMPT_THRESHOLD,
    ).length
    const reviewRequiredJobs = items.filter(
      (item) => readPromotionCount(item, 'review_required') > 0,
    ).length
    const totalAcceptedPromotions = items.reduce(
      (sum, item) => sum + readPromotionCount(item, 'accepted'),
      0,
    )
    const maxPromptChars = items.reduce(
      (max, item) => Math.max(max, readDiagnosticNumber(item, 'memory_extraction_prompt_chars')),
      0,
    )

    return {
      selectorJobs,
      workingMemoryJobs,
      heavyPromptJobs,
      reviewRequiredJobs,
      totalAcceptedPromotions,
      maxPromptChars,
    }
  }, [items])

  const filteredItems = useMemo(() => {
    switch (activeFilter) {
      case 'selector':
        return items.filter((item) => item.has_selector_diagnostics)
      case 'working-memory':
        return items.filter((item) => item.has_working_memory)
      case 'heavy-prompt':
        return items.filter(
          (item) => readDiagnosticNumber(item, 'memory_extraction_prompt_chars') >= HIGH_PROMPT_THRESHOLD,
        )
      case 'review-required':
        return items.filter((item) => readPromotionCount(item, 'review_required') > 0)
      case 'all':
      default:
        return items
    }
  }, [activeFilter, items])

  const visibleExpandedJobId = expandedJobId && filteredItems.some((item) => item.job_id === expandedJobId)
    ? expandedJobId
    : null

  const handleToggle = (jobId: string) => {
    const now = Date.now()
    if (now < cooldownRef.current) {
      return
    }

    cooldownRef.current = now + TOGGLE_COOLDOWN_MS
    setIsToggleCoolingDown(true)

    if (cooldownTimerRef.current !== null) {
      window.clearTimeout(cooldownTimerRef.current)
    }

    cooldownTimerRef.current = window.setTimeout(() => {
      cooldownRef.current = 0
      setIsToggleCoolingDown(false)
      cooldownTimerRef.current = null
    }, TOGGLE_COOLDOWN_MS)

    setExpandedJobId((current) => (current === jobId ? null : jobId))
  }

  const handleView = (jobId: string) => {
    navigate(`/reports/job/${encodeURIComponent(jobId)}`)
  }

  const handleRetry = async (item: JobReportSummary) => {
    try {
      const retriedJob = await retryJob(item.job_id)
      persistRunPageState(buildPersistedRunPageStateFromJob(retriedJob))
      navigate('/run')
    } catch (err) {
      console.error('Retry failed:', err)
      setPageError(err instanceof Error ? err.message : 'Retry failed.')
    }
  }

  const handleRunAgain = (item: JobReportSummary) => {
    const normalized = normalizeRunConfigSource(item.config_snapshot)
    persistRunPageState({
      track: normalized.track,
      dateRangeDays: normalized.dateRangeDays,
      classicMinCitations: normalized.classicMinCitations,
      venues: normalized.venues,
      structureMode: normalized.structureMode,
      topicName: normalized.topicName,
      topicQuery: normalized.topicQuery,
      defaultTopicName: normalized.topicName,
      defaultTopicQuery: normalized.topicQuery,
      topicKeywords: normalized.topicKeywords,
      topicFitGateThreshold: normalized.topicFitGateThreshold,
      postDownloadTopicFitThreshold: normalized.postDownloadTopicFitThreshold,
      profileId: typeof item.profile_id === 'number' ? item.profile_id : undefined,
      profileMode: item.profile_mode === 'explicit' ? 'explicit' : 'auto',
      mode: 'auto',
      manualUploadError: '',
      keywordGroups: [],
      keywordSuggestError: '',
      trackedJobIds: [],
      selectedJobId: null,
      manualFileMeta: null,
    })

    const retryDraft: RetryDraftState = {
      token: `${item.job_id}:${Date.now()}`,
      jobId: item.job_id,
      replaceJobId: null,
      profileId: item.profile_id,
      profileMode: item.profile_mode,
      mode: 'auto',
      structureMode: normalized.structureMode,
      topicName: normalized.topicName,
      topicQuery: normalized.topicQuery,
      topicKeywords: normalized.topicKeywords,
      topicFitGateThreshold: normalized.topicFitGateThreshold,
      postDownloadTopicFitThreshold: normalized.postDownloadTopicFitThreshold,
      track: normalized.track,
      dateRangeDays: normalized.dateRangeDays,
      classicMinCitations: normalized.classicMinCitations,
      venues: normalized.venues,
    }

    navigate('/run', { state: { retryDraft } })
  }
  const openDeleteDialog = (item: JobReportSummary) => {
    setPendingDeleteItem(item)
    setDeleteError(null)
    setDeleteCountdown(DELETE_CONFIRM_SECONDS)
    setIsDeleteDialogOpen(true)
  }

  const closeDeleteDialog = () => {
    if (isDeleting) {
      return
    }
    setIsDeleteDialogOpen(false)
    setPendingDeleteItem(null)
    setDeleteCountdown(DELETE_CONFIRM_SECONDS)
    setDeleteError(null)
  }

  const handleDeleteConfirm = async () => {
    if (!pendingDeleteItem || deleteCountdown > 0) {
      return
    }

    setIsDeleting(true)
    setDeleteError(null)
    try {
      const result = await deleteJob(pendingDeleteItem.job_id)
      if (!result.job_deleted) {
        throw new Error('Delete completed without removing the job record.')
      }
      setItems((current) => current.filter((item) => item.job_id !== pendingDeleteItem.job_id))
      setExpandedJobId((current) => (current === pendingDeleteItem.job_id ? null : current))
      setIsDeleteDialogOpen(false)
      setPendingDeleteItem(null)
      setDeleteCountdown(DELETE_CONFIRM_SECONDS)
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : 'Failed to delete this report job.')
    } finally {
      setIsDeleting(false)
    }
  }

  const pendingDeleteTitle = pendingDeleteItem?.title || pendingDeleteItem?.paper_title || pendingDeleteItem?.job_id || 'Untitled report'
  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div className="flex items-center gap-2">
        <h2 className="text-2xl font-bold">Reports</h2>
        <InfoHint
          label="Reports help"
          content={
            <div className="space-y-1.5">
              <p>Browse all jobs from a single history view.</p>
              <p>Click any card to expand or collapse its full run details.</p>
              <p>The View button opens the generated report when one is available.</p>
              <p>To keep the animation stable, each toggle needs about 2 seconds before the next one.</p>
            </div>
          }
        />
      </div>

      {pageError && <p className="text-sm text-destructive break-words">{pageError}</p>}

      {items.length === 0 ? (
        !pageError && <p className="text-muted-foreground">No jobs yet. Start a run to build your reports history.</p>
      ) : (
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <Card size="sm">
              <CardHeader className="pb-2">
                <CardDescription>Selector diagnostics</CardDescription>
                <CardTitle className="text-2xl">{diagnosticsOverview.selectorJobs}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-xs text-muted-foreground">Jobs that saved paper-selection memory and reranking traces.</p>
              </CardContent>
            </Card>

            <Card size="sm">
              <CardHeader className="pb-2">
                <CardDescription>Working memory</CardDescription>
                <CardTitle className="text-2xl">{diagnosticsOverview.workingMemoryJobs}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-xs text-muted-foreground">Jobs with interpreter short-term memory artifacts ready for inspection.</p>
              </CardContent>
            </Card>

            <Card size="sm">
              <CardHeader className="pb-2">
                <CardDescription>Heavy writeback prompts</CardDescription>
                <CardTitle className="text-2xl">{diagnosticsOverview.heavyPromptJobs}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-xs text-muted-foreground">
                  Jobs whose memory writeback prompt reached {HIGH_PROMPT_THRESHOLD.toLocaleString()}+ chars.
                  {diagnosticsOverview.maxPromptChars > 0 ? ` Max seen: ${diagnosticsOverview.maxPromptChars.toLocaleString()}.` : ''}
                </p>
              </CardContent>
            </Card>

            <Card size="sm">
              <CardHeader className="pb-2">
                <CardDescription>Review-required memory</CardDescription>
                <CardTitle className="text-2xl">{diagnosticsOverview.reviewRequiredJobs}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-xs text-muted-foreground">
                  Jobs with pending cautious promotions. Accepted promotions accumulated: {diagnosticsOverview.totalAcceptedPromotions.toLocaleString()}.
                </p>
              </CardContent>
            </Card>
          </div>

          <div className="flex flex-wrap gap-2">
            {([
              ['all', `All jobs (${items.length})`],
              ['selector', `Selector (${diagnosticsOverview.selectorJobs})`],
              ['working-memory', `Working memory (${diagnosticsOverview.workingMemoryJobs})`],
              ['heavy-prompt', `Heavy prompt (${diagnosticsOverview.heavyPromptJobs})`],
              ['review-required', `Review required (${diagnosticsOverview.reviewRequiredJobs})`],
            ] as Array<[DiagnosticFilter, string]>).map(([key, label]) => (
              <Button
                key={key}
                type="button"
                size="sm"
                variant={activeFilter === key ? 'default' : 'outline'}
                onClick={() => setActiveFilter(key)}
              >
                {label}
              </Button>
            ))}
          </div>

          <div className="space-y-3">
            {filteredItems.length === 0 ? (
              <p className="text-sm text-muted-foreground">No jobs match the current diagnostics filter.</p>
            ) : filteredItems.map((item) => {
              const isExpanded = visibleExpandedJobId === item.job_id
              const detailId = `report-details-${item.job_id}`

              return (
                <Card key={item.job_id}>
                  <CardContent className="p-4">
                    <JobHistoryCard
                      item={item}
                      expanded={isExpanded}
                      detailId={detailId}
                      toggleCoolingDown={isToggleCoolingDown}
                      onToggle={handleToggle}
                      onView={handleView}
                      onRetry={handleRetry}
                      onRunAgain={handleRunAgain}
                      onDelete={openDeleteDialog}
                    />
                  </CardContent>
                </Card>
              )
            })}
          </div>
        </div>
      )}

      <Dialog
        open={isDeleteDialogOpen}
        onOpenChange={(open) => {
          if (!open) {
            closeDeleteDialog()
            return
          }
          setIsDeleteDialogOpen(true)
        }}
      >
        <DialogContent className="max-w-lg" showCloseButton={!isDeleting}>
          <DialogHeader>
            <DialogTitle>Delete Report Job</DialogTitle>
            <DialogDescription>
              This will permanently remove the selected report job and all related artifacts.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 text-sm">
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-red-700">
              <p className="font-semibold break-words">{pendingDeleteTitle}</p>
              <p className="mt-1 font-mono text-xs text-red-600">{pendingDeleteItem?.job_id}</p>
            </div>
            <p className="text-muted-foreground">
              Deletion scope includes the report, variants, extracted assets, fetch/cache files, the linked Papers entry, and the job record itself.
            </p>
            <p className="text-muted-foreground">
              If this job already wrote into a Memory Profile, its related memory bundle and derived profile views will be rebuilt from the remaining data, and downstream Dashboard or Reports entries will disappear on the next data refresh.
            </p>
            <p className="text-muted-foreground">
              The confirm button unlocks after {DELETE_CONFIRM_SECONDS} seconds so you can review the scope carefully.
            </p>
            {deleteError && <p className="text-sm text-destructive break-words">{deleteError}</p>}
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={closeDeleteDialog} disabled={isDeleting}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={handleDeleteConfirm}
              disabled={isDeleting || deleteCountdown > 0}
            >
              {isDeleting
                ? 'Deleting...'
                : deleteCountdown > 0
                  ? `Confirm Delete (${deleteCountdown}s)`
                  : 'Confirm Delete'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
