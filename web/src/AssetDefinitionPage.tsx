import { useEffect, useState, type ReactElement } from "react"
import { useParams } from "react-router-dom"
import {
  fetchAssetDefinitionFile,
  fetchAssetDefinitionText,
  type AssetDefinitionFileDetail,
  type AssetDefinitionItem,
  type AssetDefinitionText,
} from "./assetDefinitionsApi"
import { DefinitionDocument } from "./DefinitionDocument"
import { openInFocusedGroup } from "./editorLayout"
import { useEditorTitle } from "./editorSlots"

export function AssetDefinitionPage(): ReactElement {
  const fileId: string | undefined = useParams().fileId
  const objectKey: string | undefined = useParams().objectKey
  const [detail, setDetail] = useState<AssetDefinitionFileDetail | null>(null)
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
      setDetail(null)
      setSource(null)
      setStoredKey("")
      setError(err instanceof Error ? err.message : "Could not load definition file")
    }
    if (fileId !== undefined && fileId !== "") {
      void fetchAssetDefinitionFile(fileId, controller.signal)
        .then((row: AssetDefinitionFileDetail) => {
          setDetail(row)
          setSource(row.source)
          setStoredKey(row.file.key)
          setFilename(fileName(row.file.key))
          setError(null)
        })
        .catch(failed)
    } else if (objectKey !== undefined && objectKey !== "") {
      const key: string = decodeURIComponent(objectKey)
      void fetchAssetDefinitionText(key, controller.signal)
        .then((row: AssetDefinitionText) => {
          setDetail(null)
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
  const document: ReactElement | null = source === null ? null : (
    <DefinitionDocument filename={filename} objectKey={storedKey} source={source} />
  )
  return (
    <section className="page-grid">
      {document !== null ? null : (
        <header className="page-intro">
          <p className="eyebrow">Definition</p>
          <h1>{heading}</h1>
        </header>
      )}
      {error !== null ? <p className="error-text">{error}</p> : null}
      {source === null && error === null ? <p className="note">Loading script…</p> : null}
      {document !== null ? document : source !== null ? <pre className="source-code">{source}</pre> : null}
      {detail !== null && detail.items.length > 0 ? (
        <>
          <h2>Included</h2>
          <ul className="asset-membership-list">
            {detail.items.map((item: AssetDefinitionItem) => (
              <IncludedItem key={item.id} item={item} />
            ))}
          </ul>
        </>
      ) : null}
    </section>
  )
}

function IncludedItem(props: { item: AssetDefinitionItem }): ReactElement {
  const item: AssetDefinitionItem = props.item
  const label: string = `${item.item_key} · ${item.status}`
  return (
    <li>
      {item.asset_id !== null ? (
        <button
          type="button"
          onClick={() => {
            openInFocusedGroup(`/assets/${item.asset_id}`)
          }}
        >
          {label}
        </button>
      ) : (
        <span>{label}</span>
      )}
      {item.error !== null ? <p className="note">{item.error}</p> : null}
    </li>
  )
}

function fileName(key: string): string {
  const name: string = key.split("/").pop() ?? key
  return name === "" ? key : name
}
