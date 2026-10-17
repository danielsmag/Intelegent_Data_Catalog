import { Fragment, useEffect, useMemo, useState, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent, type ReactElement, type ReactNode } from "react"
import {
  createPath,
  Navigate,
  NavigationType,
  parsePath,
  Route,
  Router,
  Routes,
  useParams,
  type Location,
  type Navigator,
  type Path,
  type To,
} from "react-router-dom"
import { ActivityBar } from "./ActivityBar"
import { AirflowDagPage, AirflowInstancePage, AirflowInstancesPage } from "./AirflowPages"
import { AirflowSidebar } from "./AirflowSidebar"
import { useAirflowSidebarOpen, setAirflowSidebarOpen } from "./airflowSidebarStore"
import { CatalogPage } from "./Explorer"
import { CatalogSidebar } from "./CatalogTree"
import { EditorDragGhost, EditorDropEdges, EditorTabBar } from "./EditorTabBar"
import { useEditorDrag } from "./editorDrag"
import {
  focusGroup,
  getNavigation,
  openInGroup,
  openInFocusedGroup,
  pathnameOf,
  readHistoryMarker,
  resizeSplit,
  restoreHistory,
  searchOf,
  useEditorLayout,
  type EditorGroup,
  type EditorLayout,
  type EditorTab,
  type LayoutNode,
  type SplitDirection,
} from "./editorLayout"
import { AppSideDock } from "./ConfigRail"
import { EditorScopeContext, RailTargetContext, useEditorStatusText, type EditorScope } from "./editorSlots"
import { HierarchyTree } from "./HierarchyTree"
import { WorkspaceFilePage } from "./WorkspaceFilePage"
import { WorkspaceTree } from "./WorkspaceTree"
import { WorkspacesPage } from "./WorkspacesPage"
import { ResizeHandle } from "./ResizeHandle"
import { QueryPage } from "./QueryPage"
import { DataSourcesPage, RegistryTablePage, ScopePage, SearchPage, SpacePage, SpacesPage } from "./RegistryPages"
import { setPendingQuerySql, setSidebarView, sidebarViewForPath, useSidebarView, type SidebarView } from "./sidebarView"
import { setSpacesSidebarOpen, useSpacesSidebarOpen } from "./spacesSidebar"
import { setWorkspaceSidebarOpen, useWorkspaceSidebarOpen } from "./workspaceSidebar"

export function EditorWorkbench(): ReactElement {
  const layout: EditorLayout = useEditorLayout()
  const status: { area: string; detail: string } = useEditorStatusText()
  const [configSlot, setConfigSlot] = useState<HTMLDivElement | null>(null)
  useEffect(() => {
    function onPop(event: PopStateEvent): void {
      const path: string = `${window.location.pathname}${window.location.search}`
      const view: SidebarView | null = sidebarViewForPath(path)
      if (view !== null) {
        setSidebarView(view)
        if (view === "query") {
          const sql: string | null = new URLSearchParams(window.location.search).get("sql")
          if (sql !== null && sql !== "") {
            setPendingQuerySql(sql)
          }
        }
        return
      }
      const marker: { groupId: string; tabId: string } | null = readHistoryMarker(event.state)
      if (marker !== null) {
        restoreHistory(marker.groupId, marker.tabId, path)
        return
      }
      openInFocusedGroup(path, "none")
    }
    window.addEventListener("popstate", onPop)
    return () => {
      window.removeEventListener("popstate", onPop)
    }
  }, [])
  return (
    <RailTargetContext.Provider value={configSlot}>
      <div className="shell">
        <div className="workspace">
          <ActivityBar />
          <SecondSidebar />
          <main className="main editor-grid">
            <LayoutView node={layout.root} />
          </main>
          <div className="editor-rail-host">
            <AppSideDock onConfigSlot={setConfigSlot} />
          </div>
        </div>
        <footer className="status">
          <span>{status.area}</span>
          <span>{status.detail}</span>
        </footer>
      </div>
      <EditorDragGhost />
    </RailTargetContext.Provider>
  )
}

function SecondSidebar(): ReactElement {
  const view: SidebarView = useSidebarView()
  const spacesOpen: boolean = useSpacesSidebarOpen()
  const airflowOpen: boolean = useAirflowSidebarOpen()
  const workspaceOpen: boolean = useWorkspaceSidebarOpen()
  if (view === "spaces") {
    return (
      <HierarchyTree
        title="Spaces"
        collapsed={!spacesOpen}
        onToggleCollapsed={() => {
          setSpacesSidebarOpen(!spacesOpen)
        }}
      />
    )
  }
  if (view === "airflow") {
    return (
      <AirflowSidebar
        collapsed={!airflowOpen}
        onToggleCollapsed={() => {
          setAirflowSidebarOpen(!airflowOpen)
        }}
      />
    )
  }
  if (view === "catalog") {
    return <CatalogSidebar />
  }
  if (view === "workspace") {
    return (
      <WorkspaceTree
        collapsed={!workspaceOpen}
        onToggleCollapsed={() => {
          setWorkspaceSidebarOpen(!workspaceOpen)
        }}
      />
    )
  }
  if (view === "search") {
    return (
      <ToolSidebar title="Search">
        <SearchPage />
      </ToolSidebar>
    )
  }
  if (view === "sources") {
    return (
      <ToolSidebar title="Sources">
        <DataSourcesPage />
      </ToolSidebar>
    )
  }
  return (
    <ToolSidebar title="Query">
      <QueryPage />
    </ToolSidebar>
  )
}

function ToolSidebar(props: { title: string; children: ReactNode }): ReactElement {
  return (
    <aside className="sidebar tool-sidebar" aria-label={props.title}>
      <div className="sidebar-head">
        <div className="sidebar-title">{props.title}</div>
      </div>
      <div className="tool-sidebar-body">{props.children}</div>
      <ResizeHandle pane="sidebar" edge="end" label="Resize sidebar" />
    </aside>
  )
}

function LayoutView(props: { node: LayoutNode }): ReactElement {
  if (props.node.type === "group") {
    return <EditorGroupPane groupId={props.node.groupId} />
  }
  const direction: SplitDirection = props.node.direction
  return (
    <div className={direction === "row" ? "editor-split row" : "editor-split column"}>
      {props.node.children.map((child: LayoutNode, index: number) => {
        const size: number = props.node.type === "split" ? props.node.sizes[index] ?? 1 : 1
        return (
          <Fragment key={child.id}>
            {index > 0 && props.node.type === "split" ? (
              <EditorSash splitId={props.node.id} boundary={index} direction={direction} before={props.node.sizes[index - 1] ?? 0.5} />
            ) : null}
            <div className="editor-split-pane" style={{ flexGrow: size }}>
              <LayoutView node={child} />
            </div>
          </Fragment>
        )
      })}
    </div>
  )
}

function EditorSash(props: { splitId: string; boundary: number; direction: SplitDirection; before: number }): ReactElement {
  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>): void {
    if (event.button !== 0) {
      return
    }
    event.preventDefault()
    const handle: HTMLDivElement = event.currentTarget
    const split: HTMLElement | null = handle.parentElement
    if (split === null) {
      return
    }
    const rect: DOMRect = split.getBoundingClientRect()
    const total: number = props.direction === "row" ? rect.width : rect.height
    if (total <= 0) {
      return
    }
    const start: number = props.direction === "row" ? event.clientX : event.clientY
    const startLeft: number = props.before
    const cursorClass: string = props.direction === "row" ? "editor-col-resize" : "editor-row-resize"
    document.documentElement.classList.add(cursorClass)
    function move(ev: PointerEvent): void {
      const current: number = props.direction === "row" ? ev.clientX : ev.clientY
      resizeSplit(props.splitId, props.boundary, startLeft + (current - start) / total)
    }
    function up(): void {
      handle.removeEventListener("pointermove", move)
      handle.removeEventListener("pointerup", up)
      document.documentElement.classList.remove(cursorClass)
    }
    handle.setPointerCapture(event.pointerId)
    handle.addEventListener("pointermove", move)
    handle.addEventListener("pointerup", up)
  }
  return (
    <div
      className={props.direction === "row" ? "editor-sash row" : "editor-sash column"}
      role="separator"
      aria-orientation={props.direction === "row" ? "vertical" : "horizontal"}
      aria-label="Resize split"
      onPointerDown={onPointerDown}
    />
  )
}

function EditorGroupPane(props: { groupId: string }): ReactElement | null {
  const layout: EditorLayout = useEditorLayout()
  const drag = useEditorDrag()
  const group: EditorGroup | undefined = layout.groups[props.groupId]
  const focused: boolean = layout.focusedGroupId === props.groupId
  const tab: EditorTab | null =
    group === undefined || group.activeId === null
      ? null
      : group.tabs.find((item: EditorTab) => item.id === group.activeId) ?? null
  const navigator: Navigator = useMemo(() => createGroupNavigator(props.groupId), [props.groupId])
  const location: Location | null = useMemo(
    () => (tab === null || tab.path === "" ? null : locationFor(tab.path, tab.id)),
    [tab],
  )
  const scope: EditorScope | null = useMemo(
    () => (tab === null || tab.path === "" ? null : { groupId: props.groupId, tabId: tab.id, focused }),
    [props.groupId, tab, focused],
  )
  if (group === undefined) {
    return null
  }
  const navigation = navigationType()
  const groupHot: boolean =
    drag?.source.kind === "path" && drag.target?.kind === "group" && drag.target.groupId === props.groupId
  const className: string = ["editor-group", focused ? "focused" : "", groupHot ? "drop-hot" : ""].filter((name: string) => name !== "").join(" ")
  return (
    <section
      className={className}
      data-editor-group=""
      data-group-id={props.groupId}
      onMouseDown={(event: ReactMouseEvent<HTMLElement>) => {
        if (event.button !== 0) {
          return
        }
        const target: EventTarget | null = event.target
        if (target instanceof Element && target.closest("a, [data-editor-tab]") !== null) {
          return
        }
        focusGroup(props.groupId)
      }}
    >
      <EditorTabBar groupId={props.groupId} />
      <div className="editor-body">
        {tab === null || location === null || scope === null ? (
          <div className="empty">
            <p>No page open</p>
          </div>
        ) : (
          <EditorScopeContext.Provider value={scope}>
            <Router location={location} navigator={navigator} navigationType={navigation}>
              <div className="main-scroll" key={tab.id}>
                <EditorRoutes />
              </div>
            </Router>
          </EditorScopeContext.Provider>
        )}
        <EditorDropEdges groupId={props.groupId} />
      </div>
    </section>
  )
}

function navigationType(): NavigationType {
  const value: "POP" | "PUSH" | "REPLACE" = getNavigation()
  if (value === "PUSH") {
    return NavigationType.Push
  }
  if (value === "REPLACE") {
    return NavigationType.Replace
  }
  return NavigationType.Pop
}

function locationFor(path: string, key: string): Location {
  return {
    pathname: pathnameOf(path),
    search: searchOf(path),
    hash: "",
    state: null,
    key,
  }
}

function createGroupNavigator(groupId: string): Navigator {
  function href(to: To): string {
    return typeof to === "string" ? to : createPath(to)
  }
  return {
    createHref: href,
    createURL(to: To): URL {
      return new URL(href(to), window.location.origin)
    },
    encodeLocation(to: To): Path {
      const parsed: Partial<Path> = parsePath(href(to))
      return {
        pathname: parsed.pathname ?? "/",
        search: parsed.search ?? "",
        hash: parsed.hash ?? "",
      }
    },
    go(delta: number): void {
      window.history.go(delta)
    },
    push(to: To): void {
      openInGroup(groupId, href(to), "push")
    },
    replace(to: To): void {
      openInGroup(groupId, href(to), "replace")
    },
  }
}

function WorkspaceFileRoute(): ReactElement {
  const nodeId: string = useParams().nodeId ?? ""
  return <WorkspaceFilePage nodeId={nodeId} />
}

function EditorRoutes(): ReactElement {
  return (
    <Routes>
      <Route path="/" element={<SpacesPage />} />
      <Route path="/airflow" element={<AirflowInstancesPage />} />
      <Route path="/airflow/:instanceId" element={<AirflowInstancePage />} />
      <Route path="/airflow/:instanceId/dags/:dagId" element={<AirflowDagPage />} />
      <Route path="/catalog" element={<CatalogPage />} />
      <Route path="/catalog/:source/:catalog/:namespace/:table" element={<CatalogPage />} />
      <Route path="/spaces" element={<SpacesPage />} />
      <Route path="/spaces/:spaceId" element={<SpacePage />} />
      <Route path="/workspaces" element={<WorkspacesPage />} />
      <Route path="/workspaces/:workspaceId" element={<WorkspacesPage />} />
      <Route path="/workspace/nodes/:nodeId" element={<WorkspaceFileRoute />} />
      <Route path="/scopes/:scopeId" element={<ScopePage />} />
      <Route path="/registry/tables/:tableId" element={<RegistryTablePage />} />
      <Route path="/iceberg" element={<Navigate to="/catalog" replace />} />
      <Route path="*" element={<MissingPage />} />
    </Routes>
  )
}

function MissingPage(): ReactElement {
  return <p className="note">Page not found.</p>
}
