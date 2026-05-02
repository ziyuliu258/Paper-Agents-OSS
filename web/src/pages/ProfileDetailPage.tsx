import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { AlertTriangle, ArrowLeft, ArrowRightLeft, FileText, Library, Trash2, Brain, PanelRightOpen, RefreshCw, Languages, CheckCircle2, Swords, TrendingUp, HelpCircle, Sparkles, Plus, ShieldCheck, Zap } from 'lucide-react'
import {
  deleteProfileJobMemory,
  deleteProfilePaperMemory,
  getPaperPdfUrl,
  getProfileDetail,
  getProfiles,
  rebuildProfileMemory,
  moveProfilePapers,
  type ProfileActivityItem,
  type ProfileDetail,
  type ProfileBrief,
  type Profile,
  type OpportunityItem,
} from '@/api/client'
import LocalizedTextBlock from '@/components/LocalizedTextBlock'
import LocalizedTextLanguageProvider from '@/components/LocalizedTextLanguageProvider'
import { resolveLocalizedText } from '@/lib/localizedText'
import DeleteProfileDialog from '@/components/DeleteProfileDialog'
import type { LocalizedContentLanguage } from '@/lib/localizedText'
import { formatDate } from '@/lib/formatters'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import InfoHint from '@/components/ui/info-hint'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

const DELETE_CONFIRM_SECONDS = 5
type DeleteScope = 'job' | 'paper'

/* ─── Synthesis type visual config ─── */
const SYNTHESIS_TYPE_CONFIG: Record<string, { color: string; borderColor: string; icon: typeof CheckCircle2; label: string; labelZh: string }> = {
  consensus: { color: 'text-emerald-600 dark:text-emerald-400', borderColor: 'border-l-emerald-500', icon: CheckCircle2, label: 'Consensus', labelZh: '共识' },
  debate:    { color: 'text-amber-600 dark:text-amber-400',   borderColor: 'border-l-amber-500',   icon: Swords,       label: 'Debate',    labelZh: '争议' },
  evolution: { color: 'text-sky-600 dark:text-sky-400',       borderColor: 'border-l-sky-500',     icon: TrendingUp,   label: 'Evolution', labelZh: '演化' },
  open_question: { color: 'text-violet-600 dark:text-violet-400', borderColor: 'border-l-violet-500', icon: HelpCircle, label: 'Open Question', labelZh: '开放问题' },
}

/* ─── Domain Brief component ─── */
function DomainBrief({ brief, contentLanguage }: { brief: ProfileBrief | null | undefined; contentLanguage: string }) {
  if (!brief) return null

  const isZh = contentLanguage === 'zh'

  if (brief.stage === 'empty') {
    return (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center justify-center py-8">
          <Sparkles className="mb-3 h-8 w-8 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">
            {isZh ? '还没有论文数据。运行一次 pipeline 开始积累领域认知。' : 'No papers yet. Run a pipeline to start building domain cognition.'}
          </p>
        </CardContent>
      </Card>
    )
  }

  if (brief.stage === 'initial') {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-lg">
            <Sparkles className="h-4 w-4" />
            {isZh ? '初步印象' : 'First Impressions'}
          </CardTitle>
          <CardDescription>
            {isZh
              ? `基于 ${brief.paper_count} 篇论文的初步认知（论文数量较少，尚不足以形成共识/争议判断）`
              : `Based on ${brief.paper_count} paper(s) — too few to form consensus/debate judgments`}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {brief.key_concepts && brief.key_concepts.length > 0 && (
            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">{isZh ? '关键概念' : 'Key Concepts'}</p>
              <div className="flex flex-wrap gap-2">
                {brief.key_concepts.map((c, i) => (
                  <Badge key={i} variant="secondary" className="text-xs">
                    {isZh ? (c.name_zh || c.name) : c.name}
                    {c.claim_count > 0 && <span className="ml-1 opacity-60">({c.claim_count})</span>}
                  </Badge>
                ))}
              </div>
            </div>
          )}
          {brief.core_findings && brief.core_findings.length > 0 && (
            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">{isZh ? '核心发现' : 'Core Findings'}</p>
              <div className="space-y-2">
                {brief.core_findings.map((f, i) => (
                  <div key={i} className="rounded-lg border border-l-4 border-l-emerald-500 px-3 py-2">
                    <p className="text-sm font-medium">{isZh ? (f.title_zh || f.title) : f.title}</p>
                    <p className="mt-1 text-xs text-muted-foreground line-clamp-2">{isZh ? (f.body_zh || f.body) : f.body}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    )
  }

  // stage === 'full'
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-lg">
          <Sparkles className="h-4 w-4" />
          {isZh ? '领域简报' : 'Domain Brief'}
        </CardTitle>
        <CardDescription>
          {isZh
            ? `基于 ${brief.paper_count} 篇论文的领域认知概览`
            : `Domain overview based on ${brief.paper_count} papers`}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-4">
          {/* Left: Themes */}
          <div>
            <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">{isZh ? '研究主题' : 'Key Themes'}</p>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {brief.key_themes && brief.key_themes.length > 0 ? brief.key_themes.map((t, i) => (
                <div key={i} className="h-full rounded-lg border px-3 py-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{isZh ? (t.anchor_zh || t.anchor) : t.anchor}</span>
                    <Badge variant="outline" className="text-xs">{t.claim_count} claims</Badge>
                    {t.paper_count > 0 && <Badge variant="outline" className="text-xs">{t.paper_count} papers</Badge>}
                    {t.maturity ? <Badge variant="secondary" className="text-xs">{t.maturity}</Badge> : null}
                    {t.has_debate && <Swords className="h-3 w-3 text-amber-500" />}
                    {t.has_open_question && <HelpCircle className="h-3 w-3 text-violet-500" />}
                  </div>
                  {(t.summary || t.summary_zh) ? (
                    <p className="mt-1 text-xs text-muted-foreground line-clamp-2">{isZh ? (t.summary_zh || t.summary) : t.summary}</p>
                  ) : null}
                  {t.methods.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {t.methods.map((m, j) => (
                        <span key={j} className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">{m}</span>
                      ))}
                    </div>
                  )}
                </div>
              )) : (
                <p className="text-sm text-muted-foreground">{isZh ? '暂无明确研究主题' : 'No clear themes yet'}</p>
              )}
            </div>
          </div>

          {/* Right: Consensus / Debates / Open Questions */}
          <div className="space-y-3">
            {brief.top_consensus && brief.top_consensus.length > 0 && (
              <div>
                <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-emerald-600 dark:text-emerald-400">
                  <CheckCircle2 className="h-3 w-3" /> {isZh ? '共识' : 'Consensus'}
                </p>
                <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                  {brief.top_consensus.map((c, i) => (
                    <div key={i} className="h-full rounded-lg border border-l-4 border-l-emerald-500 px-3 py-1.5">
                      <p className="text-sm">{isZh ? (c.title_zh || c.title) : c.title}</p>
                      <span className="text-xs text-muted-foreground">{c.claim_count > 0 ? `${c.claim_count} supporting claims` : ''}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {brief.top_debates && brief.top_debates.length > 0 && (
              <div>
                <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-amber-600 dark:text-amber-400">
                  <Swords className="h-3 w-3" /> {isZh ? '争议' : 'Debates'}
                </p>
                <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                  {brief.top_debates.map((d, i) => (
                    <div key={i} className="h-full rounded-lg border border-l-4 border-l-amber-500 px-3 py-1.5">
                      <p className="text-sm">{isZh ? (d.title_zh || d.title) : d.title}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {brief.open_questions && brief.open_questions.length > 0 && (
              <div>
                <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-violet-600 dark:text-violet-400">
                  <HelpCircle className="h-3 w-3" /> {isZh ? '开放问题' : 'Open Questions'}
                </p>
                <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                  {brief.open_questions.map((q, i) => (
                    <div key={i} className="h-full rounded-lg border border-l-4 border-l-violet-500 px-3 py-1.5">
                      <p className="text-sm">{isZh ? (q.title_zh || q.title) : q.title}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {brief.gap_watchlist && brief.gap_watchlist.length > 0 && (
              <div>
                <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-rose-600 dark:text-rose-400">
                  <AlertTriangle className="h-3 w-3" /> {isZh ? '知识空白' : 'Knowledge Gaps'}
                </p>
                <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                  {brief.gap_watchlist.map((gap, i) => (
                    <div key={i} className="h-full rounded-lg border border-l-4 border-l-rose-500 px-3 py-1.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm">{isZh ? (gap.title_zh || gap.title) : gap.title}</p>
                        <Badge variant="outline" className="text-[11px]">{gap.priority}</Badge>
                      </div>
                      {(gap.summary || gap.summary_zh) ? (
                        <p className="mt-1 text-xs text-muted-foreground line-clamp-2">{isZh ? (gap.summary_zh || gap.summary) : gap.summary}</p>
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Recent Delta */}
        {brief.recent_delta && brief.recent_delta.impact_score > 0 && (
          <div className="mt-4 rounded-lg border border-dashed bg-muted/30 px-3 py-2">
            <p className="mb-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {isZh ? '最近变化' : 'Recent Change'}: {brief.recent_delta.paper_title || brief.recent_delta.paper_id}
            </p>
            <div className="flex flex-wrap gap-2">
              {brief.recent_delta.new_entities.length > 0 && (
                <Badge variant="outline" className="gap-1 text-xs">
                  <Plus className="h-3 w-3" /> {brief.recent_delta.new_entities.length} {isZh ? '新概念' : 'new concepts'}
                </Badge>
              )}
              {brief.recent_delta.reinforced_claims.length > 0 && (
                <Badge variant="outline" className="gap-1 text-xs text-emerald-600">
                  <ShieldCheck className="h-3 w-3" /> {brief.recent_delta.reinforced_claims.length} {isZh ? '被强化' : 'reinforced'}
                </Badge>
              )}
              {brief.recent_delta.challenged_claims.length > 0 && (
                <Badge variant="outline" className="gap-1 text-xs text-amber-600">
                  <Zap className="h-3 w-3" /> {brief.recent_delta.challenged_claims.length} {isZh ? '被挑战' : 'challenged'}
                </Badge>
              )}
              {brief.recent_delta.new_debates.length > 0 && (
                <Badge variant="outline" className="gap-1 text-xs text-amber-600">
                  <Swords className="h-3 w-3" /> {brief.recent_delta.new_debates.length} {isZh ? '新争议' : 'new debates'}
                </Badge>
              )}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function OpportunityPreviewCard({
  item,
  contentLanguage,
}: {
  item: OpportunityItem
  contentLanguage: string
}) {
  const isZh = contentLanguage === 'zh'
  const priorityVariant = item.priority === 'high' ? 'destructive' : 'outline'

  return (
    <div className="rounded-xl border border-sky-200/70 bg-sky-50/40 p-3 dark:border-sky-900/60 dark:bg-sky-950/20">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={priorityVariant}>{item.priority}</Badge>
        <Badge variant="outline">{item.opportunity_type}</Badge>
        {resolveLocalizedText(item.theme_titles_localized[0], contentLanguage as 'zh' | 'en') ? (
          <Badge variant="outline">{resolveLocalizedText(item.theme_titles_localized[0], contentLanguage as 'zh' | 'en')}</Badge>
        ) : null}
      </div>
      <LocalizedTextBlock localized={item.title_localized} className="mt-2" textClassName="font-medium text-base" />
      <LocalizedTextBlock localized={item.summary_localized} className="mt-1 text-sm text-muted-foreground" />
      <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
        {item.claim_ids.length > 0 ? <span>{isZh ? `Claims ${item.claim_ids.length}` : `Claims ${item.claim_ids.length}`}</span> : null}
        {item.review_ids.length > 0 ? <span>{isZh ? `Reviews ${item.review_ids.length}` : `Reviews ${item.review_ids.length}`}</span> : null}
        {item.paper_ids.length > 0 ? <span>{isZh ? `Papers ${item.paper_ids.length}` : `Papers ${item.paper_ids.length}`}</span> : null}
      </div>
      {item.suggested_validation_steps.length > 0 ? (
        <div className="mt-3 space-y-1">
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            {isZh ? '建议验证动作' : 'Suggested Validation'}
          </p>
          <ul className="space-y-1 text-xs text-muted-foreground">
            {item.suggested_validation_steps.slice(0, 2).map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}


export default function ProfileDetailPage() {
  const { profileId } = useParams<{ profileId: string }>()
  const navigate = useNavigate()
  const numericProfileId = useMemo(() => Number(profileId), [profileId])
  const [detail, setDetail] = useState<ProfileDetail | null>(null)
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [error, setError] = useState<string | null>(null)
  const [pendingDeleteJob, setPendingDeleteJob] = useState<ProfileActivityItem | null>(null)
  const [pendingDeleteScope, setPendingDeleteScope] = useState<DeleteScope>('job')
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false)
  const [deleteCountdown, setDeleteCountdown] = useState(DELETE_CONFIRM_SECONDS)
  const [isDeleting, setIsDeleting] = useState(false)
  const [isRebuilding, setIsRebuilding] = useState(false)
  const [selectedJobIds, setSelectedJobIds] = useState<string[]>([])
  const [isMoveDialogOpen, setIsMoveDialogOpen] = useState(false)
  const [moveTargetProfileId, setMoveTargetProfileId] = useState<number | null>(null)
  const [isMoving, setIsMoving] = useState(false)
  const [contentLanguage, setContentLanguage] = useState<LocalizedContentLanguage>('zh')

  const loadDetail = useCallback(async () => {
    if (!profileId || Number.isNaN(numericProfileId)) {
      return
    }
    const payload = await getProfileDetail(numericProfileId)
    setDetail(payload)
    setError(null)
  }, [numericProfileId, profileId])

  const loadProfiles = useCallback(async () => {
    const payload = await getProfiles()
    setProfiles(payload)
  }, [])

  useEffect(() => {
    if (!profileId || Number.isNaN(numericProfileId)) {
      return
    }
    void loadDetail().catch((err) => {
      setError(err instanceof Error ? err.message : 'Failed to load profile detail.')
    })
  }, [loadDetail, numericProfileId, profileId])

  useEffect(() => {
    void loadProfiles().catch((err) => {
      setError(err instanceof Error ? err.message : 'Failed to load profiles.')
    })
  }, [loadProfiles])

  useEffect(() => {
    if (!detail) {
      return
    }
    const validJobIds = new Set(detail.activity.map((item) => item.job_id))
    setSelectedJobIds((current) => current.filter((jobId) => validJobIds.has(jobId)))
  }, [detail])

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

  const openDeleteDialog = (item: ProfileActivityItem, scope: DeleteScope) => {
    setPendingDeleteJob(item)
    setPendingDeleteScope(scope)
    setDeleteCountdown(DELETE_CONFIRM_SECONDS)
    setIsDeleteDialogOpen(true)
  }

  const closeDeleteDialog = () => {
    if (isDeleting) {
      return
    }
    setIsDeleteDialogOpen(false)
    setPendingDeleteJob(null)
    setPendingDeleteScope('job')
    setDeleteCountdown(DELETE_CONFIRM_SECONDS)
  }

  const handleDeleteConfirm = async () => {
    if (!pendingDeleteJob || Number.isNaN(numericProfileId) || deleteCountdown > 0) {
      return
    }
    setIsDeleting(true)
    try {
      if (pendingDeleteScope === 'paper') {
        if (!pendingDeleteJob.paper_id) {
          throw new Error('This activity does not have a paper id, so paper-level deletion is unavailable.')
        }
        await deleteProfilePaperMemory(numericProfileId, pendingDeleteJob.paper_id)
      } else {
        await deleteProfileJobMemory(numericProfileId, pendingDeleteJob.job_id)
      }
      await loadDetail()
      setIsDeleteDialogOpen(false)
      setPendingDeleteJob(null)
      setPendingDeleteScope('job')
      setDeleteCountdown(DELETE_CONFIRM_SECONDS)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : pendingDeleteScope === 'paper'
            ? 'Failed to delete memory for this paper.'
            : 'Failed to delete memory for this job.',
      )
    } finally {
      setIsDeleting(false)
    }
  }

  const handleRebuild = async () => {
    if (Number.isNaN(numericProfileId)) {
      return
    }
    setIsRebuilding(true)
    try {
      await rebuildProfileMemory(numericProfileId)
      await loadDetail()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to rebuild profile cognition.')
    } finally {
      setIsRebuilding(false)
    }
  }

  const toggleJobSelection = (jobId: string) => {
    setSelectedJobIds((current) =>
      current.includes(jobId)
        ? current.filter((item) => item !== jobId)
        : [...current, jobId],
    )
  }

  const selectedItems = useMemo(
    () => detail?.activity.filter((item) => selectedJobIds.includes(item.job_id)) ?? [],
    [detail?.activity, selectedJobIds],
  )

  const moveTargetOptions = useMemo(
    () => profiles.filter((item) => item.id !== numericProfileId),
    [numericProfileId, profiles],
  )

  const openMoveDialog = () => {
    if (selectedJobIds.length === 0) {
      setError('Select at least one paper before moving.')
      return
    }
    if (moveTargetOptions.length === 0) {
      setError('Create another profile first, then you can move selected papers into it.')
      return
    }
    setMoveTargetProfileId((current) => current ?? moveTargetOptions[0]?.id ?? null)
    setIsMoveDialogOpen(true)
  }

  const closeMoveDialog = () => {
    if (isMoving) {
      return
    }
    setIsMoveDialogOpen(false)
  }

  const handleMoveConfirm = async () => {
    if (Number.isNaN(numericProfileId) || !moveTargetProfileId || selectedJobIds.length === 0) {
      return
    }
    setIsMoving(true)
    try {
      await moveProfilePapers(numericProfileId, {
        target_profile_id: moveTargetProfileId,
        job_ids: selectedJobIds,
      })
      setSelectedJobIds([])
      setIsMoveDialogOpen(false)
      await Promise.all([loadDetail(), loadProfiles()])
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to move selected papers.')
    } finally {
      setIsMoving(false)
    }
  }

  if (!profileId || Number.isNaN(numericProfileId)) {
    return <p className="max-w-6xl mx-auto text-sm text-muted-foreground">No profile selected.</p>
  }

  if (error && !detail) {
    return (
      <div className="max-w-6xl mx-auto space-y-4">
        <Link to="/profiles">
          <Button variant="ghost" size="sm">
            <ArrowLeft className="mr-1 h-4 w-4" />
            Back
          </Button>
        </Link>
        <p className="text-sm text-destructive">{error}</p>
      </div>
    )
  }

  if (!detail) {
    return <p className="max-w-6xl mx-auto text-sm text-muted-foreground">Loading profile detail...</p>
  }

  const {
    profile,
    activity,
    knowledge,
    curated_digest: curatedDigest,
    style,
    links,
    overview,
    brief,
    theme_preview: themePreview,
    gap_preview: gapPreview,
    opportunity_preview: opportunityPreview,
    health,
    field_map_preview: fieldMapPreview,
    survey_meta: surveyMeta,
  } = detail
  const styleEntries = Object.entries(style)
  const pendingPaperTitle = pendingDeleteJob?.paper_title || pendingDeleteJob?.job_paper_title || 'Untitled paper'
  const selectedCount = selectedJobIds.length
  const allSelected = activity.length > 0 && selectedCount === activity.length
  const moveTargetProfile = moveTargetOptions.find((item) => item.id === moveTargetProfileId) ?? null

  return (
    <LocalizedTextLanguageProvider language={contentLanguage}>
      <>
      <div className="max-w-6xl mx-auto space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-2">
            <Link to="/profiles">
              <Button variant="ghost" size="sm">
                <ArrowLeft className="mr-1 h-4 w-4" />
                Back
              </Button>
            </Link>
            <div className="space-y-1">
              <h2 className="flex items-center gap-2 text-2xl font-bold">
                <Brain className="h-5 w-5" />
                {profile.name}
              </h2>
              <p className="text-sm text-muted-foreground">
                {profile.description || 'No description yet.'}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <div className="inline-flex h-8 items-center rounded-lg border border-border bg-background/90 p-0.5">
              <Button
                type="button"
                size="sm"
                className="h-full rounded-md"
                variant={contentLanguage === 'zh' ? 'default' : 'ghost'}
                onClick={() => setContentLanguage('zh')}
              >
                <Languages className="mr-1 h-4 w-4" />
                中文
              </Button>
              <Button
                type="button"
                size="sm"
                className="h-full rounded-md"
                variant={contentLanguage === 'en' ? 'default' : 'ghost'}
                onClick={() => setContentLanguage('en')}
              >
                EN
              </Button>
            </div>
            <Button variant="outline" disabled={isRebuilding} onClick={handleRebuild}>
              <RefreshCw className="mr-2 h-4 w-4" />
              {isRebuilding ? 'Rebuilding...' : 'Rebuild Cognition'}
            </Button>
            <DeleteProfileDialog
              profile={profile}
              onDeleted={async () => {
                navigate('/profiles')
              }}
              trigger={
                <Button variant="destructive">
                  <Trash2 className="mr-2 h-4 w-4" />
                  Delete Profile
                </Button>
              }
            />
            <Link to={`/profiles/${numericProfileId}/workspace`}>
              <Button>
                <PanelRightOpen className="mr-2 h-4 w-4" />
                Open Memory Workspace
              </Button>
            </Link>
            <Link to={`/profiles/${numericProfileId}/survey`}>
              <Button variant="outline">
                <FileText className="mr-2 h-4 w-4" />
                Open Living Survey
              </Button>
            </Link>
          </div>
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}

        {/* Domain Brief — first screen */}
        <DomainBrief brief={brief} contentLanguage={contentLanguage} />

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          <Card size="sm">
            <CardHeader className="pb-2">
              <CardDescription>Profile activity</CardDescription>
              <CardTitle className="text-2xl">{profile.paper_count}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-muted-foreground">Distinct paper/job memory bundles in this profile.</p>
            </CardContent>
          </Card>
          <Card size="sm">
            <CardHeader className="pb-2">
              <CardDescription>Claims + synthesis</CardDescription>
              <CardTitle className="text-2xl">{overview.claim_count + overview.synthesis_count}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-muted-foreground">Structured mid-level claims and high-level domain cognition.</p>
            </CardContent>
          </Card>
          <Card size="sm">
            <CardHeader className="pb-2">
              <CardDescription>Pending reviews</CardDescription>
              <CardTitle className="text-2xl">{overview.pending_review_count}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-muted-foreground">AI default resolutions still waiting for manual confirmation.</p>
            </CardContent>
          </Card>
          <Card size="sm">
            <CardHeader className="pb-2">
              <CardDescription>Themes + opportunities</CardDescription>
              <CardTitle className="text-2xl">{overview.theme_count + overview.opportunity_count}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-muted-foreground">Readable structure layers and next-step research opportunities built on top of raw memory objects.</p>
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <span>Memory Health</span>
                  <InfoHint content={<p>健康度是确定性治理检查，用来发现无证据、证据薄弱、争议、适用范围缺失和派生产物过期。</p>} />
                </CardTitle>
                <CardDescription>详细问题列表请进入 Memory Workspace 的健康度视图。</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {!health ? (
                  <p className="text-sm text-muted-foreground">No health snapshot available yet.</p>
                ) : (
                  <>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={health.status === 'critical' ? 'destructive' : 'secondary'}>{health.status}</Badge>
                      <Badge variant="outline">score {health.score.toFixed(2)}</Badge>
                      <Badge variant="outline">issues {health.issues.reduce((sum, item) => sum + item.count, 0)}</Badge>
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
                      <span>Unsupported: {health.summary.unsupported_claim_count}</span>
                      <span>Thin evidence: {health.summary.thin_evidence_claim_count}</span>
                      <span>Contested: {health.summary.contested_claim_count}</span>
                      <span>Scope incomplete: {health.summary.scope_incomplete_claim_count}</span>
                    </div>
                  </>
                )}
                <Link to={`/profiles/${numericProfileId}/workspace`}>
                  <Button variant="outline" size="sm">Open Health View</Button>
                </Link>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <span>Field Map Preview</span>
                  <InfoHint content={<p>领域地图是从 themes、claim relations 和 review 状态派生的只读结构图，帮助你理解当前 profile 的领域组织方式。</p>} />
                </CardTitle>
                <CardDescription>这里只显示前三个簇，完整地图在 Workspace 中查看。</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {fieldMapPreview.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No field map clusters yet.</p>
                ) : fieldMapPreview.map((cluster) => (
                  <div key={cluster.cluster_key} className="rounded-xl border p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="secondary">{cluster.cluster_type}</Badge>
                      <Badge variant="outline">{cluster.maturity}</Badge>
                      {cluster.controversy_count > 0 ? <Badge variant="destructive">{cluster.controversy_count} contested</Badge> : null}
                    </div>
                    <LocalizedTextBlock localized={cluster.title_localized} className="mt-2" textClassName="font-medium" />
                    <p className="mt-1 text-xs text-muted-foreground">{cluster.paper_count} papers · {cluster.claim_count} claims · {cluster.evidence_count} evidence</p>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader className="pb-4">
              <div className="space-y-2">
                <CardTitle className="flex items-center gap-2">
                  <span>Living Survey</span>
                  <InfoHint content={<p>Living Survey 是当前 Profile 的完整阅读层输出：它把主题、空白、来源覆盖与最近变化组织成一份可连续阅读的综述页面。</p>} />
                </CardTitle>
                <CardDescription className="max-w-2xl">
                  {surveyMeta?.exists
                    ? (surveyMeta.stale
                        ? 'Survey 已标记为 stale，打开时会按最新 memory 自动重建。'
                        : 'Survey 已就绪，可直接阅读。')
                    : 'Survey 尚未生成，首次打开时会根据当前 memory 自动构建。'}
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex flex-wrap items-center gap-2 gap-y-2 text-sm text-muted-foreground">
                <Badge variant="secondary">{surveyMeta?.exists ? 'available' : 'not built yet'}</Badge>
                <Badge variant="outline">{surveyMeta?.stale ? 'stale' : 'fresh'}</Badge>
                {surveyMeta?.section_count ? <Badge variant="outline">{surveyMeta.section_count} sections</Badge> : null}
                <span className="text-sm text-muted-foreground lg:ml-2">
                  <span className="font-medium text-foreground/80">Last generated:</span>{' '}
                  {surveyMeta?.updated_at
                    ? formatDate(surveyMeta.updated_at)
                    : 'No cached survey build yet.'}
                </span>
              </div>
              <Link to={`/profiles/${numericProfileId}/survey`} className="w-full lg:w-auto">
                <Button className="w-full lg:min-w-[220px]">
                  <FileText className="mr-2 h-4 w-4" />
                  Open Survey Page
                </Button>
              </Link>
            </CardContent>
          </Card>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <span>Theme Preview</span>
                  <InfoHint content={<p>这里展示当前最重要的研究主题，帮助你在进入 Workspace 之前先形成领域结构感。</p>} />
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {themePreview.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No theme structure available yet.</p>
                ) : themePreview.map((theme) => (
                  <div key={theme.theme_key} className="rounded-xl border p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="secondary">{theme.maturity}</Badge>
                      <Badge variant="outline">{theme.paper_count} papers</Badge>
                      <Badge variant="outline">{theme.claim_count} claims</Badge>
                      {theme.debate_count > 0 ? <Badge variant="outline">{theme.debate_count} debates</Badge> : null}
                      {theme.open_question_count > 0 ? <Badge variant="outline">{theme.open_question_count} open questions</Badge> : null}
                    </div>
                    <LocalizedTextBlock localized={theme.title_localized} className="mt-2" textClassName="font-medium text-base" />
                    <LocalizedTextBlock localized={theme.summary_localized} className="mt-1 text-sm text-muted-foreground" />
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <span>Gap Preview</span>
                  <InfoHint content={<p>这里展示当前 Profile 中最值得优先关注的争议、薄弱证据和开放问题。</p>} />
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {gapPreview.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No outstanding knowledge gaps right now.</p>
                ) : gapPreview.map((gap) => (
                  <div key={gap.gap_key} className="rounded-xl border border-rose-200/70 bg-rose-50/40 p-3 dark:border-rose-900/60 dark:bg-rose-950/20">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="destructive">{gap.priority}</Badge>
                      <Badge variant="outline">{gap.gap_type}</Badge>
                      {resolveLocalizedText(gap.theme_title_localized, contentLanguage as 'zh' | 'en') ? (
                        <Badge variant="outline">{resolveLocalizedText(gap.theme_title_localized, contentLanguage as 'zh' | 'en')}</Badge>
                      ) : null}
                    </div>
                    <LocalizedTextBlock localized={gap.title_localized} className="mt-2" textClassName="font-medium text-base" />
                    <LocalizedTextBlock localized={gap.summary_localized} className="mt-1 text-sm text-muted-foreground" />
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <span>Opportunity Preview</span>
                <InfoHint content={<p>这里展示 Memory V3 第一版只读派生出的研究机会项，用来帮助你快速看到当前 profile 最值得继续验证、澄清或迁移测试的方向。</p>} />
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {opportunityPreview.length === 0 ? (
                <p className="text-sm text-muted-foreground">No derived research opportunities yet.</p>
              ) : opportunityPreview.map((item) => (
                <OpportunityPreviewCard key={item.opportunity_key} item={item} contentLanguage={contentLanguage} />
              ))}
            </CardContent>
          </Card>
        </div>

        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary">Created: {formatDate(profile.created_at)}</Badge>
          <Badge variant="outline">Last used: {formatDate(profile.last_used_at)}</Badge>
          <Badge variant="outline">Entities: {overview.entity_count}</Badge>
          <Badge variant="outline">Revisions: {overview.revision_count}</Badge>
          <Badge variant="outline">Themes: {overview.theme_count}</Badge>
          <Badge variant="outline">High-priority gaps: {overview.high_priority_gap_count}</Badge>
          <Badge variant="outline">High-priority opportunities: {overview.high_priority_opportunity_count}</Badge>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <span>Linked Papers and Reports</span>
                <InfoHint content={<p>这里展示哪些作业和源论文已经向当前 Profile 写入了 memory。你可以删除某个作业级 memory bundle，或移除某篇论文在当前 Profile 中写入的全部 memory 贡献。这两种操作都不会删除报告或 PDF 本身。</p>} />
              </CardTitle>
              <CardDescription>
                Jobs and papers that have written memory into this profile. You can batch-move selected papers into another profile and rebuild both sides.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {activity.length === 0 ? (
                <p className="text-sm text-muted-foreground">This profile has no completed or active paper activity yet.</p>
              ) : (
                <>
                  <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-dashed bg-muted/30 px-3 py-2">
                    <p className="text-xs text-muted-foreground">
                      {selectedCount > 0
                        ? `${selectedCount} paper${selectedCount === 1 ? '' : 's'} selected. Moving will clear their current profile links here and rebuild memory on both profiles.`
                        : 'Select one or more papers to move them into another profile.'}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setSelectedJobIds(allSelected ? [] : activity.map((item) => item.job_id))}
                      >
                        {allSelected ? 'Clear All' : 'Select All'}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setSelectedJobIds([])}
                        disabled={selectedCount === 0}
                      >
                        Clear Selection
                      </Button>
                      <Button
                        size="sm"
                        onClick={openMoveDialog}
                        disabled={selectedCount === 0 || moveTargetOptions.length === 0}
                      >
                        <ArrowRightLeft className="mr-1 h-3.5 w-3.5" />
                        Move Selected
                      </Button>
                    </div>
                  </div>
                  {activity.map((item) => (
                <div key={item.job_id} className="rounded-xl border p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="space-y-1">
                      <label className="inline-flex items-center gap-2 text-xs text-muted-foreground">
                        <input
                          type="checkbox"
                          className="h-4 w-4 rounded border"
                          checked={selectedJobIds.includes(item.job_id)}
                          onChange={() => toggleJobSelection(item.job_id)}
                        />
                        Select for move
                      </label>
                      <p className="font-medium">{item.paper_title || item.job_paper_title || 'Untitled paper'}</p>
                      <p className="text-xs text-muted-foreground">
                        {item.paper_venue || 'Unknown venue'}
                        {item.paper_pub_date ? ` · ${item.paper_pub_date}` : ''}
                        {item.paper_id ? ` · ${item.paper_id}` : ''}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="secondary">{item.job_status || 'unknown'}</Badge>
                      <Badge variant="outline">{item.job_mode || 'unknown'}</Badge>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {item.job_report_path && (
                      <Link to={`/reports/job/${encodeURIComponent(item.job_id)}`}>
                        <Button size="sm" variant="outline">
                          <FileText className="mr-1 h-3.5 w-3.5" />
                          Open Report
                        </Button>
                      </Link>
                    )}
                    {item.paper_row_id && (item.paper_source_path || item.paper_pdf_path) && (
                      <a href={getPaperPdfUrl(item.paper_row_id)} target="_blank" rel="noreferrer">
                        <Button size="sm" variant="outline">
                          <Library className="mr-1 h-3.5 w-3.5" />
                          {item.paper_source_type === 'html' ? 'Open Source' : 'Open PDF'}
                        </Button>
                      </a>
                    )}
                    <Button
                      size="sm"
                      variant="destructive"
                      className="border border-destructive bg-destructive text-destructive-foreground hover:bg-destructive/90 hover:text-destructive-foreground"
                      aria-label={`Delete memory for job ${item.job_id}`}
                      onClick={() => openDeleteDialog(item, 'job')}
                    >
                      <Trash2 className="mr-1 h-3.5 w-3.5" />
                      Delete Job Memory
                    </Button>
                    {item.paper_id && (
                      <Button
                        size="sm"
                        variant="destructive"
                        className="border border-destructive bg-destructive/90 text-destructive-foreground hover:bg-destructive hover:text-destructive-foreground"
                        aria-label={`Delete paper memory for ${item.paper_id}`}
                        onClick={() => openDeleteDialog(item, 'paper')}
                      >
                        <Trash2 className="mr-1 h-3.5 w-3.5" />
                        Delete Paper Memory
                      </Button>
                    )}
                  </div>
                  <p className="mt-3 text-xs text-muted-foreground">
                    Job created: {formatDate(item.job_created_at)}
                    {item.job_completed_at ? ` · Completed: ${formatDate(item.job_completed_at)}` : ''}
                    {!item.job_completed_at && item.job_current_step ? ` · Current step: ${item.job_current_step}` : ''}
                  </p>
                </div>
                  ))}
                </>
              )}
            </CardContent>
          </Card>

          <div className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <span>Knowledge Snapshot</span>
                  <InfoHint content={<p>这里是当前 Profile memory 的紧凑预览，展示最有可能再次注入给 Agent 的知识内容。若要查看和编辑完整的 claims、synthesis、图谱关系与证据链，请进入完整的 workspace。</p>} />
                </CardTitle>
                <CardDescription>
                  使用页面右上角的统一语言按钮切换整页中英显示；这里不再为每个条目单独提供翻译按钮。
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {curatedDigest.length === 0 ? (
                  knowledge.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No domain knowledge stored yet.</p>
                  ) : knowledge.map((item) => (
                    <div key={item.id} className="rounded-lg border px-3 py-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="secondary">{item.category || 'general'}</Badge>
                        {item.paper_id && <Badge variant="outline">{item.paper_id}</Badge>}
                      </div>
                      <LocalizedTextBlock localized={item.content_localized} className="mt-2" englishTitle="Knowledge Snapshot 英文原文" />
                    </div>
                  ))
                ) : (
                  <>
                    {curatedDigest.map((section) => {
                      const typeConfig = SYNTHESIS_TYPE_CONFIG[section.section_type] || SYNTHESIS_TYPE_CONFIG.consensus
                      const TypeIcon = typeConfig.icon
                      return (
                        <div key={section.section_type} className={`rounded-xl border border-l-4 ${typeConfig.borderColor} p-3`}>
                          <div className="flex flex-wrap items-center gap-2">
                            <TypeIcon className={`h-4 w-4 ${typeConfig.color}`} />
                            <span className={`text-sm font-medium ${typeConfig.color}`}>{section.section_label_zh || section.section_label}</span>
                            <Badge variant="outline" className="text-xs">{section.items.length} 条</Badge>
                          </div>
                          <div className="mt-3 space-y-3">
                            {section.items.map((item) => (
                              <div key={item.id} className="rounded-lg border px-3 py-2">
                                <div className="flex flex-wrap items-center gap-2">
                                  <Badge variant="outline" className="text-xs">置信度 {item.confidence.toFixed(2)}</Badge>
                                  {item.claim_count > 0 && <Badge variant="outline" className="text-xs">Claims {item.claim_count}</Badge>}
                                  {item.review_status !== 'none' ? <Badge variant={item.review_status === 'pending' ? 'destructive' : 'outline'} className="text-xs">{item.review_status}</Badge> : null}
                                  {item.manual_locked ? <Badge variant="outline" className="text-xs">manual</Badge> : null}
                                </div>
                                <LocalizedTextBlock localized={item.title_localized} className="mt-2" textClassName="font-medium" englishTitle="认知项英文原版" />
                                <LocalizedTextBlock localized={item.summary_localized} className="mt-1 text-muted-foreground text-sm line-clamp-3" />
                              </div>
                            ))}
                          </div>
                        </div>
                      )
                    })}
                    {knowledge.length > 0 ? (
                      <details className="rounded-xl border p-3">
                        <summary className="cursor-pointer text-sm font-medium">查看原始 Knowledge 条目</summary>
                        <div className="mt-3 space-y-2">
                          {knowledge.map((item) => (
                            <div key={item.id} className="rounded-lg border px-3 py-2">
                              <div className="flex flex-wrap items-center gap-2">
                                <Badge variant="secondary">{item.category || 'general'}</Badge>
                                {item.paper_id && <Badge variant="outline">{item.paper_id}</Badge>}
                              </div>
                              <LocalizedTextBlock localized={item.content_localized} className="mt-2" englishTitle="Knowledge Snapshot 英文原文" />
                            </div>
                          ))}
                        </div>
                      </details>
                    ) : null}
                  </>
                )}
              </CardContent>
            </Card>

            <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <span>Style Memory</span>
                <InfoHint content={<p>Style Memory stores stable output preferences inferred or edited for this profile, such as detail level, tone, or formula depth. It helps the Agent keep report style consistent across runs.</p>} />
              </CardTitle>
            </CardHeader>
              <CardContent className="space-y-2">
                {styleEntries.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No style preferences stored yet.</p>
                ) : styleEntries.map(([key, value]) => (
                  <div key={key} className="rounded-lg border px-3 py-2">
                    <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{key}</p>
                    <p className="mt-1 text-sm">{value}</p>
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <span>Paper Links</span>
                <InfoHint content={<p>Paper Links 展示当前 Profile 内部抽取或编辑得到的论文级关系，例如 extends、competes、related_to。这些关系也会在构建图谱工作区时被复用。</p>} />
              </CardTitle>
            </CardHeader>
              <CardContent className="space-y-2">
                {links.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No paper relation links stored yet.</p>
                ) : links.map((item) => (
                  <div key={item.id} className="rounded-lg border px-3 py-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="secondary">{item.relation_type}</Badge>
                    </div>
                    <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] md:items-center">
                      <div className="rounded-lg border bg-muted/20 px-3 py-2">
                        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">来源论文</p>
                        <p className="mt-1 break-all text-sm">{item.source_paper_id}</p>
                      </div>
                      <span className="justify-self-center rounded-full border px-2.5 py-1 text-[11px] font-medium text-muted-foreground">关联到</span>
                      <div className="rounded-lg border bg-muted/20 px-3 py-2">
                        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">目标论文</p>
                        <p className="mt-1 break-all text-sm">{item.target_paper_id}</p>
                      </div>
                    </div>
                    {item.summary || item.summary_zh ? <LocalizedTextBlock localized={item.summary_localized} className="mt-2" englishTitle="Paper Link 英文摘要" /> : null}
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>
        </div>
      </div>

      <Dialog open={isDeleteDialogOpen} onOpenChange={(open) => {
        if (!open) {
          closeDeleteDialog()
          return
        }
        setIsDeleteDialogOpen(true)
      }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-destructive" />
              {pendingDeleteScope === 'paper' ? 'Delete memory for this paper?' : 'Delete memory for this job?'}
            </DialogTitle>
            <DialogDescription>
              You are about to remove memory linked to <span className="font-semibold text-foreground">{pendingPaperTitle}</span> from the current profile <span className="font-semibold text-foreground">{profile.name}</span>.
            </DialogDescription>
            <div className="space-y-3 text-sm">
              <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 leading-6">
                <p className="font-semibold text-destructive">
                  {pendingDeleteScope === 'paper'
                    ? (
                        <>
                          This deletes all memory written for paper <span className="font-bold">{pendingDeleteJob?.paper_id || 'unknown'}</span> inside this Memory Profile card, including linked claims, evidence, synthesis, and orphaned graph entities that only exist because of this paper.
                        </>
                      )
                    : <>This deletes all memory written for job <span className="font-bold">{pendingDeleteJob?.job_id || 'unknown'}</span> inside this Memory Profile card.</>}
                </p>
                <p className="mt-2 font-semibold text-destructive">
                  It does not delete the report or PDF itself, and does not affect any other Memory Profile cards.
                </p>
              </div>
              <p className="text-xs text-muted-foreground">
                The confirm button will unlock after {DELETE_CONFIRM_SECONDS} seconds so you can review the scope carefully.
              </p>
            </div>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={closeDeleteDialog} disabled={isDeleting}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeleteConfirm}
              disabled={isDeleting || deleteCountdown > 0}
            >
              {isDeleting
                ? 'Deleting...'
                : deleteCountdown > 0
                  ? `Confirm Delete (${deleteCountdown}s)`
                  : pendingDeleteScope === 'paper'
                    ? 'Confirm Paper Delete'
                    : 'Confirm Delete'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={isMoveDialogOpen} onOpenChange={(open) => {
        if (!open) {
          closeMoveDialog()
          return
        }
        setIsMoveDialogOpen(true)
      }}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <ArrowRightLeft className="h-4 w-4" />
              Move selected papers to another profile?
            </DialogTitle>
            <DialogDescription>
              This will remove the selected papers from <span className="font-semibold text-foreground">{profile.name}</span>, add them into the target profile, and rebuild long-term memory on both sides.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 text-sm">
            <label className="block space-y-2">
              <span className="font-medium">Target profile</span>
              <select
                className="w-full rounded-md border bg-background px-3 py-2"
                value={moveTargetProfileId ?? ''}
                onChange={(event) => setMoveTargetProfileId(event.target.value ? Number(event.target.value) : null)}
                disabled={isMoving}
              >
                {moveTargetOptions.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="rounded-lg border border-amber-300/60 bg-amber-50/50 p-3 text-amber-900 dark:border-amber-700/60 dark:bg-amber-950/20 dark:text-amber-100">
              <p className="font-medium">
                {selectedCount} selected paper{selectedCount === 1 ? '' : 's'} will be moved.
              </p>
              <p className="mt-1 text-xs leading-5">
                The source profile will lose these papers&apos; writebacks, claims, graph links, and derived cognition. The target profile will absorb them and then rebuild its own long-term memory.
              </p>
            </div>
            <div className="rounded-lg border p-3">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Selected papers</p>
              <div className="mt-2 space-y-2">
                {selectedItems.slice(0, 6).map((item) => (
                  <div key={item.job_id} className="rounded-md border px-3 py-2">
                    <p className="font-medium">{item.paper_title || item.job_paper_title || 'Untitled paper'}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {item.paper_id || item.job_id}
                    </p>
                  </div>
                ))}
                {selectedItems.length > 6 && (
                  <p className="text-xs text-muted-foreground">
                    and {selectedItems.length - 6} more...
                  </p>
                )}
              </div>
            </div>
            {moveTargetProfile ? (
              <p className="text-xs text-muted-foreground">
                Target: <span className="font-medium text-foreground">{moveTargetProfile.name}</span>
                {moveTargetProfile.description ? ` · ${moveTargetProfile.description}` : ''}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={closeMoveDialog} disabled={isMoving}>
              Cancel
            </Button>
            <Button onClick={handleMoveConfirm} disabled={isMoving || !moveTargetProfileId || selectedCount === 0}>
              {isMoving ? 'Moving and rebuilding...' : 'Confirm Move'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      </>
    </LocalizedTextLanguageProvider>
  )
}
