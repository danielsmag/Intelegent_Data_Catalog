import { useEffect, useMemo, useState, type PointerEvent as ReactPointerEvent, type ReactElement } from "react"
import { useSearchParams } from "react-router-dom"
import {
  bulkMembership,
  searchAssets,
  type AssetKind,
  type AssetListItem,
  type AssetPage,
  type AssetSort,
} from "./assetsApi"
import { DataGrid, type DataGridColumn } from "./DataGrid"
import { watchAssetDrag } from "./editorDrag"
import { openInFocusedGroup } from "./editorLayout"
import { fetchHierarchy, type HierarchySpace } from "./registryApi"
import { requestAssetBuild } from "./knowledgeBuild"
import { showPageAlert } from "./pageAlertStack"
import { getActiveWorkspaceId } from "./workspaceStore"

const KINDS: readonly AssetKind[] = ["TABLE", "AIRFLOW_DAG", "NOTEBOOK", "FILE"]
const RANKS: readonly { id: string; label: string; sort: AssetSort; order: "asc" | "desc" }[] = [
  { id: "freshness", label: "Freshness", sort: "freshness_score", order: "desc" },
  { id: "built", label: "Recently built", sort: "last_built_at", order: "desc" },
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
  const [density, setDensity] = useState<"compact" | "comfortable">("compact")
  const queryText: string = params.get("q") ?? ""
  const owner: "me" | "shared" | "all" = ownerParam(params.get("owner"))
  const rank: string = params.get("rank") ?? "updated"
  const scopeId: string | null = params.get("scope")
  const offset: number = Number(params.get("offset") ?? "0")
  const kinds: AssetKind[] = params.getAll("kind").filter(isKind)
  const chosenRank = RANKS.find((item) => item.id === rank) ?? RANKS[2]
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
  }, [queryText, owner, rank, scopeId, offset, params])
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
  const columns: DataGridColumn<AssetListItem>[] = useMemo(() => assetColumns(), [])
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
    <section className="page-grid assets-table-page">
      <header className="page-intro">
        <p className="eyebrow">Workspace</p>
        <h1>Assets</h1>
      </header>
      <div className="assets-toolbar">
        <input
          value={queryText}
          placeholder="Search name, description, context"
          onChange={(event) => {
            patch({ q: event.target.value, offset: null })
          }}
        />
        <select
          className="rank-select"
          value={rank}
          onChange={(event) => {
            patch({ rank: event.target.value, offset: null })
          }}
        >
          {RANKS.map((item) => (
            <option key={item.id} value={item.id}>
              Rank by {item.label}
            </option>
          ))}
        </select>
        <select
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
        {KINDS.map((kind: AssetKind) => (
          <button
            key={kind}
            type="button"
            className={kinds.includes(kind) ? "filter-chip is-on" : "filter-chip"}
            onClick={() => {
              const next: AssetKind[] = kinds.includes(kind)
                ? kinds.filter((item: AssetKind) => item !== kind)
                : [...kinds, kind]
              const updated: URLSearchParams = new URLSearchParams(params)
              updated.delete("kind")
              for (const item of next) {
                updated.append("kind", item)
              }
              updated.delete("offset")
              setParams(updated)
            }}
          >
            {kindLabel(kind)}
          </button>
        ))}
        <button
          type="button"
          onClick={() => {
            setDensity(density === "compact" ? "comfortable" : "compact")
          }}
        >
          {density === "compact" ? "Comfortable" : "Compact"}
        </button>
        <button
          type="button"
          onClick={() => {
            void exportCsv(params)
          }}
        >
          Export CSV
        </button>
      </div>
      {error !== null ? <p className="error-text">{error}</p> : null}
      {selected.size > 0 ? (
        <div className="assets-bulk">
          <span>{selected.size} selected</span>
          <select
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
      <DataGrid
        rows={rows}
        columns={columns}
        rowKey={(row: AssetListItem) => row.id}
        selected={selected}
        onSelectedChange={setSelected}
        loading={loading}
        density={density}
        storageKey="assets-table"
        empty="No assets match these filters."
        onOpen={(row: AssetListItem) => {
          openInFocusedGroup(`/assets/${row.id}`)
        }}
        onRowPointerDown={(event: ReactPointerEvent<HTMLDivElement>, row: AssetListItem) => {
          const ids: string[] = selected.has(row.id) && selected.size > 1 ? [...selected] : [row.id]
          watchAssetDrag(event, { assetIds: ids, title: row.name })
        }}
      />
      <footer className="assets-pager">
        <span>
          {rows.length === 0 ? 0 : offset + 1}-{offset + rows.length} of {page?.total ?? 0}
        </span>
        <button
          type="button"
          disabled={offset <= 0}
          onClick={() => {
            patch({ offset: String(Math.max(0, offset - 100)) })
          }}
        >
          Previous
        </button>
        <button
          type="button"
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

function assetColumns(): DataGridColumn<AssetListItem>[] {
  return [
    { id: "kind", title: "Kind", width: 120, render: (row) => kindLabel(row.kind) },
    {
      id: "name",
      title: "Name",
      width: 240,
      render: (row) => (
        <span>
          {row.name}
          {row.subtitle !== null ? <small className="assets-subtitle">{row.subtitle}</small> : null}
          {row.build_running ? <span className="assets-spinner" aria-label="Building" /> : null}
        </span>
      ),
    },
    {
      id: "owner",
      title: "Owner",
      width: 100,
      render: (row) => (row.owner_user_id === null ? "Shared" : "Mine"),
    },
    {
      id: "scopes",
      title: "Scopes",
      width: 180,
      render: (row) => row.scopes.map((scope) => scope.name).join(", ") || "—",
    },
    {
      id: "context",
      title: "Context",
      width: 110,
      render: (row) => <span className={`context-pill context-pill--${row.context_state}`}>{row.context_state}</span>,
    },
    {
      id: "freshness",
      title: "Freshness",
      width: 120,
      render: (row) => (
        <span className="freshness-bar" style={{ ["--score" as string]: `${row.freshness_score}%` }}>
          {row.freshness_score}
        </span>
      ),
    },
    {
      id: "built",
      title: "Built",
      width: 140,
      render: (row) => (row.last_built_at === null ? "Never" : row.last_built_at.slice(0, 16).replace("T", " ")),
    },
    { id: "status", title: "Status", width: 110, render: (row) => row.status },
    { id: "mode", title: "Refresh", width: 120, render: (row) => row.refresh_mode },
  ]
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
  while (collected.length < 5000) {
    const page: AssetPage = await searchAssets({
      q: params.get("q") ?? undefined,
      kinds,
      owner: ownerParam(params.get("owner")),
      workspaceId: getActiveWorkspaceId(),
      scopeId: params.get("scope") ?? undefined,
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
      [row.kind, csv(row.name), row.status, row.context_state, String(row.freshness_score), csv(row.scopes.map((scope) => scope.name).join(" "))].join(","),
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
