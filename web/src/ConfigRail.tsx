import { useEffect, useRef, useState, useSyncExternalStore, type ChangeEvent, type KeyboardEvent, type MutableRefObject, type ReactElement } from "react"
import {
  fetchAirflowSchedule,
  saveAirflowSchedule,
  type SyncSchedule,
} from "./airflowApi"
import { bumpAirflowRevision } from "./airflowSidebarStore"
import { errorMessage, isAbortError } from "./api"
import { PanelChevron } from "./icons"
import { ResizeHandle } from "./ResizeHandle"
import {
  DEFAULT_REFRESH_MINUTES,
  MAX_REFRESH_MINUTES,
  MIN_REFRESH_MINUTES,
  formatRefreshMinutes,
  minutesFromSeconds,
  parseRefreshMinutes,
  secondsFromMinutes,
  useRefreshTimer,
  type OptionField,
} from "./refreshSettings"

const TAB_KEY: string = "idc-side-tab"
const LEGACY_RAIL_KEY: string = "idc-config-rail-open"
const CONFIG_TAB: string = "configuration"

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
  busy: boolean
  error: string | null
  detail: string | null
  options: readonly ConfigOption[]
  onCommit: (
    minutes: number,
    enabled: boolean,
    options: Readonly<Record<string, boolean>>,
  ) => void
  onOption?: (key: string, checked: boolean) => void
}): ReactElement {
  const [draft, setDraft] = useState<string>(formatRefreshMinutes(props.minutes))
  const [draftEnabled, setDraftEnabled] = useState<boolean>(props.enabled)
  const [optionDraft, setOptionDraft] = useState<Record<string, boolean>>(() => optionMap(props.options))
  const savedOptions: string = optionKey(props.options)
  useEffect(() => {
    setDraft(formatRefreshMinutes(props.minutes))
  }, [props.minutes])
  useEffect(() => {
    setDraftEnabled(props.enabled)
  }, [props.enabled])
  useEffect(() => {
    setOptionDraft(optionMap(props.options))
  }, [savedOptions])

  const parsed: number | null = parseRefreshMinutes(draft)
  const optionsDirty: boolean = props.options.some(
    (option: ConfigOption) => optionDraft[option.key] !== option.checked,
  )
  const dirty: boolean =
    (parsed !== null && parsed !== props.minutes) || draftEnabled !== props.enabled || optionsDirty

  function save(): void {
    if (parsed === null || props.busy) {
      return
    }
    props.onCommit(parsed, draftEnabled, optionDraft)
  }

  return (
    <SideTabs
      tabs={[
        {
          id: CONFIG_TAB,
          label: "Configuration",
          content: (
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
          ),
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
        if (loaded !== null) {
          setSchedule(loaded)
          setBusy(false)
          return
        }
        const created: SyncSchedule = await saveAirflowSchedule(
          props.instanceId,
          secondsFromMinutes(DEFAULT_REFRESH_MINUTES),
          true,
        )
        if (controller.signal.aborted) {
          return
        }
        setSchedule(created)
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
  ): void {
    const requestId: number = saveId.current + 1
    saveId.current = requestId
    setBusy(true)
    setError(null)
    void saveAirflowSchedule(props.instanceId, secondsFromMinutes(nextMinutes), nextEnabled)
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
