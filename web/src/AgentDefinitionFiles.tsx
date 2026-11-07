import { useEffect, useRef, useState, type ReactElement, type RefObject } from "react"
import { fetchAgentDefinitionObjects, type AgentDefinitionObject } from "./agentDefinitionsApi"
import { watchPathDrag } from "./editorDrag"
import { openInFocusedGroup, pathnameOf, useFocusedPath, useTreeReveal } from "./editorLayout"
import { Chevron, FileIcon, FolderIcon } from "./icons"

type AgentFileNode = {
  name: string
  path: string
  kind: "folder" | "file"
  fileId: string | null
  objectKey: string
  agentIds: string[]
  children: AgentFileNode[]
}

const openStorageKey: string = "agent-files-open"

function readOpenPaths(): ReadonlySet<string> {
  try {
    const raw: string | null = window.localStorage.getItem(openStorageKey)
    if (raw === null) {
      return new Set()
    }
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) {
      return new Set()
    }
    const paths: string[] = []
    for (const item of parsed) {
      if (typeof item === "string") {
        paths.push(item)
      }
    }
    return new Set(paths)
  } catch {
    return new Set()
  }
}

function writeOpenPaths(paths: ReadonlySet<string>): void {
  try {
    const stored: string[] = [...paths]
    window.localStorage.setItem(openStorageKey, JSON.stringify(stored))
  } catch {
    // Private mode can block storage. In-memory state still updates.
  }
}

export function AgentDefinitionFiles(): ReactElement {
  const [nodes, setNodes] = useState<AgentFileNode[]>([])
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading")
  const [message, setMessage] = useState<string>("")
  const [open, setOpen] = useState<ReadonlySet<string>>(readOpenPaths)
  const [revealTick, setRevealTick] = useState<number>(0)
  const treeRef: RefObject<HTMLDivElement | null> = useRef<HTMLDivElement>(null)
  const focused: string = useFocusedPath()
  const revealRequest: number = useTreeReveal()
  const currentPath: string | null = focusedFilePath(nodes, pathnameOf(focused))

  useEffect(() => {
    writeOpenPaths(open)
  }, [open])

  useEffect(() => {
    const controller: AbortController = new AbortController()
    void fetchAgentDefinitionObjects("agents", controller.signal)
      .then((files: AgentDefinitionObject[]) => {
        setNodes(buildTree(files))
        setStatus("ready")
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) {
          return
        }
        setNodes([])
        setStatus("error")
        setMessage(err instanceof Error ? err.message : "Could not list agent files")
      })
    return () => {
      controller.abort()
    }
  }, [])

  useEffect(() => {
    if (currentPath === null) {
      return
    }
    const folders: readonly string[] = ancestorFolders(currentPath)
    setOpen((current: ReadonlySet<string>) => {
      let changed: boolean = false
      const next: Set<string> = new Set(current)
      for (const folder of folders) {
        if (!next.has(folder)) {
          next.add(folder)
          changed = true
        }
      }
      return changed ? next : current
    })
    setRevealTick((current: number) => current + 1)
  }, [currentPath, revealRequest])

  useEffect(() => {
    if (revealTick === 0) {
      return
    }
    const row: Element | null = treeRef.current?.querySelector('[aria-current="page"]') ?? null
    row?.scrollIntoView({ block: "nearest" })
  }, [revealTick])

  if (status === "loading") {
    return <p className="tree-status">Loading files…</p>
  }
  if (status === "error") {
    return <p className="tree-status">{message}</p>
  }
  if (nodes.length === 0) {
    return <p className="tree-status">No files in the agents folder.</p>
  }
  return (
    <div ref={treeRef} className="agent-files" role="tree" aria-label="Agent definition files">
      {nodes.map((node: AgentFileNode) => (
        <FileNode
          key={node.path}
          node={node}
          depth={0}
          open={open}
          currentPath={currentPath}
          onToggle={(path: string) => {
            setOpen(togglePath(open, path))
          }}
        />
      ))}
    </div>
  )
}

function FileNode(props: {
  node: AgentFileNode
  depth: number
  open: ReadonlySet<string>
  currentPath: string | null
  onToggle: (path: string) => void
}): ReactElement {
  const expanded: boolean = props.open.has(props.node.path)
  const pad: number = 6 + props.depth * 14
  if (props.node.kind === "file") {
    const pagePath: string = definitionPagePath(props.node)
    const current: boolean = props.currentPath === props.node.path
    const rowClass: string = current ? "tree-row tree-row--draggable current-branch" : "tree-row tree-row--draggable"
    return (
      <div
        className={rowClass}
        role="treeitem"
        aria-current={current ? "page" : undefined}
        style={{ paddingLeft: pad }}
        title={props.node.name}
        onClick={() => {
          openInFocusedGroup(pagePath)
        }}
        onPointerDown={(event) => {
          watchPathDrag(event, { path: pagePath, title: props.node.name })
        }}
      >
        <span className="chevron-spacer" />
        <span className="agent-files__icon" aria-hidden="true">
          <FileIcon />
        </span>
        <span className="tree-label">{props.node.name}</span>
      </div>
    )
  }
  return (
    <div role="treeitem" aria-expanded={expanded}>
      <button
        type="button"
        className="tree-row"
        style={{ paddingLeft: pad }}
        onClick={() => {
          props.onToggle(props.node.path)
        }}
      >
        <span className="chevron" aria-hidden="true">
          <Chevron open={expanded} />
        </span>
        <span className="agent-files__icon" aria-hidden="true">
          <FolderIcon open={expanded} />
        </span>
        <span className="tree-label">{props.node.name}</span>
      </button>
      {expanded
        ? props.node.children.map((child: AgentFileNode) => (
            <FileNode
              key={child.path}
              node={child}
              depth={props.depth + 1}
              open={props.open}
              currentPath={props.currentPath}
              onToggle={props.onToggle}
            />
          ))
        : null}
    </div>
  )
}

function focusedFilePath(nodes: readonly AgentFileNode[], pathname: string): string | null {
  const parts: string[] = pathname.split("/").filter((part: string) => part.length > 0)
  if (parts[0] !== "agent-definitions") {
    return null
  }
  if (parts[1] === "text") {
    const encoded: string | undefined = parts[2]
    if (encoded === undefined) {
      return null
    }
    const key: string = decodeURIComponent(encoded)
    return findFile(nodes, (node: AgentFileNode) => node.objectKey === key)
  }
  const fileId: string | undefined = parts[1]
  if (fileId === undefined || fileId === "") {
    return null
  }
  return findFile(nodes, (node: AgentFileNode) => node.fileId === fileId)
}

function findFile(nodes: readonly AgentFileNode[], match: (node: AgentFileNode) => boolean): string | null {
  for (const node of nodes) {
    if (node.kind === "file" && match(node)) {
      return node.path
    }
    const nested: string | null = findFile(node.children, match)
    if (nested !== null) {
      return nested
    }
  }
  return null
}

function ancestorFolders(filePath: string): string[] {
  const parts: string[] = filePath.split("/")
  parts.pop()
  const folders: string[] = []
  let prefix: string = ""
  for (const part of parts) {
    prefix = prefix === "" ? part : `${prefix}/${part}`
    folders.push(prefix)
  }
  return folders
}

function definitionPagePath(node: AgentFileNode): string {
  if (node.fileId !== null) {
    return `/agent-definitions/${node.fileId}/${encodeURIComponent(node.name)}`
  }
  return `/agent-definitions/text/${encodeURIComponent(node.objectKey)}`
}

function togglePath(open: ReadonlySet<string>, path: string): ReadonlySet<string> {
  const next: Set<string> = new Set(open)
  if (next.has(path)) {
    next.delete(path)
  } else {
    next.add(path)
  }
  return next
}

function buildTree(files: readonly AgentDefinitionObject[]): AgentFileNode[] {
  const byPath: Map<string, AgentDefinitionObject> = new Map()
  for (const file of files) {
    byPath.set(file.path, file)
  }
  const root: AgentFileNode[] = []
  for (const file of files) {
    const parts: string[] = file.path.split("/").filter((part: string) => part !== "")
    let siblings: AgentFileNode[] = root
    let prefix: string = ""
    parts.forEach((part: string, index: number) => {
      prefix = prefix === "" ? part : `${prefix}/${part}`
      const kind: AgentFileNode["kind"] = index === parts.length - 1 ? "file" : "folder"
      let existing: AgentFileNode | undefined = siblings.find(
        (node: AgentFileNode) => node.path === prefix && node.kind === kind,
      )
      if (existing === undefined) {
        const source: AgentDefinitionObject | undefined = kind === "file" ? byPath.get(prefix) : undefined
        const agentIds: string[] = source?.agent_ids ?? []
        const fileId: string | null = source?.file_id ?? null
        const objectKey: string = source?.key ?? ""
        existing = { name: part, path: prefix, kind, fileId, objectKey, agentIds, children: [] }
        siblings.push(existing)
      }
      if (kind === "folder") {
        siblings = existing.children
      }
    })
  }
  return sortNodes(root)
}

function sortNodes(nodes: readonly AgentFileNode[]): AgentFileNode[] {
  const sorted: AgentFileNode[] = [...nodes].sort((left: AgentFileNode, right: AgentFileNode) => {
    if (left.kind !== right.kind) {
      return left.kind === "folder" ? -1 : 1
    }
    return left.name.localeCompare(right.name)
  })
  return sorted.map((node: AgentFileNode) => ({
    ...node,
    children: sortNodes(node.children),
  }))
}
