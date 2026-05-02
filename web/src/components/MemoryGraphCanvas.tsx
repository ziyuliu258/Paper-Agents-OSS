import { useEffect, useMemo, useState } from 'react'
import { Move, Search } from 'lucide-react'
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useNodesInitialized,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import type { MemoryGraphEdge, MemoryGraphNode } from '@/api/client'
import {
  resolveLocalizedText,
  useLocalizedTextLanguage,
} from '@/lib/localizedText'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

interface MemoryGraphCanvasProps {
  nodes: MemoryGraphNode[]
  edges: MemoryGraphEdge[]
  selectedNodeId?: string | null
  onSelectNode?: (nodeId: string | null) => void
  className?: string
}

const NODE_WIDTH = 192
const MIN_NODE_HEIGHT = 84
const COLUMN_GAP = 280
const NODE_GAP = 120
const PADDING_X = 48
const PADDING_Y = 48
const MAX_VISIBLE_NODES = 28
const MIN_ZOOM = 0.45
const MAX_ZOOM = 2.1
const TYPE_ORDER = ['paper', 'entity', 'claim', 'synthesis'] as const

type GraphFilter = 'all' | (typeof TYPE_ORDER)[number]

type MemoryFlowNodeData = {
  label: string
  englishLabel: string
  nodeType: string
  isNeighbor: boolean
}

type MemoryFlowNode = Node<MemoryFlowNodeData>

const hiddenHandleClassName = '!h-2 !w-2 !border-0 !bg-transparent !opacity-0'

const TYPE_META: Record<string, { label: string; labelZh: string; cardClassName: string; chipClassName: string }> = {
  paper: {
    label: 'Paper',
    labelZh: '论文',
    cardClassName: 'border-sky-400/60 bg-sky-500/10 text-sky-950 dark:text-sky-100',
    chipClassName: 'bg-sky-500/10 text-sky-700 dark:text-sky-300',
  },
  entity: {
    label: 'Entity',
    labelZh: '实体',
    cardClassName: 'border-emerald-400/60 bg-emerald-500/10 text-emerald-950 dark:text-emerald-100',
    chipClassName: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
  },
  claim: {
    label: 'Claim',
    labelZh: '结论',
    cardClassName: 'border-amber-400/60 bg-amber-500/10 text-amber-950 dark:text-amber-100',
    chipClassName: 'bg-amber-500/10 text-amber-700 dark:text-amber-300',
  },
  synthesis: {
    label: 'Synthesis',
    labelZh: '认知',
    cardClassName: 'border-violet-400/60 bg-violet-500/10 text-violet-950 dark:text-violet-100',
    chipClassName: 'bg-violet-500/10 text-violet-700 dark:text-violet-300',
  },
  default: {
    label: 'Node',
    labelZh: '节点',
    cardClassName: 'border-border bg-card text-foreground',
    chipClassName: 'bg-muted text-muted-foreground',
  },
}

function getTypeMeta(nodeType: string) {
  return TYPE_META[nodeType] ?? TYPE_META.default
}

function MemoryNodeCard({ data, selected }: NodeProps<MemoryFlowNode>) {
  const meta = getTypeMeta(data.nodeType)

  return (
    <div
      className={cn(
        'relative w-48 rounded-2xl border px-3 py-2 shadow-sm transition-all duration-150',
        meta.cardClassName,
        selected
          ? 'border-violet-400 bg-violet-500/12 shadow-[0_0_0_2px_rgba(167,139,250,0.22)]'
          : data.isNeighbor
            ? 'border-foreground/35 shadow-[0_0_0_1px_rgba(148,163,184,0.18)]'
            : 'shadow-none',
      )}
    >
      <Handle type="target" position={Position.Left} className={hiddenHandleClassName} isConnectable={false} />
      <Handle type="source" position={Position.Right} className={hiddenHandleClassName} isConnectable={false} />
      <div className="space-y-2">
        <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-muted-foreground">{meta.label}</div>
        <div className="line-clamp-3 min-h-[3.6rem] text-sm font-medium leading-5 break-words">
          {data.label || data.englishLabel || 'Untitled'}
        </div>
      </div>
    </div>
  )
}

const nodeTypes = {
  memoryNode: MemoryNodeCard,
}

function GraphViewportSync({ signature }: { signature: string }) {
  const { fitView } = useReactFlow()
  const nodesInitialized = useNodesInitialized()

  useEffect(() => {
    if (!nodesInitialized) {
      return
    }

    const timer = window.setTimeout(() => {
      void fitView({ padding: 0.2, duration: 280, minZoom: MIN_ZOOM, maxZoom: 1.25 })
    }, 0)

    return () => window.clearTimeout(timer)
  }, [fitView, nodesInitialized, signature])

  return null
}

function MemoryGraphCanvasInner({
  nodes,
  edges,
  selectedNodeId = null,
  onSelectNode,
  className,
}: MemoryGraphCanvasProps) {
  const contentLanguage = useLocalizedTextLanguage()
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState<GraphFilter>('all')

  const graph = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase()
    const passesFilter = (node: MemoryGraphNode) => {
      if (typeFilter !== 'all' && node.node_type !== typeFilter) {
        return false
      }
      if (!normalizedSearch) {
        return true
      }
      return [
        node.label,
        node.label_zh,
        node.summary,
        node.summary_zh,
        node.ref,
        node.node_type,
      ].some((value) => value.toLowerCase().includes(normalizedSearch))
    }

    const filteredNodes = nodes.filter(passesFilter)
    const filteredNodeIds = new Set(filteredNodes.map((node) => node.id))
    const filteredEdges = edges.filter((edge) => filteredNodeIds.has(edge.source_id) && filteredNodeIds.has(edge.target_id))

    const adjacency = new Map<string, Set<string>>()
    const nodeById = new Map(filteredNodes.map((node) => [node.id, node]))

    for (const edge of filteredEdges) {
      if (!adjacency.has(edge.source_id)) {
        adjacency.set(edge.source_id, new Set())
      }
      if (!adjacency.has(edge.target_id)) {
        adjacency.set(edge.target_id, new Set())
      }
      adjacency.get(edge.source_id)?.add(edge.target_id)
      adjacency.get(edge.target_id)?.add(edge.source_id)
    }

    const sortedNodes = [...filteredNodes].sort((left, right) => {
      if (right.degree !== left.degree) {
        return right.degree - left.degree
      }
      return left.label.localeCompare(right.label)
    })

    const visibleIds = new Set<string>()
    const selected = selectedNodeId && nodeById.has(selectedNodeId) ? selectedNodeId : null

    if (selected) {
      visibleIds.add(selected)
      for (const neighborId of adjacency.get(selected) ?? []) {
        visibleIds.add(neighborId)
      }
    }

    for (const node of sortedNodes) {
      if (!normalizedSearch && visibleIds.size >= MAX_VISIBLE_NODES) {
        break
      }
      visibleIds.add(node.id)
    }

    const visibleNodes = sortedNodes.filter((node) => visibleIds.has(node.id))
    const visibleNodeIds = new Set(visibleNodes.map((node) => node.id))
    const visibleEdges = filteredEdges.filter((edge) => visibleNodeIds.has(edge.source_id) && visibleNodeIds.has(edge.target_id))

    const groups = new Map<string, MemoryGraphNode[]>()
    for (const nodeType of TYPE_ORDER) {
      groups.set(nodeType, [])
    }
    for (const node of visibleNodes) {
      const groupKey = TYPE_ORDER.includes(node.node_type as (typeof TYPE_ORDER)[number]) ? node.node_type : 'synthesis'
      groups.get(groupKey)?.push(node)
    }

    const flowNodes: MemoryFlowNode[] = []

    TYPE_ORDER.forEach((nodeType, groupIndex) => {
      const groupNodes = groups.get(nodeType) ?? []
      groupNodes.forEach((node, index) => {
        flowNodes.push({
          id: node.id,
          type: 'memoryNode',
          position: {
            x: PADDING_X + groupIndex * COLUMN_GAP,
            y: PADDING_Y + index * NODE_GAP,
          },
          draggable: false,
          selectable: true,
          sourcePosition: Position.Right,
          targetPosition: Position.Left,
          selected: node.id === selected,
          data: {
            label: resolveLocalizedText(node.label_localized, contentLanguage) || node.label,
            englishLabel: node.label,
            nodeType: node.node_type,
            isNeighbor: Boolean(selected && adjacency.get(selected)?.has(node.id)),
          },
          style: {
            width: NODE_WIDTH,
            minHeight: MIN_NODE_HEIGHT,
            background: 'transparent',
            border: 'none',
            padding: 0,
            boxShadow: 'none',
          },
        })
      })
    })

    const flowEdges: Edge[] = visibleEdges.map((edge) => {
      const isFocused = Boolean(selected && (edge.source_id === selected || edge.target_id === selected))
      const dimmed = Boolean(selected && !isFocused)
      return {
        id: String(edge.id),
        source: edge.source_id,
        target: edge.target_id,
        type: 'bezier',
        selectable: false,
        focusable: false,
        animated: false,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 18,
          height: 18,
          color: isFocused ? 'rgba(167, 139, 250, 0.95)' : dimmed ? 'rgba(148, 163, 184, 0.35)' : 'rgba(148, 163, 184, 0.68)',
        },
        style: {
          stroke: isFocused ? 'rgba(167, 139, 250, 0.95)' : dimmed ? 'rgba(148, 163, 184, 0.35)' : 'rgba(148, 163, 184, 0.68)',
          strokeWidth: Math.min(3.2, 1.1 + edge.weight * 0.75),
        },
      }
    })

    return {
      selected,
      visibleNodes,
      filteredCount: filteredNodes.length,
      totalCount: nodes.length,
      flowNodes,
      flowEdges,
    }
  }, [contentLanguage, edges, nodes, search, selectedNodeId, typeFilter])

  const fitSignature = useMemo(
    () => `${graph.selected ?? 'none'}:${graph.flowNodes.map((node) => node.id).join('|')}:${graph.flowEdges.map((edge) => edge.id).join('|')}`,
    [graph.flowEdges, graph.flowNodes, graph.selected],
  )

  return (
    <div className={cn('min-w-0 space-y-4', className)}>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {TYPE_ORDER.map((nodeType) => {
          const meta = getTypeMeta(nodeType)
          return (
            <span
              key={nodeType}
              className={cn('inline-flex items-center gap-1 rounded-full px-2 py-1 font-medium', meta.chipClassName)}
            >
              {contentLanguage === 'en' ? meta.label : meta.labelZh}
            </span>
          )
        })}
        <span className="ml-auto text-[11px] text-muted-foreground/80">
          {graph.selected
            ? `${contentLanguage === 'en' ? 'Focused:' : '已聚焦：'}${resolveLocalizedText(
                graph.visibleNodes.find((node) => node.id === graph.selected)?.label_localized,
                contentLanguage,
              ) || graph.selected}`
            : (contentLanguage === 'en'
              ? 'Click a node to focus, drag the canvas, or use the wheel to zoom.'
              : '点击节点聚焦，拖动画布，滚轮缩放')}
        </span>
      </div>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <div className="flex min-w-0 flex-wrap gap-2">
          <div className="relative min-w-[220px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-9"
              placeholder={contentLanguage === 'en' ? 'Search node' : '搜索节点'}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <select
            className="h-9 rounded-lg border border-input bg-transparent px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            value={typeFilter}
            onChange={(event) => setTypeFilter(event.target.value as GraphFilter)}
          >
            <option value="all">{contentLanguage === 'en' ? 'All types' : '全部类型'}</option>
            {TYPE_ORDER.map((type) => (
              <option key={type} value={type}>
                {contentLanguage === 'en' ? getTypeMeta(type).label : getTypeMeta(type).labelZh}
              </option>
            ))}
          </select>
          <Badge variant="outline">
            {graph.visibleNodes.length}/{graph.filteredCount || 0} {contentLanguage === 'en' ? 'visible' : '可见'}
          </Badge>
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2">
          <Badge variant="outline" className="h-9 px-3 hidden md:inline-flex">
            <Move className="mr-1 h-3.5 w-3.5" />
            {contentLanguage === 'en' ? 'Drag to pan, wheel to zoom' : '鼠标拖动画布，滚轮缩放'}
          </Badge>
          <Badge variant="secondary">{contentLanguage === 'en' ? 'Total nodes' : '总节点'} {graph.totalCount}</Badge>
        </div>
      </div>

      <div className="min-w-0 overflow-hidden rounded-xl border bg-muted/20">
        <div className="h-[34rem] min-h-[30rem] w-full min-w-0 bg-card">
          {graph.flowNodes.length === 0 ? (
            <div className="flex h-full items-center justify-center px-6 text-center text-sm text-muted-foreground">
              {contentLanguage === 'en' ? 'No graph nodes match the current filter.' : '当前筛选条件下没有可显示的图谱节点。'}
            </div>
          ) : (
            <ReactFlow
              nodes={graph.flowNodes}
              edges={graph.flowEdges}
              nodeTypes={nodeTypes}
              proOptions={{ hideAttribution: true }}
              fitView
              fitViewOptions={{ padding: 0.2, minZoom: MIN_ZOOM, maxZoom: 1.25 }}
              minZoom={MIN_ZOOM}
              maxZoom={MAX_ZOOM}
              panOnDrag
              panOnScroll={false}
              zoomOnScroll
              zoomOnPinch
              zoomOnDoubleClick={false}
              selectionOnDrag={false}
              elementsSelectable
              nodesDraggable={false}
              nodesConnectable={false}
              elevateEdgesOnSelect={false}
              preventScrolling
              onPaneClick={() => onSelectNode?.(null)}
              onNodeClick={(_event: unknown, node: { id: string }) =>
                onSelectNode?.(node.id === graph.selected ? null : node.id)
              }
              className="memory-graph-flow"
            >
              <GraphViewportSync signature={fitSignature} />
              <Background gap={24} size={1} color="rgba(148, 163, 184, 0.18)" />
              <Controls showInteractive={false} position="top-right" fitViewOptions={{ padding: 0.2, duration: 280 }} />
            </ReactFlow>
          )}
        </div>
      </div>
    </div>
  )
}

export default function MemoryGraphCanvas(props: MemoryGraphCanvasProps) {
  return (
    <ReactFlowProvider>
      <MemoryGraphCanvasInner {...props} />
    </ReactFlowProvider>
  )
}
