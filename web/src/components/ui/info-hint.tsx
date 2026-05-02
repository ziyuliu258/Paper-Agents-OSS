import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Info } from 'lucide-react'
import { cn } from '@/lib/utils'

interface InfoHintProps {
  content: ReactNode
  label?: string
  className?: string
  panelClassName?: string
}

const TOOLTIP_WIDTH = 288
const VIEWPORT_MARGIN = 12
const TOOLTIP_GAP = 8
const MIN_BOTTOM_SPACE = 120

interface TooltipPosition {
  left: number
  top: number
  placement: 'top' | 'bottom'
}

export default function InfoHint({
  content,
  label = 'More information',
  className,
  panelClassName,
}: InfoHintProps) {
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<TooltipPosition>({
    left: VIEWPORT_MARGIN,
    top: VIEWPORT_MARGIN,
    placement: 'bottom',
  })
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const tooltipId = useId()

  useEffect(() => {
    if (!open) return

    const updatePosition = () => {
      const rect = triggerRef.current?.getBoundingClientRect()
      if (!rect) return

      const left = Math.min(
        Math.max(rect.left + rect.width / 2 - TOOLTIP_WIDTH / 2, VIEWPORT_MARGIN),
        window.innerWidth - TOOLTIP_WIDTH - VIEWPORT_MARGIN,
      )

      const hasRoomBelow = window.innerHeight - rect.bottom >= MIN_BOTTOM_SPACE
      setPosition({
        left,
        top: hasRoomBelow ? rect.bottom + TOOLTIP_GAP : rect.top - TOOLTIP_GAP,
        placement: hasRoomBelow ? 'bottom' : 'top',
      })
    }

    updatePosition()
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)

    return () => {
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [open])

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-label={label}
        aria-describedby={open ? tooltipId : undefined}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        className={cn(
          'inline-flex size-4 items-center justify-center rounded-full border border-muted-foreground/30 text-muted-foreground transition-colors hover:border-foreground/30 hover:text-foreground focus-visible:outline-none focus-visible:border-foreground/20',
          className,
        )}
      >
        <Info className="size-3" />
      </button>

      {open && typeof document !== 'undefined' && createPortal(
        <div
          id={tooltipId}
          role="tooltip"
          className={cn(
            'pointer-events-none fixed z-[70] rounded-lg border bg-popover p-3 text-xs leading-5 text-popover-foreground shadow-md',
            panelClassName,
          )}
          style={{
            left: position.left,
            top: position.top,
            width: TOOLTIP_WIDTH,
            transform: position.placement === 'top' ? 'translateY(-100%)' : undefined,
          }}
        >
          {content}
        </div>,
        document.body,
      )}
    </>
  )
}
