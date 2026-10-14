import { useEffect, useState, type FormEvent, type ReactElement } from "react"
import { Link, useNavigate, useParams, useSearchParams, type NavigateFunction } from "react-router-dom"
import { ActivityBar } from "./ActivityBar"
import { BuildingBanner } from "./BuildingBanner"
import {
  addDagTable,
  createAirflowInstance,
  deleteAirflowInstance,
  fetchAirflowDag,
  fetchAirflowDagRuns,
  fetchAirflowDags,
  fetchAirflowInstance,
  fetchAirflowInstances,
  fetchAirflowKnowledgeContext,
  fetchAirflowOverview,
  fetchLatestAirflowBuild,
  removeDagTable,
  syncAirflowInstance,
  testAirflowInstance,
  updateAirflowInstance,
  type AirflowConnectionTest,
  type AirflowDag,
  type AirflowBuildJob,
  type AirflowDagDetail,
  type AirflowDagFinding,
  type AirflowDagLink,
  type AirflowDagRun,
  type AirflowDagTable,
  type AirflowKnowledgeContext,
  type AirflowHealth,
  type AirflowImportError,
  type AirflowInstance,
  type AirflowInstanceInput,
  type AirflowInstanceStatus,
  type AirflowMappingDirection,
  type AirflowOverview,
  type AirflowPlugin,
  type AirflowPool,
  type AirflowProvider,
  type AirflowRunStat,
  type AirflowSyncRun,
  type AirflowTask,
} from "./airflowApi"
import { AirflowSidebar } from "./AirflowSidebar"
import { ContextEditor } from "./ContextEditor"
import {
  followAirflowBuild,
  isCancelledBuild,
  onAirflowBuildFinished,
  requestAirflowBuild,
} from "./knowledgeBuild"
import { PageSlot } from "./PageSlot"
import { AirflowConfigRail } from "./ConfigRail"
import { bumpAirflowRevision, setAirflowSidebarOpen, useAirflowRevision, useAirflowSidebarOpen } from "./airflowSidebarStore"
import { errorMessage, isAbortError } from "./api"
import { PlusIcon } from "./icons"
import { searchCatalog, type SearchHitRecord } from "./registryApi"
import { useTabParam } from "./useTabParam"

function Shell(props: { status: string; config?: ReactElement; children: ReactElement }): ReactElement {
  const sidebarOpen: boolean = useAirflowSidebarOpen()
  return (
    <div className="shell">
      <div className="workspace">
        <ActivityBar />
        <AirflowSidebar
          collapsed={!sidebarOpen}
          onToggleCollapsed={() => {
            setAirflowSidebarOpen(!sidebarOpen)
          }}
        />
        <main className="main">
          <div className="main-scroll">
            <PageSlot>{props.children}</PageSlot>
          </div>
        </main>
        {props.config ?? null}
      </div>
      <footer className="status">
        <span>Airflow</span>
        <span>{props.status}</span>
      </footer>
    </div>
  )
}

function formatWhen(value: string | null): string {
  if (value === null || value === "") {
    return "—"
  }
  const parsed: Date = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }
  return parsed.toLocaleString()
}

function formatDuration(value: number | null): string {
  if (value === null) {
    return "—"
  }
  return `${value.toFixed(1)}s`
}

function StatePill(props: { state: string | null }): ReactElement {
  const state: string = props.state ?? "none"
  const known: boolean = state === "success" || state === "failed" || state === "running" || state === "queued"
  const tone: string = known ? state : "queued"
  return <span className={`state-pill state-${tone}`}>{state}</span>
}

function Section(props: { title: string; error: string | null; children: ReactElement | null }): ReactElement {
  let body: ReactElement
  if (props.error !== null) {
    body = <p className="error-text">{props.error}</p>
  } else if (props.children === null) {
    body = <p className="note">None.</p>
  } else {
    body = props.children
  }
  return (
    <div>
      <h2>{props.title}</h2>
      {body}
    </div>
  )
}

export function AirflowInstancesPage(): ReactElement {
  const revision: number = useAirflowRevision()
  const [params, setParams] = useSearchParams()
  const [instances, setInstances] = useState<AirflowInstance[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState<boolean>(params.get("new") === "1")
  const [syncingId, setSyncingId] = useState<string | null>(null)
  useEffect(() => {
    if (params.get("new") === "1") {
      setCreating(true)
    }
  }, [params])
  useEffect(() => {
    const controller: AbortController = new AbortController()
    void fetchAirflowInstances(controller.signal)
      .then((loaded: AirflowInstance[]) => {
        setInstances(loaded)
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

  function closeForm(): void {
    setCreating(false)
    const next: URLSearchParams = new URLSearchParams(params)
    next.delete("new")
    setParams(next, { replace: true })
  }

  function runSync(instanceId: string): void {
    setSyncingId(instanceId)
    setError(null)
    void syncAirflowInstance(instanceId)
      .then(() => {
        bumpAirflowRevision()
      })
      .catch((err: unknown) => {
        setError(errorMessage(err))
        bumpAirflowRevision()
      })
      .finally(() => {
        setSyncingId(null)
      })
  }

  return (
    <Shell status="Instances">
      <section className="catalog-page">
        <header className="catalog-hero">
          <div className="catalog-hero-top">
            <div className="catalog-hero-copy">
              <p className="catalog-kicker">Orchestration</p>
              <h1>Airflow</h1>
            </div>
            <div className="catalog-hero-actions">
              {instances !== null ? (
                <span className="catalog-count">
                  {instances.length === 1 ? "1 instance" : `${instances.length} instances`}
                </span>
              ) : null}
              <button
                type="button"
                className="button with-icon"
                aria-expanded={creating}
                onClick={() => {
                  if (creating) {
                    closeForm()
                  } else {
                    setCreating(true)
                  }
                }}
              >
                {creating ? null : <PlusIcon />}
                {creating ? "Close" : "Add instance"}
              </button>
            </div>
          </div>
          <p className="catalog-lead">
            Registered Airflow 3 deployments. Sync stores DAGs, then maps them to catalog tables.
          </p>
        </header>
        {creating ? (
          <div className="catalog-panel">
            <InstanceForm
              submitLabel="Add instance"
              onCancel={closeForm}
              onSubmit={async (input: AirflowInstanceInput, _status: AirflowInstanceStatus) => {
                await createAirflowInstance(input)
                closeForm()
                bumpAirflowRevision()
              }}
            />
          </div>
        ) : null}
        {error !== null ? <p className="error-text catalog-alert">{error}</p> : null}
        {instances !== null && instances.length === 0 ? (
          <div className="catalog-empty">
            <h2>No Airflow instances yet</h2>
            <p>Add the REST API URL, username, and password for an Airflow 3 deployment.</p>
          </div>
        ) : null}
        {instances !== null && instances.length > 0 ? (
          <table className="columns">
            <thead>
              <tr>
                <th>Name</th>
                <th>URL</th>
                <th>Status</th>
                <th>DAGs</th>
                <th>Last sync</th>
                <th>Last error</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {instances.map((instance: AirflowInstance) => (
                <tr key={instance.id}>
                  <td>
                    <Link to={`/airflow/${instance.id}`}>{instance.name}</Link>
                  </td>
                  <td>{instance.base_url}</td>
                  <td>
                    <span className="badge">{instance.status}</span>
                  </td>
                  <td>{instance.dag_count}</td>
                  <td>{formatWhen(instance.last_synced_at)}</td>
                  <td>{instance.last_error ?? "—"}</td>
                  <td>
                    <button
                      type="button"
                      className="button secondary"
                      disabled={syncingId === instance.id || instance.status !== "ACTIVE"}
                      onClick={() => {
                        runSync(instance.id)
                      }}
                    >
                      {syncingId === instance.id ? "Syncing…" : "Sync"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>
    </Shell>
  )
}

const INSTANCE_TABS: readonly InstanceTab[] = ["overview", "dags", "mappings"]
type InstanceTab = "overview" | "dags" | "mappings"

export function AirflowInstancePage(): ReactElement {
  const instanceId: string = useParams().instanceId ?? ""
  const revision: number = useAirflowRevision()
  const navigate: NavigateFunction = useNavigate()
  const [tab, selectTab] = useTabParam<InstanceTab>(INSTANCE_TABS, "overview")
  const [instance, setInstance] = useState<AirflowInstance | null>(null)
  const [overview, setOverview] = useState<AirflowOverview | null>(null)
  const [dags, setDags] = useState<AirflowDag[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [syncing, setSyncing] = useState<boolean>(false)
  const [editing, setEditing] = useState<boolean>(false)
  const [confirmingDelete, setConfirmingDelete] = useState<boolean>(false)
  const [knowledge, setKnowledge] = useState<AirflowKnowledgeContext | null>(null)
  const [knowledgeError, setKnowledgeError] = useState<string | null>(null)
  const [building, setBuilding] = useState<boolean>(false)
  const [buildJobId, setBuildJobId] = useState<string | null>(null)
  useEffect(() => {
    const controller: AbortController = new AbortController()
    setOverview(null)
    setDags(null)
    setBuilding(false)
    setBuildJobId(null)
    void fetchAirflowInstance(instanceId, controller.signal)
      .then((loaded: AirflowInstance) => {
        setInstance(loaded)
        setError(null)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setError(errorMessage(err))
        }
      })
    void fetchAirflowDags(instanceId, controller.signal)
      .then((loaded: AirflowDag[]) => {
        setDags(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setDags([])
          setError(errorMessage(err))
        }
      })
    void fetchAirflowOverview(instanceId, controller.signal)
      .then((loaded: AirflowOverview) => {
        setOverview(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setOverview(null)
          setError(errorMessage(err))
        }
      })
    setKnowledge(null)
    setKnowledgeError(null)
    void fetchAirflowKnowledgeContext(instanceId, controller.signal)
      .then((loaded: AirflowKnowledgeContext | null) => {
        setKnowledge(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setKnowledge(null)
          setKnowledgeError(errorMessage(err))
        }
      })
    void fetchLatestAirflowBuild(instanceId, controller.signal)
      .then((job: AirflowBuildJob | null) => {
        if (controller.signal.aborted || job === null || job.status !== "running") {
          return
        }
        setBuilding(true)
        setBuildJobId(job.id)
        followAirflowBuild(job)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setKnowledgeError(errorMessage(err))
        }
      })
    return () => {
      controller.abort()
    }
  }, [instanceId, revision])
  useEffect(() => {
    return onAirflowBuildFinished((job: AirflowBuildJob) => {
      if (job.instance_id !== instanceId) {
        return
      }
      setBuilding(false)
      setBuildJobId(null)
      if (isCancelledBuild(job)) {
        return
      }
      if (job.status === "failed") {
        setKnowledgeError(job.error ?? "Airflow context build failed.")
        return
      }
      const controller: AbortController = new AbortController()
      void fetchAirflowKnowledgeContext(instanceId, controller.signal)
        .then((loaded: AirflowKnowledgeContext | null) => {
          setKnowledge(loaded)
          setKnowledgeError(null)
        })
        .catch((err: unknown) => {
          if (!isAbortError(err)) {
            setKnowledgeError(errorMessage(err))
          }
        })
    })
  }, [instanceId])

  function runSync(): void {
    setSyncing(true)
    setError(null)
    void syncAirflowInstance(instanceId)
      .then((run: AirflowSyncRun) => {
        if (run.sanitized_error !== null) {
          setError(run.sanitized_error)
        }
        bumpAirflowRevision()
      })
      .catch((err: unknown) => {
        setError(errorMessage(err))
        bumpAirflowRevision()
      })
      .finally(() => {
        setSyncing(false)
      })
  }

  function runDelete(): void {
    void deleteAirflowInstance(instanceId)
      .then(() => {
        bumpAirflowRevision()
        void navigate("/airflow")
      })
      .catch((err: unknown) => {
        setError(errorMessage(err))
      })
  }

  const mappings: AirflowDagTable[] = (dags ?? []).flatMap((dag: AirflowDag) => dag.tables)
  return (
    <Shell status={instance?.name ?? "Instance"} config={<AirflowConfigRail instanceId={instanceId} />}>
      <section className="catalog-page">
        <header className="catalog-hero">
          <div className="catalog-hero-top">
            <div className="catalog-hero-copy">
              <p className="crumb">
                <Link to="/airflow">Airflow</Link>
              </p>
              <h1>{instance?.name ?? "Instance"}</h1>
            </div>
            <div className="catalog-hero-actions">
              <button
                type="button"
                className="button secondary"
                disabled={instance === null || building}
                onClick={() => {
                  setBuilding(true)
                  setBuildJobId(null)
                  setKnowledgeError(null)
                  void requestAirflowBuild(instanceId, {
                    include_source: true,
                    include_previous: true,
                    include_summary: true,
                  })
                    .then((job: AirflowBuildJob) => {
                      setBuilding(job.status === "running")
                      setBuildJobId(job.status === "running" ? job.id : null)
                    })
                    .catch((err: unknown) => {
                      setBuilding(false)
                      setBuildJobId(null)
                      setKnowledgeError(errorMessage(err))
                    })
                }}
              >
                {building
                  ? "Building Airflow context…"
                  : knowledge === null
                    ? "Build Airflow context"
                    : "Rebuild Airflow context"}
              </button>
              <button
                type="button"
                className="button secondary"
                disabled={instance === null || instance.status !== "ACTIVE" || syncing}
                onClick={runSync}
              >
                {syncing ? "Syncing…" : "Sync"}
              </button>
              <button
                type="button"
                className="button secondary"
                disabled={instance === null}
                onClick={() => {
                  setEditing((open: boolean) => !open)
                  setConfirmingDelete(false)
                }}
              >
                {editing ? "Close" : "Edit"}
              </button>
              {confirmingDelete ? (
                <button type="button" className="button" onClick={runDelete}>
                  Confirm delete
                </button>
              ) : (
                <button
                  type="button"
                  className="button secondary"
                  disabled={instance === null}
                  onClick={() => {
                    setConfirmingDelete(true)
                  }}
                >
                  Delete
                </button>
              )}
            </div>
          </div>
          <p className="catalog-lead">{instance?.base_url ?? "Loading instance…"}</p>
        </header>
        {error !== null ? <p className="error-text catalog-alert">{error}</p> : null}
        {knowledgeError !== null ? <p className="error-text catalog-alert">{knowledgeError}</p> : null}
        {building ? (
          <BuildingBanner
            jobId={buildJobId}
            onCancelled={() => {
              setBuilding(false)
              setBuildJobId(null)
            }}
          />
        ) : null}
        {editing && instance !== null ? (
          <div className="catalog-panel">
            <InstanceForm
              submitLabel="Save instance"
              initial={instance}
              onCancel={() => {
                setEditing(false)
              }}
              onSubmit={async (input: AirflowInstanceInput, status: AirflowInstanceStatus) => {
                await updateAirflowInstance(instance.id, {
                  name: input.name,
                  base_url: input.base_url,
                  username: input.username,
                  verify_tls: input.verify_tls,
                  description: input.description,
                  status,
                  ...(input.password === "" ? {} : { password: input.password }),
                })
                setEditing(false)
                bumpAirflowRevision()
              }}
            />
          </div>
        ) : null}
        <div className="tabs" role="tablist">
          {INSTANCE_TABS.map((name: InstanceTab) => (
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
            <OverviewPanel instance={instance} overview={overview} />
            {instance !== null ? (
              <ContextEditor
                kind="airflow"
                id={instance.id}
                name={instance.name}
                context={instance.context}
                origin={instance.context_origin}
                onChanged={() => {
                  bumpAirflowRevision()
                }}
              />
            ) : null}
            {knowledge !== null ? (
              <AirflowContextPanel context={knowledge} />
            ) : (
              <p className="note">Build Airflow context to get a summary and per-DAG findings.</p>
            )}
          </>
        ) : null}
        {tab === "dags" ? <DagRows instanceId={instanceId} dags={dags} /> : null}
        {tab === "mappings" ? <MappingRows instanceId={instanceId} mappings={mappings} /> : null}
      </section>
    </Shell>
  )
}

function OverviewPanel(props: {
  instance: AirflowInstance | null
  overview: AirflowOverview | null
}): ReactElement {
  if (props.instance === null) {
    return <p className="note">Loading…</p>
  }
  return (
    <div>
      <dl className="facts">
        <dt>Username</dt>
        <dd>{props.instance.username}</dd>
        <dt>TLS verify</dt>
        <dd>{props.instance.verify_tls ? "Yes" : "No"}</dd>
        <dt>Status</dt>
        <dd>{props.instance.status}</dd>
        <dt>Last sync</dt>
        <dd>{formatWhen(props.instance.last_synced_at)}</dd>
        <dt>Description</dt>
        <dd>{props.instance.description ?? "None"}</dd>
      </dl>
      {props.overview === null ? <p className="note">Live metadata is unavailable.</p> : null}
      {props.overview !== null ? (
        <>
          <Section title="Version" error={props.overview.version_error}>
            {props.overview.version === null ? null : <p>{props.overview.version}</p>}
          </Section>
          <Section title="Health" error={props.overview.health_error}>
            {props.overview.health === null ? null : <HealthFacts health={props.overview.health} />}
          </Section>
          <Section title="Pools" error={props.overview.pools_error}>
            {props.overview.pools === null || props.overview.pools.length === 0 ? null : (
              <PoolTable pools={props.overview.pools} />
            )}
          </Section>
          <Section title="Providers" error={props.overview.providers_error}>
            {props.overview.providers === null || props.overview.providers.length === 0 ? null : (
              <ProviderTable providers={props.overview.providers} />
            )}
          </Section>
          <Section title="Plugins" error={props.overview.plugins_error}>
            {props.overview.plugins === null || props.overview.plugins.length === 0 ? null : (
              <PluginTable plugins={props.overview.plugins} />
            )}
          </Section>
          <Section title="Import errors" error={props.overview.import_errors_error}>
            {props.overview.import_errors === null || props.overview.import_errors.length === 0 ? null : (
              <ImportErrorList errors={props.overview.import_errors} />
            )}
          </Section>
        </>
      ) : null}
    </div>
  )
}

function HealthFacts(props: { health: AirflowHealth }): ReactElement {
  return (
    <dl className="facts">
      <dt>Metadatabase</dt>
      <dd>{props.health.metadatabase ?? "unknown"}</dd>
      <dt>Scheduler</dt>
      <dd>{props.health.scheduler ?? "unknown"}</dd>
      <dt>Triggerer</dt>
      <dd>{props.health.triggerer ?? "unknown"}</dd>
      <dt>DAG processor</dt>
      <dd>{props.health.dag_processor ?? "unknown"}</dd>
    </dl>
  )
}

function PoolTable(props: { pools: AirflowPool[] }): ReactElement {
  return (
    <table className="columns">
      <thead>
        <tr>
          <th>Pool</th>
          <th>Slots</th>
          <th>Open</th>
        </tr>
      </thead>
      <tbody>
        {props.pools.map((pool: AirflowPool) => (
          <tr key={pool.name}>
            <td>{pool.name}</td>
            <td>{pool.slots}</td>
            <td>{pool.open_slots}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ProviderTable(props: { providers: AirflowProvider[] }): ReactElement {
  return (
    <table className="columns">
      <thead>
        <tr>
          <th>Package</th>
          <th>Version</th>
        </tr>
      </thead>
      <tbody>
        {props.providers.map((provider: AirflowProvider) => (
          <tr key={provider.package_name}>
            <td>{provider.package_name}</td>
            <td>{provider.version}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function PluginTable(props: { plugins: AirflowPlugin[] }): ReactElement {
  return (
    <table className="columns">
      <thead>
        <tr>
          <th>Plugin</th>
          <th>Source</th>
        </tr>
      </thead>
      <tbody>
        {props.plugins.map((plugin: AirflowPlugin) => (
          <tr key={plugin.name}>
            <td>{plugin.name}</td>
            <td>{plugin.source ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ImportErrorList(props: { errors: AirflowImportError[] }): ReactElement {
  return (
    <ul className="registry-list">
      {props.errors.map((item: AirflowImportError) => (
        <li key={`${item.filename}-${item.timestamp ?? ""}`}>
          <strong>{item.filename}</strong>
          <span className="comment">{formatWhen(item.timestamp)}</span>
          {item.stack_trace !== null ? <pre className="source-code">{item.stack_trace}</pre> : null}
        </li>
      ))}
    </ul>
  )
}

function DagRows(props: { instanceId: string; dags: AirflowDag[] | null }): ReactElement {
  if (props.dags === null) {
    return <p className="note">Loading DAGs…</p>
  }
  if (props.dags.length === 0) {
    return <p className="note">No DAGs synced yet.</p>
  }
  return (
    <table className="columns">
      <thead>
        <tr>
          <th>DAG</th>
          <th>Paused</th>
          <th>Schedule</th>
          <th>Tags</th>
          <th>Owners</th>
          <th>Last run</th>
          <th>Tables</th>
        </tr>
      </thead>
      <tbody>
        {props.dags.map((dag: AirflowDag) => (
          <tr key={dag.dag_id}>
            <td>
              <Link to={`/airflow/${props.instanceId}/dags/${encodeURIComponent(dag.dag_id)}`}>{dag.dag_id}</Link>
            </td>
            <td>{dag.is_paused ? "Yes" : "No"}</td>
            <td>{dag.timetable_summary ?? dag.timetable_description ?? "—"}</td>
            <td>{dag.tags.length === 0 ? "—" : dag.tags.join(", ")}</td>
            <td>{dag.owners.length === 0 ? "—" : dag.owners.join(", ")}</td>
            <td>{dag.last_run === null ? "—" : <StatePill state={dag.last_run.state} />}</td>
            <td>{dag.tables.length}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function MappingRows(props: { instanceId: string; mappings: AirflowDagTable[] }): ReactElement {
  if (props.mappings.length === 0) {
    return <p className="note">No DAG to table mappings yet. Sync the instance or link a table from a DAG.</p>
  }
  return (
    <table className="columns">
      <thead>
        <tr>
          <th>DAG</th>
          <th>Table</th>
          <th>Origin</th>
          <th>Direction</th>
          <th>Evidence</th>
        </tr>
      </thead>
      <tbody>
        {props.mappings.map((mapping: AirflowDagTable) => (
          <tr key={mapping.id}>
            <td>
              <Link to={`/airflow/${props.instanceId}/dags/${encodeURIComponent(mapping.dag_id)}`}>
                {mapping.dag_id}
              </Link>
            </td>
            <td>
              <Link to={`/registry/tables/${mapping.catalog_table_id}`}>{mapping.qualified_name}</Link>
            </td>
            <td>
              <span className="badge">{mapping.origin}</span>
            </td>
            <td>
              <span className="badge">{mapping.direction}</span>
            </td>
            <td>{mapping.evidence ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

const DAG_TABS: readonly DagTab[] = ["overview", "tasks", "runs", "tables", "source"]
type DagTab = "overview" | "tasks" | "runs" | "tables" | "source"

export function AirflowDagPage(): ReactElement {
  const params: Readonly<Record<string, string | undefined>> = useParams()
  const instanceId: string = params.instanceId ?? ""
  const dagId: string = params.dagId ?? ""
  const [tab, selectTab] = useTabParam<DagTab>(DAG_TABS, "overview")
  const [reload, setReload] = useState<number>(0)
  const [dag, setDag] = useState<AirflowDagDetail | null>(null)
  const [runs, setRuns] = useState<AirflowDagRun[] | null>(null)
  const [finding, setFinding] = useState<AirflowDagFinding | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    const controller: AbortController = new AbortController()
    setDag(null)
    void fetchAirflowDag(instanceId, dagId, controller.signal)
      .then((loaded: AirflowDagDetail) => {
        setDag(loaded)
        setError(null)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setError(errorMessage(err))
        }
      })
    setFinding(null)
    void fetchAirflowKnowledgeContext(instanceId, controller.signal)
      .then((loaded: AirflowKnowledgeContext | null) => {
        const match: AirflowDagFinding | null =
          loaded?.findings.find((item: AirflowDagFinding) => item.dag_id === dagId) ?? null
        setFinding(match)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setFinding(null)
        }
      })
    return () => {
      controller.abort()
    }
  }, [instanceId, dagId, reload])
  useEffect(() => {
    if (tab !== "runs") {
      return
    }
    const controller: AbortController = new AbortController()
    setRuns(null)
    void fetchAirflowDagRuns(instanceId, dagId, controller.signal)
      .then((loaded: AirflowDagRun[]) => {
        setRuns(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setRuns([])
          setError(errorMessage(err))
        }
      })
    return () => {
      controller.abort()
    }
  }, [instanceId, dagId, tab])

  function refresh(): void {
    setReload((current: number) => current + 1)
    bumpAirflowRevision()
  }

  return (
    <Shell status={dag?.dag_id ?? dagId}>
      <section className="catalog-page">
        <header className="catalog-hero">
          <div className="catalog-hero-copy">
            <p className="crumb">
              <Link to="/airflow">Airflow</Link>
              {" / "}
              <Link to={`/airflow/${instanceId}`}>Instance</Link>
            </p>
            <h1>{dag?.display_name ?? dag?.dag_id ?? dagId}</h1>
          </div>
          <p className="catalog-lead">{dag?.description ?? "Synced DAG snapshot."}</p>
        </header>
        {error !== null ? <p className="error-text catalog-alert">{error}</p> : null}
        <div className="tabs" role="tablist">
          {DAG_TABS.map((name: DagTab) => (
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
        {tab === "overview" && dag !== null ? (
          <>
            <DagFacts dag={dag} />
            {finding !== null ? (
              <section className="context-block" aria-label="DAG finding">
                <h2>Pipeline</h2>
                <p>{finding.summary === "" ? "No summary." : finding.summary}</p>
                <ul className="registry-list">
                  {finding.links.map((link: AirflowDagLink) => (
                    <li key={`${link.table_id}-${link.direction}`}>
                      <span className="badge">{link.direction}</span>
                      <Link to={`/registry/tables/${link.table_id}`}>{link.table_id}</Link>
                      <span className="comment">{link.reason}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : (
              <p className="note">Build Airflow context on the instance to explain this DAG.</p>
            )}
          </>
        ) : null}
        {tab === "tasks" ? <TaskTable tasks={dag?.tasks ?? null} /> : null}
        {tab === "runs" ? <RunTable runs={runs} /> : null}
        {tab === "tables" && dag !== null ? (
          <div>
            <MappingRows instanceId={instanceId} mappings={dag.tables} />
            <LinkTableForm
              onLink={async (tableId: string, direction: AirflowMappingDirection) => {
                await addDagTable(instanceId, dagId, tableId, direction)
                refresh()
              }}
              onRemove={async (tableId: string) => {
                await removeDagTable(instanceId, dagId, tableId)
                refresh()
              }}
              mappings={dag.tables}
            />
          </div>
        ) : null}
        {tab === "source" ? (
          dag?.source_code ? (
            <pre className="source-code">{dag.source_code}</pre>
          ) : (
            <p className="note">No source stored. Sync the instance to capture it.</p>
          )
        ) : null}
      </section>
    </Shell>
  )
}

function AirflowContextPanel(props: { context: AirflowKnowledgeContext }): ReactElement {
  const context: AirflowKnowledgeContext = props.context
  return (
    <section className="context-block" aria-label="Airflow context">
      <h2>Airflow context</h2>
      <p>{context.summary ?? "No summary yet."}</p>
      <dl className="facts">
        <dt>DAGs analyzed</dt>
        <dd>{context.dags_analyzed}</dd>
        <dt>Findings</dt>
        <dd>{context.findings.length}</dd>
        <dt>Previous links kept</dt>
        <dd>{context.previous_links_kept}</dd>
      </dl>
      {context.findings.map((finding: AirflowDagFinding) => (
        <div key={finding.dag_row_id}>
          <h3>{finding.dag_id}</h3>
          <p>{finding.summary === "" ? "No summary." : finding.summary}</p>
          {finding.links.length === 0 ? <p className="note">No table links.</p> : null}
          <ul className="registry-list">
            {finding.links.map((link: AirflowDagLink) => (
              <li key={`${link.table_id}-${link.direction}`}>
                <span className="badge">{link.direction}</span>
                <Link to={`/registry/tables/${link.table_id}`}>{link.table_id}</Link>
                <span className="comment">{link.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  )
}

function DagFacts(props: { dag: AirflowDagDetail }): ReactElement {
  const dag: AirflowDagDetail = props.dag
  return (
    <div>
      <dl className="facts">
        <dt>Paused</dt>
        <dd>{dag.is_paused ? "Yes" : "No"}</dd>
        <dt>Schedule</dt>
        <dd>{dag.timetable_description ?? dag.timetable_summary ?? "—"}</dd>
        <dt>Owners</dt>
        <dd>{dag.owners.length === 0 ? "—" : dag.owners.join(", ")}</dd>
        <dt>Tags</dt>
        <dd>{dag.tags.length === 0 ? "—" : dag.tags.join(", ")}</dd>
        <dt>File</dt>
        <dd>{dag.fileloc ?? "—"}</dd>
        <dt>Bundle</dt>
        <dd>{dag.bundle_name ?? "—"}</dd>
        <dt>Parsed</dt>
        <dd>{formatWhen(dag.last_parsed_time)}</dd>
        <dt>Import errors</dt>
        <dd>{dag.has_import_errors ? "Yes" : "No"}</dd>
      </dl>
      {dag.run_stats.length > 0 ? (
        <p>
          {dag.run_stats.map((stat: AirflowRunStat) => (
            <span key={stat.state} className="badge">
              {stat.state} {stat.count}
            </span>
          ))}
        </p>
      ) : null}
    </div>
  )
}

function TaskTable(props: { tasks: AirflowTask[] | null }): ReactElement {
  if (props.tasks === null) {
    return <p className="note">Loading tasks…</p>
  }
  if (props.tasks.length === 0) {
    return <p className="note">No tasks stored.</p>
  }
  return (
    <table className="columns">
      <thead>
        <tr>
          <th>Task</th>
          <th>Operator</th>
          <th>Downstream</th>
        </tr>
      </thead>
      <tbody>
        {props.tasks.map((task: AirflowTask) => (
          <tr key={task.task_id}>
            <td>{task.task_id}</td>
            <td>{task.operator_name ?? "—"}</td>
            <td>{task.downstream_task_ids.length === 0 ? "—" : task.downstream_task_ids.join(", ")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function RunTable(props: { runs: AirflowDagRun[] | null }): ReactElement {
  if (props.runs === null) {
    return <p className="note">Loading runs…</p>
  }
  if (props.runs.length === 0) {
    return <p className="note">No runs.</p>
  }
  return (
    <table className="columns">
      <thead>
        <tr>
          <th>Run</th>
          <th>State</th>
          <th>Type</th>
          <th>Start</th>
          <th>End</th>
          <th>Duration</th>
        </tr>
      </thead>
      <tbody>
        {props.runs.map((run: AirflowDagRun) => (
          <tr key={run.dag_run_id}>
            <td>{run.dag_run_id}</td>
            <td>
              <StatePill state={run.state} />
            </td>
            <td>{run.run_type ?? "—"}</td>
            <td>{formatWhen(run.start_date)}</td>
            <td>{formatWhen(run.end_date)}</td>
            <td>{formatDuration(run.duration)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function LinkTableForm(props: {
  mappings: AirflowDagTable[]
  onLink: (tableId: string, direction: AirflowMappingDirection) => Promise<void>
  onRemove: (tableId: string) => Promise<void>
}): ReactElement {
  const [query, setQuery] = useState<string>("")
  const [hits, setHits] = useState<SearchHitRecord[] | null>(null)
  const [direction, setDirection] = useState<AirflowMappingDirection>("UNKNOWN")
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState<boolean>(false)
  const manual: AirflowDagTable[] = props.mappings.filter((mapping: AirflowDagTable) => mapping.origin === "MANUAL")

  function onSearch(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const controller: AbortController = new AbortController()
    setError(null)
    void searchCatalog(query, controller.signal)
      .then((loaded: SearchHitRecord[]) => {
        setHits(loaded.filter((hit: SearchHitRecord) => hit.kind === "table"))
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setError(errorMessage(err))
        }
      })
  }

  return (
    <div className="catalog-panel">
      <h2>Link a catalog table</h2>
      <form className="search-form" onSubmit={onSearch}>
        <input
          className="search"
          value={query}
          aria-label="Search catalog tables"
          placeholder="Search catalog tables"
          onChange={(event) => {
            setQuery(event.target.value)
          }}
        />
        <select
          aria-label="Direction"
          value={direction}
          onChange={(event) => {
            setDirection(event.target.value as AirflowMappingDirection)
          }}
        >
          <option value="UNKNOWN">Unknown</option>
          <option value="INPUT">Input</option>
          <option value="OUTPUT">Output</option>
        </select>
        <button className="button" type="submit" disabled={query.trim() === ""}>
          Search
        </button>
      </form>
      {error !== null ? <p className="error-text">{error}</p> : null}
      {hits !== null && hits.length === 0 ? <p className="note">No tables match.</p> : null}
      <ul className="registry-list">
        {(hits ?? []).map((hit: SearchHitRecord) => (
          <li key={hit.id}>
            <span>{hit.qualified_name}</span>
            <button
              type="button"
              className="button secondary"
              disabled={pending}
              onClick={() => {
                setPending(true)
                setError(null)
                void props
                  .onLink(hit.id, direction)
                  .catch((err: unknown) => {
                    setError(errorMessage(err))
                  })
                  .finally(() => {
                    setPending(false)
                  })
              }}
            >
              Link
            </button>
          </li>
        ))}
      </ul>
      {manual.length > 0 ? <h2>Manual links</h2> : null}
      <ul className="registry-list">
        {manual.map((mapping: AirflowDagTable) => (
          <li key={mapping.id}>
            <Link to={`/registry/tables/${mapping.catalog_table_id}`}>{mapping.qualified_name}</Link>
            <span className="badge">{mapping.direction}</span>
            <button
              type="button"
              className="button secondary"
              disabled={pending}
              onClick={() => {
                setPending(true)
                setError(null)
                void props
                  .onRemove(mapping.catalog_table_id)
                  .catch((err: unknown) => {
                    setError(errorMessage(err))
                  })
                  .finally(() => {
                    setPending(false)
                  })
              }}
            >
              Remove
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

function InstanceForm(props: {
  submitLabel: string
  initial?: AirflowInstance
  onCancel: () => void
  onSubmit: (input: AirflowInstanceInput, status: AirflowInstanceStatus) => Promise<void>
}): ReactElement {
  const initial: AirflowInstance | undefined = props.initial
  const [name, setName] = useState<string>(initial?.name ?? "")
  const [baseUrl, setBaseUrl] = useState<string>(initial?.base_url ?? "http://localhost:8082")
  const [username, setUsername] = useState<string>(initial?.username ?? "admin")
  const [password, setPassword] = useState<string>("")
  const [verifyTls, setVerifyTls] = useState<boolean>(initial?.verify_tls ?? true)
  const [status, setStatus] = useState<AirflowInstanceStatus>(initial?.status ?? "ACTIVE")
  const [description, setDescription] = useState<string>(initial?.description ?? "")
  const [test, setTest] = useState<AirflowConnectionTest | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<"save" | "test" | null>(null)
  const pending: boolean = busy !== null
  const passwordRequired: boolean = initial === undefined
  const ready: boolean =
    name.trim() !== "" && baseUrl.trim() !== "" && username.trim() !== "" && (!passwordRequired || password !== "")

  function input(): AirflowInstanceInput {
    const descriptionText: string = description.trim()
    return {
      name: name.trim(),
      base_url: baseUrl.trim(),
      username: username.trim(),
      password,
      verify_tls: verifyTls,
      description: descriptionText === "" ? null : descriptionText,
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (!ready || pending) {
      return
    }
    setBusy("save")
    setError(null)
    void props
      .onSubmit(input(), status)
      .catch((err: unknown) => {
        setError(errorMessage(err))
        setBusy(null)
      })
  }

  return (
    <form className="create-form" onSubmit={onSubmit}>
      <label htmlFor="airflow-name">
        Name
        <input
          id="airflow-name"
          value={name}
          maxLength={255}
          disabled={pending}
          onChange={(event) => {
            setName(event.target.value)
          }}
        />
      </label>
      <label htmlFor="airflow-url">
        API URL
        <input
          id="airflow-url"
          value={baseUrl}
          maxLength={2048}
          disabled={pending}
          onChange={(event) => {
            setBaseUrl(event.target.value)
          }}
        />
      </label>
      <label htmlFor="airflow-username">
        Username
        <input
          id="airflow-username"
          value={username}
          maxLength={255}
          disabled={pending}
          autoComplete="off"
          onChange={(event) => {
            setUsername(event.target.value)
          }}
        />
      </label>
      <label htmlFor="airflow-password">
        {passwordRequired ? "Password" : "Password (blank keeps the stored one)"}
        <input
          id="airflow-password"
          type="password"
          value={password}
          maxLength={1024}
          disabled={pending}
          autoComplete="new-password"
          onChange={(event) => {
            setPassword(event.target.value)
          }}
        />
      </label>
      {initial !== undefined ? (
        <label htmlFor="airflow-status">
          Status
          <select
            id="airflow-status"
            value={status}
            disabled={pending}
            onChange={(event) => {
              setStatus(event.target.value === "DISABLED" ? "DISABLED" : "ACTIVE")
            }}
          >
            <option value="ACTIVE">ACTIVE</option>
            <option value="DISABLED">DISABLED</option>
          </select>
        </label>
      ) : null}
      <label className="check-row" htmlFor="airflow-tls">
        <input
          id="airflow-tls"
          type="checkbox"
          checked={verifyTls}
          disabled={pending}
          onChange={(event) => {
            setVerifyTls(event.target.checked)
          }}
        />
        Verify TLS
      </label>
      <label htmlFor="airflow-description">
        Description
        <textarea
          id="airflow-description"
          value={description}
          maxLength={16000}
          rows={3}
          disabled={pending}
          onChange={(event) => {
            setDescription(event.target.value)
          }}
        />
      </label>
      {test !== null ? <ConnectionResult result={test} /> : null}
      {error !== null ? <p className="error-text">{error}</p> : null}
      <div className="create-actions">
        <button className="button" type="submit" disabled={pending || !ready}>
          {busy === "save" ? "Saving…" : props.submitLabel}
        </button>
        <button
          className="button secondary"
          type="button"
          disabled={pending || !ready || password === ""}
          onClick={() => {
            setBusy("test")
            setError(null)
            void testAirflowInstance(input())
              .then((result: AirflowConnectionTest) => {
                setTest(result)
              })
              .catch((err: unknown) => {
                setError(errorMessage(err))
              })
              .finally(() => {
                setBusy(null)
              })
          }}
        >
          {busy === "test" ? "Testing…" : "Test connection"}
        </button>
        <button className="button secondary" type="button" disabled={pending} onClick={props.onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}

function ConnectionResult(props: { result: AirflowConnectionTest }): ReactElement {
  if (!props.result.ok) {
    return <p className="error-text">{props.result.error ?? "Connection failed."}</p>
  }
  return (
    <p className="comment">
      Connected. Airflow {props.result.version ?? "unknown"}. Scheduler{" "}
      {props.result.health?.scheduler ?? "unknown"}.
    </p>
  )
}
