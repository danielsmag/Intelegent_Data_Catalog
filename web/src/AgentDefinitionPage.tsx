import { useEffect, useState, type ReactElement } from "react"
import { useParams } from "react-router-dom"
import {
  fetchAgentDefinitionFile,
  fetchAgentDefinitionText,
  type AgentDefinitionFileDetail,
  type AgentDefinitionText,
} from "./agentDefinitionsApi"
import { useEditorTitle } from "./editorSlots"
import { HighlightedSource } from "./sourceHighlight"

export function AgentDefinitionPage(): ReactElement {
  const fileId: string | undefined = useParams().fileId
  const objectKey: string | undefined = useParams().objectKey
  const [source, setSource] = useState<string | null>(null)
  const [filename, setFilename] = useState<string>("")
  const [storedKey, setStoredKey] = useState<string>("")
  const [error, setError] = useState<string | null>(null)
  useEditorTitle(filename === "" ? null : filename)
  useEffect(() => {
    const controller: AbortController = new AbortController()
    const failed = (err: unknown): void => {
      if (controller.signal.aborted) {
        return
      }
      setSource(null)
      setStoredKey("")
      setError(err instanceof Error ? err.message : "Could not load definition file")
    }
    if (fileId !== undefined && fileId !== "") {
      void fetchAgentDefinitionFile(fileId, controller.signal)
        .then((row: AgentDefinitionFileDetail) => {
          setSource(row.source)
          setStoredKey(row.file.key)
          setFilename(fileName(row.file.key))
          setError(null)
        })
        .catch(failed)
    } else if (objectKey !== undefined && objectKey !== "") {
      const key: string = decodeURIComponent(objectKey)
      void fetchAgentDefinitionText(key, controller.signal)
        .then((row: AgentDefinitionText) => {
          setSource(row.source)
          setStoredKey(row.key)
          setFilename(fileName(row.key))
          setError(null)
        })
        .catch(failed)
    }
    return () => {
      controller.abort()
    }
  }, [fileId, objectKey])
  const heading: string = filename === "" ? "Definition" : filename
  return (
    <section className="page-grid">
      <header className="page-intro definition-head">
        <p className="eyebrow">{fileKind(filename)}</p>
        <h1>{heading}</h1>
        {storedKey !== "" ? <p className="definition-path">{displayPath(storedKey)}</p> : null}
      </header>
      {error !== null ? <p className="error-text">{error}</p> : null}
      {source === null && error === null ? <p className="note">Loading script…</p> : null}
      {source !== null ? <HighlightedSource filename={filename} source={source} /> : null}
    </section>
  )
}

function fileKind(name: string): string {
  const lower: string = name.toLowerCase()
  if (lower.endsWith(".py")) {
    return "Python"
  }
  if (lower.endsWith(".md")) {
    return "Markdown"
  }
  const dot: number = lower.lastIndexOf(".")
  if (dot < 0 || dot === lower.length - 1) {
    return "File"
  }
  return lower.slice(dot + 1)
}

function displayPath(key: string): string {
  const prefix: string = "agent-definitions/"
  return key.startsWith(prefix) ? key.slice(prefix.length) : key
}

function fileName(key: string): string {
  const name: string = key.split("/").pop() ?? key
  return name === "" ? key : name
}
