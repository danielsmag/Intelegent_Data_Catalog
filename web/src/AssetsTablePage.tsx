import { useEffect, useRef, useState, type MutableRefObject, type PointerEvent as ReactPointerEvent, type ReactElement } from "react"
import { useSearchParams } from "react-router-dom"
import { isAbortError } from "./api"
import {
  detailString,
  fetchAsset,
  searchAssets,
  updateAssetStatus,
  type AssetKind,
  type AssetListItem,
  type AssetPage,
  type AssetQuery,
  type AssetSort,
  type AssetStatus,
} from "./assetsApi"
import { watchAssetDrag } from "./editorDrag"
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
import { onAssetBuildFinished, onKnowledgeBuildFinished, requestAssetBuild } from "./knowledgeBuild"
import { showPageAlert } from "./pageAlertStack"
import { getActiveWorkspaceId } from "./workspaceStore"

const SERVICES: readonly { id: ServiceId; label: string }[] = [
  { id: "", label: "All services" },
  { id: "airflow", label: "Airflow" },
  { id: "iceberg", label: "Iceberg" },
]

const STATUSES: readonly { id: "" | AssetStatus; label: string }[] = [
  { id: "", label: "All" },
  { id: "ACTIVE", label: "Active" },
  { id: "MISSING", label: "Missing" },
  { id: "DEPRECATED", label: "Deprecated" },
]

const ASSET_LIST_POLL_MS: number = 5000
const FRESHNESS_TICK_MS: number = 1000
const DEFAULT_FRESHNESS_INTERVAL_SECONDS: number = 600

const RANKS: readonly { id: string; label: string; sort: AssetSort; order: "asc" | "desc" }[] = [
  { id: "freshness", label: "Freshness", sort: "freshness_score", order: "desc" },
  { id: "built", label: "Latest build", sort: "last_built_at", order: "desc" },
  { id: "updated", label: "Recently updated", sort: "updated_at", order: "desc" },
  { id: "name", label: "Name A-Z", sort: "name", order: "asc" },
  { id: "kind", label: "Kind", sort: "kind", order: "asc" },
]

export function AssetsTablePage(): ReactElement {
  const [params, setParams] = useSearchParams()
  const [page, setPage] = useState<AssetPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const queryText: string = params.get("q") ?? ""
  const owner: "me" | "shared" | "all" = ownerParam(params.get("owner"))
  const rank: string = params.get("rank") ?? "updated"
  const status: AssetStatus | null = statusParam(params.get("status"))
  const offset: number = Number(params.get("offset") ?? "0")
  const service: ServiceId = serviceParam(params.get("service"))
  const queryKinds: AssetKind[] = serviceKinds(service)
  const queryFormats: string[] = service === "iceberg" ? ["ICEBERG"] : []
  const creatingInstance: boolean = service === "airflow" && params.get("new") === "1"
  const chosenRank = RANKS.find((item) => item.id === rank) ?? RANKS[2]
  const now: number = useClock(FRESHNESS_TICK_MS)
  const [switchingId, setSwitchingId] = useState<string | null>(null)
  const [reloadingId, setReloadingId] = useState<string | null>(null)
  const buildingIds: MutableRefObject<Set<string>> = useRef<Set<string>>(new Set())
  const reloadRef: MutableRefObject<() => void> = useRef<() => void>(() => {})
  useEditorTitle("Assets")
  useEditorStatus("Assets", page === null ? "Assets" : `${page.total} assets`)
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
      void searchAssets(
        assetListQuery({
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
        .then((result: AssetPage) => {
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
            setError(err instanceof Error ? err.message : "Could not load assets")
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
    const stopAsset: () => void = onAssetBuildFinished((job) => {
      buildingIds.current.delete(job.asset_id)
      refresh()
    })
    const stopKnowledge: () => void = onKnowledgeBuildFinished(refresh)
    return () => {
      stopAsset()
      stopKnowledge()
    }
  }, [])
  const rows: AssetListItem[] = page?.items ?? []
  const total: number = page?.total ?? 0
  function toggleInstance(assetId: string, current: AssetStatus): void {
    const next: AirflowInstanceStatus = current === "ACTIVE" ? "DISABLED" : "ACTIVE"
    const shown: AssetStatus = next === "DISABLED" ? "DEPRECATED" : "ACTIVE"
    setSwitchingId(assetId)
    setPage((loaded: AssetPage | null) => replaceStatus(loaded, assetId, shown))
    void fetchAsset(assetId)
      .then((asset) => {
        const instanceId: string | null = detailString(asset.detail, "instance_id")
        if (instanceId === null) {
          throw new Error("Airflow instance is missing")
        }
        return updateAirflowInstance(instanceId, { status: next })
      })
      .catch((err: unknown) => {
        setPage((loaded: AssetPage | null) => replaceStatus(loaded, assetId, current))
        showPageAlert(err instanceof Error ? err.message : "Could not change Airflow status", "error", "Assets")
      })
      .finally(() => {
        setSwitchingId((busy: string | null) => (busy === assetId ? null : busy))
      })
  }
  function reloadMetadata(assetId: string): void {
    setReloadingId(assetId)
    void fetchAsset(assetId)
      .then((asset) => {
        const instanceId: string | null = detailString(asset.detail, "instance_id")
        if (instanceId === null) {
          throw new Error("Airflow instance is missing")
        }
        return syncAirflowInstance(instanceId)
      })
      .then((run) => {
        if (run.sanitized_error !== null) {
          showPageAlert(run.sanitized_error, "error", "Assets")
          return
        }
        showPageAlert("Reloaded Airflow metadata", "success", "Assets")
        reloadRef.current()
      })
      .catch((err: unknown) => {
        showPageAlert(err instanceof Error ? err.message : "Could not reload Airflow metadata", "error", "Assets")
      })
      .finally(() => {
        setReloadingId((busy: string | null) => (busy === assetId ? null : busy))
      })
  }
  function toggleAvailability(assetId: string, current: AssetStatus): void {
    const next: AssetStatus = current === "ACTIVE" ? "DEPRECATED" : "ACTIVE"
    setSwitchingId(assetId)
    setPage((loaded: AssetPage | null) => replaceStatus(loaded, assetId, next))
    void updateAssetStatus(assetId, next)
      .catch((err: unknown) => {
        setPage((loaded: AssetPage | null) => replaceStatus(loaded, assetId, current))
        showPageAlert(err instanceof Error ? err.message : "Could not change status", "error", "Assets")
      })
      .finally(() => {
        setSwitchingId((busy: string | null) => (busy === assetId ? null : busy))
      })
  }
  function startBuilds(ids: readonly string[]): void {
    const pending: Set<string> = buildingIds.current
    for (const id of ids) {
      pending.add(id)
    }
    setPage((current: AssetPage | null) => withPendingBuilds(current, pending))
    for (const id of ids) {
      void requestAssetBuild(id).catch((err: unknown) => {
        pending.delete(id)
        setPage((current: AssetPage | null) => clearBuilding(current, id))
        showPageAlert(err instanceof Error ? err.message : "Build failed", "error", "Assets")
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
    <section className="catalog-page asset-board">
      <header className="catalog-hero">
        <div className="catalog-hero-top">
          <div className="catalog-hero-copy">
            <p className="catalog-kicker">Catalog</p>
            <h1>Assets</h1>
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
            <span className="catalog-count">{loading && page === null ? "Loading…" : assetCount(total)}</span>
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
              if (created.asset_id !== null) {
                openInFocusedGroup(`/assets/${created.asset_id}`)
              }
            }}
          />
        </div>
      ) : null}
      <div className="asset-board__filters">
        <input
          className="asset-board__search"
          value={queryText}
          placeholder="Search assets"
          aria-label="Search assets"
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
        <div className="asset-board__pills" role="group" aria-label="Status">
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
      <div className="asset-board__meta">
        <span>{loading && page === null ? "…" : assetCount(total)}</span>
        <label className="asset-board__sort">
          Sort by
          <select
            aria-label="Sort assets"
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
        <div className="assets-bulk">
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
      {loading && page === null ? <p className="note">Loading assets…</p> : null}
      {!loading && error === null && rows.length === 0 ? (
        <p className="note">No assets match these filters.</p>
      ) : null}
      <div className="asset-board__table" role="table" aria-label="Assets">
        <div className="asset-row asset-row--head" role="row">
          <span role="columnheader" />
          <span role="columnheader">Asset</span>
          <span role="columnheader">Refresh</span>
          <span role="columnheader">Latest build</span>
          <span role="columnheader">Updated</span>
          <span role="columnheader" />
          <span role="columnheader">Freshness</span>
        </div>
        {rows.map((row: AssetListItem) => (
          <AssetRow
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
              openInFocusedGroup(`/assets/${row.id}`)
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
              watchAssetDrag(event, { assetIds: ids, title: row.name })
            }}
          />
        ))}
      </div>
      <footer className="assets-pager">
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

function assetListQuery(input: {
  q: string
  kinds: AssetKind[]
  formats: string[]
  owner: "me" | "shared" | "all"
  status: AssetStatus | null
  sort: AssetSort | undefined
  order: "asc" | "desc" | undefined
  offset: number
}): AssetQuery {
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

function withPendingBuilds(page: AssetPage | null, pending: ReadonlySet<string>): AssetPage | null {
  if (page === null || pending.size === 0) {
    return page
  }
  const items: AssetListItem[] = page.items.map((row: AssetListItem) => {
    if (!pending.has(row.id) || row.build_running) {
      return row
    }
    return { ...row, build_running: true }
  })
  return { items, total: page.total }
}

function clearBuilding(page: AssetPage | null, assetId: string): AssetPage | null {
  if (page === null) {
    return page
  }
  const items: AssetListItem[] = page.items.map((row: AssetListItem) => {
    if (row.id !== assetId) {
      return row
    }
    return { ...row, build_running: false }
  })
  return { items, total: page.total }
}

function liveFreshness(row: AssetListItem, now: number): number {
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

function AssetRow(props: {
  row: AssetListItem
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
  const row: AssetListItem = props.row
  const score: number = liveFreshness(row, props.now)
  return (
    <div className={props.selected ? "asset-row is-selected" : "asset-row"} role="row">
      <span className="asset-row__check" role="cell">
        <input
          type="checkbox"
          checked={props.selected}
          aria-label={`Select ${row.name}`}
          onChange={(event) => {
            props.onSelected(event.target.checked)
          }}
        />
      </span>
      <span className="asset-row__identity" role="cell">
        {row.kind === "TABLE" || row.kind === "AIRFLOW_INSTANCE" ? (
          <Availability status={row.status} disabled={props.switching} onToggle={props.onToggle} />
        ) : null}
        <button
          type="button"
          className="asset-row__name"
          onPointerDown={props.onDragStart}
          onClick={props.onOpen}
        >
          {row.name}
        </button>
      </span>
      <span className="asset-row__refresh" role="cell">
        {typeof row.schedule === "string" && row.schedule !== "" ? (
          <span title={row.schedule}>{scheduleTime(row.schedule)}</span>
        ) : (
          <span>—</span>
        )}
        {typeof row.next_run_at === "string" && row.next_run_at !== "" ? (
          <small className="asset-row__when">next {formatWhen(row.next_run_at)}</small>
        ) : null}
      </span>
      <span role="cell">
        {formatWhen(row.last_built_at)}
        <StateMark row={row} />
      </span>
      <span role="cell">
        {formatWhen(row.updated_at)}
      </span>
      <span className="asset-row__actions" role="cell">
        {row.kind === "AIRFLOW_INSTANCE" ? (
          <button
            type="button"
            className="button secondary asset-row__build"
            disabled={props.reloading || row.status !== "ACTIVE"}
            title="Reload Airflow metadata"
            onClick={props.onReload}
          >
            {props.reloading ? "Reloading" : "Reload"}
          </button>
        ) : null}
        <button
          type="button"
          className="button secondary asset-row__build"
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
      className={`asset-availability asset-availability--${tone}`}
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

function replaceStatus(page: AssetPage | null, assetId: string, status: AssetStatus): AssetPage | null {
  if (page === null) {
    return null
  }
  return {
    ...page,
    items: page.items.map((item: AssetListItem) => (item.id === assetId ? { ...item, status } : item)),
  }
}

function StateMark(props: { row: AssetListItem }): ReactElement {
  if (props.row.build_running) {
    return <span className="asset-state asset-state--running" title="Building" aria-label="Building" />
  }
  if (props.row.status === "MISSING") {
    return <span className="asset-state asset-state--missing" title="Missing" aria-label="Missing" />
  }
  if (props.row.status === "DEPRECATED") {
    return <span className="asset-state asset-state--deprecated" title="Deprecated" aria-label="Deprecated" />
  }
  return <span className="asset-state asset-state--active" title="Active" aria-label="Active" />
}

function FreshnessSpark(props: { score: number }): ReactElement {
  const tone: string = props.score >= 70 ? "good" : props.score >= 30 ? "warn" : "stale"
  const bars: number[] = freshnessBars(props.score)
  return (
    <span className={`asset-spark asset-spark--${tone}`} aria-label={`Freshness ${props.score}`}>
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

function assetCount(total: number): string {
  return total === 1 ? "1 Asset" : `${total} Assets`
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

function serviceKinds(service: ServiceId): AssetKind[] {
  if (service === "airflow") {
    return ["AIRFLOW_DAG", "AIRFLOW_INSTANCE"]
  }
  if (service === "iceberg") {
    return ["TABLE"]
  }
  return []
}

function statusParam(value: string | null): AssetStatus | null {
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
  const kinds: AssetKind[] = serviceKinds(serviceParam(params.get("service")))
  const formats: string[] = params.get("service") === "iceberg" ? ["ICEBERG"] : []
  const collected: AssetListItem[] = []
  let offset: number = 0
  const status: AssetStatus | null = statusParam(params.get("status"))
  while (collected.length < 5000) {
    const page: AssetPage = await searchAssets({
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
  link.download = "assets.csv"
  link.click()
  URL.revokeObjectURL(url)
}

function csv(value: string): string {
  return `"${value.replaceAll("\"", "\"\"")}"`
}
