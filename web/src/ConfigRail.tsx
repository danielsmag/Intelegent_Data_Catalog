import { useEffect, useRef, useState, useSyncExternalStore, type ChangeEvent, type KeyboardEvent, type MutableRefObject, type ReactElement } from "react"
import {
  fetchAirflowSchedule,
  saveAirflowSchedule,
  type SyncSchedule,
} from "./airflowApi"
import { bumpAirflowRevision } from "./airflowSidebarStore"
import { errorMessage, isAbortError } from "./api"
import { BuildTracePanel } from "./BuildTracePanel"
import { PanelChevron } from "./icons"
import { AlertPanel } from "./pageAlertStack"
import { ResizeHandle } from "./ResizeHandle"
import {
  DEFAULT_REFRESH_MINUTES,
  EFFORT_LEVELS,
  MAX_REFRESH_MINUTES,
  MIN_REFRESH_MINUTES,
  MODEL_OPTIONS,
  effortId,
  effortLevel,
  formatRefreshMinutes,
  minutesFromSeconds,
  modelId,
  parseRefreshMinutes,
  secondsFromMinutes,
  useRefreshTimer,
  type EffortLevel,
  type ModelId,
  type OptionField,
  type ReasoningEffort,
} from "./refreshSettings"

const TAB_KEY: string = "idc-side-tab"
const LEGACY_RAIL_KEY: string = "idc-config-rail-open"
const CONFIG_TAB: string = "configuration"
const TRACE_TAB: string = "stream-tracing"
const ALERT_TAB: string = "alerts"

let activeTab: string | null = readActiveTab()
const tabListeners: Set<() => void> = new Set()

function readActiveTab(): string | null {
  try {
    const stored: string | null = window.localStorage.getItem(TAB_KEY)
    if (stored !== null) {
      return stored === "" ? null : stored
    }
    if (window.localStorage.getItem(LEGACY_RAIL_KEY) === "closed") {
      return null
    }
  } catch {
    return CONFIG_TAB
  }
  return CONFIG_TAB
}

function getActiveTab(): string | null {
  return activeTab
}

function subscribeTabs(listener: () => void): () => void {
  tabListeners.add(listener)
  return () => {
    tabListeners.delete(listener)
  }
}

function setActiveTab(tabId: string | null): void {
  activeTab = tabId
  try {
    window.localStorage.setItem(TAB_KEY, tabId ?? "")
  } catch {
    activeTab = tabId
  }
  for (const listener of tabListeners) {
    listener()
  }
}

function useActiveTab(): string | null {
  return useSyncExternalStore(subscribeTabs, getActiveTab, () => CONFIG_TAB)
}

export type SideTab = {
  id: string
  label: string
  content: ReactElement
}

export function SideTabs(props: { tabs: readonly SideTab[] }): ReactElement {
  const activeId: string | null = useActiveTab()
  const active: SideTab | null = props.tabs.find((tab: SideTab) => tab.id === activeId) ?? null
  return (
    <div className="side-dock">
      {active === null ? null : (
        <div className="config-rail-frame">
          <ResizeHandle pane="rail" edge="start" label={`Resize ${active.label}`} />
          <aside className="config-rail" aria-label={active.label}>
            <div className="config-rail-head">
              <span>{active.label}</span>
              <button
                type="button"
                className="sidebar-toggle"
                aria-label={`Close ${active.label}`}
                title="Close"
                onClick={() => {
                  setActiveTab(null)
                }}
              >
                <PanelChevron direction="right" />
              </button>
            </div>
            <div className="config-rail-body">{active.content}</div>
          </aside>
        </div>
      )}
      <div className="side-tabs" role="tablist" aria-label="Side panels">
        {props.tabs.map((tab: SideTab) => {
          const selected: boolean = tab.id === active?.id
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              className="side-tab"
              aria-selected={selected}
              title={selected ? `Close ${tab.label}` : `Open ${tab.label}`}
              onClick={() => {
                setActiveTab(selected ? null : tab.id)
              }}
            >
              <span>{tab.label}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

export type ConfigOption = {
  key: string
  label: string
  checked: boolean
}

export function ConfigRail(props: {
  minutes: number
  enabled: boolean
  model: ModelId
  effort: ReasoningEffort
  busy: boolean
  error: string | null
  detail: string | null
  options: readonly ConfigOption[]
  onCommit: (
    minutes: number,
    enabled: boolean,
    options: Readonly<Record<string, boolean>>,
    model: ModelId,
    effort: ReasoningEffort,
  ) => void
  onOption?: (key: string, checked: boolean) => void
}): ReactElement {
  const [draft, setDraft] = useState<string>(formatRefreshMinutes(props.minutes))
  const [draftEnabled, setDraftEnabled] = useState<boolean>(props.enabled)
  const [draftModel, setDraftModel] = useState<ModelId>(props.model)
  const [thinkingOn, setThinkingOn] = useState<boolean>(props.effort !== "off")
  const [draftLevel, setDraftLevel] = useState<EffortLevel>(effortLevel(props.effort))
  const [optionDraft, setOptionDraft] = useState<Record<string, boolean>>(() => optionMap(props.options))
  const savedOptions: string = optionKey(props.options)
  useEffect(() => {
    setDraft(formatRefreshMinutes(props.minutes))
  }, [props.minutes])
  useEffect(() => {
    setDraftEnabled(props.enabled)
  }, [props.enabled])
  useEffect(() => {
    setDraftModel(props.model)
  }, [props.model])
  useEffect(() => {
    setThinkingOn(props.effort !== "off")
    setDraftLevel(effortLevel(props.effort))
  }, [props.effort])
  useEffect(() => {
    setOptionDraft(optionMap(props.options))
  }, [savedOptions])

  const parsed: number | null = parseRefreshMinutes(draft)
  const committedEffort: ReasoningEffort = thinkingOn ? draftLevel : "off"
  const optionsDirty: boolean = props.options.some(
    (option: ConfigOption) => optionDraft[option.key] !== option.checked,
  )
  const dirty: boolean =
    (parsed !== null && parsed !== props.minutes) ||
    draftEnabled !== props.enabled ||
    draftModel !== props.model ||
    committedEffort !== props.effort ||
    optionsDirty

  function save(): void {
    if (parsed === null || props.busy) {
      return
    }
    props.onCommit(parsed, draftEnabled, optionDraft, draftModel, committedEffort)
  }

  return (
    <>
              <label className="config-field">
                <span>Refresh every</span>
                <span className="config-minutes">
                  <input
                    type="number"
                    min={MIN_REFRESH_MINUTES}
                    max={MAX_REFRESH_MINUTES}
                    step="any"
                    inputMode="decimal"
                    value={draft}
                    disabled={props.busy}
                    aria-label="Refresh interval in minutes"
                    onChange={(event: ChangeEvent<HTMLInputElement>) => {
                      setDraft(event.target.value)
                    }}
                    onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
                      if (event.key === "Enter") {
                        save()
                      }
                    }}
                  />
                  <span>minutes</span>
                </span>
              </label>
              <p className="comment">Default {formatRefreshMinutes(DEFAULT_REFRESH_MINUTES)} minutes.</p>
              <label className="config-check">
                <input
                  type="checkbox"
                  checked={draftEnabled}
                  disabled={props.busy}
                  onChange={(event: ChangeEvent<HTMLInputElement>) => {
                    setDraftEnabled(event.target.checked)
                  }}
                />
                Automatic refresh
              </label>
              <label className="config-field">
                <span>Model</span>
                <select
                  value={draftModel}
                  disabled={props.busy}
                  aria-label="Model"
                  onChange={(event: ChangeEvent<HTMLSelectElement>) => {
                    setDraftModel(modelId(event.target.value))
                  }}
                >
                  {MODEL_OPTIONS.map((choice: (typeof MODEL_OPTIONS)[number]) => (
                    <option key={choice.id} value={choice.id}>
                      {choice.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="config-check">
                <input
                  type="checkbox"
                  checked={thinkingOn}
                  disabled={props.busy}
                  aria-label="Thinking"
                  onChange={(event: ChangeEvent<HTMLInputElement>) => {
                    setThinkingOn(event.target.checked)
                  }}
                />
                Thinking
              </label>
              <label className="config-field">
                <span>Effort</span>
                <select
                  value={draftLevel}
                  disabled={props.busy || !thinkingOn}
                  aria-label="Reasoning effort"
                  onChange={(event: ChangeEvent<HTMLSelectElement>) => {
                    setDraftLevel(effortLevel(event.target.value))
                  }}
                >
                  {EFFORT_LEVELS.map((choice: (typeof EFFORT_LEVELS)[number]) => (
                    <option key={choice.id} value={choice.id}>
                      {choice.label}
                    </option>
                  ))}
                </select>
              </label>
              {props.detail !== null ? <p className="comment">{props.detail}</p> : null}
              {props.error !== null ? <p className="error-text">{props.error}</p> : null}
              {props.options.length > 0 ? (
                <div className="config-options">
                  <h2>Context build</h2>
                  {props.options.map((option: ConfigOption) => (
                    <label key={option.key} className="config-check">
                      <input
                        type="checkbox"
                        checked={optionDraft[option.key] ?? option.checked}
                        onChange={(event: ChangeEvent<HTMLInputElement>) => {
                          setOptionDraft((current: Record<string, boolean>) => ({
                            ...current,
                            [option.key]: event.target.checked,
                          }))
                        }}
                      />
                      {option.label}
                    </label>
                  ))}
                </div>
              ) : null}
              <button
                type="button"
                className="button config-save"
                disabled={props.busy || !dirty || parsed === null}
                onClick={save}
              >
                {props.busy ? "Saving…" : "Save"}
              </button>
    </>
  )
}

export function AppSideDock(props: { onConfigSlot: (node: HTMLDivElement | null) => void }): ReactElement {
  return (
    <SideTabs
      tabs={[
        {
          id: CONFIG_TAB,
          label: "Configuration",
          content: <div className="config-rail-slot" ref={props.onConfigSlot} />,
        },
        {
          id: TRACE_TAB,
          label: "Stream tracing",
          content: <BuildTracePanel />,
        },
        {
          id: ALERT_TAB,
          label: "Alerts",
          content: <AlertPanel />,
        },
      ]}
    />
  )
}

function optionMap(options: readonly ConfigOption[]): Record<string, boolean> {
  const values: Record<string, boolean> = {}
  for (const option of options) {
    values[option.key] = option.checked
  }
  return values
}

function optionKey(options: readonly ConfigOption[]): string {
  return options.map((option: ConfigOption) => `${option.key}:${option.checked}`).join("|")
}

export function optionsFromFields(
  fields: readonly OptionField[],
  values: Readonly<Record<string, boolean>>,
): ConfigOption[] {
  return fields.map((field: OptionField): ConfigOption => ({
    key: field.key,
    label: field.label,
    checked: values[field.key] ?? field.defaultValue,
  }))
}

export function AirflowConfigRail(props: { instanceId: string }): ReactElement {
  const [schedule, setSchedule] = useState<SyncSchedule | null>(null)
  const [busy, setBusy] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)
  const saveId: MutableRefObject<number> = useRef(0)
  const [clock, setClock] = useState<number>(Date.now())
  const minutes: number =
    schedule === null ? DEFAULT_REFRESH_MINUTES : minutesFromSeconds(schedule.interval_seconds)
  const enabled: boolean = schedule?.enabled ?? true
  const model: ModelId = modelId(schedule?.model)
  const effort: ReasoningEffort = effortId(schedule?.effort)

  useEffect(() => {
    const controller: AbortController = new AbortController()
    setBusy(true)
    setError(null)
    setSchedule(null)
    void fetchAirflowSchedule(props.instanceId, controller.signal)
      .then(async (loaded: SyncSchedule | null) => {
        if (controller.signal.aborted) {
          return
        }
        if (loaded === null) {
          setError("Airflow schedule is missing")
          setBusy(false)
          return
        }
        setSchedule(loaded)
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
  }, [props.instanceId])

  useEffect(() => {
    const timer: number = window.setInterval(() => {
      setClock(Date.now())
    }, 1000)
    return () => {
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    let stopped: boolean = false
    const timer: number = window.setInterval(() => {
      const request: number = saveId.current
      void fetchAirflowSchedule(props.instanceId, AbortSignal.timeout(4000))
        .then((loaded: SyncSchedule | null) => {
          if (stopped || loaded === null || saveId.current !== request) {
            return
          }
          setSchedule(loaded)
        })
        .catch(() => {
          return
        })
    }, 1000)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [props.instanceId])

  useRefreshTimer(schedule !== null && enabled, minutes, () => {
    bumpAirflowRevision()
  })

  function commit(
    nextMinutes: number,
    nextEnabled: boolean,
    _options: Readonly<Record<string, boolean>>,
    model: ModelId,
    effort: ReasoningEffort,
  ): void {
    const requestId: number = saveId.current + 1
    saveId.current = requestId
    setBusy(true)
    setError(null)
    void saveAirflowSchedule(
      props.instanceId,
      secondsFromMinutes(nextMinutes),
      nextEnabled,
      model,
      effort,
    )
      .then((saved: SyncSchedule) => {
        if (saveId.current !== requestId) {
          return
        }
        setSchedule(saved)
      })
      .catch((err: unknown) => {
        if (saveId.current !== requestId) {
          return
        }
        setError(errorMessage(err))
      })
      .finally(() => {
        if (saveId.current === requestId) {
          setBusy(false)
        }
      })
  }

  let detail: string | null = null
  if (schedule !== null && schedule.enabled) {
    detail = nextSyncLabel(schedule.next_run_at, clock)
  } else if (schedule !== null) {
    detail = "Automatic refresh is off."
  }

  return (
    <ConfigRail
      minutes={minutes}
      enabled={enabled}
      model={model}
      effort={effort}
      busy={busy}
      error={error}
      detail={detail}
      options={[]}
      onCommit={commit}
    />
  )
}

function nextSyncLabel(nextRunAt: string, now: number): string {
  const when: string = formatWhen(nextRunAt)
  const at: number = new Date(nextRunAt).getTime()
  if (Number.isNaN(at)) {
    return `Next sync ${when}.`
  }
  const left: number = Math.ceil((at - now) / 1000)
  if (left <= 0) {
    return `Next sync ${when} · due`
  }
  if (left < 60) {
    return `Next sync ${when} · in ${left}s`
  }
  const mins: number = Math.floor(left / 60)
  const secs: number = left % 60
  return `Next sync ${when} · in ${mins}m ${secs}s`
}

function formatWhen(value: string): string {
  const parsed: Date = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }
  return parsed.toLocaleString()
}
