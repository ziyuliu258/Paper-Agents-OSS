import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, FileText, Languages, LibraryBig, ListTree, PanelRightOpen } from 'lucide-react'
import { getProfileSurvey, type LivingSurvey } from '@/api/client'
import LocalizedTextBlock from '@/components/LocalizedTextBlock'
import LocalizedTextLanguageProvider from '@/components/LocalizedTextLanguageProvider'
import type { LocalizedContentLanguage } from '@/lib/localizedText'
import { formatDate } from '@/lib/formatters'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

const SOURCE_KEY_LABELS: Record<string, string> = {
  retrogfn: 'RetroGFN',
  molca: 'MolCA',
  meea: 'MEEA*',
  ai4chem: 'AI4Chem',
  iupac: 'IUPAC',
  llm: 'LLM',
  mcts: 'MCTS',
  gfn: 'GFN',
  gflownet: 'GFlowNet',
  uspto: 'USPTO',
}

function humanizeSourceKey(value: string) {
  const normalized = value.trim()
  if (!normalized) {
    return ''
  }
  const known = SOURCE_KEY_LABELS[normalized.toLowerCase()]
  if (known) {
    return known
  }
  return normalized
    .split(/[-_:]+/)
    .filter(Boolean)
    .map((part) => SOURCE_KEY_LABELS[part.toLowerCase()] || `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(' ')
}

function formatSurveySourceKey(kind: 'theme' | 'gap', value: string) {
  const trimmed = value.trim()
  if (!trimmed) {
    return ''
  }
  if (kind === 'theme') {
    return humanizeSourceKey(trimmed.replace(/^theme:/, ''))
  }
  if (trimmed.startsWith('coverage:theme:')) {
    return `覆盖空白 · ${humanizeSourceKey(trimmed.replace(/^coverage:theme:/, ''))}`
  }
  if (trimmed.startsWith('coverage:')) {
    return `覆盖空白 · ${humanizeSourceKey(trimmed.replace(/^coverage:/, ''))}`
  }
  return humanizeSourceKey(trimmed.replace(/^gap:/, '').replace(/^theme:/, ''))
}

function formatSurveySourceLabel(kind: 'theme' | 'gap', key: string, fallbackTitle?: string) {
  const title = fallbackTitle?.trim()
  if (title) {
    return title
  }
  return formatSurveySourceKey(kind, key)
}

function getSectionPurpose(sectionKey: string) {
  const purposeMap: Record<string, string> = {
    overview: '先确认当前记忆覆盖了多少论文、主题和 claims，判断这份综述的可靠范围。',
    themes: '把零散论文压缩成几个可复用研究主题，帮助你快速建立领域地图。',
    gaps: '列出仍未解决的问题和知识空白，适合决定下一步该查证什么。',
    opportunities: '把薄弱证据、边界不清和矛盾转成可验证的研究机会。',
    digest: '汇总已经提升到高层 synthesis 的共识、争议和演化认知。',
    sources: '按写回来源追溯每篇论文给当前 profile 带来的记忆贡献。',
    recent_changes: '说明最近一次写回让领域认知发生了什么变化。',
  }
  return purposeMap[sectionKey] || '阅读这一节以理解当前 profile 的一个派生视角。'
}

function SurveyStatCard({ title, value, hint }: { title: string; value: string | number; hint: string }) {
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

export default function LivingSurveyPage() {
  const { profileId } = useParams<{ profileId: string }>()
  const numericProfileId = useMemo(() => Number(profileId), [profileId])
  const [survey, setSurvey] = useState<LivingSurvey | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [contentLanguage, setContentLanguage] = useState<LocalizedContentLanguage>('zh')

  useEffect(() => {
    if (!profileId || Number.isNaN(numericProfileId)) {
      return
    }

    let cancelled = false
    const timer = window.setTimeout(() => {
      void getProfileSurvey(numericProfileId)
        .then((payload) => {
          if (cancelled) {
            return
          }
          setSurvey(payload)
          setError(null)
        })
        .catch((err) => {
          if (cancelled) {
            return
          }
          setError(err instanceof Error ? err.message : 'Failed to load living survey.')
        })
    }, 0)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [numericProfileId, profileId])

  if (!profileId || Number.isNaN(numericProfileId)) {
    return <p className="max-w-6xl mx-auto text-sm text-muted-foreground">No profile selected.</p>
  }

  if (error && !survey) {
    return (
      <div className="max-w-6xl mx-auto space-y-4">
        <Link to={`/profiles/${numericProfileId}`}>
          <Button variant="ghost" size="sm">
            <ArrowLeft className="mr-1 h-4 w-4" />
            Back
          </Button>
        </Link>
        <p className="text-sm text-destructive">{error}</p>
      </div>
    )
  }

  if (!survey) {
    return <p className="max-w-6xl mx-auto text-sm text-muted-foreground">Loading living survey...</p>
  }

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
              <h2 className="flex items-center gap-2 text-2xl font-bold">
                <FileText className="h-5 w-5" />
                {survey.profile_name} · 动态综述
              </h2>
              <p className="text-sm text-muted-foreground">面向阅读的动态领域综述：先帮你读懂当前方向，再把重要判断追溯回 Workspace 中的 claims、证据和来源论文。</p>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <div className="inline-flex items-center rounded-xl border border-border bg-background/90 p-1">
              <Button
                type="button"
                size="sm"
                variant={contentLanguage === 'zh' ? 'default' : 'ghost'}
                onClick={() => setContentLanguage('zh')}
              >
                <Languages className="mr-1 h-4 w-4" />
                中文
              </Button>
              <Button
                type="button"
                size="sm"
                variant={contentLanguage === 'en' ? 'default' : 'ghost'}
                onClick={() => setContentLanguage('en')}
              >
                EN
              </Button>
            </div>
            <Link to={`/profiles/${numericProfileId}/workspace`}>
              <Button variant="outline">
                <PanelRightOpen className="mr-2 h-4 w-4" />
                打开工作台
              </Button>
            </Link>
          </div>
        </div>

        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        <div className="grid grid-cols-1 gap-4 md:grid-cols-3 xl:grid-cols-4">
          <SurveyStatCard title="论文来源" value={survey.paper_count} hint="支撑当前综述的 writeback bundles 数量" />
          <SurveyStatCard title="研究主题" value={survey.theme_count} hint="从实体与 claims 聚合出的领域主题" />
          <SurveyStatCard title="追踪空白" value={survey.gap_count} hint="当前仍值得验证的问题和边界缺口" />
          <SurveyStatCard title="生成时间" value={formatDate(survey.generated_at)} hint="派生综述的最近构建时间" />
        </div>

        <Card>
          <CardHeader>
            <CardTitle>建议阅读路径</CardTitle>
            <CardDescription>这页允许高信息密度，但建议按顺序读，避免把派生项当成无上下文清单。</CardDescription>
          </CardHeader>
          <CardContent className="grid grid-cols-1 gap-3 md:grid-cols-4">
            {[
              ['1', '领域概览', '先确认覆盖范围和 claims 数量，知道这份综述能回答到什么程度。'],
              ['2', '研究主题', '再看主题如何组织，快速建立当前方向的骨架。'],
              ['3', '空白与机会', '接着看未解决问题和研究机会，判断下一步读什么或做什么实验。'],
              ['4', '来源追溯', '最后展开来源，回到工作台审查原始 claims、证据和论文来源。'],
            ].map(([step, title, description]) => (
              <div key={step} className="rounded-xl border bg-muted/20 p-3">
                <Badge variant="secondary">{step}</Badge>
                <p className="mt-2 text-sm font-medium">{title}</p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <LibraryBig className="h-4 w-4" />
              综述概览
            </CardTitle>
          </CardHeader>
          <CardContent>
            <LocalizedTextBlock localized={survey.overview_localized} className="text-sm leading-7" />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ListTree className="h-4 w-4" />
              目录与来源说明
            </CardTitle>
            <CardDescription>每个章节都对应一个用途；每个重要内容块都会尽量绑定 claim、synthesis、review 或 paper 来源。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {survey.sections.map((section) => (
              <a key={section.section_key} href={`#${section.section_key}`} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border px-3 py-2 text-sm hover:bg-muted/30">
                <span className="min-w-0 flex-1">
                  <span className="font-medium">{section.title_zh || section.title}</span>
                  <span className="mt-1 block text-xs leading-5 text-muted-foreground">{getSectionPurpose(section.section_key)}</span>
                </span>
                <Badge variant="outline">{section.blocks.length} 条内容</Badge>
              </a>
            ))}
          </CardContent>
        </Card>

        <div className="space-y-6">
          {survey.sections.map((section) => (
            <Card key={section.section_key} id={section.section_key}>
              <CardHeader>
                <CardTitle>
                  <LocalizedTextBlock localized={section.title_localized} textClassName="text-lg font-semibold" />
                </CardTitle>
                {(section.summary || section.summary_zh) ? (
                  <CardDescription>
                    <LocalizedTextBlock localized={section.summary_localized} className="text-sm leading-6" />
                  </CardDescription>
                ) : null}
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="rounded-xl border bg-muted/20 p-3 text-sm text-muted-foreground">{getSectionPurpose(section.section_key)}</p>
                {section.blocks.length === 0 ? (
                  <p className="text-sm text-muted-foreground">这一节目前只有章节摘要，还没有可展开的子内容。</p>
                ) : section.blocks.map((block) => {
                  const hasStructuredSources = Boolean(
                    block.theme_key
                    || block.gap_key
                    || block.claim_ids.length > 0
                    || block.synthesis_ids.length > 0
                    || block.review_ids.length > 0
                    || block.paper_ids.length > 0,
                  )
                  const titleForSource = block.title_zh || block.title
                  return (
                    <div key={block.block_key} className="rounded-xl border p-4">
                      <div className="flex flex-wrap gap-2">
                        {block.badges.map((badge, index) => (
                          <Badge key={`${block.block_key}-${index}`} variant={index === 0 ? 'secondary' : 'outline'}>
                            {badge}
                          </Badge>
                        ))}
                      </div>
                      <LocalizedTextBlock localized={block.title_localized} className="mt-3" textClassName="font-medium text-base" />
                      {(block.summary || block.summary_zh) ? (
                        <LocalizedTextBlock localized={block.summary_localized} className="mt-2 text-sm text-muted-foreground leading-6" />
                      ) : null}
                      <details className="mt-3 rounded-lg border bg-muted/20 px-3 py-2">
                        <summary className="cursor-pointer text-xs font-medium text-muted-foreground">来源追溯</summary>
                        <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
                          {block.theme_key ? <Badge variant="outline">主题：{formatSurveySourceLabel('theme', block.theme_key, section.section_key === 'themes' ? titleForSource : undefined)}</Badge> : null}
                          {block.gap_key ? <Badge variant="outline">知识空白：{formatSurveySourceLabel('gap', block.gap_key, section.section_key === 'gaps' ? titleForSource : undefined)}</Badge> : null}
                          {block.claim_ids.length > 0 ? <Badge variant="outline">Claims {block.claim_ids.length}</Badge> : null}
                          {block.synthesis_ids.length > 0 ? <Badge variant="outline">高层认知 {block.synthesis_ids.length}</Badge> : null}
                          {block.review_ids.length > 0 ? <Badge variant="outline">审阅项 {block.review_ids.length}</Badge> : null}
                          {block.paper_ids.length > 0 ? <Badge variant="outline">论文来源 {block.paper_ids.length}</Badge> : null}
                          {!hasStructuredSources ? <span>暂无结构化来源 ID。</span> : null}
                        </div>
                        {hasStructuredSources ? (
                          <div className="mt-3 flex justify-end">
                            <Link to={`/profiles/${numericProfileId}/workspace`} className="w-full sm:w-auto">
                              <Button size="sm" variant="outline" className={cn('h-auto min-h-7 w-full whitespace-normal text-center sm:w-auto')}>
                                <PanelRightOpen className="mr-2 h-4 w-4" />
                                打开工作台查看来源对象
                              </Button>
                            </Link>
                          </div>
                        ) : null}
                      </details>
                    </div>
                  )
                })}
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    </LocalizedTextLanguageProvider>
  )
}
