import { useState, type ReactNode } from 'react'
import { createProfile, type Profile } from '@/api/client'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'

interface CreateProfileDialogProps {
  trigger: ReactNode
  onCreated?: (profile: Profile) => void | Promise<void>
  title?: string
  description?: string
}

const DEFAULT_DESCRIPTION =
  'Create a dedicated profile for a research domain, task preference, or writing style so the system can accumulate more relevant long-term memory.'

export default function CreateProfileDialog({
  trigger,
  onCreated,
  title = 'Create Profile',
  description = DEFAULT_DESCRIPTION,
}: CreateProfileDialogProps) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [profileDescription, setProfileDescription] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const resetForm = () => {
    setName('')
    setProfileDescription('')
    setSubmitting(false)
    setError('')
  }

  const handleOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen)
    if (!nextOpen) {
      resetForm()
    }
  }

  const handleCreate = async () => {
    if (!name.trim()) {
      setError('Profile name is required.')
      return
    }

    setSubmitting(true)
    setError('')

    try {
      const profile = await createProfile(name.trim(), profileDescription.trim())
      await onCreated?.(profile)
      resetForm()
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create profile.')
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger>{trigger}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <p className="text-xs font-medium text-foreground">Profile name</p>
            <Input
              placeholder="e.g. Medical imaging — rare disease detection"
              value={name}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setName(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <p className="text-xs font-medium text-foreground">Description</p>
            <Input
              placeholder="e.g. Focus on method details, ablations, and a more in-depth interpretation style"
              value={profileDescription}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setProfileDescription(e.target.value)}
            />
          </div>

          <div className="rounded-lg border bg-muted/30 p-3 text-xs leading-5 text-muted-foreground">
            Try to describe the research domain, task priority, or output preference. That makes each profile easier to distinguish from the Run page.
          </div>

          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>

        <DialogFooter showCloseButton>
          <Button onClick={handleCreate} disabled={submitting || !name.trim()}>
            {submitting ? 'Creating...' : 'Create Profile'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
