export const LOCAL_RUNTIME_SETTINGS_KEY = 'paper-agent.runtime-settings.local'
export const LOCAL_DEFAULT_RUN_CONFIG_KEY = 'paper-agent.default-run-config.local'
export const ENV_RUNTIME_AUTH_TOKEN_KEY = 'paper-agent.env-runtime-auth.token'
export const RUNTIME_OVERRIDE_HEADER_NAME = 'x-paper-agent-runtime'
export const ENV_RUNTIME_AUTH_HEADER_NAME = 'x-paper-agent-env-auth'

export interface EnvRuntimeUnlockChallenge {
  protected: boolean
  algorithm: string
  iterations: number
  salt: string
  challenge_id: string
  nonce: string
  expires_at: number
}

export type RuntimeSecretPath =
  | 'providers.openai.api_key'
  | 'providers.lite.api_key'
  | 'providers.embedding.api_key'
  | 'providers.semantic_scholar.api_key'
  | 'providers.mineru.api_key'
  | 'providers.r2.access_key_id'
  | 'providers.r2.secret_access_key'

export interface LocalRuntimeSettings {
  providers?: {
    openai?: {
      base_url?: string
      api_key?: string
    }
    lite?: {
      base_url?: string
      api_key?: string
    }
    embedding?: {
      base_url?: string
      api_key?: string
      model?: string
    }
    semantic_scholar?: {
      api_key?: string
    }
    mineru?: {
      api_key?: string
    }
    r2?: {
      endpoint?: string
      bucket?: string
      access_key_id?: string
      secret_access_key?: string
      public_base_url?: string
    }
    network?: {
      proxy_port?: number | null
    }
  }
  model_aliases?: Record<string, string>
}

function pruneEmptyObject<T>(value: T): T | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return value
  }
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, item]) => item !== undefined)
  if (entries.length === 0) {
    return undefined
  }
  return Object.fromEntries(entries) as T
}

function trimText(value: unknown): string | undefined {
  const text = String(value ?? '').trim()
  return text || undefined
}

function normalizeProxyPort(value: unknown): number | null | undefined {
  if (value === null) {
    return null
  }
  const text = String(value ?? '').trim()
  if (!text) {
    return undefined
  }
  const port = Number.parseInt(text, 10)
  if (!Number.isFinite(port) || port < 1 || port > 65535) {
    return undefined
  }
  return port
}

function sanitizeRuntimeSettings(payload: LocalRuntimeSettings | null | undefined): LocalRuntimeSettings {
  const providers = payload?.providers || {}
  const aliases = payload?.model_aliases || {}

  const sanitizedProviders = pruneEmptyObject({
    openai: pruneEmptyObject({
      base_url: trimText(providers.openai?.base_url),
      api_key: trimText(providers.openai?.api_key),
    }),
    lite: pruneEmptyObject({
      base_url: trimText(providers.lite?.base_url),
      api_key: trimText(providers.lite?.api_key),
    }),
    embedding: pruneEmptyObject({
      base_url: trimText(providers.embedding?.base_url),
      api_key: trimText(providers.embedding?.api_key),
      model: trimText(providers.embedding?.model),
    }),
    semantic_scholar: pruneEmptyObject({
      api_key: trimText(providers.semantic_scholar?.api_key),
    }),
    mineru: pruneEmptyObject({
      api_key: trimText(providers.mineru?.api_key),
    }),
    r2: pruneEmptyObject({
      endpoint: trimText(providers.r2?.endpoint),
      bucket: trimText(providers.r2?.bucket),
      access_key_id: trimText(providers.r2?.access_key_id),
      secret_access_key: trimText(providers.r2?.secret_access_key),
      public_base_url: trimText(providers.r2?.public_base_url),
    }),
    network: pruneEmptyObject({
      proxy_port: normalizeProxyPort(providers.network?.proxy_port),
    }),
  })

  const sanitizedAliasEntries = Object.entries(aliases)
    .map(([key, value]) => [key, trimText(value)] as const)
    .filter((entry): entry is [string, string] => Boolean(entry[1]))
  const sanitizedAliases = sanitizedAliasEntries.length > 0
    ? Object.fromEntries(sanitizedAliasEntries) as Record<string, string>
    : undefined

  return pruneEmptyObject({
    providers: sanitizedProviders,
    model_aliases: sanitizedAliases,
  }) || {}
}

function sanitizeDefaultRunConfig(payload: Record<string, unknown> | null | undefined): Record<string, unknown> {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return {}
  }
  return JSON.parse(JSON.stringify(payload)) as Record<string, unknown>
}

function readJsonStorage<T>(key: string): T | null {
  if (typeof window === 'undefined') {
    return null
  }
  try {
    const raw = window.localStorage.getItem(key)
    if (!raw) {
      return null
    }
    return JSON.parse(raw) as T
  } catch {
    return null
  }
}

function writeJsonStorage(key: string, value: unknown) {
  if (typeof window === 'undefined') {
    return
  }
  if (!value || (typeof value === 'object' && Object.keys(value as Record<string, unknown>).length === 0)) {
    window.localStorage.removeItem(key)
    return
  }
  window.localStorage.setItem(key, JSON.stringify(value))
}

function readSessionText(key: string): string {
  if (typeof window === 'undefined') {
    return ''
  }
  return window.sessionStorage.getItem(key) || ''
}

function writeSessionText(key: string, value: string) {
  if (typeof window === 'undefined') {
    return
  }
  if (!value.trim()) {
    window.sessionStorage.removeItem(key)
    return
  }
  window.sessionStorage.setItem(key, value)
}

function deepMerge(base: Record<string, unknown>, override: Record<string, unknown>): Record<string, unknown> {
  const merged: Record<string, unknown> = { ...base }
  for (const [key, value] of Object.entries(override)) {
    const existing = merged[key]
    if (
      value &&
      typeof value === 'object' &&
      !Array.isArray(value) &&
      existing &&
      typeof existing === 'object' &&
      !Array.isArray(existing)
    ) {
      merged[key] = deepMerge(existing as Record<string, unknown>, value as Record<string, unknown>)
    } else {
      merged[key] = value
    }
  }
  return merged
}

export function loadLocalRuntimeSettings(): LocalRuntimeSettings {
  return sanitizeRuntimeSettings(readJsonStorage<LocalRuntimeSettings>(LOCAL_RUNTIME_SETTINGS_KEY))
}

export function saveLocalRuntimeSettings(settings: LocalRuntimeSettings) {
  writeJsonStorage(LOCAL_RUNTIME_SETTINGS_KEY, sanitizeRuntimeSettings(settings))
}

export function clearLocalRuntimeSettings() {
  if (typeof window === 'undefined') {
    return
  }
  window.localStorage.removeItem(LOCAL_RUNTIME_SETTINGS_KEY)
}

export function loadEnvRuntimeAuthToken(): string {
  return readSessionText(ENV_RUNTIME_AUTH_TOKEN_KEY).trim()
}

export function saveEnvRuntimeAuthToken(token: string) {
  writeSessionText(ENV_RUNTIME_AUTH_TOKEN_KEY, token.trim())
}

export function clearEnvRuntimeAuthToken() {
  if (typeof window === 'undefined') {
    return
  }
  window.sessionStorage.removeItem(ENV_RUNTIME_AUTH_TOKEN_KEY)
}

export function loadLocalDefaultRunConfig(): Record<string, unknown> {
  return sanitizeDefaultRunConfig(readJsonStorage<Record<string, unknown>>(LOCAL_DEFAULT_RUN_CONFIG_KEY))
}

export function saveLocalDefaultRunConfig(config: Record<string, unknown>) {
  writeJsonStorage(LOCAL_DEFAULT_RUN_CONFIG_KEY, sanitizeDefaultRunConfig(config))
}

export function clearLocalDefaultRunConfig() {
  if (typeof window === 'undefined') {
    return
  }
  window.localStorage.removeItem(LOCAL_DEFAULT_RUN_CONFIG_KEY)
}

export function mergeWithLocalDefaultRunConfig(baseConfig: Record<string, unknown>): Record<string, unknown> {
  const localConfig = loadLocalDefaultRunConfig()
  return deepMerge(baseConfig, localConfig)
}

function encodeBase64Url(text: string): string {
  const bytes = new TextEncoder().encode(text)
  let binary = ''
  for (const byte of bytes) {
    binary += String.fromCharCode(byte)
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '')
}

export function buildRuntimeOverrideHeaderValue(): string | null {
  const sanitized = sanitizeRuntimeSettings(loadLocalRuntimeSettings())
  if (!sanitized.providers && !sanitized.model_aliases) {
    return null
  }
  return encodeBase64Url(JSON.stringify(sanitized))
}

export function buildEnvRuntimeAuthHeaderValue(): string | null {
  const token = loadEnvRuntimeAuthToken()
  return token || null
}

function decodeBase64UrlToArrayBuffer(value: string): ArrayBuffer {
  const normalized = value.replace(/-/g, '+').replace(/_/g, '/')
  const padding = '='.repeat((4 - (normalized.length % 4 || 4)) % 4)
  const binary = atob(`${normalized}${padding}`)
  const buffer = new ArrayBuffer(binary.length)
  const bytes = new Uint8Array(buffer)
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i)
  }
  return buffer
}

function encodeBytesToBase64Url(bytes: ArrayBuffer): string {
  const view = new Uint8Array(bytes)
  let binary = ''
  for (const byte of view) {
    binary += String.fromCharCode(byte)
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '')
}

export async function buildEnvRuntimeUnlockProof(
  password: string,
  challenge: EnvRuntimeUnlockChallenge,
): Promise<string> {
  const cryptoApi = globalThis.crypto?.subtle
  if (!cryptoApi) {
    throw new Error('Web Crypto is unavailable in this browser.')
  }
  if (!challenge.protected) {
    throw new Error('This server .env mode is not password-protected.')
  }
  const textEncoder = new TextEncoder()
  const passwordKey = await cryptoApi.importKey(
    'raw',
    textEncoder.encode(password),
    'PBKDF2',
    false,
    ['deriveBits'],
  )
  const derivedBits = await cryptoApi.deriveBits(
    {
      name: 'PBKDF2',
      hash: 'SHA-256',
      salt: decodeBase64UrlToArrayBuffer(challenge.salt),
      iterations: challenge.iterations,
    },
    passwordKey,
    256,
  )
  const hmacKey = await cryptoApi.importKey(
    'raw',
    derivedBits,
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  )
  const payload = `${challenge.challenge_id}:${challenge.nonce}:${Math.trunc(challenge.expires_at)}`
  const signature = await cryptoApi.sign('HMAC', hmacKey, textEncoder.encode(payload))
  return encodeBytesToBase64Url(signature)
}

export function maskSecret(value: string | undefined): string {
  const secret = String(value || '').trim()
  if (!secret) {
    return ''
  }
  if (secret.length <= 8) {
    return '*'.repeat(secret.length)
  }
  return `${secret.slice(0, 4)}${'*'.repeat(Math.max(secret.length - 8, 4))}${secret.slice(-4)}`
}
