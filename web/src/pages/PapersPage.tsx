import { useEffect, useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { FileText } from 'lucide-react'
import { getPaperPdfUrl, getPapers, type Paper } from '@/api/client'

export default function PapersPage() {
  const [papers, setPapers] = useState<Paper[]>([])
  const [search, setSearch] = useState('')

  const fetchPapers = (q = '') => {
    getPapers(50, q).then(setPapers).catch(console.error)
  }

  useEffect(() => { fetchPapers() }, [])

  const handleSearch = () => fetchPapers(search)

  const handleOpenSource = (paper: Paper) => {
    if (!paper.source_path && !paper.pdf_path) {
      return
    }
    window.open(getPaperPdfUrl(paper.id), '_blank', 'noopener,noreferrer')
  }

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <h2 className="text-2xl font-bold">Papers Library</h2>
      <div className="flex gap-2">
        <Input
          placeholder="Search by title, venue, ID..."
          value={search}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setSearch(e.target.value)}
          onKeyDown={(e: React.KeyboardEvent) => e.key === 'Enter' && handleSearch()}
        />
        <Button onClick={handleSearch} variant="secondary">Search</Button>
      </div>
      {papers.length === 0 ? (
        <p className="text-muted-foreground">No papers found.</p>
      ) : (
        <div className="space-y-3">
          {papers.map(p => (
            <Card key={p.id}>
              <CardContent className="p-4 flex items-center justify-between">
                <div className="space-y-1">
                  <p className="font-medium text-sm">{p.title || p.paper_id}</p>
                  <div className="flex gap-2 flex-wrap">
                    {p.venue && <Badge variant="secondary">{p.venue}</Badge>}
                    {p.match_track && <Badge variant="outline">{p.match_track}</Badge>}
                    {p.source && <Badge variant="outline">{p.source}</Badge>}
                    <Badge variant="outline">{p.source_type === 'html' ? 'HTML Source' : 'PDF Source'}</Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">{p.pub_date} · {p.paper_id}</p>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleOpenSource(p)}
                  disabled={!p.source_path && !p.pdf_path}
                >
                  <FileText className="w-4 h-4 mr-1" />
                  {p.source_path || p.pdf_path ? (p.source_type === 'html' ? 'Open Source' : 'Open PDF') : 'Source Unavailable'}
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
