import { useEffect, useState, type PointerEvent as ReactPointerEvent, type ReactElement } from "react"
import { useSearchParams } from "react-router-dom"
import {
  bulkMembership,
  searchAssets,
  type AssetKind,
  type AssetListItem,
  type AssetPage,
  type AssetSort,
  type AssetStatus,
  type RefreshMode,
} from "./assetsApi"
import { watchAssetDrag } from "./editorDrag"
import { openInFocusedGroup } from "./editorLayout"
import { useEditorStatus, useEditorTitle } from "./editorSlots"
import { requestAssetBuild } from "./knowledgeBuild"
import { showPageAlert } from "./pageAlertStack"
import { fetchHierarchy, type HierarchySpace } from "./registryApi"
import { getActiveWorkspaceId } from "./workspaceStore"

const KIND_TABS: readonly { id: "all" | AssetKind; label: string }[] = [
  { id: "all", label: "Assets" },
  { id: "TABLE", label: "Tables" },
  { id: "AIRFLOW_DAG", label: "DAGs" },
  { id: "NOTEBOOK", label: "Notebooks" },
  { id: "FILE", label: "Files" },
]

const STATUSES: readonly { id: "" | AssetStatus; label: string }[] = [
  { id: "", label: "All" },
  { id: "ACTIVE", label: "Active" },
  { id: "MISSING", label: "Missing" },
  { id: "DEPRECATED", label: "Deprecated" },
]

const RANKS: readonly { id: string; label: string; sort: AssetSort; order: "asc" | "desc" }[] = [
  { id: "freshness", label: "Freshness", sort: "freshness_score", order: "desc" },
  { id: "built", label: "Latest build", sort: "last_built_at", order: "desc" },
  { id: "updated", label: "Recently updated", sort: "updated_at", order: "desc" },
  { id: "attached", label: "Most attached", sort: "membership_count", order: "desc" },
  { id: "name", label: "Name A-Z", sort: "name", order: "asc" },
  { id: "kind", label: "Kind", sort: "kind", order: "asc" },
]

export function AssetsTablePage(): ReactElement {
  const [params, setParams] = useSearchParams()
  const [page, setPage] = useState<AssetPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [hierarchy, setHierarchy] = useState<HierarchySpace[]>([])
  const queryText: string = params.get("q") ?? ""
  const owner: "me" | "shared" | "all" = ownerParam(params.get("owner"))
  const rank: string = params.get("rank") ?? "updated"
  const scopeId: string | null = params.get("scope")
  const status: AssetStatus | null = statusParam(params.get("status"))
  const offset: number = Number(params.get("offset") ?? "0")
  const kinds: AssetKind[] = params.getAll("kind").filter(isKind)
  const activeKind: "all" | AssetKind = kinds.length === 1 ? (kinds[0] ?? "all") : "all"
  const chosenRank = RANKS.find((item) => item.id === rank) ?? RANKS[2]
  useEditorTitle("Assets")
  useEditorStatus("Assets", page === null ? "Assets" : `${page.total} assets`)
  useEffect(() => {
    const controller: AbortController = new AbortController()
    setLoading(true)
    void searchAssets(
      {
        q: queryText,
        kinds,
        owner,
        workspaceId: getActiveWorkspaceId(),
        scopeId: scopeId ?? undefined,
        status: status ?? undefined,
        sort: chosenRank?.sort,
        order: chosenRank?.order,
        limit: 100,
        offset: Number.isFinite(offset) ? offset : 0,
      },
      controller.signal,
    )
      .then((result: AssetPage) => {
        setPage(result)
        setError(null)
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) {
          return
        }
        setError(err instanceof Error ? err.message : "Could not load assets")
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false)
        }
      })
    return () => {
      controller.abort()
    }
  }, [queryText, owner, rank, scopeId, status, offset, params])
  useEffect(() => {
    const controller: AbortController = new AbortController()
    void fetchHierarchy(controller.signal)
      .then((spaces: HierarchySpace[]) => {
        setHierarchy(spaces)
      })
      .catch(() => {
        setHierarchy([])
      })
    return () => {
      controller.abort()
    }
  }, [])
  const rows: AssetListItem[] = page?.items ?? []
  const total: number = page?.total ?? 0
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
  function selectKind(kind: "all" | AssetKind): void {
    const updated: URLSearchParams = new URLSearchParams(params)
    updated.delete("kind")
    if (kind !== "all") {
      updated.append("kind", kind)
    }
    updated.delete("offset")
    setParams(updated)
  }
  async function addTo(targetKind: "scope" | "space", targetId: string): Promise<void> {
    const ids: string[] = [...selected]
    const result = await bulkMembership(ids, targetKind, targetId, "attach")
    showPageAlert(
      result.unchanged > 0 && result.changed === 0 ? "Already attached" : `Attached ${result.changed}`,
      "success",
      "Assets",
    )
    patch({ tick: String(Date.now()) })
  }
  return (
    <section className="catalog-page asset-board">
      <header className="catalog-hero">
        <div className="catalog-hero-top">
          <div className="catalog-hero-copy">
            <p className="catalog-kicker">Catalog</p>
            <h1>Assets</h1>
          </div>
          <span className="catalog-count">{loading && page === null ? "Loading…" : assetCount(total)}</span>
        </div>
        <p className="catalog-lead">Tables, DAGs, notebooks, and files in this workspace.</p>
      </header>
      <div className="tabs" role="tablist" aria-label="Asset kinds">
        {KIND_TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            className={activeKind === item.id ? "tab selected" : "tab"}
            aria-selected={activeKind === item.id}
            onClick={() => {
              selectKind(item.id)
            }}
          >
            {item.label}
          </button>
        ))}
      </div>
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
        <select
          aria-label="Scope"
          value={scopeId ?? ""}
          onChange={(event) => {
            patch({ scope: event.target.value || null, offset: null })
          }}
        >
          <option value="">Any scope</option>
          {hierarchy.flatMap((space: HierarchySpace) =>
            space.scopes.map((scope) => (
              <option key={scope.id} value={scope.id}>
                {space.name} / {scope.name}
              </option>
            )),
          )}
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
          <select
            aria-label="Add selected assets to a scope"
            defaultValue=""
            onChange={(event) => {
              const value: string = event.target.value
              if (value.startsWith("scope:")) {
                void addTo("scope", value.slice(6))
              }
              event.currentTarget.value = ""
            }}
          >
            <option value="">Add to scope...</option>
            {hierarchy.flatMap((space: HierarchySpace) =>
              space.scopes.map((scope) => (
                <option key={scope.id} value={`scope:${scope.id}`}>
                  {space.name} / {scope.name}
                </option>
              )),
            )}
          </select>
          <button
            type="button"
            className="button secondary"
            onClick={() => {
              for (const id of selected) {
                void requestAssetBuild(id).catch((err: unknown) => {
                  showPageAlert(err instanceof Error ? err.message : "Build failed", "error", "Assets")
                })
              }
            }}
          >
            Build context
          </button>
          {scopeId !== null ? (
            <button
              type="button"
              className="button secondary"
              onClick={() => {
                void bulkMembership([...selected], "scope", scopeId, "detach").then(() => {
                  patch({ tick: String(Date.now()) })
                })
              }}
            >
              Remove from scope
            </button>
          ) : null}
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

function AssetRow(props: {
  row: AssetListItem
  selected: boolean
  onSelected: (on: boolean) => void
  onOpen: () => void
  onDragStart: (event: ReactPointerEvent<HTMLButtonElement>) => void
}): ReactElement {
  const row: AssetListItem = props.row
  const tags: string = row.scopes.map((scope) => scope.name).join(", ")
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
        <button
          type="button"
          className="asset-row__name"
          onPointerDown={props.onDragStart}
          onClick={props.onOpen}
        >
          {row.name}
        </button>
        <span className="asset-row__kind">{kindLabel(row.kind)}</span>
        {tags !== "" ? <span className="asset-row__tags">{tags}</span> : null}
        {row.subtitle !== null ? <small className="assets-subtitle">{row.subtitle}</small> : null}
      </span>
      <span role="cell">
        {refreshLabel(row.refresh_mode)}
      </span>
      <span role="cell">
        {formatWhen(row.last_built_at)}
        <StateMark row={row} />
      </span>
      <span role="cell">
        {formatWhen(row.updated_at)}
      </span>
      <span role="cell">
        <button
          type="button"
          className="button secondary asset-row__build"
          disabled={row.build_running}
          onClick={() => {
            void requestAssetBuild(row.id).catch((err: unknown) => {
              showPageAlert(err instanceof Error ? err.message : "Build failed", "error", "Assets")
            })
          }}
        >
          {row.build_running ? "Building" : "Build"}
        </button>
      </span>
      <span role="cell">
        <FreshnessSpark score={row.freshness_score} />
      </span>
    </div>
  )
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
  return value.slice(0, 19).replace("T", " ")
}

function refreshLabel(mode: RefreshMode): string {
  if (mode === "ON_CHANGE") {
    return "On change"
  }
  if (mode === "MANUAL") {
    return "Manual"
  }
  return "Interval"
}

function kindLabel(kind: AssetKind): string {
  if (kind === "AIRFLOW_DAG") {
    return "DAG"
  }
  if (kind === "NOTEBOOK") {
    return "Notebook"
  }
  if (kind === "FILE") {
    return "File"
  }
  return "Table"
}

function isKind(value: string): value is AssetKind {
  return value === "TABLE" || value === "AIRFLOW_DAG" || value === "NOTEBOOK" || value === "FILE"
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
  const kinds: AssetKind[] = params.getAll("kind").filter(isKind)
  const collected: AssetListItem[] = []
  let offset: number = 0
  const status: AssetStatus | null = statusParam(params.get("status"))
  while (collected.length < 5000) {
    const page: AssetPage = await searchAssets({
      q: params.get("q") ?? undefined,
      kinds,
      owner: ownerParam(params.get("owner")),
      workspaceId: getActiveWorkspaceId(),
      scopeId: params.get("scope") ?? undefined,
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
  const lines: string[] = ["kind,name,status,context,freshness,scopes"]
  for (const row of collected) {
    lines.push(
      [
        row.kind,
        csv(row.name),
        row.status,
        row.context_state,
        String(row.freshness_score),
        csv(row.scopes.map((scope) => scope.name).join(" ")),
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
