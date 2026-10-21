import {
  Fragment,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type MouseEvent,
  type ReactElement,
  type RefObject,
} from "react"
import { createNodeAsset } from "./assetsApi"
import { errorMessage } from "./api"
import { fetchHierarchy, type HierarchySpace } from "./registryApi"
import { openInFocusedGroup } from "./editorLayout"
import { Chevron, FileIcon, FolderIcon, MoreIcon, PanelChevron, PersonIcon } from "./icons"
import { ResizeHandle } from "./ResizeHandle"
import { setSidebarView } from "./sidebarView"
import { useCatalogRevision } from "./catalogRevision"
import { getActiveWorkspaceId, setActiveWorkspaceId, useActiveWorkspaceId } from "./workspaceStore"
import { useMe, type Me, type Membership } from "./workspaceApi"
import {
  createFolder,
  createTextFile,
  deleteNode,
  fetchNodes,
  moveNode,
  publishNodeLabel,
  uploadFile,
  type WorkspaceNode,
} from "./workspaceFilesApi"

const USERS_ROOT: string = "Users"
const UUID_NAME: RegExp = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const NEW_FILES: Array<{ label: string; name: string; text: string }> = [
  { label: "New SQL file", name: "query.sql", text: "" },
  { label: "New note", name: "note.md", text: "" },
  { label: "New Python file", name: "script.py", text: "" },
  {
    label: "New notebook",
    name: "notebook.ipynb",
    text: JSON.stringify(
      { cells: [], metadata: {}, nbformat: 4, nbformat_minor: 5 },
      null,
      2,
    ),
  },
]

type FileTemplate = {
  label: string
  name: string
  text: string
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

export function WorkspaceTree(props: { collapsed: boolean; onToggleCollapsed: () => void }): ReactElement {
  const workspaceId: string | null = useActiveWorkspaceId()
  const me: Me | null = useMe()
  const memberships: Membership[] = me?.memberships ?? []
  const revision: number = useCatalogRevision()
  const [nodes, setNodes] = useState<WorkspaceNode[]>([])
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [children, setChildren] = useState<Map<string, WorkspaceNode[]>>(new Map())
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [menuId, setMenuId] = useState<string | null>(null)
  const [scopeTarget, setScopeTarget] = useState<WorkspaceNode | null>(null)
  const [scopeChoices, setScopeChoices] = useState<Array<{ id: string; label: string }>>([])
  const fileRef: RefObject<HTMLInputElement | null> = useRef<HTMLInputElement>(null)
  const uploadParentId: RefObject<string | null> = useRef<string | null>(null)

  useEffect(() => {
    setOpen(new Set())
    setChildren(new Map())
    setSelected(null)
    setRenamingId(null)
    setMenuId(null)
    if (workspaceId === null) {
      setNodes([])
      return
    }
    const controller: AbortController = new AbortController()
    void fetchNodes(workspaceId, null)
      .then((loaded: WorkspaceNode[]) => {
        if (!controller.signal.aborted) {
          setNodes(loaded)
          setError(null)
        }
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setError(errorMessage(err))
        }
      })
    return () => {
      controller.abort()
    }
  }, [workspaceId, revision])

  function openMenu(key: string): void {
    setMenuId((current: string | null) => (current === key ? null : key))
  }

  function closeMenu(): void {
    setMenuId(null)
  }

  async function toggle(node: WorkspaceNode): Promise<void> {
    if (workspaceId === null || node.kind !== "FOLDER") {
      return
    }
    const next: Set<string> = new Set(open)
    if (next.has(node.id)) {
      next.delete(node.id)
      setOpen(next)
      return
    }
    const loaded: WorkspaceNode[] = await fetchNodes(workspaceId, node.id)
    setChildren(new Map(children).set(node.id, loaded))
    next.add(node.id)
    setOpen(next)
  }

  function openFile(node: WorkspaceNode): void {
    const active: string | null = getActiveWorkspaceId()
    if (active === null) {
      return
    }
    setSidebarView("workspace")
    openInFocusedGroup(`/workspace/nodes/${node.id}?workspace=${encodeURIComponent(active)}`)
  }

  async function reload(focusId: string | null, dropId: string | null = null): Promise<void> {
    if (workspaceId === null) {
      return
    }
    const roots: WorkspaceNode[] = await fetchNodes(workspaceId, null)
    const nextOpen: Set<string> = new Set(open)
    if (dropId !== null) {
      nextOpen.delete(dropId)
    }
    if (focusId !== null) {
      nextOpen.add(focusId)
    }
    const nextChildren: Map<string, WorkspaceNode[]> = new Map()
    for (const id of nextOpen) {
      nextChildren.set(id, await fetchNodes(workspaceId, id))
    }
    setNodes(roots)
    setOpen(nextOpen)
    setChildren(nextChildren)
    setError(null)
  }

  async function run(action: () => Promise<void>): Promise<void> {
    try {
      await action()
    } catch (err: unknown) {
      setError(errorMessage(err))
    }
  }

  async function addFolder(parent: WorkspaceNode): Promise<void> {
    if (workspaceId === null) {
      return
    }
    const name: string | null = window.prompt("Folder name")
    if (name === null || name.trim() === "") {
      return
    }
    const parentId: string = parent.id
    await createFolder(workspaceId, parentId, name.trim())
    await reload(parentId)
  }

  async function addFile(parent: WorkspaceNode, template: FileTemplate): Promise<void> {
    if (workspaceId === null) {
      return
    }
    const parentId: string = parent.id
    const siblings: WorkspaceNode[] = await fetchNodes(workspaceId, parentId)
    const taken: Set<string> = new Set(siblings.map((item: WorkspaceNode) => item.name))
    const name: string = uniqueFileName(template.name, taken)
    const created: WorkspaceNode = await createTextFile(workspaceId, parentId, name, template.text)
    await reload(parentId)
    openFile(created)
  }

  function beginRename(node: WorkspaceNode): void {
    if (!canChange(node)) {
      return
    }
    setSelected(node.id)
    setRenamingId(node.id)
    setError(null)
  }

  async function commitRename(node: WorkspaceNode, name: string): Promise<void> {
    if (workspaceId === null) {
      return
    }
    const parentId: string | null = node.parent_id
    const updated: WorkspaceNode = await moveNode(workspaceId, node.id, { name })
    publishNodeLabel(updated)
    setRenamingId(null)
    await reload(parentId)
  }

  async function removeNode(node: WorkspaceNode): Promise<void> {
    if (workspaceId === null || !canChange(node)) {
      return
    }
    const parentId: string | null = node.parent_id
    const nodeId: string = node.id
    await deleteNode(workspaceId, nodeId)
    setSelected((current: string | null) => (current === nodeId ? null : current))
    await reload(parentId, nodeId)
  }

  function beginUpload(parent: WorkspaceNode): void {
    uploadParentId.current = parent.id
    fileRef.current?.click()
  }

  async function onUpload(event: ChangeEvent<HTMLInputElement>): Promise<void> {
    const file: File | undefined = event.target.files?.[0]
    event.target.value = ""
    const parentId: string | null = uploadParentId.current
    uploadParentId.current = null
    if (file === undefined || workspaceId === null || parentId === null) {
      return
    }
    await uploadFile(workspaceId, parentId, file)
    await reload(parentId)
  }

  async function onDrop(event: DragEvent<HTMLDivElement>, folder: WorkspaceNode): Promise<void> {
    event.preventDefault()
    const nodeId: string = event.dataTransfer.getData("text/plain")
    if (workspaceId === null || nodeId === "" || nodeId === folder.id || folder.path === USERS_ROOT) {
      return
    }
    await moveNode(workspaceId, nodeId, { parent_id: folder.id })
    await reload(folder.id)
  }

  function menuFor(node: WorkspaceNode): MenuAction[] {
    const items: MenuAction[] = []
    if (node.kind === "FOLDER" && node.path !== USERS_ROOT) {
      items.push({
        label: "New folder",
        onSelect: () => {
          closeMenu()
          void run(() => addFolder(node))
        },
      })
      for (const template of NEW_FILES) {
        items.push({
          label: template.label,
          onSelect: () => {
            closeMenu()
            void run(() => addFile(node, template))
          },
        })
      }
      items.push({
        label: "Upload file",
        onSelect: () => {
          closeMenu()
          beginUpload(node)
        },
      })
    }
    if (node.kind === "FILE") {
      items.push({
        label: "Add to scope...",
        onSelect: () => {
          closeMenu()
          setScopeTarget(node)
          void fetchHierarchy(new AbortController().signal).then((spaces: HierarchySpace[]) => {
            const choices: Array<{ id: string; label: string }> = []
            for (const space of spaces) {
              for (const scope of space.scopes) {
                choices.push({ id: scope.id, label: `${space.name} / ${scope.name}` })
              }
            }
            setScopeChoices(choices)
          })
        },
      })
    }
    if (canChange(node)) {
      items.push({
        label: "Rename",
        onSelect: () => {
          closeMenu()
          beginRename(node)
        },
      })
      items.push({
        label: "Delete",
        danger: true,
        onSelect: () => {
          closeMenu()
          void run(() => removeNode(node))
        },
      })
    }
    return items
  }

  const sidebarClass: string = props.collapsed
    ? "sidebar hierarchy workspace-files collapsed"
    : "sidebar hierarchy workspace-files"
  return (
    <aside className={sidebarClass} aria-label="Workspace">
      <div className={props.collapsed ? "sidebar-head" : "sidebar-head ws-head"}>
        {props.collapsed ? (
          <>
            <span className="sidebar-rail-label">Workspace</span>
            <div className="sidebar-actions">
              <button
                type="button"
                className="sidebar-toggle"
                aria-expanded={false}
                aria-label="Expand workspace sidebar"
                title="Expand sidebar"
                onClick={props.onToggleCollapsed}
              >
                <PanelChevron direction="right" />
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="ws-head-row">
              <div className="sidebar-title">
                <span>Workspace</span>
              </div>
              <button
                type="button"
                className="sidebar-toggle"
                aria-expanded
                aria-label="Minimize workspace sidebar"
                title="Minimize sidebar"
                onClick={props.onToggleCollapsed}
              >
                <PanelChevron direction="left" />
              </button>
            </div>
            <div className="ws-switch">
              <select
                className="sidebar-workspace"
                aria-label="Active workspace"
                value={workspaceId ?? ""}
                onChange={(event) => {
                  setActiveWorkspaceId(event.target.value === "" ? null : event.target.value)
                }}
              >
                {memberships.map((item: Membership) => (
                  <option key={item.workspace_id} value={item.workspace_id}>
                    {item.name}
                  </option>
                ))}
              </select>
              <span className="ws-switch-chevron" aria-hidden="true">
                <Chevron open />
              </span>
            </div>
          </>
        )}
      </div>
      {props.collapsed ? null : (
        <>
          {error !== null ? <p className="tree-status error-text">{error}</p> : null}
          {scopeTarget !== null ? (
            <label className="tree-status">
              Add {scopeTarget.name} to
              <select
                defaultValue=""
                onChange={(event) => {
                  const scopeId: string = event.target.value
                  const workspace: string | null = getActiveWorkspaceId()
                  if (scopeId === "" || workspace === null) {
                    return
                  }
                  void createNodeAsset(workspace, scopeTarget.id, scopeId, null)
                    .then(() => {
                      setScopeTarget(null)
                    })
                    .catch((err: unknown) => {
                      setError(errorMessage(err))
                    })
                }}
              >
                <option value="">Choose a scope</option>
                {scopeChoices.map((choice) => (
                  <option key={choice.id} value={choice.id}>
                    {choice.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {workspaceId === null ? <p className="tree-status">No workspace selected.</p> : null}
          <input ref={fileRef} type="file" hidden onChange={(event) => void run(() => onUpload(event))} />
          <div className="tree" role="tree">
            {nodes.map((node: WorkspaceNode) => (
              <NodeRow
                key={node.id}
                node={node}
                depth={0}
                open={open}
                childrenById={children}
                selected={selected}
                renamingId={renamingId}
                me={me}
                menuId={menuId}
                menuFor={menuFor}
                onSelect={setSelected}
                onBeginRename={beginRename}
                onCommitRename={async (node: WorkspaceNode, name: string) => {
                  try {
                    await commitRename(node, name)
                  } catch (err: unknown) {
                    setError(errorMessage(err))
                    throw err
                  }
                }}
                onCancelRename={() => {
                  setRenamingId(null)
                }}
                onOpenMenu={openMenu}
                onCloseMenu={closeMenu}
                onToggle={(item: WorkspaceNode) => void run(() => toggle(item))}
                onOpen={openFile}
                onDrop={(event: DragEvent<HTMLDivElement>, folder: WorkspaceNode) => void run(() => onDrop(event, folder))}
              />
            ))}
          </div>
          <ResizeHandle pane="sidebar" edge="end" label="Resize sidebar" />
        </>
      )}
    </aside>
  )
}

function NodeRow(props: {
  node: WorkspaceNode
  depth: number
  open: Set<string>
  childrenById: Map<string, WorkspaceNode[]>
  selected: string | null
  renamingId: string | null
  me: Me | null
  menuId: string | null
  menuFor: (node: WorkspaceNode) => MenuAction[]
  onSelect: (id: string) => void
  onBeginRename: (node: WorkspaceNode) => void
  onCommitRename: (node: WorkspaceNode, name: string) => Promise<void>
  onCancelRename: () => void
  onOpenMenu: (key: string) => void
  onCloseMenu: () => void
  onToggle: (node: WorkspaceNode) => void
  onOpen: (node: WorkspaceNode) => void
  onDrop: (event: DragEvent<HTMLDivElement>, folder: WorkspaceNode) => void
}): ReactElement {
  const openTimer: RefObject<number | null> = useRef<number | null>(null)
  const expanded: boolean = props.open.has(props.node.id)
  const nested: WorkspaceNode[] = props.childrenById.get(props.node.id) ?? []
  const selected: boolean = props.selected === props.node.id
  const renaming: boolean = props.renamingId === props.node.id
  const label: string = rowLabel(props.node, props.me)
  const items: MenuAction[] = props.menuFor(props.node)
  const userHome: boolean = isUserHome(props.node)

  useEffect(() => {
    return () => {
      if (openTimer.current !== null) {
        window.clearTimeout(openTimer.current)
      }
    }
  }, [])

  function activate(): void {
    props.onSelect(props.node.id)
    if (props.node.kind === "FOLDER") {
      props.onToggle(props.node)
      return
    }
    if (openTimer.current !== null) {
      window.clearTimeout(openTimer.current)
    }
    openTimer.current = window.setTimeout(() => {
      openTimer.current = null
      props.onOpen(props.node)
    }, 220)
  }

  function renameFromDoubleClick(event: MouseEvent<HTMLButtonElement>): void {
    event.preventDefault()
    event.stopPropagation()
    if (openTimer.current !== null) {
      window.clearTimeout(openTimer.current)
      openTimer.current = null
    }
    if (props.node.kind === "FILE" && canChange(props.node)) {
      props.onBeginRename(props.node)
    }
  }
  const rowClass: string = selected
    ? `tree-row ws-row current-branch${props.depth === 0 ? " ws-root" : ""}`
    : `tree-row ws-row${props.depth === 0 ? " ws-root" : ""}`
  const canDrop: boolean = props.node.kind === "FOLDER" && props.node.path !== USERS_ROOT
  return (
    <div role="treeitem" aria-expanded={props.node.kind === "FOLDER" ? expanded : undefined}>
      <div
        className={rowClass}
        style={{ paddingLeft: 6 + props.depth * 14 }}
        onDragOver={(event: DragEvent<HTMLDivElement>) => {
          if (canDrop) {
            event.preventDefault()
          }
        }}
        onDrop={(event: DragEvent<HTMLDivElement>) => {
          if (canDrop) {
            props.onDrop(event, props.node)
          }
        }}
      >
        {renaming ? (
          <div className="ws-rename">
            <RowGlyph node={props.node} expanded={expanded} userHome={userHome} />
            <NameField
              initial={props.node.name}
              label={label}
              onCommit={(name: string) => props.onCommitRename(props.node, name)}
              onCancel={props.onCancelRename}
            />
          </div>
        ) : (
          <button
            type="button"
            className="ws-hit"
            title={props.node.path}
            draggable
            onDragStart={(event: DragEvent<HTMLButtonElement>) => {
              event.dataTransfer.setData("text/plain", props.node.id)
              event.dataTransfer.effectAllowed = "move"
            }}
            onClick={activate}
            onDoubleClick={renameFromDoubleClick}
          >
            <RowGlyph node={props.node} expanded={expanded} userHome={userHome} />
            <span className="tree-label">{label}</span>
          </button>
        )}
        {items.length === 0 ? null : (
          <div
            className="ws-more"
            onMouseDown={(event) => {
              event.stopPropagation()
            }}
          >
            <TreeMenu
              menuKey={props.node.id}
              label={`Actions for ${label}`}
              open={props.menuId === props.node.id}
              onOpen={(key: string) => {
                props.onSelect(props.node.id)
                props.onOpenMenu(key)
              }}
              onClose={props.onCloseMenu}
              items={items}
            />
          </div>
        )}
      </div>
      {expanded
        ? nested.map((child: WorkspaceNode) => (
            <NodeRow key={child.id} {...props} node={child} depth={props.depth + 1} />
          ))
        : null}
    </div>
  )
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
  const firstDanger: number = props.items.findIndex((item: MenuAction) => item.danger === true)

  function place(): void {
    const trigger: HTMLButtonElement | null = triggerRef.current
    if (trigger === null) {
      return
    }
    const rect: DOMRect = trigger.getBoundingClientRect()
    const width: number = 188
    const left: number = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8))
    const height: number = panelRef.current?.offsetHeight ?? 240
    const below: number = rect.bottom + 4
    const top: number = below + height > window.innerHeight - 8 ? Math.max(8, rect.top - height - 4) : below
    setBox({ top, left })
  }

  useEffect(() => {
    if (!props.open) {
      return
    }
    place()
    const frame: number = window.requestAnimationFrame(place)
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
    function onKey(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        props.onClose()
      }
    }
    window.addEventListener("pointerdown", onPointer)
    window.addEventListener("keydown", onKey)
    window.addEventListener("resize", props.onClose)
    return () => {
      window.cancelAnimationFrame(frame)
      window.removeEventListener("pointerdown", onPointer)
      window.removeEventListener("keydown", onKey)
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
        onClick={(event) => {
          event.stopPropagation()
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
          style={{ position: "fixed", top: box.top, left: box.left, minWidth: 188 }}
        >
          {props.items.map((item: MenuAction, index: number) => (
            <Fragment key={item.label}>
              {index === firstDanger && firstDanger > 0 ? <div className="tree-menu-sep" role="separator" /> : null}
              <button
                type="button"
                role="menuitem"
                className={item.danger === true ? "tree-menu-item danger" : "tree-menu-item"}
                onClick={item.onSelect}
              >
                {item.label}
              </button>
            </Fragment>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function RowGlyph(props: { node: WorkspaceNode; expanded: boolean; userHome: boolean }): ReactElement {
  return (
    <>
      {props.node.kind === "FOLDER" ? (
        <span className="chevron" aria-hidden="true">
          <Chevron open={props.expanded} />
        </span>
      ) : (
        <span className="chevron-spacer" />
      )}
      <span className={`ws-ico ${iconKind(props.node, props.userHome)}`} aria-hidden="true">
        {props.userHome ? <PersonIcon /> : props.node.kind === "FOLDER" ? <FolderIcon open={props.expanded} /> : <FileIcon />}
      </span>
    </>
  )
}

function NameField(props: {
  initial: string
  label: string
  onCommit: (name: string) => Promise<void>
  onCancel: () => void
}): ReactElement {
  const inputRef: RefObject<HTMLInputElement | null> = useRef<HTMLInputElement | null>(null)
  const closed: RefObject<boolean> = useRef<boolean>(false)

  useEffect(() => {
    const input: HTMLInputElement | null = inputRef.current
    if (input === null) {
      return
    }
    input.focus()
    const dot: number = input.value.lastIndexOf(".")
    const end: number = dot > 0 ? dot : input.value.length
    input.setSelectionRange(0, end)
  }, [])

  function finish(commit: boolean, value: string): void {
    if (closed.current) {
      return
    }
    const next: string = value.trim()
    if (!commit || next === "" || next === props.initial) {
      closed.current = true
      props.onCancel()
      return
    }
    closed.current = true
    void props.onCommit(next).catch(() => {
      closed.current = false
      inputRef.current?.focus()
    })
  }

  return (
    <input
      ref={inputRef}
      className="ws-rename-input"
      aria-label={`Rename ${props.label}`}
      defaultValue={props.initial}
      spellCheck={false}
      onMouseDown={(event: MouseEvent<HTMLInputElement>) => {
        event.stopPropagation()
      }}
      onClick={(event: MouseEvent<HTMLInputElement>) => {
        event.stopPropagation()
      }}
      onDoubleClick={(event: MouseEvent<HTMLInputElement>) => {
        event.stopPropagation()
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault()
          finish(true, event.currentTarget.value)
        } else if (event.key === "Escape") {
          event.preventDefault()
          finish(false, "")
        }
      }}
      onBlur={(event) => {
        finish(true, event.currentTarget.value)
      }}
    />
  )
}

function uniqueFileName(base: string, taken: ReadonlySet<string>): string {
  if (!taken.has(base)) {
    return base
  }
  const dot: number = base.lastIndexOf(".")
  const stem: string = dot > 0 ? base.slice(0, dot) : base
  const extension: string = dot > 0 ? base.slice(dot) : ""
  let index: number = 2
  let candidate: string = `${stem}-${index}${extension}`
  while (taken.has(candidate)) {
    index += 1
    candidate = `${stem}-${index}${extension}`
  }
  return candidate
}

function canChange(node: WorkspaceNode): boolean {
  if (node.parent_id === null) {
    return false
  }
  const parts: string[] = node.path.split("/")
  if (parts.length <= 1) {
    return false
  }
  if (parts[0] === USERS_ROOT && parts.length === 2) {
    return false
  }
  return true
}

function isUserHome(node: WorkspaceNode): boolean {
  if (node.kind !== "FOLDER") {
    return false
  }
  const parts: string[] = node.path.split("/")
  return parts.length === 2 && parts[0] === USERS_ROOT
}

function rowLabel(node: WorkspaceNode, me: Me | null): string {
  if (me === null || !isUserHome(node)) {
    return node.name
  }
  const mine: boolean = node.owner_user_id === me.user_id || node.name === me.subject
  if (!mine) {
    return node.name
  }
  const friendly: string | null = personLabel(me)
  return friendly ?? node.name
}

function personLabel(me: Me): string | null {
  const name: string = me.name.trim()
  if (name !== "" && name !== "Signed in" && !UUID_NAME.test(name)) {
    return name
  }
  if (me.email !== null) {
    const local: string = me.email.split("@")[0] ?? ""
    if (local !== "") {
      return local
    }
  }
  return null
}

function iconKind(node: WorkspaceNode, userHome: boolean): string {
  if (userHome) {
    return "user"
  }
  if (node.kind === "FOLDER") {
    return "folder"
  }
  const lower: string = node.name.toLowerCase()
  if (lower.endsWith(".sql")) {
    return "sql"
  }
  if (lower.endsWith(".py")) {
    return "py"
  }
  if (lower.endsWith(".md")) {
    return "md"
  }
  if (lower.endsWith(".ipynb")) {
    return "ipynb"
  }
  if (lower.endsWith(".json")) {
    return "json"
  }
  return "file"
}
