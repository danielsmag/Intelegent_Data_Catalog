import type { ReactElement } from "react"
import { Link, useLocation } from "react-router-dom"
import { CatalogIcon, GraphIcon, LineageIcon, QueryIcon } from "./icons"

export function ActivityBar(): ReactElement {
  const path: string = useLocation().pathname
  const onCatalog: boolean = path === "/" || path.startsWith("/catalog/")
  const onQuery: boolean = path === "/query"
  return (
    <nav className="activity" aria-label="Views">
      <Link
        to="/"
        className="activity-button"
        aria-current={onCatalog ? "page" : undefined}
        aria-label="Catalog"
        title="Catalog"
      >
        <CatalogIcon />
      </Link>
      <Link
        to="/query"
        className="activity-button"
        aria-current={onQuery ? "page" : undefined}
        aria-label="Query"
        title="Query"
      >
        <QueryIcon />
      </Link>
      <button
        type="button"
        className="activity-button"
        disabled
        aria-label="Lineage"
        title="Available in a later version"
      >
        <LineageIcon />
      </button>
      <button
        type="button"
        className="activity-button"
        disabled
        aria-label="Graph"
        title="Available in a later version"
      >
        <GraphIcon />
      </button>
    </nav>
  )
}
