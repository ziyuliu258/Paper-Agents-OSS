import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  ArrowLeft,
  Brain,
  ChevronDown,
  ChevronUp,
  Download,
  FileJson,
  FileSearch,
  Files,
  HelpCircle,
  Network,
  RotateCcw,
  SearchCheck,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { clipText } from '@/lib/formatters'
import { cn } from '@/lib/utils'
import {
  getJobLocalizedDistilledSummary,
  getJobLocalizedWorkingMemoryArtifact,
  getJobMemoryArtifactUrl,
  getJobReportAuditArtifact,
  getJobReport,
  getJobSelectorDiagnosticsArtifact,
  getJobWorkingMemoryArtifact,
  refineJobReport,
  rerunJob,
  type JobReport,
  type ReportAuditArtifact,
  type JobReportVariantSummary,
  type SelectorDiagnosticsArtifact,
  type WorkingMemoryArtifact,
} from '@/api/client'

interface ReportState {
  key: string
  report: JobReport | null
  error: string | null
}

type WorkspacePanel = 'working-memory' | 'distilled-summary' | 'selector' | 'audit'
type MemoryLanguage = 'zh' | 'en'
type PromotionFilter = 'all' | 'accepted' | 'review_required' | 'rejected'
type ReportStructureTarget = 'preserve' | 'classic' | 'pmrc'
type ReportDetailLevel = 'auto' | 'concise' | 'balanced' | 'detailed'
const textareaClass = 'min-h-28 w-full rounded-lg border border-input bg-transparent px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50'

function byLanguage(language: MemoryLanguage, zh: string, en: string) {
  return language === 'zh' ? zh : en
}

function formatMetricValue(value: number) {
  return Number.isFinite(value) ? value.toLocaleString() : '0'
}

function formatConfidence(value?: number) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return null
  }
  return `${Math.round(Math.max(0, Math.min(value, 1)) * 100)}%`
}

function formatDateTime(value?: number) {
  if (!value || !Number.isFinite(value)) {
    return 'Unavailable'
  }
  return new Date(value * 1000).toLocaleString()
}


function needsExpansion(text: string, threshold = 260) {
  const normalized = text.trim()
  return normalized.length > threshold || normalized.includes('\n')
}

function formatStageLabel(stage?: string, language: MemoryLanguage = 'en') {
  const labels = {
    en: {
      paper_notes_ready: 'Paper Notes Ready',
      memory_context_ready: 'Memory Context Ready',
      tasks_complete: 'Tasks Complete',
      writeback_ready: 'Writeback Ready',
      report_assembled: 'Report Assembled',
      default: 'Preparing',
    },
    zh: {
      paper_notes_ready: '论文笔记已就绪',
      memory_context_ready: '记忆上下文已注入',
      tasks_complete: '任务执行完成',
      writeback_ready: '写回候选已就绪',
      report_assembled: '报告已组装',
      default: '准备中',
    },
  } as const
  return labels[language][stage as keyof (typeof labels)[typeof language]] || labels[language].default
}

function formatSectionKeyLabel(sectionKey?: string, language: MemoryLanguage = 'en') {
  const normalized = (sectionKey || '').trim().toLowerCase()
  const labels: Record<string, { zh: string; en: string }> = {
    background: { zh: '背景', en: 'background' },
    method: { zh: '方法', en: 'method' },
    experiments: { zh: '实验', en: 'experiments' },
    ablation: { zh: '消融', en: 'ablation' },
    conclusion: { zh: '结论', en: 'conclusion' },
    limitations: { zh: '局限性', en: 'limitations' },
    summary: { zh: '总结', en: 'summary' },
    task_output: { zh: '任务输出', en: 'task_output' },
    paper_notes: { zh: '论文笔记', en: 'paper_notes' },
  }
  return labels[normalized]?.[language] || sectionKey || ''
}

function formatObservationKindLabel(kind?: string, language: MemoryLanguage = 'en') {
  const normalized = (kind || '').trim().toLowerCase()
  const labels: Record<string, { zh: string; en: string }> = {
    task_output: { zh: '任务输出', en: 'task_output' },
    paper_note: { zh: '论文笔记', en: 'paper_note' },
    evidence: { zh: '证据', en: 'evidence' },
    adjudication: { zh: '裁决', en: 'adjudication' },
    memory_recall: { zh: '记忆召回', en: 'memory_recall' },
  }
  return labels[normalized]?.[language] || kind || ''
}

function formatPromotionStatusLabel(status?: string, language: MemoryLanguage = 'en') {
  const normalized = (status || '').trim().toLowerCase()
  const labels: Record<string, { zh: string; en: string }> = {
    accepted: { zh: '已接受', en: 'accepted' },
    review_required: { zh: '待复核', en: 'review_required' },
    rejected: { zh: '已拒绝', en: 'rejected' },
    candidate: { zh: '候选', en: 'candidate' },
    open: { zh: '待处理', en: 'open' },
    resolved: { zh: '已解决', en: 'resolved' },
  }
  return labels[normalized]?.[language] || status || ''
}

function formatPromotionTypeLabel(candidateType?: string, language: MemoryLanguage = 'en') {
  const normalized = (candidateType || '').trim().toLowerCase()
  const labels: Record<string, { zh: string; en: string }> = {
    claim: { zh: 'Claim', en: 'claim' },
    synthesis: { zh: 'Synthesis', en: 'synthesis' },
    entity_link: { zh: '实体关联', en: 'entity_link' },
    style_preference: { zh: '风格偏好', en: 'style_preference' },
  }
  return labels[normalized]?.[language] || candidateType || ''
}

function formatImportanceLabel(importance?: string, language: MemoryLanguage = 'en') {
  const normalized = (importance || '').trim().toLowerCase()
  const labels: Record<string, { zh: string; en: string }> = {
    high: { zh: '高重要性', en: 'high' },
    medium: { zh: '中重要性', en: 'medium' },
    low: { zh: '低重要性', en: 'low' },
  }
  return labels[normalized]?.[language] || importance || ''
}

function formatStructureModeLabel(mode?: string, language: MemoryLanguage = 'en') {
  const normalized = (mode || '').trim().toLowerCase()
  const labels: Record<string, { zh: string; en: string }> = {
    preserve: { zh: '维持当前结构', en: 'preserve current structure' },
    classic: { zh: '当前结构', en: 'current structure' },
    pmrc: { zh: 'PMRC 叙事', en: 'PMRC narrative' },
  }
  return labels[normalized]?.[language] || mode || ''
}

function formatDetailLevelLabel(level?: string, language: MemoryLanguage = 'en') {
  const normalized = (level || '').trim().toLowerCase()
  const labels: Record<string, { zh: string; en: string }> = {
    auto: { zh: '自动', en: 'auto' },
    concise: { zh: '精简', en: 'concise' },
    balanced: { zh: '平衡', en: 'balanced' },
    detailed: { zh: '展开', en: 'detailed' },
  }
  return labels[normalized]?.[language] || level || ''
}

function buildAssetBase(jobId?: string) {
  if (!jobId) {
    return ''
  }
  return `/api/reports/jobs/${encodeURIComponent(jobId)}/assets/`
}

function rewriteMarkdownAssetLinks(markdown: string, assetBase: string) {
  if (!markdown || !assetBase) {
    return markdown
  }

  return markdown
    .replace(/\]\(assets\//g, `](${assetBase}`)
    .replace(/\]\(<assets\//g, `](<${assetBase}`)
}

function rewriteGithubAlerts(markdown: string) {
  if (!markdown) {
    return markdown
  }

  return markdown.replace(
    /^>\s*\[!([^\]]+)\]\s*\n((?:>.*(?:\n|$))*)/gm,
    (_match, level: string, body: string) => {
      const rawLabel = level.trim()
      const normalizedLower = rawLabel.toLowerCase()
      const labelMap: Record<string, string> = {
        note: 'Note',
        tip: 'Tip',
        important: 'Important',
        warning: 'Warning',
        caution: 'Caution',
        提示: '提示',
        重要: '重要',
        警告: '警告',
        注意: '注意',
      }
      const label = labelMap[normalizedLower] || labelMap[rawLabel] || rawLabel
      const normalizedBody = body
        .split('\n')
        .map((line) => {
          if (!line.trim()) {
            return '>'
          }
          return line.startsWith('>') ? line : `> ${line}`
        })
        .join('\n')
        .replace(/\n+$/, '')
      return `> **${label}**\n${normalizedBody}`
    },
  )
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null
  }
  return value as Record<string, unknown>
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function asString(value: unknown) {
  return typeof value === 'string' ? value.trim() : ''
}

function asNumber(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function pickText(record: Record<string, unknown> | null, keys: string[]) {
  if (!record) {
    return ''
  }
  for (const key of keys) {
    const value = asString(record[key])
    if (value) {
      return value
    }
  }
  return ''
}

function pickNumber(record: Record<string, unknown> | null, keys: string[]) {
  if (!record) {
    return null
  }
  for (const key of keys) {
    const value = asNumber(record[key])
    if (value !== null) {
      return value
    }
  }
  return null
}

function extractPromotionText(payload?: { title?: string; body?: string; summary?: string }) {
  return payload?.summary?.trim() || payload?.body?.trim() || payload?.title?.trim() || ''
}

function findVariantSummary(report: JobReport | null, variantId: string) {
  if (!report) {
    return null
  }
  return report.variants.find((item) => item.variant_id === variantId) ?? null
}

const markdownComponents: Components = {
  a: ({ href, children, ...props }) => {
    const normalizedHref = href ?? ''
    const isExternal = /^https?:\/\//i.test(normalizedHref)

    return (
      <a
        {...props}
        href={normalizedHref}
        target={isExternal ? '_blank' : undefined}
        rel={isExternal ? 'noreferrer' : undefined}
      >
        {children}
      </a>
    )
  },
  img: ({ src, alt, ...props }) => (
    <img
      {...props}
      src={src ?? ''}
      alt={alt ?? ''}
      loading="lazy"
    />
  ),
  table: ({ children, ...props }) => (
    <div className="markdown-table-wrap">
      <table {...props}>{children}</table>
    </div>
  ),
}

function SectionHeader({
  icon: Icon,
  title,
  description,
}: {
  icon: typeof Brain
  title: string
  description: string
}) {
  return (
    <div className="flex items-start gap-3">
      <div className="rounded-2xl bg-sky-100 p-2 text-sky-700">
        <Icon className="h-4 w-4" />
      </div>
      <div className="space-y-1">
        <h3 className="text-base font-semibold text-neutral-950">{title}</h3>
        <p className="text-sm text-neutral-600">{description}</p>
      </div>
    </div>
  )
}

function MetricStrip({
  items,
}: {
  items: Array<{ label: string; value: string }>
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {items.map((item) => (
        <div key={item.label} className="rounded-2xl border border-neutral-200 bg-white px-4 py-3 shadow-sm">
          <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-neutral-500">{item.label}</p>
          <p className="mt-1 text-lg font-semibold text-neutral-950">{item.value}</p>
        </div>
      ))}
    </div>
  )
}

function MemoryRichText({
  content,
  className,
}: {
  content: string
  className?: string
}) {
  const normalized = content.trim()
  if (!normalized) {
    return null
  }

  return (
    <div
      className={cn(
        'report-markdown max-w-none text-sm leading-6 [&_ol]:my-2 [&_p]:my-0 [&_ul]:my-2 [&_li]:my-1',
        className,
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {normalized}
      </ReactMarkdown>
    </div>
  )
}

export default function ReportViewer() {
  const { jobId } = useParams<{ jobId?: string }>()
  const navigate = useNavigate()
  const [reportState, setReportState] = useState<ReportState | null>(null)
  const [selectedVariantId, setSelectedVariantId] = useState<string>('original')
  const [isRerunning, setIsRerunning] = useState(false)
  const [rerunError, setRerunError] = useState<string | null>(null)
  const [rerunJobId, setRerunJobId] = useState<string | null>(null)
  const [refineInstruction, setRefineInstruction] = useState('')
  const [targetStructureMode, setTargetStructureMode] = useState<ReportStructureTarget>('preserve')
  const [detailLevel, setDetailLevel] = useState<ReportDetailLevel>('balanced')
  const [isRefining, setIsRefining] = useState(false)
  const [refineError, setRefineError] = useState<string | null>(null)
  const [workingMemoryArtifact, setWorkingMemoryArtifact] = useState<WorkingMemoryArtifact | null>(null)
  const [localizedWorkingMemoryArtifact, setLocalizedWorkingMemoryArtifact] = useState<WorkingMemoryArtifact | null>(null)
  const [isLocalizedWorkingMemoryLoading, setIsLocalizedWorkingMemoryLoading] = useState(false)
  const [localizedWorkingMemoryError, setLocalizedWorkingMemoryError] = useState<string | null>(null)
  const [selectorDiagnosticsArtifact, setSelectorDiagnosticsArtifact] = useState<SelectorDiagnosticsArtifact | null>(null)
  const [reportAuditArtifact, setReportAuditArtifact] = useState<ReportAuditArtifact | null>(null)
  const [distilledSummary, setDistilledSummary] = useState<string>('')
  const [distilledSummaryError, setDistilledSummaryError] = useState<string | null>(null)
  const [activePanel, setActivePanel] = useState<WorkspacePanel>('working-memory')
  const [memoryLanguage, setMemoryLanguage] = useState<MemoryLanguage>('zh')
  const [promotionFilter, setPromotionFilter] = useState<PromotionFilter>('all')
  const [expandedObservationKeys, setExpandedObservationKeys] = useState<Record<string, boolean>>({})
  const [isWorkspaceCollapsed, setIsWorkspaceCollapsed] = useState(false)

  useEffect(() => {
    setSelectedVariantId('original')
    setRefineInstruction('')
    setTargetStructureMode('preserve')
    setDetailLevel('balanced')
    setRefineError(null)
  }, [jobId])

  useEffect(() => {
    if (!jobId) return

    let cancelled = false
    const activeVariantId = selectedVariantId && selectedVariantId !== 'original' ? selectedVariantId : undefined
    const requestKey = `${jobId}:${activeVariantId || 'original'}`

    getJobReport(jobId, activeVariantId ? { variantId: activeVariantId } : undefined)
      .then((report) => {
        if (cancelled) return
        setReportState({
          key: requestKey,
          report,
          error: null,
        })
      })
      .catch((error) => {
        console.error(error)
        if (cancelled) return
        setReportState({
          key: requestKey,
          report: null,
          error: error instanceof Error ? error.message : 'Failed to load report.',
        })
      })

    return () => {
      cancelled = true
    }
  }, [jobId, selectedVariantId])

  const routeKey = jobId ?? ''
  const reportRequestKey = `${routeKey}:${selectedVariantId || 'original'}`
  const isCurrentReport = reportState?.key === reportRequestKey
  const loading = Boolean(routeKey) && !isCurrentReport
  const error = isCurrentReport ? reportState?.error ?? null : null
  const report = isCurrentReport ? reportState?.report : null
  const content = report?.content ?? ''

  useEffect(() => {
    if (!jobId || !report) {
      setWorkingMemoryArtifact(null)
      setLocalizedWorkingMemoryArtifact(null)
      setIsLocalizedWorkingMemoryLoading(false)
      setLocalizedWorkingMemoryError(null)
      setSelectorDiagnosticsArtifact(null)
      setReportAuditArtifact(null)
      setDistilledSummary('')
      setDistilledSummaryError(null)
      return
    }

    let cancelled = false

    if (report.has_working_memory) {
      setIsLocalizedWorkingMemoryLoading(true)
      setLocalizedWorkingMemoryError(null)
      getJobWorkingMemoryArtifact(jobId)
        .then((payload) => {
          if (!cancelled) {
            setWorkingMemoryArtifact(payload)
          }
        })
        .catch((loadError) => {
          console.error(loadError)
          if (!cancelled) {
            setWorkingMemoryArtifact(null)
          }
        })

      getJobLocalizedWorkingMemoryArtifact(jobId, 'zh')
        .then((payload) => {
          if (!cancelled) {
            setLocalizedWorkingMemoryArtifact(payload)
            setLocalizedWorkingMemoryError(null)
          }
        })
        .catch((loadError) => {
          console.error(loadError)
          if (!cancelled) {
            setLocalizedWorkingMemoryArtifact(null)
            setLocalizedWorkingMemoryError(
              loadError instanceof Error ? loadError.message : 'Failed to localize working memory.',
            )
          }
        })
        .finally(() => {
          if (!cancelled) {
            setIsLocalizedWorkingMemoryLoading(false)
          }
        })
    } else {
      setWorkingMemoryArtifact(null)
      setLocalizedWorkingMemoryArtifact(null)
      setIsLocalizedWorkingMemoryLoading(false)
      setLocalizedWorkingMemoryError(null)
    }

    if (report.has_selector_diagnostics) {
      getJobSelectorDiagnosticsArtifact(jobId)
        .then((payload) => {
          if (!cancelled) {
            setSelectorDiagnosticsArtifact(payload)
          }
        })
        .catch((loadError) => {
          console.error(loadError)
          if (!cancelled) {
            setSelectorDiagnosticsArtifact(null)
          }
        })
    } else {
      setSelectorDiagnosticsArtifact(null)
    }

    if (report.has_report_audit) {
      getJobReportAuditArtifact(jobId)
        .then((payload) => {
          if (!cancelled) {
            setReportAuditArtifact(payload)
          }
        })
        .catch((loadError) => {
          console.error(loadError)
          if (!cancelled) {
            setReportAuditArtifact(null)
          }
        })
    } else {
      setReportAuditArtifact(null)
    }

    return () => {
      cancelled = true
    }
  }, [jobId, report])

  useEffect(() => {
    if (!jobId || !report?.has_distilled_memory_summary) {
      setDistilledSummary('')
      setDistilledSummaryError(null)
      return
    }

    let cancelled = false

    const loadDistilledSummary = async () => {
      try {
        const payload = await getJobLocalizedDistilledSummary(jobId, memoryLanguage)
        if (!cancelled) {
          setDistilledSummary(payload)
          setDistilledSummaryError(null)
        }
      } catch (loadError) {
        console.error(loadError)
        if (memoryLanguage === 'zh') {
          try {
            const fallbackPayload = await getJobLocalizedDistilledSummary(jobId, 'en')
            if (!cancelled) {
              setDistilledSummary(fallbackPayload)
              setDistilledSummaryError('Chinese localization is unavailable, so the page is temporarily falling back to English.')
            }
            return
          } catch (fallbackError) {
            console.error(fallbackError)
          }
        }

        if (!cancelled) {
          setDistilledSummary('')
          setDistilledSummaryError(loadError instanceof Error ? loadError.message : 'Failed to load distilled summary.')
        }
      }
    }

    void loadDistilledSummary()

    return () => {
      cancelled = true
    }
  }, [jobId, memoryLanguage, report?.has_distilled_memory_summary])

  const availablePanels = useMemo(() => {
    const panels: WorkspacePanel[] = []
    if (report?.has_working_memory) {
      panels.push('working-memory')
    }
    if (report?.has_distilled_memory_summary) {
      panels.push('distilled-summary')
    }
    if (report?.has_selector_diagnostics) {
      panels.push('selector')
    }
    if (report?.has_report_audit) {
      panels.push('audit')
    }
    return panels
  }, [report])

  useEffect(() => {
    if (availablePanels.length === 0) {
      return
    }
    if (!availablePanels.includes(activePanel)) {
      setActivePanel(availablePanels[0])
    }
  }, [activePanel, availablePanels])

  const processedContent = useMemo(() => {
    const assetBase = buildAssetBase(jobId)
    return rewriteGithubAlerts(rewriteMarkdownAssetLinks(content, assetBase))
  }, [content, jobId])

  const displayWorkingMemoryArtifact = useMemo(() => {
    if (memoryLanguage === 'zh') {
      return localizedWorkingMemoryArtifact ?? workingMemoryArtifact
    }
    return workingMemoryArtifact
  }, [localizedWorkingMemoryArtifact, memoryLanguage, workingMemoryArtifact])

  const workingMemoryView = useMemo(() => {
    const observations = displayWorkingMemoryArtifact?.observations ?? []
    const openQuestions = (displayWorkingMemoryArtifact?.open_questions ?? []).filter((item) => item.status === 'open')
    const resolvedQuestions = (displayWorkingMemoryArtifact?.open_questions ?? []).filter((item) => item.status === 'resolved')
    const draftClaims = displayWorkingMemoryArtifact?.draft_claims ?? []
    const promotionCandidates = displayWorkingMemoryArtifact?.promotion_candidates ?? []
    const terminologyEntries = Object.entries(displayWorkingMemoryArtifact?.terminology_map ?? {})
    const metrics = displayWorkingMemoryArtifact?.metrics ?? {}
    const promotionCounts = {
      accepted: promotionCandidates.filter((item) => item.status === 'accepted').length,
      review_required: promotionCandidates.filter((item) => item.status === 'review_required').length,
      rejected: promotionCandidates.filter((item) => item.status === 'rejected').length,
    }

    return {
      stageLabel: formatStageLabel(displayWorkingMemoryArtifact?.artifact_stage, memoryLanguage),
      observationCount: observations.length,
      openQuestionCount: openQuestions.length,
      resolvedQuestionCount: resolvedQuestions.length,
      draftClaimCount: draftClaims.length,
      promotionCount: promotionCandidates.length,
      promptChars: typeof metrics.memory_extraction_prompt_chars === 'number' ? metrics.memory_extraction_prompt_chars : 0,
      summaryChars: typeof metrics.memory_extraction_summary_chars === 'number' ? metrics.memory_extraction_summary_chars : 0,
      reviewChars: typeof metrics.memory_extraction_review_context_chars === 'number' ? metrics.memory_extraction_review_context_chars : 0,
      originalCandidateCount:
        typeof metrics.memory_extraction_original_candidate_count === 'number'
          ? metrics.memory_extraction_original_candidate_count
          : 0,
      selectedCandidateCount:
        typeof metrics.memory_extraction_candidate_count === 'number' ? metrics.memory_extraction_candidate_count : 0,
      retrievedClaimCount: typeof metrics.retrieved_claim_count === 'number' ? metrics.retrieved_claim_count : 0,
      retrievedEvidenceCount: typeof metrics.retrieved_evidence_count === 'number' ? metrics.retrieved_evidence_count : 0,
      translationHintCount: typeof metrics.translation_hint_count === 'number' ? metrics.translation_hint_count : 0,
      recentObservations: observations.slice(-6).reverse(),
      openQuestions: openQuestions.slice(0, 6),
      resolvedQuestions: resolvedQuestions.slice(-3).reverse(),
      draftClaims: draftClaims.slice(0, 6),
      promotionCandidates,
      promotionCounts,
      terminologyEntries: terminologyEntries.slice(0, 12),
      interpreterBundle: displayWorkingMemoryArtifact?.retrieved_context?.interpreter_bundle,
      translationBundle: displayWorkingMemoryArtifact?.retrieved_context?.translation_bundle,
    }
  }, [displayWorkingMemoryArtifact, memoryLanguage])

  useEffect(() => {
    if (!displayWorkingMemoryArtifact) {
      return
    }
    const currentCount = promotionFilter === 'all'
      ? workingMemoryView.promotionCandidates.length
      : workingMemoryView.promotionCounts[promotionFilter]
    if (currentCount > 0) {
      return
    }
    if (workingMemoryView.promotionCounts.review_required > 0) {
      setPromotionFilter('review_required')
      return
    }
    setPromotionFilter('all')
  }, [displayWorkingMemoryArtifact, promotionFilter, workingMemoryView.promotionCandidates.length, workingMemoryView.promotionCounts])

  const visiblePromotionCandidates = useMemo(() => {
    if (promotionFilter === 'all') {
      return workingMemoryView.promotionCandidates.slice(0, 12)
    }
    return workingMemoryView.promotionCandidates
      .filter((item) => item.status === promotionFilter)
      .slice(0, 12)
  }, [promotionFilter, workingMemoryView.promotionCandidates])

  const selectorView = useMemo(() => {
    const selected = selectorDiagnosticsArtifact?.selected
    const rankedCandidates = asArray(selectorDiagnosticsArtifact?.ranked_candidates)
      .slice(0, 5)
      .map((entry, index) => {
        const record = asRecord(entry)
        return {
          key: `${pickText(record, ['paper_id', 'title', 'paper_title']) || 'candidate'}-${index}`,
          title: pickText(record, ['title', 'paper_title', 'paper_id']) || `Candidate ${index + 1}`,
          venue: pickText(record, ['venue', 'source', 'publication_venue']),
          year: pickText(record, ['year', 'pub_year']),
          track: pickText(record, ['match_track', 'track']),
          reason: pickText(record, ['selection_reason', 'why_selected', 'summary']),
          score: pickNumber(record, ['score', 'rerank_score', 'final_score']),
        }
      })

    const bundle = selectorDiagnosticsArtifact?.selection_memory_bundle
    const digest = asArray(bundle?.high_level_digest)
    const priorityClaims = asArray(bundle?.priority_claims)
    const relatedPapers = asArray(bundle?.related_papers)
    const keywords = asArray(bundle?.keywords)

    return {
      candidateCount:
        typeof selectorDiagnosticsArtifact?.candidate_count === 'number' ? selectorDiagnosticsArtifact.candidate_count : 0,
      rankedCount:
        typeof selectorDiagnosticsArtifact?.ranked_count === 'number' ? selectorDiagnosticsArtifact.ranked_count : 0,
      selectionMemoryChars: selectorDiagnosticsArtifact?.selection_memory?.length ?? 0,
      selectedTitle: selected?.title || selected?.paper_id || 'Unavailable',
      selectedTrack: selected?.match_track || 'unknown track',
      selectedSource: selected?.source || 'unknown source',
      digest,
      priorityClaims,
      relatedPapers,
      keywords,
      rankedCandidates,
      memoryExcerpt: clipText(selectorDiagnosticsArtifact?.selection_memory || '', 420),
      fitJudgments: selectorDiagnosticsArtifact?.fit_judgments ?? [],
      failureReason: selectorDiagnosticsArtifact?.failure_reason || '',
      selectedPaperTopicAudit: selectorDiagnosticsArtifact?.selected_paper_topic_audit ?? null,
    }
  }, [selectorDiagnosticsArtifact])

  const reportAuditView = useMemo(() => {
    const issues = reportAuditArtifact?.issues ?? []
    const severityCounts = reportAuditArtifact?.severity_counts ?? {}
    return {
      status: reportAuditArtifact?.status || 'unavailable',
      warning: Boolean(reportAuditArtifact?.warning),
      repaired: Boolean(reportAuditArtifact?.repaired),
      issues,
      high: typeof severityCounts.high === 'number' ? severityCounts.high : 0,
      medium: typeof severityCounts.medium === 'number' ? severityCounts.medium : 0,
      low: typeof severityCounts.low === 'number' ? severityCounts.low : 0,
    }
  }, [reportAuditArtifact])

  const activeVariant = useMemo(
    () => findVariantSummary(report, report?.variant_id || selectedVariantId),
    [report, selectedVariantId],
  )

  const handleExportPdf = () => {
    window.print()
  }

  const handleOpenArtifact = (
    artifactName: 'selector-diagnostics' | 'working-memory' | 'distilled-memory-summary' | 'report-audit',
  ) => {
    if (!jobId) {
      return
    }
    window.open(getJobMemoryArtifactUrl(jobId, artifactName), '_blank', 'noopener,noreferrer')
  }

  const handleRerun = async () => {
    if (!jobId || isRerunning) {
      return
    }

    setIsRerunning(true)
    setRerunError(null)
    try {
      const job = await rerunJob(jobId)
      setRerunJobId(job.id)
    } catch (rerunLoadError) {
      console.error(rerunLoadError)
      setRerunError(rerunLoadError instanceof Error ? rerunLoadError.message : 'Failed to start regeneration.')
    } finally {
      setIsRerunning(false)
    }
  }

  const handleSelectVariant = (variant: JobReportVariantSummary) => {
    if (loading || variant.variant_id === selectedVariantId) {
      return
    }
    setSelectedVariantId(variant.variant_id)
    setRefineError(null)
  }

  const handleRefineReport = async () => {
    if (!jobId || !report || isRefining) {
      return
    }
    const instruction = refineInstruction.trim()
    if (!instruction) {
      setRefineError(byLanguage(memoryLanguage, '请先输入微调指令。', 'Please enter a refinement instruction first.'))
      return
    }

    setIsRefining(true)
    setRefineError(null)
    try {
      const refined = await refineJobReport(jobId, {
        instruction,
        target_structure_mode: targetStructureMode,
        detail_level: detailLevel,
        base_variant_id: report.variant_id || 'original',
      })
      setSelectedVariantId(refined.variant_id || 'original')
      setReportState({
        key: `${jobId}:${refined.variant_id || 'original'}`,
        report: refined,
        error: null,
      })
      setRefineInstruction('')
    } catch (loadError) {
      console.error(loadError)
      setRefineError(loadError instanceof Error ? loadError.message : byLanguage(memoryLanguage, '微调失败。', 'Refinement failed.'))
    } finally {
      setIsRefining(false)
    }
  }

  const toggleObservationExpanded = (key: string) => {
    setExpandedObservationKeys((current) => ({
      ...current,
      [key]: !current[key],
    }))
  }

  const renderWorkingMemoryPanel = () => (
    <div className="space-y-5">
      <SectionHeader
        icon={Brain}
        title={byLanguage(memoryLanguage, '短期记忆快照', 'Working Memory Snapshot')}
        description={byLanguage(
          memoryLanguage,
          '这部分展示解释器在生成报告时保留的短期认知状态：它看到了什么、暂时形成了哪些判断、还有哪些问题没完全闭环。',
          'This section shows the interpreter’s short-term reasoning state during report generation.',
        )}
      />

      <MetricStrip
        items={[
          { label: memoryLanguage === 'zh' ? '阶段' : 'Stage', value: workingMemoryView.stageLabel },
          { label: memoryLanguage === 'zh' ? '观察' : 'Observations', value: formatMetricValue(workingMemoryView.observationCount) },
          { label: memoryLanguage === 'zh' ? '草稿论断' : 'Draft Claims', value: formatMetricValue(workingMemoryView.draftClaimCount) },
          { label: memoryLanguage === 'zh' ? '未决问题' : 'Open Questions', value: formatMetricValue(workingMemoryView.openQuestionCount) },
        ]}
      />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(320px,0.9fr)]">
        <div className="space-y-4">
          <Card className="border-neutral-200 bg-white/95">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">{byLanguage(memoryLanguage, '近期观察', 'Recent observations')}</CardTitle>
              <CardDescription>
                {byLanguage(
                  memoryLanguage,
                  '模型刚刚确认过的事实与线索，会优先影响后续章节写作。',
                  'Fresh observations that most directly influenced later sections.',
                )}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {workingMemoryView.recentObservations.length === 0 ? (
                <p className="text-sm text-neutral-500">{byLanguage(memoryLanguage, '当前还没有记录到观察项。', 'No observations recorded yet.')}</p>
              ) : (
                workingMemoryView.recentObservations.map((item, index) => (
                  (() => {
                    const observationKey = `${item.section_key || 'observation'}-${index}`
                    const expanded = Boolean(expandedObservationKeys[observationKey])
                    const expandable = needsExpansion(item.summary || '')
                    return (
                      <div key={observationKey} className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3">
                        <div className="flex flex-wrap gap-2">
                          {item.section_key && <Badge variant="outline">{formatSectionKeyLabel(item.section_key, memoryLanguage)}</Badge>}
                          {item.kind && <Badge variant="secondary">{formatObservationKindLabel(item.kind, memoryLanguage)}</Badge>}
                          {formatConfidence(item.confidence) && (
                            <Badge variant="outline">
                              {byLanguage(memoryLanguage, '置信度', 'Confidence')} {formatConfidence(item.confidence)}
                            </Badge>
                          )}
                        </div>
                        <div className="relative">
                          <MemoryRichText
                            content={item.summary || ''}
                            className={cn('mt-2 text-neutral-900', !expanded && expandable && 'max-h-32 overflow-hidden')}
                          />
                          {!expanded && expandable ? (
                            <div className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-neutral-50 to-transparent" />
                          ) : null}
                        </div>
                        {expandable ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="mt-2 h-auto px-0 text-sky-700 hover:bg-transparent hover:text-sky-900"
                            onClick={() => toggleObservationExpanded(observationKey)}
                          >
                            {expanded
                              ? (memoryLanguage === 'zh' ? '收起' : 'Collapse')
                              : (memoryLanguage === 'zh' ? '展开全文' : 'Expand')}
                          </Button>
                        ) : null}
                        {item.evidence_refs?.length ? (
                          <div className="mt-3 flex flex-wrap gap-1.5">
                            {item.evidence_refs.slice(0, 4).map((ref, refIndex) => (
                              <Badge key={`${ref}-${refIndex}`} variant="outline">{clipText(ref, 42)}</Badge>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    )
                  })()
                ))
              )}
            </CardContent>
          </Card>

          <Card className="border-neutral-200 bg-white/95">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">{byLanguage(memoryLanguage, '论断草稿', 'Draft claims')}</CardTitle>
              <CardDescription>
                {byLanguage(
                  memoryLanguage,
                  '这些是还没完全晋升到长期记忆、但已经接近稳定结论的论断草稿。',
                  'Claim drafts that were close to being promoted into long-term memory.',
                )}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {workingMemoryView.draftClaims.length === 0 ? (
                <p className="text-sm text-neutral-500">{byLanguage(memoryLanguage, '这个检查点里还没有草稿论断。', 'No draft claims in this checkpoint.')}</p>
              ) : (
                workingMemoryView.draftClaims.map((item, index) => (
                  <div key={`${item.section_key || 'claim'}-${index}`} className="rounded-2xl border border-neutral-200 bg-white px-4 py-3">
                    <div className="flex flex-wrap gap-2">
                      {item.section_key && <Badge variant="outline">{formatSectionKeyLabel(item.section_key, memoryLanguage)}</Badge>}
                      {item.importance && <Badge variant="secondary">{formatImportanceLabel(item.importance, memoryLanguage)}</Badge>}
                      {formatConfidence(item.confidence) && (
                        <Badge variant="outline">
                          {byLanguage(memoryLanguage, '置信度', 'Confidence')} {formatConfidence(item.confidence)}
                        </Badge>
                      )}
                    </div>
                    <MemoryRichText content={item.claim || ''} className="mt-2 text-neutral-900" />
                    {item.evidence_refs?.length ? (
                      <p className="mt-2 text-xs text-neutral-500">
                        {byLanguage(memoryLanguage, '证据', 'Evidence')}: {item.evidence_refs.slice(0, 4).join(' · ')}
                      </p>
                    ) : null}
                  </div>
                ))
              )}
            </CardContent>
          </Card>

          <Card className="border-neutral-200 bg-white/95">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">{byLanguage(memoryLanguage, '未决问题与信息缺口', 'Open questions and unresolved gaps')}</CardTitle>
              <CardDescription>
                {byLanguage(
                  memoryLanguage,
                  '这里反映的是 agent 在写作过程中暂时没有完全确认、需要谨慎处理的部分。',
                  'Gaps or uncertainties the agent had not fully resolved while writing.',
                )}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {workingMemoryView.openQuestions.length === 0 ? (
                <p className="text-sm text-neutral-500">{byLanguage(memoryLanguage, '当前保存的检查点里没有未决问题。', 'No open questions remain in the saved checkpoint.')}</p>
              ) : (
                workingMemoryView.openQuestions.map((item, index) => (
                  <div key={`${item.section_key || 'question'}-${index}`} className="rounded-2xl border border-amber-200 bg-amber-50/80 px-4 py-3">
                    <div className="flex flex-wrap gap-2">
                      {item.section_key && <Badge variant="outline">{formatSectionKeyLabel(item.section_key, memoryLanguage)}</Badge>}
                      <Badge variant="secondary">{formatPromotionStatusLabel(item.status || 'open', memoryLanguage)}</Badge>
                    </div>
                    <MemoryRichText content={item.question || ''} className="mt-2 font-medium text-neutral-950" />
                    {item.reason && <MemoryRichText content={item.reason} className="mt-1 text-neutral-700" />}
                  </div>
                ))
              )}
              {workingMemoryView.resolvedQuestions.length > 0 ? (
                <div className="rounded-2xl border border-emerald-200 bg-emerald-50/70 px-4 py-3">
                  <p className="text-xs font-medium uppercase tracking-[0.16em] text-emerald-700">
                    {byLanguage(memoryLanguage, '最近已解决', 'Recently resolved')}
                  </p>
                  <div className="mt-2 space-y-2">
                    {workingMemoryView.resolvedQuestions.map((item, index) => (
                      <p key={`${item.section_key || 'resolved'}-${index}`} className="text-sm text-emerald-900">
                        {clipText(item.question || '', 120)}
                        {item.resolution_note ? `: ${clipText(item.resolution_note, 120)}` : ''}
                      </p>
                    ))}
                  </div>
                </div>
              ) : null}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <Card className="border-sky-200 bg-sky-50/70">
            <CardHeader className="pb-2">
              <CardTitle className="text-base text-sky-950">{byLanguage(memoryLanguage, '记忆晋升漏斗', 'Promotion funnel')}</CardTitle>
              <CardDescription className="text-sky-700/70">
                {byLanguage(
                  memoryLanguage,
                  '短期记忆里的候选知识如何被筛选、接受或暂缓写回长期记忆。',
                  'How working-memory candidates were accepted, held for review, or rejected.',
                )}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  className={promotionFilter === 'all'
                    ? 'border border-sky-300 bg-white text-sky-900 hover:bg-sky-50 hover:text-sky-900'
                    : 'border border-sky-200 bg-sky-50/50 text-sky-700 hover:bg-sky-100/60 hover:text-sky-800'}
                  onClick={() => setPromotionFilter('all')}
                >
                  {memoryLanguage === 'zh' ? '全部' : 'All'} {formatMetricValue(workingMemoryView.promotionCandidates.length)}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className={promotionFilter === 'accepted'
                    ? 'border border-sky-300 bg-white text-sky-900 hover:bg-sky-50 hover:text-sky-900'
                    : 'border border-sky-200 bg-sky-50/50 text-sky-700 hover:bg-sky-100/60 hover:text-sky-800'}
                  onClick={() => setPromotionFilter('accepted')}
                >
                  {memoryLanguage === 'zh' ? '已接受' : 'Accepted'} {formatMetricValue(workingMemoryView.promotionCounts.accepted)}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className={promotionFilter === 'review_required'
                    ? 'border border-sky-300 bg-white text-sky-900 hover:bg-sky-50 hover:text-sky-900'
                    : 'border border-sky-200 bg-sky-50/50 text-sky-700 hover:bg-sky-100/60 hover:text-sky-800'}
                  onClick={() => setPromotionFilter('review_required')}
                >
                  {memoryLanguage === 'zh' ? '待复核' : 'Review'} {formatMetricValue(workingMemoryView.promotionCounts.review_required)}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className={promotionFilter === 'rejected'
                    ? 'border border-sky-300 bg-white text-sky-900 hover:bg-sky-50 hover:text-sky-900'
                    : 'border border-sky-200 bg-sky-50/50 text-sky-700 hover:bg-sky-100/60 hover:text-sky-800'}
                  onClick={() => setPromotionFilter('rejected')}
                >
                  {memoryLanguage === 'zh' ? '已拒绝' : 'Rejected'} {formatMetricValue(workingMemoryView.promotionCounts.rejected)}
                </Button>
              </div>
              {visiblePromotionCandidates.length === 0 ? (
                <p className="text-sm text-sky-600">
                  {memoryLanguage === 'zh' ? '当前筛选下没有候选项。' : 'No promotion candidates for this filter.'}
                </p>
              ) : (
                visiblePromotionCandidates.map((item, index) => (
                  <div key={`${item.candidate_type || 'candidate'}-${index}`} className="rounded-2xl border border-sky-200/80 bg-white/80 px-4 py-3">
                    <div className="flex flex-wrap gap-2">
                      {item.candidate_type && <Badge variant="secondary">{formatPromotionTypeLabel(item.candidate_type, memoryLanguage)}</Badge>}
                      {item.status && <Badge variant="outline" className="border-sky-200 text-sky-800">{formatPromotionStatusLabel(item.status, memoryLanguage)}</Badge>}
                      {formatConfidence(item.confidence) && (
                        <Badge variant="outline" className="border-sky-200 text-sky-800">
                          {byLanguage(memoryLanguage, '置信度', 'Confidence')} {formatConfidence(item.confidence)}
                        </Badge>
                      )}
                    </div>
                    <MemoryRichText content={extractPromotionText(item.payload)} className="mt-2 text-neutral-800" />
                    {item.source_section ? (
                      <p className="mt-2 text-xs text-sky-600">
                        {memoryLanguage === 'zh' ? '来源' : 'Source'}: {formatSectionKeyLabel(item.source_section, memoryLanguage)}
                      </p>
                    ) : null}
                  </div>
                ))
              )}
            </CardContent>
          </Card>

          <Card className="border-neutral-200 bg-white/95">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">{byLanguage(memoryLanguage, '术语映射', 'Terminology map')}</CardTitle>
              <CardDescription>{byLanguage(memoryLanguage, '当前任务里最值得固定表达的概念词与解释。', 'Terminology and phrasing hints captured during this run.')}</CardDescription>
            </CardHeader>
            <CardContent>
              {workingMemoryView.terminologyEntries.length === 0 ? (
                <p className="text-sm text-neutral-500">{byLanguage(memoryLanguage, '当前没有术语提示。', 'No terminology hints saved.')}</p>
              ) : (
                <div className="space-y-2">
                  {workingMemoryView.terminologyEntries.map(([term, description]) => (
                    <div key={term} className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3">
                      <p className="text-sm font-medium text-neutral-950">{term}</p>
                      <MemoryRichText content={description} className="mt-1 text-neutral-600" />
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="border-neutral-200 bg-white/95">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">{byLanguage(memoryLanguage, '检索与写回预算', 'Retrieval and writeback budget')}</CardTitle>
              <CardDescription>{byLanguage(memoryLanguage, '长期记忆检索进来了多少上下文，以及最终写回时用了多少提示预算。', 'How much long-term context was retrieved and how much prompt budget the final writeback consumed.')}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 text-sm text-neutral-700">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3">
                  <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-neutral-500">{byLanguage(memoryLanguage, '已检索上下文', 'Retrieved Context')}</p>
                  <p className="mt-2">{byLanguage(memoryLanguage, 'Claims', 'Claims')} {formatMetricValue(workingMemoryView.retrievedClaimCount)}</p>
                  <p>{byLanguage(memoryLanguage, 'Evidence', 'Evidence')} {formatMetricValue(workingMemoryView.retrievedEvidenceCount)}</p>
                  <p>{byLanguage(memoryLanguage, '翻译提示', 'Translation hints')} {formatMetricValue(workingMemoryView.translationHintCount)}</p>
                </div>
                <div className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3">
                  <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-neutral-500">{byLanguage(memoryLanguage, '提示预算', 'Prompt Budget')}</p>
                  <p className="mt-2">{byLanguage(memoryLanguage, '抽取提示', 'Extraction prompt')} {formatMetricValue(workingMemoryView.promptChars)}</p>
                  <p>{byLanguage(memoryLanguage, '摘要片段', 'Summary slice')} {formatMetricValue(workingMemoryView.summaryChars)}</p>
                  <p>{byLanguage(memoryLanguage, '复核片段', 'Review slice')} {formatMetricValue(workingMemoryView.reviewChars)}</p>
                </div>
              </div>
              <div className="rounded-2xl border border-dashed border-neutral-300 px-4 py-3">
                <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-neutral-500">{byLanguage(memoryLanguage, '晋升蒸馏', 'Promotion Distillation')}</p>
                <p className="mt-2">
                  {byLanguage(memoryLanguage, '选中候选', 'Selected candidates')} {formatMetricValue(workingMemoryView.selectedCandidateCount)} / {byLanguage(memoryLanguage, '原始候选', 'raw candidates')} {formatMetricValue(workingMemoryView.originalCandidateCount)}
                </p>
                <p className="mt-1 text-neutral-500">
                  {byLanguage(memoryLanguage, '解释器上下文包', 'Interpreter bundle')}: {byLanguage(memoryLanguage, 'claims', 'claims')} {formatMetricValue(asArray(workingMemoryView.interpreterBundle?.priority_claims).length)} · {byLanguage(memoryLanguage, 'evidence', 'evidence')} {formatMetricValue(asArray(workingMemoryView.interpreterBundle?.relevant_evidence).length)} · {byLanguage(memoryLanguage, 'conflicts', 'conflicts')} {formatMetricValue(asArray(workingMemoryView.interpreterBundle?.active_conflicts).length)}
                </p>
                <p className="mt-1 text-neutral-500">
                  {byLanguage(memoryLanguage, '相关论文', 'Related papers')} {formatMetricValue(asArray(workingMemoryView.interpreterBundle?.related_papers).length)} · {byLanguage(memoryLanguage, '翻译上下文提示', 'translation bundle hints')} {formatMetricValue(asArray(workingMemoryView.translationBundle?.terminology_hints).length)}
                </p>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )

  const renderDistilledSummaryPanel = () => (
    <div className="space-y-5">
      <SectionHeader
        icon={Sparkles}
        title={byLanguage(memoryLanguage, '蒸馏记忆摘要', 'Distilled Memory Summary')}
        description={byLanguage(
          memoryLanguage,
          '这是从短期记忆里压缩出来的可复用结论层，用来帮助后续长期记忆沉淀和跨论文迁移。',
          'This is the condensed conclusion layer distilled from working memory for later reuse and promotion.',
        )}
      />
      {distilledSummaryError && distilledSummary.trim() ? (
        <Card className="border-amber-200 bg-amber-50">
          <CardContent className="py-4 text-sm text-amber-800">
            {memoryLanguage === 'zh'
              ? '中文预翻译暂时不可用，当前先回退显示英文原文。'
              : distilledSummaryError}
          </CardContent>
        </Card>
      ) : null}
      {distilledSummaryError && !distilledSummary.trim() ? (
        <Card className="border-red-200 bg-red-50">
          <CardContent className="py-4 text-sm text-red-700">{distilledSummaryError}</CardContent>
        </Card>
      ) : !distilledSummary.trim() ? (
        <Card>
          <CardContent className="py-4 text-sm text-neutral-500">
            {byLanguage(memoryLanguage, '这个任务还没有保存蒸馏摘要。', 'No distilled summary saved for this job yet.')}
          </CardContent>
        </Card>
      ) : (
        <Card className="border-neutral-200 bg-white/95">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">{byLanguage(memoryLanguage, '内联摘要预览', 'Inline summary preview')}</CardTitle>
            <CardDescription>{byLanguage(memoryLanguage, '不需要再打开单独的 Markdown artifact，这里直接可读。', 'Readable directly here without opening a separate Markdown artifact.')}</CardDescription>
          </CardHeader>
          <CardContent>
            <article className="report-markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                {distilledSummary}
              </ReactMarkdown>
            </article>
          </CardContent>
        </Card>
      )}
    </div>
  )

  const renderSelectorPanel = () => (
    <div className="space-y-5">
      <SectionHeader
        icon={SearchCheck}
        title={byLanguage(memoryLanguage, '论文筛选诊断', 'Selector Diagnostics')}
        description={byLanguage(
          memoryLanguage,
          '展示 profile memory 如何参与选题、重排候选论文、以及最终为什么是这篇论文进入正式解读。',
          'Shows how profile memory influenced search, reranking, and the final paper choice.',
        )}
      />

      <MetricStrip
        items={[
          { label: byLanguage(memoryLanguage, '候选论文', 'Candidates'), value: formatMetricValue(selectorView.candidateCount) },
          { label: byLanguage(memoryLanguage, '重排后', 'Ranked'), value: formatMetricValue(selectorView.rankedCount) },
          { label: byLanguage(memoryLanguage, '记忆字符数', 'Memory Chars'), value: formatMetricValue(selectorView.selectionMemoryChars) },
          { label: byLanguage(memoryLanguage, 'Bundle 关键词', 'Bundle Keywords'), value: formatMetricValue(selectorView.keywords.length) },
        ]}
      />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(320px,0.95fr)]">
        <div className="space-y-4">
          <Card className="border-neutral-200 bg-white/95">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">{byLanguage(memoryLanguage, '最终入选论文', 'Selected paper')}</CardTitle>
              <CardDescription>{byLanguage(memoryLanguage, '最终进入主流程的论文，以及它对应的 track 与来源。', 'The paper that entered the pipeline, along with its track and source.')}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-4">
                <p className="text-lg font-semibold leading-7 text-neutral-950">{selectorView.selectedTitle}</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Badge variant="secondary">{selectorView.selectedTrack}</Badge>
                  <Badge variant="outline">{selectorView.selectedSource}</Badge>
                  {selectorView.selectedPaperTopicAudit?.topic_fit_score !== undefined && (
                    <Badge variant="outline">
                      {byLanguage(memoryLanguage, '主题匹配', 'Topic fit')} {Number(selectorView.selectedPaperTopicAudit.topic_fit_score).toFixed(2)}
                    </Badge>
                  )}
                </div>
              </div>
              {selectorView.failureReason ? (
                <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                  {selectorView.failureReason}
                </div>
              ) : null}
              {selectorView.selectedPaperTopicAudit?.mismatch_reasons?.length ? (
                <div className="rounded-2xl border border-neutral-200 bg-white px-4 py-3">
                  <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-neutral-500">
                    {byLanguage(memoryLanguage, '下载后复核', 'Post-download audit')}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {selectorView.selectedPaperTopicAudit.mismatch_reasons.map((item, index) => (
                      <Badge key={`selected-audit-${index}`} variant="outline">{item}</Badge>
                    ))}
                  </div>
                </div>
              ) : null}
              {selectorView.memoryExcerpt ? (
                <div className="rounded-2xl border border-dashed border-neutral-300 px-4 py-3">
                  <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-neutral-500">{byLanguage(memoryLanguage, '筛选记忆摘录', 'Selection memory excerpt')}</p>
                  <p className="mt-2 text-sm leading-6 text-neutral-700">{selectorView.memoryExcerpt}</p>
                </div>
              ) : null}
            </CardContent>
          </Card>

          <Card className="border-neutral-200 bg-white/95">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">{byLanguage(memoryLanguage, '高优先级候选', 'Top ranked candidates')}</CardTitle>
              <CardDescription>{byLanguage(memoryLanguage, '重排阶段留下来的高优先级备选项，方便判断 selector 是否选得合理。', 'High-priority alternatives kept after reranking so you can audit the selector decision.')}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {selectorView.rankedCandidates.length === 0 ? (
                <p className="text-sm text-neutral-500">{byLanguage(memoryLanguage, '当前没有可预览的候选论文。', 'No ranked candidate preview available.')}</p>
              ) : (
                selectorView.rankedCandidates.map((item, index) => (
                  <div key={item.key} className="rounded-2xl border border-neutral-200 bg-white px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="secondary">#{index + 1}</Badge>
                      {item.track && <Badge variant="outline">{item.track}</Badge>}
                      {item.venue && <Badge variant="outline">{item.venue}</Badge>}
                      {item.year && <Badge variant="outline">{item.year}</Badge>}
                      {item.score !== null && <Badge variant="outline">{byLanguage(memoryLanguage, '评分', 'Score')} {item.score.toFixed(3)}</Badge>}
                    </div>
                    <p className="mt-2 text-sm font-medium leading-6 text-neutral-950">{item.title}</p>
                    {item.reason ? <p className="mt-1 text-sm leading-6 text-neutral-600">{clipText(item.reason, 180)}</p> : null}
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <Card className="border-sky-200 bg-sky-50/70">
            <CardHeader className="pb-2">
              <CardTitle className="text-base text-sky-950">{byLanguage(memoryLanguage, '记忆包贡献', 'Memory bundle contribution')}</CardTitle>
              <CardDescription className="text-sky-700/70">
                {byLanguage(memoryLanguage, '这是 profile memory 给 selector 提供的高层摘要、重点 claims 和相关论文线索。', 'High-level digest, priority claims, and related-paper cues provided by profile memory.')}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl border border-sky-200/80 bg-white/80 px-4 py-3">
                  <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-sky-600">{byLanguage(memoryLanguage, '摘要条目', 'Digest')}</p>
                  <p className="mt-2 text-2xl font-semibold text-sky-950">{formatMetricValue(selectorView.digest.length)}</p>
                </div>
                <div className="rounded-2xl border border-sky-200/80 bg-white/80 px-4 py-3">
                  <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-sky-600">{byLanguage(memoryLanguage, '重点 Claims', 'Priority Claims')}</p>
                  <p className="mt-2 text-2xl font-semibold text-sky-950">{formatMetricValue(selectorView.priorityClaims.length)}</p>
                </div>
                <div className="rounded-2xl border border-sky-200/80 bg-white/80 px-4 py-3">
                  <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-sky-600">{byLanguage(memoryLanguage, '相关论文', 'Related Papers')}</p>
                  <p className="mt-2 text-2xl font-semibold text-sky-950">{formatMetricValue(selectorView.relatedPapers.length)}</p>
                </div>
                <div className="rounded-2xl border border-sky-200/80 bg-white/80 px-4 py-3">
                  <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-sky-600">{byLanguage(memoryLanguage, '关键词', 'Keywords')}</p>
                  <p className="mt-2 text-2xl font-semibold text-sky-950">{formatMetricValue(selectorView.keywords.length)}</p>
                </div>
              </div>
              {selectorView.keywords.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {selectorView.keywords.slice(0, 12).map((item, index) => (
                    <Badge key={`keyword-${index}`} variant="outline" className="border-sky-200 text-sky-800">
                      {clipText(typeof item === 'string' ? item : JSON.stringify(item), 40)}
                    </Badge>
                  ))}
                </div>
              ) : null}
            </CardContent>
          </Card>

          <Card className="border-neutral-200 bg-white/95">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">{byLanguage(memoryLanguage, '记忆包预览', 'Bundle preview')}</CardTitle>
              <CardDescription>{byLanguage(memoryLanguage, '帮助快速判断 selector 用到的记忆内容是否有代表性。', 'Helps you quickly judge whether the retrieved memory bundle is representative.')}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div>
                <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-neutral-500">{byLanguage(memoryLanguage, '高层摘要', 'High-level digest')}</p>
                <div className="mt-2 space-y-2">
                  {selectorView.digest.slice(0, 3).map((item, index) => (
                    <p key={`digest-${index}`} className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm leading-6 text-neutral-700">
                      {clipText(typeof item === 'string' ? item : JSON.stringify(item), 200)}
                    </p>
                  ))}
                  {selectorView.digest.length === 0 && <p className="text-sm text-neutral-500">{byLanguage(memoryLanguage, '没有摘要条目。', 'No digest entries.')}</p>}
                </div>
              </div>
              <div>
                <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-neutral-500">{byLanguage(memoryLanguage, '重点 Claims', 'Priority claims')}</p>
                <div className="mt-2 space-y-2">
                  {selectorView.priorityClaims.slice(0, 3).map((item, index) => (
                    <p key={`claim-${index}`} className="rounded-2xl border border-neutral-200 bg-white px-4 py-3 text-sm leading-6 text-neutral-700">
                      {clipText(typeof item === 'string' ? item : JSON.stringify(item), 200)}
                    </p>
                  ))}
                  {selectorView.priorityClaims.length === 0 && <p className="text-sm text-neutral-500">{byLanguage(memoryLanguage, '这个记忆包里没有重点 claims。', 'No priority claims in the saved bundle.')}</p>}
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )

  const renderAuditPanel = () => (
    <div className="space-y-5">
      <SectionHeader
        icon={ShieldCheck}
        title={byLanguage(memoryLanguage, '报告审查', 'Report Audit')}
        description={byLanguage(
          memoryLanguage,
          '独立检查结构化 claims 的证据锚点，以及 plain-text 部分是否出现脱离证据的数字或结论。',
          'Independent audit for evidence anchors in structured claims and risky plain-text conclusions.',
        )}
      />

      <MetricStrip
        items={[
          { label: byLanguage(memoryLanguage, '高风险', 'High'), value: formatMetricValue(reportAuditView.high) },
          { label: byLanguage(memoryLanguage, '中风险', 'Medium'), value: formatMetricValue(reportAuditView.medium) },
          { label: byLanguage(memoryLanguage, '低风险', 'Low'), value: formatMetricValue(reportAuditView.low) },
          { label: byLanguage(memoryLanguage, '自动修复', 'Auto repaired'), value: reportAuditView.repaired ? byLanguage(memoryLanguage, '是', 'Yes') : byLanguage(memoryLanguage, '否', 'No') },
        ]}
      />

      <Card className="border-neutral-200 bg-white/95">
        <CardHeader className="pb-2">
          <CardTitle className="text-base">{byLanguage(memoryLanguage, '审查结果', 'Audit result')}</CardTitle>
          <CardDescription>
            {reportAuditView.warning
              ? byLanguage(memoryLanguage, '存在需要你留意的问题，报告已尽量保守处理。', 'The audit found issues worth reviewing, and the report was kept conservative where possible.')
              : byLanguage(memoryLanguage, '当前没有发现需要报警的问题。', 'No warning-level issues were found in the current audit.')}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <Badge variant={reportAuditView.warning ? 'destructive' : 'secondary'}>{reportAuditView.status}</Badge>
            {reportAuditView.repaired ? <Badge variant="outline">{byLanguage(memoryLanguage, '已做自动修复', 'Auto repair applied')}</Badge> : null}
          </div>
          {reportAuditView.issues.length === 0 ? (
            <p className="text-sm text-neutral-500">{byLanguage(memoryLanguage, '没有额外审查项。', 'No audit issues were recorded.')}</p>
          ) : (
            reportAuditView.issues.map((item, index) => (
              <div key={`audit-issue-${index}`} className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3">
                <div className="flex flex-wrap gap-2">
                  <Badge variant={item.severity === 'high' ? 'destructive' : item.severity === 'medium' ? 'secondary' : 'outline'}>
                    {item.severity || 'unknown'}
                  </Badge>
                  {item.section_key ? <Badge variant="outline">{item.section_key}</Badge> : null}
                  {item.issue_type ? <Badge variant="outline">{item.issue_type}</Badge> : null}
                </div>
                {item.claim ? <p className="mt-2 text-sm font-medium text-neutral-950">{clipText(item.claim, 220)}</p> : null}
                {item.reason ? <p className="mt-1 text-sm leading-6 text-neutral-700">{item.reason}</p> : null}
                {item.repair_action ? <p className="mt-1 text-sm leading-6 text-neutral-600">{item.repair_action}</p> : null}
                {item.evidence_refs?.length ? (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {item.evidence_refs.map((ref, refIndex) => (
                      <Badge key={`audit-ref-${index}-${refIndex}`} variant="outline">{clipText(ref, 48)}</Badge>
                    ))}
                  </div>
                ) : null}
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  )

  const renderWorkspacePanel = () => {
    switch (activePanel) {
      case 'audit':
        return renderAuditPanel()
      case 'distilled-summary':
        return renderDistilledSummaryPanel()
      case 'selector':
        return renderSelectorPanel()
      case 'working-memory':
      default:
        return renderWorkingMemoryPanel()
    }
  }

  return (
    <div className="report-viewer-page -m-6 min-h-screen bg-white py-6 text-black">
      <div className="mx-auto max-w-7xl space-y-6 px-8">
        <div className="report-viewer-toolbar flex flex-wrap items-center justify-between gap-3">
          <Link to="/reports">
            <Button variant="ghost" size="sm" className="text-black hover:bg-black/5 hover:text-black">
              <ArrowLeft className="mr-1 h-4 w-4" />
              {byLanguage(memoryLanguage, '返回', 'Back')}
            </Button>
          </Link>
          <div className="flex flex-wrap items-center gap-2">
            {report?.profile_id ? (
              <Link to={`/profiles/${report.profile_id}/workspace`}>
                <Button
                  variant="outline"
                  size="sm"
                  className="border-sky-200 bg-white/90 text-sky-900 hover:bg-sky-50 hover:text-sky-950"
                >
                  <Network className="mr-1 h-4 w-4" />
                  {byLanguage(memoryLanguage, '打开图谱工作台', 'Open Graph Workspace')}
                </Button>
              </Link>
            ) : null}
            {jobId && (
              <Button
                variant="outline"
                size="sm"
                className="border-black/15 bg-white text-black hover:bg-black/5 hover:text-black"
                onClick={handleRerun}
                disabled={isRerunning}
              >
                <RotateCcw className="mr-1 h-4 w-4" />
                {isRerunning
                  ? byLanguage(memoryLanguage, '启动中...', 'Starting...')
                  : byLanguage(memoryLanguage, '重新生成', 'Regenerate')}
              </Button>
            )}
            <Button variant="outline" size="sm" className="border-black/15 bg-white text-black hover:bg-black/5 hover:text-black" onClick={handleExportPdf}>
              <Download className="mr-1 h-4 w-4" />
              {byLanguage(memoryLanguage, '导出 PDF', 'Export PDF')}
            </Button>
          </div>
        </div>

        {rerunJobId && (
          <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
            <span>
              {byLanguage(
                memoryLanguage,
                `已启动替换式重跑任务：${rerunJobId}。新任务成功后，这份旧报告会被自动移除。`,
                `Started a replacement regeneration job: ${rerunJobId}. When it succeeds, this older report will be removed automatically.`,
              )}
            </span>
            <Button
              variant="outline"
              size="sm"
              className="border-emerald-300 bg-white text-emerald-900 hover:bg-emerald-100"
              onClick={() => navigate('/run')}
            >
              {byLanguage(memoryLanguage, '打开运行页', 'Open Run Page')}
            </Button>
          </div>
        )}

        {rerunError && (
          <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {rerunError}
          </div>
        )}

        {report ? (
          <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-700">
            <Badge variant="outline">
              {report.profile_mode === 'auto'
                ? report.profile_name
                  ? `${report.profile_assignment_status === 'created' ? 'Auto created' : 'Auto matched'}: ${report.profile_name}`
                  : 'Auto assigning...'
                : `Profile: ${report.profile_name || 'Default'}`}
            </Badge>
            {report.profile_assignment_note ? <span>{report.profile_assignment_note}</span> : null}
            {report.has_report_audit ? (
              <Badge variant={reportAuditView.warning ? 'destructive' : 'secondary'}>
                {reportAuditView.warning ? byLanguage(memoryLanguage, '审查有告警', 'Audit warning') : byLanguage(memoryLanguage, '审查通过', 'Audit pass')}
              </Badge>
            ) : null}
          </div>
        ) : null}

        {!routeKey ? (
          <p className="text-sm text-neutral-600">{byLanguage(memoryLanguage, '尚未选择报告。', 'No report selected.')}</p>
        ) : loading ? (
          <p className="text-sm text-neutral-600">{byLanguage(memoryLanguage, '正在加载报告内容...', 'Loading report content...')}</p>
        ) : error ? (
          <div className="space-y-2">
            <p className="text-sm font-medium text-red-600">{byLanguage(memoryLanguage, '无法加载这份报告。', 'Unable to load this report.')}</p>
            <p className="break-words text-sm text-neutral-600">{error}</p>
          </div>
        ) : !content.trim() ? (
          <p className="text-sm text-neutral-600">{byLanguage(memoryLanguage, '这份报告存在，但 Markdown 内容为空。', 'This report exists but its Markdown content is empty.')}</p>
        ) : (
          <div className="space-y-6">
            <div className="space-y-6 report-print-exclude">
              <section className="overflow-hidden rounded-[28px] border border-neutral-200 bg-[linear-gradient(135deg,#f8fbff_0%,#f4f8ff_40%,#ffffff_100%)] p-6 shadow-sm">
                <div className="grid gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(320px,0.9fr)]">
                  <div className="space-y-4">
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="secondary">Job {report?.job_id || jobId}</Badge>
                      {report?.has_working_memory && <Badge variant="outline">{byLanguage(memoryLanguage, '短期记忆', 'Working Memory')}</Badge>}
                      {report?.has_distilled_memory_summary && <Badge variant="outline">{byLanguage(memoryLanguage, '蒸馏摘要', 'Distilled Summary')}</Badge>}
                      {report?.has_selector_diagnostics && <Badge variant="outline">{byLanguage(memoryLanguage, '筛选诊断', 'Selector Diagnostics')}</Badge>}
                    </div>
                    <div className="space-y-2">
                      <h1 className="text-3xl font-semibold tracking-tight text-neutral-950">
                        {report?.paper_title || report?.title || 'Paper Report'}
                      </h1>
                      <p className="max-w-3xl text-sm leading-6 text-neutral-600">
                        {byLanguage(
                          memoryLanguage,
                          '这里不只是最终 Markdown 报告，还包括解释器在写作时的短期记忆快照、蒸馏后的稳定结论，以及 selector 如何利用长期记忆做论文筛选。',
                          'This page includes not only the final Markdown report, but also the interpreter’s working-memory snapshot, distilled conclusions, and selector diagnostics.',
                        )}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-3 text-sm text-neutral-600">
                      <span>{byLanguage(memoryLanguage, '更新时间', 'Updated')} {formatDateTime(report?.modified_at)}</span>
                      <span>{byLanguage(memoryLanguage, '大小', 'Size')} {report?.size_bytes ? `${Math.round(report.size_bytes / 1024)} KB` : byLanguage(memoryLanguage, '不可用', 'Unavailable')}</span>
                    </div>
                  </div>

                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
                    <div className="rounded-3xl border border-neutral-200 bg-white/90 p-4 shadow-sm">
                      <div className="flex items-center gap-3">
                        <div className="rounded-2xl bg-sky-100 p-2 text-sky-700">
                          <Brain className="h-4 w-4" />
                        </div>
                        <div>
                          <p className="text-sm font-medium text-neutral-950">{byLanguage(memoryLanguage, '解释器记忆', 'Interpreter memory')}</p>
                          <p className="text-xs text-neutral-500">
                            {workingMemoryArtifact ? formatStageLabel(workingMemoryArtifact.artifact_stage, memoryLanguage) : byLanguage(memoryLanguage, '还没有快照', 'No snapshot saved')}
                          </p>
                        </div>
                      </div>
                      <div className="mt-4 flex flex-wrap gap-2">
                        <Badge variant="secondary">{byLanguage(memoryLanguage, '观察', 'Obs')} {formatMetricValue(workingMemoryView.observationCount)}</Badge>
                        <Badge variant="outline">{byLanguage(memoryLanguage, 'Claims', 'Claims')} {formatMetricValue(workingMemoryView.draftClaimCount)}</Badge>
                        <Badge variant="outline">{byLanguage(memoryLanguage, '问题', 'Questions')} {formatMetricValue(workingMemoryView.openQuestionCount)}</Badge>
                      </div>
                    </div>

                    <div className="rounded-3xl border border-neutral-200 bg-white/90 p-4 shadow-sm">
                      <div className="flex items-center gap-3">
                        <div className="rounded-2xl bg-sky-100 p-2 text-sky-700">
                          <Network className="h-4 w-4" />
                        </div>
                        <div>
                          <p className="text-sm font-medium text-neutral-950">{byLanguage(memoryLanguage, '检索影响', 'Retrieval impact')}</p>
                          <p className="text-xs text-neutral-500">{byLanguage(memoryLanguage, '长期记忆如何影响了这次运行', 'How long-term memory influenced this run')}</p>
                        </div>
                      </div>
                      <div className="mt-4 space-y-1 text-sm text-neutral-700">
                        <p>{byLanguage(memoryLanguage, '召回 Claims', 'Claims retrieved')}: {formatMetricValue(workingMemoryView.retrievedClaimCount)}</p>
                        <p>{byLanguage(memoryLanguage, '召回 Evidence', 'Evidence retrieved')}: {formatMetricValue(workingMemoryView.retrievedEvidenceCount)}</p>
                        <p>{byLanguage(memoryLanguage, 'Selector 候选', 'Selector candidates')}: {formatMetricValue(selectorView.candidateCount)}</p>
                      </div>
                    </div>
                  </div>
                </div>
              </section>

              <section className="rounded-[28px] border border-neutral-200 bg-white p-6 shadow-sm">
              <div className="grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(320px,0.9fr)]">
                <div className="space-y-4">
                  <div className="space-y-2">
                    <h2 className="text-xl font-semibold tracking-tight text-neutral-950">
                      {byLanguage(memoryLanguage, 'ReAct 报告微调', 'ReAct Report Refinement')}
                    </h2>
                    <p className="max-w-3xl text-sm leading-6 text-neutral-600">
                      {byLanguage(
                        memoryLanguage,
                        '基于当前报告、蒸馏摘要和短期记忆生成一个新的报告变体。适合做结构切换、方法/实验展开、段落压缩和叙事重排。',
                        'Create a new report variant from the current report, distilled summary, and working memory. Useful for structure switches, method/result expansion, compression, and narrative reshaping.',
                      )}
                    </p>
                  </div>

                  <div className="space-y-2">
                    <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-neutral-500">
                      {byLanguage(memoryLanguage, '报告版本', 'Report variants')}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {(report?.variants ?? []).map((variant) => (
                        <Button
                          key={variant.variant_id}
                          variant={variant.variant_id === (report?.variant_id || selectedVariantId) ? 'default' : 'outline'}
                          size="sm"
                          onClick={() => handleSelectVariant(variant)}
                        >
                          {variant.kind === 'original'
                            ? byLanguage(memoryLanguage, '原稿', 'Original')
                            : variant.label}
                        </Button>
                      ))}
                    </div>
                  </div>

                  <label className="space-y-2">
                    <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-neutral-500">
                      {byLanguage(memoryLanguage, '微调指令', 'Refinement instruction')}
                    </span>
                    <textarea
                      className={textareaClass}
                      value={refineInstruction}
                      onChange={(e) => setRefineInstruction(e.target.value)}
                      placeholder={byLanguage(
                        memoryLanguage,
                        '例如：保持事实不变，把方法部分写得更适合 PMRC 叙事；实验部分压缩到只保留关键指标与主要结论。',
                        'Example: keep the facts unchanged, rewrite the method section into a PMRC-friendly narrative, and compress the experiments to only the key metrics and conclusions.',
                      )}
                    />
                  </label>

                  {refineError ? (
                    <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                      {refineError}
                    </div>
                  ) : null}

                  <div className="flex flex-wrap gap-2">
                    <Button
                      onClick={handleRefineReport}
                      disabled={isRefining || !refineInstruction.trim() || !report}
                    >
                      <Sparkles className="mr-1 h-4 w-4" />
                      {isRefining
                        ? byLanguage(memoryLanguage, '生成中...', 'Generating...')
                        : byLanguage(memoryLanguage, '生成微调版本', 'Create refined variant')}
                    </Button>
                    {report?.variant_id !== 'original' ? (
                      <Button
                        variant="outline"
                        onClick={() => setSelectedVariantId('original')}
                        disabled={isRefining}
                      >
                        {byLanguage(memoryLanguage, '回到原稿', 'Back to original')}
                      </Button>
                    ) : null}
                  </div>
                </div>

                <div className="space-y-4">
                  <div className="rounded-3xl border border-neutral-200 bg-neutral-50/80 p-4">
                    <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-neutral-500">
                      {byLanguage(memoryLanguage, '当前版本', 'Current variant')}
                    </p>
                    <div className="mt-3 space-y-2 text-sm text-neutral-700">
                      <p>
                        {byLanguage(memoryLanguage, '标签', 'Label')}: {activeVariant?.kind === 'original'
                          ? byLanguage(memoryLanguage, '原稿', 'Original')
                          : (activeVariant?.label || report?.variant_label || byLanguage(memoryLanguage, '未命名', 'Untitled'))}
                      </p>
                      <p>
                        {byLanguage(memoryLanguage, '结构', 'Structure')}: {formatStructureModeLabel(report?.structure_mode, memoryLanguage)}
                      </p>
                      <p>
                        {byLanguage(memoryLanguage, '细节粒度', 'Detail level')}: {formatDetailLevelLabel(report?.detail_level, memoryLanguage)}
                      </p>
                      {activeVariant?.modified_at ? (
                        <p>
                          {byLanguage(memoryLanguage, '更新时间', 'Updated')}: {formatDateTime(activeVariant.modified_at)}
                        </p>
                      ) : null}
                    </div>
                    {report?.instruction ? (
                      <div className="mt-3 rounded-2xl border border-neutral-200 bg-white px-3 py-3 text-sm text-neutral-700">
                        {report.instruction}
                      </div>
                    ) : null}
                  </div>

                  <div className="rounded-3xl border border-neutral-200 bg-neutral-50/80 p-4">
                    <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-neutral-500">
                      {byLanguage(memoryLanguage, '目标结构', 'Target structure')}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {([
                        { value: 'preserve', labelZh: '维持当前结构', labelEn: 'Preserve current' },
                        { value: 'classic', labelZh: '当前结构', labelEn: 'Current structure' },
                        { value: 'pmrc', labelZh: 'PMRC 叙事', labelEn: 'PMRC narrative' },
                      ] as const).map((option) => (
                        <Button
                          key={option.value}
                          variant={targetStructureMode === option.value ? 'default' : 'outline'}
                          size="sm"
                          onClick={() => setTargetStructureMode(option.value)}
                        >
                          {memoryLanguage === 'zh' ? option.labelZh : option.labelEn}
                        </Button>
                      ))}
                    </div>
                  </div>

                  <div className="rounded-3xl border border-neutral-200 bg-neutral-50/80 p-4">
                    <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-neutral-500">
                      {byLanguage(memoryLanguage, '细节控制', 'Detail control')}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {([
                        { value: 'concise', labelZh: '精简', labelEn: 'Concise' },
                        { value: 'balanced', labelZh: '平衡', labelEn: 'Balanced' },
                        { value: 'detailed', labelZh: '展开', labelEn: 'Detailed' },
                      ] as const).map((option) => (
                        <Button
                          key={option.value}
                          variant={detailLevel === option.value ? 'default' : 'outline'}
                          size="sm"
                          onClick={() => setDetailLevel(option.value)}
                        >
                          {memoryLanguage === 'zh' ? option.labelZh : option.labelEn}
                        </Button>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
              </section>

              {availablePanels.length > 0 ? (
                <section className="space-y-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        className="rounded-lg p-1 text-neutral-500 transition-colors hover:bg-neutral-100 hover:text-neutral-700"
                        onClick={() => setIsWorkspaceCollapsed((v) => !v)}
                        aria-label={isWorkspaceCollapsed ? 'Expand workspace' : 'Collapse workspace'}
                      >
                        {isWorkspaceCollapsed ? <ChevronDown className="h-5 w-5" /> : <ChevronUp className="h-5 w-5" />}
                      </button>
                      <div>
                        <h2 className="text-xl font-semibold tracking-tight text-neutral-950">{byLanguage(memoryLanguage, '分析工作台', 'Analysis Workspace')}</h2>
                        <p className="text-sm text-neutral-600">{byLanguage(memoryLanguage, '把运行过程中产生的记忆与诊断信息组织成可阅读视图。', 'Readable views for memory and diagnostics produced during the run.')}</p>
                      </div>
                    </div>
                    {!isWorkspaceCollapsed && (
                      <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-neutral-200 bg-white/90 p-1 shadow-sm">
                        <Button
                          variant={memoryLanguage === 'zh' ? 'default' : 'ghost'}
                          size="sm"
                          onClick={() => setMemoryLanguage('zh')}
                        >
                          中文
                        </Button>
                        <Button
                          variant={memoryLanguage === 'en' ? 'default' : 'ghost'}
                          size="sm"
                          onClick={() => setMemoryLanguage('en')}
                        >
                          EN
                        </Button>
                        {availablePanels.includes('working-memory') && (
                          <Button
                            variant={activePanel === 'working-memory' ? 'default' : 'ghost'}
                            size="sm"
                            onClick={() => setActivePanel('working-memory')}
                          >
                            <Brain className="mr-1 h-4 w-4" />
                            {byLanguage(memoryLanguage, '短期记忆', 'Working Memory')}
                          </Button>
                        )}
                        {availablePanels.includes('distilled-summary') && (
                          <Button
                            variant={activePanel === 'distilled-summary' ? 'default' : 'ghost'}
                            size="sm"
                            onClick={() => setActivePanel('distilled-summary')}
                          >
                            <Sparkles className="mr-1 h-4 w-4" />
                            {byLanguage(memoryLanguage, '蒸馏摘要', 'Distilled Summary')}
                          </Button>
                        )}
                        {availablePanels.includes('selector') && (
                          <Button
                            variant={activePanel === 'selector' ? 'default' : 'ghost'}
                            size="sm"
                            onClick={() => setActivePanel('selector')}
                          >
                            <SearchCheck className="mr-1 h-4 w-4" />
                            {byLanguage(memoryLanguage, '筛选诊断', 'Selector')}
                          </Button>
                        )}
                        {availablePanels.includes('audit') && (
                          <Button
                            variant={activePanel === 'audit' ? 'default' : 'ghost'}
                            size="sm"
                            onClick={() => setActivePanel('audit')}
                          >
                            <ShieldCheck className="mr-1 h-4 w-4" />
                            {byLanguage(memoryLanguage, '报告审查', 'Audit')}
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                  {!isWorkspaceCollapsed && (
                    <>
                      {memoryLanguage === 'zh' && isLocalizedWorkingMemoryLoading ? (
                        <p className="text-xs text-neutral-500">正在使用 flash-lite 翻译短期记忆...</p>
                      ) : null}
                      {memoryLanguage === 'zh' && localizedWorkingMemoryError ? (
                        <p className="text-xs text-amber-700">
                          {byLanguage(memoryLanguage, '中文翻译暂时不可用，当前先回退显示英文原文。', 'Chinese localization is unavailable, so the page is temporarily falling back to English.')}
                        </p>
                      ) : null}

                      <Card className="border-neutral-200 bg-white/80 backdrop-blur-sm">
                        <CardContent className="pt-4">
                          {renderWorkspacePanel()}
                        </CardContent>
                      </Card>

                      <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-neutral-200 bg-white/80 px-4 py-3 text-sm text-neutral-600">
                        <Files className="h-4 w-4" />
                        <span>{byLanguage(memoryLanguage, '原始 artifacts 仍然保留用于调试，但上面已经是主要阅读视图。', 'Raw artifacts remain available for debugging, but the primary view is rendered above.')}</span>
                        {report?.has_working_memory && (
                          <Button variant="outline" size="sm" onClick={() => handleOpenArtifact('working-memory')}>
                            <FileJson className="mr-1 h-4 w-4" />
                            {byLanguage(memoryLanguage, '原始短期记忆', 'Raw working memory')}
                          </Button>
                        )}
                        {report?.has_distilled_memory_summary && (
                          <Button variant="outline" size="sm" onClick={() => handleOpenArtifact('distilled-memory-summary')}>
                            <FileSearch className="mr-1 h-4 w-4" />
                            {byLanguage(memoryLanguage, '原始蒸馏摘要', 'Raw distilled summary')}
                          </Button>
                        )}
                        {report?.has_selector_diagnostics && (
                          <Button variant="outline" size="sm" onClick={() => handleOpenArtifact('selector-diagnostics')}>
                            <SearchCheck className="mr-1 h-4 w-4" />
                            {byLanguage(memoryLanguage, '原始筛选诊断', 'Raw selector diagnostics')}
                          </Button>
                        )}
                        {report?.has_report_audit && (
                          <Button variant="outline" size="sm" onClick={() => handleOpenArtifact('report-audit')}>
                            <ShieldCheck className="mr-1 h-4 w-4" />
                            {byLanguage(memoryLanguage, '原始报告审查', 'Raw report audit')}
                          </Button>
                        )}
                      </div>
                    </>
                  )}
                </section>
              ) : (
                <Card className="border-dashed border-neutral-300 bg-white/80">
                  <CardContent className="flex items-center gap-3 py-5 text-sm text-neutral-600">
                    <HelpCircle className="h-4 w-4" />
                    {byLanguage(memoryLanguage, '这个任务没有保存记忆 artifacts，因此下面只显示最终 Markdown 报告。', 'No memory artifacts were saved for this job, so only the final Markdown report is available below.')}
                  </CardContent>
                </Card>
              )}
            </div>

            <section className="report-export-section">
              <article className={cn('report-markdown rounded-[28px] border border-neutral-200 bg-white px-6 py-6 shadow-sm')}>
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                  {processedContent}
                </ReactMarkdown>
              </article>
            </section>
          </div>
        )}
      </div>
    </div>
  )
}
