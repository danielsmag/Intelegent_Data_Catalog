import { useEffect, useState, type ReactElement } from "react"
import { fetchAssetDefinitionObjects, type AssetDefinitionObject } from "./assetDefinitionsApi"
import { watchPathDrag } from "./editorDrag"
import { openInFocusedGroup } from "./editorLayout"
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
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())

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
    <div className="asset-files" role="tree" aria-label="Asset definition files">
      {nodes.map((node: AssetFileNode) => (
        <FileNode
          key={node.path}
          node={node}
          depth={0}
          collapsed={collapsed}
          onToggle={(path: string) => {
            setCollapsed(togglePath(collapsed, path))
          }}
        />
      ))}
    </div>
  )
}

function FileNode(props: {
  node: AssetFileNode
  depth: number
  collapsed: ReadonlySet<string>
  onToggle: (path: string) => void
}): ReactElement {
  const expanded: boolean = !props.collapsed.has(props.node.path)
  const pad: number = 6 + props.depth * 14
  if (props.node.kind === "file") {
    const pagePath: string = definitionPagePath(props.node)
    const rowClass: string = "tree-row tree-row--draggable"
    return (
      <div
        className={rowClass}
        role="treeitem"
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
              collapsed={props.collapsed}
              onToggle={props.onToggle}
            />
          ))
        : null}
    </div>
  )
}

function definitionPagePath(node: AssetFileNode): string {
  if (node.fileId !== null) {
    return `/asset-definitions/${node.fileId}/${encodeURIComponent(node.name)}`
  }
  return `/asset-definitions/text/${encodeURIComponent(node.objectKey)}`
}

function togglePath(collapsed: ReadonlySet<string>, path: string): ReadonlySet<string> {
  const next: Set<string> = new Set(collapsed)
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
