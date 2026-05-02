import type { ReactNode } from 'react'
import { LocalizedTextLanguageContext, type LocalizedContentLanguage } from '@/lib/localizedText'

export default function LocalizedTextLanguageProvider({
  language,
  children,
}: {
  language: LocalizedContentLanguage
  children: ReactNode
}) {
  return (
    <LocalizedTextLanguageContext.Provider value={language}>
      {children}
    </LocalizedTextLanguageContext.Provider>
  )
}
