import { createContext, useContext } from 'react'
import type { LocalizedText } from '@/api/client'

export type LocalizedContentLanguage = 'zh' | 'en'

export const LocalizedTextLanguageContext = createContext<LocalizedContentLanguage>('zh')

export function useLocalizedTextLanguage() {
  return useContext(LocalizedTextLanguageContext)
}

export function resolveLocalizedText(
  localized?: LocalizedText | null,
  language: LocalizedContentLanguage = 'zh',
) {
  const primary = localized?.primary?.trim() || ''
  const zh = localized?.zh?.trim() || ''
  const en = localized?.en?.trim() || ''
  if (language === 'en') {
    return en || primary || zh
  }
  return zh || primary || en
}
