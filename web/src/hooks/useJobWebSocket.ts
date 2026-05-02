import { useEffect, useRef, useState } from 'react'
import { getJob, type Job } from '@/api/client'

export interface LogEntry {
  level: string
  logger: string
  message: string
  timestamp: number
}

interface UseJobWebSocketReturn {
  logs: LogEntry[]
  job: Job | null
  isConnected: boolean
  isDone: boolean
}

export interface SocketState {
  jobId: string | null
  logs: LogEntry[]
  job: Job | null
  isConnected: boolean
  isDone: boolean
}

export const INITIAL_SOCKET_STATE: SocketState = {
  jobId: null,
  logs: [],
  job: null,
  isConnected: false,
  isDone: false,
}

interface UseJobWebSocketOptions {
  initialState?: SocketState
  onStateChange?: (state: SocketState) => void
}

const TERMINAL_STATUSES = new Set(['completed', 'failed'])
const MAX_RECONNECT_ATTEMPTS = 5
const RECONNECT_BASE_DELAY_MS = 1000
const POLL_FALLBACK_DELAY_MS = 5000

function buildJobState(jobId: string, previous: SocketState, updates: Partial<SocketState>): SocketState {
  const base = previous.jobId === jobId ? previous : { ...INITIAL_SOCKET_STATE, jobId }
  return {
    ...base,
    ...updates,
  }
}

export function useJobWebSocket(jobId: string | null, options?: UseJobWebSocketOptions): UseJobWebSocketReturn {
  const [state, setState] = useState<SocketState>(() => {
    const initialState = options?.initialState
    if (!initialState || initialState.jobId !== jobId) {
      return INITIAL_SOCKET_STATE
    }
    return initialState
  })
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimerRef = useRef<number | null>(null)
  const reconnectAttemptsRef = useRef(0)

  useEffect(() => {
    if (!jobId) {
      return
    }

    let disposed = false
    let shouldReconnect = true

    const clearReconnectTimer = () => {
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
    }

    const syncJobState = async (): Promise<Job | null> => {
      try {
        const job = await getJob(jobId)
        if (disposed) {
          return job
        }
        setState((prev) => buildJobState(jobId, prev, {
          job,
          isDone: TERMINAL_STATUSES.has(job.status),
        }))
        return job
      } catch (err) {
        if (disposed) {
          return null
        }
        // Job record deleted (force-stop / purge) — treat as terminal so
        // we don't attempt a WebSocket connection or reconnect loop.
        const is404 = err instanceof Error && err.message.startsWith('404')
        if (is404) {
          shouldReconnect = false
          setState((prev) => buildJobState(jobId, prev, {
            isDone: true,
            isConnected: false,
          }))
          return null
        }
        setState((prev) => buildJobState(jobId, prev, {
          isConnected: false,
        }))
        return null
      }
    }

    const scheduleReconnect = (delayOverride?: number) => {
      if (disposed || !shouldReconnect || reconnectTimerRef.current !== null) {
        return
      }
      const delay = delayOverride ?? (
        reconnectAttemptsRef.current >= MAX_RECONNECT_ATTEMPTS
          ? POLL_FALLBACK_DELAY_MS
          : RECONNECT_BASE_DELAY_MS * 2 ** reconnectAttemptsRef.current
      )
      if (reconnectAttemptsRef.current < MAX_RECONNECT_ATTEMPTS) {
        reconnectAttemptsRef.current += 1
      }
      reconnectTimerRef.current = window.setTimeout(() => {
        reconnectTimerRef.current = null
        void connect()
      }, delay)
    }

    const connect = async () => {
      if (disposed || !shouldReconnect) {
        return
      }

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const ws = new WebSocket(`${protocol}//${window.location.host}/api/jobs/${jobId}/ws`)
      wsRef.current = ws

      ws.onopen = () => {
        reconnectAttemptsRef.current = 0
        clearReconnectTimer()
        setState((prev) => buildJobState(jobId, prev, { isConnected: true }))
      }

      ws.onclose = () => {
        if (disposed) {
          return
        }
        setState((prev) => (prev.jobId === jobId ? { ...prev, isConnected: false } : prev))
        // If we already received a "done" message (or know the job is
        // gone), skip the sync HTTP call to avoid noisy 404s after
        // force-stop / purge.
        if (!shouldReconnect) {
          return
        }
        void syncJobState().then((job) => {
          if (job && TERMINAL_STATUSES.has(job.status)) {
            shouldReconnect = false
            return
          }
          if (!shouldReconnect || disposed) {
            return
          }
          scheduleReconnect()
        })
      }

      ws.onerror = () => {
        if (disposed) {
          return
        }
        setState((prev) => (prev.jobId === jobId ? { ...prev, isConnected: false } : prev))
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.type === 'log') {
            setState((prev) => buildJobState(jobId, prev, {
              logs: [...(prev.jobId === jobId ? prev.logs : []), data as LogEntry],
              isConnected: true,
            }))
          } else if (data.type === 'state' && data.job) {
            const nextJob = data.job as Job
            setState((prev) => buildJobState(jobId, prev, {
              job: nextJob,
              isConnected: true,
              isDone: TERMINAL_STATUSES.has(nextJob.status),
            }))
          } else if (data.type === 'done') {
            shouldReconnect = false
            setState((prev) => buildJobState(jobId, prev, {
              job: (data.job as Job | null) ?? prev.job,
              isDone: true,
              isConnected: false,
            }))
            clearReconnectTimer()
            if (wsRef.current === ws) {
              wsRef.current = null
            }
            ws.close()
          } else if (data.type === 'heartbeat') {
            setState((prev) => buildJobState(jobId, prev, { isConnected: true }))
          }
        } catch {
          // ignore parse errors
        }
      }
    }

    void syncJobState().then((job) => {
      if (disposed) {
        return
      }
      if (job && TERMINAL_STATUSES.has(job.status)) {
        shouldReconnect = false
        return
      }
      void connect()
    })

    return () => {
      disposed = true
      shouldReconnect = false
      clearReconnectTimer()
      reconnectAttemptsRef.current = 0
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [jobId])

  useEffect(() => {
    options?.onStateChange?.(state)
  }, [options, state])

  const isCurrentJob = state.jobId === jobId

  return {
    logs: isCurrentJob ? state.logs : [],
    job: isCurrentJob ? state.job : null,
    isConnected: isCurrentJob ? state.isConnected : false,
    isDone: isCurrentJob ? state.isDone : false,
  }
}
