import { useEffect, useRef, useState, type FormEvent, type ReactElement } from "react"
import { Link, useParams } from "react-router-dom"
import { useTabParam } from "./useTabParam"
import { ActivityBar } from "./ActivityBar"
import { AirflowAttachmentsPanel } from "./AirflowAttachmentsPanel"
import { AttachAirflowForm } from "./AttachAirflowForm"
import { AttachCatalogForm } from "./AttachCatalogForm"
import { bumpCatalogRevision, useCatalogRevision } from "./catalogRevision"
import { ConfigRail, optionsFromFields } from "./ConfigRail"
import { CreateRecordForm } from "./CreateRecordForm"
import { KnowledgeContextPanel } from "./KnowledgeContextPanel"
import { ContextEditor } from "./ContextEditor"
import { ScopeContextPanel } from "./ScopeContextPanel"
import { SpaceContextPanel } from "./SpaceContextPanel"
import { SpaceScopesDigest } from "./SpaceScopesDigest"
import { SubgraphView } from "./SubgraphView"
import {
  followContextGeneration,
  followKnowledgeBuild,
  followScopeBuild,
  followSpaceBuild,
  useRunningJobWatch,
  onContextGenerationFinished,
  onKnowledgeBuildFinished,
  onScopeBuildFinished,
  onSpaceBuildFinished,
  requestContextGeneration,
  requestKnowledgeBuild,
  requestScopeBuild,
  requestSpaceBuild,
} from "./knowledgeBuild"
import { HierarchyTree } from "./HierarchyTree"
import { PageSlot } from "./PageSlot"
import { PanelChevron, PlusIcon } from "./icons"
import { setSpacesSidebarOpen, useSpacesSidebarOpen } from "./spacesSidebar"
import { errorMessage, isAbortError } from "./api"
import { useDeadlineLabel, useEntityConfig, type EntityConfigController, type OptionField } from "./refreshSettings"
import {
  fetchKnowledgeContext,
  fetchLatestKnowledgeBuild,
  fetchLatestScopeBuild,
  fetchLatestSpaceBuild,
  fetchLatestContextGeneration,
  fetchScopeGraph,
  fetchScopeKnowledgeContext,
  fetchSpaceGraph,
  fetchSpaceKnowledgeContext,
  createScope,
  createSpace,
  fetchBindings,
  fetchCatalogColumns,
  fetchCatalogTable,
  fetchKnowledgeGraph,
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
  type ContextGenerationJob,
  type DataSourceRecord,
  type GraphViewRecord,
  type KnowledgeBuildJob,
  type KnowledgeContext,
  type ScopeBuildJob,
  type ScopeKnowledgeContext,
  type ScopeRecord,
  type SearchHitRecord,
  type SpaceBuildJob,
  type SpaceKnowledgeContext,
  type SpaceRecord,
  type SparkQueryAvailability,
  type TableRelationships,
  type TagRecord,
} from "./registryApi"

type TabName = "overview" | "columns" | "relationships" | "lineage" | "graph" | "source"

function Shell(props: {
  status: string
  tree?: boolean
  config?: ReactElement
  children: ReactElement
}): ReactElement {
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
          <div className="main-scroll">
            <PageSlot>{props.children}</PageSlot>
          </div>
        </main>
        {props.config ?? null}
      </div>
      <footer className="status">
        <span>Spaces</span>
        <span>{props.status}</span>
      </footer>
    </div>
  )
}

type EntityCardItem = {
  id: string
  name: string
  description: string | null
  meta: string
  metaKind: "slug" | "status"
  to: string
}

const MARK_TONES: readonly string[] = ["tone-a", "tone-b", "tone-c", "tone-d", "tone-e"]

function countLabel(count: number, singular: string, plural: string): string {
  return count === 1 ? `1 ${singular}` : `${count} ${plural}`
}

function initials(name: string): string {
  const parts: string[] = name
    .trim()
    .split(/[\s_-]+/)
    .filter((part: string) => part.length > 0)
  const firstWord: string | undefined = parts[0]
  if (firstWord === undefined) {
    return "?"
  }
  const secondWord: string | undefined = parts[1]
  if (secondWord === undefined) {
    return firstWord.slice(0, 2).toUpperCase()
  }
  const first: string = firstWord[0] ?? ""
  const second: string = secondWord[0] ?? ""
  return `${first}${second}`.toUpperCase()
}

function toneClass(name: string): string {
  let hash: number = 0
  for (const char of name) {
    hash = (hash + char.charCodeAt(0)) % MARK_TONES.length
  }
  const tone: string | undefined = MARK_TONES[hash]
  return tone ?? "tone-a"
}

function EntityGrid(props: { items: EntityCardItem[] }): ReactElement {
  return (
    <ul className="entity-grid">
      {props.items.map((item: EntityCardItem) => (
        <li key={item.id}>
          <Link className="entity-card" to={item.to}>
            <span className="entity-card-top">
              <span className={`entity-mark ${toneClass(item.name)}`} aria-hidden="true">
                {initials(item.name)}
              </span>
              <span className="entity-go" aria-hidden="true">
                <PanelChevron direction="right" />
              </span>
            </span>
            <span className="entity-copy">
              <span className="entity-name">{item.name}</span>
              <span className="entity-desc">{item.description ?? "No description yet."}</span>
            </span>
            <span className="entity-foot">
              <span className={item.metaKind === "status" ? "badge" : "entity-meta"}>{item.meta}</span>
            </span>
          </Link>
        </li>
      ))}
    </ul>
  )
}

function EntitySkeletonGrid(): ReactElement {
  return (
    <div className="entity-skeleton-grid" aria-hidden="true">
      <div className="entity-skeleton" />
      <div className="entity-skeleton" />
      <div className="entity-skeleton" />
    </div>
  )
}

export function SpacesPage(): ReactElement {
  const revision: number = useCatalogRevision()
  const [spaces, setSpaces] = useState<SpaceRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState<boolean>(false)
  useEffect(() => {
    const controller: AbortController = new AbortController()
    void fetchSpaces(controller.signal)
      .then((loaded: SpaceRecord[]) => {
        setSpaces(loaded)
        setError(null)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setError(errorMessage(err))
        }
      })
    return () => {
      controller.abort()
    }
  }, [revision])
  const loading: boolean = spaces === null && error === null
  const cards: EntityCardItem[] = (spaces ?? []).map((space: SpaceRecord): EntityCardItem => ({
    id: space.id,
    name: space.name,
    description: space.description,
    meta: space.slug,
    metaKind: "slug",
    to: `/spaces/${space.id}`,
  }))
  return (
    <Shell status="Spaces" tree>
      <section className="catalog-page">
        <header className="catalog-hero">
          <div className="catalog-hero-top">
            <div className="catalog-hero-copy">
              <p className="catalog-kicker">Organization</p>
              <h1>Spaces</h1>
            </div>
            <div className="catalog-hero-actions">
            {spaces !== null ? (
              <span className="catalog-count">{countLabel(spaces.length, "space", "spaces")}</span>
            ) : null}
            <button
              type="button"
              className="button with-icon"
              aria-expanded={creating}
              onClick={() => {
                setCreating((open: boolean) => !open)
              }}
            >
              {creating ? null : <PlusIcon />}
              {creating ? "Close" : "Add space"}
            </button>
            </div>
          </div>
          <p className="catalog-lead">Business domains that group scopes, tables, and shared context.</p>
        </header>
        {creating ? (
          <div className="catalog-panel">
            <CreateRecordForm
              idPrefix="page-space"
              nameLabel="Space name"
              submitLabel="Add space"
              onCancel={() => {
                setCreating(false)
              }}
              onCreate={async (input) => {
                await createSpace(input)
                setCreating(false)
                bumpCatalogRevision()
              }}
            />
          </div>
        ) : null}
        {error !== null ? <p className="error-text catalog-alert">{error}</p> : null}
        {loading ? <EntitySkeletonGrid /> : null}
        {spaces !== null && spaces.length === 0 ? (
          <div className="catalog-empty">
            <span className="entity-mark tone-a" aria-hidden="true">
              Sp
            </span>
            <h2>No spaces yet</h2>
            <p>Add a space to organize catalogs by team, domain, or product.</p>
          </div>
        ) : null}
        {cards.length > 0 ? <EntityGrid items={cards} /> : null}
      </section>
    </Shell>
  )
}

const SPACE_TABS: readonly SpaceTab[] = ["overview", "scopes", "airflow", "graph"]

type SpaceTab = "overview" | "scopes" | "airflow" | "graph"

const SPACE_OPTION_FIELDS: readonly OptionField[] = [
  { key: "include_scope_knowledge", label: "Include scope knowledge", defaultValue: true },
  { key: "include_previous", label: "Keep previous findings", defaultValue: true },
  { key: "include_summary", label: "Write summary", defaultValue: true },
  { key: "include_airflow", label: "Include Airflow", defaultValue: true },
]

export function SpacePage(): ReactElement {
  const spaceId: string = useParams().spaceId ?? ""
  const revision: number = useCatalogRevision()
  const [tab, selectTab] = useTabParam<SpaceTab>(SPACE_TABS, "overview")
  const [space, setSpace] = useState<SpaceRecord | null>(null)
  const [scopes, setScopes] = useState<ScopeRecord[] | null>(null)
  const [scopeTables, setScopeTables] = useState<Map<string, number> | null>(null)
  const [spaceGraph, setSpaceGraph] = useState<GraphViewRecord | null>(null)
  const [graphError, setGraphError] = useState<string | null>(null)
  const [knowledge, setKnowledge] = useState<SpaceKnowledgeContext | null>(null)
  const [knowledgeError, setKnowledgeError] = useState<string | null>(null)
  const [building, setBuilding] = useState<boolean>(false)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState<boolean>(false)
  const [attachingAirflow, setAttachingAirflow] = useState<boolean>(false)
  const config: EntityConfigController = useEntityConfig("space", spaceId, SPACE_OPTION_FIELDS)
  const rebuildLabel: string | null = useDeadlineLabel(config.nextRunAt, config.loaded && config.enabled)
  useRunningJobWatch(
    config.loaded && config.enabled,
    (signal: AbortSignal) => fetchLatestSpaceBuild(spaceId, signal),
    followSpaceBuild,
    () => {
      setBuilding(true)
    },
  )
  useEffect(() => {
    setCreating(false)
    setAttachingAirflow(false)
    setBuilding(false)
    setKnowledge(null)
    setSpaceGraph(null)
    setScopeTables(null)
  }, [spaceId])
  useEffect(() => {
    const controller: AbortController = new AbortController()
    void Promise.all([fetchSpace(spaceId, controller.signal), fetchScopes(spaceId, controller.signal)])
      .then(async ([loadedSpace, loadedScopes]: [SpaceRecord, ScopeRecord[]]) => {
        setSpace(loadedSpace)
        setScopes(loadedScopes)
        setError(null)
        const pairs: Array<[string, number]> = await Promise.all(
          loadedScopes.map(async (scope: ScopeRecord): Promise<[string, number]> => {
            const tables: CatalogTableRecord[] = await fetchScopeTables(scope.id, controller.signal)
            return [scope.id, tables.length]
          }),
        )
        if (!controller.signal.aborted) {
          setScopeTables(new Map(pairs))
        }
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setError(errorMessage(err))
        }
      })
    setKnowledge(null)
    setKnowledgeError(null)
    void fetchSpaceKnowledgeContext(spaceId, controller.signal)
      .then((loaded: SpaceKnowledgeContext | null) => {
        setKnowledge(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setKnowledge(null)
          setKnowledgeError(errorMessage(err))
        }
      })
    setSpaceGraph(null)
    setGraphError(null)
    void fetchSpaceGraph(spaceId, controller.signal)
      .then((loaded: GraphViewRecord) => {
        setSpaceGraph(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setSpaceGraph({ nodes: [], edges: [], truncated: false })
          setGraphError(errorMessage(err))
        }
      })
    void fetchLatestSpaceBuild(spaceId, controller.signal)
      .then((job: SpaceBuildJob | null) => {
        if (controller.signal.aborted || job === null || job.status !== "running") {
          return
        }
        setBuilding(true)
        followSpaceBuild(job)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setKnowledgeError(errorMessage(err))
        }
      })
    return () => {
      controller.abort()
    }
  }, [spaceId, revision])
  useEffect(() => {
    return onSpaceBuildFinished((job: SpaceBuildJob) => {
      if (job.space_id !== spaceId) {
        return
      }
      setBuilding(false)
      if (job.status === "failed") {
        setKnowledgeError(job.error ?? "Space context build failed.")
        return
      }
      const controller: AbortController = new AbortController()
      void fetchSpaceKnowledgeContext(spaceId, controller.signal)
        .then((loaded: SpaceKnowledgeContext | null) => {
          setKnowledge(loaded)
          setKnowledgeError(null)
        })
        .catch((err: unknown) => {
          if (!isAbortError(err)) {
            setKnowledgeError(errorMessage(err))
          }
        })
      void fetchSpaceGraph(spaceId, controller.signal)
        .then((loaded: GraphViewRecord) => {
          setSpaceGraph(loaded)
          setGraphError(null)
        })
        .catch((err: unknown) => {
          if (!isAbortError(err)) {
            setSpaceGraph({ nodes: [], edges: [], truncated: false })
            setGraphError(errorMessage(err))
          }
        })
    })
  }, [spaceId])
  function runBuildContext(): void {
    setBuilding(true)
    setKnowledgeError(null)
    void requestSpaceBuild(spaceId, {
      include_scope_knowledge: config.options.include_scope_knowledge ?? true,
      include_previous: config.options.include_previous ?? true,
      include_summary: config.options.include_summary ?? true,
      include_airflow: config.options.include_airflow ?? true,
    })
      .then((job: SpaceBuildJob) => {
        setBuilding(job.status === "running")
      })
      .catch((err: unknown) => {
        setBuilding(false)
        setKnowledgeError(errorMessage(err))
      })
  }
  const loading: boolean = space === null && error === null
  const cards: EntityCardItem[] = (scopes ?? []).map((scope: ScopeRecord): EntityCardItem => ({
    id: scope.id,
    name: scope.name,
    description: scope.description,
    meta: scope.slug,
    metaKind: "slug",
    to: `/scopes/${scope.id}`,
  }))
  const lead: string =
    space?.description !== null && space?.description !== undefined && space.description !== ""
      ? space.description
      : "Scopes collected inside this space."
  const tableTotal: number | null =
    scopeTables === null
      ? null
      : [...scopeTables.values()].reduce((sum: number, count: number) => sum + count, 0)
  return (
    <Shell
      status={space?.name ?? "Space"}
      tree
      config={
        <ConfigRail
          minutes={config.minutes}
          enabled={config.enabled}
          busy={config.busy}
          error={config.error}
          detail={rebuildDetail(config, rebuildLabel)}
          options={optionsFromFields(SPACE_OPTION_FIELDS, config.options)}
          onCommit={config.commit}
        />
      }
    >
      <section className={tab === "graph" ? "catalog-page catalog-page-graph" : "catalog-page"}>
        <header className="catalog-hero">
          <div className="catalog-hero-top">
            <div className="catalog-hero-copy">
              <p className="crumb">
                <Link to="/spaces">Spaces</Link>
              </p>
              <h1>{space?.name ?? "Space"}</h1>
            </div>
            <div className="catalog-hero-actions">
            {scopes !== null ? (
              <span className="catalog-count">{countLabel(scopes.length, "scope", "scopes")}</span>
            ) : null}
            <button
              className="button secondary"
              type="button"
              disabled={space === null || building}
              onClick={runBuildContext}
            >
              {building
                ? "Building space context…"
                : knowledge === null
                  ? "Build space context"
                  : "Rebuild space context"}
            </button>
            <button
              type="button"
              className="button with-icon"
              aria-expanded={creating}
              disabled={space === null}
              onClick={() => {
                setCreating((open: boolean) => !open)
                selectTab("scopes")
              }}
            >
              {creating ? null : <PlusIcon />}
              {creating ? "Close" : "Add scope"}
            </button>
            <button
              type="button"
              className="button with-icon"
              aria-expanded={attachingAirflow}
              disabled={space === null}
              onClick={() => {
                setAttachingAirflow((open: boolean) => !open)
                selectTab("airflow")
              }}
            >
              {attachingAirflow ? "Close" : "Attach Airflow"}
            </button>
            </div>
          </div>
          <p className="catalog-lead">{lead}</p>
        </header>
        {building ? (
          <p className="comment">
            Building in the background. Keep using the catalog. An alert appears when it finishes.
          </p>
        ) : null}
        {error !== null ? <p className="error-text catalog-alert">{error}</p> : null}
        {knowledgeError !== null ? <p className="error-text catalog-alert">{knowledgeError}</p> : null}
        <div className="tabs" role="tablist">
          {SPACE_TABS.map((name: SpaceTab) => (
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
          <>
            {space !== null ? (
              <dl className="facts">
                <dt>Scopes</dt>
                <dd>{scopes === null ? "…" : scopes.length}</dd>
                <dt>Tables</dt>
                <dd>{tableTotal === null ? "…" : tableTotal}</dd>
                <dt>Description</dt>
                <dd>
                  {space.description !== null && space.description !== "" ? space.description : "None"}
                </dd>
              </dl>
            ) : null}
            {space !== null ? (
              <ContextEditor
                kind="space"
                id={space.id}
                name={space.name}
                context={space.context}
                origin={space.context_origin}
                onChanged={() => {
                  const controller: AbortController = new AbortController()
                  void fetchSpace(spaceId, controller.signal)
                    .then((loaded: SpaceRecord) => {
                      setSpace(loaded)
                    })
                    .catch((err: unknown) => {
                      if (!isAbortError(err)) {
                        setError(errorMessage(err))
                      }
                    })
                }}
              />
            ) : null}
            {scopes !== null ? <SpaceScopesDigest scopes={scopes} tableCounts={scopeTables} /> : null}
            {knowledge !== null ? (
              <SpaceContextPanel context={knowledge} />
            ) : (
              <p className="note">Build space context to get a summary and cross-scope links.</p>
            )}
          </>
        ) : null}
        {tab === "scopes" ? (
          <>
            {creating ? (
              <div className="catalog-panel">
                <CreateRecordForm
                  idPrefix="page-scope"
                  nameLabel="Scope name"
                  submitLabel="Add scope"
                  onCancel={() => {
                    setCreating(false)
                  }}
                  onCreate={async (input) => {
                    await createScope(spaceId, input)
                    setCreating(false)
                    bumpCatalogRevision()
                  }}
                />
              </div>
            ) : null}
            {loading ? <EntitySkeletonGrid /> : null}
            {scopes !== null && scopes.length === 0 ? (
              <div className="catalog-empty">
                <span className="entity-mark tone-b" aria-hidden="true">
                  Sc
                </span>
                <h2>No scopes yet</h2>
                <p>Add a scope to attach catalogs and tables to this space.</p>
              </div>
            ) : null}
            {cards.length > 0 ? <EntityGrid items={cards} /> : null}
          </>
        ) : null}
        {tab === "airflow" ? (
          <>
            {attachingAirflow ? (
              <div className="catalog-panel">
                <AttachAirflowForm
                  targetKind="space"
                  targetId={spaceId}
                  onCancel={() => {
                    setAttachingAirflow(false)
                  }}
                  onAttached={() => {
                    bumpCatalogRevision()
                  }}
                />
              </div>
            ) : null}
            <AirflowAttachmentsPanel
              targetKind="space"
              targetId={spaceId}
              revision={revision}
              onChanged={() => {
                bumpCatalogRevision()
              }}
            />
          </>
        ) : null}
        {tab === "graph" ? (
          <SubgraphView
            graph={spaceGraph}
            error={graphError}
            emptyMessage="No scopes in this space."
          />
        ) : null}
      </section>
    </Shell>
  )
}

const SCOPE_TABS: readonly ScopeTab[] = ["overview", "tables", "airflow", "graph"]

type ScopeTab = "overview" | "tables" | "airflow" | "graph"

const SCOPE_OPTION_FIELDS: readonly OptionField[] = [
  { key: "include_table_knowledge", label: "Include table knowledge", defaultValue: true },
  { key: "include_previous", label: "Keep previous findings", defaultValue: true },
  { key: "include_summary", label: "Write summary", defaultValue: true },
  { key: "include_airflow", label: "Include Airflow", defaultValue: true },
]

export function ScopePage(): ReactElement {
  const scopeId: string = useParams().scopeId ?? ""
  const revision: number = useCatalogRevision()
  const [tab, selectTab] = useTabParam<ScopeTab>(SCOPE_TABS, "overview")
  const [scope, setScope] = useState<ScopeRecord | null>(null)
  const [spaceName, setSpaceName] = useState<string | null>(null)
  const [tables, setTables] = useState<CatalogTableRecord[] | null>(null)
  const [scopeGraph, setScopeGraph] = useState<GraphViewRecord | null>(null)
  const [graphError, setGraphError] = useState<string | null>(null)
  const [knowledge, setKnowledge] = useState<ScopeKnowledgeContext | null>(null)
  const [knowledgeError, setKnowledgeError] = useState<string | null>(null)
  const [building, setBuilding] = useState<boolean>(false)
  const [error, setError] = useState<string | null>(null)
  const [attaching, setAttaching] = useState<boolean>(false)
  const [attachingAirflow, setAttachingAirflow] = useState<boolean>(false)
  const config: EntityConfigController = useEntityConfig("scope", scopeId, SCOPE_OPTION_FIELDS)
  const rebuildLabel: string | null = useDeadlineLabel(config.nextRunAt, config.loaded && config.enabled)
  useRunningJobWatch(
    config.loaded && config.enabled,
    (signal: AbortSignal) => fetchLatestScopeBuild(scopeId, signal),
    followScopeBuild,
    () => {
      setBuilding(true)
    },
  )
  useEffect(() => {
    setAttaching(false)
    setAttachingAirflow(false)
    setSpaceName(null)
    setBuilding(false)
    setKnowledge(null)
    setScopeGraph(null)
  }, [scopeId])
  useEffect(() => {
    const controller: AbortController = new AbortController()
    void Promise.all([
      fetchScope(scopeId, controller.signal),
      fetchScopeTables(scopeId, controller.signal),
    ])
      .then(async ([loadedScope, loadedTables]: [ScopeRecord, CatalogTableRecord[]]) => {
        setScope(loadedScope)
        setTables(loadedTables)
        setError(null)
        try {
          const parent: SpaceRecord = await fetchSpace(loadedScope.space_id, controller.signal)
          setSpaceName(parent.name)
        } catch (parentError: unknown) {
          if (!isAbortError(parentError)) {
            setSpaceName(null)
          }
        }
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setError(errorMessage(err))
        }
      })
    setScopeGraph(null)
    setGraphError(null)
    void fetchScopeGraph(scopeId, controller.signal)
      .then((loaded: GraphViewRecord) => {
        setScopeGraph(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setScopeGraph({ nodes: [], edges: [], truncated: false })
          setGraphError(errorMessage(err))
        }
      })
    setKnowledge(null)
    setKnowledgeError(null)
    void fetchScopeKnowledgeContext(scopeId, controller.signal)
      .then((loaded: ScopeKnowledgeContext | null) => {
        setKnowledge(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setKnowledge(null)
          setKnowledgeError(errorMessage(err))
        }
      })
    void fetchLatestScopeBuild(scopeId, controller.signal)
      .then((job: ScopeBuildJob | null) => {
        if (controller.signal.aborted || job === null || job.status !== "running") {
          return
        }
        setBuilding(true)
        followScopeBuild(job)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setKnowledgeError(errorMessage(err))
        }
      })
    return () => {
      controller.abort()
    }
  }, [scopeId, revision])
  useEffect(() => {
    return onScopeBuildFinished((job: ScopeBuildJob) => {
      if (job.scope_id !== scopeId) {
        return
      }
      setBuilding(false)
      if (job.status === "failed") {
        setKnowledgeError(job.error ?? "Scope context build failed.")
        return
      }
      const controller: AbortController = new AbortController()
      void fetchScopeKnowledgeContext(scopeId, controller.signal)
        .then((loaded: ScopeKnowledgeContext | null) => {
          setKnowledge(loaded)
          setKnowledgeError(null)
        })
        .catch((err: unknown) => {
          if (!isAbortError(err)) {
            setKnowledgeError(errorMessage(err))
          }
        })
      void fetchScopeGraph(scopeId, controller.signal)
        .then((loaded: GraphViewRecord) => {
          setScopeGraph(loaded)
          setGraphError(null)
        })
        .catch((err: unknown) => {
          if (!isAbortError(err)) {
            setScopeGraph({ nodes: [], edges: [], truncated: false })
            setGraphError(errorMessage(err))
          }
        })
    })
  }, [scopeId])
  function runBuildContext(): void {
    setBuilding(true)
    setKnowledgeError(null)
    void requestScopeBuild(scopeId, {
      include_table_knowledge: config.options.include_table_knowledge ?? true,
      include_previous: config.options.include_previous ?? true,
      include_summary: config.options.include_summary ?? true,
      include_airflow: config.options.include_airflow ?? true,
    })
      .then((job: ScopeBuildJob) => {
        setBuilding(job.status === "running")
      })
      .catch((err: unknown) => {
        setBuilding(false)
        setKnowledgeError(errorMessage(err))
      })
  }
  const loading: boolean = scope === null && error === null
  const cards: EntityCardItem[] = (tables ?? []).map((table: CatalogTableRecord): EntityCardItem => {
    const qualified: string = `${table.physical_catalog}.${table.physical_namespace}.${table.physical_table}`
    return {
      id: table.id,
      name: table.display_name ?? table.name,
      description: table.description ?? qualified,
      meta: table.status,
      metaKind: "status",
      to: `/registry/tables/${table.id}`,
    }
  })
  const lead: string =
    scope?.description !== null && scope?.description !== undefined && scope.description !== ""
      ? scope.description
      : "Tables attached to this scope."
  return (
    <Shell
      status={scope?.name ?? "Scope"}
      tree
      config={
        <ConfigRail
          minutes={config.minutes}
          enabled={config.enabled}
          busy={config.busy}
          error={config.error}
          detail={rebuildDetail(config, rebuildLabel)}
          options={optionsFromFields(SCOPE_OPTION_FIELDS, config.options)}
          onCommit={config.commit}
        />
      }
    >
      <section className={tab === "graph" ? "catalog-page catalog-page-graph" : "catalog-page"}>
        <header className="catalog-hero">
          <div className="catalog-hero-top">
            <div className="catalog-hero-copy">
              <p className="crumb">
                <Link to="/spaces">Spaces</Link>
                {scope !== null ? (
                  <>
                    {" / "}
                    <Link to={`/spaces/${scope.space_id}`}>{spaceName ?? "Space"}</Link>
                  </>
                ) : null}
              </p>
              <h1>{scope?.name ?? "Scope"}</h1>
            </div>
            <div className="catalog-hero-actions">
            {tables !== null ? (
              <span className="catalog-count">{countLabel(tables.length, "table", "tables")}</span>
            ) : null}
            <button
              className="button secondary"
              type="button"
              disabled={scope === null || building}
              onClick={runBuildContext}
            >
              {building
                ? "Building scope context…"
                : knowledge === null
                  ? "Build scope context"
                  : "Rebuild scope context"}
            </button>
            <button
              type="button"
              className="button with-icon"
              aria-expanded={attaching}
              disabled={scope === null}
              onClick={() => {
                setAttaching((open: boolean) => !open)
                selectTab("tables")
              }}
            >
              {attaching ? null : <PlusIcon />}
              {attaching ? "Close" : "Attach from catalog"}
            </button>
            <button
              type="button"
              className="button with-icon"
              aria-expanded={attachingAirflow}
              disabled={scope === null}
              onClick={() => {
                setAttachingAirflow((open: boolean) => !open)
                selectTab("airflow")
              }}
            >
              {attachingAirflow ? "Close" : "Attach Airflow"}
            </button>
            </div>
          </div>
          <p className="catalog-lead">{lead}</p>
        </header>
        {building ? (
          <p className="comment">
            Building in the background. Keep using the catalog. An alert appears when it finishes.
          </p>
        ) : null}
        {error !== null ? <p className="error-text catalog-alert">{error}</p> : null}
        {knowledgeError !== null ? <p className="error-text catalog-alert">{knowledgeError}</p> : null}
        <div className="tabs" role="tablist">
          {SCOPE_TABS.map((name: ScopeTab) => (
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
          <>
            {scope !== null ? (
              <ContextEditor
                kind="scope"
                id={scope.id}
                name={scope.name}
                context={scope.context}
                origin={scope.context_origin}
                onChanged={() => {
                  const controller: AbortController = new AbortController()
                  void fetchScope(scopeId, controller.signal)
                    .then((loaded: ScopeRecord) => {
                      setScope(loaded)
                    })
                    .catch((err: unknown) => {
                      if (!isAbortError(err)) {
                        setError(errorMessage(err))
                      }
                    })
                }}
              />
            ) : null}
            {knowledge !== null ? (
              <ScopeContextPanel context={knowledge} />
            ) : (
              <p className="note">Build scope context to get a summary and cross-table links.</p>
            )}
          </>
        ) : null}
        {tab === "tables" ? (
          <>
            {attaching ? (
              <div className="catalog-panel">
                <AttachCatalogForm
                  scopeId={scopeId}
                  onCancel={() => {
                    setAttaching(false)
                  }}
                  onAttached={() => {
                    bumpCatalogRevision()
                  }}
                />
              </div>
            ) : null}
            {loading ? <EntitySkeletonGrid /> : null}
            {tables !== null && tables.length === 0 ? (
              <div className="catalog-empty">
                <span className="entity-mark tone-c" aria-hidden="true">
                  Tb
                </span>
                <h2>No tables yet</h2>
                <p>Attach a catalog namespace to bring tables into this scope.</p>
              </div>
            ) : null}
            {cards.length > 0 ? <EntityGrid items={cards} /> : null}
          </>
        ) : null}
        {tab === "airflow" ? (
          <>
            {attachingAirflow ? (
              <div className="catalog-panel">
                <AttachAirflowForm
                  targetKind="scope"
                  targetId={scopeId}
                  onCancel={() => {
                    setAttachingAirflow(false)
                  }}
                  onAttached={() => {
                    bumpCatalogRevision()
                  }}
                />
              </div>
            ) : null}
            <AirflowAttachmentsPanel
              targetKind="scope"
              targetId={scopeId}
              revision={revision}
              onChanged={() => {
                bumpCatalogRevision()
              }}
            />
          </>
        ) : null}
        {tab === "graph" ? (
          <SubgraphView
            graph={scopeGraph}
            error={graphError}
            emptyMessage="No tables in this scope."
          />
        ) : null}
      </section>
    </Shell>
  )
}

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
  const [bindings, setBindings] = useState<AssetBindingRecord[]>([])
  const [tags, setTags] = useState<TagRecord[]>([])
  const [lineage, setLineage] = useState<GraphViewRecord | null>(null)
  const [relationGraph, setRelationGraph] = useState<GraphViewRecord | null>(null)
  const [knowledgeGraph, setKnowledgeGraph] = useState<GraphViewRecord | null>(null)
  const [graphError, setGraphError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [knowledge, setKnowledge] = useState<KnowledgeContext | null>(null)
  const [knowledgeError, setKnowledgeError] = useState<string | null>(null)
  const [building, setBuilding] = useState<boolean>(false)
  const [columnsBuilding, setColumnsBuilding] = useState<boolean>(false)
  useRunningJobWatch(
    config.loaded && config.enabled,
    (signal: AbortSignal) => fetchLatestKnowledgeBuild(tableId, signal),
    followKnowledgeBuild,
    () => {
      setBuilding(true)
    },
  )
  const loadAbort = useRef<AbortController | null>(null)
  useEffect(() => {
    setBuilding(false)
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
    setKnowledgeError(null)
    void requestKnowledgeBuild(tableId, {
      include_data_profile: config.options.include_data_profile ?? false,
      include_graph: config.options.include_graph ?? true,
      include_lineage: config.options.include_lineage ?? true,
    })
      .then((job: KnowledgeBuildJob) => {
        setBuilding(job.status === "running")
      })
      .catch((err: unknown) => {
        setBuilding(false)
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
  return (
    <Shell
      status={qualified}
      tree
      config={
        <ConfigRail
          minutes={config.minutes}
          enabled={config.enabled}
          busy={config.busy}
          error={config.error}
          detail={rebuildDetail(config, rebuildLabel)}
          options={optionsFromFields(TABLE_OPTION_FIELDS, config.options)}
          onCommit={config.commit}
        />
      }
    >
      <section className={tab === "graph" ? "section-fill" : undefined}>
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
          <p className="comment">
            Building in the background. Keep using the catalog. An alert appears when it finishes.
          </p>
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
            aria-label="Search spaces"
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

function ColumnTable(props: {
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
            <td>{column.data_type ?? column.discovered_data_type ?? "Unknown"}</td>
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
