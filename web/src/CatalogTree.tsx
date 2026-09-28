import { useMemo, useState, type ChangeEvent, type ReactElement } from "react"
import { Link } from "react-router-dom"
import { tablePath } from "./api"
import { CatalogIcon, Chevron, SchemaIcon, TableIcon } from "./icons"
import type { CatalogNode, CatalogTree, NamespaceNode, TableSummary } from "./types"

type CatalogTreeProps = {
  tree: CatalogTree | null
  loading: boolean
  error: string | null
  query: string
  onQueryChange: (value: string) => void
  onRetry: () => void
  selectedCatalog: string | undefined
  selectedNamespace: string | undefined
  selectedTable: string | undefined
}

export function CatalogTreePane(props: CatalogTreeProps): ReactElement {
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())
  const searching: boolean = props.query.trim() !== ""
  const visible: CatalogTree | null = useMemo(() => {
    if (props.tree === null) {
      return null
    }
    return filterTree(props.tree, props.query)
  }, [props.tree, props.query])

  function toggle(key: string): void {
    if (searching) {
      return
    }
    setCollapsed((current: ReadonlySet<string>) => {
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
    return collapsed.has(key)
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-title">Catalog</div>
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
        {visible !== null && visible.catalogs.length === 0 ? (
          <p className="tree-status">
            {searching ? "No tables match this search." : "No catalogs found in Polaris."}
          </p>
        ) : null}
        {visible?.catalogs.map((catalog: CatalogNode) => {
          const catalogKey: string = catalog.name
          const catalogClosed: boolean = closed(catalogKey)
          return (
            <div key={catalogKey} role="treeitem" aria-expanded={!catalogClosed}>
              <div className="tree-row depth-catalog">
                <button
                  type="button"
                  className="chevron"
                  aria-label={catalogClosed ? `Expand ${catalog.name}` : `Collapse ${catalog.name}`}
                  onClick={() => {
                    toggle(catalogKey)
                  }}
                >
                  <Chevron open={!catalogClosed} />
                </button>
                <CatalogIcon />
                <span className="tree-label">{catalog.name}</span>
              </div>
              {catalogClosed
                ? null
                : catalog.namespaces.map((namespace: NamespaceNode) => {
                    const namespaceKey: string = `${catalog.name}/${namespace.name}`
                    const namespaceClosed: boolean = closed(namespaceKey)
                    return (
                      <div key={namespaceKey} role="treeitem" aria-expanded={!namespaceClosed}>
                        <div className="tree-row depth-schema">
                          <button
                            type="button"
                            className="chevron"
                            aria-label={
                              namespaceClosed
                                ? `Expand ${namespace.name}`
                                : `Collapse ${namespace.name}`
                            }
                            onClick={() => {
                              toggle(namespaceKey)
                            }}
                          >
                            <Chevron open={!namespaceClosed} />
                          </button>
                          <SchemaIcon />
                          <span className="tree-label">{namespace.name}</span>
                        </div>
                        {namespaceClosed
                          ? null
                          : namespace.tables.map((table: TableSummary) => {
                              const selected: boolean =
                                props.selectedCatalog === catalog.name &&
                                props.selectedNamespace === namespace.name &&
                                props.selectedTable === table.name
                              return (
                                <Link
                                  key={table.name}
                                  className="tree-row depth-table"
                                  role="treeitem"
                                  to={tablePath(catalog.name, namespace.name, table.name)}
                                  aria-current={selected ? "page" : undefined}
                                >
                                  <span className="chevron-spacer" />
                                  <TableIcon />
                                  <span className="tree-label">{table.name}</span>
                                </Link>
                              )
                            })}
                      </div>
                    )
                  })}
            </div>
          )
        })}
      </div>
    </aside>
  )
}

function filterTree(tree: CatalogTree, query: string): CatalogTree {
  const needle: string = query.trim().toLowerCase()
  if (needle === "") {
    return tree
  }
  const catalogs: CatalogNode[] = []
  for (const catalog of tree.catalogs) {
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
  return { catalogs }
}
