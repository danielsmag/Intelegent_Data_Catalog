import { useEffect, useMemo, useRef, useState, type ChangeEvent, type ReactElement, type ReactNode, type RefObject } from "react"
import { errorMessage, isAbortError } from "./api"
import { openInFocusedGroup, parseRouteIds, pathnameOf, searchOf, useFocusedPath, useTreeReveal, type RouteIds } from "./editorLayout"
import { WorkbenchLink } from "./editorSlots"
import { bumpCatalogRevision, useCatalogRevision } from "./catalogRevision"
import { CreateRecordForm } from "./CreateRecordForm"
import { AirflowIcon, Chevron, IcebergIcon, MoreIcon, PanelChevron, ScopeIcon, SearchIcon, SpaceIcon } from "./icons"
import { RenameRecordForm } from "./RenameRecordForm"
import { ResizeHandle } from "./ResizeHandle"
import {
  createScope,
  createSpace,
  deleteScope,
  deleteSpace,
  renameScope,
  renameSpace,
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

type RenameTarget = {
  kind: "space" | "scope"
  id: string
  name: string
}

const collapsedStorageKey: string = "spaces-tree-collapsed"

function readCollapsedKeys(): ReadonlySet<string> {
  try {
    const raw: string | null = window.localStorage.getItem(collapsedStorageKey)
    if (raw === null) {
      return new Set()
    }
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) {
      return new Set()
    }
    const keys: string[] = []
    for (const item of parsed) {
      if (typeof item === "string") {
        keys.push(item)
      }
    }
    return new Set(keys)
  } catch {
    return new Set()
  }
}

function writeCollapsedKeys(keys: ReadonlySet<string>): void {
  try {
    const stored: string[] = [...keys]
    window.localStorage.setItem(collapsedStorageKey, JSON.stringify(stored))
  } catch {
    // Private mode can block storage. In-memory state still updates.
  }
}

function revealAncestors(spaces: readonly HierarchySpace[], pathname: string, search: string): readonly string[] {
  const ids: RouteIds = parseRouteIds(pathname)
  const params: URLSearchParams = new URLSearchParams(search)
  const onAirflow: boolean = params.get("tab") === "airflow"
  const instanceId: string | null = params.get("instance")
  const dagId: string | null = params.get("dag")
  if (ids.tableId !== null) {
    for (const space of spaces) {
      for (const scope of space.scopes) {
        const hit: boolean = scope.tables.some((table: HierarchyTable) => table.id === ids.tableId)
        if (!hit) {
          continue
        }
        return ["spaces", `space:${space.id}`, `scopes:${space.id}`, `scope:${scope.id}`, `tables:${scope.id}`]
      }
    }
    return []
  }
  if (ids.scopeId !== null) {
    for (const space of spaces) {
      const scope: HierarchyScope | undefined = space.scopes.find((item: HierarchyScope) => item.id === ids.scopeId)
      if (scope === undefined) {
        continue
      }
      const keys: string[] = ["spaces", `space:${space.id}`, `scopes:${space.id}`]
      if (onAirflow && instanceId !== null) {
        keys.push(`scope:${scope.id}`, `airflow-group:${scope.id}`)
        if (dagId !== null) {
          keys.push(`airflow:${scope.id}:${instanceId}`)
        }
      }
      return keys
    }
    return []
  }
  if (ids.spaceId !== null) {
    const space: HierarchySpace | undefined = spaces.find((item: HierarchySpace) => item.id === ids.spaceId)
    if (space === undefined) {
      return []
    }
    const keys: string[] = ["spaces"]
    if (onAirflow && instanceId !== null) {
      keys.push(`space:${space.id}`, `airflow-group:${space.id}`)
      if (dagId !== null) {
        keys.push(`airflow:${space.id}:${instanceId}`)
      }
    }
    return keys
  }
  return []
}

export function HierarchyTree(props: HierarchyTreeProps): ReactElement {
  const [spaces, setSpaces] = useState<HierarchySpace[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [query, setQuery] = useState<string>("")
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(readCollapsedKeys)
  const [addingSpace, setAddingSpace] = useState<boolean>(false)
  const [addingScopeId, setAddingScopeId] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<RenameTarget | null>(null)
  const [menuId, setMenuId] = useState<string | null>(null)
  const [pendingDrop, setPendingDrop] = useState<string | null>(null)
  const [revealTick, setRevealTick] = useState<number>(0)
  const treeRef: RefObject<HTMLDivElement | null> = useRef<HTMLDivElement>(null)
  const revealedPath: RefObject<string | null> = useRef<string | null>(null)
  const settled: RefObject<boolean> = useRef<boolean>(false)
  const lastReveal: RefObject<number> = useRef<number>(0)
  const revealRequest: number = useTreeReveal()
  const revision: number = useCatalogRevision()
  const focused: string = useFocusedPath()
  const pathname: string = pathnameOf(focused)
  const search: string = searchOf(focused)
  const ids: RouteIds = parseRouteIds(pathname)
  const selectedSpace: string | undefined = ids.spaceId ?? undefined
  const selectedScope: string | undefined = ids.scopeId ?? undefined
  const selectedTable: string | undefined = ids.tableId ?? undefined
  const searching: boolean = query.trim() !== ""
  const visible: HierarchySpace[] = useMemo(
    () => filterSpaces(spaces ?? [], query),
    [spaces, query],
  )

  useEffect(() => {
    writeCollapsedKeys(collapsed)
  }, [collapsed])

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

  useEffect(() => {
    if (spaces === null) {
      return
    }
    const requestChanged: boolean = lastReveal.current !== revealRequest
    lastReveal.current = revealRequest
    if (!settled.current) {
      settled.current = true
      if (!requestChanged) {
        revealedPath.current = focused
        return
      }
    }
    if (!requestChanged && revealedPath.current === focused) {
      return
    }
    revealedPath.current = focused
    const keys: readonly string[] = revealAncestors(spaces, pathname, search)
    if (keys.length === 0) {
      return
    }
    setCollapsed((current: ReadonlySet<string>) => {
      let changed: boolean = false
      const next: Set<string> = new Set(current)
      for (const key of keys) {
        if (next.delete(key)) {
          changed = true
        }
      }
      return changed ? next : current
    })
    setRevealTick((current: number) => current + 1)
  }, [focused, spaces, pathname, search, revealRequest])

  useEffect(() => {
    if (revealTick === 0) {
      return
    }
    const row: Element | null = treeRef.current?.querySelector('[aria-current="page"]') ?? null
    row?.scrollIntoView({ block: "nearest" })
  }, [revealTick])

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

  function openMenu(key: string): void {
    setPendingDrop(null)
    setMenuId((current: string | null) => (current === key ? null : key))
  }

  function closeMenu(): void {
    setMenuId(null)
    setPendingDrop(null)
  }

  function beginRename(kind: "space" | "scope", id: string, name: string): void {
    closeMenu()
    setAddingSpace(false)
    setAddingScopeId(null)
    setRenaming({ kind, id, name })
  }

  async function dropSpace(space: HierarchySpace): Promise<void> {
    try {
      await deleteSpace(space.id)
      closeMenu()
      if (selectedSpace === space.id) {
        openInFocusedGroup("/spaces")
      }
      bumpCatalogRevision()
    } catch (err: unknown) {
      closeMenu()
      setError(errorMessage(err))
    }
  }

  async function dropScope(space: HierarchySpace, scope: HierarchyScope): Promise<void> {
    try {
      await deleteScope(scope.id)
      closeMenu()
      if (selectedScope === scope.id) {
        openInFocusedGroup(`/spaces/${space.id}`)
      }
      bumpCatalogRevision()
    } catch (err: unknown) {
      closeMenu()
      setError(errorMessage(err))
    }
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
            <TreeMenu
              menuKey="header"
              label="Space actions"
              open={menuId === "header"}
              onOpen={openMenu}
              onClose={closeMenu}
              items={[
                {
                  label: "Add space",
                  onSelect: () => {
                    closeMenu()
                    setAddingScopeId(null)
                    setRenaming(null)
                    setAddingSpace(true)
                  },
                },
              ]}
            />
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
          <IcebergIcon /> Iceberg
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
          showVisibility
          compact
          onCancel={() => {
            setAddingSpace(false)
          }}
          onCreate={async (input) => {
            const created = await createSpace(input)
            setAddingSpace(false)
            bumpCatalogRevision()
            openInFocusedGroup(`/spaces/${created.id}`)
          }}
        />
      ) : null}
      <div className="tree" role="tree" ref={treeRef}>
        {loading ? <p className="tree-status">Loading spaces…</p> : null}
        {error !== null ? <p className="tree-status error-text">{error}</p> : null}
        {!loading && error === null && visible.length === 0 ? (
          <p className="tree-status">{searching ? "No matches." : "No spaces yet."}</p>
        ) : null}
        {visible.length > 0 ? (
        <TreeGroup
          groupKey="spaces"
          label="Spaces"
          depth="depth-root"
          figure="figure-space"
          icon={<SpaceIcon />}
          closed={closed("spaces")}
          onToggle={toggle}
        >
        {visible.map((space: HierarchySpace) => {
          const spaceKey: string = `space:${space.id}`
          const spaceClosed: boolean = closed(spaceKey)
          const spaceSelected: boolean = selectedSpace === space.id
          return (
            <div key={space.id} role="treeitem" aria-expanded={!spaceClosed}>
              <div className={spaceSelected ? "tree-row depth-space current-branch" : "tree-row depth-space"}>
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
                <WorkbenchLink to={`/spaces/${space.id}`} className="tree-link figure-space" aria-current={spaceSelected ? "page" : undefined}>
                  <SpaceIcon />
                  <span className="tree-label">
                    {space.name}
                    <span className="badge">{space.isolation_mode === "OPEN" ? "Shared" : "Private"}</span>
                  </span>
                </WorkbenchLink>
                <TreeMenu
                  menuKey={`space:${space.id}`}
                  label={`Actions for ${space.name}`}
                  open={menuId === `space:${space.id}`}
                  onOpen={openMenu}
                  onClose={closeMenu}
                  items={
                    pendingDrop === `space:${space.id}`
                      ? [
                          {
                            label: `Drop ${space.name}`,
                            danger: true,
                            onSelect: () => {
                              void dropSpace(space)
                            },
                          },
                          { label: "Cancel", onSelect: () => setPendingDrop(null) },
                        ]
                      : [
                          {
                            label: "Add scope",
                            onSelect: () => {
                              closeMenu()
                              setAddingSpace(false)
                              setRenaming(null)
                              setAddingScopeId(space.id)
                              setCollapsed((current: ReadonlySet<string>) => {
                                const next: Set<string> = new Set(current)
                                next.delete("spaces")
                                next.delete(spaceKey)
                                return next
                              })
                            },
                          },
                          {
                            label: "Rename",
                            onSelect: () => {
                              beginRename("space", space.id, space.name)
                            },
                          },
                          {
                            label: "Drop",
                            danger: true,
                            onSelect: () => setPendingDrop(`space:${space.id}`),
                          },
                        ]
                  }
                />
              </div>
              {renaming?.kind === "space" && renaming.id === space.id ? (
                <RenameRecordForm
                  idPrefix={`tree-rename-space-${space.id}`}
                  nameLabel="Space name"
                  initialName={renaming.name}
                  compact
                  onCancel={() => {
                    setRenaming(null)
                  }}
                  onRename={async (name: string) => {
                    await renameSpace(space.id, name)
                    setRenaming(null)
                    bumpCatalogRevision()
                  }}
                />
              ) : null}
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
                      next.delete("spaces")
                      next.delete(spaceKey)
                      next.delete(`scopes:${space.id}`)
                      return next
                    })
                    bumpCatalogRevision()
                    openInFocusedGroup(`/scopes/${created.id}`)
                  }}
                />
              ) : null}
              {spaceClosed ? null : (
                <>
                  {space.scopes.length > 0 ? (
                  <TreeGroup
                    groupKey={`scopes:${space.id}`}
                    label="Scopes"
                    depth="depth-scopes"
                    figure="figure-scope"
                    icon={<ScopeIcon />}
                    closed={closed(`scopes:${space.id}`)}
                    onToggle={toggle}
                  >
                  {space.scopes.map((scope: HierarchyScope) => {
                    const scopeKey: string = `scope:${scope.id}`
                    const scopeClosed: boolean = closed(scopeKey)
                    const scopeSelected: boolean = selectedScope === scope.id
                    return (
                      <div key={scope.id} role="treeitem" aria-expanded={!scopeClosed}>
                        <div className={scopeSelected ? "tree-row depth-scope current-branch" : "tree-row depth-scope"}>
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
                          <WorkbenchLink
                            to={`/scopes/${scope.id}`}
                            className="tree-link figure-scope"
                            aria-current={scopeSelected ? "page" : undefined}
                          >
                            <ScopeIcon />
                            <span className="tree-label">{scope.name}</span>
                          </WorkbenchLink>
                          <TreeMenu
                            menuKey={`scope:${scope.id}`}
                            label={`Actions for ${scope.name}`}
                            open={menuId === `scope:${scope.id}`}
                            onOpen={openMenu}
                            onClose={closeMenu}
                            items={
                              pendingDrop === `scope:${scope.id}`
                                ? [
                                    {
                                      label: `Drop ${scope.name}`,
                                      danger: true,
                                      onSelect: () => {
                                        void dropScope(space, scope)
                                      },
                                    },
                                    { label: "Cancel", onSelect: () => setPendingDrop(null) },
                                  ]
                                : [
                                    {
                                      label: "Rename",
                                      onSelect: () => {
                                        beginRename("scope", scope.id, scope.name)
                                      },
                                    },
                                    {
                                      label: "Drop",
                                      danger: true,
                                      onSelect: () => setPendingDrop(`scope:${scope.id}`),
                                    },
                                  ]
                            }
                          />
                        </div>
                        {renaming?.kind === "scope" && renaming.id === scope.id ? (
                          <RenameRecordForm
                            idPrefix={`tree-rename-scope-${scope.id}`}
                            nameLabel="Scope name"
                            initialName={renaming.name}
                            compact
                            onCancel={() => {
                              setRenaming(null)
                            }}
                            onRename={async (name: string) => {
                              await renameScope(scope.id, name)
                              setRenaming(null)
                              bumpCatalogRevision()
                            }}
                          />
                        ) : null}
                        {scopeClosed ? null : (
                          <>
                            {scope.tables.length > 0 ? (
                              <TreeGroup
                                groupKey={`tables:${scope.id}`}
                                label="Assets"
                                depth="depth-section"
                                figure="figure-table"
                                icon={<IcebergIcon />}
                                closed={closed(`tables:${scope.id}`)}
                                onToggle={toggle}
                              >
                                {scope.tables.map((table: HierarchyTable) => {
                                  const tableSelected: boolean = selectedTable === table.id
                                  return (
                                    <WorkbenchLink
                                      key={table.id}
                                      to={table.assetId !== null ? `/assets/${table.assetId}` : `/registry/tables/${table.id}`}
                                      className="tree-row depth-node figure-table"
                                      role="treeitem"
                                      aria-current={tableSelected ? "page" : undefined}
                                    >
                                      <span className="chevron-spacer" />
                                      <IcebergIcon />
                                      <span className="tree-label">{table.name}</span>
                                    </WorkbenchLink>
                                  )
                                })}
                              </TreeGroup>
                            ) : null}
                            {scope.airflow.length > 0 ? (
                              <TreeGroup
                                groupKey={`airflow-group:${scope.id}`}
                                label="Airflow"
                                depth="depth-section"
                                figure="figure-airflow"
                                icon={<AirflowIcon />}
                                closed={closed(`airflow-group:${scope.id}`)}
                                onToggle={toggle}
                              >
                                <AirflowNodes
                                  nodes={scope.airflow}
                                  parentKey={scope.id}
                                  ownerPath={`/scopes/${scope.id}`}
                                  instanceDepth="depth-node"
                                  dagDepth="depth-child"
                                  collapsed={closed}
                                  onToggle={toggle}
                                  pathname={pathname}
                                  search={search}
                                />
                              </TreeGroup>
                            ) : null}
                          </>
                        )}
                      </div>
                    )
                  })}
                  </TreeGroup>
                  ) : null}
                  {space.airflow.length > 0 ? (
                    <TreeGroup
                      groupKey={`airflow-group:${space.id}`}
                      label="Airflow"
                      depth="depth-scopes"
                      figure="figure-airflow"
                      icon={<AirflowIcon />}
                      closed={closed(`airflow-group:${space.id}`)}
                      onToggle={toggle}
                    >
                      <AirflowNodes
                        nodes={space.airflow}
                        parentKey={space.id}
                        ownerPath={`/spaces/${space.id}`}
                        instanceDepth="depth-scope"
                        dagDepth="depth-section"
                        collapsed={closed}
                        onToggle={toggle}
                        pathname={pathname}
                        search={search}
                      />
                    </TreeGroup>
                  ) : null}
                </>
              )}
            </div>
          )
        })}
        </TreeGroup>
        ) : null}
      </div>
        </>
      )}
      {props.collapsed ? null : <ResizeHandle pane="sidebar" edge="end" label="Resize sidebar" />}
    </aside>
  )
}

type MenuAction = {
  label: string
  danger?: boolean
  onSelect: () => void
}

type MenuBox = {
  top: number
  left: number
}

function TreeMenu(props: {
  menuKey: string
  label: string
  open: boolean
  onOpen: (key: string) => void
  onClose: () => void
  items: readonly MenuAction[]
}): ReactElement {
  const triggerRef: RefObject<HTMLButtonElement | null> = useRef<HTMLButtonElement>(null)
  const panelRef: RefObject<HTMLDivElement | null> = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState<MenuBox | null>(null)

  function place(): void {
    const trigger: HTMLButtonElement | null = triggerRef.current
    if (trigger === null) {
      return
    }
    const rect: DOMRect = trigger.getBoundingClientRect()
    const width: number = 160
    const left: number = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8))
    const top: number = rect.bottom + 4
    setBox({ top, left })
  }

  useEffect(() => {
    if (!props.open) {
      return
    }
    place()
    function onPointer(event: PointerEvent): void {
      const target: EventTarget | null = event.target
      if (!(target instanceof Node)) {
        return
      }
      if (triggerRef.current?.contains(target) === true || panelRef.current?.contains(target) === true) {
        return
      }
      props.onClose()
    }
    window.addEventListener("pointerdown", onPointer)
    window.addEventListener("resize", props.onClose)
    return () => {
      window.removeEventListener("pointerdown", onPointer)
      window.removeEventListener("resize", props.onClose)
    }
  }, [props.open, props.menuKey, props.onClose])

  return (
    <div className="tree-menu">
      <button
        ref={triggerRef}
        type="button"
        className="tree-add"
        aria-haspopup="menu"
        aria-expanded={props.open}
        aria-label={props.label}
        title={props.label}
        onClick={() => {
          props.onOpen(props.menuKey)
        }}
      >
        <MoreIcon />
      </button>
      {props.open && box !== null ? (
        <div
          ref={panelRef}
          className="tree-menu-panel"
          role="menu"
          aria-label={props.label}
          style={{ position: "fixed", top: box.top, left: box.left }}
        >
          {props.items.map((item: MenuAction) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              className={item.danger === true ? "tree-menu-item danger" : "tree-menu-item"}
              onClick={item.onSelect}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
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
  pathname: string
  search: string
}): ReactElement | null {
  if (props.nodes.length === 0) {
    return null
  }
  const onOwner: boolean = props.pathname === props.ownerPath
  const search: URLSearchParams = new URLSearchParams(props.search)
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
              <WorkbenchLink
                to={instancePath}
                className="tree-link figure-airflow"
                aria-current={instanceSelected ? "page" : undefined}
              >
                <AirflowIcon />
                <span className="tree-label">{instance.name}</span>
              </WorkbenchLink>
            </div>
            {instanceClosed
              ? null
              : instance.dags.map((dag: HierarchyDag) => {
                  const dagPath: string = `${instancePath}&dag=${encodeURIComponent(dag.dagId)}`
                  const dagSelected: boolean =
                    onAirflowTab && selectedInstance === instance.instanceId && selectedDag === dag.dagId
                  return (
                    <WorkbenchLink
                      key={dag.dagRowId}
                      to={dagPath}
                      className={`tree-row ${props.dagDepth} figure-airflow`}
                      role="treeitem"
                      aria-current={dagSelected ? "page" : undefined}
                    >
                      <span className="chevron-spacer" />
                      <AirflowIcon />
                      <span className="tree-label">{dag.dagId}</span>
                    </WorkbenchLink>
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
      matched.push({
        id: space.id,
        name: space.name,
        isolation_mode: space.isolation_mode,
        scopes,
        airflow,
      })
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
