import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { deleteProfile, getProfileDetail, type Profile } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'

interface DeleteProfileDialogProps {
  profile: Profile
  trigger: ReactNode
  onDeleted?: (profileId: number) => void | Promise<void>
}

function formatRequestError(error: unknown, fallback: string) {
  if (!(error instanceof Error)) {
    return fallback
  }
  const raw = error.message.replace(/^\d+:\s*/, '').trim()
  try {
    const payload = JSON.parse(raw) as { detail?: string }
    if (typeof payload.detail === 'string' && payload.detail.trim()) {
      return payload.detail.trim()
    }
  } catch {
    return raw || fallback
  }
  return raw || fallback
}

export default function DeleteProfileDialog({
  profile,
  trigger,
  onDeleted,
}: DeleteProfileDialogProps) {
  const [open, setOpen] = useState(false)
  const [confirmText, setConfirmText] = useState('')
  const [isDeleting, setIsDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activityCount, setActivityCount] = useState<number | null>(null)
  const [reportCount, setReportCount] = useState<number | null>(null)
  const [isLoadingCounts, setIsLoadingCounts] = useState(false)

  const expectedText = useMemo(() => `DELETE ${profile.name}`, [profile.name])
  const isConfirmed = confirmText.trim() === expectedText

  useEffect(() => {
    if (!open) {
      setConfirmText('')
      setError(null)
      return
    }

    let cancelled = false
    setIsLoadingCounts(true)
    getProfileDetail(profile.id)
      .then((detail) => {
        if (cancelled) {
          return
        }
        setActivityCount(detail.activity.length)
        setReportCount(detail.activity.filter((item) => Boolean(item.job_report_path)).length)
      })
      .catch((loadError) => {
        console.error(loadError)
        if (!cancelled) {
          setActivityCount(null)
          setReportCount(null)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoadingCounts(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [open, profile.id])

  const handleDelete = async () => {
    if (!isConfirmed || isDeleting) {
      return
    }
    setIsDeleting(true)
    setError(null)
    try {
      await deleteProfile(profile.id)
      await onDeleted?.(profile.id)
      setOpen(false)
    } catch (deleteError) {
      setError(
        formatRequestError(
          deleteError,
          'Failed to delete this profile and its related resources.',
        ),
      )
    } finally {
      setIsDeleting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !isDeleting && setOpen(nextOpen)}>
      <DialogTrigger>{trigger}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete Profile</DialogTitle>
          <DialogDescription>
            This permanently removes the profile, its saved memory, and all job/report resources linked to it.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 text-sm">
          <div className="rounded-lg border bg-muted/30 p-3">
            <p className="font-medium">{profile.name}</p>
            <p className="mt-1 text-muted-foreground">
              {profile.description || 'No description.'}
            </p>
          </div>

          <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-amber-900">
            <p>
              {isLoadingCounts
                ? 'Checking linked jobs and reports...'
                : `Estimated cleanup: ${activityCount ?? 0} linked jobs, ${reportCount ?? 0} reports.`}
            </p>
            <p className="mt-1 text-xs">
              Active jobs will block deletion. Default profile cannot be deleted.
            </p>
          </div>

          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">
              Type <code>{expectedText}</code> to confirm.
            </p>
            <Input
              value={confirmText}
              onChange={(event) => setConfirmText(event.target.value)}
              placeholder={expectedText}
            />
          </div>

          {error ? <p className="text-sm text-destructive">{error}</p> : null}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={isDeleting}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={handleDelete} disabled={!isConfirmed || isDeleting}>
            {isDeleting ? 'Deleting...' : 'Delete Profile'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
