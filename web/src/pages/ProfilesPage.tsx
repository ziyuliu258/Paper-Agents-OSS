import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Plus, Brain, ArrowRight, Trash2 } from 'lucide-react'
import { getProfiles, type Profile } from '@/api/client'
import CreateProfileDialog from '@/components/CreateProfileDialog'
import DeleteProfileDialog from '@/components/DeleteProfileDialog'

export default function ProfilesPage() {
  const [profiles, setProfiles] = useState<Profile[]>([])

  const refresh = () => getProfiles().then(setProfiles).catch(console.error)
  useEffect(() => { refresh() }, [])

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div className="space-y-1">
          <h2 className="text-2xl font-bold">Memory Profiles</h2>
          <p className="text-sm text-muted-foreground">
            Use profiles to separate long-term memory by research domain, task preference, or writing style. Any profile created here is immediately available from the Run page.
          </p>
        </div>

        <CreateProfileDialog
          trigger={
            <Button><Plus className="mr-2 h-4 w-4" />New Profile</Button>
          }
          description="Create a profile with a memorable name and a short description so it is easy to recognize later from the Run page."
          onCreated={refresh}
        />
      </div>

      <Card size="sm">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">How to name a useful profile</CardTitle>
          <CardDescription>
            A good pattern is: research domain + task focus + output preference. For example, “Medical imaging — lesion detection — more detailed methods”.
          </CardDescription>
        </CardHeader>
      </Card>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {profiles.map(p => (
          <Link key={p.id} to={`/profiles/${p.id}`} className="block">
            <Card className="transition-colors hover:border-foreground/20 hover:bg-muted/10">
            <CardHeader className="pb-2">
              <div className="flex items-start justify-between gap-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Brain className="h-4 w-4" />
                  {p.name}
                </CardTitle>
                <div onClick={(event) => {
                  event.preventDefault()
                  event.stopPropagation()
                }}
                >
                  <DeleteProfileDialog
                    profile={p}
                    onDeleted={refresh}
                    trigger={
                      <Button variant="ghost" size="icon" aria-label={`Delete profile ${p.name}`}>
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    }
                  />
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <p className="mb-2 text-sm text-muted-foreground">{p.description || 'No description yet. Add one when creating future profiles to make them easier to distinguish from the Run page.'}</p>
              <div className="flex flex-wrap gap-2 text-xs">
                <Badge variant="secondary">{p.paper_count} papers</Badge>
                <Badge variant="outline">Last used: {new Date(p.last_used_at * 1000).toLocaleDateString()}</Badge>
              </div>
              <div className="mt-4 inline-flex items-center gap-1 text-sm text-muted-foreground">
                Open details
                <ArrowRight className="h-3.5 w-3.5" />
              </div>
            </CardContent>
          </Card>
          </Link>
        ))}
      </div>
    </div>
  )
}
