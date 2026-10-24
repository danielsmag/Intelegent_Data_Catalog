import { useEffect, useState, type ReactElement } from "react"
import { ContextEditor } from "./ContextEditor"
import { KnowledgeContextPanel } from "./KnowledgeContextPanel"
import { SubgraphView } from "./SubgraphView"
import { errorMessage, isAbortError } from "./api"
import { type AssetRecord } from "./assetsApi"
import { apiFetch, readDetail } from "./http"
import { onAssetBuildFinished, onContextGenerationFinished, requestContextGeneration } from "./knowledgeBuild"
import {
  fetchBindings,
  fetchCatalogColumns,
  fetchCatalogTable,
  fetchKnowledgeGraph,
  fetchLineage,
  fetchRelationshipGraph,
  fetchRelationships,
  fetchSparkQuery,
  fetchTableTags,
  type AssetBindingRecord,
  type CatalogColumnRecord,
  type CatalogTableRecord,
  type GraphViewRecord,
  type KnowledgeContext,
  type SparkQueryAvailability,
  type TableRelationships,
  type TagRecord,
} from "./registryApi"
import { setPendingQuerySql, setSidebarView } from "./sidebarView"

const EMPTY_GRAPH: GraphViewRecord = { nodes: [], edges: [], truncated: false }

export type TableAssetTab = "overview" | "columns" | "relationships" | "lineage" | "graph" | "source"

export function TableAssetView(props: {
  asset: AssetRecord
  tableId: string
  tab: TableAssetTab
  onAssetChanged: () => void
}): ReactElement {
  const [table, setTable] = useState<CatalogTableRecord | null>(null)
  const [columns, setColumns] = useState<CatalogColumnRecord[]>([])
  const [relationships, setRelationships] = useState<TableRelationships | null>(null)
  const [spark, setSpark] = useState<SparkQueryAvailability | null>(null)
  const [bindings, setBindings] = useState<AssetBindingRecord[]>([])
  const [tags, setTags] = useState<TagRecord[]>([])
  const [lineage, setLineage] = useState<GraphViewRecord | null>(null)
  const [relationGraph, setRelationGraph] = useState<GraphViewRecord | null>(null)
  const [knowledgeGraph, setKnowledgeGraph] = useState<GraphViewRecord | null>(null)
  const [graphError, setGraphError] = useState<string | null>(null)
  const [knowledge, setKnowledge] = useState<KnowledgeContext | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [columnsBuilding, setColumnsBuilding] = useState<boolean>(false)
  const [reloadToken, setReloadToken] = useState<number>(0)
  useEffect(() => {
    return onAssetBuildFinished((job) => {
      if (job.asset_id !== props.asset.id || job.status !== "succeeded") {
        return
      }
      setReloadToken((current: number) => current + 1)
    })
  }, [props.asset.id])
  useEffect(() => {
    return onContextGenerationFinished((job) => {
      if (job.subject_kind !== "table_columns" || job.subject_id !== props.tableId) {
        return
      }
      setColumnsBuilding(false)
      if (job.status === "succeeded") {
        setReloadToken((current: number) => current + 1)
      }
    })
  }, [props.tableId])
  useEffect(() => {
    const controller: AbortController = new AbortController()
    setError(null)
    void Promise.all([
      fetchCatalogTable(props.tableId, controller.signal),
      fetchCatalogColumns(props.tableId, controller.signal),
      fetchRelationships(props.tableId, controller.signal),
      fetchSparkQuery(props.tableId, controller.signal),
      fetchBindings(props.tableId, controller.signal),
      fetchTableTags(props.tableId, controller.signal),
      fetchAssetTableSnapshot(props.asset.id, controller.signal),
    ])
      .then((loaded) => {
        setTable(loaded[0])
        setColumns(loaded[1])
        setRelationships(loaded[2])
        setSpark(loaded[3])
        setBindings(loaded[4])
        setTags(loaded[5])
        setKnowledge(loaded[6])
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setError(errorMessage(err))
        }
      })
    void fetchLineage(props.tableId, controller.signal)
      .then((loaded: GraphViewRecord) => {
        setLineage(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setLineage(EMPTY_GRAPH)
        }
      })
    void fetchRelationshipGraph(props.tableId, controller.signal)
      .then((loaded: GraphViewRecord) => {
        setRelationGraph(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setRelationGraph(EMPTY_GRAPH)
        }
      })
    void fetchKnowledgeGraph(props.tableId, controller.signal)
      .then((loaded: GraphViewRecord) => {
        setKnowledgeGraph(loaded)
        setGraphError(null)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setKnowledgeGraph(EMPTY_GRAPH)
          setGraphError(errorMessage(err))
        }
      })
    return () => {
      controller.abort()
    }
  }, [props.asset.id, props.tableId, reloadToken])
  const qualified: string = table?.source_key ?? props.asset.name
  return (
    <div>
      {error !== null ? <p className="error-text">{error}</p> : null}
      {props.tab === "overview" ? (
        <div>
          <ContextEditor
            kind="asset"
            id={props.asset.id}
            name={props.asset.name}
            context={props.asset.context}
            origin={props.asset.context_origin}
            onChanged={props.onAssetChanged}
          />
          <button
            className="button secondary"
            type="button"
            disabled={columnsBuilding}
            onClick={() => {
              setColumnsBuilding(true)
              setError(null)
              void requestContextGeneration("table_columns", props.tableId).catch((err: unknown) => {
                setColumnsBuilding(false)
                setError(errorMessage(err))
              })
            }}
          >
            {columnsBuilding ? "Generating column contexts…" : "Generate all column contexts"}
          </button>
          {knowledge !== null ? <KnowledgeContextPanel context={knowledge} /> : (
            <p className="note">Build context to write a snapshot.</p>
          )}
          {table !== null ? (
            <>
              <p>{qualified}</p>
              <p>{table.description ?? table.source_description ?? "No description"}</p>
              <p>
                Tags: {tags.length === 0 ? "None" : tags.map((tag: TagRecord) => tag.name).join(", ")}
              </p>
              <p className="comment">Last synced {table.last_synced_at ?? "never"}</p>
            </>
          ) : null}
          <ul>
            {props.asset.memberships.map((membership) => (
              <li key={membership.id}>{membership.scope_id ?? membership.space_id}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {props.tab === "columns" ? (
        <ColumnTable
          columns={columns}
          onContextChanged={() => {
            setReloadToken((current: number) => current + 1)
          }}
        />
      ) : null}
      {props.tab === "relationships" && relationships !== null ? (
        <RelationshipLists relationships={relationships} graph={relationGraph} />
      ) : null}
      {props.tab === "lineage" ? <GraphList graph={lineage} empty="No lineage recorded." /> : null}
      {props.tab === "graph" ? (
        <SubgraphView graph={knowledgeGraph} error={graphError} focusTableId={props.tableId} />
      ) : null}
      {props.tab === "source" && table !== null ? (
        <SourceTab table={table} bindings={bindings} spark={spark} />
      ) : null}
    </div>
  )
}

export function ColumnTable(props: {
  columns: CatalogColumnRecord[]
  onContextChanged: () => void
}): ReactElement {
  if (props.columns.length === 0) {
    return <p className="note">No columns.</p>
  }
  return (
    <table className="columns">
      <thead>
        <tr>
          <th>Column</th>
          <th>Type</th>
          <th>Nullable</th>
          <th>PK</th>
          <th>Context</th>
        </tr>
      </thead>
      <tbody>
        {props.columns.map((column: CatalogColumnRecord) => (
          <tr key={column.id}>
            <td>{column.name}</td>
            <td className="type">{column.data_type ?? column.discovered_data_type ?? "Unknown"}</td>
            <td>{column.nullable === null ? "Unknown" : column.nullable ? "Yes" : "No"}</td>
            <td>{column.is_primary_key === null ? "Unknown" : column.is_primary_key ? "Yes" : "No"}</td>
            <td>
              <ContextEditor
                kind="column"
                id={column.id}
                name={column.name}
                context={column.context}
                origin={column.context_origin}
                onChanged={props.onContextChanged}
                compact
              />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function RelationshipLists(props: {
  relationships: TableRelationships
  graph: GraphViewRecord | null
}): ReactElement {
  return (
    <div>
      <h2>Outgoing</h2>
      <RelationshipItems items={props.relationships.outgoing} empty="No outgoing relationships." />
      <h2>Incoming</h2>
      <RelationshipItems items={props.relationships.incoming} empty="No incoming relationships." />
      <GraphList graph={props.graph} empty="No relationship graph." />
    </div>
  )
}

function RelationshipItems(props: {
  items: TableRelationships["outgoing"]
  empty: string
}): ReactElement {
  if (props.items.length === 0) {
    return <p className="note">{props.empty}</p>
  }
  return (
    <ul className="registry-list">
      {props.items.map((item) => (
        <li key={item.id}>
          <span className="badge">{item.origin}</span>
          <span className="badge">{item.status}</span>
          {item.column_mappings
            .map((mapping) => `${mapping.source_column_name} → ${mapping.target_column_name}`)
            .join(", ")}
        </li>
      ))}
    </ul>
  )
}

export function GraphList(props: { graph: GraphViewRecord | null; empty: string }): ReactElement {
  if (props.graph === null || props.graph.edges.length === 0) {
    return <p className="note">{props.empty}</p>
  }
  const labels: Map<string, string> = new Map(
    props.graph.nodes.map((node) => [node.id, node.resolved ? node.label : `${node.label} (unresolved)`]),
  )
  return (
    <ul className="registry-list">
      {props.graph.edges.map((edge, index: number) => (
        <li key={`${edge.source}-${edge.target}-${edge.kind}-${index}`} className={`edge edge-${edge.style}`}>
          <span>{labels.get(edge.source) ?? edge.source}</span>
          <span className="comment">{edge.style}</span>
          <span>{labels.get(edge.target) ?? edge.target}</span>
        </li>
      ))}
      {props.graph.truncated ? <li className="comment">Graph truncated.</li> : null}
    </ul>
  )
}

export function SourceTab(props: {
  table: CatalogTableRecord
  bindings: AssetBindingRecord[]
  spark: SparkQueryAvailability | null
}): ReactElement {
  const discovery: AssetBindingRecord | undefined = props.bindings.find(
    (binding: AssetBindingRecord) => binding.role === "DISCOVERY",
  )
  const query: AssetBindingRecord | undefined = props.bindings.find(
    (binding: AssetBindingRecord) => binding.role === "QUERY" && binding.is_selected,
  )
  const sql: string | null = props.spark?.sql ?? null
  return (
    <div>
      <p>{`Physical ${props.table.physical_catalog}.${props.table.physical_namespace}.${props.table.physical_table}`}</p>
      <p>Format {props.table.table_format ?? "Unknown"}</p>
      <p>Discovery {discovery?.engine_ref_key ?? "Not configured"}</p>
      <p>Spark {query?.engine_ref_key ?? "Not configured for Spark queries"}</p>
      <p className="comment">Freshness {props.table.last_synced_at ?? "never synced"}</p>
      {props.spark?.available && sql !== null ? (
        <button
          type="button"
          className="button"
          onClick={() => {
            setSidebarView("query")
            setPendingQuerySql(sql)
          }}
        >
          Query in Spark
        </button>
      ) : (
        <p>{props.spark?.message ?? "Not configured for Spark queries"}</p>
      )}
    </div>
  )
}

async function fetchAssetTableSnapshot(assetId: string, signal: AbortSignal): Promise<KnowledgeContext | null> {
  const response: Response = await apiFetch(`/api/v1/assets/${assetId}/knowledge-context`, { signal })
  if (response.status === 404) {
    return null
  }
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  const body: unknown = await response.json()
  if (typeof body !== "object" || body === null || !("payload" in body)) {
    return null
  }
  return tableSnapshot(body.payload)
}

function tableSnapshot(value: unknown): KnowledgeContext | null {
  if (typeof value !== "object" || value === null || !("table" in value) || !("columns" in value)) {
    return null
  }
  const columns: unknown = value.columns
  const table: unknown = value.table
  if (typeof table !== "object" || table === null || !Array.isArray(columns)) {
    return null
  }
  return value as KnowledgeContext
}
