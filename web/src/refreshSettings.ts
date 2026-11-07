import { apiFetch } from "./http"
import { useEffect, useRef, useState, type MutableRefObject } from "react"
import { errorMessage, isAbortError } from "./api"

export const DEFAULT_REFRESH_MINUTES: number = 10
export const MIN_REFRESH_MINUTES: number = 1 / 60
export const MAX_REFRESH_MINUTES: number = 1440

export type ModelId = "llama3.2" | "qwen3:4b" | "gpt-4.1" | "gpt-4.1-mini" | "gpt-6-astra"

export const MODEL_OPTIONS: readonly { id: ModelId; label: string }[] = [
  { id: "llama3.2", label: "Llama 3.2" },
  { id: "qwen3:4b", label: "Qwen3 4B" },
  { id: "gpt-4.1-mini", label: "GPT-4.1 mini" },
  { id: "gpt-4.1", label: "GPT-4.1" },
  { id: "gpt-6-astra", label: "GPT-6 Astra" },
]

export type ReasoningEffort = "off" | "minimal" | "low" | "medium" | "high" | "xhigh"

export type EffortLevel = Exclude<ReasoningEffort, "off">

export const EFFORT_LEVELS: readonly { id: EffortLevel; label: string }[] = [
  { id: "minimal", label: "Minimal" },
  { id: "low", label: "Low" },
  { id: "medium", label: "Medium" },
  { id: "high", label: "High" },
  { id: "xhigh", label: "Extra high" },
]

export type OptionField = {
  key: string
  label: string
  defaultValue: boolean
}

export type RefreshMode = "INTERVAL" | "ON_CHANGE" | "MANUAL"

export type EntityConfig = {
  minutes: number
  enabled: boolean
  model: ModelId
  effort: ReasoningEffort
  refreshMode: RefreshMode
  options: Record<string, boolean>
  nextRunAt: string | null
}

export type EntityConfigController = {
  minutes: number
  enabled: boolean
  model: ModelId
  effort: ReasoningEffort
  refreshMode: RefreshMode
  options: Readonly<Record<string, boolean>>
  nextRunAt: string | null
  busy: boolean
  loaded: boolean
  error: string | null
  commit: (
    minutes: number,
    enabled: boolean,
    options: Readonly<Record<string, boolean>>,
    model: ModelId,
    effort: ReasoningEffort,
    refreshMode?: RefreshMode,
  ) => void
}

export function useEntityConfig(
  kind: string,
  id: string,
  fields: readonly OptionField[],
  active: boolean = true,
): EntityConfigController {
  const fieldsRef: MutableRefObject<readonly OptionField[]> = useRef(fields)
  fieldsRef.current = fields
  const fieldKey: string = fields.map((field: OptionField) => field.key).join("|")
  const [config, setConfig] = useState<EntityConfig>(() => defaultConfig(fields))
  const [busy, setBusy] = useState<boolean>(true)
  const [loaded, setLoaded] = useState<boolean>(false)
  const [error, setError] = useState<string | null>(null)
  const busyRef: MutableRefObject<boolean> = useRef(busy)
  busyRef.current = busy
  useEffect(() => {
    if (!active || id === "") {
      return
    }
    const controller: AbortController = new AbortController()
    setBusy(true)
    setLoaded(false)
    setError(null)
    void fetchEntityConfiguration(kind, id, controller.signal)
      .then((loadedConfig: EntityConfig) => {
        if (controller.signal.aborted) {
          return
        }
        setConfig(mergeConfig(loadedConfig, fieldsRef.current))
        setLoaded(true)
        setBusy(false)
      })
      .catch((err: unknown) => {
        if (isAbortError(err) || controller.signal.aborted) {
          return
        }
        setError(errorMessage(err))
        setBusy(false)
      })
    return () => {
      controller.abort()
    }
  }, [kind, id, active, fieldKey])
  useEffect(() => {
    if (!loaded) {
      return
    }
    let inflight: AbortController | null = null
    const timer: number = window.setInterval(() => {
      if (busyRef.current) {
        return
      }
      inflight?.abort()
      const controller: AbortController = new AbortController()
      inflight = controller
      void fetchEntityConfiguration(kind, id, controller.signal)
        .then((loadedConfig: EntityConfig) => {
          if (controller.signal.aborted || busyRef.current) {
            return
          }
          setConfig((current: EntityConfig) => ({
            ...current,
            nextRunAt: loadedConfig.nextRunAt,
          }))
        })
        .catch((err: unknown) => {
          if (isAbortError(err) || controller.signal.aborted) {
            return
          }
        })
    }, 5000)
    return () => {
      inflight?.abort()
      window.clearInterval(timer)
    }
  }, [loaded, kind, id])

  function commit(
    minutes: number,
    enabled: boolean,
    options: Readonly<Record<string, boolean>>,
    model: ModelId,
    effort: ReasoningEffort,
    refreshMode: RefreshMode = config.refreshMode,
  ): void {
    setBusy(true)
    setError(null)
    const saved: EntityConfig = {
      minutes: clampMinutes(minutes),
      enabled,
      model,
      effort,
      refreshMode,
      options: { ...options },
      nextRunAt: null,
    }
    void saveEntityConfiguration(kind, id, saved)
      .then((stored: EntityConfig) => {
        setConfig(mergeConfig(stored, fieldsRef.current))
      })
      .catch((err: unknown) => {
        setError(errorMessage(err))
      })
      .finally(() => {
        setBusy(false)
      })
  }

  return {
    minutes: config.minutes,
    enabled: config.enabled,
    model: config.model,
    effort: config.effort,
    refreshMode: config.refreshMode,
    options: config.options,
    nextRunAt: config.nextRunAt,
    busy,
    loaded,
    error,
    commit,
  }
}

export function useDeadlineLabel(nextRunAt: string | null, enabled: boolean): string | null {
  const [now, setNow] = useState<number>(Date.now())
  useEffect(() => {
    if (!enabled || nextRunAt === null) {
      return
    }
    const timer: number = window.setInterval(() => {
      setNow(Date.now())
    }, 1000)
    return () => {
      window.clearInterval(timer)
    }
  }, [enabled, nextRunAt])
  if (!enabled || nextRunAt === null) {
    return null
  }
  return countdownLabel("Next context rebuild", nextRunAt, now)
}

export function useRefreshTimer(enabled: boolean, minutes: number, onRefresh: () => void): void {
  const refresh: MutableRefObject<() => void> = useRef(onRefresh)
  refresh.current = onRefresh
  useEffect(() => {
    if (!enabled || minutes < MIN_REFRESH_MINUTES) {
      return
    }
    const timer: number = window.setInterval(() => {
      refresh.current()
    }, minutes * 60 * 1000)
    return () => {
      window.clearInterval(timer)
    }
  }, [enabled, minutes])
}

function countdownLabel(prefix: string, nextAtIso: string, now: number): string {
  const at: number = new Date(nextAtIso).getTime()
  const when: string = Number.isNaN(at) ? nextAtIso : new Date(nextAtIso).toLocaleString()
  if (Number.isNaN(at)) {
    return `${prefix} ${when}`
  }
  const left: number = Math.ceil((at - now) / 1000)
  if (left <= 0) {
    return `${prefix} ${when} · due`
  }
  if (left < 60) {
    return `${prefix} ${when} · in ${left}s`
  }
  const mins: number = Math.floor(left / 60)
  const secs: number = left % 60
  return `${prefix} ${when} · in ${mins}m ${secs}s`
}

function defaultConfig(fields: readonly OptionField[]): EntityConfig {
  return {
    minutes: DEFAULT_REFRESH_MINUTES,
    enabled: true,
    model: "gpt-4.1",
    effort: "low",
    refreshMode: "INTERVAL",
    options: defaults(fields),
    nextRunAt: null,
  }
}

function mergeConfig(config: EntityConfig, fields: readonly OptionField[]): EntityConfig {
  const options: Record<string, boolean> = defaults(fields)
  for (const field of fields) {
    const value: boolean | undefined = config.options[field.key]
    if (value !== undefined) {
      options[field.key] = value
    }
  }
  return {
    minutes: clampMinutes(config.minutes),
    enabled: config.enabled,
    model: config.model,
    effort: config.effort,
    refreshMode: config.refreshMode,
    options,
    nextRunAt: config.nextRunAt,
  }
}

export async function readEntityOptions(
  kind: string,
  id: string,
  signal?: AbortSignal,
): Promise<Record<string, boolean>> {
  const loaded: EntityConfig = await fetchEntityConfiguration(kind, id, signal ?? AbortSignal.timeout(15000))
  return loaded.options
}

export async function setEntityOption(
  kind: string,
  id: string,
  key: string,
  value: boolean,
): Promise<Record<string, boolean>> {
  const current: EntityConfig = await fetchEntityConfiguration(kind, id, AbortSignal.timeout(15000))
  const options: Record<string, boolean> = { ...current.options, [key]: value }
  const saved: EntityConfig = await saveEntityConfiguration(kind, id, { ...current, options })
  return saved.options
}

async function fetchEntityConfiguration(
  kind: string,
  id: string,
  signal: AbortSignal,
): Promise<EntityConfig> {
  const response: Response = await apiFetch(configurationPath(kind, id), { signal })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return configFromBody(await response.json())
}

async function saveEntityConfiguration(
  kind: string,
  id: string,
  config: EntityConfig,
): Promise<EntityConfig> {
  const response: Response = await apiFetch(configurationPath(kind, id), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      interval_seconds: secondsFromMinutes(config.minutes),
      enabled: config.enabled,
      model: config.model,
      effort: config.effort,
      refresh_mode: config.refreshMode,
      options: config.options,
    }),
  })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return configFromBody(await response.json())
}

function configurationPath(kind: string, id: string): string {
  if (kind === "agent") {
    return `/api/v1/agents/${id}/configuration`
  }
  return `/api/v1/tables/${id}/configuration`
}

function configFromBody(body: unknown): EntityConfig {
  if (typeof body !== "object" || body === null) {
    return defaultConfig([])
  }
  const record: Record<string, unknown> = body as Record<string, unknown>
  const options: Record<string, boolean> = {}
  const stored: unknown = record.options
  if (typeof stored === "object" && stored !== null) {
    const optionRecord: Record<string, unknown> = stored as Record<string, unknown>
    for (const key of Object.keys(optionRecord)) {
      const value: unknown = optionRecord[key]
      if (typeof value === "boolean") {
        options[key] = value
      }
    }
  }
  const seconds: unknown = record.interval_seconds
  const minutes: number = typeof seconds === "number" ? seconds / 60 : DEFAULT_REFRESH_MINUTES
  const nextRunAt: unknown = record.next_run_at
  return {
    minutes,
    enabled: record.enabled !== false,
    model: modelId(record.model),
    effort: effortId(record.effort),
    refreshMode: refreshModeId(record.refresh_mode),
    options,
    nextRunAt: typeof nextRunAt === "string" && nextRunAt !== "" ? nextRunAt : null,
  }
}

export function refreshModeId(value: unknown): RefreshMode {
  if (value === "INTERVAL" || value === "ON_CHANGE" || value === "MANUAL") {
    return value
  }
  return "INTERVAL"
}

export function modelId(value: unknown): ModelId {
  if (
    value === "llama3.2" ||
    value === "qwen3:4b" ||
    value === "gpt-4.1" ||
    value === "gpt-4.1-mini" ||
    value === "gpt-6-astra"
  ) {
    return value
  }
  if (value === "gpt") {
    return "gpt-4.1"
  }
  return "gpt-4.1"
}

export function effortId(value: unknown): ReasoningEffort {
  if (
    value === "off" ||
    value === "minimal" ||
    value === "low" ||
    value === "medium" ||
    value === "high" ||
    value === "xhigh"
  ) {
    return value
  }
  return "low"
}

export function effortLevel(value: unknown): EffortLevel {
  const effort: ReasoningEffort = effortId(value)
  if (effort === "off") {
    return "low"
  }
  return effort
}

async function readDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (typeof body === "object" && body !== null && "detail" in body) {
      const detail: unknown = body.detail
      if (typeof detail === "string" && detail !== "") {
        return detail
      }
    }
  } catch {
    return `Request failed (${response.status})`
  }
  return `Request failed (${response.status})`
}

function defaults(fields: readonly OptionField[]): Record<string, boolean> {
  const options: Record<string, boolean> = {}
  for (const field of fields) {
    options[field.key] = field.defaultValue
  }
  return options
}

function clampMinutes(value: unknown): number {
  const parsed: number | null = typeof value === "number" ? acceptMinutes(value) : null
  if (parsed === null) {
    return DEFAULT_REFRESH_MINUTES
  }
  return parsed
}

export function parseRefreshMinutes(raw: string): number | null {
  const parsed: number = Number(raw)
  if (!Number.isFinite(parsed)) {
    return null
  }
  return acceptMinutes(parsed)
}

export function formatRefreshMinutes(minutes: number): string {
  const rounded: number = Math.round(minutes * 1000) / 1000
  return String(rounded)
}

export function secondsFromMinutes(minutes: number): number {
  return Math.max(1, Math.round(minutes * 60))
}

export function minutesFromSeconds(seconds: number): number {
  return acceptMinutes(seconds / 60) ?? DEFAULT_REFRESH_MINUTES
}

function acceptMinutes(value: number): number | null {
  if (!Number.isFinite(value)) {
    return null
  }
  if (value < MIN_REFRESH_MINUTES || value > MAX_REFRESH_MINUTES) {
    return null
  }
  return value
}
