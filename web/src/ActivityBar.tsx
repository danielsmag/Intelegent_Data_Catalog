import type { ReactElement } from "react"
import { CatalogIcon, GraphIcon, LineageIcon } from "./icons"

export function ActivityBar(): ReactElement {
  return (
    <nav className="activity" aria-label="Views">
      <button type="button" className="activity-button" aria-current="page" aria-label="Catalog" title="Catalog">
        <CatalogIcon />
      </button>
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
