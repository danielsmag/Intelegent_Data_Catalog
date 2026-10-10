import { useEffect, useMemo, useState, type ChangeEvent, type ReactElement } from "react"
import { Link } from "react-router-dom"
import { catalogTablePath } from "./api"
import { CatalogIcon, Chevron, RefreshIcon, SchemaIcon, SearchIcon, SourceIcon, TableIcon } from "./icons"
import { ResizeHandle } from "./ResizeHandle"
import type { CatalogNode, ExplorerSource, ExplorerTree, NamespaceNode, TableSummary } from "./types"

type CatalogTreeProps = {
  tree: ExplorerTree | null
  loading: boolean
  error: string | null
  query: string
  onQueryChange: (value: string) => void
  onRetry: () => void
  selectedSource: string | undefined
  selectedCatalog: string | undefined
  selectedNamespace: string | undefined
  selectedTable: string | undefined
}

export function CatalogTreePane(props: CatalogTreeProps): ReactElement {
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set())
  const searching: boolean = props.query.trim() !== ""
  useEffect(() => {
    setOpen((current: ReadonlySet<string>) => {
      const next: Set<string> = new Set(current)
      if (props.selectedSource !== undefined) {
        next.add(`source:${props.selectedSource}`)
      }
      if (props.selectedSource !== undefined && props.selectedCatalog !== undefined) {
        next.add(`${props.selectedSource}/${props.selectedCatalog}`)
      }
      if (
        props.selectedSource !== undefined &&
        props.selectedCatalog !== undefined &&
        props.selectedNamespace !== undefined
      ) {
        next.add(`${props.selectedSource}/${props.selectedCatalog}/${props.selectedNamespace}`)
      }
      return next
    })
  }, [props.selectedSource, props.selectedCatalog, props.selectedNamespace])
  const visible: ExplorerTree | null = useMemo(() => {
    if (props.tree === null) {
      return null
    }
    return filterTree(props.tree, props.query)
  }, [props.tree, props.query])

  function toggle(key: string): void {
    if (searching) {
      return
    }
    setOpen((current: ReadonlySet<string>) => {
      const next: Set<string> = new Set(current)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
      }
      return next
    })
  }

  function closed(key: string): boolean {
    if (searching) {
      return false
    }
    return !open.has(key)
  }

  return (
    <aside className="sidebar catalog-browser">
      <div className="sidebar-head">
        <div className="sidebar-title">Catalog</div>
        <button
          type="button"
          className="sidebar-toggle"
          aria-label="Refresh catalog"
          title="Refresh catalog"
          onClick={props.onRetry}
        >
          <RefreshIcon />
        </button>
      </div>
      <label className="search-field">
        <SearchIcon />
        <input
          className="search"
          type="search"
          placeholder="Search tables"
          value={props.query}
          aria-label="Search tables"
          onChange={(event: ChangeEvent<HTMLInputElement>) => {
            props.onQueryChange(event.target.value)
          }}
        />
      </label>
      <div className="tree" role="tree">
        {props.loading ? <p className="tree-status">Loading catalog…</p> : null}
        {props.error !== null ? (
          <div className="tree-status">
            <p>{props.error}</p>
            <button type="button" className="button" onClick={props.onRetry}>
              Retry
            </button>
          </div>
        ) : null}
        {visible !== null && visible.sources.length === 0 ? (
          <p className="tree-status">{searching ? "No tables match this search." : "No sources found."}</p>
        ) : null}
        {visible?.sources.map((source: ExplorerSource) => {
          const sourceKey: string = `source:${source.id}`
          const sourceClosed: boolean = closed(sourceKey)
          return (
            <div key={source.id} role="treeitem" aria-expanded={!sourceClosed}>
              <div className="tree-row depth-source">
                <button
                  type="button"
                  className="chevron"
                  aria-label={sourceClosed ? `Expand ${source.name}` : `Collapse ${source.name}`}
                  onClick={() => {
                    toggle(sourceKey)
                  }}
                >
                  <Chevron open={!sourceClosed} />
                </button>
                <SourceIcon />
                <span className="tree-label figure-source">{source.name}</span>
              </div>
              {sourceClosed ? null : (
                <SourceBody
                  source={source}
                  closed={closed}
                  toggle={toggle}
                  selectedSource={props.selectedSource}
                  selectedCatalog={props.selectedCatalog}
                  selectedNamespace={props.selectedNamespace}
                  selectedTable={props.selectedTable}
                />
              )}
            </div>
          )
        })}
      </div>
      <ResizeHandle pane="sidebar" edge="end" label="Resize sidebar" />
    </aside>
  )
}

function SourceBody(props: {
  source: ExplorerSource
  closed: (key: string) => boolean
  toggle: (key: string) => void
  selectedSource: string | undefined
  selectedCatalog: string | undefined
  selectedNamespace: string | undefined
  selectedTable: string | undefined
}): ReactElement {
  if (!props.source.available) {
    return <p className="tree-status">{props.source.error ?? `${props.source.name} is unavailable.`}</p>
  }
  if (props.source.catalogs.length === 0) {
    return <p className="tree-status">{`No catalogs in ${props.source.name}.`}</p>
  }
  return (
    <>
      {props.source.catalogs.map((catalog: CatalogNode) => {
        const catalogKey: string = `${props.source.id}/${catalog.name}`
        const catalogClosed: boolean = props.closed(catalogKey)
        return (
          <div key={catalogKey} role="treeitem" aria-expanded={!catalogClosed}>
            <div className="tree-row depth-catalog">
              <button
                type="button"
                className="chevron"
                aria-label={catalogClosed ? `Expand ${catalog.name}` : `Collapse ${catalog.name}`}
                onClick={() => {
                  props.toggle(catalogKey)
                }}
              >
                <Chevron open={!catalogClosed} />
              </button>
              <CatalogIcon />
              <span className="tree-label figure-catalog">{catalog.name}</span>
            </div>
            {catalogClosed
              ? null
              : catalog.namespaces.map((namespace: NamespaceNode) => (
                  <NamespaceBranch
                    key={`${catalogKey}/${namespace.name}`}
                    sourceId={props.source.id}
                    catalogName={catalog.name}
                    namespace={namespace}
                    closed={props.closed}
                    toggle={props.toggle}
                    selectedSource={props.selectedSource}
                    selectedCatalog={props.selectedCatalog}
                    selectedNamespace={props.selectedNamespace}
                    selectedTable={props.selectedTable}
                  />
                ))}
          </div>
        )
      })}
    </>
  )
}

function NamespaceBranch(props: {
  sourceId: string
  catalogName: string
  namespace: NamespaceNode
  closed: (key: string) => boolean
  toggle: (key: string) => void
  selectedSource: string | undefined
  selectedCatalog: string | undefined
  selectedNamespace: string | undefined
  selectedTable: string | undefined
}): ReactElement {
  const namespaceKey: string = `${props.sourceId}/${props.catalogName}/${props.namespace.name}`
  const namespaceClosed: boolean = props.closed(namespaceKey)
  return (
    <div role="treeitem" aria-expanded={!namespaceClosed}>
      <div className="tree-row depth-schema">
        <button
          type="button"
          className="chevron"
          aria-label={
            namespaceClosed ? `Expand ${props.namespace.name}` : `Collapse ${props.namespace.name}`
          }
          onClick={() => {
            props.toggle(namespaceKey)
          }}
        >
          <Chevron open={!namespaceClosed} />
        </button>
        <SchemaIcon />
        <span className="tree-label figure-scope">{props.namespace.name}</span>
      </div>
      {namespaceClosed
        ? null
        : props.namespace.tables.map((table: TableSummary) => {
            const selected: boolean =
              props.selectedSource === props.sourceId &&
              props.selectedCatalog === props.catalogName &&
              props.selectedNamespace === props.namespace.name &&
              props.selectedTable === table.name
            return (
              <Link
                key={table.name}
                className="tree-row depth-table"
                role="treeitem"
                to={catalogTablePath(props.sourceId, props.catalogName, props.namespace.name, table.name)}
                aria-current={selected ? "page" : undefined}
              >
                <span className="chevron-spacer" />
                <TableIcon />
                <span className="tree-label figure-table">{table.name}</span>
              </Link>
            )
          })}
    </div>
  )
}

function filterTree(tree: ExplorerTree, query: string): ExplorerTree {
  const needle: string = query.trim().toLowerCase()
  if (needle === "") {
    return tree
  }
  const sources: ExplorerSource[] = []
  for (const source of tree.sources) {
    if (source.name.toLowerCase().includes(needle)) {
      sources.push(source)
      continue
    }
    const catalogs: CatalogNode[] = []
    for (const catalog of source.catalogs) {
      if (catalog.name.toLowerCase().includes(needle)) {
        catalogs.push(catalog)
        continue
      }
      const namespaces: NamespaceNode[] = []
      for (const namespace of catalog.namespaces) {
        if (namespace.name.toLowerCase().includes(needle)) {
          namespaces.push(namespace)
          continue
        }
        const tables: TableSummary[] = namespace.tables.filter((table: TableSummary) =>
          table.name.toLowerCase().includes(needle),
        )
        if (tables.length > 0) {
          namespaces.push({ name: namespace.name, tables })
        }
      }
      if (namespaces.length > 0) {
        catalogs.push({ name: catalog.name, namespaces })
      }
    }
    if (catalogs.length > 0) {
      sources.push({ ...source, catalogs })
    }
  }
  return { sources }
}
