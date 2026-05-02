import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { type JobReportSummary } from '@/api/client'
import { normalizeRunConfigSource } from '@/lib/jobConfig'
import { readDiagnosticNumber, readPromotionCount } from '@/lib/reportDiagnostics'
import { cn } from '@/lib/utils'

const STATUS_VARIANT: Record<string, 'default' | 'secondary' | 'destructive' | 'outline'> = {
  completed: 'default',
  failed: 'destructive',
  pending: 'outline',
  selecting: 'secondary',
  processing: 'secondary',
  interpreting: 'secondary',
}

const TRACK_LABELS: Record<string, string> = {
  auto: 'Auto',
  recent: 'Recent',
  classic: 'Classic',
  goat: 'GOAT',
}

const MAX_TOPIC_KEYWORDS = 8

interface DetailItemProps {
  label: string
  children: ReactNode
  className?: string
}

interface JobHistoryCardProps {
  item: JobReportSummary
  expanded: boolean
  detailId: string
  toggleCoolingDown: boolean
  onToggle: (jobId: string) => void
  onView: (jobId: string) => void
  onRetry: (item: JobReportSummary) => void
  onRunAgain: (item: JobReportSummary) => void
  onDelete: (item: JobReportSummary) => void
}

function DetailItem({ label, children, className }: DetailItemProps) {
  return (
    <div className={cn('space-y-1.5', className)}>
      <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">{label}</p>
      {children}
    </div>
  )
}

function formatDateTime(timestamp: number | null | undefined) {
  if (typeof timestamp !== 'number' || Number.isNaN(timestamp) || timestamp <= 0) {
    return '—'
  }
  return new Date(timestamp * 1000).toLocaleString()
}

function formatDuration(startedAt: number | null, completedAt: number | null) {
  if (typeof startedAt !== 'number' || typeof completedAt !== 'number') {
    return ''
  }
  const seconds = Math.max(0, Math.round(completedAt - startedAt))
  return `${seconds}s elapsed`
}

function formatSize(sizeBytes: number) {
  if (!sizeBytes) {
    return '—'
  }
  return `${(sizeBytes / 1024).toFixed(1)} KB`
}

function getTopicDetails(item: JobReportSummary) {
  const normalized = normalizeRunConfigSource(item.config_snapshot)
  return {
    topicName: normalized.topicName,
    topicKeywords: normalized.topicKeywords,
  }
}

function getSelectionDetails(item: JobReportSummary) {
  const normalized = normalizeRunConfigSource(item.config_snapshot)
  return {
    track: normalized.track,
    preferredVenues: normalized.venues,
  }
}

function getReportLabel(reportPath: string) {
  const trimmedPath = reportPath.trim()
  if (!trimmedPath) {
    return ''
  }
  const parts = trimmedPath.split(/[\\/]/).filter(Boolean)
  return parts.at(-1) ?? trimmedPath
}

function getProfileLabel(item: JobReportSummary) {
  if (item.profile_name.trim()) {
    if (item.profile_mode === 'auto' && item.profile_assignment_status === 'created') {
      return `Auto created: ${item.profile_name}`
    }
    if (item.profile_mode === 'auto' && item.profile_assignment_status === 'matched') {
      return `Auto matched: ${item.profile_name}`
    }
    return item.profile_name
  }
  if (item.profile_mode === 'auto') {
    return item.profile_assignment_status === 'pending' ? 'Auto assigning...' : 'Auto assign'
  }
  return 'Default'
}

function JobHistoryDetailPanel({ expanded, detailId, item, onDelete }: Pick<JobHistoryCardProps, 'expanded' | 'detailId' | 'item' | 'onDelete'>) {
  const contentRef = useRef<HTMLDivElement | null>(null)
  const [maxHeight, setMaxHeight] = useState(0)

  const { topicName, topicKeywords } = getTopicDetails(item)
  const { track, preferredVenues } = getSelectionDetails(item)
  const showAutoSettings = item.mode === 'auto'
  const visibleKeywords = topicKeywords.slice(0, MAX_TOPIC_KEYWORDS)
  const hiddenKeywordCount = Math.max(topicKeywords.length - visibleKeywords.length, 0)
  const reportLabel = getReportLabel(item.report_path)
  const profileLabel = getProfileLabel(item)
  const progressLabel = `${Math.max(0, Math.min(100, Math.round(item.progress || 0)))}%`
  const trackLabel = TRACK_LABELS[track] || track
  const durationLabel = formatDuration(item.started_at, item.completed_at)
  const selectorCandidateCount = readDiagnosticNumber(item, 'selector_candidate_count')
  const selectorRankedCount = readDiagnosticNumber(item, 'selector_ranked_count')
  const selectorMemoryChars = readDiagnosticNumber(item, 'selector_memory_chars')
  const promptChars = readDiagnosticNumber(item, 'memory_extraction_prompt_chars')
  const acceptedPromotions = readPromotionCount(item, 'accepted')
  const reviewPromotions = readPromotionCount(item, 'review_required')

  const handleDeleteClick = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation()
    onDelete(item)
  }

  useEffect(() => {
    const updateHeight = () => {
      setMaxHeight(expanded ? (contentRef.current?.scrollHeight ?? 0) : 0)
    }

    updateHeight()

    if (typeof ResizeObserver === 'undefined' || !contentRef.current) {
      return
    }

    const observer = new ResizeObserver(() => {
      updateHeight()
    })
    observer.observe(contentRef.current)
    return () => observer.disconnect()
  }, [
    expanded,
    hiddenKeywordCount,
    item.completed_at,
    item.created_at,
    item.current_step,
    item.has_report,
    item.modified_at,
    item.paper_title,
    item.profile_name,
    item.progress,
    item.report_path,
    item.size_bytes,
    item.started_at,
    preferredVenues,
    reportLabel,
    topicKeywords,
    topicName,
    trackLabel,
    durationLabel,
  ])

  return (
    <div
      id={detailId}
      aria-hidden={!expanded}
      className="overflow-hidden transition-[max-height] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none"
      style={{ maxHeight }}
    >
      <div
        ref={contentRef}
        className={cn(
          'border-t border-border/70 pt-4 transition-[opacity,transform] duration-300 ease-out motion-reduce:transition-none',
          expanded ? 'translate-y-0 opacity-100 delay-100' : '-translate-y-1 opacity-0',
        )}
      >
        <div className="grid gap-3 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
          <section className="space-y-3 rounded-xl border border-border/60 bg-muted/25 p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Run metadata</p>
            <div className="grid gap-3 md:grid-cols-2">
              <DetailItem label="Memory Profile">
                <div className="space-y-1">
                  <Badge variant="secondary" className="w-fit">{profileLabel}</Badge>
                  <p className="text-xs text-muted-foreground">
                    {item.profile_assignment_note || 'Shared memory context for cognition, claims, conflicts, and source bundles.'}
                  </p>
                </div>
              </DetailItem>

              <DetailItem label="Pipeline">
                <div className="space-y-1">
                  <p className="text-sm text-foreground">{item.current_step || 'Waiting to start'}</p>
                  <p className="text-xs text-muted-foreground">Progress {progressLabel}</p>
                </div>
              </DetailItem>

              <DetailItem label="Created At">
                <p className="text-sm text-foreground">{formatDateTime(item.created_at)}</p>
              </DetailItem>

              <DetailItem label="Started At">
                <p className="text-sm text-foreground">{formatDateTime(item.started_at)}</p>
              </DetailItem>

              <DetailItem label="Completed At">
                <p className="text-sm text-foreground">{formatDateTime(item.completed_at)}</p>
              </DetailItem>

              <DetailItem label="Duration">
                <p className="text-sm text-foreground">{durationLabel || '—'}</p>
              </DetailItem>

              <DetailItem label="Report File" className="md:col-span-2">
                {item.has_report && reportLabel ? (
                  <div className="space-y-1">
                    <p className="truncate font-mono text-xs text-muted-foreground" title={item.report_path}>
                      {reportLabel}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      Updated {formatDateTime(item.modified_at)} · {formatSize(item.size_bytes)}
                    </p>
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">No report generated yet.</p>
                )}
              </DetailItem>

              <DetailItem label="Memory Artifacts" className="md:col-span-2">
                {item.has_selector_diagnostics || item.has_working_memory || item.has_distilled_memory_summary ? (
                  <div className="flex flex-wrap gap-1.5">
                    {item.has_selector_diagnostics && <Badge variant="outline">Selector Diagnostics</Badge>}
                    {item.has_working_memory && <Badge variant="outline">Working Memory</Badge>}
                    {item.has_distilled_memory_summary && <Badge variant="outline">Distilled Summary</Badge>}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">No memory artifacts saved for this job yet.</p>
                )}
              </DetailItem>

              {(selectorCandidateCount > 0 || promptChars > 0 || acceptedPromotions > 0 || reviewPromotions > 0) && (
                <DetailItem label="Diagnostic Snapshot" className="md:col-span-2">
                  <div className="flex flex-wrap gap-1.5">
                    {selectorCandidateCount > 0 && (
                      <Badge variant="secondary">
                        Selector {selectorCandidateCount}
                        {selectorRankedCount > 0 ? ` -> ${selectorRankedCount}` : ''}
                      </Badge>
                    )}
                    {selectorMemoryChars > 0 && (
                      <Badge variant="outline">Selector memory {selectorMemoryChars} chars</Badge>
                    )}
                    {promptChars > 0 && (
                      <Badge variant="outline">Writeback prompt {promptChars} chars</Badge>
                    )}
                    {acceptedPromotions > 0 && (
                      <Badge variant="secondary">Accepted {acceptedPromotions}</Badge>
                    )}
                    {reviewPromotions > 0 && (
                      <Badge variant="outline">Review {reviewPromotions}</Badge>
                    )}
                  </div>
                </DetailItem>
              )}
            </div>
          </section>

          <section className="space-y-3 rounded-xl border border-border/60 bg-muted/25 p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Run settings</p>
            <div className="grid gap-3 md:grid-cols-2">
              {showAutoSettings && topicName && (
                <DetailItem label="Topic" className="md:col-span-2">
                  <p className="text-sm font-medium text-foreground break-words">{topicName}</p>
                </DetailItem>
              )}

              {showAutoSettings && trackLabel && (
                <DetailItem label="Track">
                  <Badge variant="outline" className="w-fit">{trackLabel}</Badge>
                </DetailItem>
              )}

              <DetailItem label="Mode">
                <Badge variant="outline" className="w-fit">{item.mode}</Badge>
              </DetailItem>

              {showAutoSettings && preferredVenues.length > 0 && (
                <DetailItem label="Preferred Venues" className="md:col-span-2">
                  <div className="flex flex-wrap gap-1.5">
                    {preferredVenues.map((venue) => (
                      <Badge key={venue} variant="outline">{venue}</Badge>
                    ))}
                  </div>
                </DetailItem>
              )}

              {showAutoSettings && visibleKeywords.length > 0 && (
                <DetailItem label="Topic Keywords" className="md:col-span-2">
                  <div className="flex flex-wrap gap-1.5">
                    {visibleKeywords.map((keyword) => (
                      <Badge key={keyword} variant="secondary">{keyword}</Badge>
                    ))}
                    {hiddenKeywordCount > 0 && <Badge variant="outline">+{hiddenKeywordCount} more</Badge>}
                  </div>
                </DetailItem>
              )}

              {!showAutoSettings && (
                <DetailItem label="Settings" className="md:col-span-2">
                  <p className="text-sm text-muted-foreground">Manual runs do not include topic, track, venue, or keyword settings.</p>
                </DetailItem>
              )}
            </div>
          </section>
        </div>

        <div className="mt-4 flex justify-end">
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="border-red-200 bg-white font-bold text-red-600 shadow-sm hover:bg-red-50 hover:text-red-700 dark:border-red-300 dark:bg-white dark:text-red-600 dark:hover:bg-red-50 dark:hover:text-red-700"
            onClick={handleDeleteClick}
          >
            Delete
          </Button>
        </div>
      </div>
    </div>
  )
}

export function JobHistoryCard({ item, expanded, detailId, toggleCoolingDown, onToggle, onView, onRetry, onRunAgain, onDelete }: JobHistoryCardProps) {
  const title = item.title || item.paper_title || item.job_id
  const durationLabel = formatDuration(item.started_at, item.completed_at)
  const canRetry = item.status === 'failed'
  const canRunAgain = item.status === 'failed' && item.mode === 'auto'
  const selectorCandidateCount = readDiagnosticNumber(item, 'selector_candidate_count')
  const selectorRankedCount = readDiagnosticNumber(item, 'selector_ranked_count')
  const promptChars = readDiagnosticNumber(item, 'memory_extraction_prompt_chars')
  const acceptedPromotions = readPromotionCount(item, 'accepted')

  const handleCardKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Enter' && event.key !== ' ') {
      return
    }
    event.preventDefault()
    onToggle(item.job_id)
  }

  const handleViewClick = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation()
    if (!item.has_report) {
      return
    }
    onView(item.job_id)
  }

  const handleRetryClick = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation()
    if (!canRetry) {
      return
    }
    onRetry(item)
  }

  const handleRunAgainClick = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation()
    if (!canRunAgain) {
      return
    }
    onRunAgain(item)
  }

  return (
    <div
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      aria-controls={detailId}
      aria-disabled={toggleCoolingDown}
      onClick={() => onToggle(item.job_id)}
      onKeyDown={handleCardKeyDown}
      className={cn(
        '-m-4 rounded-xl p-4 transition-[box-shadow,transform] duration-300 ease-out motion-reduce:transition-none focus-visible:outline-none',
        toggleCoolingDown ? 'cursor-default' : 'cursor-pointer',
      )}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={STATUS_VARIANT[item.status] || 'secondary'}>{item.status}</Badge>
            <Badge variant="outline">{item.mode}</Badge>
            <span className="text-xs font-mono text-muted-foreground">{item.job_id}</span>
          </div>

          <div className="space-y-1">
            <p className="text-sm font-semibold text-foreground break-words">{title}</p>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
              <span>{formatDateTime(item.created_at)}</span>
              {item.has_report ? <span>Report ready</span> : <span>No report yet</span>}
              {item.has_selector_diagnostics && <span>Selector diagnostics</span>}
              {item.has_working_memory && <span>Working memory</span>}
              {item.has_distilled_memory_summary && <span>Distilled summary</span>}
              {selectorCandidateCount > 0 && <span>Selector {selectorCandidateCount}{selectorRankedCount > 0 ? `->${selectorRankedCount}` : ''}</span>}
              {promptChars > 0 && <span>Prompt {promptChars}</span>}
              {acceptedPromotions > 0 && <span>Accepted {acceptedPromotions}</span>}
              {item.has_report && item.size_bytes > 0 && <span>{formatSize(item.size_bytes)}</span>}
            </div>
            {item.error && <p className="text-xs text-destructive break-words">{item.error}</p>}
          </div>
        </div>

        <div className="flex shrink-0 flex-col items-end gap-2 text-right">
          <div className="space-y-1 text-xs text-muted-foreground">
            {item.has_report ? (
              <p>{formatDateTime(item.modified_at)}</p>
            ) : (
              <p>{item.current_step || 'Waiting to start'}</p>
            )}
            <p>{durationLabel || `Progress ${Math.max(0, Math.min(100, Math.round(item.progress || 0)))}%`}</p>
          </div>

          {canRetry ? (
            <div className="flex flex-col items-end gap-2">
              {canRunAgain && (
                <Button
                  type="button"
                  size="sm"
                  variant="default"
                  onClick={handleRunAgainClick}
                >
                  Run Again
                </Button>
              )}
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={handleRetryClick}
              >
                Retry
              </Button>
            </div>
          ) : (
            <Button
              type="button"
              size="sm"
              variant={item.has_report ? 'default' : 'outline'}
              disabled={!item.has_report}
              onClick={handleViewClick}
            >
              View
            </Button>
          )}
        </div>
      </div>

      <JobHistoryDetailPanel expanded={expanded} detailId={detailId} item={item} onDelete={onDelete} />
    </div>
  )
}
