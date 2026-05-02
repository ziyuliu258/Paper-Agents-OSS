import { useEffect, useRef } from 'react'
import type { LogEntry } from '@/hooks/useJobWebSocket'
import { ScrollArea } from '@/components/ui/scroll-area'

const LEVEL_COLORS: Record<string, string> = {
  ERROR: 'text-red-400',
  WARNING: 'text-yellow-400',
  INFO: 'text-green-400',
  DEBUG: 'text-gray-500',
}

export default function LogStream({ logs }: { logs: LogEntry[] }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const viewport = containerRef.current?.querySelector<HTMLElement>('[data-slot="scroll-area-viewport"]')
    if (!viewport) {
      return
    }

    viewport.scrollTo({
      top: viewport.scrollHeight,
      behavior: 'smooth',
    })
  }, [logs.length])

  return (
    <div ref={containerRef}>
      <ScrollArea className="h-80 rounded-lg border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs">
        {logs.length === 0 && <p className="text-zinc-600">Waiting for logs...</p>}
        {logs.map((entry, i) => (
          <div key={i} className="leading-5">
            <span className={LEVEL_COLORS[entry.level] || 'text-zinc-400'}>{entry.message}</span>
          </div>
        ))}
      </ScrollArea>
    </div>
  )
}
