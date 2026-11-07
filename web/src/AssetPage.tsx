import { useEffect, useState, type ReactElement } from "react"
import { useParams } from "react-router-dom"
import { detailString, fetchAsset, type AssetKind, type AssetRecord } from "./assetsApi"
import { AssetRuns } from "./AssetRuns"
import { ContextEditor } from "./ContextEditor"
import { useTabParam } from "./useTabParam"
import { optionsFromFields, type ConfigOption } from "./ConfigRail"
import { useEntityConfig, type OptionField, type RefreshMode } from "./refreshSettings"
import { onAssetBuildFinished, requestAssetBuild } from "./knowledgeBuild"
import { WorkspaceFilePage } from "./WorkspaceFilePage"
import { TableAssetView, type TableAssetTab } from "./TableDetail"
import { AirflowDagView, AirflowInstanceView } from "./AirflowPages"
import { openInFocusedGroup } from "./editorLayout"
import { AgentPackPanel } from "./AgentPackPanel"

const OTHER_TABS = ["overview", "source", "runs", "agent", "config"] as const
const TABLE_TABS = [
  "overview",
  "columns",
  "relationships",
  "lineage",
  "graph",
  "source",
  "runs",
  "agent",
  "config",
] as const
type AssetTab = (typeof TABLE_TABS)[number]

const TAB_LABELS: Record<AssetTab, string> = {
  overview: "Overview",
  columns: "Columns",
  relationships: "Relationships",
  lineage: "Lineage",
  graph: "Graph",
  source: "Source",
  runs: "Runs",
  agent: "Agent",
  config: "Config",
}

const TABLE_ASSET_FIELDS: readonly OptionField[] = [
  { key: "include_graph", label: "Include graph", defaultValue: true },
  { key: "include_lineage", label: "Include lineage", defaultValue: true },
  { key: "include_data_profile", label: "Include data profile", defaultValue: false },
]

const FILE_ASSET_FIELDS: readonly OptionField[] = [
  { key: "include_outputs", label: "Include notebook outputs", defaultValue: false },
  { key: "include_table_links", label: "Link mentioned tables", defaultValue: true },
]

const AIRFLOW_ASSET_FIELDS: readonly OptionField[] = [
  { key: "include_source", label: "Include source", defaultValue: true },
  { key: "include_previous", label: "Keep previous findings", defaultValue: true },
]

const TABLE_VIEW_TABS: readonly TableAssetTab[] = [
  "overview",
  "columns",
  "relationships",
  "lineage",
  "graph",
  "source",
]

export function AssetPage(): ReactElement {
  const assetId: string = useParams().assetId ?? ""
  const [asset, setAsset] = useState<AssetRecord | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [building, setBuilding] = useState<boolean>(false)
  const fields: readonly OptionField[] = optionFieldsForKind(asset?.kind)
  const config = useEntityConfig("asset", assetId, fields, asset !== null)
  useEffect(() => {
    return onAssetBuildFinished((job) => {
      if (job.asset_id !== assetId) {
        return
      }
      setBuilding(false)
    })
  }, [assetId])
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
  const isTable: boolean = asset?.kind === "TABLE" && tableId !== null
  const allowed: readonly AssetTab[] = isTable ? TABLE_TABS : OTHER_TABS
  const [tab, selectTab] = useTabParam<AssetTab>(allowed, "overview")
  const tableTab: TableAssetTab | null = isTable && isTableViewTab(tab) ? tab : null
  const sourceKey: string | null = asset === null ? null : propertyString(asset.properties, "source_key")
  const tags: string[] = asset === null ? [] : declaredTags(asset)
  const visibility: string = asset?.owner_user_id === null ? "Shared" : "Personal"
  const instanceId: string | null = asset === null ? null : detailString(asset.detail, "instance_id")
  const dagId: string | null = asset === null ? null : detailString(asset.detail, "dag_id")
  if (asset?.kind === "AIRFLOW_INSTANCE" && instanceId !== null) {
    return (
      <AirflowInstanceView
        instanceId={instanceId}
        assetId={asset.id}
        onDeleted={() => {
          openInFocusedGroup("/assets?service=airflow")
        }}
      />
    )
  }
  if (asset?.kind === "AIRFLOW_DAG" && instanceId !== null && dagId !== null) {
    return <AirflowDagView instanceId={instanceId} dagId={dagId} onBack={null} />
  }
  return (
    <section className={tab === "graph" ? "catalog-page section-fill" : "catalog-page"}>
      <header className="catalog-hero asset-hero">
        <div className="catalog-hero-top">
          <div className="catalog-hero-copy">
            <p className="catalog-kicker">Asset</p>
            <div className="catalog-title-line">
              <h1>{asset?.name ?? "Asset"}</h1>
              <span className="asset-chip">{visibility}</span>
              {asset !== null ? (
                <span className={`asset-chip asset-status asset-status-${asset.status.toLowerCase()}`}>
                  {asset.status}
                </span>
              ) : null}
            </div>
            {sourceKey !== null ? <p className="asset-qualified">{sourceKey}</p> : null}
            {asset?.description !== null && asset?.description !== undefined && asset.description !== "" ? (
              <p className="catalog-lead">{asset.description}</p>
            ) : null}
            {tags.length > 0 ? (
              <div className="asset-tags asset-hero-tags">
                {tags.map((tag: string) => (
                  <span key={tag} className="asset-chip asset-tag">
                    {tag}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
          <div className="catalog-hero-actions">
            <button
              className="button"
              type="button"
              disabled={building || asset === null}
              onClick={() => {
                setBuilding(true)
                setError(null)
                void requestAssetBuild(assetId).catch((err: unknown) => {
                  setBuilding(false)
                  setError(err instanceof Error ? err.message : "Could not build context")
                })
              }}
            >
              {building ? "Building context…" : "Build context"}
            </button>
          </div>
        </div>
      </header>
      {error !== null ? <p className="error-text asset-alert">{error}</p> : null}
      <div className="tabs" role="tablist" aria-label="Asset sections">
        {allowed.map((name: AssetTab) => (
          <button
            key={name}
            type="button"
            className={tab === name ? "tab selected" : "tab"}
            role="tab"
            aria-selected={tab === name}
            onClick={() => {
              selectTab(name)
            }}
          >
            {TAB_LABELS[name]}
          </button>
        ))}
      </div>
      {tableTab !== null && asset !== null && tableId !== null ? (
        <TableAssetView
          asset={asset}
          tableId={tableId}
          tab={tableTab}
          onAssetChanged={() => {
            void fetchAsset(asset.id).then(setAsset)
          }}
        />
      ) : null}
      {tab === "overview" && asset !== null && !isTable ? (
        <div className="asset-sheet">
          <section className="asset-card">
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
          </section>
        </div>
      ) : null}
      {tab === "source" && asset !== null && !isTable ? (
        nodeId !== null ? (
          <WorkspaceFilePage nodeId={nodeId} />
        ) : (
          <pre>{JSON.stringify(asset.detail, null, 2)}</pre>
        )
      ) : null}
      {tab === "runs" ? <AssetRuns assetId={assetId} /> : null}
      {tab === "agent" && asset !== null ? (
        <AgentPackPanel assetId={asset.id} assetKind={asset.kind} />
      ) : null}
      {tab === "config" ? (
        <div className="asset-sheet">
          <section className="asset-card asset-config">
            <h2>Refresh</h2>
            <p className="catalog-lead">Choose when this asset rebuilds its context.</p>
            <label className="asset-field">
              <span>Mode</span>
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
            <div className="config-options">
              <h2>Context build</h2>
              {optionsFromFields(fields, config.options).map((option: ConfigOption) => (
                <label key={option.key} className="config-check">
                  <input
                    type="checkbox"
                    checked={option.checked}
                    disabled={config.busy || !config.loaded}
                    onChange={(event) => {
                      const next: Record<string, boolean> = {
                        ...config.options,
                        [option.key]: event.target.checked,
                      }
                      config.commit(
                        config.minutes,
                        config.enabled,
                        next,
                        config.model,
                        config.effort,
                        config.refreshMode,
                      )
                    }}
                  />
                  {option.label}
                </label>
              ))}
            </div>
          </section>
        </div>
      ) : null}
    </section>
  )
}

function optionFieldsForKind(kind: AssetKind | undefined): readonly OptionField[] {
  if (kind === "TABLE") {
    return TABLE_ASSET_FIELDS
  }
  if (kind === "FILE" || kind === "NOTEBOOK") {
    return FILE_ASSET_FIELDS
  }
  if (kind === "AIRFLOW_DAG" || kind === "AIRFLOW_INSTANCE") {
    return AIRFLOW_ASSET_FIELDS
  }
  return []
}

function isTableViewTab(tab: AssetTab): tab is TableAssetTab {
  const names: readonly string[] = TABLE_VIEW_TABS
  return names.includes(tab)
}

function declaredTags(asset: AssetRecord): string[] {
  if (Array.isArray(asset.tags) && asset.tags.length > 0) {
    return asset.tags.filter((tag: string) => tag !== "")
  }
  const raw: unknown = asset.properties.tags
  if (!Array.isArray(raw)) {
    return []
  }
  return raw.filter((item: unknown): item is string => typeof item === "string" && item !== "")
}

function propertyString(properties: Record<string, unknown>, key: string): string | null {
  const value: unknown = properties[key]
  if (typeof value !== "string" || value === "") {
    return null
  }
  return value
}
