import type { LocalizedText } from '@/api/client'
import { cn } from '@/lib/utils'
import { useLocalizedTextLanguage, resolveLocalizedText, type LocalizedContentLanguage } from '@/lib/localizedText'

interface LocalizedTextBlockProps {
  localized?: LocalizedText | null
  className?: string
  textClassName?: string
  emptyText?: string
  englishTitle?: string
  englishDescription?: string
  as?: 'p' | 'div' | 'span'
  preserveWhitespace?: boolean
  language?: LocalizedContentLanguage
}

export default function LocalizedTextBlock({
  localized,
  className,
  textClassName,
  emptyText = '暂无内容',
  englishTitle = '查看英文原文',
  englishDescription = '这里展示当前记忆对象的英文 source-of-truth。编辑时只修改英文内容，中文展示会自动更新。',
  as = 'p',
  preserveWhitespace = false,
  language,
}: LocalizedTextBlockProps) {
  void englishTitle
  void englishDescription
  const contextLanguage = useLocalizedTextLanguage()
  const activeLanguage = language ?? contextLanguage
  const primary = resolveLocalizedText(localized, activeLanguage)
  const Tag = as

  return (
    <Tag
      className={cn(
        'min-w-0 text-sm text-foreground',
        preserveWhitespace && 'whitespace-pre-wrap',
        className,
        textClassName,
      )}
    >
      {primary || emptyText}
    </Tag>
  )
}
