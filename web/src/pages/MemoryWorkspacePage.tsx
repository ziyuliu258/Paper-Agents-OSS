import { Fragment, type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Compass,
  Database,
  FileClock,
  FilePenLine,
  FileSearch,
  GitBranch,
  HelpCircle,
  History,
  LibraryBig,
  Languages,
  Network,
  RefreshCw,
  Save,
  Sparkles,
  Swords,
  Trash2,
  TrendingUp,
  Workflow,
} from 'lucide-react'
import {
  createWorkspaceClaim,
  createWorkspaceEntity,
  createWorkspaceEvidence,
  createWorkspaceGraphEdge,
  createWorkspaceSynthesis,
  deleteProfilePaperMemory,
  deleteWorkspaceClaim,
  deleteWorkspaceEntity,
  deleteWorkspaceEvidence,
  deleteWorkspaceGraphEdge,
  deleteWorkspaceSynthesis,
  getProfile,
  getWorkspaceClaims,
  getWorkspaceCurated,
  getWorkspaceEntities,
  getWorkspaceEvidence,
  getWorkspaceEvidenceMatrix,
  getWorkspaceFieldMap,
  getWorkspaceGaps,
  getWorkspaceGraph,
  getWorkspaceGraphEdges,
  getWorkspaceHealth,
  getWorkspaceOverview,
  getWorkspaceOpportunities,
  getWorkspaceReviews,
  getWorkspaceRevisions,
  getWorkspaceSynthesis,
  getWorkspaceThemes,
  getWorkspaceTimeline,
  rebuildProfileMemory,
  resolveWorkspaceReview,
  updateWorkspaceClaim,
  updateWorkspaceEntity,
  updateWorkspaceEvidence,
  updateWorkspaceGraphEdge,
  updateWorkspaceSynthesis,
  type MemoryClaim,
  type MemoryClaimInput,
  type MemoryEditableGraphEdge,
  type MemoryEntity,
  type MemoryEntityInput,
  type MemoryEvidence,
  type MemoryEvidenceInput,
  type MemoryGraphEdgeInput,
  type MemoryReviewItem,
  type MemorySynthesisInput,
  type MemorySynthesisItem,
  type MemoryTimelineItem,
  type MemoryWorkspaceSnapshot,
  type Profile,
  type EvidenceMatrixSnapshot,
  type FieldMapSnapshot,
  type GapSnapshot,
  type MemoryHealth,
  type OpportunitySnapshot,
  type ThemeSnapshot,
} from '@/api/client'
import LocalizedTextBlock from '@/components/LocalizedTextBlock'
import LocalizedTextLanguageProvider from '@/components/LocalizedTextLanguageProvider'
import { resolveLocalizedText, type LocalizedContentLanguage } from '@/lib/localizedText'
import { formatDate } from '@/lib/formatters'
import MemoryGraphCanvas from '@/components/MemoryGraphCanvas'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import InfoHint from '@/components/ui/info-hint'
import { Input } from '@/components/ui/input'

const textareaClass = 'min-h-24 w-full rounded-lg border border-input bg-transparent px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50'
const selectClass = 'h-9 w-full rounded-lg border border-input bg-transparent px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50'

type WorkspaceView = 'knowledge' | 'health' | 'field-map' | 'matrix' | 'themes' | 'gaps' | 'opportunities' | 'graph' | 'timeline' | 'reviews' | 'history'
type KnowledgeSubView = 'curated' | 'raw'
type WorkspaceTab = { id: WorkspaceView; label: string; icon: typeof LibraryBig }
type RawKnowledgeFocus = {
  section: 'synthesis' | 'claims' | 'entities' | 'evidence'
  label: string
  claimId?: number
  paperId?: string
  entityType?: string
  synthesisType?: string
}

function toJsonText(value: unknown) {
  if (value === null || value === undefined || value === '') {
    return '—'
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function parseCsv(value: string) {
  return value
    .split(/[;,，；\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function parseIdCsv(value: string) {
  return value
    .split(/[;,，；\n]/)
    .map((item) => Number(item.trim()))
    .filter((item) => Number.isFinite(item) && item > 0)
}

function createEmptySnapshot(profile: Profile | null): MemoryWorkspaceSnapshot {
  return {
    profile,
    overview: {
      paper_source_count: 0,
      entity_count: 0,
      claim_count: 0,
      synthesis_count: 0,
      pending_review_count: 0,
      revision_count: 0,
      graph_node_count: 0,
      graph_edge_count: 0,
      theme_count: 0,
      gap_count: 0,
      high_priority_gap_count: 0,
      opportunity_count: 0,
      high_priority_opportunity_count: 0,
    },
    knowledge_items: [],
    style: {},
    links: [],
    entities: [],
    claims: [],
    evidence_fragments: [],
    synthesis_items: [],
    editable_edges: [],
    graph: { nodes: [], edges: [] },
    reviews: [],
    revisions: [],
    timeline: [],
    curated: {
      domain_digest: [],
      priority_claims: [],
      active_conflicts: [],
      source_bundles: [],
      entity_clusters: [],
    },
    themes: null,
    gaps: null,
    opportunities: null,
    health: null,
    field_map: null,
    evidence_matrix: null,
  }
}

function EmptyState({ title }: { title: string }) {
  return <p className="text-sm text-muted-foreground">{title}</p>
}

function OverviewCard({ title, value, hint }: { title: string; value: number | string; hint: string }) {
  return (
    <Card size="sm">
      <CardHeader className="pb-2">
        <CardDescription>{title}</CardDescription>
        <CardTitle className="text-2xl">{value}</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </CardContent>
    </Card>
  )
}

function SectionTitle({ title, hint, icon }: { title: string; hint: string; icon?: ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      {icon}
      <span>{title}</span>
      <InfoHint content={<p>{hint}</p>} />
    </div>
  )
}

function formatStatusLabel(status: string) {
  if (status === 'pending') {
    return 'pending review'
  }
  return status
}

function renderStateBadge(status?: string | null) {
  const normalized = (status || '').trim()
  if (!normalized || normalized === 'none' || normalized === 'active') {
    return null
  }
  const variant = normalized === 'pending' || normalized === 'conflicted' ? 'destructive' : 'outline'
  return <Badge variant={variant}>{formatStatusLabel(normalized)}</Badge>
}

function statusToneClass(status?: string | null) {
  const normalized = (status || '').trim()
  if (normalized === 'pending' || normalized === 'conflicted') {
    return 'border-destructive/35 bg-destructive/5'
  }
  return ''
}

function LocalizedJsonPreview({ value }: { value: unknown }) {
  const normalized = value && typeof value === 'object' ? value : null
  if (!normalized) {
    return <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs text-muted-foreground">{toJsonText(value)}</pre>
  }
  return <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs text-muted-foreground">{toJsonText(normalized)}</pre>
}

function EditDialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  children: ReactNode
  footer: ReactNode
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">{children}</div>
        <DialogFooter>{footer}</DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default function MemoryWorkspacePage() {
  const { profileId } = useParams<{ profileId: string }>()
  const numericProfileId = useMemo(() => Number(profileId), [profileId])

  const [detail, setDetail] = useState<Profile | null>(null)
  const [snapshot, setSnapshot] = useState<MemoryWorkspaceSnapshot | null>(null)
  const [loadedData, setLoadedData] = useState({
    knowledge: false,
    graph: false,
    reviews: false,
    history: false,
    timeline: false,
    themes: false,
    gaps: false,
    opportunities: false,
    health: false,
    fieldMap: false,
    matrix: false,
  })
  const [error, setError] = useState<string | null>(null)
  const [activeView, setActiveView] = useState<WorkspaceView>('knowledge')
  const [knowledgeSubView, setKnowledgeSubView] = useState<KnowledgeSubView>('curated')
  const [rawFocus, setRawFocus] = useState<RawKnowledgeFocus | null>(null)
  const [expandedClaimId, setExpandedClaimId] = useState<number | null>(null)
  const [isBusy, setIsBusy] = useState(false)
  const [selectedGraphNodeId, setSelectedGraphNodeId] = useState<string | null>(null)
  const [contentLanguage, setContentLanguage] = useState<LocalizedContentLanguage>('zh')

  const [editingEntityId, setEditingEntityId] = useState<number | null>(null)
  const [isEntityDialogOpen, setIsEntityDialogOpen] = useState(false)
  const [entityDraft, setEntityDraft] = useState<{ name: string; entity_type: string; summary: string; aliases: string }>({
    name: '',
    entity_type: 'concept',
    summary: '',
    aliases: '',
  })

  const [editingClaimId, setEditingClaimId] = useState<number | null>(null)
  const [isClaimDialogOpen, setIsClaimDialogOpen] = useState(false)
  const [claimDraft, setClaimDraft] = useState<{
    title: string
    body: string
    claim_type: string
    stance: string
    importance: string
    default_resolution: string
    entity_names: string
    scope_conditions: string
    scope_boundary: string
    scope_population: string
    scope_notes: string
  }>({
    title: '',
    body: '',
    claim_type: 'finding',
    stance: 'support',
    importance: '0.7',
    default_resolution: '',
    entity_names: '',
    scope_conditions: '',
    scope_boundary: '',
    scope_population: '',
    scope_notes: '',
  })

  const [editingEvidenceId, setEditingEvidenceId] = useState<number | null>(null)
  const [isEvidenceDialogOpen, setIsEvidenceDialogOpen] = useState(false)
  const [evidenceDraft, setEvidenceDraft] = useState<{
    claim_id: string
    section_key: string
    section_title: string
    snippet: string
    evidence_summary: string
    page_label: string
    anchor_kind: string
    context_before: string
    context_after: string
    structured_task: string
    structured_method: string
    structured_metric: string
    structured_value: string
    structured_baseline: string
    structured_comparator: string
    structured_dataset: string
    structured_setting: string
    structured_limitation: string
    structured_scope_note: string
  }>({
    claim_id: '',
    section_key: 'other',
    section_title: '',
    snippet: '',
    evidence_summary: '',
    page_label: '',
    anchor_kind: 'text',
    context_before: '',
    context_after: '',
    structured_task: '',
    structured_method: '',
    structured_metric: '',
    structured_value: '',
    structured_baseline: '',
    structured_comparator: '',
    structured_dataset: '',
    structured_setting: '',
    structured_limitation: '',
    structured_scope_note: '',
  })

  const [editingSynthesisId, setEditingSynthesisId] = useState<number | null>(null)
  const [isSynthesisDialogOpen, setIsSynthesisDialogOpen] = useState(false)
  const [synthesisDraft, setSynthesisDraft] = useState<{ item_type: string; title: string; summary: string; confidence: string; default_resolution: string; claim_ids: string }>({
    item_type: 'consensus',
    title: '',
    summary: '',
    confidence: '0.7',
    default_resolution: '',
    claim_ids: '',
  })

  const [editingEdgeId, setEditingEdgeId] = useState<number | null>(null)
  const [isEdgeDialogOpen, setIsEdgeDialogOpen] = useState(false)
  const [edgeDraft, setEdgeDraft] = useState<{ source_kind: string; source_ref: string; target_kind: string; target_ref: string; relation_type: string; summary: string; weight: string }>({
    source_kind: 'entity',
    source_ref: '',
    target_kind: 'entity',
    target_ref: '',
    relation_type: 'related_to',
    summary: '',
    weight: '1',
  })

  const loadWorkspaceShell = useCallback(async () => {
    if (!profileId || Number.isNaN(numericProfileId)) {
      return
    }
    setLoadedData({
      knowledge: false,
      graph: false,
      reviews: false,
      history: false,
      timeline: false,
      themes: false,
      gaps: false,
      opportunities: false,
      health: false,
      fieldMap: false,
      matrix: false,
    })
    const [profilePayload, overviewPayload, curatedPayload] = await Promise.all([
      getProfile(numericProfileId),
      getWorkspaceOverview(numericProfileId),
      getWorkspaceCurated(numericProfileId),
    ])
    setDetail(profilePayload)
    setSnapshot({
      ...createEmptySnapshot(profilePayload),
      profile: profilePayload,
      overview: overviewPayload,
      curated: curatedPayload,
    })
    setError(null)
  }, [numericProfileId, profileId])

  const loadKnowledgeData = useCallback(async () => {
    if (Number.isNaN(numericProfileId)) return
    const [entities, claims, evidence, synthesis] = await Promise.all([
      getWorkspaceEntities(numericProfileId),
      getWorkspaceClaims(numericProfileId),
      getWorkspaceEvidence(numericProfileId),
      getWorkspaceSynthesis(numericProfileId),
    ])
    setSnapshot((prev) => {
      if (!prev) return prev
      return {
        ...prev,
        entities,
        claims,
        evidence_fragments: evidence,
        synthesis_items: synthesis,
      }
    })
    setLoadedData((prev) => ({ ...prev, knowledge: true }))
  }, [numericProfileId])

  const loadGraphData = useCallback(async () => {
    if (Number.isNaN(numericProfileId)) return
    const [graph, editableEdges] = await Promise.all([
      getWorkspaceGraph(numericProfileId),
      getWorkspaceGraphEdges(numericProfileId),
    ])
    setSnapshot((prev) => {
      if (!prev) return prev
      return { ...prev, graph, editable_edges: editableEdges }
    })
    setLoadedData((prev) => ({ ...prev, graph: true }))
  }, [numericProfileId])

  const loadReviewData = useCallback(async () => {
    if (Number.isNaN(numericProfileId)) return
    const reviews = await getWorkspaceReviews(numericProfileId)
    setSnapshot((prev) => {
      if (!prev) return prev
      return { ...prev, reviews }
    })
    setLoadedData((prev) => ({ ...prev, reviews: true }))
  }, [numericProfileId])

  const loadRevisionData = useCallback(async () => {
    if (Number.isNaN(numericProfileId)) return
    const revisions = await getWorkspaceRevisions(numericProfileId)
    setSnapshot((prev) => {
      if (!prev) return prev
      return { ...prev, revisions }
    })
    setLoadedData((prev) => ({ ...prev, history: true }))
  }, [numericProfileId])

  const loadTimelineData = useCallback(async () => {
    if (Number.isNaN(numericProfileId)) return
    const timeline = await getWorkspaceTimeline(numericProfileId)
    setSnapshot((prev) => {
      if (!prev) return prev
      return { ...prev, timeline }
    })
    setLoadedData((prev) => ({ ...prev, timeline: true }))
  }, [numericProfileId])

  const loadThemeData = useCallback(async () => {
    if (Number.isNaN(numericProfileId)) return
    const themes = await getWorkspaceThemes(numericProfileId)
    setSnapshot((prev) => {
      if (!prev) return prev
      return { ...prev, themes }
    })
    setLoadedData((prev) => ({ ...prev, themes: true }))
  }, [numericProfileId])

  const loadGapData = useCallback(async () => {
    if (Number.isNaN(numericProfileId)) return
    const gaps = await getWorkspaceGaps(numericProfileId)
    setSnapshot((prev) => {
      if (!prev) return prev
      return { ...prev, gaps }
    })
    setLoadedData((prev) => ({ ...prev, gaps: true }))
  }, [numericProfileId])

  const loadOpportunityData = useCallback(async () => {
    if (Number.isNaN(numericProfileId)) return
    const opportunities = await getWorkspaceOpportunities(numericProfileId)
    setSnapshot((prev) => {
      if (!prev) return prev
      return { ...prev, opportunities }
    })
    setLoadedData((prev) => ({ ...prev, opportunities: true }))
  }, [numericProfileId])

  const loadHealthData = useCallback(async () => {
    if (Number.isNaN(numericProfileId)) return
    const health = await getWorkspaceHealth(numericProfileId)
    setSnapshot((prev) => {
      if (!prev) return prev
      return { ...prev, health }
    })
    setLoadedData((prev) => ({ ...prev, health: true }))
  }, [numericProfileId])

  const loadFieldMapData = useCallback(async () => {
    if (Number.isNaN(numericProfileId)) return
    const fieldMap = await getWorkspaceFieldMap(numericProfileId)
    setSnapshot((prev) => {
      if (!prev) return prev
      return { ...prev, field_map: fieldMap }
    })
    setLoadedData((prev) => ({ ...prev, fieldMap: true }))
  }, [numericProfileId])

  const loadMatrixData = useCallback(async () => {
    if (Number.isNaN(numericProfileId)) return
    const evidenceMatrix = await getWorkspaceEvidenceMatrix(numericProfileId)
    setSnapshot((prev) => {
      if (!prev) return prev
      return { ...prev, evidence_matrix: evidenceMatrix }
    })
    setLoadedData((prev) => ({ ...prev, matrix: true }))
  }, [numericProfileId])

  useEffect(() => {
    if (!profileId || Number.isNaN(numericProfileId)) {
      return
    }
    void loadWorkspaceShell().catch((err) => {
      setError(err instanceof Error ? err.message : 'Failed to load memory workspace.')
    })
  }, [loadWorkspaceShell, numericProfileId, profileId])

  useEffect(() => {
    if (!snapshot) return
    const needsKnowledge = activeView === 'knowledge' && knowledgeSubView === 'raw'
    if (needsKnowledge && !loadedData.knowledge) {
      void loadKnowledgeData().catch((err) => {
        setError(err instanceof Error ? err.message : 'Failed to load workspace knowledge data.')
      })
    }
  }, [activeView, knowledgeSubView, loadKnowledgeData, loadedData.knowledge, snapshot])

  useEffect(() => {
    if (!snapshot) return
    if (activeView === 'graph' && !loadedData.graph) {
      void loadGraphData().catch((err) => {
        setError(err instanceof Error ? err.message : 'Failed to load workspace graph data.')
      })
    }
  }, [activeView, loadGraphData, loadedData.graph, snapshot])

  useEffect(() => {
    if (!snapshot) return
    if (activeView === 'reviews' && !loadedData.reviews) {
      void loadReviewData().catch((err) => {
        setError(err instanceof Error ? err.message : 'Failed to load workspace review data.')
      })
    }
  }, [activeView, loadReviewData, loadedData.reviews, snapshot])

  useEffect(() => {
    if (!snapshot) return
    if (activeView === 'history' && !loadedData.history) {
      void loadRevisionData().catch((err) => {
        setError(err instanceof Error ? err.message : 'Failed to load workspace revision data.')
      })
    }
  }, [activeView, loadRevisionData, loadedData.history, snapshot])

  useEffect(() => {
    if (!snapshot) return
    if (activeView === 'timeline' && !loadedData.timeline) {
      void loadTimelineData().catch((err) => {
        setError(err instanceof Error ? err.message : 'Failed to load workspace timeline data.')
      })
    }
  }, [activeView, loadTimelineData, loadedData.timeline, snapshot])

  useEffect(() => {
    if (!snapshot) return
    if (activeView === 'themes' && !loadedData.themes) {
      void loadThemeData().catch((err) => {
        setError(err instanceof Error ? err.message : 'Failed to load workspace themes.')
      })
    }
  }, [activeView, loadThemeData, loadedData.themes, snapshot])

  useEffect(() => {
    if (!snapshot) return
    if (activeView === 'gaps' && !loadedData.gaps) {
      void loadGapData().catch((err) => {
        setError(err instanceof Error ? err.message : 'Failed to load workspace gaps.')
      })
    }
  }, [activeView, loadGapData, loadedData.gaps, snapshot])

  useEffect(() => {
    if (!snapshot) return
    if (activeView === 'opportunities' && !loadedData.opportunities) {
      void loadOpportunityData().catch((err) => {
        setError(err instanceof Error ? err.message : 'Failed to load workspace opportunities.')
      })
    }
  }, [activeView, loadOpportunityData, loadedData.opportunities, snapshot])

  useEffect(() => {
    if (!snapshot) return
    if (activeView === 'health' && !loadedData.health) {
      void loadHealthData().catch((err) => {
        setError(err instanceof Error ? err.message : 'Failed to load workspace health.')
      })
    }
  }, [activeView, loadHealthData, loadedData.health, snapshot])

  useEffect(() => {
    if (!snapshot) return
    if (activeView === 'field-map' && !loadedData.fieldMap) {
      void loadFieldMapData().catch((err) => {
        setError(err instanceof Error ? err.message : 'Failed to load workspace field map.')
      })
    }
  }, [activeView, loadFieldMapData, loadedData.fieldMap, snapshot])

  useEffect(() => {
    if (!snapshot) return
    if (activeView === 'matrix' && !loadedData.matrix) {
      void loadMatrixData().catch((err) => {
        setError(err instanceof Error ? err.message : 'Failed to load evidence matrix.')
      })
    }
  }, [activeView, loadMatrixData, loadedData.matrix, snapshot])

  useEffect(() => {
    if (!snapshot) {
      return
    }
    if (selectedGraphNodeId && snapshot.graph.nodes.some((node) => node.id === selectedGraphNodeId)) {
      return
    }
    const nextId = snapshot.graph.nodes[0]?.id ?? null
    setSelectedGraphNodeId(nextId)
  }, [selectedGraphNodeId, snapshot])

  const runBusyAction = useCallback(
    async (action: () => Promise<void>) => {
      setIsBusy(true)
      try {
        await action()
        await loadWorkspaceShell()
        if (snapshot?.claims.length || snapshot?.entities.length || snapshot?.synthesis_items.length) {
          await loadKnowledgeData()
        }
        if (snapshot?.graph.nodes.length || snapshot?.editable_edges.length) {
          await loadGraphData()
        }
        if (snapshot?.reviews.length) {
          await loadReviewData()
        }
        if (snapshot?.revisions.length) {
          await loadRevisionData()
        }
        if (snapshot?.timeline.length) {
          await loadTimelineData()
        }
        if (snapshot?.themes) {
          await loadThemeData()
        }
        if (snapshot?.gaps) {
          await loadGapData()
        }
        if (snapshot?.opportunities) {
          await loadOpportunityData()
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : '操作失败。')
      } finally {
        setIsBusy(false)
      }
    },
    [loadGapData, loadGraphData, loadKnowledgeData, loadOpportunityData, loadReviewData, loadRevisionData, loadThemeData, loadTimelineData, loadWorkspaceShell, snapshot],
  )

  const resetEntityEditor = () => {
    setEditingEntityId(null)
    setEntityDraft({ name: '', entity_type: 'concept', summary: '', aliases: '' })
    setIsEntityDialogOpen(false)
  }

  const resetClaimEditor = () => {
    setEditingClaimId(null)
    setClaimDraft({
      title: '',
      body: '',
      claim_type: 'finding',
      stance: 'support',
      importance: '0.7',
      default_resolution: '',
      entity_names: '',
      scope_conditions: '',
      scope_boundary: '',
      scope_population: '',
      scope_notes: '',
    })
    setIsClaimDialogOpen(false)
  }

  const resetEvidenceEditor = () => {
    setEditingEvidenceId(null)
    setEvidenceDraft({
      claim_id: '',
      section_key: 'other',
      section_title: '',
      snippet: '',
      evidence_summary: '',
      page_label: '',
      anchor_kind: 'text',
      context_before: '',
      context_after: '',
      structured_task: '',
      structured_method: '',
      structured_metric: '',
      structured_value: '',
      structured_baseline: '',
      structured_comparator: '',
      structured_dataset: '',
      structured_setting: '',
      structured_limitation: '',
      structured_scope_note: '',
    })
    setIsEvidenceDialogOpen(false)
  }

  const resetSynthesisEditor = () => {
    setEditingSynthesisId(null)
    setSynthesisDraft({ item_type: 'consensus', title: '', summary: '', confidence: '0.7', default_resolution: '', claim_ids: '' })
    setIsSynthesisDialogOpen(false)
  }

  const resetEdgeEditor = () => {
    setEditingEdgeId(null)
    setEdgeDraft({ source_kind: 'entity', source_ref: '', target_kind: 'entity', target_ref: '', relation_type: 'related_to', summary: '', weight: '1' })
    setIsEdgeDialogOpen(false)
  }

  const fillEntityDraft = (entity: MemoryEntity) => {
    setEditingEntityId(entity.id)
    setEntityDraft({
      name: entity.canonical_name,
      entity_type: entity.entity_type,
      summary: entity.summary,
      aliases: '',
    })
    setIsEntityDialogOpen(true)
    setActiveView('knowledge')
  }

  const fillClaimDraft = (claim: MemoryClaim) => {
    setEditingClaimId(claim.id)
    setClaimDraft({
      title: claim.title,
      body: claim.body,
      claim_type: claim.claim_type,
      stance: claim.stance,
      importance: String(claim.importance),
      default_resolution: claim.default_resolution,
      entity_names: claim.entity_names.join('; '),
      scope_conditions: Array.isArray(claim.scope?.conditions) ? claim.scope.conditions.join('; ') : '',
      scope_boundary: typeof claim.scope?.boundary === 'string' ? claim.scope.boundary : '',
      scope_population: typeof claim.scope?.population === 'string' ? claim.scope.population : '',
      scope_notes: typeof claim.scope?.notes === 'string' ? claim.scope.notes : '',
    })
    setIsClaimDialogOpen(true)
    setActiveView('knowledge')
  }

  const fillEvidenceDraft = (evidence: MemoryEvidence) => {
    const structuredSignal = (() => {
      if (evidence.structured_signal && Object.keys(evidence.structured_signal).length > 0) {
        return evidence.structured_signal as Record<string, string>
      }
      try {
        const payload = JSON.parse(evidence.structured_signal_json || '{}')
        return typeof payload === 'object' && payload ? payload as Record<string, string> : {}
      } catch {
        return {}
      }
    })()
    setEditingEvidenceId(evidence.id)
    setEvidenceDraft({
      claim_id: String(evidence.claim_id),
      section_key: evidence.section_key,
      section_title: evidence.section_title,
      snippet: evidence.snippet,
      evidence_summary: evidence.evidence_summary,
      page_label: evidence.page_label,
      anchor_kind: evidence.anchor_kind || 'text',
      context_before: evidence.context_before || '',
      context_after: evidence.context_after || '',
      structured_task: structuredSignal.task || '',
      structured_method: structuredSignal.method || '',
      structured_metric: structuredSignal.metric || '',
      structured_value: structuredSignal.value || '',
      structured_baseline: structuredSignal.baseline || '',
      structured_comparator: structuredSignal.comparator || '',
      structured_dataset: structuredSignal.dataset || '',
      structured_setting: structuredSignal.setting || '',
      structured_limitation: structuredSignal.limitation || '',
      structured_scope_note: structuredSignal.scope_note || '',
    })
    setIsEvidenceDialogOpen(true)
    setActiveView('knowledge')
  }

  const fillSynthesisDraft = (item: MemorySynthesisItem) => {
    setEditingSynthesisId(item.id)
    setSynthesisDraft({
      item_type: item.item_type,
      title: item.title,
      summary: item.summary,
      confidence: String(item.confidence),
      default_resolution: item.default_resolution,
      claim_ids: item.claim_ids.join(', '),
    })
    setIsSynthesisDialogOpen(true)
    setActiveView('knowledge')
  }

  const fillEdgeDraft = (edge: MemoryEditableGraphEdge) => {
    setEditingEdgeId(edge.id)
    setEdgeDraft({
      source_kind: edge.source_kind,
      source_ref: edge.source_ref,
      target_kind: edge.target_kind,
      target_ref: edge.target_ref,
      relation_type: edge.relation_type,
      summary: edge.summary,
      weight: String(edge.weight),
    })
    setIsEdgeDialogOpen(true)
    setActiveView('graph')
  }

  const handleEntitySubmit = async () => {
    const payload: MemoryEntityInput = {
      name: entityDraft.name.trim(),
      entity_type: entityDraft.entity_type,
      summary: entityDraft.summary.trim(),
      aliases: parseCsv(entityDraft.aliases),
    }
    await runBusyAction(async () => {
      if (editingEntityId) {
        await updateWorkspaceEntity(numericProfileId, editingEntityId, payload)
      } else {
        await createWorkspaceEntity(numericProfileId, payload)
      }
      resetEntityEditor()
    })
  }

  const handleClaimSubmit = async () => {
    const payload: MemoryClaimInput = {
      title: claimDraft.title.trim(),
      body: claimDraft.body.trim(),
      claim_type: claimDraft.claim_type,
      stance: claimDraft.stance,
      importance: Number(claimDraft.importance) || 0.5,
      default_resolution: claimDraft.default_resolution.trim(),
      scope: {
        conditions: parseCsv(claimDraft.scope_conditions),
        boundary: claimDraft.scope_boundary.trim(),
        population: claimDraft.scope_population.trim(),
        notes: claimDraft.scope_notes.trim(),
      },
      entity_names: parseCsv(claimDraft.entity_names),
    }
    await runBusyAction(async () => {
      if (editingClaimId) {
        await updateWorkspaceClaim(numericProfileId, editingClaimId, payload)
      } else {
        await createWorkspaceClaim(numericProfileId, payload)
      }
      resetClaimEditor()
    })
  }

  const handleEvidenceSubmit = async () => {
    const payload: MemoryEvidenceInput = {
      claim_id: Number(evidenceDraft.claim_id),
      section_key: evidenceDraft.section_key,
      section_title: evidenceDraft.section_title.trim(),
      snippet: evidenceDraft.snippet.trim(),
      evidence_summary: evidenceDraft.evidence_summary.trim(),
      page_label: evidenceDraft.page_label.trim(),
      anchor_kind: evidenceDraft.anchor_kind,
      context_before: evidenceDraft.context_before.trim(),
      context_after: evidenceDraft.context_after.trim(),
      structured_signal: {
        task: evidenceDraft.structured_task.trim(),
        method: evidenceDraft.structured_method.trim(),
        dataset: evidenceDraft.structured_dataset.trim(),
        metric: evidenceDraft.structured_metric.trim(),
        value: evidenceDraft.structured_value.trim(),
        baseline: evidenceDraft.structured_baseline.trim(),
        comparator: evidenceDraft.structured_comparator.trim(),
        setting: evidenceDraft.structured_setting.trim(),
        limitation: evidenceDraft.structured_limitation.trim(),
        scope_note: evidenceDraft.structured_scope_note.trim(),
      },
    }
    await runBusyAction(async () => {
      if (editingEvidenceId) {
        await updateWorkspaceEvidence(numericProfileId, editingEvidenceId, payload)
      } else {
        await createWorkspaceEvidence(numericProfileId, payload)
      }
      resetEvidenceEditor()
    })
  }

  const handleSynthesisSubmit = async () => {
    const payload: MemorySynthesisInput = {
      item_type: synthesisDraft.item_type,
      title: synthesisDraft.title.trim(),
      summary: synthesisDraft.summary.trim(),
      confidence: Number(synthesisDraft.confidence) || 0.5,
      default_resolution: synthesisDraft.default_resolution.trim(),
      claim_ids: parseIdCsv(synthesisDraft.claim_ids),
    }
    await runBusyAction(async () => {
      if (editingSynthesisId) {
        await updateWorkspaceSynthesis(numericProfileId, editingSynthesisId, payload)
      } else {
        await createWorkspaceSynthesis(numericProfileId, payload)
      }
      resetSynthesisEditor()
    })
  }

  const handleEdgeSubmit = async () => {
    const payload: MemoryGraphEdgeInput = {
      source_kind: edgeDraft.source_kind,
      source_ref: edgeDraft.source_ref.trim(),
      target_kind: edgeDraft.target_kind,
      target_ref: edgeDraft.target_ref.trim(),
      relation_type: edgeDraft.relation_type.trim(),
      summary: edgeDraft.summary.trim(),
      weight: Number(edgeDraft.weight) || 1,
    }
    await runBusyAction(async () => {
      if (editingEdgeId) {
        await updateWorkspaceGraphEdge(numericProfileId, editingEdgeId, payload)
      } else {
        await createWorkspaceGraphEdge(numericProfileId, payload)
      }
      resetEdgeEditor()
    })
  }

  const handleDelete = async (message: string, action: () => Promise<unknown>) => {
    if (!window.confirm(message)) {
      return
    }
    await runBusyAction(async () => {
      await action()
    })
  }

  const jumpToRawView = useCallback((focus: RawKnowledgeFocus) => {
    setActiveView('knowledge')
    setKnowledgeSubView('raw')
    setRawFocus(focus)
  }, [])

  const timelineItems = useMemo(() => snapshot?.timeline ?? [], [snapshot?.timeline])
  const synthesisItems = useMemo(() => snapshot?.synthesis_items ?? [], [snapshot?.synthesis_items])
  const claimItems = useMemo(() => snapshot?.claims ?? [], [snapshot?.claims])
  const entityItems = useMemo(() => snapshot?.entities ?? [], [snapshot?.entities])
  const evidenceItems = useMemo(() => snapshot?.evidence_fragments ?? [], [snapshot?.evidence_fragments])
  const graphItems = useMemo(() => snapshot?.graph ?? { nodes: [], edges: [] }, [snapshot?.graph])
  const reviewItems = useMemo(() => snapshot?.reviews ?? [], [snapshot?.reviews])
  const themeSnapshot: ThemeSnapshot | null = snapshot?.themes ?? null
  const gapSnapshot: GapSnapshot | null = snapshot?.gaps ?? null
  const opportunitySnapshot: OpportunitySnapshot | null = snapshot?.opportunities ?? null
  const healthSnapshot: MemoryHealth | null = snapshot?.health ?? null
  const fieldMapSnapshot: FieldMapSnapshot | null = snapshot?.field_map ?? null
  const evidenceMatrixSnapshot: EvidenceMatrixSnapshot | null = snapshot?.evidence_matrix ?? null
  const curated = snapshot?.curated ?? {
    domain_digest: [],
    priority_claims: [],
    active_conflicts: [],
    source_bundles: [],
    entity_clusters: [],
  }
  const activeConflictCount = curated.active_conflicts.length
  const suggestedConflictCount = curated.active_conflicts.filter((item) => item.has_suggested_payload).length
  const synthesisTypeGuides = [
    { type: 'consensus', label: '共识' },
    { type: 'debate', label: '争议' },
    { type: 'evolution', label: '方法演化' },
    { type: 'open_question', label: '开放问题' },
  ]
  const presentSynthesisTypes = new Set(curated.domain_digest.map((section) => section.section_type))
  const missingSynthesisTypeLabels = synthesisTypeGuides
    .filter((item) => !presentSynthesisTypes.has(item.type))
    .map((item) => item.label)
  const pendingReviews = reviewItems.filter((item) => item.status === 'pending')
  const timelineSections = useMemo(() => {
    const sections: Array<{ key: string; anchor: MemoryTimelineItem | null; items: MemoryTimelineItem[] }> = []
    const sectionMap = new Map<string, { key: string; anchor: MemoryTimelineItem | null; items: MemoryTimelineItem[] }>()

    const ensureSection = (key: string) => {
      const existing = sectionMap.get(key)
      if (existing) {
        return existing
      }
      const created = { key, anchor: null, items: [] as MemoryTimelineItem[] }
      sectionMap.set(key, created)
      sections.push(created)
      return created
    }

    for (const item of timelineItems) {
      const key = item.source_job_id || `general:${item.bundle_label || item.target_type || item.item_type}`
      const section = ensureSection(key)
      if (item.item_type === 'paper_ingested' && !section.anchor) {
        section.anchor = item
        continue
      }
      section.items.push(item)
    }

    for (const section of sections) {
      section.items.sort((a, b) => b.timestamp - a.timestamp)
    }

    return sections.sort((a, b) => {
      const aTimestamp = a.anchor?.timestamp ?? a.items[0]?.timestamp ?? 0
      const bTimestamp = b.anchor?.timestamp ?? b.items[0]?.timestamp ?? 0
      return bTimestamp - aTimestamp
    })
  }, [timelineItems])
  const filteredSynthesisItems = useMemo(() => {
    if (!rawFocus?.synthesisType) {
      return synthesisItems
    }
    return synthesisItems.filter((item) => item.item_type === rawFocus.synthesisType)
  }, [rawFocus, synthesisItems])
  const filteredClaims = useMemo(() => {
    if (!rawFocus) {
      return claimItems
    }
    if (typeof rawFocus.claimId === 'number') {
      return claimItems.filter((claim) => claim.id === rawFocus.claimId)
    }
    if (rawFocus.paperId) {
      return claimItems.filter((claim) => claim.paper_id === rawFocus.paperId)
    }
    return claimItems
  }, [rawFocus, claimItems])
  const filteredEntities = useMemo(() => {
    if (!rawFocus?.entityType) {
      return entityItems
    }
    return entityItems.filter((entity) => entity.entity_type === rawFocus.entityType)
  }, [rawFocus, entityItems])
  const filteredEvidence = useMemo(() => {
    if (!rawFocus) {
      return evidenceItems
    }
    if (typeof rawFocus.claimId === 'number') {
      return evidenceItems.filter((evidence) => evidence.claim_id === rawFocus.claimId)
    }
    if (rawFocus.paperId) {
      return evidenceItems.filter((evidence) => evidence.paper_id === rawFocus.paperId)
    }
    return evidenceItems
  }, [rawFocus, evidenceItems])
  const graphNodes = [...graphItems.nodes].sort((a, b) => b.degree - a.degree || a.label.localeCompare(b.label))
  const selectedGraphNode = graphNodes.find((node) => node.id === selectedGraphNodeId) ?? null
  const focusedGraphEdges = selectedGraphNode
    ? graphItems.edges.filter((edge) => edge.source_id === selectedGraphNode.id || edge.target_id === selectedGraphNode.id)
    : graphItems.edges

  if (!profileId || Number.isNaN(numericProfileId)) {
    return <p className="max-w-6xl mx-auto text-sm text-muted-foreground">No profile selected.</p>
  }

  if (error && (!detail || !snapshot)) {
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

  if (!detail || !snapshot) {
    return <p className="max-w-6xl mx-auto text-sm text-muted-foreground">Loading memory workspace...</p>
  }

  const tabGroups: Array<{ label: string; description: string; tabs: WorkspaceTab[] }> = [
    {
      label: '先读懂领域',
      description: '先从整理摘要、领域结构和机会入口建立整体理解。',
      tabs: [
        { id: 'knowledge', label: '知识库', icon: LibraryBig },
        { id: 'field-map', label: '领域地图', icon: Compass },
        { id: 'themes', label: '主题结构', icon: Sparkles },
        { id: 'opportunities', label: '研究机会', icon: FileSearch },
      ],
    },
    {
      label: '查证据与问题',
      description: '核对证据矩阵、知识空白、健康问题和待裁决冲突。',
      tabs: [
        { id: 'matrix', label: '证据矩阵', icon: Database },
        { id: 'gaps', label: '知识空白', icon: AlertTriangle },
        { id: 'health', label: '健康度', icon: CheckCircle2 },
        { id: 'reviews', label: '冲突队列', icon: AlertTriangle },
      ],
    },
    {
      label: '高级审计',
      description: '查看图谱、时间演化和修订记录，用于深度排查。',
      tabs: [
        { id: 'graph', label: '图谱', icon: Network },
        { id: 'timeline', label: '时间线', icon: GitBranch },
        { id: 'history', label: '修订历史', icon: History },
      ],
    },
  ]

  return (
    <LocalizedTextLanguageProvider language={contentLanguage}>
      <div className="max-w-6xl mx-auto space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-2">
          <Link to={`/profiles/${numericProfileId}`}>
            <Button variant="ghost" size="sm">
              <ArrowLeft className="mr-1 h-4 w-4" />
              返回概览
            </Button>
          </Link>
          <div className="space-y-1">
            <h2 className="text-2xl font-bold">{detail.name} · 记忆工作台</h2>
            <p className="text-sm text-muted-foreground">{detail.description || '暂无描述。'}</p>
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
          <Button
            variant="outline"
            disabled={isBusy}
            onClick={() => runBusyAction(async () => {
              await rebuildProfileMemory(numericProfileId)
            })}
          >
            <RefreshCw className="mr-2 h-4 w-4" />
            重建认知
          </Button>
          <Button variant="outline" disabled={isBusy} onClick={() => runBusyAction(async () => {})}>
            <RefreshCw className="mr-2 h-4 w-4" />
            刷新
          </Button>
        </div>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        <OverviewCard title="来源论文" value={snapshot.overview.paper_source_count} hint="当前仍然有效的记忆写回来源" />
        <OverviewCard title="Claims + 认知" value={snapshot.overview.claim_count + snapshot.overview.synthesis_count} hint="驱动 Agent 推理的中层与高层结构化记忆" />
        <OverviewCard title="待处理冲突" value={snapshot.overview.pending_review_count} hint="AI 默认解仍等待人工确认" />
        <OverviewCard title="图谱规模" value={snapshot.overview.graph_node_count + snapshot.overview.graph_edge_count} hint="当前 profile 中的节点与边总量" />
      </div>

      <Card size="sm">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">
            <SectionTitle
              title="工作台视图"
              hint="Knowledge Base 是结构化知识对象，Graph 是可视化关系图，Timeline 追踪演化，Conflict Queue 管理待人工裁决项，Revision History 用于回溯修改。"
            />
          </CardTitle>
          <CardDescription>
            使用页面右上角的统一语言按钮切换整页显示；编辑时仍然只修改英文 source-of-truth。
          </CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3 xl:grid-cols-3">
          {tabGroups.map((group) => (
            <div key={group.label} className="rounded-xl border bg-muted/20 p-3">
              <div className="space-y-1">
                <p className="text-sm font-medium">{group.label}</p>
                <p className="text-xs leading-5 text-muted-foreground">{group.description}</p>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {group.tabs.map((tab) => {
                  const Icon = tab.icon
                  const active = activeView === tab.id
                  return (
                    <Button key={tab.id} variant={active ? 'default' : 'outline'} size="sm" onClick={() => setActiveView(tab.id)}>
                      <Icon className="mr-2 h-4 w-4" />
                      {tab.label}
                    </Button>
                  )
                })}
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      {activeView === 'health' && (
        <Card>
          <CardHeader>
            <CardTitle>
              <SectionTitle
                title="记忆健康度"
                icon={<CheckCircle2 className="h-4 w-4" />}
                hint="健康度是基于数据库的确定性检查，不调用 LLM；它用来发现无证据、证据薄弱、争议、适用范围缺失和派生产物过期。"
              />
            </CardTitle>
            <CardDescription>这些指标只用于治理和审计，不会直接改变事实层记忆。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {!healthSnapshot ? (
              <EmptyState title="正在加载记忆健康度..." />
            ) : (
              <Fragment>
                <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                  <OverviewCard title="状态" value={healthSnapshot.status} hint="good / attention / critical" />
                  <OverviewCard title="健康分" value={healthSnapshot.score.toFixed(2)} hint="1.0 表示当前未发现明显治理问题" />
                  <OverviewCard title="问题数" value={healthSnapshot.issues.reduce((sum, item) => sum + item.count, 0)} hint="所有健康检查项的计数总和" />
                </div>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
                  {Object.entries(healthSnapshot.summary).map(([key, value]) => (
                    <div key={key} className="rounded-xl border bg-muted/20 p-3">
                      <p className="text-xs text-muted-foreground">{key}</p>
                      <p className="mt-1 text-xl font-semibold">{value}</p>
                    </div>
                  ))}
                </div>
                <div className="space-y-3">
                  {healthSnapshot.issues.length === 0 ? (
                    <EmptyState title="当前没有发现明显健康问题。" />
                  ) : healthSnapshot.issues.map((issue) => (
                    <div key={issue.issue_type} className="rounded-xl border p-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={issue.severity === 'high' ? 'destructive' : 'outline'}>{issue.severity}</Badge>
                        <Badge variant="secondary">{issue.count}</Badge>
                        <Badge variant="outline">{issue.target_type}</Badge>
                      </div>
                      <LocalizedTextBlock localized={issue.title_localized} className="mt-2" textClassName="font-medium" />
                      {issue.target_ids.length > 0 ? (
                        <p className="mt-2 break-all text-xs text-muted-foreground">Targets: {issue.target_ids.join(', ')}</p>
                      ) : null}
                    </div>
                  ))}
                </div>
              </Fragment>
            )}
          </CardContent>
        </Card>
      )}

      {activeView === 'field-map' && (
        <Card>
          <CardHeader>
            <CardTitle>
              <SectionTitle
                title="领域认知地图"
                icon={<Compass className="h-4 w-4" />}
                hint="领域地图是从主题、主张关系和审阅状态派生的只读视图，用来说明这个 profile 的领域结构，而不是新的事实源。"
              />
            </CardTitle>
            <CardDescription>点击簇里的 Claim 数量后，可回到原始对象视图继续审阅。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            {!fieldMapSnapshot ? (
              <EmptyState title="正在加载领域地图..." />
            ) : fieldMapSnapshot.clusters.length === 0 ? (
              <EmptyState title="还没有足够的 claims 形成领域地图。" />
            ) : (
              <Fragment>
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                  {fieldMapSnapshot.clusters.map((cluster) => (
                    <div key={cluster.cluster_key} className="rounded-xl border bg-background p-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="secondary">{cluster.cluster_type}</Badge>
                        <Badge variant="outline">{cluster.maturity}</Badge>
                        {cluster.controversy_count > 0 ? <Badge variant="destructive">争议 {cluster.controversy_count}</Badge> : null}
                      </div>
                      <LocalizedTextBlock localized={cluster.title_localized} className="mt-3" textClassName="font-medium text-base" />
                      <LocalizedTextBlock localized={cluster.summary_localized} className="mt-2 text-sm text-muted-foreground" />
                      <div className="mt-3 flex flex-wrap gap-2">
                        <Badge variant="outline">Papers {cluster.paper_count}</Badge>
                        <Badge variant="outline">Claims {cluster.claim_count}</Badge>
                        <Badge variant="outline">Evidence {cluster.evidence_count}</Badge>
                      </div>
                      <div className="mt-3 flex justify-end">
                        <Button size="sm" variant="ghost" onClick={() => jumpToRawView({ section: 'claims', label: `${cluster.title || cluster.cluster_key} · 原始 Claims` })}>
                          查看原始 Claims
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
                <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                  <div className="rounded-xl border p-4">
                    <p className="font-medium">簇关系</p>
                    <div className="mt-3 space-y-2">
                      {fieldMapSnapshot.links.length === 0 ? <EmptyState title="暂未发现跨簇关系。" /> : fieldMapSnapshot.links.slice(0, 12).map((link, index) => (
                        <div key={`${link.source_cluster_key}-${link.target_cluster_key}-${index}`} className="rounded-lg border bg-muted/20 p-3 text-sm">
                          <p className="break-all font-medium">{link.source_cluster_key} → {link.target_cluster_key}</p>
                          <p className="mt-1 text-xs text-muted-foreground">{link.relation_type} · weight {link.weight}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="rounded-xl border p-4">
                    <p className="font-medium">阅读入口</p>
                    <div className="mt-3 space-y-2">
                      {fieldMapSnapshot.entry_points.map((entry) => (
                        <div key={entry.audience} className="rounded-lg border bg-muted/20 p-3 text-sm">
                          <p className="font-medium">{contentLanguage === 'zh' ? (entry.title_zh || entry.title) : entry.title}</p>
                          <p className="mt-1 text-muted-foreground">{contentLanguage === 'zh' ? (entry.rationale_zh || entry.rationale) : entry.rationale}</p>
                          <p className="mt-2 break-all text-xs text-muted-foreground">{entry.cluster_keys.filter(Boolean).join(', ') || '暂无推荐簇'}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </Fragment>
            )}
          </CardContent>
        </Card>
      )}

      {activeView === 'matrix' && (
        <Card>
          <CardHeader>
            <CardTitle>
              <SectionTitle
                title="证据矩阵"
                icon={<Database className="h-4 w-4" />}
                hint="证据矩阵把同任务、同数据集、同指标下的证据放在一起比较；缺少方法、结果、设置或边界时会显式标记不完整。"
              />
            </CardTitle>
            <CardDescription>矩阵是只读派生视图，修改证据请回到知识库的原始对象视图。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {!evidenceMatrixSnapshot ? (
              <EmptyState title="正在加载证据矩阵..." />
            ) : evidenceMatrixSnapshot.rows.length === 0 ? (
              <EmptyState title="还没有可聚合的证据片段。" />
            ) : (
              <Fragment>
                <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                  <OverviewCard title="矩阵行" value={evidenceMatrixSnapshot.row_count} hint="task / dataset / metric 组合数" />
                  <OverviewCard title="证据片段" value={evidenceMatrixSnapshot.evidence_count} hint="参与矩阵聚合的证据数量" />
                  <OverviewCard title="不完整项" value={evidenceMatrixSnapshot.incomplete_count} hint="缺少设置、结果值或适用范围的证据单元" />
                </div>
                <div className="space-y-4">
                  {evidenceMatrixSnapshot.rows.map((row) => (
                    <div key={row.row_key} className="rounded-xl border p-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="secondary">{row.task}</Badge>
                        <Badge variant="outline">{row.dataset}</Badge>
                        <Badge variant="outline">{row.metric}</Badge>
                        {row.incomplete_count > 0 ? <Badge variant="destructive">不完整 {row.incomplete_count}</Badge> : null}
                      </div>
                      <div className="mt-4 overflow-x-auto">
                        <div className="grid min-w-[720px] gap-3">
                          {row.cells.map((cell) => (
                            <div key={cell.evidence_id} className="grid grid-cols-[1.1fr_0.8fr_0.8fr_1fr] gap-3 rounded-lg border bg-muted/20 p-3 text-sm">
                              <div>
                                <LocalizedTextBlock localized={cell.claim_title_localized} textClassName="font-medium" />
                                <p className="mt-1 text-xs text-muted-foreground">Evidence #{cell.evidence_id} · Claim #{cell.claim_id}</p>
                              </div>
                              <div>
                                <p className="text-xs text-muted-foreground">Method</p>
                                <p>{cell.method || '—'}</p>
                              </div>
                              <div>
                                <p className="text-xs text-muted-foreground">Result</p>
                                <p>{cell.value || '—'}</p>
                                {cell.baseline ? <p className="text-xs text-muted-foreground">vs {cell.baseline}</p> : null}
                              </div>
                              <div>
                                <p className="text-xs text-muted-foreground">Scope</p>
                                <p className="line-clamp-2">{cell.scope_note || cell.setting || '—'}</p>
                                {cell.incomplete_fields.length > 0 ? <p className="mt-1 text-xs text-destructive">Missing: {cell.incomplete_fields.join(', ')}</p> : null}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </Fragment>
            )}
          </CardContent>
        </Card>
      )}

      <EditDialog
        open={isEntityDialogOpen}
        onOpenChange={(open) => {
          setIsEntityDialogOpen(open)
          if (!open) {
            resetEntityEditor()
          }
        }}
        title={editingEntityId ? '编辑实体英文内容' : '新建实体（英文）'}
        description="这里输入英文 source-of-truth。保存后会自动生成中文展示文本。"
        footer={
          <Fragment>
            {editingEntityId ? <Button variant="outline" disabled={isBusy} onClick={resetEntityEditor}>取消</Button> : null}
            <Button disabled={isBusy} onClick={handleEntitySubmit}>
              <Save className="mr-2 h-4 w-4" />
              {editingEntityId ? '保存实体' : '创建实体'}
            </Button>
          </Fragment>
        }
      >
        <div className="space-y-2">
          <Input placeholder="Entity name (English)" value={entityDraft.name} onChange={(e) => setEntityDraft((prev) => ({ ...prev, name: e.target.value }))} />
          <Input placeholder="Entity type" value={entityDraft.entity_type} onChange={(e) => setEntityDraft((prev) => ({ ...prev, entity_type: e.target.value }))} />
          <textarea className={textareaClass} placeholder="Summary (English)" value={entityDraft.summary} onChange={(e) => setEntityDraft((prev) => ({ ...prev, summary: e.target.value }))} />
          <Input placeholder="Aliases, separated by semicolons" value={entityDraft.aliases} onChange={(e) => setEntityDraft((prev) => ({ ...prev, aliases: e.target.value }))} />
        </div>
      </EditDialog>

      <EditDialog
        open={isClaimDialogOpen}
        onOpenChange={(open) => {
          setIsClaimDialogOpen(open)
          if (!open) {
            resetClaimEditor()
          }
        }}
        title={editingClaimId ? '编辑 Claim 英文内容' : '新建 Claim（英文）'}
        description="Claim 的英文内容会被 Agent 直接消费，中文版本仅作为展示层自动生成。"
        footer={
          <Fragment>
            {editingClaimId ? <Button variant="outline" disabled={isBusy} onClick={resetClaimEditor}>取消</Button> : null}
            <Button disabled={isBusy} onClick={handleClaimSubmit}>
              <Save className="mr-2 h-4 w-4" />
              {editingClaimId ? '保存 Claim' : '创建 Claim'}
            </Button>
          </Fragment>
        }
      >
        <div className="space-y-2">
          <select className={selectClass} value={claimDraft.claim_type} onChange={(e) => setClaimDraft((prev) => ({ ...prev, claim_type: e.target.value }))}>
            <option value="finding">finding</option>
            <option value="comparison">comparison</option>
            <option value="limitation">limitation</option>
            <option value="hypothesis">hypothesis</option>
            <option value="open_question">open_question</option>
          </select>
          <select className={selectClass} value={claimDraft.stance} onChange={(e) => setClaimDraft((prev) => ({ ...prev, stance: e.target.value }))}>
            <option value="support">support</option>
            <option value="oppose">oppose</option>
            <option value="mixed">mixed</option>
            <option value="open">open</option>
          </select>
          <Input placeholder="Title (English)" value={claimDraft.title} onChange={(e) => setClaimDraft((prev) => ({ ...prev, title: e.target.value }))} />
          <textarea className={textareaClass} placeholder="Claim body (English)" value={claimDraft.body} onChange={(e) => setClaimDraft((prev) => ({ ...prev, body: e.target.value }))} />
          <textarea className={textareaClass} placeholder="Default resolution used by the agent" value={claimDraft.default_resolution} onChange={(e) => setClaimDraft((prev) => ({ ...prev, default_resolution: e.target.value }))} />
          <Input placeholder="Importance (0-1)" value={claimDraft.importance} onChange={(e) => setClaimDraft((prev) => ({ ...prev, importance: e.target.value }))} />
          <Input placeholder="Entity names, separated by semicolons" value={claimDraft.entity_names} onChange={(e) => setClaimDraft((prev) => ({ ...prev, entity_names: e.target.value }))} />
          <Input placeholder="Scope conditions, separated by semicolons" value={claimDraft.scope_conditions} onChange={(e) => setClaimDraft((prev) => ({ ...prev, scope_conditions: e.target.value }))} />
          <Input placeholder="Scope boundary" value={claimDraft.scope_boundary} onChange={(e) => setClaimDraft((prev) => ({ ...prev, scope_boundary: e.target.value }))} />
          <Input placeholder="Scope population / data regime" value={claimDraft.scope_population} onChange={(e) => setClaimDraft((prev) => ({ ...prev, scope_population: e.target.value }))} />
          <textarea className={textareaClass} placeholder="Scope notes" value={claimDraft.scope_notes} onChange={(e) => setClaimDraft((prev) => ({ ...prev, scope_notes: e.target.value }))} />
        </div>
      </EditDialog>

      <EditDialog
        open={isEvidenceDialogOpen}
        onOpenChange={(open) => {
          setIsEvidenceDialogOpen(open)
          if (!open) {
            resetEvidenceEditor()
          }
        }}
        title={editingEvidenceId ? '编辑证据英文内容' : '新建证据（英文）'}
        description="证据原文与摘要都以英文保存，中文仅作为阅读辅助。"
        footer={
          <Fragment>
            {editingEvidenceId ? <Button variant="outline" disabled={isBusy} onClick={resetEvidenceEditor}>取消</Button> : null}
            <Button disabled={isBusy} onClick={handleEvidenceSubmit}>
              <Save className="mr-2 h-4 w-4" />
              {editingEvidenceId ? '保存证据' : '创建证据'}
            </Button>
          </Fragment>
        }
      >
        <div className="space-y-2">
          <select className={selectClass} value={evidenceDraft.claim_id} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, claim_id: e.target.value }))}>
            <option value="">Select a claim</option>
            {snapshot.claims.map((claim) => (
              <option key={claim.id} value={claim.id}>{claim.id} · {claim.title}</option>
            ))}
          </select>
          <select className={selectClass} value={evidenceDraft.section_key} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, section_key: e.target.value }))}>
            <option value="background">background</option>
            <option value="method">method</option>
            <option value="experiments">experiments</option>
            <option value="ablation">ablation</option>
            <option value="limitations">limitations</option>
            <option value="conclusion">conclusion</option>
            <option value="other">other</option>
          </select>
          <Input placeholder="Section title (English)" value={evidenceDraft.section_title} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, section_title: e.target.value }))} />
          <textarea className={textareaClass} placeholder="Snippet (English)" value={evidenceDraft.snippet} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, snippet: e.target.value }))} />
          <textarea className={textareaClass} placeholder="Evidence summary (English)" value={evidenceDraft.evidence_summary} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, evidence_summary: e.target.value }))} />
          <Input placeholder="Page label / page hint" value={evidenceDraft.page_label} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, page_label: e.target.value }))} />
          <select className={selectClass} value={evidenceDraft.anchor_kind} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, anchor_kind: e.target.value }))}>
            {['text', 'quote', 'result', 'metric', 'table', 'figure', 'claim', 'other'].map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
          <Input placeholder="Short context before" value={evidenceDraft.context_before} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, context_before: e.target.value }))} />
          <Input placeholder="Short context after" value={evidenceDraft.context_after} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, context_after: e.target.value }))} />
          <Input placeholder="Structured task" value={evidenceDraft.structured_task} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, structured_task: e.target.value }))} />
          <Input placeholder="Structured method" value={evidenceDraft.structured_method} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, structured_method: e.target.value }))} />
          <Input placeholder="Structured dataset" value={evidenceDraft.structured_dataset} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, structured_dataset: e.target.value }))} />
          <Input placeholder="Structured metric" value={evidenceDraft.structured_metric} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, structured_metric: e.target.value }))} />
          <Input placeholder="Structured value" value={evidenceDraft.structured_value} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, structured_value: e.target.value }))} />
          <Input placeholder="Structured baseline" value={evidenceDraft.structured_baseline} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, structured_baseline: e.target.value }))} />
          <Input placeholder="Structured comparator" value={evidenceDraft.structured_comparator} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, structured_comparator: e.target.value }))} />
          <Input placeholder="Structured setting" value={evidenceDraft.structured_setting} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, structured_setting: e.target.value }))} />
          <Input placeholder="Structured limitation" value={evidenceDraft.structured_limitation} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, structured_limitation: e.target.value }))} />
          <Input placeholder="Structured scope note" value={evidenceDraft.structured_scope_note} onChange={(e) => setEvidenceDraft((prev) => ({ ...prev, structured_scope_note: e.target.value }))} />
        </div>
      </EditDialog>

      <EditDialog
        open={isSynthesisDialogOpen}
        onOpenChange={(open) => {
          setIsSynthesisDialogOpen(open)
          if (!open) {
            resetSynthesisEditor()
          }
        }}
        title={editingSynthesisId ? '编辑认知项英文内容' : '新建认知项（英文）'}
        description="高层认知对象会优先注入 Agent prompt，因此英文内容是唯一 source-of-truth。"
        footer={
          <Fragment>
            {editingSynthesisId ? <Button variant="outline" disabled={isBusy} onClick={resetSynthesisEditor}>取消</Button> : null}
            <Button disabled={isBusy} onClick={handleSynthesisSubmit}>
              <Save className="mr-2 h-4 w-4" />
              {editingSynthesisId ? '保存认知项' : '创建认知项'}
            </Button>
          </Fragment>
        }
      >
        <div className="space-y-2">
          <select className={selectClass} value={synthesisDraft.item_type} onChange={(e) => setSynthesisDraft((prev) => ({ ...prev, item_type: e.target.value }))}>
            <option value="consensus">consensus</option>
            <option value="debate">debate</option>
            <option value="evolution">evolution</option>
            <option value="open_question">open_question</option>
          </select>
          <Input placeholder="Title (English)" value={synthesisDraft.title} onChange={(e) => setSynthesisDraft((prev) => ({ ...prev, title: e.target.value }))} />
          <textarea className={textareaClass} placeholder="Summary (English)" value={synthesisDraft.summary} onChange={(e) => setSynthesisDraft((prev) => ({ ...prev, summary: e.target.value }))} />
          <textarea className={textareaClass} placeholder="Default resolution (English)" value={synthesisDraft.default_resolution} onChange={(e) => setSynthesisDraft((prev) => ({ ...prev, default_resolution: e.target.value }))} />
          <Input placeholder="Confidence (0-1)" value={synthesisDraft.confidence} onChange={(e) => setSynthesisDraft((prev) => ({ ...prev, confidence: e.target.value }))} />
          <Input placeholder="Linked claim IDs, separated by commas" value={synthesisDraft.claim_ids} onChange={(e) => setSynthesisDraft((prev) => ({ ...prev, claim_ids: e.target.value }))} />
        </div>
      </EditDialog>

      <EditDialog
        open={isEdgeDialogOpen}
        onOpenChange={(open) => {
          setIsEdgeDialogOpen(open)
          if (!open) {
            resetEdgeEditor()
          }
        }}
        title={editingEdgeId ? '编辑图谱边英文内容' : '新建图谱边（英文）'}
        description="关系说明仅编辑英文内容，中文摘要会自动重译。"
        footer={
          <Fragment>
            {editingEdgeId ? <Button variant="outline" disabled={isBusy} onClick={resetEdgeEditor}>取消</Button> : null}
            <Button disabled={isBusy} onClick={handleEdgeSubmit}>
              <Save className="mr-2 h-4 w-4" />
              {editingEdgeId ? '保存图谱边' : '创建图谱边'}
            </Button>
          </Fragment>
        }
      >
        <div className="space-y-2">
          <select className={selectClass} value={edgeDraft.source_kind} onChange={(e) => setEdgeDraft((prev) => ({ ...prev, source_kind: e.target.value }))}>
            <option value="entity">entity</option>
            <option value="claim">claim</option>
            <option value="synthesis">synthesis</option>
            <option value="paper">paper</option>
          </select>
          <Input placeholder="Source ref (English identifier)" value={edgeDraft.source_ref} onChange={(e) => setEdgeDraft((prev) => ({ ...prev, source_ref: e.target.value }))} />
          <select className={selectClass} value={edgeDraft.target_kind} onChange={(e) => setEdgeDraft((prev) => ({ ...prev, target_kind: e.target.value }))}>
            <option value="entity">entity</option>
            <option value="claim">claim</option>
            <option value="synthesis">synthesis</option>
            <option value="paper">paper</option>
          </select>
          <Input placeholder="Target ref" value={edgeDraft.target_ref} onChange={(e) => setEdgeDraft((prev) => ({ ...prev, target_ref: e.target.value }))} />
          <Input placeholder="Relation type" value={edgeDraft.relation_type} onChange={(e) => setEdgeDraft((prev) => ({ ...prev, relation_type: e.target.value }))} />
          <textarea className={textareaClass} placeholder="Relation summary (English)" value={edgeDraft.summary} onChange={(e) => setEdgeDraft((prev) => ({ ...prev, summary: e.target.value }))} />
          <Input placeholder="Weight" value={edgeDraft.weight} onChange={(e) => setEdgeDraft((prev) => ({ ...prev, weight: e.target.value }))} />
        </div>
      </EditDialog>

      {activeView === 'knowledge' && (
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>
                <SectionTitle
                  title="知识库视图"
                  icon={<Sparkles className="h-4 w-4" />}
                  hint="整理摘要视图优先展示当前 profile 中最值得读的领域认知、关键结论、冲突与来源聚合；原始对象视图则保留完整编辑能力。"
                />
              </CardTitle>
              <CardDescription>
                先读整理后的关键信息，再按需切换到原始对象视图进行精细编辑，避免一上来被大量原始条目淹没。
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="inline-flex rounded-xl border p-1">
                  <Button variant={knowledgeSubView === 'curated' ? 'secondary' : 'ghost'} size="sm" onClick={() => setKnowledgeSubView('curated')}>
                    整理摘要
                  </Button>
                  <Button variant={knowledgeSubView === 'raw' ? 'secondary' : 'ghost'} size="sm" onClick={() => setKnowledgeSubView('raw')}>
                    原始对象与编辑
                  </Button>
                </div>
                {knowledgeSubView === 'curated' ? (
                  <Button variant="outline" size="sm" onClick={() => setKnowledgeSubView('raw')}>
                    进入原始对象视图
                  </Button>
                ) : null}
              </div>
              <div className="rounded-xl border bg-muted/30 p-4 text-sm text-muted-foreground">
                {knowledgeSubView === 'curated'
                  ? '建议阅读顺序：先看高层认知摘要，再看待处理冲突数量，随后展开高价值 claims；实体分组与来源 bundle 用于理解概念覆盖和来源回溯。'
                  : '当前模式保留完整原始对象与编辑按钮，适合精细维护 claim、evidence、entity 与 synthesis。'}
              </div>
              {knowledgeSubView === 'raw' && rawFocus ? (
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border/70 bg-background p-3 text-sm">
                  <p className="text-muted-foreground">当前定位：{rawFocus.label}</p>
                  <Button size="sm" variant="outline" onClick={() => setRawFocus(null)}>
                    清除定位
                  </Button>
                </div>
              ) : null}
            </CardContent>
          </Card>

          {knowledgeSubView === 'curated' ? (
            <Fragment>
              <Card className={rawFocus?.section === 'synthesis' ? 'ring-2 ring-ring/35' : undefined}>
                <CardHeader>
                  <CardTitle>
                    <SectionTitle
                      title="领域认知摘要"
                      icon={<Sparkles className="h-4 w-4" />}
                      hint="这里按共识、争议、方法演化、开放问题整理当前 profile 中最值得优先阅读的高层认知。"
                    />
                  </CardTitle>
                  <CardDescription>这里展示的是已经提升为 synthesis 的高层认知，不是全部 claims；每组只保留少量高信号认知项。</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  {curated.domain_digest.length === 0 ? (
                    <EmptyState title="尚未形成可整理的高层认知摘要。" />
                  ) : (
                    <Fragment>
                      {missingSynthesisTypeLabels.length > 0 ? (
                        <div className="rounded-xl border bg-muted/25 p-3 text-sm text-muted-foreground">
                          当前尚未形成 {missingSynthesisTypeLabels.join('、')} 类高层认知。系统不会把普通 claim 自动伪造成共识；这些类型会随着更多论文写回、冲突审阅或手动维护逐步沉淀。
                        </div>
                      ) : null}
                      {curated.domain_digest.map((section) => {
                    const synthTypeMap: Record<string, { color: string; borderColor: string; icon: typeof CheckCircle2 }> = {
                      consensus:     { color: 'text-emerald-600 dark:text-emerald-400', borderColor: 'border-l-emerald-500', icon: CheckCircle2 },
                      debate:        { color: 'text-amber-600 dark:text-amber-400',   borderColor: 'border-l-amber-500',   icon: Swords },
                      evolution:     { color: 'text-sky-600 dark:text-sky-400',       borderColor: 'border-l-sky-500',     icon: TrendingUp },
                      open_question: { color: 'text-violet-600 dark:text-violet-400', borderColor: 'border-l-violet-500', icon: HelpCircle },
                    }
                    const cfg = synthTypeMap[section.section_type] || synthTypeMap.consensus
                    const SectionIcon = cfg.icon
                    return (
                      <div key={section.section_type} className={`rounded-xl border border-l-4 ${cfg.borderColor} p-4`}>
                        <div className="flex flex-wrap items-center gap-2">
                          <SectionIcon className={`h-4 w-4 ${cfg.color}`} />
                          <span className={`text-sm font-medium ${cfg.color}`}>{section.section_label_zh || section.section_label}</span>
                          <Badge variant="outline" className="text-xs">{section.items.length} 条</Badge>
                        </div>
                        <div className="mt-4 space-y-3">
                          {section.items.map((item) => (
                            <div key={item.id} className={`rounded-lg border p-3 ${statusToneClass(item.review_status)}`}>
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <div className="flex flex-wrap items-center gap-2">
                                  <Badge variant="outline" className="text-xs">置信度 {item.confidence.toFixed(2)}</Badge>
                                  {item.claim_count > 0 && <Badge variant="outline" className="text-xs">关联 Claims {item.claim_count}</Badge>}
                                  {renderStateBadge(item.review_status)}
                                  {item.manual_locked && <Badge variant="outline" className="text-xs">manual</Badge>}
                                </div>
                                <Button size="sm" variant="ghost" onClick={() => jumpToRawView({ section: 'synthesis', synthesisType: section.section_type, label: `${section.section_label_zh || section.section_label} · 原始认知项` })}>
                                  查看原始项
                                </Button>
                              </div>
                              <LocalizedTextBlock localized={item.title_localized} className="mt-2" textClassName="font-medium text-base" englishTitle="认知项英文原版" />
                              <LocalizedTextBlock localized={item.summary_localized} className="mt-1 text-muted-foreground text-sm line-clamp-3" />
                            </div>
                          ))}
                        </div>
                      </div>
                    )
                      })}
                    </Fragment>
                  )}
                </CardContent>
              </Card>

              <div className="space-y-6">
                <Card>
                  <CardHeader>
                    <CardTitle>
                      <SectionTitle
                        title="当前冲突与待确认默认解"
                        icon={<AlertTriangle className="h-4 w-4" />}
                        hint="这里展示仍在影响 Agent 默认认知的 pending review，适合优先处理。"
                      />
                    </CardTitle>
                    <CardDescription>摘要页只显示待处理数量；逐条裁决请进入冲突队列，避免这里变成第二个 review 列表。</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className={`flex flex-col gap-4 rounded-xl border p-4 sm:flex-row sm:items-center sm:justify-between ${activeConflictCount > 0 ? 'border-destructive/35 bg-destructive/5' : 'bg-muted/20'}`}>
                      <div className="space-y-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge variant={activeConflictCount > 0 ? 'destructive' : 'outline'}>{activeConflictCount} 条待解决</Badge>
                          {suggestedConflictCount > 0 ? <Badge variant="outline">{suggestedConflictCount} 条含建议更新</Badge> : null}
                        </div>
                        <p className="text-sm text-muted-foreground">
                          {activeConflictCount > 0
                            ? '这些冲突会影响后续 Agent 默认认知。建议优先进入冲突队列逐条确认，而不是在摘要页全量阅读。'
                            : '当前没有待处理冲突；后续如出现矛盾或默认解待确认，会在这里给出数量提醒。'}
                        </p>
                      </div>
                      <Button size="sm" variant="outline" onClick={() => setActiveView('reviews')}>
                        前往冲突队列
                      </Button>
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle>
                      <SectionTitle
                        title="高价值 Claims"
                        icon={<FilePenLine className="h-4 w-4" />}
                        hint="这里按显著性对 claims 排序，并只附上一条最强证据预览，让你先看到最值得关注的结论。"
                      />
                    </CardTitle>
                    <CardDescription>整理摘要模式不展开全部证据链，避免中层对象再次堆积成新的信息墙。</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {curated.priority_claims.length === 0 ? (
                      <EmptyState title="尚未形成可整理的重点 Claims。" />
                    ) : curated.priority_claims.map((claim) => {
                      const isExpanded = expandedClaimId === claim.id
                      return (
                        <div
                          key={claim.id}
                          className={`rounded-xl border transition-all cursor-pointer ${statusToneClass(claim.review_status)} ${isExpanded ? 'p-4' : 'px-4 py-3'}`}
                          onClick={() => setExpandedClaimId(isExpanded ? null : claim.id)}
                        >
                          {/* Collapsed: compact summary line */}
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge variant="outline" className="text-xs font-semibold tabular-nums">{claim.importance.toFixed(2)}</Badge>
                            <span className="text-sm font-medium line-clamp-1 flex-1">
                              {resolveLocalizedText(claim.title_localized, contentLanguage) || claim.title_localized.en}
                            </span>
                            <Badge variant="secondary" className="text-xs">{claim.claim_type}</Badge>
                            <Badge variant="outline" className="text-xs">{claim.stance}</Badge>
                            {claim.evidence_count > 0 && <Badge variant="outline" className="text-xs">{claim.evidence_count} ev</Badge>}
                            {renderStateBadge(claim.review_status)}
                            {claim.manual_locked && <Badge variant="outline" className="text-xs">manual</Badge>}
                          </div>
                          {/* Expanded: full detail */}
                          {isExpanded && (
                            <div className="mt-3 space-y-2" onClick={(e) => e.stopPropagation()}>
                              <LocalizedTextBlock localized={claim.summary_localized} className="text-sm text-muted-foreground" />
                              {claim.top_evidence ? (
                                <div className="rounded-lg border bg-muted/25 p-3">
                                  <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                                    <Badge variant="outline" className="text-xs">{claim.top_evidence.section_key}</Badge>
                                    {claim.top_evidence.page_label ? <Badge variant="outline" className="text-xs">{claim.top_evidence.page_label}</Badge> : null}
                                  </div>
                                  {resolveLocalizedText(claim.top_evidence.evidence_summary_localized, contentLanguage) ? (
                                    <LocalizedTextBlock localized={claim.top_evidence.evidence_summary_localized} className="mt-2 text-sm text-muted-foreground" />
                                  ) : null}
                                  <LocalizedTextBlock localized={claim.top_evidence.snippet_localized} className="mt-2 text-sm" preserveWhitespace emptyText="暂无证据片段预览。" />
                                </div>
                              ) : null}
                              <p className="text-xs text-muted-foreground">
                                {claim.entity_names.length > 0 ? `关联实体：${claim.entity_names.join('、')} · ` : ''}
                                来源论文：{claim.paper_id || 'manual'}
                              </p>
                              <div className="flex justify-end">
                                <Button size="sm" variant="ghost" onClick={() => jumpToRawView({ section: 'claims', claimId: claim.id, label: `Claim #${claim.id} · ${resolveLocalizedText(claim.title_localized, contentLanguage) || claim.title_localized.en}` })}>
                                  定位到原始 Claim
                                </Button>
                              </div>
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </CardContent>
                </Card>
              </div>

              <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
                <Card>
                  <CardHeader>
                    <CardTitle>
                      <SectionTitle
                        title="实体分组"
                        hint="实体是 Memory 图谱中的稳定概念节点，如任务、方法、数据集、指标和模块；这里按类型聚合，帮助判断当前领域记忆的概念覆盖是否均衡。"
                      />
                    </CardTitle>
                    <CardDescription>实体标签后的数字表示关联 claims 数；逐条修正名称、类型或摘要请进入原始对象视图。</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <p className="rounded-xl border bg-muted/20 p-3 text-sm text-muted-foreground">
                      这不是论文作者列表，而是当前 profile 用来连接 claims、证据和图谱的概念节点概览。
                    </p>
                    {curated.entity_clusters.length === 0 ? (
                      <EmptyState title="暂无实体分组。" />
                    ) : curated.entity_clusters.map((cluster) => (
                      <div key={cluster.entity_type} className="rounded-xl border p-4">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge variant="secondary">{cluster.label_zh || cluster.entity_type}</Badge>
                            <Badge variant="outline">{cluster.count} 个</Badge>
                          </div>
                          <Button size="sm" variant="ghost" onClick={() => jumpToRawView({ section: 'entities', entityType: cluster.entity_type, label: `${cluster.label_zh || cluster.entity_type} · 原始实体` })}>
                            查看原始实体
                          </Button>
                        </div>
                        <div className="mt-3 flex flex-wrap gap-2">
                          {cluster.top_entities.map((entity) => (
                            <span key={entity.id} className="inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs text-foreground">
                              <span>{resolveLocalizedText(entity.name_localized, contentLanguage) || entity.name_localized.en || 'Untitled'}</span>
                              <span className="text-muted-foreground">{entity.claim_count}</span>
                              {entity.manual_locked ? <span className="text-muted-foreground">manual</span> : null}
                            </span>
                          ))}
                        </div>
                      </div>
                    ))}
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle>
                      <SectionTitle
                        title="来源 Bundles"
                        icon={<Workflow className="h-4 w-4" />}
                        hint="这里按 job / paper provenance 聚合 memory bundle，帮助你从‘这篇论文带来了什么’而不是‘对象类型列表’去理解记忆。"
                      />
                    </CardTitle>
                    <CardDescription>这能更自然地支撑回溯、审计和按来源删除 memory bundle。</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {curated.source_bundles.length === 0 ? (
                      <EmptyState title="暂无来源 bundle。" />
                    ) : curated.source_bundles.map((bundle) => (
                      <div key={bundle.job_id} className="rounded-xl border p-4">
                        <p className="font-medium">{bundle.paper_title || bundle.paper_id || bundle.job_id}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          Job: {bundle.job_id}
                          {bundle.paper_id ? ` · Paper: ${bundle.paper_id}` : ''}
                          {bundle.created_at ? ` · 写入时间：${formatDate(bundle.created_at)}` : ''}
                        </p>
                        <div className="mt-3 flex flex-wrap gap-2">
                          <Badge variant="outline">Claims {bundle.claim_count}</Badge>
                          <Badge variant="outline">Entities {bundle.entity_count}</Badge>
                          <Badge variant="outline">Synthesis {bundle.synthesis_count}</Badge>
                        </div>
                        <div className="mt-3 flex justify-end">
                          <div className="flex flex-wrap gap-2">
                            <Button size="sm" variant="ghost" onClick={() => jumpToRawView({ section: 'claims', paperId: bundle.paper_id, label: `${bundle.paper_title || bundle.paper_id || bundle.job_id} · 原始 Claims/Evidence` })}>
                              查看原始 Claims / Evidence
                            </Button>
                            {bundle.paper_id && (
                              <Button
                                size="sm"
                                variant="destructive"
                                onClick={() => handleDelete(`Delete all profile memory for paper “${bundle.paper_title || bundle.paper_id}”?\n\nThis removes every writeback in this profile whose source paper id is ${bundle.paper_id}.`, () => deleteProfilePaperMemory(numericProfileId, bundle.paper_id))}
                              >
                                <Trash2 className="mr-1 h-4 w-4" />
                                从 Profile 删除这篇论文
                              </Button>
                            )}
                          </div>
                        </div>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              </div>
            </Fragment>
          ) : (
            <Fragment>
              <Card>
                <CardHeader>
                  <CardTitle>
                    <SectionTitle
                      title="高层领域认知"
                      icon={<Sparkles className="h-4 w-4" />}
                      hint="这里承载领域级认知：共识、争议、方法演化、开放问题。它们会被 Agent 优先消费，也是人类理解该领域最重要的入口。"
                    />
                  </CardTitle>
                  <CardDescription>
                    通过页面右上角的统一语言按钮切换整页中英显示；如需修改，请通过英文编辑入口完成。
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex justify-end">
                    <Button variant="outline" onClick={() => setIsSynthesisDialogOpen(true)}>
                      <Save className="mr-2 h-4 w-4" />
                      新建认知项
                    </Button>
                  </div>
                  {filteredSynthesisItems.length === 0 ? (
                    <EmptyState title="尚未沉淀高层认知。" />
                  ) : filteredSynthesisItems.map((item) => (
                    <div key={item.id} className={`rounded-xl border p-4 ${statusToneClass(item.review_status)}`}>
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0 flex-1 space-y-2">
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge variant="secondary">{item.item_type}</Badge>
                            {renderStateBadge(item.review_status)}
                            {item.manual_locked && <Badge variant="outline">manual</Badge>}
                          </div>
                          <LocalizedTextBlock localized={item.title_localized} textClassName="font-medium text-base" englishTitle="认知项英文原版" />
                          <LocalizedTextBlock localized={item.default_resolution_localized.en || item.summary_localized.en ? item.default_resolution_localized : item.summary_localized} className="text-muted-foreground" />
                          {item.claim_ids.length > 0 && <p className="text-xs text-muted-foreground">关联 Claim：{item.claim_ids.join(', ')}</p>}
                        </div>
                        <div className="flex flex-wrap gap-2">
                          <Button size="sm" variant="outline" onClick={() => fillSynthesisDraft(item)}>编辑英文</Button>
                          <Button size="sm" variant="destructive" onClick={() => handleDelete(`Delete synthesis item “${item.title}”?`, () => deleteWorkspaceSynthesis(numericProfileId, item.id))}>
                            <Trash2 className="mr-1 h-3.5 w-3.5" />
                            删除
                          </Button>
                        </div>
                      </div>
                    </div>
                  ))}
                </CardContent>
              </Card>

              <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
                <Card className={rawFocus?.section === 'claims' ? 'ring-2 ring-ring/35' : undefined}>
                  <CardHeader>
                    <CardTitle>
                      <SectionTitle
                        title="Claims"
                        icon={<FilePenLine className="h-4 w-4" />}
                        hint="Claims 是中层知识对象：结论、比较、局限、假设等。它们必须有证据支撑，并且会被上层认知和冲突队列复用。"
                      />
                    </CardTitle>
                    <CardDescription>
                      使用整页语言切换浏览结论摘要；如需修改，请通过英文 source-of-truth 编辑入口完成。
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="flex justify-end">
                      <Button variant="outline" onClick={() => setIsClaimDialogOpen(true)}>
                        <Save className="mr-2 h-4 w-4" />
                        新建 Claim
                      </Button>
                    </div>
                    <div className="space-y-3">
                      {filteredClaims.length === 0 ? (
                        <EmptyState title="No claims stored yet." />
                      ) : filteredClaims.map((claim) => (
                        <div key={claim.id} className={`rounded-xl border p-4 ${statusToneClass(claim.review_status || claim.status)}`}>
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div className="min-w-0 flex-1 space-y-2">
                              <div className="flex flex-wrap gap-2">
                                <Badge variant="secondary">{claim.claim_type}</Badge>
                                <Badge variant="outline">{claim.stance}</Badge>
                                {renderStateBadge(claim.review_status || claim.status)}
                                {claim.manual_locked && <Badge variant="outline">manual</Badge>}
                              </div>
                              <LocalizedTextBlock localized={claim.title_localized} textClassName="font-medium text-base" englishTitle="Claim 英文原版" />
                              <LocalizedTextBlock localized={claim.default_resolution_localized.en || claim.body_localized.en ? claim.default_resolution_localized : claim.body_localized} className="text-muted-foreground" />
                              {claim.entity_names.length > 0 && <p className="text-xs text-muted-foreground">关联实体：{claim.entity_names.join('、')}</p>}
                              <p className="text-xs text-muted-foreground">证据数：{claim.evidence_count} · Paper: {claim.paper_id || 'manual'}</p>
                            </div>
                            <div className="flex flex-wrap gap-2">
                              <Button size="sm" variant="outline" onClick={() => fillClaimDraft(claim)}>编辑英文</Button>
                              <Button size="sm" variant="destructive" onClick={() => handleDelete(`Delete claim “${claim.title}”?`, () => deleteWorkspaceClaim(numericProfileId, claim.id))}>
                                <Trash2 className="mr-1 h-3.5 w-3.5" />
                                删除
                              </Button>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>

                <Card className={rawFocus?.section === 'entities' ? 'ring-2 ring-ring/35' : undefined}>
                  <CardHeader>
                    <CardTitle>
                      <SectionTitle
                        title="Entities"
                        hint="Entities 是图谱中的稳定节点，例如任务、方法、模块、数据集、指标和概念。Claims 与图谱边会围绕它们组织。"
                      />
                    </CardTitle>
                    <CardDescription>
                      使用整页语言切换查看概念节点名称与摘要。
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="flex justify-end">
                      <Button variant="outline" onClick={() => setIsEntityDialogOpen(true)}>
                        <Save className="mr-2 h-4 w-4" />
                        新建实体
                      </Button>
                    </div>
                    <div className="space-y-3">
                      {filteredEntities.length === 0 ? (
                        <EmptyState title="No entities stored yet." />
                      ) : filteredEntities.map((entity) => (
                        <div key={entity.id} className="rounded-xl border p-4">
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div className="min-w-0 flex-1 space-y-2">
                              <div className="flex flex-wrap gap-2">
                                <Badge variant="secondary">{entity.entity_type}</Badge>
                                <Badge variant="outline">Claims: {entity.claim_count}</Badge>
                                {entity.manual_locked && <Badge variant="outline">manual</Badge>}
                              </div>
                              <LocalizedTextBlock localized={entity.name_localized} textClassName="font-medium text-base" englishTitle="实体英文原名" />
                              <LocalizedTextBlock localized={entity.summary_localized} className="text-muted-foreground" emptyText="暂无摘要。" />
                            </div>
                            <div className="flex flex-wrap gap-2">
                              <Button size="sm" variant="outline" onClick={() => fillEntityDraft(entity)}>编辑英文</Button>
                              <Button size="sm" variant="destructive" onClick={() => handleDelete(`Delete entity “${entity.canonical_name}”?`, () => deleteWorkspaceEntity(numericProfileId, entity.id))}>
                                <Trash2 className="mr-1 h-3.5 w-3.5" />
                                删除
                              </Button>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              </div>

              <Card className={rawFocus?.section === 'evidence' || (rawFocus?.section === 'claims' && (rawFocus?.paperId || typeof rawFocus?.claimId === 'number')) ? 'ring-2 ring-ring/35' : undefined}>
                <CardHeader>
                  <CardTitle>
                    <SectionTitle
                      title="证据片段"
                      icon={<FileSearch className="h-4 w-4" />}
                      hint="Evidence fragments 把 claim 追溯回具体的章节、片段和页码提示，是‘结论 → 证据’这条链路中最关键的人工校验层。"
                    />
                  </CardTitle>
                    <CardDescription>
                    证据层内容跟随页面统一语言切换显示，便于连续审阅。
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex justify-end">
                    <Button variant="outline" onClick={() => setIsEvidenceDialogOpen(true)}>
                      <Save className="mr-2 h-4 w-4" />
                      新建证据
                    </Button>
                  </div>
                  <div className="space-y-3">
                    {filteredEvidence.length === 0 ? (
                      <EmptyState title="No evidence fragments stored yet." />
                    ) : filteredEvidence.map((evidence) => (
                      <div key={evidence.id} className="rounded-xl border p-4">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div className="min-w-0 flex-1 space-y-2">
                            <div className="flex flex-wrap gap-2">
                              <Badge variant="secondary">{evidence.section_key}</Badge>
                              <Badge variant="outline">Claim #{evidence.claim_id}</Badge>
                              {evidence.page_label && <Badge variant="outline">{evidence.page_label}</Badge>}
                              {evidence.manual_locked && <Badge variant="outline">manual</Badge>}
                            </div>
                            <LocalizedTextBlock localized={evidence.claim_title_localized} textClassName="font-medium text-base" englishTitle="关联 Claim 英文原文" />
                            <LocalizedTextBlock localized={evidence.snippet_localized} preserveWhitespace />
                            {(evidence.evidence_summary || evidence.evidence_summary_zh) ? (
                              <LocalizedTextBlock localized={evidence.evidence_summary_localized} className="text-muted-foreground" />
                            ) : null}
                            <p className="text-xs text-muted-foreground">Paper: {evidence.paper_id || 'manual'} · Job: {evidence.job_id || 'manual'}</p>
                          </div>
                          <div className="flex flex-wrap gap-2">
                            <Button size="sm" variant="outline" onClick={() => fillEvidenceDraft(evidence)}>编辑英文</Button>
                            <Button size="sm" variant="destructive" onClick={() => handleDelete(`Delete evidence #${evidence.id}?`, () => deleteWorkspaceEvidence(numericProfileId, evidence.id))}>
                              <Trash2 className="mr-1 h-3.5 w-3.5" />
                              删除
                            </Button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </Fragment>
          )}
        </div>
      )}

      {activeView === 'themes' && (
        <Card>
          <CardHeader>
            <CardTitle>
              <SectionTitle
                title="主题结构"
                icon={<Sparkles className="h-4 w-4" />}
                hint="Theme 层把原始 entities、claims、synthesis 聚合成更适合人类理解的研究主题，是当前 Profile 的结构化阅读入口。"
              />
            </CardTitle>
            <CardDescription>
              这里不直接编辑对象，而是帮助你快速看清“当前领域被归纳成了哪些主题”。如需修正，请跳回原始对象视图处理 claims、evidence 与 synthesis。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {!themeSnapshot ? (
              <EmptyState title="正在加载主题结构..." />
            ) : themeSnapshot.items.length === 0 ? (
              <EmptyState title="尚未形成可展示的主题结构。" />
            ) : themeSnapshot.items.map((theme) => (
              <div key={theme.theme_key} className="rounded-xl border p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="secondary">{theme.maturity}</Badge>
                    <Badge variant="outline">{theme.paper_count} papers</Badge>
                    <Badge variant="outline">{theme.claim_count} claims</Badge>
                    <Badge variant="outline">{theme.evidence_count} evidence</Badge>
                    {theme.debate_count > 0 ? <Badge variant="outline">{theme.debate_count} debates</Badge> : null}
                    {theme.open_question_count > 0 ? <Badge variant="outline">{theme.open_question_count} open questions</Badge> : null}
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => jumpToRawView({
                      section: 'claims',
                      claimId: theme.claim_ids[0],
                      label: `${resolveLocalizedText(theme.title_localized, contentLanguage) || theme.title}`,
                    })}
                  >
                    跳到原始 Claim
                  </Button>
                </div>
                <LocalizedTextBlock localized={theme.title_localized} className="mt-3" textClassName="font-medium text-base" />
                <LocalizedTextBlock localized={theme.summary_localized} className="mt-2 text-sm text-muted-foreground" />
                {theme.method_entities.length > 0 ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {theme.method_entities.map((entity) => (
                      <Badge key={`${theme.theme_key}-${entity.id}`} variant="outline">
                        {resolveLocalizedText({ en: entity.name, zh: entity.name_zh, primary: entity.name_zh || entity.name }, contentLanguage) || entity.name}
                      </Badge>
                    ))}
                  </div>
                ) : null}
                {theme.representative_claims.length > 0 ? (
                  <div className="mt-4 space-y-2">
                    <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Representative Claims</p>
                    {theme.representative_claims.map((claim) => (
                      <div key={`${theme.theme_key}-claim-${claim.id}`} className="rounded-lg border bg-muted/20 px-3 py-2 text-sm">
                        <LocalizedTextBlock localized={claim.title_localized} textClassName="font-medium" />
                        <p className="mt-1 text-xs text-muted-foreground">importance {claim.importance.toFixed(2)} · evidence {claim.evidence_count} · {claim.paper_id || 'manual'}</p>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {activeView === 'gaps' && (
        <Card>
          <CardHeader>
            <CardTitle>
              <SectionTitle
                title="知识空白"
                icon={<AlertTriangle className="h-4 w-4" />}
                hint="Gap 层把当前仍未解决的争议、开放问题和证据薄弱点显式列出来，帮助你知道接下来该优先补什么。"
              />
            </CardTitle>
            <CardDescription>
              它不是新的 source-of-truth，而是对现有 Memory 的可解释派生视图；真正的修正仍应回到 raw claims、evidence 或 review queue。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {!gapSnapshot ? (
              <EmptyState title="正在加载知识空白..." />
            ) : gapSnapshot.items.length === 0 ? (
              <EmptyState title="当前没有明显的知识空白或未解决问题。" />
            ) : gapSnapshot.items.map((gap) => (
              <div key={gap.gap_key} className="rounded-xl border border-destructive/35 bg-destructive/5 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="destructive">{gap.priority}</Badge>
                    <Badge variant="outline">{gap.gap_type}</Badge>
                    {resolveLocalizedText(gap.theme_title_localized, contentLanguage) ? (
                      <Badge variant="outline">{resolveLocalizedText(gap.theme_title_localized, contentLanguage)}</Badge>
                    ) : null}
                    {gap.reason_codes.map((code) => (
                      <Badge key={`${gap.gap_key}-${code}`} variant="outline">{code}</Badge>
                    ))}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {gap.review_ids.length > 0 ? (
                      <Button size="sm" variant="ghost" onClick={() => setActiveView('reviews')}>
                        前往冲突队列
                      </Button>
                    ) : null}
                    {gap.claim_ids.length > 0 ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => jumpToRawView({
                          section: 'claims',
                          claimId: gap.claim_ids[0],
                          label: `${resolveLocalizedText(gap.title_localized, contentLanguage) || gap.title}`,
                        })}
                      >
                        跳到 Claim
                      </Button>
                    ) : null}
                  </div>
                </div>
                <LocalizedTextBlock localized={gap.title_localized} className="mt-3" textClassName="font-medium text-base" />
                <LocalizedTextBlock localized={gap.summary_localized} className="mt-2 text-sm text-muted-foreground" />
                <p className="mt-2 text-xs text-muted-foreground">
                  Claims: {gap.claim_ids.length} · Synthesis: {gap.synthesis_ids.length} · Reviews: {gap.review_ids.length} · Evidence: {gap.evidence_count}
                </p>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {activeView === 'opportunities' && (
        <Card>
          <CardHeader>
            <CardTitle>
              <SectionTitle
                title="研究机会"
                icon={<FileSearch className="h-4 w-4" />}
                hint="Opportunity 层是 Memory V3 第一版只读派生视图，用来暴露最值得继续验证、澄清边界或做迁移测试的方向。"
              />
            </CardTitle>
            <CardDescription>
              这里展示的是派生出的机会项，不是新的 source-of-truth。真正的修正仍应回到 claims、evidence、review queue 或新的论文运行结果。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {!opportunitySnapshot ? (
              <EmptyState title="正在加载研究机会..." />
            ) : opportunitySnapshot.items.length === 0 ? (
              <EmptyState title="当前还没有派生出明确的研究机会。" />
            ) : opportunitySnapshot.items.map((item) => (
              <div key={item.opportunity_key} className="rounded-xl border border-sky-200/70 bg-sky-50/30 p-4 dark:border-sky-900/60 dark:bg-sky-950/10">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap gap-2">
                    <Badge variant={item.priority === 'high' ? 'destructive' : 'secondary'}>{item.priority}</Badge>
                    <Badge variant="outline">{item.opportunity_type}</Badge>
                    {item.theme_titles_localized.map((themeTitle, index) => {
                      const label = resolveLocalizedText(themeTitle, contentLanguage)
                      return label ? <Badge key={`${item.opportunity_key}-${index}-${label}`} variant="outline">{label}</Badge> : null
                    })}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {item.review_ids.length > 0 ? (
                      <Button size="sm" variant="ghost" onClick={() => setActiveView('reviews')}>
                        前往冲突队列
                      </Button>
                    ) : null}
                    {item.claim_ids.length > 0 ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => jumpToRawView({
                          section: 'claims',
                          claimId: item.claim_ids[0],
                          label: `${resolveLocalizedText(item.title_localized, contentLanguage) || item.title}`,
                        })}
                      >
                        跳到 Claim
                      </Button>
                    ) : null}
                  </div>
                </div>
                <LocalizedTextBlock localized={item.title_localized} className="mt-3" textClassName="font-medium text-base" />
                <LocalizedTextBlock localized={item.summary_localized} className="mt-2 text-sm text-muted-foreground" />
                {item.suggested_validation_steps.length > 0 ? (
                  <div className="mt-3 rounded-lg border bg-background/70 p-3">
                    <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Suggested validation</p>
                    <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
                      {item.suggested_validation_steps.map((step, index) => (
                        <li key={`${item.opportunity_key}-step-${index}`}>{step}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
                  <Badge variant="outline">Claims {item.claim_ids.length}</Badge>
                  <Badge variant="outline">Supporting {item.supporting_claim_ids.length}</Badge>
                  <Badge variant="outline">Conflicting {item.conflicting_claim_ids.length}</Badge>
                  <Badge variant="outline">Reviews {item.review_ids.length}</Badge>
                  <Badge variant="outline">Papers {item.paper_ids.length}</Badge>
                  {item.reason_codes.map((code) => (
                    <Badge key={`${item.opportunity_key}-${code}`} variant="outline">{code}</Badge>
                  ))}
                  {item.risk_flags.map((flag) => (
                    <Badge key={`${item.opportunity_key}-risk-${flag}`} variant="outline">risk:{flag}</Badge>
                  ))}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {activeView === 'graph' && (
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>
                <SectionTitle
                  title="图谱关系"
                  icon={<Network className="h-4 w-4" />}
                  hint="这是当前 profile 的可视化知识图谱。支持滚轮缩放、拖拽平移、搜索与类型筛选。图上节点与右侧详情都会跟随页面统一语言切换。"
                />
              </CardTitle>
              <CardDescription>
                显式边可人工维护；系统也会根据 claim、证据与 synthesis 自动推导关系。
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-6 xl:grid-cols-[minmax(0,0.34fr)_minmax(0,0.66fr)]">
              <div className="min-w-0 space-y-4 rounded-xl border p-4">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm font-medium">显式图谱边</p>
                  <Button size="sm" variant="outline" onClick={() => setIsEdgeDialogOpen(true)}>
                    <Save className="mr-2 h-4 w-4" />
                    新建边
                  </Button>
                </div>
                <div className="rounded-lg border bg-muted/30 p-3 text-xs text-muted-foreground">
                  <p>提示：显式边是人工可编辑部分；系统推导边则来自 claim、evidence 与 synthesis 关系。</p>
                </div>
                <div className="space-y-3">
                  {snapshot.editable_edges.length === 0 ? (
                    <EmptyState title="No editable edges yet." />
                  ) : snapshot.editable_edges.map((edge) => (
                    <div key={edge.id} className="rounded-xl border p-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="secondary">{edge.relation_type}</Badge>
                        <span className="max-w-full rounded-full border border-border px-2 py-0.5 text-xs break-all text-foreground">
                          {edge.source_kind}:{edge.source_ref}
                        </span>
                        <span className="max-w-full rounded-full border border-border px-2 py-0.5 text-xs break-all text-foreground">
                          {edge.target_kind}:{edge.target_ref}
                        </span>
                      </div>
                      <LocalizedTextBlock localized={edge.summary_localized} className="mt-3" emptyText="暂无关系摘要。" englishTitle="关系摘要英文原版" />
                      <div className="mt-3 flex flex-wrap gap-2">
                        <Button size="sm" variant="outline" onClick={() => fillEdgeDraft(edge)}>编辑英文</Button>
                        <Button size="sm" variant="destructive" onClick={() => handleDelete(`Delete graph edge #${edge.id}?`, () => deleteWorkspaceGraphEdge(numericProfileId, edge.id))}>
                          <Trash2 className="mr-1 h-3.5 w-3.5" />
                          删除
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="min-w-0 space-y-4">
                <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                  <OverviewCard title="图谱节点" value={snapshot.graph.nodes.length} hint="Papers / Entities / Claims / Synthesis" />
                  <OverviewCard title="图谱边" value={snapshot.graph.edges.length} hint="系统推导 + 人工显式边" />
                  <OverviewCard title="可编辑边" value={snapshot.editable_edges.length} hint="支持人工修订的显式关系" />
                </div>

                <MemoryGraphCanvas
                  nodes={snapshot.graph.nodes}
                  edges={snapshot.graph.edges}
                  selectedNodeId={selectedGraphNodeId}
                  onSelectNode={setSelectedGraphNodeId}
                />

                <div className="rounded-xl border p-4">
                  <div className="flex items-center gap-2">
                    <Workflow className="h-4 w-4" />
                    <p className="font-medium">节点详情</p>
                  </div>
                  {selectedGraphNode ? (
                    <div className="mt-3 space-y-3">
                      <div className="flex flex-wrap gap-2">
                        <Badge variant="secondary">{selectedGraphNode.node_type}</Badge>
                        <Badge variant="outline">Degree {selectedGraphNode.degree}</Badge>
                        {renderStateBadge(selectedGraphNode.status)}
                      </div>
                      <LocalizedTextBlock localized={selectedGraphNode.label_localized} textClassName="font-medium text-base" englishTitle="节点英文原名" />
                      <LocalizedTextBlock localized={selectedGraphNode.summary_localized} className="text-muted-foreground" emptyText="暂无节点摘要。" englishTitle="节点英文摘要" />
                      <p className="text-xs text-muted-foreground break-all">Ref: {selectedGraphNode.ref}</p>
                      <p className="text-xs text-muted-foreground">当前可见关联边：{focusedGraphEdges.length}</p>
                      {focusedGraphEdges.length > 0 ? (
                        <div className="space-y-2 rounded-lg border bg-muted/20 p-3">
                          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">关联边</p>
                          {focusedGraphEdges.map((edge) => (
                            <div key={String(edge.id)} className="rounded-lg border bg-background px-3 py-2">
                              <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                                <Badge variant="outline">{edge.relation_type}</Badge>
                                <Badge variant="outline">权重 {edge.weight}</Badge>
                              </div>
                              <LocalizedTextBlock localized={edge.summary_localized} className="mt-2" emptyText="暂无关系摘要。" englishTitle="边摘要英文原文" />
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ) : (
                    <EmptyState title="点击图谱中的节点后，在这里查看当前语言下的节点摘要与关联边。" />
                  )}
                </div>
              </div>
            </CardContent>
          </Card>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,0.5fr)_minmax(0,0.5fr)]">
            <Card className="min-w-0">
              <CardHeader>
                <CardTitle>
                  <SectionTitle
                    title="节点列表"
                    hint="这里是图谱节点的结构化列表视图，按连接度排序。适合在图上看不清时精确定位节点，并与图画布联动。"
                  />
                </CardTitle>
                <CardDescription>
                  按连接度排序，优先展示更核心、更高连接的知识节点。
                </CardDescription>
              </CardHeader>
              <CardContent className="min-w-0 space-y-3">
                {graphNodes.length === 0 ? (
                  <EmptyState title="No graph nodes yet." />
                ) : graphNodes.map((node) => {
                  const isSelected = node.id === selectedGraphNodeId
                  return (
                    <button
                      key={node.id}
                      type="button"
                      onClick={() => setSelectedGraphNodeId(isSelected ? null : node.id)}
                      className={`block w-full rounded-xl border p-4 text-left transition-colors hover:border-foreground/30 ${isSelected ? 'border-violet-400 bg-violet-500/5' : ''}`}
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="secondary">{node.node_type}</Badge>
                        <Badge variant="outline">Degree {node.degree}</Badge>
                        {renderStateBadge(node.status)}
                      </div>
                      <LocalizedTextBlock localized={node.label_localized} className="mt-2" textClassName="font-medium" englishTitle="节点英文原名" />
                      {(node.summary || node.summary_zh) ? <LocalizedTextBlock localized={node.summary_localized} className="mt-2 text-muted-foreground" /> : null}
                      <p className="mt-2 text-xs text-muted-foreground break-all">Ref: {node.ref}</p>
                    </button>
                  )
                })}
              </CardContent>
            </Card>

            <Card className="min-w-0">
              <CardHeader>
                <CardTitle>
                  <SectionTitle
                    title="派生图谱边"
                    hint="这些边由系统根据 claim↔entity、synthesis↔claim、paper↔claim 以及显式 paper relation 自动推导。若已选节点，这里会优先显示其局部邻接边。"
                  />
                </CardTitle>
                <CardDescription>
                  包括 claim↔entity、synthesis↔claim、paper↔claim 以及显式 paper relation。
                </CardDescription>
              </CardHeader>
              <CardContent className="min-w-0 space-y-3">
                {focusedGraphEdges.length === 0 ? (
                  <EmptyState title="No graph edges yet." />
                ) : focusedGraphEdges.map((edge) => (
                  <div key={String(edge.id)} className="rounded-xl border p-4">
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="secondary">{edge.relation_type}</Badge>
                      <span className="max-w-full rounded-full border border-border px-2 py-0.5 text-xs break-all text-foreground">
                        {edge.source_id}
                      </span>
                      <span className="max-w-full rounded-full border border-border px-2 py-0.5 text-xs break-all text-foreground">
                        {edge.target_id}
                      </span>
                    </div>
                    {(edge.summary || edge.summary_zh) ? <LocalizedTextBlock localized={edge.summary_localized} className="mt-2 text-muted-foreground" englishTitle="图谱边英文摘要" /> : null}
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>
        </div>
      )}

      {activeView === 'timeline' && (
        <Card>
          <CardHeader>
            <CardTitle>
              <SectionTitle
                title="时间线"
                icon={<GitBranch className="h-4 w-4" />}
                hint="Timeline 把高层认知、claim 变更和待处理 review 放在同一条时间轴上，适合从‘系统如何逐步理解这个领域’的角度观察记忆演化。"
              />
            </CardTitle>
            <CardDescription>
              先按论文/任务 bundle 分段，再在每段内部查看认知更新、结论变更与待处理冲突，减少跨论文信息缠绕。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {snapshot.timeline.length === 0 ? (
              <EmptyState title="Timeline is empty." />
            ) : timelineSections.map((section) => {
              const anchor = section.anchor
              const sectionTitle = resolveLocalizedText(anchor?.title_localized, contentLanguage) || anchor?.title || section.items[0]?.bundle_label || 'General memory updates'
              const sectionMeta = anchor?.source_paper_id || anchor?.source_job_id || section.items[0]?.source_paper_id || section.items[0]?.source_job_id || ''
              return (
                <div key={section.key} className="rounded-xl border p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3 border-b pb-4">
                    <div className="min-w-0 flex-1 space-y-2">
                      <div className="flex flex-wrap gap-2">
                        <Badge variant="secondary">{anchor ? 'paper bundle' : 'general updates'}</Badge>
                        {sectionMeta ? <Badge variant="outline">{sectionMeta}</Badge> : null}
                        <Badge variant="outline">{section.items.length} 条事件</Badge>
                      </div>
                      {anchor ? (
                        <>
                          <LocalizedTextBlock localized={anchor.title_localized} textClassName="font-medium text-base" englishTitle="论文锚点英文原文" />
                          <LocalizedTextBlock localized={anchor.summary_localized} className="text-muted-foreground" emptyText="暂无摘要。" englishTitle="论文锚点英文摘要" />
                        </>
                      ) : (
                        <div>
                          <p className="font-medium">{sectionTitle}</p>
                          <p className="mt-1 text-sm text-muted-foreground">这组事件暂时无法稳定归属到单一 paper bundle，通常来自跨论文 synthesis、人工编辑或无明确 provenance 的更新。</p>
                        </div>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground">{formatDate(anchor?.timestamp ?? section.items[0]?.timestamp)}</p>
                  </div>

                  <div className="mt-4 space-y-3">
                    {section.items.length === 0 ? (
                      <EmptyState title="这篇论文当前还没有更多派生的 timeline 事件。" />
                    ) : section.items.map((item: MemoryTimelineItem) => (
                      <div key={item.id} className={`rounded-xl border p-4 ${statusToneClass(item.status)}`}>
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div className="min-w-0 flex-1 space-y-2">
                            <div className="flex flex-wrap gap-2">
                              <Badge variant="secondary">{item.item_type}</Badge>
                              {item.target_type ? <Badge variant="outline">{item.target_type}:{item.target_id}</Badge> : null}
                              {renderStateBadge(item.status)}
                            </div>
                            <LocalizedTextBlock localized={item.title_localized} textClassName="font-medium" englishTitle="时间线标题英文原文" />
                            <LocalizedTextBlock localized={item.summary_localized} className="text-muted-foreground" emptyText="暂无摘要。" englishTitle="时间线摘要英文原文" />
                          </div>
                          <p className="text-xs text-muted-foreground">{formatDate(item.timestamp)}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </CardContent>
        </Card>
      )}

      {activeView === 'reviews' && (
        <Card>
          <CardHeader>
            <CardTitle>
              <SectionTitle
                title="冲突队列"
                icon={<AlertTriangle className="h-4 w-4" />}
                hint="当新论文与现有结论冲突，或 AI 试图更新人工锁定对象时，系统会把默认解放在这里等待你裁决。未裁决前，Agent 会继续使用默认解。"
              />
            </CardTitle>
            <CardDescription>
              冲突说明与建议动作会跟随页面统一语言切换，方便连续裁决。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {pendingReviews.length === 0 ? (
              <EmptyState title="No pending reviews right now." />
            ) : pendingReviews.map((review: MemoryReviewItem) => (
              <div key={review.id} className="rounded-xl border border-destructive/35 bg-destructive/5 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1 space-y-3">
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="destructive">{review.review_type}</Badge>
                      <Badge variant="outline">{review.target_type} #{review.target_id}</Badge>
                    </div>
                    <LocalizedTextBlock localized={review.title_localized} textClassName="font-medium text-base" englishTitle="冲突标题英文原文" />
                    {(review.description || review.description_zh) ? <LocalizedTextBlock localized={review.description_localized} className="text-muted-foreground" englishTitle="冲突描述英文原文" /> : null}
                    {(review.default_resolution || review.default_resolution_zh) ? (
                      <div className="rounded-lg border bg-muted/30 p-3 text-sm">
                        <p className="font-medium">当前系统默认采用的解法</p>
                        <LocalizedTextBlock localized={review.default_resolution_localized} className="mt-2 text-muted-foreground" englishTitle="默认解英文原文" />
                      </div>
                    ) : null}
                    {Boolean(review.suggested_payload) && (
                      <details className="rounded-lg border p-3 text-sm">
                        <summary className="cursor-pointer font-medium">查看结构化建议载荷</summary>
                        <LocalizedJsonPreview value={review.suggested_payload} />
                      </details>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">Updated: {formatDate(review.updated_at)}</p>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Button disabled={isBusy} onClick={() => runBusyAction(async () => {
                    await resolveWorkspaceReview(numericProfileId, review.id, { adopt_suggested: true, resolution_note: 'Suggested payload adopted' })
                  })}>
                    <Sparkles className="mr-2 h-4 w-4" />
                    采纳建议更新
                  </Button>
                  <Button variant="outline" disabled={isBusy} onClick={() => runBusyAction(async () => {
                    await resolveWorkspaceReview(numericProfileId, review.id, { resolution_note: 'Marked reviewed manually' })
                  })}>
                    标记为已审核
                  </Button>
                  <Button variant="outline" disabled={isBusy} onClick={() => runBusyAction(async () => {
                    await resolveWorkspaceReview(numericProfileId, review.id, { dismiss: true, resolution_note: 'Dismissed from queue' })
                  })}>
                    忽略提醒
                  </Button>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {activeView === 'history' && (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[0.42fr_0.58fr]">
          <Card>
            <CardHeader>
              <CardTitle>
                <SectionTitle
                  title="修订历史"
                  icon={<FileClock className="h-4 w-4" />}
                  hint="这里记录 AI 自动更新和人工编辑的完整历史，可用来审计当前知识为何形成、以及某次修改具体改了什么。"
                />
              </CardTitle>
              <CardDescription>
                摘要跟随页面统一语言切换；如需进一步审计，可展开 before / after 原始载荷。
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {snapshot.revisions.length === 0 ? (
                <EmptyState title="No revision history yet." />
              ) : snapshot.revisions.map((entry) => (
                <div key={entry.id} className="rounded-xl border p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="secondary">{entry.action}</Badge>
                    <Badge variant="outline">{entry.actor_type}</Badge>
                    <Badge variant="outline">{entry.target_type}:{entry.target_id}</Badge>
                  </div>
                  <LocalizedTextBlock localized={entry.summary_localized} className="mt-2" textClassName="font-medium" englishTitle="修订摘要英文原文" emptyText="Revision entry" />
                  <p className="mt-1 text-xs text-muted-foreground">{formatDate(entry.created_at)}</p>
                  {(entry.before_json !== null && entry.before_json !== undefined) || (entry.after_json !== null && entry.after_json !== undefined) ? (
                    <details className="mt-3 rounded-lg border p-3 text-sm">
                      <summary className="cursor-pointer font-medium">查看 before / after 载荷</summary>
                      <div className="mt-3 grid gap-3 xl:grid-cols-2">
                        <div>
                          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Before</p>
                          <LocalizedJsonPreview value={entry.before_json} />
                        </div>
                        <div>
                          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">After</p>
                          <LocalizedJsonPreview value={entry.after_json} />
                        </div>
                      </div>
                    </details>
                  ) : null}
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>
                <SectionTitle
                  title="当前 review 状态快照"
                  hint="这个视图用于配合 revision history 一起看：左侧是历史变更，右侧是当前 review 队列的状态快照，便于理解还有哪些冲突未关闭。"
                />
              </CardTitle>
              <CardDescription>
                便于从“当前状态”角度快速检查还有哪些冲突或已解决事项。
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {snapshot.reviews.length === 0 ? (
                <EmptyState title="No review items yet." />
              ) : snapshot.reviews.map((review) => (
                <div key={review.id} className={`rounded-xl border p-4 ${statusToneClass(review.status)}`}>
                  <div className="flex flex-wrap gap-2">
                    {renderStateBadge(review.status)}
                    <Badge variant="outline">{review.review_type}</Badge>
                    <Badge variant="outline">{review.target_type}:{review.target_id}</Badge>
                  </div>
                  <LocalizedTextBlock localized={review.title_localized} className="mt-2" textClassName="font-medium" englishTitle="Review 标题英文原文" />
                  {(review.description || review.description_zh) ? <LocalizedTextBlock localized={review.description_localized} className="mt-1 text-muted-foreground" englishTitle="Review 描述英文原文" /> : null}
                  {review.resolution_note && <p className="mt-2 text-xs text-muted-foreground">Resolution note: {review.resolution_note}</p>}
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      )}
      </div>
    </LocalizedTextLanguageProvider>
  )
}
