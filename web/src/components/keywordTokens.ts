export function normalizeKeywordToken(token: string) {
  return token.replace(/\s+/g, ' ').trim()
}

export function mergeKeywordTokens(existing: string[], incoming: string[]) {
  const seen = new Set<string>()
  const merged: string[] = []

  for (const token of [...existing, ...incoming]) {
    const normalized = normalizeKeywordToken(token)
    const key = normalized.toLocaleLowerCase()
    if (!normalized || seen.has(key)) {
      continue
    }
    seen.add(key)
    merged.push(normalized)
  }

  return merged
}
