import type { JobReportSummary } from '@/api/client'

/** Read a numeric value from a job report's diagnostic snapshot. */
export function readDiagnosticNumber(item: JobReportSummary, key: string) {
  const value = item.diagnostic_snapshot?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

/** Read a promotion count (accepted / review_required / rejected) from a job report's diagnostic snapshot. */
export function readPromotionCount(item: JobReportSummary, key: 'accepted' | 'review_required' | 'rejected') {
  const payload = item.diagnostic_snapshot?.promotion_counts
  if (!payload || typeof payload !== 'object') {
    return 0
  }
  const value = (payload as Record<string, unknown>)[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}
