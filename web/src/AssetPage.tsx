import { useEffect, useState, type ReactElement } from "react"
import { useParams } from "react-router-dom"
import { detailString, fetchAsset, type AssetRecord } from "./assetsApi"
import { AssetRuns } from "./AssetRuns"
import { ContextEditor } from "./ContextEditor"
import { useTabParam } from "./useTabParam"
import { useEntityConfig, type RefreshMode } from "./refreshSettings"
import { requestAssetBuild } from "./knowledgeBuild"
import { WorkspaceFilePage } from "./WorkspaceFilePage"
import { WorkbenchLink } from "./editorSlots"

const TABS = ["overview", "source", "runs", "config"] as const
type AssetTab = (typeof TABS)[number]

const OPTION_FIELDS = [
  { key: "include_graph", label: "Include graph", defaultValue: true },
  { key: "include_lineage", label: "Include lineage", defaultValue: true },
  { key: "include_data_profile", label: "Include data profile", defaultValue: false },
  { key: "include_source", label: "Include source", defaultValue: true },
  { key: "include_previous", label: "Keep previous findings", defaultValue: true },
  { key: "include_outputs", label: "Include notebook outputs", defaultValue: false },
  { key: "include_table_links", label: "Link mentioned tables", defaultValue: true },
]

export function AssetPage(): ReactElement {
  const assetId: string = useParams().assetId ?? ""
  const [tab, selectTab] = useTabParam<AssetTab>(TABS, "overview")
  const [asset, setAsset] = useState<AssetRecord | null>(null)
  const [error, setError] = useState<string | null>(null)
  const config = useEntityConfig("asset", assetId, OPTION_FIELDS)
  useEffect(() => {
    const controller: AbortController = new AbortController()
    void fetchAsset(assetId, controller.signal)
      .then((row: AssetRecord) => {
        setAsset(row)
        setError(null)
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "Could not load asset")
        }
      })
    return () => {
      controller.abort()
    }
  }, [assetId])
  const nodeId: string | null = asset === null ? null : detailString(asset.detail, "node_id")
  const tableId: string | null = asset === null ? null : detailString(asset.detail, "table_id")
  return (
    <section className="page-grid">
      <header className="page-intro">
        <p className="eyebrow">{asset?.kind ?? "Asset"}</p>
        <h1>{asset?.name ?? "Asset"}</h1>
        <p className="note">{asset?.owner_user_id === null ? "Shared" : "Personal"}</p>
      </header>
      {error !== null ? <p className="error-text">{error}</p> : null}
      <div className="tab-row">
        {TABS.map((name: AssetTab) => (
          <button key={name} type="button" className={tab === name ? "is-on" : ""} onClick={() => { selectTab(name) }}>
            {name}
          </button>
        ))}
        <button
          type="button"
          onClick={() => {
            void requestAssetBuild(assetId)
          }}
        >
          Build context
        </button>
      </div>
      {tab === "overview" && asset !== null ? (
        <>
          <ContextEditor
            kind="asset"
            id={asset.id}
            name={asset.name}
            context={asset.context}
            origin={asset.context_origin}
            onChanged={() => {
              void fetchAsset(asset.id).then(setAsset)
            }}
          />
          <ul>
            {asset.memberships.map((membership) => (
              <li key={membership.id}>{membership.scope_id ?? membership.space_id}</li>
            ))}
          </ul>
        </>
      ) : null}
      {tab === "source" && asset !== null ? (
        nodeId !== null ? (
          <WorkspaceFilePage nodeId={nodeId} />
        ) : tableId !== null ? (
          <p>
            <WorkbenchLink to={`/registry/tables/${tableId}`}>Open table detail</WorkbenchLink>
          </p>
        ) : (
          <pre>{JSON.stringify(asset.detail, null, 2)}</pre>
        )
      ) : null}
      {tab === "runs" ? <AssetRuns assetId={assetId} /> : null}
      {tab === "config" ? (
        <div>
          <label>
            Refresh
            <select
              value={config.refreshMode}
              onChange={(event) => {
                const mode: RefreshMode = event.target.value === "ON_CHANGE" || event.target.value === "MANUAL"
                  ? event.target.value
                  : "INTERVAL"
                config.commit(config.minutes, config.enabled, config.options, config.model, config.effort, mode)
              }}
            >
              <option value="INTERVAL">Interval</option>
              <option value="ON_CHANGE">On change</option>
              <option value="MANUAL">Manual</option>
            </select>
          </label>
        </div>
      ) : null}
    </section>
  )
}
