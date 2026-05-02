import { useCallback, useEffect, useMemo, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  createEnvRuntimeUnlockChallenge,
  getConfig,
  getRuntimeAccessStatus,
  verifyEnvRuntimeUnlock,
} from '@/api/client'
import {
  buildEnvRuntimeUnlockProof,
  clearEnvRuntimeAuthToken,
  clearLocalDefaultRunConfig,
  clearLocalRuntimeSettings,
  loadEnvRuntimeAuthToken,
  loadLocalRuntimeSettings,
  maskSecret,
  mergeWithLocalDefaultRunConfig,
  saveEnvRuntimeAuthToken,
  saveLocalDefaultRunConfig,
  saveLocalRuntimeSettings,
  type EnvRuntimeUnlockChallenge,
  type LocalRuntimeSettings,
  type RuntimeSecretPath,
} from '@/lib/runtimeSettings'
import { Settings2, Shield, KeyRound, BrainCircuit, Cloud } from 'lucide-react'

interface RuntimeAccessState {
  guard_mode: 'off' | 'password'
  protected: boolean
  password_configured: boolean
  unlocked: boolean
  auth_header_name: string
}

interface RuntimeFormState {
  openaiBaseUrl: string
  openaiApiKey: string
  liteBaseUrl: string
  liteApiKey: string
  embeddingBaseUrl: string
  embeddingApiKey: string
  embeddingModel: string
  semanticScholarApiKey: string
  mineruApiKey: string
  r2Endpoint: string
  r2Bucket: string
  r2AccessKeyId: string
  r2SecretAccessKey: string
  r2PublicBaseUrl: string
  proxyPort: string
  aliasGptPro: string
  aliasGemPro: string
  aliasGemFlash: string
  aliasGemImage: string
  aliasLiteModel: string
}

interface DefaultConfigFormState {
  topicName: string
  topicQuery: string
  topicKeywords: string
  track: string
  candidatePoolSize: string
  dateRangeDays: string
  classicMinCitations: string
  semanticTopK: string
  minSemanticScore: string
  topicFitGateThreshold: string
  postDownloadTopicFitThreshold: string
  preferredVenues: string
  preferredInstitutions: string
  fast: string
  primary: string
  secondary: string
  mergeModel: string
  reasoningEffort: string
  structureMode: string
}

const DEFAULT_RUNTIME_FORM: RuntimeFormState = {
  openaiBaseUrl: '',
  openaiApiKey: '',
  liteBaseUrl: '',
  liteApiKey: '',
  embeddingBaseUrl: '',
  embeddingApiKey: '',
  embeddingModel: 'text-embedding-3-small',
  semanticScholarApiKey: '',
  mineruApiKey: '',
  r2Endpoint: '',
  r2Bucket: '',
  r2AccessKeyId: '',
  r2SecretAccessKey: '',
  r2PublicBaseUrl: '',
  proxyPort: '',
  aliasGptPro: '',
  aliasGemPro: '',
  aliasGemFlash: '',
  aliasGemImage: '',
  aliasLiteModel: '',
}

const DEFAULT_CONFIG_FORM: DefaultConfigFormState = {
  topicName: '',
  topicQuery: '',
  topicKeywords: '',
  track: 'auto',
  candidatePoolSize: '80',
  dateRangeDays: '7',
  classicMinCitations: '50',
  semanticTopK: '8',
  minSemanticScore: '0.4',
  topicFitGateThreshold: '0.72',
  postDownloadTopicFitThreshold: '0.55',
  preferredVenues: '',
  preferredInstitutions: '',
  fast: 'gem_flash',
  primary: 'gem_pro',
  secondary: 'gpt_pro',
  mergeModel: 'gem_pro',
  reasoningEffort: 'high',
  structureMode: 'classic',
}

function formatRequestError(error: unknown, fallback = 'Failed to save settings.') {
  if (!(error instanceof Error)) {
    return fallback
  }
  const raw = error.message.replace(/^\d+:\s*/, '').trim()
  try {
    const payload = JSON.parse(raw) as { detail?: string }
    if (typeof payload.detail === 'string' && payload.detail.trim()) {
      return payload.detail.trim()
    }
  } catch {
    return raw || fallback
  }
  return raw || fallback
}

function parseCsv(text: string) {
  return text
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

function normalizeConfigForm(config: Record<string, unknown>): DefaultConfigFormState {
  const topics = Array.isArray(config.topics) ? config.topics : []
  const firstTopic = (topics[0] && typeof topics[0] === 'object' ? topics[0] : {}) as Record<string, unknown>
  const selection = (config.selection && typeof config.selection === 'object' ? config.selection : {}) as Record<string, unknown>
  const models = (config.models && typeof config.models === 'object' ? config.models : {}) as Record<string, unknown>
  const report = (config.report && typeof config.report === 'object' ? config.report : {}) as Record<string, unknown>

  return {
    topicName: String(firstTopic.name || ''),
    topicQuery: String(firstTopic.query || ''),
    topicKeywords: Array.isArray(firstTopic.keywords) ? firstTopic.keywords.join(', ') : '',
    track: String(selection.track || 'auto'),
    candidatePoolSize: String(selection.candidate_pool_size || 80),
    dateRangeDays: String(selection.date_range_days || 7),
    classicMinCitations: String(selection.classic_min_citations || 50),
    semanticTopK: String(selection.semantic_top_k || 8),
    minSemanticScore: String(selection.min_semantic_score || 0.4),
    topicFitGateThreshold: String(selection.topic_fit_gate_threshold || 0.72),
    postDownloadTopicFitThreshold: String(selection.post_download_topic_fit_threshold || 0.55),
    preferredVenues: Array.isArray(selection.preferred_venues) ? selection.preferred_venues.join(', ') : '',
    preferredInstitutions: Array.isArray(selection.preferred_institutions) ? selection.preferred_institutions.join(', ') : '',
    fast: String(models.fast || 'gem_flash'),
    primary: String(models.primary || 'gem_pro'),
    secondary: String(models.secondary || 'gpt_pro'),
    mergeModel: String(models.merge_model || 'gem_pro'),
    reasoningEffort: String(models.reasoning_effort || 'high'),
    structureMode: String(report.structure_mode || 'classic'),
  }
}

function normalizeRuntimeForm(runtime: LocalRuntimeSettings): RuntimeFormState {
  return {
    openaiBaseUrl: String(runtime.providers?.openai?.base_url || ''),
    openaiApiKey: '',
    liteBaseUrl: String(runtime.providers?.lite?.base_url || ''),
    liteApiKey: '',
    embeddingBaseUrl: String(runtime.providers?.embedding?.base_url || ''),
    embeddingApiKey: '',
    embeddingModel: String(runtime.providers?.embedding?.model || 'text-embedding-3-small'),
    semanticScholarApiKey: '',
    mineruApiKey: '',
    r2Endpoint: String(runtime.providers?.r2?.endpoint || ''),
    r2Bucket: String(runtime.providers?.r2?.bucket || ''),
    r2AccessKeyId: '',
    r2SecretAccessKey: '',
    r2PublicBaseUrl: String(runtime.providers?.r2?.public_base_url || ''),
    proxyPort:
      runtime.providers?.network?.proxy_port === null || runtime.providers?.network?.proxy_port === undefined
        ? ''
        : String(runtime.providers.network.proxy_port),
    aliasGptPro: String(runtime.model_aliases?.gpt_pro || ''),
    aliasGemPro: String(runtime.model_aliases?.gem_pro || ''),
    aliasGemFlash: String(runtime.model_aliases?.gem_flash || ''),
    aliasGemImage: String(runtime.model_aliases?.gem_image || ''),
    aliasLiteModel: String(runtime.model_aliases?.lite_model || ''),
  }
}

export default function SettingsPage() {
  const [storedRuntimeConfig, setStoredRuntimeConfig] = useState<LocalRuntimeSettings>({})
  const [browserRuntimeEnabled, setBrowserRuntimeEnabled] = useState(false)
  const [runtimeAccess, setRuntimeAccess] = useState<RuntimeAccessState>({
    guard_mode: 'off',
    protected: false,
    password_configured: false,
    unlocked: false,
    auth_header_name: '',
  })
  const [envModeUnlocked, setEnvModeUnlocked] = useState(false)
  const [envUnlockPassword, setEnvUnlockPassword] = useState('')
  const [runtimeForm, setRuntimeForm] = useState<RuntimeFormState>(DEFAULT_RUNTIME_FORM)
  const [defaultConfigForm, setDefaultConfigForm] = useState<DefaultConfigFormState>(DEFAULT_CONFIG_FORM)
  const [clearSecrets, setClearSecrets] = useState<Record<string, boolean>>({})
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [isSavingRuntime, setIsSavingRuntime] = useState(false)
  const [isSavingDefaults, setIsSavingDefaults] = useState(false)
  const [isUnlockingEnvMode, setIsUnlockingEnvMode] = useState(false)

  const loadAll = useCallback(async () => {
    const [config, accessStatus] = await Promise.all([
      getConfig(),
      getRuntimeAccessStatus(),
    ])
    const envAccess = accessStatus.env_mode
    const browserEnabled = Boolean(accessStatus.browser_runtime_enabled)
    const localRuntime = loadLocalRuntimeSettings()
    setDefaultConfigForm(normalizeConfigForm(mergeWithLocalDefaultRunConfig(config)))
    setStoredRuntimeConfig(localRuntime)
    setBrowserRuntimeEnabled(browserEnabled)
    setRuntimeAccess(envAccess)
    setEnvModeUnlocked(Boolean(envAccess.unlocked && loadEnvRuntimeAuthToken()))
    setRuntimeForm(normalizeRuntimeForm(localRuntime))
    setClearSecrets({})
    setError(null)
  }, [])

  useEffect(() => {
    void loadAll().catch((err) => {
      setError(err instanceof Error ? err.message : 'Failed to load settings.')
    })
  }, [loadAll])

  const configuredSecrets = useMemo(() => {
    const providers = storedRuntimeConfig.providers || {}
    return {
      'providers.openai.api_key': maskSecret(providers.openai?.api_key),
      'providers.lite.api_key': maskSecret(providers.lite?.api_key),
      'providers.embedding.api_key': maskSecret(providers.embedding?.api_key),
      'providers.semantic_scholar.api_key': maskSecret(providers.semantic_scholar?.api_key),
      'providers.mineru.api_key': maskSecret(providers.mineru?.api_key),
      'providers.r2.access_key_id': maskSecret(providers.r2?.access_key_id),
      'providers.r2.secret_access_key': maskSecret(providers.r2?.secret_access_key),
    } as Record<RuntimeSecretPath, string>
  }, [storedRuntimeConfig])

  const updateRuntimeField = <K extends keyof RuntimeFormState>(key: K, value: RuntimeFormState[K]) => {
    setRuntimeForm((current) => ({ ...current, [key]: value }))
  }

  const updateDefaultField = <K extends keyof DefaultConfigFormState>(key: K, value: DefaultConfigFormState[K]) => {
    setDefaultConfigForm((current) => ({ ...current, [key]: value }))
  }

  const toggleClearSecret = (path: RuntimeSecretPath) => {
    setClearSecrets((current) => ({ ...current, [path]: !current[path] }))
  }

  const unlockEnvMode = async () => {
    const password = envUnlockPassword
    if (!password.trim()) {
      setError('Enter the .env unlock password first.')
      return
    }
    setIsUnlockingEnvMode(true)
    setError(null)
    setSuccess(null)
    try {
      const challenge = await createEnvRuntimeUnlockChallenge()
      if (!challenge.protected) {
        clearEnvRuntimeAuthToken()
        setRuntimeAccess((current) => ({
          ...current,
          guard_mode: challenge.guard_mode,
          protected: challenge.protected,
          password_configured: challenge.password_configured,
          unlocked: true,
        }))
        setEnvModeUnlocked(true)
        setEnvUnlockPassword('')
        setSuccess('Server env access guard is disabled, so no unlock step is needed.')
        return
      }
      const proof = await buildEnvRuntimeUnlockProof(
        password,
        challenge as EnvRuntimeUnlockChallenge,
      )
      const result = await verifyEnvRuntimeUnlock({
        challenge_id: challenge.challenge_id,
        proof,
      })
      saveEnvRuntimeAuthToken(result.token)
      setRuntimeAccess((current) => ({
        ...current,
        guard_mode: result.guard_mode,
        protected: result.protected,
        password_configured: result.password_configured,
        unlocked: true,
      }))
      setEnvModeUnlocked(true)
      setEnvUnlockPassword('')
      await loadAll()
      setSuccess('Server env access unlocked for this browser session. The password itself is not stored locally.')
    } catch (err) {
      clearEnvRuntimeAuthToken()
      setRuntimeAccess((current) => ({ ...current, unlocked: false }))
      setEnvModeUnlocked(false)
      setError(formatRequestError(err, 'Failed to unlock server env access.'))
    } finally {
      setIsUnlockingEnvMode(false)
    }
  }

  const lockEnvMode = () => {
    clearEnvRuntimeAuthToken()
    setRuntimeAccess((current) => ({ ...current, unlocked: false }))
    setEnvModeUnlocked(false)
    setEnvUnlockPassword('')
    setError(null)
    setSuccess('Server env access has been locked again for this browser session.')
  }

  const saveRuntimeSettings = async () => {
    setIsSavingRuntime(true)
    setError(null)
    setSuccess(null)
    try {
      const nextRuntimeConfig: LocalRuntimeSettings = {
        providers: {
          openai: {
            base_url: runtimeForm.openaiBaseUrl.trim(),
            api_key: clearSecrets['providers.openai.api_key']
              ? ''
              : (runtimeForm.openaiApiKey || storedRuntimeConfig.providers?.openai?.api_key || ''),
          },
          lite: {
            base_url: runtimeForm.liteBaseUrl.trim(),
            api_key: clearSecrets['providers.lite.api_key']
              ? ''
              : (runtimeForm.liteApiKey || storedRuntimeConfig.providers?.lite?.api_key || ''),
          },
          embedding: {
            base_url: runtimeForm.embeddingBaseUrl.trim(),
            api_key: clearSecrets['providers.embedding.api_key']
              ? ''
              : (runtimeForm.embeddingApiKey || storedRuntimeConfig.providers?.embedding?.api_key || ''),
            model: runtimeForm.embeddingModel.trim(),
          },
          semantic_scholar: {
            api_key: clearSecrets['providers.semantic_scholar.api_key']
              ? ''
              : (runtimeForm.semanticScholarApiKey || storedRuntimeConfig.providers?.semantic_scholar?.api_key || ''),
          },
          mineru: {
            api_key: clearSecrets['providers.mineru.api_key']
              ? ''
              : (runtimeForm.mineruApiKey || storedRuntimeConfig.providers?.mineru?.api_key || ''),
          },
          r2: {
            endpoint: runtimeForm.r2Endpoint.trim(),
            bucket: runtimeForm.r2Bucket.trim(),
            access_key_id: clearSecrets['providers.r2.access_key_id']
              ? ''
              : (runtimeForm.r2AccessKeyId || storedRuntimeConfig.providers?.r2?.access_key_id || ''),
            secret_access_key: clearSecrets['providers.r2.secret_access_key']
              ? ''
              : (runtimeForm.r2SecretAccessKey || storedRuntimeConfig.providers?.r2?.secret_access_key || ''),
            public_base_url: runtimeForm.r2PublicBaseUrl.trim(),
          },
          network: {
            proxy_port: runtimeForm.proxyPort.trim() ? Number.parseInt(runtimeForm.proxyPort.trim(), 10) : null,
          },
        },
        model_aliases: {
          gpt_pro: runtimeForm.aliasGptPro.trim(),
          gem_pro: runtimeForm.aliasGemPro.trim(),
          gem_flash: runtimeForm.aliasGemFlash.trim(),
          gem_image: runtimeForm.aliasGemImage.trim(),
          lite_model: runtimeForm.aliasLiteModel.trim(),
        },
      }
      saveLocalRuntimeSettings(nextRuntimeConfig)
      await loadAll()
      setSuccess(
        browserRuntimeEnabled
          ? 'Browser runtime settings saved. They will be used as this browser\'s fallback runtime settings whenever server .env access is unavailable in this session.'
          : 'Browser runtime settings saved locally in this browser. The backend will ignore them until browser runtime is enabled on the server.',
      )
    } catch (err) {
      setError(formatRequestError(err, 'Failed to save browser-local runtime settings.'))
    } finally {
      setIsSavingRuntime(false)
    }
  }

  const saveDefaultSettings = async () => {
    setIsSavingDefaults(true)
    setError(null)
    setSuccess(null)
    try {
      saveLocalDefaultRunConfig({
        topics:
          defaultConfigForm.topicName.trim() || defaultConfigForm.topicQuery.trim() || defaultConfigForm.topicKeywords.trim()
            ? [
                {
                  name: defaultConfigForm.topicName.trim(),
                  query: defaultConfigForm.topicQuery.trim(),
                  keywords: parseCsv(defaultConfigForm.topicKeywords),
                },
              ]
            : [],
        selection: {
          track: defaultConfigForm.track,
          candidate_pool_size: Number.parseInt(defaultConfigForm.candidatePoolSize, 10) || 80,
          date_range_days: Number.parseInt(defaultConfigForm.dateRangeDays, 10) || 7,
          classic_min_citations: Number.parseInt(defaultConfigForm.classicMinCitations, 10) || 50,
          semantic_top_k: Number.parseInt(defaultConfigForm.semanticTopK, 10) || 8,
          min_semantic_score: Number.parseFloat(defaultConfigForm.minSemanticScore) || 0.4,
          topic_fit_gate_threshold: Number.parseFloat(defaultConfigForm.topicFitGateThreshold) || 0.72,
          post_download_topic_fit_threshold: Number.parseFloat(defaultConfigForm.postDownloadTopicFitThreshold) || 0.55,
          preferred_venues: parseCsv(defaultConfigForm.preferredVenues),
          preferred_institutions: parseCsv(defaultConfigForm.preferredInstitutions),
        },
        models: {
          fast: defaultConfigForm.fast.trim(),
          primary: defaultConfigForm.primary.trim(),
          secondary: defaultConfigForm.secondary.trim(),
          merge_model: defaultConfigForm.mergeModel.trim(),
          reasoning_effort: defaultConfigForm.reasoningEffort.trim(),
        },
        report: {
          structure_mode: defaultConfigForm.structureMode,
        },
      })
      setSuccess('Default run config saved in this browser.')
    } catch (err) {
      setError(formatRequestError(err, 'Failed to save browser-local default run config.'))
    } finally {
      setIsSavingDefaults(false)
    }
  }

  const renderSecretStatus = (path: RuntimeSecretPath) => {
    const secret = configuredSecrets[path]
    const isClearing = Boolean(clearSecrets[path])
    if (isClearing) {
      return <Badge variant="destructive">Will clear locally</Badge>
    }
    if (secret) {
      return <Badge variant="secondary">{secret}</Badge>
    }
    return <Badge variant="outline">Empty</Badge>
  }

  const clearBrowserLocalSettings = () => {
    clearEnvRuntimeAuthToken()
    clearLocalRuntimeSettings()
    clearLocalDefaultRunConfig()
    void loadAll()
    setEnvModeUnlocked(false)
    setEnvUnlockPassword('')
    setSuccess('Browser-local settings cleared for this browser.')
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-2">
          <h2 className="flex items-center gap-2 text-2xl font-bold">
            <Settings2 className="h-5 w-5" />
            Settings
          </h2>
          <p className="max-w-3xl text-sm text-muted-foreground">
            This page controls the runtime source priority for this browser. When server <code>.env</code> access is available, model-powered requests use the server-side <code>.env</code>; otherwise they fall back to this browser&apos;s local API settings if browser runtime is enabled and this browser has saved them.
          </p>
        </div>
        <Button variant="outline" onClick={clearBrowserLocalSettings}>
          Clear Browser Settings
        </Button>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {success ? <p className="text-sm text-emerald-600">{success}</p> : null}

      <Card>
        <CardHeader>
          <CardTitle>Runtime Source</CardTitle>
          <CardDescription>
            The backend now chooses the runtime source automatically: server <code>.env</code> wins whenever it is available; otherwise it falls back to this browser&apos;s saved API settings only if browser runtime is enabled.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-3">
          <p className="w-full text-sm text-muted-foreground">
            {runtimeAccess.guard_mode === 'off'
              ? 'Current policy: the backend always uses the server .env runtime settings. Browser-local settings are stored only for this browser and will not be used until the server enables browser runtime.'
              : envModeUnlocked
                ? 'Current policy: server .env access is unlocked in this browser session, so model-powered requests will use the server .env runtime settings.'
                : 'Current policy: server .env access is locked for this browser session. If browser runtime is enabled and this browser has saved runtime settings, model-powered requests will fall back to those local settings.'}
          </p>
          {!browserRuntimeEnabled ? (
            <div className="w-full rounded-xl border border-muted/60 bg-muted/30 p-4">
              <p className="text-sm font-medium">Browser Runtime Disabled</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Browser-local runtime settings can still be saved in this browser, but the backend will ignore them until the server enables browser runtime by setting <code>ENV_RUNTIME_ACCESS_GUARD=password</code> and restarting.
              </p>
            </div>
          ) : null}
          {runtimeAccess.guard_mode === 'password' && runtimeAccess.password_configured ? (
            <div className="w-full rounded-xl border border-amber-300/60 bg-amber-50/50 p-4">
              <p className="text-sm font-medium">Protected Server Env Access</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Server-side <code>.env</code> access is protected by an independent password guard. When this browser session is locked, the backend will fall back to this browser&apos;s saved runtime settings if browser runtime is enabled and local settings exist. The browser sends only a derived proof during unlock; the password itself is not saved in localStorage or returned by the server.
              </p>
              <div className="mt-4 flex flex-col gap-3 md:flex-row md:items-end">
                <label className="flex-1 space-y-2">
                  <span className="text-sm font-medium">Access Password</span>
                  <Input
                    type="password"
                    value={envUnlockPassword}
                    onChange={(event) => setEnvUnlockPassword(event.target.value)}
                    placeholder={envModeUnlocked ? 'Unlocked for this browser session' : 'Enter password to unlock server env access'}
                  />
                </label>
                <Button onClick={unlockEnvMode} disabled={isUnlockingEnvMode}>
                  {isUnlockingEnvMode ? 'Unlocking...' : 'Unlock Access'}
                </Button>
                <Button variant="outline" onClick={lockEnvMode} disabled={!envModeUnlocked}>
                  Lock Again
                </Button>
              </div>
              <p className="mt-3 text-xs text-muted-foreground">
                Status: {envModeUnlocked ? 'Unlocked in this browser session.' : 'Locked. Model-powered actions will fall back to this browser\'s saved runtime settings if available; otherwise they will be rejected.'}
              </p>
            </div>
          ) : null}
          {runtimeAccess.guard_mode === 'password' && !runtimeAccess.password_configured ? (
            <div className="w-full rounded-xl border border-destructive/40 bg-destructive/5 p-4">
              <p className="text-sm font-medium">Broken Server Env Guard</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Server env access guard is enabled, but the server is missing <code>ENV_RUNTIME_PASSWORD_HASH</code>. Until the server fixes that configuration, server <code>.env</code> access cannot be unlocked.
              </p>
            </div>
          ) : null}
          {runtimeAccess.guard_mode === 'off' ? (
            <div className="w-full rounded-xl border border-sky-300/60 bg-sky-50/50 p-4">
              <p className="text-sm font-medium">Open Server Env Access</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Server <code>.env</code> access is currently open, so no password step is active for this browser. Model-powered requests will use the server-side <code>.env</code> directly.
              </p>
              <p className="mt-3 text-xs text-muted-foreground">
                To enable the independent password layer for deployment, set <code>ENV_RUNTIME_ACCESS_GUARD=password</code> and provide <code>ENV_RUNTIME_PASSWORD_HASH=...</code> in the server&apos;s <code>.env</code>, then restart the backend.
              </p>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card className="border-amber-300/60 bg-amber-50/40 dark:border-amber-800/50 dark:bg-amber-950/20">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <Shield className="h-4 w-4" />
            Security Note
          </CardTitle>
          <CardDescription>
            Browser custom API secrets are stored only in this browser&apos;s local storage. Leaving a secret field blank keeps the current browser-stored value. Use the clear toggle only when you want to remove it from this browser on purpose.
          </CardDescription>
        </CardHeader>
      </Card>

      <div className="grid gap-6 xl:grid-cols-[1.05fr_0.95fr]">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <KeyRound className="h-4 w-4" />
                Runtime Providers
              </CardTitle>
              <CardDescription>
                OpenAI-compatible endpoints, embedding backend, translation backend, retrieval keys, and storage credentials saved in this browser. These fields act as this browser&apos;s fallback runtime settings when server <code>.env</code> access is unavailable and browser runtime is enabled on the server.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="grid gap-4 md:grid-cols-2">
                <label className="space-y-2">
                  <span className="text-sm font-medium">OpenAI-compatible Base URL</span>
                  <Input value={runtimeForm.openaiBaseUrl} onChange={(event) => updateRuntimeField('openaiBaseUrl', event.target.value)} placeholder="https://api.openai.com/v1" />
                </label>
                <label className="space-y-2">
                  <span className="text-sm font-medium">OpenAI-compatible API Key</span>
                  <div className="flex items-center gap-2">
                    <Input value={runtimeForm.openaiApiKey} onChange={(event) => updateRuntimeField('openaiApiKey', event.target.value)} placeholder="Leave blank to keep existing key" />
                    {renderSecretStatus('providers.openai.api_key')}
                  </div>
                  <label className="inline-flex items-center gap-2 text-xs text-muted-foreground">
                    <input type="checkbox" checked={Boolean(clearSecrets['providers.openai.api_key'])} onChange={() => toggleClearSecret('providers.openai.api_key')} />
                    Clear browser-stored key
                  </label>
                </label>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <label className="space-y-2">
                  <span className="text-sm font-medium">Lite Translation Base URL</span>
                  <Input value={runtimeForm.liteBaseUrl} onChange={(event) => updateRuntimeField('liteBaseUrl', event.target.value)} placeholder="https://..." />
                </label>
                <label className="space-y-2">
                  <span className="text-sm font-medium">Lite Translation API Key</span>
                  <div className="flex items-center gap-2">
                    <Input value={runtimeForm.liteApiKey} onChange={(event) => updateRuntimeField('liteApiKey', event.target.value)} placeholder="Leave blank to keep existing key" />
                    {renderSecretStatus('providers.lite.api_key')}
                  </div>
                  <label className="inline-flex items-center gap-2 text-xs text-muted-foreground">
                    <input type="checkbox" checked={Boolean(clearSecrets['providers.lite.api_key'])} onChange={() => toggleClearSecret('providers.lite.api_key')} />
                    Clear browser-stored key
                  </label>
                </label>
              </div>

              <div className="grid gap-4 md:grid-cols-3">
                <label className="space-y-2">
                  <span className="text-sm font-medium">Embedding Base URL</span>
                  <Input value={runtimeForm.embeddingBaseUrl} onChange={(event) => updateRuntimeField('embeddingBaseUrl', event.target.value)} placeholder="https://..." />
                </label>
                <label className="space-y-2">
                  <span className="text-sm font-medium">Embedding API Key</span>
                  <div className="flex items-center gap-2">
                    <Input value={runtimeForm.embeddingApiKey} onChange={(event) => updateRuntimeField('embeddingApiKey', event.target.value)} placeholder="Leave blank to keep existing key" />
                    {renderSecretStatus('providers.embedding.api_key')}
                  </div>
                  <label className="inline-flex items-center gap-2 text-xs text-muted-foreground">
                    <input type="checkbox" checked={Boolean(clearSecrets['providers.embedding.api_key'])} onChange={() => toggleClearSecret('providers.embedding.api_key')} />
                    Clear browser-stored key
                  </label>
                </label>
                <label className="space-y-2">
                  <span className="text-sm font-medium">Embedding Model</span>
                  <Input value={runtimeForm.embeddingModel} onChange={(event) => updateRuntimeField('embeddingModel', event.target.value)} placeholder="text-embedding-3-small" />
                </label>
              </div>

              <div className="grid gap-4 md:grid-cols-3">
                <label className="space-y-2">
                  <span className="text-sm font-medium">Semantic Scholar API Key</span>
                  <div className="flex items-center gap-2">
                    <Input value={runtimeForm.semanticScholarApiKey} onChange={(event) => updateRuntimeField('semanticScholarApiKey', event.target.value)} placeholder="Optional" />
                    {renderSecretStatus('providers.semantic_scholar.api_key')}
                  </div>
                  <label className="inline-flex items-center gap-2 text-xs text-muted-foreground">
                    <input type="checkbox" checked={Boolean(clearSecrets['providers.semantic_scholar.api_key'])} onChange={() => toggleClearSecret('providers.semantic_scholar.api_key')} />
                    Clear browser-stored key
                  </label>
                </label>
                <label className="space-y-2">
                  <span className="text-sm font-medium">MinerU Key</span>
                  <div className="flex items-center gap-2">
                    <Input value={runtimeForm.mineruApiKey} onChange={(event) => updateRuntimeField('mineruApiKey', event.target.value)} placeholder="Optional" />
                    {renderSecretStatus('providers.mineru.api_key')}
                  </div>
                  <label className="inline-flex items-center gap-2 text-xs text-muted-foreground">
                    <input type="checkbox" checked={Boolean(clearSecrets['providers.mineru.api_key'])} onChange={() => toggleClearSecret('providers.mineru.api_key')} />
                    Clear browser-stored key
                  </label>
                </label>
                <label className="space-y-2">
                  <span className="text-sm font-medium">Local Proxy Port</span>
                  <Input value={runtimeForm.proxyPort} onChange={(event) => updateRuntimeField('proxyPort', event.target.value)} placeholder="Optional" />
                </label>
              </div>

              <div className="rounded-xl border p-4">
                <div className="mb-4 flex items-center gap-2">
                  <Cloud className="h-4 w-4" />
                  <p className="text-sm font-medium">Cloudflare R2 / S3-Compatible Storage</p>
                </div>
                <div className="grid gap-4 md:grid-cols-2">
                  <label className="space-y-2">
                    <span className="text-sm font-medium">Endpoint</span>
                    <Input value={runtimeForm.r2Endpoint} onChange={(event) => updateRuntimeField('r2Endpoint', event.target.value)} placeholder="https://..." />
                  </label>
                  <label className="space-y-2">
                    <span className="text-sm font-medium">Bucket</span>
                    <Input value={runtimeForm.r2Bucket} onChange={(event) => updateRuntimeField('r2Bucket', event.target.value)} placeholder="bucket-name" />
                  </label>
                  <label className="space-y-2">
                    <span className="text-sm font-medium">Access Key ID</span>
                    <div className="flex items-center gap-2">
                      <Input value={runtimeForm.r2AccessKeyId} onChange={(event) => updateRuntimeField('r2AccessKeyId', event.target.value)} placeholder="Leave blank to keep existing key" />
                      {renderSecretStatus('providers.r2.access_key_id')}
                    </div>
                    <label className="inline-flex items-center gap-2 text-xs text-muted-foreground">
                      <input type="checkbox" checked={Boolean(clearSecrets['providers.r2.access_key_id'])} onChange={() => toggleClearSecret('providers.r2.access_key_id')} />
                      Clear browser-stored key
                    </label>
                  </label>
                  <label className="space-y-2">
                    <span className="text-sm font-medium">Secret Access Key</span>
                    <div className="flex items-center gap-2">
                      <Input value={runtimeForm.r2SecretAccessKey} onChange={(event) => updateRuntimeField('r2SecretAccessKey', event.target.value)} placeholder="Leave blank to keep existing key" />
                      {renderSecretStatus('providers.r2.secret_access_key')}
                    </div>
                    <label className="inline-flex items-center gap-2 text-xs text-muted-foreground">
                      <input type="checkbox" checked={Boolean(clearSecrets['providers.r2.secret_access_key'])} onChange={() => toggleClearSecret('providers.r2.secret_access_key')} />
                      Clear browser-stored key
                    </label>
                  </label>
                  <label className="space-y-2 md:col-span-2">
                    <span className="text-sm font-medium">Public Base URL</span>
                    <Input value={runtimeForm.r2PublicBaseUrl} onChange={(event) => updateRuntimeField('r2PublicBaseUrl', event.target.value)} placeholder="https://public.example.com" />
                  </label>
                </div>
              </div>

              <Button onClick={saveRuntimeSettings} disabled={isSavingRuntime}>
                {isSavingRuntime ? 'Saving browser settings...' : 'Save Browser Runtime Settings'}
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <BrainCircuit className="h-4 w-4" />
                Model Aliases
              </CardTitle>
              <CardDescription>
                These aliases are reused across selector, processor, interpreter, translator, and report refinement flows.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <label className="space-y-2">
                <span className="text-sm font-medium">`gpt_pro`</span>
                <Input value={runtimeForm.aliasGptPro} onChange={(event) => updateRuntimeField('aliasGptPro', event.target.value)} placeholder="openai/gpt-5.4" />
              </label>
              <label className="space-y-2">
                <span className="text-sm font-medium">`gem_pro`</span>
                <Input value={runtimeForm.aliasGemPro} onChange={(event) => updateRuntimeField('aliasGemPro', event.target.value)} placeholder="google/gemini-2.5-pro" />
              </label>
              <label className="space-y-2">
                <span className="text-sm font-medium">`gem_flash`</span>
                <Input value={runtimeForm.aliasGemFlash} onChange={(event) => updateRuntimeField('aliasGemFlash', event.target.value)} placeholder="google/gemini-2.5-flash" />
              </label>
              <label className="space-y-2">
                <span className="text-sm font-medium">`gem_image`</span>
                <Input value={runtimeForm.aliasGemImage} onChange={(event) => updateRuntimeField('aliasGemImage', event.target.value)} placeholder="google/gemini-image" />
              </label>
              <label className="space-y-2 md:col-span-2">
                <span className="text-sm font-medium">`lite_model`</span>
                <Input value={runtimeForm.aliasLiteModel} onChange={(event) => updateRuntimeField('aliasLiteModel', event.target.value)} placeholder="A lightweight translation / localization model" />
              </label>
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Default Run Config</CardTitle>
              <CardDescription>
                Browser-local defaults for new jobs. Run page overrides still work per job, but these values become the baseline for this browser only.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <label className="space-y-2">
                <span className="text-sm font-medium">Default Topic Name</span>
                <Input value={defaultConfigForm.topicName} onChange={(event) => updateDefaultField('topicName', event.target.value)} placeholder="Time series forecasting" />
              </label>
              <label className="space-y-2">
                <span className="text-sm font-medium">Default Topic Description</span>
                <textarea
                  className="min-h-28 w-full rounded-md border bg-background px-3 py-2 text-sm"
                  value={defaultConfigForm.topicQuery}
                  onChange={(event) => updateDefaultField('topicQuery', event.target.value)}
                  placeholder="Describe the papers you want to search..."
                />
              </label>
              <label className="space-y-2">
                <span className="text-sm font-medium">Default Topic Keywords</span>
                <textarea
                  className="min-h-20 w-full rounded-md border bg-background px-3 py-2 text-sm"
                  value={defaultConfigForm.topicKeywords}
                  onChange={(event) => updateDefaultField('topicKeywords', event.target.value)}
                  placeholder="Comma separated keywords"
                />
              </label>

              <div className="grid gap-4 md:grid-cols-2">
                <label className="space-y-2">
                  <span className="text-sm font-medium">Track</span>
                  <select className="w-full rounded-md border bg-background px-3 py-2 text-sm" value={defaultConfigForm.track} onChange={(event) => updateDefaultField('track', event.target.value)}>
                    <option value="auto">auto</option>
                    <option value="recent">recent</option>
                    <option value="classic">classic</option>
                    <option value="goat">goat</option>
                  </select>
                </label>
                <label className="space-y-2">
                  <span className="text-sm font-medium">Report Structure</span>
                  <select className="w-full rounded-md border bg-background px-3 py-2 text-sm" value={defaultConfigForm.structureMode} onChange={(event) => updateDefaultField('structureMode', event.target.value)}>
                    <option value="classic">classic</option>
                    <option value="pmrc">pmrc</option>
                  </select>
                </label>
                <label className="space-y-2">
                  <span className="text-sm font-medium">Candidate Pool Size</span>
                  <Input value={defaultConfigForm.candidatePoolSize} onChange={(event) => updateDefaultField('candidatePoolSize', event.target.value)} />
                </label>
                <label className="space-y-2">
                  <span className="text-sm font-medium">Date Range Days</span>
                  <Input value={defaultConfigForm.dateRangeDays} onChange={(event) => updateDefaultField('dateRangeDays', event.target.value)} />
                </label>
                <label className="space-y-2">
                  <span className="text-sm font-medium">Classic Min Citations</span>
                  <Input value={defaultConfigForm.classicMinCitations} onChange={(event) => updateDefaultField('classicMinCitations', event.target.value)} />
                </label>
                <label className="space-y-2">
                  <span className="text-sm font-medium">Semantic Top K</span>
                  <Input value={defaultConfigForm.semanticTopK} onChange={(event) => updateDefaultField('semanticTopK', event.target.value)} />
                </label>
                <label className="space-y-2">
                  <span className="text-sm font-medium">Min Semantic Score</span>
                  <Input value={defaultConfigForm.minSemanticScore} onChange={(event) => updateDefaultField('minSemanticScore', event.target.value)} />
                </label>
                <label className="space-y-2">
                  <span className="text-sm font-medium">Topic Fit Gate Threshold</span>
                  <Input value={defaultConfigForm.topicFitGateThreshold} onChange={(event) => updateDefaultField('topicFitGateThreshold', event.target.value)} />
                </label>
                <label className="space-y-2">
                  <span className="text-sm font-medium">Post-download Topic Audit Threshold</span>
                  <Input value={defaultConfigForm.postDownloadTopicFitThreshold} onChange={(event) => updateDefaultField('postDownloadTopicFitThreshold', event.target.value)} />
                </label>
                <label className="space-y-2">
                  <span className="text-sm font-medium">Reasoning Effort</span>
                  <Input value={defaultConfigForm.reasoningEffort} onChange={(event) => updateDefaultField('reasoningEffort', event.target.value)} placeholder="low | medium | high" />
                </label>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <label className="space-y-2">
                  <span className="text-sm font-medium">Preferred Venues</span>
                  <textarea
                    className="min-h-20 w-full rounded-md border bg-background px-3 py-2 text-sm"
                    value={defaultConfigForm.preferredVenues}
                    onChange={(event) => updateDefaultField('preferredVenues', event.target.value)}
                    placeholder="Comma separated venues"
                  />
                </label>
                <label className="space-y-2">
                  <span className="text-sm font-medium">Preferred Institutions</span>
                  <textarea
                    className="min-h-20 w-full rounded-md border bg-background px-3 py-2 text-sm"
                    value={defaultConfigForm.preferredInstitutions}
                    onChange={(event) => updateDefaultField('preferredInstitutions', event.target.value)}
                    placeholder="Comma separated institutions"
                  />
                </label>
              </div>

              <div className="rounded-xl border p-4">
                <p className="mb-4 text-sm font-medium">Default model roles</p>
                <div className="grid gap-4 md:grid-cols-2">
                  <label className="space-y-2">
                    <span className="text-sm font-medium">Fast</span>
                    <Input value={defaultConfigForm.fast} onChange={(event) => updateDefaultField('fast', event.target.value)} placeholder="gem_flash" />
                  </label>
                  <label className="space-y-2">
                    <span className="text-sm font-medium">Primary</span>
                    <Input value={defaultConfigForm.primary} onChange={(event) => updateDefaultField('primary', event.target.value)} placeholder="gem_pro" />
                  </label>
                  <label className="space-y-2">
                    <span className="text-sm font-medium">Secondary</span>
                    <Input value={defaultConfigForm.secondary} onChange={(event) => updateDefaultField('secondary', event.target.value)} placeholder="gpt_pro" />
                  </label>
                  <label className="space-y-2">
                    <span className="text-sm font-medium">Merge Model</span>
                    <Input value={defaultConfigForm.mergeModel} onChange={(event) => updateDefaultField('mergeModel', event.target.value)} placeholder="gem_pro" />
                  </label>
                </div>
              </div>

              <Button onClick={saveDefaultSettings} disabled={isSavingDefaults}>
                {isSavingDefaults ? 'Saving browser defaults...' : 'Save Browser Default Run Config'}
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
