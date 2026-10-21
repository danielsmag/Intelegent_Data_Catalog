import { useEffect, useState, type ReactElement } from "react"
import { searchAssets, type AssetKind, type AssetListItem } from "./assetsApi"
import { watchAssetDrag } from "./editorDrag"
import { openInFocusedGroup } from "./editorLayout"
import { getActiveWorkspaceId, useActiveWorkspaceId } from "./workspaceStore"

const KINDS: readonly AssetKind[] = ["TABLE", "AIRFLOW_DAG", "NOTEBOOK", "FILE"]

export function AssetsExplorer(): ReactElement {
  const workspaceId: string | null = useActiveWorkspaceId()
  const [query, setQuery] = useState<string>("")
  const [kinds, setKinds] = useState<AssetKind[]>([])
  const [owner, setOwner] = useState<"me" | "shared" | "all">("all")
  const [rows, setRows] = useState<AssetListItem[]>([])
  useEffect(() => {
    const controller: AbortController = new AbortController()
    const timer: number = window.setTimeout(() => {
      void searchAssets(
        {
          q: query,
          kinds,
          owner,
          workspaceId: workspaceId ?? getActiveWorkspaceId(),
          limit: 50,
        },
        controller.signal,
      )
        .then((page) => {
          setRows(page.items)
        })
        .catch(() => {
          if (!controller.signal.aborted) {
            setRows([])
          }
        })
    }, 250)
    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [kinds, owner, query, workspaceId])
  return (
    <div className="assets-explorer">
      <input
        value={query}
        placeholder="Search assets"
        onChange={(event) => {
          setQuery(event.target.value)
        }}
      />
      <div className="assets-explorer__chips">
        {KINDS.map((kind: AssetKind) => (
          <button
            key={kind}
            type="button"
            className={kinds.includes(kind) ? "filter-chip is-on" : "filter-chip"}
            onClick={() => {
              setKinds(
                kinds.includes(kind) ? kinds.filter((item: AssetKind) => item !== kind) : [...kinds, kind],
              )
            }}
          >
            {kind === "AIRFLOW_DAG" ? "DAG" : kind}
          </button>
        ))}
      </div>
      <div className="assets-explorer__owner" role="group" aria-label="Owner">
        {(["all", "me", "shared"] as const).map((value) => (
          <button key={value} type="button" className={owner === value ? "is-on" : ""} onClick={() => { setOwner(value) }}>
            {value === "all" ? "All" : value === "me" ? "Mine" : "Shared"}
          </button>
        ))}
      </div>
      <ul>
        {rows.map((row: AssetListItem) => (
          <li key={row.id}>
            <button
              type="button"
              onPointerDown={(event) => {
                watchAssetDrag(event, { assetIds: [row.id], title: row.name })
              }}
              onClick={() => {
                openInFocusedGroup(`/assets/${row.id}`)
              }}
            >
              <span className={`freshness-dot freshness-dot--${freshnessTone(row.freshness_score)}`} />
              {row.name}
              <small>{row.scopes.map((scope) => scope.name).join(", ")}</small>
            </button>
          </li>
        ))}
      </ul>
      <button
        type="button"
        className="assets-explorer__open"
        onClick={() => {
          openInFocusedGroup("/workspace/assets")
        }}
      >
        Open assets table
      </button>
    </div>
  )
}

function freshnessTone(score: number): string {
  if (score >= 70) {
    return "good"
  }
  if (score >= 30) {
    return "warn"
  }
  return "stale"
}
