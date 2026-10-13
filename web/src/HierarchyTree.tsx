import { useEffect, useMemo, useState, type ChangeEvent, type ReactElement, type ReactNode } from "react"
import { Link, useLocation, useNavigate, useParams, type Location, type NavigateFunction } from "react-router-dom"
import { errorMessage, isAbortError } from "./api"
import { bumpCatalogRevision, useCatalogRevision } from "./catalogRevision"
import { CreateRecordForm } from "./CreateRecordForm"
import { AirflowIcon, Chevron, PanelChevron, PlusIcon, ScopeIcon, SearchIcon, SpaceIcon, TableIcon } from "./icons"
import { ResizeHandle } from "./ResizeHandle"
import {
  createScope,
  createSpace,
  fetchHierarchy,
  type HierarchyAirflow,
  type HierarchyDag,
  type HierarchyScope,
  type HierarchySpace,
  type HierarchyTable,
} from "./registryApi"

type HierarchyTreeProps = {
  title: string
  collapsed: boolean
  onToggleCollapsed: () => void
}

export function HierarchyTree(props: HierarchyTreeProps): ReactElement {
  const [spaces, setSpaces] = useState<HierarchySpace[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [query, setQuery] = useState<string>("")
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())
  const [addingSpace, setAddingSpace] = useState<boolean>(false)
  const [addingScopeId, setAddingScopeId] = useState<string | null>(null)
  const revision: number = useCatalogRevision()
  const navigate: NavigateFunction = useNavigate()
  const params: Readonly<Record<string, string | undefined>> = useParams()
  const location: Location = useLocation()
  const path: string = location.pathname
  const selectedSpace: string | undefined = path.startsWith("/spaces/") ? params.spaceId : undefined
  const selectedScope: string | undefined = path.startsWith("/scopes/") ? params.scopeId : undefined
  const selectedTable: string | undefined = path.startsWith("/registry/tables/") ? params.tableId : undefined
  const searching: boolean = query.trim() !== ""
  const visible: HierarchySpace[] = useMemo(
    () => filterSpaces(spaces ?? [], query),
    [spaces, query],
  )

  useEffect(() => {
    const controller: AbortController = new AbortController()
    setError(null)
    void fetchHierarchy(controller.signal)
      .then((loaded: HierarchySpace[]) => {
        setSpaces(loaded)
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) {
          return
        }
        setError(errorMessage(err))
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
    <aside className={sidebarClass} aria-label="Spaces">
      <div className="sidebar-head">
        {props.collapsed ? (
          <span className="sidebar-rail-label">{props.title}</span>
        ) : (
          <div className="sidebar-title">
            <span>{props.title}</span>
          </div>
        )}
        <div className="sidebar-actions">
          {props.collapsed ? null : (
            <button
              type="button"
              className="sidebar-toggle"
              aria-expanded={addingSpace}
              aria-label={addingSpace ? "Close add space" : "Add space"}
              title={addingSpace ? "Close add space" : "Add space"}
              onClick={() => {
                setAddingScopeId(null)
                setAddingSpace((open: boolean) => !open)
              }}
            >
              <PlusIcon />
            </button>
          )}
          <button
            type="button"
            className="sidebar-toggle"
            aria-expanded={!props.collapsed}
            aria-label={props.collapsed ? "Expand spaces sidebar" : "Minimize spaces sidebar"}
            title={props.collapsed ? "Expand sidebar" : "Minimize sidebar"}
            onClick={props.onToggleCollapsed}
          >
            <PanelChevron direction={props.collapsed ? "right" : "left"} />
          </button>
        </div>
      </div>
      {props.collapsed ? null : (
        <>
      <div className="legend" aria-label="Hierarchy">
        <span className="figure-space">
          <SpaceIcon /> Space
        </span>
        <span className="figure-scope">
          <ScopeIcon /> Scope
        </span>
        <span className="figure-table">
          <TableIcon /> Table
        </span>
        <span className="figure-airflow">
          <AirflowIcon /> Airflow
        </span>
      </div>
      <label className="search-field">
        <SearchIcon />
        <input
          className="search"
          type="search"
          placeholder="Filter hierarchy"
          value={query}
          aria-label="Filter spaces, scopes, tables, and Airflow"
          onChange={(event: ChangeEvent<HTMLInputElement>) => {
            setQuery(event.target.value)
          }}
        />
      </label>
      {addingSpace ? (
        <CreateRecordForm
          idPrefix="tree-space"
          nameLabel="Space name"
          submitLabel="Add space"
          compact
          onCancel={() => {
            setAddingSpace(false)
          }}
          onCreate={async (input) => {
            const created = await createSpace(input)
            setAddingSpace(false)
            bumpCatalogRevision()
            navigate(`/spaces/${created.id}`)
          }}
        />
      ) : null}
      <div className="tree" role="tree">
        {loading ? <p className="tree-status">Loading spaces…</p> : null}
        {error !== null ? <p className="tree-status error-text">{error}</p> : null}
        {!loading && error === null && visible.length === 0 ? (
          <p className="tree-status">{searching ? "No matches." : "No spaces yet."}</p>
        ) : null}
        {visible.map((space: HierarchySpace) => {
          const spaceKey: string = `space:${space.id}`
          const spaceClosed: boolean = closed(spaceKey)
          const spaceSelected: boolean = selectedSpace === space.id
          return (
            <div key={space.id} role="treeitem" aria-expanded={!spaceClosed}>
              <div className={spaceSelected ? "tree-row depth-catalog current-branch" : "tree-row depth-catalog"}>
                <button
                  type="button"
                  className="chevron"
                  aria-label={spaceClosed ? `Expand ${space.name}` : `Collapse ${space.name}`}
                  onClick={() => {
                    toggle(spaceKey)
                  }}
                >
                  <Chevron open={!spaceClosed} />
                </button>
                <Link to={`/spaces/${space.id}`} className="tree-link figure-space" aria-current={spaceSelected ? "page" : undefined}>
                  <SpaceIcon />
                  <span className="tree-label">{space.name}</span>
                </Link>
                <button
                  type="button"
                  className="tree-add"
                  aria-expanded={addingScopeId === space.id}
                  aria-label={addingScopeId === space.id ? `Close add scope in ${space.name}` : `Add scope in ${space.name}`}
                  title="Add scope"
                  onClick={() => {
                    setAddingSpace(false)
                    setAddingScopeId((current: string | null) => (current === space.id ? null : space.id))
                  }}
                >
                  <PlusIcon />
                </button>
              </div>
              {addingScopeId === space.id ? (
                <CreateRecordForm
                  idPrefix={`tree-scope-${space.id}`}
                  nameLabel="Scope name"
                  submitLabel="Add scope"
                  compact
                  onCancel={() => {
                    setAddingScopeId(null)
                  }}
                  onCreate={async (input) => {
                    const created = await createScope(space.id, input)
                    setAddingScopeId(null)
                    setCollapsed((current: ReadonlySet<string>) => {
                      const next: Set<string> = new Set(current)
                      next.delete(spaceKey)
                      return next
                    })
                    bumpCatalogRevision()
                    navigate(`/scopes/${created.id}`)
                  }}
                />
              ) : null}
              {spaceClosed ? null : (
                <>
                  {space.scopes.map((scope: HierarchyScope) => {
                    const scopeKey: string = `scope:${scope.id}`
                    const scopeClosed: boolean = closed(scopeKey)
                    const scopeSelected: boolean = selectedScope === scope.id
                    return (
                      <div key={scope.id} role="treeitem" aria-expanded={!scopeClosed}>
                        <div className={scopeSelected ? "tree-row depth-schema current-branch" : "tree-row depth-schema"}>
                          <button
                            type="button"
                            className="chevron"
                            aria-label={scopeClosed ? `Expand ${scope.name}` : `Collapse ${scope.name}`}
                            onClick={() => {
                              toggle(scopeKey)
                            }}
                          >
                            <Chevron open={!scopeClosed} />
                          </button>
                          <Link
                            to={`/scopes/${scope.id}`}
                            className="tree-link figure-scope"
                            aria-current={scopeSelected ? "page" : undefined}
                          >
                            <ScopeIcon />
                            <span className="tree-label">{scope.name}</span>
                          </Link>
                        </div>
                        {scopeClosed ? null : (
                          <>
                            {scope.tables.length > 0 ? (
                              <TreeGroup
                                groupKey={`tables:${scope.id}`}
                                label="Iceberg tables"
                                depth="depth-table"
                                figure="figure-table"
                                icon={<TableIcon />}
                                closed={closed(`tables:${scope.id}`)}
                                onToggle={toggle}
                              >
                                {scope.tables.map((table: HierarchyTable) => {
                                  const tableSelected: boolean = selectedTable === table.id
                                  return (
                                    <Link
                                      key={table.id}
                                      to={`/registry/tables/${table.id}`}
                                      className="tree-row depth-airflow figure-table"
                                      role="treeitem"
                                      aria-current={tableSelected ? "page" : undefined}
                                    >
                                      <span className="chevron-spacer" />
                                      <TableIcon />
                                      <span className="tree-label">{table.name}</span>
                                    </Link>
                                  )
                                })}
                              </TreeGroup>
                            ) : null}
                            {scope.airflow.length > 0 ? (
                              <TreeGroup
                                groupKey={`airflow-group:${scope.id}`}
                                label="Airflow"
                                depth="depth-table"
                                figure="figure-airflow"
                                icon={<AirflowIcon />}
                                closed={closed(`airflow-group:${scope.id}`)}
                                onToggle={toggle}
                              >
                                <AirflowNodes
                                  nodes={scope.airflow}
                                  parentKey={scope.id}
                                  ownerPath={`/scopes/${scope.id}`}
                                  instanceDepth="depth-airflow"
                                  dagDepth="depth-dag"
                                  collapsed={closed}
                                  onToggle={toggle}
                                  location={location}
                                />
                              </TreeGroup>
                            ) : null}
                          </>
                        )}
                      </div>
                    )
                  })}
                  {space.airflow.length > 0 ? (
                    <TreeGroup
                      groupKey={`airflow-group:${space.id}`}
                      label="Airflow"
                      depth="depth-schema"
                      figure="figure-airflow"
                      icon={<AirflowIcon />}
                      closed={closed(`airflow-group:${space.id}`)}
                      onToggle={toggle}
                    >
                      <AirflowNodes
                        nodes={space.airflow}
                        parentKey={space.id}
                        ownerPath={`/spaces/${space.id}`}
                        instanceDepth="depth-table"
                        dagDepth="depth-airflow"
                        collapsed={closed}
                        onToggle={toggle}
                        location={location}
                      />
                    </TreeGroup>
                  ) : null}
                </>
              )}
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

function TreeGroup(props: {
  groupKey: string
  label: string
  depth: string
  figure: string
  icon: ReactElement
  closed: boolean
  onToggle: (key: string) => void
  children: ReactNode
}): ReactElement {
  return (
    <div role="treeitem" aria-expanded={!props.closed}>
      <button
        type="button"
        className={`tree-row tree-group ${props.depth} ${props.figure}`}
        aria-expanded={!props.closed}
        onClick={() => {
          props.onToggle(props.groupKey)
        }}
      >
        <span className="chevron">
          <Chevron open={!props.closed} />
        </span>
        {props.icon}
        <span className="tree-label">{props.label}</span>
      </button>
      {props.closed ? null : <div role="group">{props.children}</div>}
    </div>
  )
}

function AirflowNodes(props: {
  nodes: HierarchyAirflow[]
  parentKey: string
  ownerPath: string
  instanceDepth: string
  dagDepth: string
  collapsed: (key: string) => boolean
  onToggle: (key: string) => void
  location: Location
}): ReactElement | null {
  if (props.nodes.length === 0) {
    return null
  }
  const onOwner: boolean = props.location.pathname === props.ownerPath
  const search: URLSearchParams = new URLSearchParams(props.location.search)
  const onAirflowTab: boolean = onOwner && search.get("tab") === "airflow"
  const selectedInstance: string | null = search.get("instance")
  const selectedDag: string | null = search.get("dag")
  return (
    <>
      {props.nodes.map((instance: HierarchyAirflow) => {
        const instanceKey: string = `airflow:${props.parentKey}:${instance.instanceId}`
        const instanceClosed: boolean = props.collapsed(instanceKey)
        const instancePath: string = `${props.ownerPath}?tab=airflow&instance=${encodeURIComponent(instance.instanceId)}`
        const instanceSelected: boolean =
          onAirflowTab && selectedInstance === instance.instanceId && selectedDag === null
        return (
          <div key={instance.instanceId} role="treeitem" aria-expanded={!instanceClosed}>
            <div className={instanceSelected ? `tree-row ${props.instanceDepth} current-branch` : `tree-row ${props.instanceDepth}`}>
              <button
                type="button"
                className="chevron"
                aria-label={instanceClosed ? `Expand ${instance.name}` : `Collapse ${instance.name}`}
                onClick={() => {
                  props.onToggle(instanceKey)
                }}
              >
                <Chevron open={!instanceClosed} />
              </button>
              <Link
                to={instancePath}
                className="tree-link figure-airflow"
                aria-current={instanceSelected ? "page" : undefined}
              >
                <AirflowIcon />
                <span className="tree-label">{instance.name}</span>
              </Link>
            </div>
            {instanceClosed
              ? null
              : instance.dags.map((dag: HierarchyDag) => {
                  const dagPath: string = `${instancePath}&dag=${encodeURIComponent(dag.dagId)}`
                  const dagSelected: boolean =
                    onAirflowTab && selectedInstance === instance.instanceId && selectedDag === dag.dagId
                  return (
                    <Link
                      key={dag.dagRowId}
                      to={dagPath}
                      className={`tree-row ${props.dagDepth} figure-airflow`}
                      role="treeitem"
                      aria-current={dagSelected ? "page" : undefined}
                    >
                      <span className="chevron-spacer" />
                      <AirflowIcon />
                      <span className="tree-label">{dag.dagId}</span>
                    </Link>
                  )
                })}
          </div>
        )
      })}
    </>
  )
}

function filterSpaces(spaces: HierarchySpace[], query: string): HierarchySpace[] {
  const needle: string = query.trim().toLowerCase()
  if (needle === "") {
    return spaces
  }
  const matched: HierarchySpace[] = []
  for (const space of spaces) {
    if (space.name.toLowerCase().includes(needle)) {
      matched.push(space)
      continue
    }
    const scopes: HierarchyScope[] = []
    for (const scope of space.scopes) {
      if (scope.name.toLowerCase().includes(needle)) {
        scopes.push(scope)
        continue
      }
      const tables: HierarchyTable[] = scope.tables.filter((table: HierarchyTable) =>
        table.name.toLowerCase().includes(needle),
      )
      const airflow: HierarchyAirflow[] = filterAirflow(scope.airflow, needle)
      if (tables.length > 0 || airflow.length > 0) {
        scopes.push({ id: scope.id, name: scope.name, tables, airflow })
      }
    }
    const airflow: HierarchyAirflow[] = filterAirflow(space.airflow, needle)
    if (scopes.length > 0 || airflow.length > 0) {
      matched.push({ id: space.id, name: space.name, scopes, airflow })
    }
  }
  return matched
}

function filterAirflow(nodes: HierarchyAirflow[], needle: string): HierarchyAirflow[] {
  const matched: HierarchyAirflow[] = []
  for (const instance of nodes) {
    if (instance.name.toLowerCase().includes(needle)) {
      matched.push(instance)
      continue
    }
    const dags: HierarchyDag[] = instance.dags.filter((dag: HierarchyDag) =>
      dag.dagId.toLowerCase().includes(needle),
    )
    if (dags.length > 0) {
      matched.push({ instanceId: instance.instanceId, name: instance.name, dags })
    }
  }
  return matched
}
