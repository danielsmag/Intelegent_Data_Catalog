import { useEffect, useMemo, useState, type ChangeEvent, type ReactElement } from "react"
import { Link, useNavigate, useParams, type NavigateFunction } from "react-router-dom"
import { fetchAirflowTree, type AirflowDag, type AirflowTree } from "./airflowApi"
import { bumpAirflowRevision, useAirflowRevision } from "./airflowSidebarStore"
import { errorMessage, isAbortError } from "./api"
import { AirflowIcon, Chevron, PanelChevron, PlusIcon, RefreshIcon, SchemaIcon, SearchIcon } from "./icons"
import { ResizeHandle } from "./ResizeHandle"

type AirflowSidebarProps = {
  collapsed: boolean
  onToggleCollapsed: () => void
}

export function AirflowSidebar(props: AirflowSidebarProps): ReactElement {
  const revision: number = useAirflowRevision()
  const navigate: NavigateFunction = useNavigate()
  const params: Readonly<Record<string, string | undefined>> = useParams()
  const selectedInstance: string | undefined = params.instanceId
  const selectedDag: string | undefined = params.dagId
  const [tree, setTree] = useState<AirflowTree[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [query, setQuery] = useState<string>("")
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())
  const searching: boolean = query.trim() !== ""
  const visible: AirflowTree[] = useMemo(() => filterTree(tree ?? [], query), [tree, query])

  useEffect(() => {
    const controller: AbortController = new AbortController()
    setLoading(true)
    void fetchAirflowTree(controller.signal)
      .then((loaded: AirflowTree[]) => {
        setTree(loaded)
        setError(null)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setError(errorMessage(err))
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false)
        }
      })
    return () => {
      controller.abort()
    }
  }, [revision])

  useEffect(() => {
    if (selectedInstance === undefined) {
      return
    }
    setCollapsed((current: ReadonlySet<string>) => {
      const next: Set<string> = new Set(current)
      next.delete(`instance:${selectedInstance}`)
      return next
    })
  }, [selectedInstance])

  function toggle(key: string): void {
    if (searching) {
      return
    }
    setCollapsed((current: ReadonlySet<string>) => {
      const next: Set<string> = new Set(current)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
      }
      return next
    })
  }

  function closed(key: string): boolean {
    if (searching) {
      return false
    }
    return collapsed.has(key)
  }

  const sidebarClass: string = props.collapsed ? "sidebar hierarchy collapsed" : "sidebar hierarchy"
  return (
    <aside className={sidebarClass} aria-label="Airflow">
      <div className="sidebar-head">
        {props.collapsed ? (
          <span className="sidebar-rail-label">Airflow</span>
        ) : (
          <div className="sidebar-title">
            <span>Airflow</span>
          </div>
        )}
        <div className="sidebar-actions">
          {props.collapsed ? null : (
            <button
              type="button"
              className="sidebar-toggle"
              aria-label="Add Airflow instance"
              title="Add instance"
              onClick={() => {
                void navigate("/airflow?new=1")
              }}
            >
              <PlusIcon />
            </button>
          )}
          {props.collapsed ? null : (
            <button
              type="button"
              className="sidebar-toggle"
              aria-label="Refresh Airflow"
              title="Refresh"
              onClick={() => {
                bumpAirflowRevision()
              }}
            >
              <RefreshIcon />
            </button>
          )}
          <button
            type="button"
            className="sidebar-toggle"
            aria-expanded={!props.collapsed}
            aria-label={props.collapsed ? "Expand Airflow sidebar" : "Minimize Airflow sidebar"}
            title={props.collapsed ? "Expand sidebar" : "Minimize sidebar"}
            onClick={props.onToggleCollapsed}
          >
            <PanelChevron direction={props.collapsed ? "right" : "left"} />
          </button>
        </div>
      </div>
      {props.collapsed ? null : (
        <>
          <label className="search-field">
            <SearchIcon />
            <input
              className="search"
              type="search"
              placeholder="Filter instances and DAGs"
              value={query}
              aria-label="Filter instances and DAGs"
              onChange={(event: ChangeEvent<HTMLInputElement>) => {
                setQuery(event.target.value)
              }}
            />
          </label>
          <div className="tree" role="tree">
            {loading && tree === null ? <p className="tree-status">Loading Airflow…</p> : null}
            {error !== null ? <p className="tree-status">{error}</p> : null}
            {tree !== null && visible.length === 0 ? (
              <p className="tree-status">{searching ? "No matches." : "No instances yet."}</p>
            ) : null}
            {visible.map((node: AirflowTree) => {
              const key: string = `instance:${node.instance.id}`
              const instanceClosed: boolean = closed(key)
              const current: boolean = selectedInstance === node.instance.id && selectedDag === undefined
              return (
                <div key={node.instance.id} role="treeitem" aria-expanded={!instanceClosed}>
                  <div className={current ? "tree-row depth-catalog current-branch" : "tree-row depth-catalog"}>
                    <button
                      type="button"
                      className="chevron"
                      aria-label={instanceClosed ? `Expand ${node.instance.name}` : `Collapse ${node.instance.name}`}
                      onClick={() => {
                        toggle(key)
                      }}
                    >
                      <Chevron open={!instanceClosed} />
                    </button>
                    <Link
                      to={`/airflow/${node.instance.id}`}
                      className="tree-link figure-catalog"
                      aria-current={current ? "page" : undefined}
                    >
                      <AirflowIcon />
                      <span className="tree-label">{node.instance.name}</span>
                    </Link>
                  </div>
                  {instanceClosed
                    ? null
                    : node.dags.map((dag: AirflowDag) => {
                        const dagCurrent: boolean =
                          selectedInstance === node.instance.id && selectedDag === dag.dag_id
                        return (
                          <Link
                            key={dag.dag_id}
                            to={`/airflow/${node.instance.id}/dags/${encodeURIComponent(dag.dag_id)}`}
                            className="tree-row depth-schema"
                            role="treeitem"
                            aria-current={dagCurrent ? "page" : undefined}
                          >
                            <span className="chevron-spacer" />
                            <SchemaIcon />
                            <span className="tree-label figure-scope">{dag.dag_id}</span>
                          </Link>
                        )
                      })}
                </div>
              )
            })}
          </div>
        </>
      )}
      {props.collapsed ? null : <ResizeHandle pane="sidebar" edge="end" label="Resize sidebar" />}
    </aside>
  )
}

function filterTree(tree: AirflowTree[], query: string): AirflowTree[] {
  const needle: string = query.trim().toLowerCase()
  if (needle === "") {
    return tree
  }
  const found: AirflowTree[] = []
  for (const node of tree) {
    if (node.instance.name.toLowerCase().includes(needle)) {
      found.push(node)
      continue
    }
    const dags: AirflowDag[] = node.dags.filter((dag: AirflowDag) => dag.dag_id.toLowerCase().includes(needle))
    if (dags.length > 0) {
      found.push({ instance: node.instance, dags })
    }
  }
  return found
}
