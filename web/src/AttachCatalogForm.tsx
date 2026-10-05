import { useEffect, useMemo, useState, type ReactElement } from "react"
import { errorMessage, fetchExplorerTree, isAbortError } from "./api"
import { attachScopeCatalog, type AttachmentItem } from "./registryApi"
import type { CatalogNode, ExplorerSource, ExplorerTree, NamespaceNode, TableSummary } from "./types"

type AttachCatalogFormProps = {
  scopeId: string
  onCancel: () => void
  onAttached: () => void
}

export function AttachCatalogForm(props: AttachCatalogFormProps): ReactElement {
  const [tree, setTree] = useState<ExplorerTree | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [sourceId, setSourceId] = useState<string>("")
  const [catalogName, setCatalogName] = useState<string>("")
  const [namespaceName, setNamespaceName] = useState<string>("")
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const [pending, setPending] = useState<boolean>(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller: AbortController = new AbortController()
    void fetchExplorerTree(controller.signal)
      .then((loaded: ExplorerTree) => {
        setTree(loaded)
        setLoadError(null)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setLoadError(errorMessage(err))
        }
      })
    return () => {
      controller.abort()
    }
  }, [])

  const source: ExplorerSource | null = useMemo(() => {
    return tree?.sources.find((item: ExplorerSource) => item.id === sourceId) ?? null
  }, [tree, sourceId])
  const catalog: CatalogNode | null = useMemo(() => {
    return source?.catalogs.find((item: CatalogNode) => item.name === catalogName) ?? null
  }, [source, catalogName])
  const namespace: NamespaceNode | null = useMemo(() => {
    return catalog?.namespaces.find((item: NamespaceNode) => item.name === namespaceName) ?? null
  }, [catalog, namespaceName])
  const tables: TableSummary[] = namespace?.tables ?? []

  function chooseSource(next: string): void {
    setSourceId(next)
    setCatalogName("")
    setNamespaceName("")
    setSelected(new Set())
    setMessage(null)
  }

  function chooseCatalog(next: string): void {
    setCatalogName(next)
    setNamespaceName("")
    setSelected(new Set())
    setMessage(null)
  }

  function chooseNamespace(next: string): void {
    setNamespaceName(next)
    setSelected(new Set())
    setMessage(null)
  }

  function toggleTable(name: string): void {
    setSelected((current: ReadonlySet<string>) => {
      const next: Set<string> = new Set(current)
      if (next.has(name)) {
        next.delete(name)
      } else {
        next.add(name)
      }
      return next
    })
  }

  function toggleAll(): void {
    if (selected.size === tables.length) {
      setSelected(new Set())
      return
    }
    setSelected(new Set(tables.map((table: TableSummary) => table.name)))
  }

  function attach(entireSchema: boolean): void {
    if (source === null || catalog === null || namespace === null || pending) {
      return
    }
    const names: string[] = [...selected]
    if (!entireSchema && names.length === 0) {
      return
    }
    setPending(true)
    setError(null)
    setMessage(null)
    void attachScopeCatalog(props.scopeId, {
      source_id: source.id,
      catalog: catalog.name,
      namespace: namespace.name,
      tables: entireSchema ? null : names,
    })
      .then((items: AttachmentItem[]) => {
        setMessage(summarize(items))
        setSelected(new Set())
        props.onAttached()
        setPending(false)
      })
      .catch((err: unknown) => {
        setError(errorMessage(err))
        setPending(false)
      })
  }

  return (
    <form
      className="attach-panel"
      onSubmit={(event) => {
        event.preventDefault()
        attach(false)
      }}
    >
      <p className="note">Copy schemas or tables from a catalog source into this scope.</p>
      {loadError !== null ? <p className="error-text">{loadError}</p> : null}
      {tree === null && loadError === null ? <p className="note">Loading catalog…</p> : null}
      <div className="attach-grid">
        <label>
          Source
          <select
            aria-label="Catalog source"
            value={sourceId}
            disabled={pending || tree === null}
            onChange={(event) => {
              chooseSource(event.target.value)
            }}
          >
            <option value="">Select source</option>
            {(tree?.sources ?? []).map((item: ExplorerSource) => (
              <option key={item.id} value={item.id}>
                {item.available ? item.name : `${item.name} unavailable`}
              </option>
            ))}
          </select>
        </label>
        <label>
          Catalog
          <select
            aria-label="Catalog"
            value={catalogName}
            disabled={pending || source === null || !source.available}
            onChange={(event) => {
              chooseCatalog(event.target.value)
            }}
          >
            <option value="">Select catalog</option>
            {(source?.catalogs ?? []).map((item: CatalogNode) => (
              <option key={item.name} value={item.name}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Schema
          <select
            aria-label="Schema"
            value={namespaceName}
            disabled={pending || catalog === null}
            onChange={(event) => {
              chooseNamespace(event.target.value)
            }}
          >
            <option value="">Select schema</option>
            {(catalog?.namespaces ?? []).map((item: NamespaceNode) => (
              <option key={item.name} value={item.name}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {source !== null && !source.available ? (
        <p className="error-text">{source.error ?? `${source.name} is unavailable.`}</p>
      ) : null}
      {namespace !== null ? (
        <div className="attach-tables">
          <label>
            <input
              type="checkbox"
              checked={tables.length > 0 && selected.size === tables.length}
              disabled={pending || tables.length === 0}
              onChange={toggleAll}
            />
            {tables.length === 0 ? "No tables in this schema" : "Select all tables"}
          </label>
          {tables.map((table: TableSummary) => (
            <label key={table.name}>
              <input
                type="checkbox"
                checked={selected.has(table.name)}
                disabled={pending}
                onChange={() => {
                  toggleTable(table.name)
                }}
              />
              {table.name}
            </label>
          ))}
        </div>
      ) : null}
      {message !== null ? <p className="note">{message}</p> : null}
      {error !== null ? <p className="error-text">{error}</p> : null}
      <div className="create-actions">
        <button className="button" type="submit" disabled={pending || selected.size === 0}>
          {pending ? "Attaching…" : "Attach tables"}
        </button>
        <button
          className="button secondary"
          type="button"
          disabled={pending || namespace === null}
          onClick={() => {
            attach(true)
          }}
        >
          Attach schema
        </button>
        <button className="button secondary" type="button" disabled={pending} onClick={props.onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}

function summarize(items: AttachmentItem[]): string {
  if (items.length === 0) {
    return "This schema has no tables."
  }
  const created: number = items.filter((item: AttachmentItem) => item.outcome === "created").length
  const here: number = items.filter((item: AttachmentItem) => item.outcome === "already_in_scope").length
  const elsewhere: number = items.filter((item: AttachmentItem) => item.outcome === "already_elsewhere").length
  const parts: string[] = []
  if (created > 0) {
    parts.push(`Attached ${created}`)
  }
  if (here > 0) {
    parts.push(`${here} already in this scope`)
  }
  if (elsewhere > 0) {
    parts.push(`${elsewhere} already attached to another scope`)
  }
  return `${parts.join(". ")}.`
}
