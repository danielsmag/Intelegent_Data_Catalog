import { useEffect, useRef, useState, type FormEvent, type ReactElement } from "react"
import { Link, useParams } from "react-router-dom"
import { useTabParam } from "./useTabParam"
import { BuildingBanner } from "./BuildingBanner"
import { useCatalogRevision } from "./catalogRevision"
import { ConfigRail, optionsFromFields } from "./ConfigRail"
import { KnowledgeContextPanel } from "./KnowledgeContextPanel"
import { ContextEditor } from "./ContextEditor"
import { SubgraphView } from "./SubgraphView"
import {
  followContextGeneration,
  followKnowledgeBuild,
  isCancelledBuild,
  useRunningJobWatch,
  onContextGenerationFinished,
  onKnowledgeBuildFinished,
  requestContextGeneration,
  requestKnowledgeBuild,
} from "./knowledgeBuild"
import { EditorRail, WorkbenchLink, useEditorStatus, useEditorTitle } from "./editorSlots"
import { ColumnTable, GraphList, RelationshipLists, SourceTab } from "./TableDetail"
import { errorMessage, isAbortError } from "./api"
import { useDeadlineLabel, useEntityConfig, type EntityConfigController, type OptionField } from "./refreshSettings"
import {
  fetchKnowledgeContext,
  fetchLatestKnowledgeBuild,
  fetchLatestContextGeneration,
  fetchBindings,
  fetchCatalogColumns,
  fetchCatalogTable,
  fetchKnowledgeGraph,
  fetchLineage,
  fetchRelationshipGraph,
  fetchRelationships,
  fetchSparkQuery,
  fetchTableTags,
  searchCatalog,
  type AgentBindingRecord,
  type CatalogColumnRecord,
  type CatalogTableRecord,
  type ContextGenerationJob,
  type GraphViewRecord,
  type KnowledgeBuildJob,
  type KnowledgeContext,
  type SearchHitRecord,
  type SparkQueryAvailability,
  type TableRelationships,
  type TagRecord,
} from "./registryApi"

type TabName = "overview" | "columns" | "relationships" | "lineage" | "graph" | "source"

function rebuildDetail(config: EntityConfigController, label: string | null): string | null {
  if (!config.loaded) {
    return null
  }
  if (!config.enabled) {
    return "Automatic refresh is off."
  }
  return label
}

const TABLE_TABS: readonly TabName[] = ["overview", "columns", "relationships", "lineage", "graph", "source"]

const TABLE_OPTION_FIELDS: readonly OptionField[] = [
  { key: "include_graph", label: "Include graph", defaultValue: true },
  { key: "include_lineage", label: "Include lineage", defaultValue: true },
  { key: "include_data_profile", label: "Include data profile", defaultValue: false },
]

export function RegistryTablePage(): ReactElement {
  const tableId: string = useParams().tableId ?? ""
  const revision: number = useCatalogRevision()
  const config: EntityConfigController = useEntityConfig("table", tableId, TABLE_OPTION_FIELDS)
  const rebuildLabel: string | null = useDeadlineLabel(config.nextRunAt, config.loaded && config.enabled)
  const [tab, selectTab] = useTabParam<TabName>(TABLE_TABS, "overview")
  const [table, setTable] = useState<CatalogTableRecord | null>(null)
  const [columns, setColumns] = useState<CatalogColumnRecord[]>([])
  const [relationships, setRelationships] = useState<TableRelationships | null>(null)
  const [spark, setSpark] = useState<SparkQueryAvailability | null>(null)
  const [bindings, setBindings] = useState<AgentBindingRecord[]>([])
  const [tags, setTags] = useState<TagRecord[]>([])
  const [lineage, setLineage] = useState<GraphViewRecord | null>(null)
  const [relationGraph, setRelationGraph] = useState<GraphViewRecord | null>(null)
  const [knowledgeGraph, setKnowledgeGraph] = useState<GraphViewRecord | null>(null)
  const [graphError, setGraphError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [knowledge, setKnowledge] = useState<KnowledgeContext | null>(null)
  const [knowledgeError, setKnowledgeError] = useState<string | null>(null)
  const [building, setBuilding] = useState<boolean>(false)
  const [buildJobId, setBuildJobId] = useState<string | null>(null)
  const [columnsBuilding, setColumnsBuilding] = useState<boolean>(false)
  useRunningJobWatch(
    config.loaded && config.enabled,
    (signal: AbortSignal) => fetchLatestKnowledgeBuild(tableId, signal),
    followKnowledgeBuild,
    (job: KnowledgeBuildJob) => {
      setBuilding(true)
      setBuildJobId(job.id)
    },
  )
  const loadAbort = useRef<AbortController | null>(null)
  useEffect(() => {
    setBuilding(false)
    setBuildJobId(null)
    setColumnsBuilding(false)
  }, [tableId])
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
    setKnowledgeGraph(null)
    setGraphError(null)
    void fetchKnowledgeGraph(tableId, controller.signal)
      .then((loaded: GraphViewRecord) => {
        setKnowledgeGraph(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setKnowledgeGraph({ nodes: [], edges: [], truncated: false })
          setGraphError(errorMessage(err))
        }
      })
    setKnowledge(null)
    setKnowledgeError(null)
    const knowledgeController: AbortController = new AbortController()
    loadAbort.current = knowledgeController
    void fetchKnowledgeContext(tableId, knowledgeController.signal)
      .then((loaded: KnowledgeContext | null) => {
        setKnowledge(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setKnowledge(null)
          setKnowledgeError(errorMessage(err))
        }
      })
    void fetchLatestKnowledgeBuild(tableId, controller.signal)
      .then((job: KnowledgeBuildJob | null) => {
        if (controller.signal.aborted || job === null || job.status !== "running") {
          return
        }
        setBuilding(true)
        setBuildJobId(job.id)
        followKnowledgeBuild(job)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setKnowledgeError(errorMessage(err))
        }
      })
    void fetchLatestContextGeneration("table_columns", tableId, controller.signal)
      .then((job: ContextGenerationJob | null) => {
        if (controller.signal.aborted || job === null || job.status !== "running") {
          return
        }
        setColumnsBuilding(true)
        followContextGeneration(job)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setKnowledgeError(errorMessage(err))
        }
      })
    return () => {
      controller.abort()
      knowledgeController.abort()
    }
  }, [tableId, revision])
  useEffect(() => {
    return onKnowledgeBuildFinished((job: KnowledgeBuildJob) => {
      if (job.table_id !== tableId) {
        return
      }
      setBuilding(false)
      setBuildJobId(null)
      if (isCancelledBuild(job)) {
        return
      }
      if (job.status === "failed") {
        setKnowledgeError(job.error ?? "Context build failed.")
        return
      }
      const controller: AbortController = new AbortController()
      void fetchKnowledgeContext(tableId, controller.signal)
        .then((loaded: KnowledgeContext | null) => {
          setKnowledge(loaded)
        })
        .catch((err: unknown) => {
          if (!isAbortError(err)) {
            setKnowledgeError(errorMessage(err))
          }
        })
      void fetchKnowledgeGraph(tableId, controller.signal)
        .then((loaded: GraphViewRecord) => {
          setKnowledgeGraph(loaded)
          setGraphError(null)
        })
        .catch((err: unknown) => {
          if (!isAbortError(err)) {
            setKnowledgeGraph({ nodes: [], edges: [], truncated: false })
            setGraphError(errorMessage(err))
          }
        })
    })
  }, [tableId])
  useEffect(() => {
    return onContextGenerationFinished((job: ContextGenerationJob) => {
      if (job.subject_kind !== "table_columns" || job.subject_id !== tableId) {
        return
      }
      setColumnsBuilding(false)
      if (job.status !== "succeeded") {
        return
      }
      const controller: AbortController = new AbortController()
      void fetchCatalogColumns(tableId, controller.signal)
        .then((loaded: CatalogColumnRecord[]) => {
          setColumns(loaded)
        })
        .catch((err: unknown) => {
          if (!isAbortError(err)) {
            setError(errorMessage(err))
          }
        })
    })
  }, [tableId])
  function reloadTable(): void {
    const controller: AbortController = new AbortController()
    void fetchCatalogTable(tableId, controller.signal)
      .then((loaded: CatalogTableRecord) => {
        setTable(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setError(errorMessage(err))
        }
      })
  }
  function reloadColumns(): void {
    const controller: AbortController = new AbortController()
    void fetchCatalogColumns(tableId, controller.signal)
      .then((loaded: CatalogColumnRecord[]) => {
        setColumns(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setError(errorMessage(err))
        }
      })
  }
  function runBuildContext(): void {
    setBuilding(true)
    setBuildJobId(null)
    setKnowledgeError(null)
    void requestKnowledgeBuild(tableId, {
      include_data_profile: config.options.include_data_profile ?? false,
      include_graph: config.options.include_graph ?? true,
      include_lineage: config.options.include_lineage ?? true,
    })
      .then((job: KnowledgeBuildJob) => {
        setBuilding(job.status === "running")
        setBuildJobId(job.status === "running" ? job.id : null)
      })
      .catch((err: unknown) => {
        setBuilding(false)
        setBuildJobId(null)
        setKnowledgeError(errorMessage(err))
      })
  }
  function runColumnContexts(): void {
    setColumnsBuilding(true)
    setError(null)
    void requestContextGeneration("table_columns", tableId)
      .then((job: ContextGenerationJob) => {
        setColumnsBuilding(job.status === "running")
        if (job.status === "succeeded") {
          reloadColumns()
        }
      })
      .catch((err: unknown) => {
        setColumnsBuilding(false)
        setError(errorMessage(err))
      })
  }
  const qualified: string = table?.source_key ?? "Table"
  const linkedAgentId: string | null = table?.agent_id ?? null
  useEditorTitle(table?.display_name ?? table?.name ?? null)
  useEditorStatus("Catalog", qualified)
  if (linkedAgentId !== null && linkedAgentId !== "") {
    return (
      <section>
        <p className="crumb">
          <Link to="/catalog">Catalog</Link>
        </p>
        <h1>{table?.display_name ?? table?.name ?? "Table"}</h1>
        <p className="note">Context for this table is on the agent.</p>
        <p>
          <WorkbenchLink to={`/agents/${linkedAgentId}`}>Open agent</WorkbenchLink>
        </p>
      </section>
    )
  }
  return (
    <>
      <EditorRail>
        <ConfigRail
          minutes={config.minutes}
          enabled={config.enabled}
          model={config.model}
          effort={config.effort}
          busy={config.busy}
          error={config.error}
          detail={rebuildDetail(config, rebuildLabel)}
          options={optionsFromFields(TABLE_OPTION_FIELDS, config.options)}
          onCommit={config.commit}
        />
      </EditorRail>
      <section className={tab === "graph" ? "section-fill" : undefined}>
        <p className="crumb">
          <Link to="/catalog">Catalog</Link>
        </p>
        <div className="title-row">
          <h1>{table?.display_name ?? table?.name ?? "Table"}</h1>
          {table !== null ? <span className="badge">{table.status}</span> : null}
          <button
            className="button secondary title-action"
            type="button"
            disabled={table === null || building}
            onClick={runBuildContext}
          >
            {building
              ? "Building context…"
              : knowledge === null
                ? "Build context"
                : "Rebuild context"}
          </button>
        </div>
        {building ? (
          <BuildingBanner
            jobId={buildJobId}
            onCancelled={() => {
              setBuilding(false)
              setBuildJobId(null)
            }}
          />
        ) : null}
        {error !== null ? <p className="error-text">{error}</p> : null}
        {knowledgeError !== null ? <p className="error-text">{knowledgeError}</p> : null}
        <div className="tabs" role="tablist">
          {TABLE_TABS.map((name: TabName) => (
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
              {name}
            </button>
          ))}
        </div>
        {tab === "overview" ? (
          <div>
            {table !== null ? (
              <ContextEditor
                kind="table"
                id={table.id}
                name={table.display_name ?? table.name}
                context={table.context}
                origin={table.context_origin}
                onChanged={reloadTable}
              />
            ) : null}
            <button
              className="button secondary"
              type="button"
              disabled={table === null || columnsBuilding}
              onClick={runColumnContexts}
            >
              {columnsBuilding ? "Generating column contexts…" : "Generate all column contexts"}
            </button>
            {knowledge !== null ? <KnowledgeContextPanel context={knowledge} /> : null}
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
          </div>
        ) : null}
        {tab === "columns" ? (
          <ColumnTable columns={columns} onContextChanged={reloadColumns} />
        ) : null}
        {tab === "relationships" && relationships !== null ? (
          <RelationshipLists relationships={relationships} graph={relationGraph} />
        ) : null}
        {tab === "lineage" ? <GraphList graph={lineage} empty="No lineage recorded." /> : null}
        {tab === "graph" ? (
          <SubgraphView graph={knowledgeGraph} error={graphError} focusTableId={tableId} />
        ) : null}
        {tab === "source" && table !== null ? (
          <SourceTab table={table} bindings={bindings} spark={spark} />
        ) : null}
      </section>
    </>
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
    <>
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
                <WorkbenchLink to={`/registry/tables/${hit.id}`}>{hit.qualified_name}</WorkbenchLink>
              ) : (
                <span>{hit.qualified_name}</span>
              )}
              {hit.description !== null ? <span className="comment">{hit.description}</span> : null}
            </li>
          ))}
        </ul>
      </section>
    </>
  )
}
