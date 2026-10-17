import { useEffect, useState, type ReactElement } from "react"
import { useSearchParams } from "react-router-dom"
import { errorMessage } from "./api"
import { useEditorTitle } from "./editorSlots"
import { openInFocusedGroup } from "./editorLayout"
import { setPendingQuerySql, setSidebarView } from "./sidebarView"
import { ResultGrid } from "./ResultGrid"
import type { QueryResult } from "./types"
import { fetchNode, fetchNodeBlob, saveNodeText, type WorkspaceNode } from "./workspaceFilesApi"
import { getActiveWorkspaceId } from "./workspaceStore"

const TEXT_SUFFIXES: string[] = ["sql", "md", "py", "txt", "json", "yaml", "yml", "ipynb"]

export function WorkspaceFilePage(props: { nodeId: string }): ReactElement {
  const [params] = useSearchParams()
  const workspaceId: string = params.get("workspace") ?? getActiveWorkspaceId() ?? ""
  const [node, setNode] = useState<WorkspaceNode | null>(null)
  const [text, setText] = useState<string>("")
  const [saved, setSaved] = useState<string>("")
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<boolean>(false)
  const [rawNotebook, setRawNotebook] = useState<boolean>(false)
  const suffix: string = node === null ? "" : extension(node.name)
  const textFile: boolean = TEXT_SUFFIXES.includes(suffix)

  useEffect(() => {
    if (workspaceId === "") {
      return
    }
    const controller: AbortController = new AbortController()
    void fetchNode(workspaceId, props.nodeId)
      .then(async (loaded: WorkspaceNode) => {
        if (controller.signal.aborted) {
          return
        }
        setNode(loaded)
        const blob: Blob = await fetchNodeBlob(workspaceId, props.nodeId)
        if (controller.signal.aborted) {
          return
        }
        const url: string = URL.createObjectURL(blob)
        setBlobUrl((current: string | null) => {
          if (current !== null) {
            URL.revokeObjectURL(current)
          }
          return url
        })
        if (TEXT_SUFFIXES.includes(extension(loaded.name))) {
          const body: string = await blob.text()
          setText(body)
          setSaved(body)
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
  }, [workspaceId, props.nodeId])

  useEffect(() => {
    return () => {
      if (blobUrl !== null) {
        URL.revokeObjectURL(blobUrl)
      }
    }
  }, [blobUrl])

  async function save(): Promise<void> {
    const updated: WorkspaceNode = await saveNodeText(workspaceId, props.nodeId, text)
    setNode(updated)
    setSaved(text)
  }

  function runSql(): void {
    setSidebarView("query")
    setPendingQuerySql(text)
    openInFocusedGroup(`/query?sql=${encodeURIComponent(text)}`)
  }

  const dirty: boolean = text !== saved
  useEditorTitle(node === null ? null : `${dirty ? "● " : ""}${node.name}`)

  if (error !== null) {
    return <p className="error-text">{error}</p>
  }
  if (node === null) {
    return <p className="note">Loading file…</p>
  }
  return (
    <section className="page">
      <header className="page-header">
        <h1>{node.path}</h1>
        <div className="create-actions">
          {dirty ? <span className="note">Unsaved</span> : null}
          {suffix === "md" ? (
            <button type="button" className="button secondary" onClick={() => setPreview(!preview)}>
              {preview ? "Edit" : "Preview"}
            </button>
          ) : null}
          {suffix === "ipynb" ? (
            <button type="button" className="button secondary" onClick={() => setRawNotebook(!rawNotebook)}>
              {rawNotebook ? "Cells" : "Raw JSON"}
            </button>
          ) : null}
          {suffix === "sql" ? (
            <button type="button" className="button secondary" onClick={runSql}>
              Run
            </button>
          ) : null}
          {textFile ? (
            <button type="button" className="button" disabled={!dirty} onClick={() => void save()}>
              Save
            </button>
          ) : null}
          {blobUrl !== null ? (
            <a className="button secondary" href={blobUrl} download={node.name}>
              Download
            </a>
          ) : null}
        </div>
      </header>
      {textFile && !(suffix === "md" && preview) && !(suffix === "ipynb" && !rawNotebook) && suffix !== "csv" ? (
        <textarea className="file-editor" value={text} onChange={(event) => setText(event.target.value)} />
      ) : null}
      {suffix === "md" && preview ? <pre className="file-preview">{text}</pre> : null}
      {suffix === "csv" ? <ResultGrid result={csvGrid(text)} /> : null}
      {suffix === "pdf" && blobUrl !== null ? <iframe className="file-frame" title={node.name} src={blobUrl} /> : null}
      {suffix === "ipynb" && !rawNotebook ? <NotebookView source={text} /> : null}
      {!textFile && suffix !== "pdf" && suffix !== "csv" ? <p className="note">No preview for this file.</p> : null}
    </section>
  )
}

function NotebookView(props: { source: string }): ReactElement {
  let parsed: unknown
  try {
    parsed = JSON.parse(props.source)
  } catch {
    return <pre className="file-preview">{props.source}</pre>
  }
  const cells: unknown = typeof parsed === "object" && parsed !== null && "cells" in parsed ? parsed.cells : null
  if (!Array.isArray(cells)) {
    return <pre className="file-preview">{props.source}</pre>
  }
  return (
    <div className="notebook">
      {cells.map((cell: unknown, index: number) => (
        <article key={index} className="notebook-cell">
          <p className="note">{cellKind(cell)}</p>
          <pre>{cellSource(cell)}</pre>
          <pre className="note">{cellOutput(cell)}</pre>
        </article>
      ))}
    </div>
  )
}

function extension(name: string): string {
  const dot: number = name.lastIndexOf(".")
  if (dot < 0) {
    return ""
  }
  return name.slice(dot + 1).toLowerCase()
}

function csvGrid(text: string): QueryResult {
  const lines: string[] = text.split(/\r?\n/).filter((line: string) => line !== "").slice(0, 501)
  const header: string[] = (lines[0] ?? "").split(",")
  const rows: string[][] = lines.slice(1).map((line: string) => line.split(","))
  return {
    columns: header.map((name: string) => ({ name, type: "string" })),
    rows,
    truncated: lines.length > 500,
  }
}

function cellKind(cell: unknown): string {
  if (typeof cell === "object" && cell !== null && "cell_type" in cell && typeof cell.cell_type === "string") {
    return cell.cell_type
  }
  return "cell"
}

function cellSource(cell: unknown): string {
  if (typeof cell !== "object" || cell === null || !("source" in cell)) {
    return ""
  }
  const source: unknown = cell.source
  if (typeof source === "string") {
    return source
  }
  if (Array.isArray(source)) {
    return source.filter((part: unknown): part is string => typeof part === "string").join("")
  }
  return ""
}

function cellOutput(cell: unknown): string {
  if (typeof cell !== "object" || cell === null || !("outputs" in cell) || !Array.isArray(cell.outputs)) {
    return ""
  }
  const parts: string[] = []
  for (const output of cell.outputs) {
    if (typeof output === "object" && output !== null && "text" in output) {
      const text: unknown = output.text
      if (typeof text === "string") {
        parts.push(text)
      } else if (Array.isArray(text)) {
        parts.push(text.filter((part: unknown): part is string => typeof part === "string").join(""))
      }
    }
  }
  return parts.join("")
}
