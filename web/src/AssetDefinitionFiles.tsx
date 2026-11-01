import { useEffect, useRef, useState, type ReactElement, type RefObject } from "react"
import { fetchAssetDefinitionObjects, type AssetDefinitionObject } from "./assetDefinitionsApi"
import { watchPathDrag } from "./editorDrag"
import { openInFocusedGroup, pathnameOf, useFocusedPath, useTreeReveal } from "./editorLayout"
import { Chevron, FileIcon, FolderIcon } from "./icons"

type AssetFileNode = {
  name: string
  path: string
  kind: "folder" | "file"
  fileId: string | null
  objectKey: string
  assetIds: string[]
  children: AssetFileNode[]
}

export function AssetDefinitionFiles(): ReactElement {
  const [nodes, setNodes] = useState<AssetFileNode[]>([])
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading")
  const [message, setMessage] = useState<string>("")
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set())
  const [revealTick, setRevealTick] = useState<number>(0)
  const treeRef: RefObject<HTMLDivElement | null> = useRef<HTMLDivElement>(null)
  const focused: string = useFocusedPath()
  const revealRequest: number = useTreeReveal()
  const currentPath: string | null = focusedFilePath(nodes, pathnameOf(focused))

  useEffect(() => {
    const controller: AbortController = new AbortController()
    void fetchAssetDefinitionObjects("assets", controller.signal)
      .then((files: AssetDefinitionObject[]) => {
        setNodes(buildTree(files))
        setStatus("ready")
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) {
          return
        }
        setNodes([])
        setStatus("error")
        setMessage(err instanceof Error ? err.message : "Could not list asset files")
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
    return <p className="tree-status">No files in the assets folder.</p>
  }
  return (
    <div ref={treeRef} className="asset-files" role="tree" aria-label="Asset definition files">
      {nodes.map((node: AssetFileNode) => (
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
  node: AssetFileNode
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
        onDoubleClick={() => {
          openInFocusedGroup(pagePath)
        }}
        onPointerDown={(event) => {
          watchPathDrag(event, { path: pagePath, title: props.node.name })
        }}
      >
        <span className="chevron-spacer" />
        <span className="asset-files__icon" aria-hidden="true">
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
        <span className="asset-files__icon" aria-hidden="true">
          <FolderIcon open={expanded} />
        </span>
        <span className="tree-label">{props.node.name}</span>
      </button>
      {expanded
        ? props.node.children.map((child: AssetFileNode) => (
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

function focusedFilePath(nodes: readonly AssetFileNode[], pathname: string): string | null {
  const parts: string[] = pathname.split("/").filter((part: string) => part.length > 0)
  if (parts[0] !== "asset-definitions") {
    return null
  }
  if (parts[1] === "text") {
    const encoded: string | undefined = parts[2]
    if (encoded === undefined) {
      return null
    }
    const key: string = decodeURIComponent(encoded)
    return findFile(nodes, (node: AssetFileNode) => node.objectKey === key)
  }
  const fileId: string | undefined = parts[1]
  if (fileId === undefined || fileId === "") {
    return null
  }
  return findFile(nodes, (node: AssetFileNode) => node.fileId === fileId)
}

function findFile(nodes: readonly AssetFileNode[], match: (node: AssetFileNode) => boolean): string | null {
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

function definitionPagePath(node: AssetFileNode): string {
  if (node.fileId !== null) {
    return `/asset-definitions/${node.fileId}/${encodeURIComponent(node.name)}`
  }
  return `/asset-definitions/text/${encodeURIComponent(node.objectKey)}`
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

function buildTree(files: readonly AssetDefinitionObject[]): AssetFileNode[] {
  const byPath: Map<string, AssetDefinitionObject> = new Map()
  for (const file of files) {
    byPath.set(file.path, file)
  }
  const root: AssetFileNode[] = []
  for (const file of files) {
    const parts: string[] = file.path.split("/").filter((part: string) => part !== "")
    let siblings: AssetFileNode[] = root
    let prefix: string = ""
    parts.forEach((part: string, index: number) => {
      prefix = prefix === "" ? part : `${prefix}/${part}`
      const kind: AssetFileNode["kind"] = index === parts.length - 1 ? "file" : "folder"
      let existing: AssetFileNode | undefined = siblings.find(
        (node: AssetFileNode) => node.path === prefix && node.kind === kind,
      )
      if (existing === undefined) {
        const source: AssetDefinitionObject | undefined = kind === "file" ? byPath.get(prefix) : undefined
        const assetIds: string[] = source?.asset_ids ?? []
        const fileId: string | null = source?.file_id ?? null
        const objectKey: string = source?.key ?? ""
        existing = { name: part, path: prefix, kind, fileId, objectKey, assetIds, children: [] }
        siblings.push(existing)
      }
      if (kind === "folder") {
        siblings = existing.children
      }
    })
  }
  return sortNodes(root)
}

function sortNodes(nodes: readonly AssetFileNode[]): AssetFileNode[] {
  const sorted: AssetFileNode[] = [...nodes].sort((left: AssetFileNode, right: AssetFileNode) => {
    if (left.kind !== right.kind) {
      return left.kind === "folder" ? -1 : 1
    }
    return left.name.localeCompare(right.name)
  })
  return sorted.map((node: AssetFileNode) => ({
    ...node,
    children: sortNodes(node.children),
  }))
}
