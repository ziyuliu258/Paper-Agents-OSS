import { useState } from 'react'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { mergeKeywordTokens, normalizeKeywordToken } from '@/components/keywordTokens'

interface KeywordTokenInputProps {
  value: string[]
  onChange: (keywords: string[]) => void
  placeholder?: string
  className?: string
}

export default function KeywordTokenInput({
  value,
  onChange,
  placeholder = 'Keywords; split with semicolons',
  className,
}: KeywordTokenInputProps) {
  const [draft, setDraft] = useState('')

  const commitTokens = (tokens: string[]) => {
    const next = mergeKeywordTokens(value, tokens)
    if (next.length !== value.length || next.some((token, index) => token !== value[index])) {
      onChange(next)
    }
  }

  const commitDraft = () => {
    const normalized = normalizeKeywordToken(draft)
    if (!normalized) {
      setDraft('')
      return
    }
    commitTokens([normalized])
    setDraft('')
  }

  const handleDraftChange = (nextValue: string) => {
    if (!/[;；]/.test(nextValue)) {
      setDraft(nextValue)
      return
    }

    const parts = nextValue.split(/[;；]/)
    const completed = parts.slice(0, -1)
    const remainder = parts.at(-1) ?? ''

    commitTokens(completed)
    setDraft(remainder)
  }

  const removeToken = (tokenToRemove: string) => {
    onChange(value.filter((token) => token !== tokenToRemove))
  }

  return (
    <div
      className={cn(
        'flex min-h-8 w-full flex-wrap items-center gap-1.5 rounded-lg border border-input bg-transparent px-2.5 py-1.5 transition-colors focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50',
        className,
      )}
    >
      {value.map((token) => (
        <span
          key={token}
          className="inline-flex items-center gap-1 rounded-full border border-black/70 bg-white px-2 py-0.5 text-xs text-black"
        >
          <span>{token}</span>
          <button
            type="button"
            onClick={() => removeToken(token)}
            className="inline-flex size-3.5 items-center justify-center rounded-full text-black/60 transition-colors hover:bg-black/10 hover:text-black focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-black/30"
            aria-label={`Remove ${token}`}
          >
            <X className="size-3" />
          </button>
        </span>
      ))}

      <input
        value={draft}
        onChange={(e) => handleDraftChange(e.target.value)}
        onBlur={commitDraft}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            commitDraft()
            return
          }

          if (e.key === 'Backspace' && !draft && value.length > 0) {
            e.preventDefault()
            onChange(value.slice(0, -1))
          }
        }}
        className="h-6 min-w-[160px] flex-1 border-0 bg-transparent p-0 text-sm outline-none placeholder:text-muted-foreground"
        placeholder={placeholder}
      />
    </div>
  )
}
