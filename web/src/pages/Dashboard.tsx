import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { FileText, Brain, History, Library, Play } from 'lucide-react'
import { getStats, getJobReports, type JobReportSummary } from '@/api/client'

export default function Dashboard() {
  const [stats, setStats] = useState({ jobs_total: 0, jobs_running: 0, papers_total: 0, reports_total: 0, profiles_total: 0 })
  const [reports, setReports] = useState<JobReportSummary[]>([])

  useEffect(() => {
    getStats().then(setStats).catch(console.error)
    getJobReports().then(r => setReports(r.reports.slice(0, 5))).catch(console.error)
  }, [])

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">Dashboard</h2>
        <Link to="/run">
          <Button><Play className="w-4 h-4 mr-2" />New Run</Button>
        </Link>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground flex items-center gap-2"><FileText className="w-4 h-4" />Reports</CardTitle></CardHeader>
          <CardContent><p className="text-3xl font-bold">{stats.reports_total}</p></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground flex items-center gap-2"><Library className="w-4 h-4" />Papers</CardTitle></CardHeader>
          <CardContent><p className="text-3xl font-bold">{stats.papers_total}</p></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground flex items-center gap-2"><History className="w-4 h-4" />Jobs</CardTitle></CardHeader>
          <CardContent><p className="text-3xl font-bold">{stats.jobs_total}{stats.jobs_running > 0 && <span className="text-sm text-green-500 ml-2">({stats.jobs_running} running)</span>}</p></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground flex items-center gap-2"><Brain className="w-4 h-4" />Profiles</CardTitle></CardHeader>
          <CardContent><p className="text-3xl font-bold">{stats.profiles_total}</p></CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle>Recent Reports</CardTitle></CardHeader>
        <CardContent>
          {reports.length === 0 ? (
            <p className="text-muted-foreground text-sm">No reports yet. Start a run to generate your first report.</p>
          ) : (
            <div className="space-y-3">
              {reports.map(r => (
                <Link key={r.job_id} to={`/reports/job/${encodeURIComponent(r.job_id)}`} className="block">
                  <div className="flex items-center justify-between p-3 rounded-lg border hover:bg-accent/50 transition-colors">
                    <div>
                      <p className="font-medium text-sm">{r.title || r.paper_title || r.job_id}</p>
                      <p className="text-xs text-muted-foreground mt-0.5 font-mono">Job {r.job_id}</p>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {new Date(r.modified_at * 1000).toLocaleString()} · {(r.size_bytes / 1024).toFixed(1)} KB
                      </p>
                    </div>
                    <Badge variant="secondary">View</Badge>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
