import { useEffect, useState, type ReactElement } from "react"
import { useParams } from "react-router-dom"
import {
  fetchAssetDefinitionFile,
  fetchAssetDefinitionText,
  type AssetDefinitionFileDetail,
  type AssetDefinitionText,
} from "./assetDefinitionsApi"
import { useEditorTitle } from "./editorSlots"
import { HighlightedSource } from "./sourceHighlight"

export function AssetDefinitionPage(): ReactElement {
  const fileId: string | undefined = useParams().fileId
  const objectKey: string | undefined = useParams().objectKey
  const [source, setSource] = useState<string | null>(null)
  const [filename, setFilename] = useState<string>("")
  const [error, setError] = useState<string | null>(null)
  useEditorTitle(filename === "" ? null : filename)
  useEffect(() => {
    const controller: AbortController = new AbortController()
    const failed = (err: unknown): void => {
      if (controller.signal.aborted) {
        return
      }
      setSource(null)
      setError(err instanceof Error ? err.message : "Could not load definition file")
    }
    if (fileId !== undefined && fileId !== "") {
      void fetchAssetDefinitionFile(fileId, controller.signal)
        .then((row: AssetDefinitionFileDetail) => {
          setSource(row.source)
          setFilename(fileName(row.file.key))
          setError(null)
        })
        .catch(failed)
    } else if (objectKey !== undefined && objectKey !== "") {
      const key: string = decodeURIComponent(objectKey)
      void fetchAssetDefinitionText(key, controller.signal)
        .then((row: AssetDefinitionText) => {
          setSource(row.source)
          setFilename(fileName(row.key))
          setError(null)
        })
        .catch(failed)
    }
    return () => {
      controller.abort()
    }
  }, [fileId, objectKey])
  return (
    <section className="page-grid">
      <header className="page-intro definition-head">
        <p className="eyebrow">Python</p>
        <h1>{filename === "" ? "Definition" : filename}</h1>
      </header>
      {error !== null ? <p className="error-text">{error}</p> : null}
      {source === null && error === null ? <p className="note">Loading script…</p> : null}
      {source !== null ? <HighlightedSource filename={filename} source={source} /> : null}
    </section>
  )
}

function fileName(key: string): string {
  const parts: string[] = key.split("/")
  return parts[parts.length - 1] ?? key
}
