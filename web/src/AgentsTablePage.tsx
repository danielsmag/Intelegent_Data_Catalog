import { useEffect, useRef, useState, type MutableRefObject, type PointerEvent as ReactPointerEvent, type ReactElement } from "react"
import { useSearchParams } from "react-router-dom"
import { isAbortError } from "./api"
import {
  detailString,
  fetchAgent,
  searchAgents,
  updateAgentStatus,
  type AgentKind,
  type AgentListItem,
  type AgentPage,
  type AgentQuery,
  type AgentSort,
  type AgentStatus,
} from "./agentsApi"
import { watchAgentDrag } from "./editorDrag"
import { openInFocusedGroup } from "./editorLayout"
import { InstanceForm } from "./AirflowPages"
import {
  createAirflowInstance,
  syncAirflowInstance,
  updateAirflowInstance,
  type AirflowInstance,
  type AirflowInstanceInput,
  type AirflowInstanceStatus,
} from "./airflowApi"
import { useEditorStatus, useEditorTitle } from "./editorSlots"
import { onAgentBuildFinished, onKnowledgeBuildFinished, requestAgentBuild } from "./knowledgeBuild"
import { showPageAlert } from "./pageAlertStack"
import { getActiveWorkspaceId } from "./workspaceStore"

const SERVICES: readonly { id: ServiceId; label: string }[] = [
  { id: "", label: "All services" },
  { id: "airflow", label: "Airflow" },
  { id: "iceberg", label: "Iceberg" },
]

const STATUSES: readonly { id: "" | AgentStatus; label: string }[] = [
  { id: "", label: "All" },
  { id: "ACTIVE", label: "Active" },
  { id: "MISSING", label: "Missing" },
  { id: "DEPRECATED", label: "Deprecated" },
]

const ASSET_LIST_POLL_MS: number = 5000
const FRESHNESS_TICK_MS: number = 1000
const DEFAULT_FRESHNESS_INTERVAL_SECONDS: number = 600

const RANKS: readonly { id: string; label: string; sort: AgentSort; order: "asc" | "desc" }[] = [
  { id: "freshness", label: "Freshness", sort: "freshness_score", order: "desc" },
  { id: "built", label: "Latest build", sort: "last_built_at", order: "desc" },
  { id: "updated", label: "Recently updated", sort: "updated_at", order: "desc" },
  { id: "name", label: "Name A-Z", sort: "name", order: "asc" },
  { id: "kind", label: "Kind", sort: "kind", order: "asc" },
]

export function AgentsTablePage(): ReactElement {
  const [params, setParams] = useSearchParams()
  const [page, setPage] = useState<AgentPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const queryText: string = params.get("q") ?? ""
  const owner: "me" | "shared" | "all" = ownerParam(params.get("owner"))
  const rank: string = params.get("rank") ?? "updated"
  const status: AgentStatus | null = statusParam(params.get("status"))
  const offset: number = Number(params.get("offset") ?? "0")
  const service: ServiceId = serviceParam(params.get("service"))
  const queryKinds: AgentKind[] = serviceKinds(service)
  const queryFormats: string[] = service === "iceberg" ? ["ICEBERG"] : []
  const creatingInstance: boolean = service === "airflow" && params.get("new") === "1"
  const chosenRank = RANKS.find((item) => item.id === rank) ?? RANKS[2]
  const now: number = useClock(FRESHNESS_TICK_MS)
  const [switchingId, setSwitchingId] = useState<string | null>(null)
  const [reloadingId, setReloadingId] = useState<string | null>(null)
  const buildingIds: MutableRefObject<Set<string>> = useRef<Set<string>>(new Set())
  const reloadRef: MutableRefObject<() => void> = useRef<() => void>(() => {})
  useEditorTitle("Agents")
  useEditorStatus("Agents", page === null ? "Agents" : `${page.total} agents`)
  useEffect(() => {
    let stopped: boolean = false
    let inflight: AbortController | null = null
    const load = (announce: boolean): void => {
      inflight?.abort()
      const controller: AbortController = new AbortController()
      inflight = controller
      if (announce) {
        setLoading(true)
      }
      void searchAgents(
        agentListQuery({
          q: queryText,
          kinds: queryKinds,
          formats: queryFormats,
          owner,
          status,
          sort: chosenRank?.sort,
          order: chosenRank?.order,
          offset,
        }),
        controller.signal,
      )
        .then((result: AgentPage) => {
          if (stopped || controller.signal.aborted) {
            return
          }
          const pending: Set<string> = buildingIds.current
          for (const row of result.items) {
            if (row.build_running) {
              pending.delete(row.id)
            }
          }
          setPage(withPendingBuilds(result, pending))
          setError(null)
        })
        .catch((err: unknown) => {
          if (stopped || controller.signal.aborted || isAbortError(err)) {
            return
          }
          if (announce) {
            setError(err instanceof Error ? err.message : "Could not load agents")
          }
        })
        .finally(() => {
          if (!stopped && !controller.signal.aborted) {
            setLoading(false)
          }
        })
    }
    reloadRef.current = () => {
      load(false)
    }
    load(true)
    const timer: number = window.setInterval(() => {
      load(false)
    }, ASSET_LIST_POLL_MS)
    return () => {
      stopped = true
      inflight?.abort()
      window.clearInterval(timer)
    }
  }, [queryText, owner, rank, status, offset, params])
  useEffect(() => {
    const refresh = (): void => {
      reloadRef.current()
    }
    const stopAgent: () => void = onAgentBuildFinished((job) => {
      buildingIds.current.delete(job.agent_id)
      refresh()
    })
    const stopKnowledge: () => void = onKnowledgeBuildFinished(refresh)
    return () => {
      stopAgent()
      stopKnowledge()
    }
  }, [])
  const rows: AgentListItem[] = page?.items ?? []
  const total: number = page?.total ?? 0
  function toggleInstance(agentId: string, current: AgentStatus): void {
    const next: AirflowInstanceStatus = current === "ACTIVE" ? "DISABLED" : "ACTIVE"
    const shown: AgentStatus = next === "DISABLED" ? "DEPRECATED" : "ACTIVE"
    setSwitchingId(agentId)
    setPage((loaded: AgentPage | null) => replaceStatus(loaded, agentId, shown))
    void fetchAgent(agentId)
      .then((agent) => {
        const instanceId: string | null = detailString(agent.detail, "instance_id")
        if (instanceId === null) {
          throw new Error("Airflow instance is missing")
        }
        return updateAirflowInstance(instanceId, { status: next })
      })
      .catch((err: unknown) => {
        setPage((loaded: AgentPage | null) => replaceStatus(loaded, agentId, current))
        showPageAlert(err instanceof Error ? err.message : "Could not change Airflow status", "error", "Agents")
      })
      .finally(() => {
        setSwitchingId((busy: string | null) => (busy === agentId ? null : busy))
      })
  }
  function reloadMetadata(agentId: string): void {
    setReloadingId(agentId)
    void fetchAgent(agentId)
      .then((agent) => {
        const instanceId: string | null = detailString(agent.detail, "instance_id")
        if (instanceId === null) {
          throw new Error("Airflow instance is missing")
        }
        return syncAirflowInstance(instanceId)
      })
      .then((run) => {
        if (run.sanitized_error !== null) {
          showPageAlert(run.sanitized_error, "error", "Agents")
          return
        }
        showPageAlert("Reloaded Airflow metadata", "success", "Agents")
        reloadRef.current()
      })
      .catch((err: unknown) => {
        showPageAlert(err instanceof Error ? err.message : "Could not reload Airflow metadata", "error", "Agents")
      })
      .finally(() => {
        setReloadingId((busy: string | null) => (busy === agentId ? null : busy))
      })
  }
  function toggleAvailability(agentId: string, current: AgentStatus): void {
    const next: AgentStatus = current === "ACTIVE" ? "DEPRECATED" : "ACTIVE"
    setSwitchingId(agentId)
    setPage((loaded: AgentPage | null) => replaceStatus(loaded, agentId, next))
    void updateAgentStatus(agentId, next)
      .catch((err: unknown) => {
        setPage((loaded: AgentPage | null) => replaceStatus(loaded, agentId, current))
        showPageAlert(err instanceof Error ? err.message : "Could not change status", "error", "Agents")
      })
      .finally(() => {
        setSwitchingId((busy: string | null) => (busy === agentId ? null : busy))
      })
  }
  function startBuilds(ids: readonly string[]): void {
    const pending: Set<string> = buildingIds.current
    for (const id of ids) {
      pending.add(id)
    }
    setPage((current: AgentPage | null) => withPendingBuilds(current, pending))
    for (const id of ids) {
      void requestAgentBuild(id).catch((err: unknown) => {
        pending.delete(id)
        setPage((current: AgentPage | null) => clearBuilding(current, id))
        showPageAlert(err instanceof Error ? err.message : "Build failed", "error", "Agents")
      })
    }
  }
  function patch(next: Record<string, string | null>, drop?: string[]): void {
    const updated: URLSearchParams = new URLSearchParams(params)
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === "") {
        updated.delete(key)
      } else {
        updated.set(key, value)
      }
    }
    for (const key of drop ?? []) {
      updated.delete(key)
    }
    if (!updated.has("offset") && next.offset === undefined) {
      updated.delete("offset")
    }
    setParams(updated)
  }
  function selectService(next: ServiceId): void {
    const updated: URLSearchParams = new URLSearchParams(params)
    updated.delete("kind")
    updated.delete("offset")
    updated.delete("new")
    if (next === "") {
      updated.delete("service")
    } else {
      updated.set("service", next)
    }
    setParams(updated)
  }
  return (
    <section className="catalog-page agent-board">
      <header className="catalog-hero">
        <div className="catalog-hero-top">
          <div className="catalog-hero-copy">
            <p className="catalog-kicker">Catalog</p>
            <h1>Agents</h1>
          </div>
          <div className="catalog-hero-actions">
            {service === "airflow" ? (
              <button
                type="button"
                className="button"
                onClick={() => {
                  patch({ new: creatingInstance ? null : "1" })
                }}
              >
                {creatingInstance ? "Close" : "Add instance"}
              </button>
            ) : null}
            <span className="catalog-count">{loading && page === null ? "Loading…" : agentCount(total)}</span>
          </div>
        </div>
        <p className="catalog-lead">Anything tracked in this workspace.</p>
      </header>
      {creatingInstance ? (
        <div className="catalog-panel">
          <InstanceForm
            submitLabel="Add instance"
            onCancel={() => {
              patch({ new: null })
            }}
            onSubmit={async (input: AirflowInstanceInput) => {
              const created: AirflowInstance = await createAirflowInstance(input)
              patch({ new: null })
              if (created.agent_id !== null) {
                openInFocusedGroup(`/agents/${created.agent_id}`)
              }
            }}
          />
        </div>
      ) : null}
      <div className="agent-board__filters">
        <input
          className="agent-board__search"
          value={queryText}
          placeholder="Search agents"
          aria-label="Search agents"
          onChange={(event) => {
            patch({ q: event.target.value, offset: null })
          }}
        />
        <select
          aria-label="Service type"
          value={service}
          onChange={(event) => {
            selectService(serviceParam(event.target.value))
          }}
        >
          {SERVICES.map((item) => (
            <option key={item.id === "" ? "all" : item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
        <div className="agent-board__pills" role="group" aria-label="Status">
          {STATUSES.map((item) => (
            <button
              key={item.id === "" ? "all" : item.id}
              type="button"
              className={status === (item.id === "" ? null : item.id) ? "filter-chip is-on" : "filter-chip"}
              onClick={() => {
                patch({ status: item.id === "" ? null : item.id, offset: null })
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
        <select
          aria-label="Owner"
          value={owner}
          onChange={(event) => {
            patch({ owner: event.target.value, offset: null })
          }}
        >
          <option value="all">All owners</option>
          <option value="me">Mine</option>
          <option value="shared">Shared</option>
        </select>
        <button
          type="button"
          className="button secondary"
          onClick={() => {
            void exportCsv(params)
          }}
        >
          Export CSV
        </button>
      </div>
      <div className="agent-board__meta">
        <span>{loading && page === null ? "…" : agentCount(total)}</span>
        <label className="agent-board__sort">
          Sort by
          <select
            aria-label="Sort agents"
            value={rank}
            onChange={(event) => {
              patch({ rank: event.target.value, offset: null })
            }}
          >
            {RANKS.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {error !== null ? <p className="error-text">{error}</p> : null}
      {selected.size > 0 ? (
        <div className="agents-bulk">
          <span>{selected.size} selected</span>
          <button
            type="button"
            className="button secondary"
            onClick={() => {
              startBuilds([...selected])
            }}
          >
            Build context
          </button>
        </div>
      ) : null}
      {loading && page === null ? <p className="note">Loading agents…</p> : null}
      {!loading && error === null && rows.length === 0 ? (
        <p className="note">No agents match these filters.</p>
      ) : null}
      <div className="agent-board__table" role="table" aria-label="Agents">
        <div className="agent-row agent-row--head" role="row">
          <span role="columnheader" />
          <span role="columnheader">Agent</span>
          <span role="columnheader">Refresh</span>
          <span role="columnheader">Latest build</span>
          <span role="columnheader">Updated</span>
          <span role="columnheader" />
          <span role="columnheader">Freshness</span>
        </div>
        {rows.map((row: AgentListItem) => (
          <AgentRow
            key={row.id}
            row={row}
            now={now}
            selected={selected.has(row.id)}
            onSelected={(on: boolean) => {
              setSelected((current: Set<string>) => {
                const next: Set<string> = new Set(current)
                if (on) {
                  next.add(row.id)
                } else {
                  next.delete(row.id)
                }
                return next
              })
            }}
            onOpen={() => {
              openInFocusedGroup(`/agents/${row.id}`)
            }}
            onBuild={() => {
              startBuilds([row.id])
            }}
            switching={switchingId === row.id}
            onToggle={() => {
              if (row.kind === "AIRFLOW_INSTANCE") {
                toggleInstance(row.id, row.status)
                return
              }
              toggleAvailability(row.id, row.status)
            }}
            reloading={reloadingId === row.id}
            onReload={() => {
              reloadMetadata(row.id)
            }}
            onDragStart={(event: ReactPointerEvent<HTMLButtonElement>) => {
              const ids: string[] = selected.has(row.id) && selected.size > 1 ? [...selected] : [row.id]
              watchAgentDrag(event, { agentIds: ids, title: row.name })
            }}
          />
        ))}
      </div>
      <footer className="agents-pager">
        <span>
          {rows.length === 0 ? 0 : offset + 1}-{offset + rows.length} of {total}
        </span>
        <button
          type="button"
          className="button secondary"
          disabled={offset <= 0}
          onClick={() => {
            patch({ offset: String(Math.max(0, offset - 100)) })
          }}
        >
          Previous
        </button>
        <button
          type="button"
          className="button secondary"
          disabled={page === null || offset + rows.length >= page.total}
          onClick={() => {
            patch({ offset: String(offset + 100) })
          }}
        >
          Next
        </button>
      </footer>
    </section>
  )
}

function useClock(intervalMs: number): number {
  const [now, setNow] = useState<number>(() => Date.now())
  useEffect(() => {
    const timer: number = window.setInterval(() => {
      setNow(Date.now())
    }, intervalMs)
    return () => {
      window.clearInterval(timer)
    }
  }, [intervalMs])
  return now
}

function agentListQuery(input: {
  q: string
  kinds: AgentKind[]
  formats: string[]
  owner: "me" | "shared" | "all"
  status: AgentStatus | null
  sort: AgentSort | undefined
  order: "asc" | "desc" | undefined
  offset: number
}): AgentQuery {
  const offset: number = Number.isFinite(input.offset) ? input.offset : 0
  return {
    q: input.q,
    kinds: input.kinds,
    formats: input.formats,
    owner: input.owner,
    workspaceId: getActiveWorkspaceId(),
    status: input.status ?? undefined,
    sort: input.sort,
    order: input.order,
    limit: 100,
    offset,
  }
}

function withPendingBuilds(page: AgentPage | null, pending: ReadonlySet<string>): AgentPage | null {
  if (page === null || pending.size === 0) {
    return page
  }
  const items: AgentListItem[] = page.items.map((row: AgentListItem) => {
    if (!pending.has(row.id) || row.build_running) {
      return row
    }
    return { ...row, build_running: true }
  })
  return { items, total: page.total }
}

function clearBuilding(page: AgentPage | null, agentId: string): AgentPage | null {
  if (page === null) {
    return page
  }
  const items: AgentListItem[] = page.items.map((row: AgentListItem) => {
    if (row.id !== agentId) {
      return row
    }
    return { ...row, build_running: false }
  })
  return { items, total: page.total }
}

function liveFreshness(row: AgentListItem, now: number): number {
  if (row.last_built_at === null || row.last_built_at === "") {
    return 0
  }
  if (row.refresh_mode === "MANUAL") {
    return 100
  }
  const builtMs: number = Date.parse(row.last_built_at)
  if (Number.isNaN(builtMs)) {
    return row.freshness_score
  }
  const span: number = row.interval_seconds > 0 ? row.interval_seconds : DEFAULT_FRESHNESS_INTERVAL_SECONDS
  const ageSeconds: number = Math.max(0, (now - builtMs) / 1000)
  const raw: number = 100 - Math.trunc((ageSeconds / span) * 100)
  if (raw < 0) {
    return 0
  }
  if (raw > 100) {
    return 100
  }
  return raw
}

function AgentRow(props: {
  row: AgentListItem
  now: number
  selected: boolean
  onSelected: (on: boolean) => void
  onOpen: () => void
  onBuild: () => void
  onDragStart: (event: ReactPointerEvent<HTMLButtonElement>) => void
  switching: boolean
  onToggle: () => void
  reloading: boolean
  onReload: () => void
}): ReactElement {
  const row: AgentListItem = props.row
  const score: number = liveFreshness(row, props.now)
  return (
    <div className={props.selected ? "agent-row is-selected" : "agent-row"} role="row">
      <span className="agent-row__check" role="cell">
        <input
          type="checkbox"
          checked={props.selected}
          aria-label={`Select ${row.name}`}
          onChange={(event) => {
            props.onSelected(event.target.checked)
          }}
        />
      </span>
      <span className="agent-row__identity" role="cell">
        {row.kind === "TABLE" || row.kind === "AIRFLOW_INSTANCE" ? (
          <Availability status={row.status} disabled={props.switching} onToggle={props.onToggle} />
        ) : null}
        <button
          type="button"
          className="agent-row__name"
          onPointerDown={props.onDragStart}
          onClick={props.onOpen}
        >
          {row.name}
        </button>
      </span>
      <span className="agent-row__refresh" role="cell">
        {typeof row.schedule === "string" && row.schedule !== "" ? (
          <span title={row.schedule}>{scheduleTime(row.schedule)}</span>
        ) : (
          <span>—</span>
        )}
        {typeof row.next_run_at === "string" && row.next_run_at !== "" ? (
          <small className="agent-row__when">next {formatWhen(row.next_run_at)}</small>
        ) : null}
      </span>
      <span role="cell">
        {formatWhen(row.last_built_at)}
        <StateMark row={row} />
      </span>
      <span role="cell">
        {formatWhen(row.updated_at)}
      </span>
      <span className="agent-row__actions" role="cell">
        {row.kind === "AIRFLOW_INSTANCE" ? (
          <button
            type="button"
            className="button secondary agent-row__build"
            disabled={props.reloading || row.status !== "ACTIVE"}
            title="Reload Airflow metadata"
            onClick={props.onReload}
          >
            {props.reloading ? "Reloading" : "Reload"}
          </button>
        ) : null}
        <button
          type="button"
          className="button secondary agent-row__build"
          disabled={row.build_running}
          onClick={() => {
            props.onBuild()
          }}
        >
          {row.build_running ? "Building" : "Build"}
        </button>
      </span>
      <span role="cell">
        <FreshnessSpark score={score} />
      </span>
    </div>
  )
}

function Availability(props: { status: string; disabled: boolean; onToggle: () => void }): ReactElement {
  const active: boolean = props.status === "ACTIVE"
  const tone: string = active ? "active" : "disabled"
  const label: string = active ? "Active" : "Disabled"
  return (
    <button
      type="button"
      className={`agent-availability agent-availability--${tone}`}
      aria-pressed={active}
      disabled={props.disabled}
      onClick={(event) => {
        event.stopPropagation()
        props.onToggle()
      }}
    >
      {label}
    </button>
  )
}

function replaceStatus(page: AgentPage | null, agentId: string, status: AgentStatus): AgentPage | null {
  if (page === null) {
    return null
  }
  return {
    ...page,
    items: page.items.map((item: AgentListItem) => (item.id === agentId ? { ...item, status } : item)),
  }
}

function StateMark(props: { row: AgentListItem }): ReactElement {
  if (props.row.build_running) {
    return <span className="agent-state agent-state--running" title="Building" aria-label="Building" />
  }
  if (props.row.status === "MISSING") {
    return <span className="agent-state agent-state--missing" title="Missing" aria-label="Missing" />
  }
  if (props.row.status === "DEPRECATED") {
    return <span className="agent-state agent-state--deprecated" title="Deprecated" aria-label="Deprecated" />
  }
  return <span className="agent-state agent-state--active" title="Active" aria-label="Active" />
}

function FreshnessSpark(props: { score: number }): ReactElement {
  const tone: string = props.score >= 70 ? "good" : props.score >= 30 ? "warn" : "stale"
  const bars: number[] = freshnessBars(props.score)
  return (
    <span className={`agent-spark agent-spark--${tone}`} aria-label={`Freshness ${props.score}`}>
      {bars.map((height: number, index: number) => (
        <span key={index} style={{ height: `${height}%` }} />
      ))}
    </span>
  )
}

function freshnessBars(score: number): number[] {
  const bars: number[] = []
  for (let index: number = 0; index < 12; index += 1) {
    const wave: number = 35 + ((index * 17 + score) % 50)
    const scaled: number = Math.round((wave * Math.max(score, 12)) / 100)
    bars.push(Math.max(18, Math.min(100, scaled)))
  }
  return bars
}

function agentCount(total: number): string {
  return total === 1 ? "1 Agent" : `${total} Agents`
}

function formatWhen(value: string | null): string {
  if (value === null || value === "") {
    return "—"
  }
  const parsed: Date = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value.slice(0, 19).replace("T", " ")
  }
  return localStamp(parsed)
}

function localStamp(value: Date): string {
  const year: string = String(value.getFullYear())
  const month: string = pad2(String(value.getMonth() + 1))
  const day: string = pad2(String(value.getDate()))
  const hour: string = pad2(String(value.getHours()))
  const minute: string = pad2(String(value.getMinutes()))
  const second: string = pad2(String(value.getSeconds()))
  return `${year}-${month}-${day} ${hour}:${minute}:${second}`
}

const WEEKDAYS: readonly string[] = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

function scheduleTime(cron: string): string {
  const parts: string[] = cron.trim().split(/\s+/)
  if (parts.length !== 5) {
    return cron
  }
  const minute: string = parts[0] ?? "*"
  const hour: string = parts[1] ?? "*"
  const day: string = parts[2] ?? "*"
  const month: string = parts[3] ?? "*"
  const weekday: string = parts[4] ?? "*"
  if (minute.startsWith("*/") && hour === "*" && day === "*" && month === "*" && weekday === "*") {
    const step: string = minute.slice(2)
    return step === "1" ? "every minute" : `every ${step} min`
  }
  if (minute === "0" && hour === "*" && day === "*" && month === "*" && weekday === "*") {
    return "hourly"
  }
  if (isField(minute, 59) && isField(hour, 23) && day === "*" && month === "*") {
    const clock: string = `${pad2(hour)}:${pad2(minute)}`
    if (weekday === "*") {
      return `${clock} daily`
    }
    const days: string | null = weekdayLabel(weekday)
    if (days !== null) {
      return `${clock} ${days}`
    }
  }
  return cron
}

function isField(value: string, max: number): boolean {
  if (!/^\d{1,2}$/.test(value)) {
    return false
  }
  const parsed: number = Number(value)
  return parsed >= 0 && parsed <= max
}

function pad2(value: string): string {
  return value.padStart(2, "0")
}

function weekdayLabel(value: string): string | null {
  if (/^\d$/.test(value)) {
    const index: number = Number(value)
    return WEEKDAYS[index] ?? null
  }
  const range: RegExpMatchArray | null = /^(\d)-(\d)$/.exec(value)
  if (range === null) {
    return null
  }
  const start: number = Number(range[1])
  const end: number = Number(range[2])
  const left: string | undefined = WEEKDAYS[start]
  const right: string | undefined = WEEKDAYS[end]
  if (left === undefined || right === undefined || start > end) {
    return null
  }
  return `${left}–${right}`
}

type ServiceId = "" | "airflow" | "iceberg"

function serviceParam(value: string | null): ServiceId {
  if (value === "airflow" || value === "iceberg") {
    return value
  }
  return ""
}

function serviceKinds(service: ServiceId): AgentKind[] {
  if (service === "airflow") {
    return ["AIRFLOW_DAG", "AIRFLOW_INSTANCE"]
  }
  if (service === "iceberg") {
    return ["TABLE", "SCHEMA"]
  }
  return []
}

function statusParam(value: string | null): AgentStatus | null {
  if (value === "ACTIVE" || value === "MISSING" || value === "DEPRECATED") {
    return value
  }
  return null
}

function ownerParam(value: string | null): "me" | "shared" | "all" {
  if (value === "me" || value === "shared") {
    return value
  }
  return "all"
}

async function exportCsv(params: URLSearchParams): Promise<void> {
  const kinds: AgentKind[] = serviceKinds(serviceParam(params.get("service")))
  const formats: string[] = params.get("service") === "iceberg" ? ["ICEBERG"] : []
  const collected: AgentListItem[] = []
  let offset: number = 0
  const status: AgentStatus | null = statusParam(params.get("status"))
  while (collected.length < 5000) {
    const page: AgentPage = await searchAgents({
      q: params.get("q") ?? undefined,
      kinds,
      formats,
      owner: ownerParam(params.get("owner")),
      workspaceId: getActiveWorkspaceId(),
      status: status ?? undefined,
      limit: 200,
      offset,
    })
    collected.push(...page.items)
    offset += page.items.length
    if (page.items.length === 0 || offset >= page.total) {
      break
    }
  }
  const lines: string[] = ["kind,name,status,context,freshness"]
  for (const row of collected) {
    lines.push(
      [
        row.kind,
        csv(row.name),
        row.status,
        row.context_state,
        String(row.freshness_score),
      ].join(","),
    )
  }
  const blob: Blob = new Blob([lines.join("\n")], { type: "text/csv" })
  const url: string = URL.createObjectURL(blob)
  const link: HTMLAnchorElement = document.createElement("a")
  link.href = url
  link.download = "agents.csv"
  link.click()
  URL.revokeObjectURL(url)
}

function csv(value: string): string {
  return `"${value.replaceAll("\"", "\"\"")}"`
}
