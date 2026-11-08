import { useEffect, useState, type ReactElement } from "react"
import { useSearchParams } from "react-router-dom"
import { isAbortError } from "./api"
import {
  fetchAssets,
  runAsset,
  type AssetKind,
  type AssetPage,
  type AssetRecord,
  type AssetRunStatus,
  type AssetSort,
} from "./assetsApi"
import { openInFocusedGroup } from "./editorLayout"
import { useEditorStatus, useEditorTitle } from "./editorSlots"
import { showPageAlert } from "./pageAlertStack"

const PAGE_SIZE: number = 100
const LIST_POLL_MS: number = 5000

const KINDS: readonly { id: "" | AssetKind; label: string }[] = [
  { id: "", label: "All kinds" },
  { id: "AIRFLOW_INSTANCE", label: "Airflow" },
  { id: "ICEBERG_TABLE", label: "Iceberg table" },
  { id: "ICEBERG_SCHEMA", label: "Iceberg schema" },
  { id: "ICEBERG_CATALOG", label: "Iceberg catalog" },
  { id: "GITHUB_REPO", label: "GitHub" },
]

const RANKS: readonly { id: AssetSort; label: string }[] = [
  { id: "name", label: "Name A-Z" },
  { id: "kind", label: "Kind" },
  { id: "updated", label: "Recently updated" },
]

export function AssetsTablePage(): ReactElement {
  const [params, setParams] = useSearchParams()
  const [page, setPage] = useState<AssetPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [runningId, setRunningId] = useState<string | null>(null)
  const queryText: string = params.get("q") ?? ""
  const kind: AssetKind | null = kindParam(params.get("kind"))
  const rank: AssetSort = sortParam(params.get("rank"))
  const offset: number = Number(params.get("offset") ?? "0")
  useEditorTitle("Assets")
  useEditorStatus("Assets", page === null ? "Assets" : assetCount(page.total))
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
      void fetchAssets(
        {
          q: queryText,
          kind: kind ?? undefined,
          sort: rank,
          limit: PAGE_SIZE,
          offset: Number.isFinite(offset) ? offset : 0,
        },
        controller.signal,
      )
        .then((result: AssetPage) => {
          if (stopped || controller.signal.aborted) {
            return
          }
          setPage(result)
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
    load(true)
    const timer: number = window.setInterval(() => {
      load(false)
    }, LIST_POLL_MS)
    return () => {
      stopped = true
      inflight?.abort()
      window.clearInterval(timer)
    }
  }, [queryText, kind, rank, offset])
  const rows: AssetRecord[] = page?.items ?? []
  const total: number = page?.total ?? 0
  function patch(next: Record<string, string | null>): void {
    const updated: URLSearchParams = new URLSearchParams(params)
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === "") {
        updated.delete(key)
      } else {
        updated.set(key, value)
      }
    }
    if (next.offset === undefined) {
      updated.delete("offset")
    }
    setParams(updated)
  }
  function trigger(assetId: string): void {
    setRunningId(assetId)
    setPage((current: AssetPage | null) => replaceRunStatus(current, assetId, "queued"))
    void runAsset(assetId, false)
      .catch((err: unknown) => {
        setPage((current: AssetPage | null) => replaceRunStatus(current, assetId, null))
        showPageAlert(err instanceof Error ? err.message : "Could not run asset", "error", "Assets")
      })
      .finally(() => {
        setRunningId((busy: string | null) => (busy === assetId ? null : busy))
      })
  }
  return (
    <section className="catalog-page agent-board">
      <header className="catalog-hero">
        <div className="catalog-hero-top">
          <div className="catalog-hero-copy">
            <p className="catalog-kicker">Catalog</p>
            <h1>Assets</h1>
          </div>
          <div className="catalog-hero-actions">
            <span className="catalog-count">{loading && page === null ? "Loading…" : assetCount(total)}</span>
          </div>
        </div>
        <p className="catalog-lead">Deterministic operators. Schedules and Run write markdown.</p>
      </header>
      <div className="agent-board__filters">
        <input
          className="agent-board__search"
          value={queryText}
          placeholder="Search assets"
          aria-label="Search assets"
          onChange={(event) => {
            patch({ q: event.target.value })
          }}
        />
        <select
          aria-label="Asset kind"
          value={kind ?? ""}
          onChange={(event) => {
            patch({ kind: event.target.value })
          }}
        >
          {KINDS.map((item) => (
            <option key={item.id === "" ? "all" : item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
      </div>
      <div className="agent-board__meta">
        <span>{loading && page === null ? "…" : assetCount(total)}</span>
        <label className="agent-board__sort">
          Sort by
          <select
            aria-label="Sort assets"
            value={rank}
            onChange={(event) => {
              patch({ rank: event.target.value })
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
      {loading && page === null ? <p className="note">Loading assets…</p> : null}
      {!loading && error === null && rows.length === 0 ? <p className="note">No assets match these filters.</p> : null}
      <div className="agent-board__table" role="table" aria-label="Assets">
        <div className="agent-row agent-row--head agent-row--assets" role="row">
          <span role="columnheader">Asset</span>
          <span role="columnheader">Kind</span>
          <span role="columnheader">Schedule</span>
          <span role="columnheader">Last run</span>
          <span role="columnheader">Updated</span>
          <span role="columnheader" />
        </div>
        {rows.map((row: AssetRecord) => (
          <AssetRow
            key={row.id}
            row={row}
            running={runningId === row.id || row.run_status === "queued" || row.run_status === "running"}
            onOpen={() => {
              openInFocusedGroup(`/assets/${row.id}`)
            }}
            onRun={() => {
              trigger(row.id)
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
            patch({ offset: String(Math.max(0, offset - PAGE_SIZE)) })
          }}
        >
          Previous
        </button>
        <button
          type="button"
          className="button secondary"
          disabled={page === null || offset + rows.length >= page.total}
          onClick={() => {
            patch({ offset: String(offset + PAGE_SIZE) })
          }}
        >
          Next
        </button>
      </footer>
    </section>
  )
}

function AssetRow(props: {
  row: AssetRecord
  running: boolean
  onOpen: () => void
  onRun: () => void
}): ReactElement {
  const row: AssetRecord = props.row
  const active: boolean = row.status === "ACTIVE"
  return (
    <div className="agent-row agent-row--assets" role="row">
      <span className="agent-row__identity" role="cell">
        <span className={active ? "agent-availability agent-availability--active" : "agent-availability agent-availability--disabled"}>
          {active ? "Active" : "Missing"}
        </span>
        <button type="button" className="agent-row__name" onClick={props.onOpen}>
          {row.name}
        </button>
      </span>
      <span role="cell">{kindLabel(row.kind)}</span>
      <span className="agent-row__refresh" role="cell">
        {row.schedule ?? "Once"}
      </span>
      <span role="cell">
        {formatWhen(row.ran_at)}
        <RunMark status={row.run_status} running={props.running} />
      </span>
      <span role="cell">{formatWhen(row.updated_at)}</span>
      <span className="agent-row__actions" role="cell">
        <button
          type="button"
          className="button secondary agent-row__build"
          disabled={props.running}
          onClick={props.onRun}
        >
          {props.running ? "Running" : "Run"}
        </button>
      </span>
    </div>
  )
}

function RunMark(props: { status: AssetRunStatus | null; running: boolean }): ReactElement {
  if (props.running || props.status === "queued" || props.status === "running") {
    return <span className="agent-state agent-state--running" title="Running" aria-label="Running" />
  }
  if (props.status === "failed") {
    return <span className="agent-state agent-state--missing" title="Failed" aria-label="Failed" />
  }
  if (props.status === "succeeded") {
    return <span className="agent-state agent-state--active" title="Succeeded" aria-label="Succeeded" />
  }
  return <span className="agent-state agent-state--deprecated" title="No run" aria-label="No run" />
}

function replaceRunStatus(page: AssetPage | null, assetId: string, status: AssetRunStatus | null): AssetPage | null {
  if (page === null) {
    return null
  }
  return {
    ...page,
    items: page.items.map((item: AssetRecord) => (item.id === assetId ? { ...item, run_status: status } : item)),
  }
}

function assetCount(total: number): string {
  return total === 1 ? "1 Asset" : `${total} Assets`
}

function kindLabel(kind: AssetKind): string {
  const match: { id: "" | AssetKind; label: string } | undefined = KINDS.find((item) => item.id === kind)
  return match?.label ?? kind
}

function sortParam(value: string | null): AssetSort {
  if (value === "kind" || value === "updated") {
    return value
  }
  return "name"
}

function kindParam(value: string | null): AssetKind | null {
  if (
    value === "AIRFLOW_INSTANCE" ||
    value === "ICEBERG_TABLE" ||
    value === "ICEBERG_SCHEMA" ||
    value === "ICEBERG_CATALOG" ||
    value === "GITHUB_REPO"
  ) {
    return value
  }
  return null
}

function formatWhen(value: string | null): string {
  if (value === null || value === "") {
    return "—"
  }
  const parsed: Date = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value.slice(0, 19).replace("T", " ")
  }
  const year: string = String(parsed.getFullYear())
  const month: string = String(parsed.getMonth() + 1).padStart(2, "0")
  const day: string = String(parsed.getDate()).padStart(2, "0")
  const hour: string = String(parsed.getHours()).padStart(2, "0")
  const minute: string = String(parsed.getMinutes()).padStart(2, "0")
  const second: string = String(parsed.getSeconds()).padStart(2, "0")
  return `${year}-${month}-${day} ${hour}:${minute}:${second}`
}
