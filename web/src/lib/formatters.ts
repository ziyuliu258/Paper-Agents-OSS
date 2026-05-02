/**
 * Shared pure formatting helpers.
 *
 * Only functions whose implementations are verified-identical across
 * multiple call sites belong here.  Do NOT add anything with
 * site-specific default values or locale variations.
 */

/** Format a UNIX-epoch timestamp (seconds) to a zh-CN locale string. */
export function formatDate(timestamp?: number | null) {
  if (!timestamp) {
    return '未知'
  }
  return new Date(timestamp * 1000).toLocaleString('zh-CN')
}

/**
 * Collapse whitespace, trim, and truncate with ellipsis.
 *
 * Callers MUST supply an explicit `limit` – no default is provided
 * on purpose so that every call site documents its own threshold.
 */
export function clipText(text: string, limit: number) {
  const cleaned = text.replace(/\s+/g, ' ').trim()
  if (cleaned.length <= limit) {
    return cleaned
  }
  return `${cleaned.slice(0, limit - 3).trimEnd()}...`
}
