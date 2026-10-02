import { useEffect, useState, type FormEvent, type ReactElement } from "react"
import { Link, useParams } from "react-router-dom"
import { ActivityBar } from "./ActivityBar"
import { HierarchyTree } from "./HierarchyTree"
import { setSpacesSidebarOpen, useSpacesSidebarOpen } from "./spacesSidebar"
import { errorMessage, isAbortError } from "./api"
import {
  fetchBindings,
  fetchCatalogColumns,
  fetchCatalogTable,
  fetchContextGraph,
  fetchDataSources,
  fetchLineage,
  fetchRelationshipGraph,
  fetchRelationships,
  fetchScope,
  fetchScopeTables,
  fetchScopes,
  fetchSpace,
  fetchSpaces,
  fetchSparkQuery,
  fetchTableTags,
  searchCatalog,
  type AssetBindingRecord,
  type CatalogColumnRecord,
  type CatalogTableRecord,
  type DataSourceRecord,
  type GraphViewRecord,
  type ScopeRecord,
  type SearchHitRecord,
  type SpaceRecord,
  type SparkQueryAvailability,
  type TableRelationships,
  type TagRecord,
} from "./registryApi"

type TabName = "overview" | "columns" | "relationships" | "lineage" | "graph" | "source"

function Shell(props: { status: string; tree?: boolean; children: ReactElement }): ReactElement {
  const sidebarOpen: boolean = useSpacesSidebarOpen()
  return (
    <div className="shell">
      <div className="workspace">
        <ActivityBar />
        {props.tree ? (
          <HierarchyTree
            title="Spaces"
            collapsed={!sidebarOpen}
            onToggleCollapsed={() => {
              setSpacesSidebarOpen(!sidebarOpen)
            }}
          />
        ) : null}
        <main className="main">
          <div className="main-scroll">{props.children}</div>
        </main>
      </div>
      <footer className="status">
        <span>Catalog</span>
        <span>{props.status}</span>
      </footer>
    </div>
  )
}

export function SpacesPage(): ReactElement {
  const [spaces, setSpaces] = useState<SpaceRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useLoad(setSpaces, setError, fetchSpaces)
  return (
    <Shell status="Spaces" tree>
      <section>
        <h1>Spaces</h1>
        {error !== null ? <p className="error-text">{error}</p> : null}
        {spaces !== null && spaces.length === 0 ? <p className="note">No spaces yet.</p> : null}
        <ul className="registry-list">
          {(spaces ?? []).map((space: SpaceRecord) => (
            <li key={space.id}>
              <Link to={`/spaces/${space.id}`}>{space.name}</Link>
              {space.description !== null ? <span className="comment">{space.description}</span> : null}
            </li>
          ))}
        </ul>
      </section>
    </Shell>
  )
}

export function SpacePage(): ReactElement {
  const spaceId: string = useParams().spaceId ?? ""
  const [space, setSpace] = useState<SpaceRecord | null>(null)
  const [scopes, setScopes] = useState<ScopeRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    const controller: AbortController = new AbortController()
    void Promise.all([fetchSpace(spaceId, controller.signal), fetchScopes(spaceId, controller.signal)])
      .then(([loadedSpace, loadedScopes]: [SpaceRecord, ScopeRecord[]]) => {
        setSpace(loadedSpace)
        setScopes(loadedScopes)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setError(errorMessage(err))
        }
      })
    return () => {
      controller.abort()
    }
  }, [spaceId])
  return (
    <Shell status={space?.name ?? "Space"} tree>
      <section>
        <p className="crumb">
          <Link to="/spaces">Spaces</Link>
        </p>
        <h1>{space?.name ?? "Space"}</h1>
        {space?.description !== null && space?.description !== undefined ? <p>{space.description}</p> : null}
        <h2>Scopes</h2>
        {error !== null ? <p className="error-text">{error}</p> : null}
        {scopes !== null && scopes.length === 0 ? <p className="note">No scopes yet.</p> : null}
        <ul className="registry-list">
          {(scopes ?? []).map((scope: ScopeRecord) => (
            <li key={scope.id}>
              <Link to={`/scopes/${scope.id}`}>{scope.name}</Link>
            </li>
          ))}
        </ul>
      </section>
    </Shell>
  )
}

export function ScopePage(): ReactElement {
  const scopeId: string = useParams().scopeId ?? ""
  const [scope, setScope] = useState<ScopeRecord | null>(null)
  const [tables, setTables] = useState<CatalogTableRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    const controller: AbortController = new AbortController()
    void Promise.all([
      fetchScope(scopeId, controller.signal),
      fetchScopeTables(scopeId, controller.signal),
    ])
      .then(([loadedScope, loadedTables]: [ScopeRecord, CatalogTableRecord[]]) => {
        setScope(loadedScope)
        setTables(loadedTables)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setError(errorMessage(err))
        }
      })
    return () => {
      controller.abort()
    }
  }, [scopeId])
  return (
    <Shell status={scope?.name ?? "Scope"} tree>
      <section>
        <p className="crumb">
          <Link to="/spaces">Spaces</Link>
          {scope !== null ? (
            <>
              {" / "}
              <Link to={`/spaces/${scope.space_id}`}>Space</Link>
            </>
          ) : null}
        </p>
        <h1>{scope?.name ?? "Scope"}</h1>
        <h2>Tables</h2>
        {error !== null ? <p className="error-text">{error}</p> : null}
        {tables !== null && tables.length === 0 ? <p className="note">No tables yet.</p> : null}
        <ul className="registry-list">
          {(tables ?? []).map((table: CatalogTableRecord) => (
            <li key={table.id}>
              <Link to={`/registry/tables/${table.id}`}>{table.name}</Link>
              <span className="badge">{table.status}</span>
            </li>
          ))}
        </ul>
      </section>
    </Shell>
  )
}

export function RegistryTablePage(): ReactElement {
  const tableId: string = useParams().tableId ?? ""
  const [tab, setTab] = useState<TabName>("overview")
  const [table, setTable] = useState<CatalogTableRecord | null>(null)
  const [columns, setColumns] = useState<CatalogColumnRecord[]>([])
  const [relationships, setRelationships] = useState<TableRelationships | null>(null)
  const [spark, setSpark] = useState<SparkQueryAvailability | null>(null)
  const [bindings, setBindings] = useState<AssetBindingRecord[]>([])
  const [tags, setTags] = useState<TagRecord[]>([])
  const [lineage, setLineage] = useState<GraphViewRecord | null>(null)
  const [relationGraph, setRelationGraph] = useState<GraphViewRecord | null>(null)
  const [contextGraph, setContextGraph] = useState<GraphViewRecord | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    const controller: AbortController = new AbortController()
    setError(null)
    void Promise.all([
      fetchCatalogTable(tableId, controller.signal),
      fetchCatalogColumns(tableId, controller.signal),
      fetchRelationships(tableId, controller.signal),
      fetchSparkQuery(tableId, controller.signal),
      fetchBindings(tableId, controller.signal),
      fetchTableTags(tableId, controller.signal),
    ])
      .then((loaded) => {
        setTable(loaded[0])
        setColumns(loaded[1])
        setRelationships(loaded[2])
        setSpark(loaded[3])
        setBindings(loaded[4])
        setTags(loaded[5])
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setError(errorMessage(err))
        }
      })
    void fetchLineage(tableId, controller.signal)
      .then((loaded: GraphViewRecord) => {
        setLineage(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setLineage({ nodes: [], edges: [], truncated: false })
        }
      })
    void fetchRelationshipGraph(tableId, controller.signal)
      .then((loaded: GraphViewRecord) => {
        setRelationGraph(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setRelationGraph({ nodes: [], edges: [], truncated: false })
        }
      })
    void fetchContextGraph(tableId, controller.signal)
      .then((loaded: GraphViewRecord) => {
        setContextGraph(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setContextGraph({ nodes: [], edges: [], truncated: false })
        }
      })
    return () => {
      controller.abort()
    }
  }, [tableId])
  const qualified: string = table?.source_key ?? "Table"
  return (
    <Shell status={qualified} tree>
      <section>
        <p className="crumb">
          <Link to="/spaces">Spaces</Link>
          {table !== null ? (
            <>
              {" / "}
              <Link to={`/scopes/${table.scope_id}`}>Scope</Link>
            </>
          ) : null}
        </p>
        <div className="title-row">
          <h1>{table?.display_name ?? table?.name ?? "Table"}</h1>
          {table !== null ? <span className="badge">{table.status}</span> : null}
        </div>
        {error !== null ? <p className="error-text">{error}</p> : null}
        <div className="tabs" role="tablist">
          {(["overview", "columns", "relationships", "lineage", "graph", "source"] as const).map(
            (name: TabName) => (
              <button
                key={name}
                type="button"
                className={tab === name ? "tab selected" : "tab"}
                role="tab"
                aria-selected={tab === name}
                onClick={() => {
                  setTab(name)
                }}
              >
                {name}
              </button>
            ),
          )}
        </div>
        {tab === "overview" && table !== null ? (
          <div>
            <p>{qualified}</p>
            <p>{table.description ?? table.source_description ?? "No description"}</p>
            <p>
              Tags: {tags.length === 0 ? "None" : tags.map((tag: TagRecord) => tag.name).join(", ")}
            </p>
            <p className="comment">Last synced {table.last_synced_at ?? "never"}</p>
          </div>
        ) : null}
        {tab === "columns" ? <ColumnTable columns={columns} /> : null}
        {tab === "relationships" && relationships !== null ? (
          <RelationshipLists relationships={relationships} graph={relationGraph} />
        ) : null}
        {tab === "lineage" ? <GraphList graph={lineage} empty="No lineage recorded." /> : null}
        {tab === "graph" ? <GraphList graph={contextGraph} empty="No graph around this table." /> : null}
        {tab === "source" && table !== null ? (
          <SourceTab table={table} bindings={bindings} spark={spark} />
        ) : null}
      </section>
    </Shell>
  )
}

export function SearchPage(): ReactElement {
  const [query, setQuery] = useState<string>("")
  const [hits, setHits] = useState<SearchHitRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  function onSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const controller: AbortController = new AbortController()
    setError(null)
    void searchCatalog(query, controller.signal)
      .then((loaded: SearchHitRecord[]) => {
        setHits(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setError(errorMessage(err))
        }
      })
  }
  return (
    <Shell status="Search">
      <section>
        <h1>Search</h1>
        <form onSubmit={onSubmit} className="search-form">
          <input
            className="search"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value)
            }}
            aria-label="Search catalog"
          />
          <button className="button" type="submit" disabled={query.trim() === ""}>
            Search
          </button>
        </form>
        {error !== null ? <p className="error-text">{error}</p> : null}
        {hits !== null && hits.length === 0 ? <p className="note">No matches.</p> : null}
        <ul className="registry-list">
          {(hits ?? []).map((hit: SearchHitRecord) => (
            <li key={`${hit.kind}-${hit.id}`}>
              <span className="badge">{hit.kind}</span>
              {hit.kind === "table" ? (
                <Link to={`/registry/tables/${hit.id}`}>{hit.qualified_name}</Link>
              ) : (
                <span>{hit.qualified_name}</span>
              )}
              {hit.description !== null ? <span className="comment">{hit.description}</span> : null}
            </li>
          ))}
        </ul>
      </section>
    </Shell>
  )
}

export function DataSourcesPage(): ReactElement {
  const [sources, setSources] = useState<DataSourceRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useLoad(setSources, setError, fetchDataSources)
  return (
    <Shell status="Data sources">
      <section>
        <h1>Data sources</h1>
        <p className="comment">A source record does not provision a Trino or Spark connector.</p>
        {error !== null ? <p className="error-text">{error}</p> : null}
        {sources !== null && sources.length === 0 ? <p className="note">No data sources yet.</p> : null}
        <ul className="registry-list">
          {(sources ?? []).map((source: DataSourceRecord) => (
            <li key={source.id}>
              <strong>{source.name}</strong>
              <span className="badge">{source.type}</span>
              <span className="comment">{source.credential_ref ?? "No credential reference"}</span>
            </li>
          ))}
        </ul>
      </section>
    </Shell>
  )
}

function ColumnTable(props: { columns: CatalogColumnRecord[] }): ReactElement {
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
        </tr>
      </thead>
      <tbody>
        {props.columns.map((column: CatalogColumnRecord) => (
          <tr key={column.id}>
            <td>{column.name}</td>
            <td>{column.data_type ?? column.discovered_data_type ?? "Unknown"}</td>
            <td>{column.nullable === null ? "Unknown" : column.nullable ? "Yes" : "No"}</td>
            <td>{column.is_primary_key === null ? "Unknown" : column.is_primary_key ? "Yes" : "No"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function RelationshipLists(props: {
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
            .map(
              (mapping) => `${mapping.source_column_name} → ${mapping.target_column_name}`,
            )
            .join(", ")}
        </li>
      ))}
    </ul>
  )
}

function GraphList(props: { graph: GraphViewRecord | null; empty: string }): ReactElement {
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

function SourceTab(props: {
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
        <Link className="button" to={`/query?sql=${encodeURIComponent(sql)}`}>
          Query in Spark
        </Link>
      ) : (
        <p>{props.spark?.message ?? "Not configured for Spark queries"}</p>
      )}
    </div>
  )
}

function useLoad<T>(
  setValue: (value: T) => void,
  setError: (value: string | null) => void,
  load: (signal: AbortSignal) => Promise<T>,
): void {
  useEffect(() => {
    const controller: AbortController = new AbortController()
    void load(controller.signal)
      .then((loaded: T) => {
        setValue(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setError(errorMessage(err))
        }
      })
    return () => {
      controller.abort()
    }
  }, [load, setError, setValue])
}
