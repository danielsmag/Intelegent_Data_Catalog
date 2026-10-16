import type { ReactElement } from "react"
import { AirflowIcon, BrandMark, CatalogIcon, GraphIcon, QueryIcon, SpaceIcon, TableIcon } from "./icons"
import { setSidebarView, useSidebarView, type SidebarView } from "./sidebarView"
import { setSpacesSidebarOpen } from "./spacesSidebar"

type Item = {
  view: SidebarView
  label: string
  icon: ReactElement
}

export function ActivityBar(): ReactElement {
  const current: SidebarView = useSidebarView()
  const items: Item[] = [
    { view: "spaces", label: "Spaces", icon: <SpaceIcon /> },
    { view: "catalog", label: "Catalog", icon: <CatalogIcon /> },
    { view: "airflow", label: "Airflow", icon: <AirflowIcon /> },
    { view: "search", label: "Search", icon: <QueryIcon /> },
    { view: "sources", label: "Sources", icon: <GraphIcon /> },
    { view: "query", label: "Query", icon: <TableIcon /> },
  ]
  return (
    <nav className="activity" aria-label="Views">
      <div className="activity-brand" aria-hidden="true">
        <BrandMark />
      </div>
      {items.map((item: Item) => (
        <button
          key={item.view}
          type="button"
          className="activity-button"
          aria-current={current === item.view ? "page" : undefined}
          aria-label={item.label}
          title={item.label}
          onClick={() => {
            setSidebarView(item.view)
            if (item.view === "spaces") {
              setSpacesSidebarOpen(true)
            }
          }}
        >
          {item.icon}
          <span>{item.label}</span>
        </button>
      ))}
    </nav>
  )
}
