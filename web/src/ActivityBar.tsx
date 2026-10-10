import type { ReactElement } from "react"
import { Link, useLocation } from "react-router-dom"
import { AirflowIcon, BrandMark, CatalogIcon, GraphIcon, QueryIcon, SpaceIcon, TableIcon } from "./icons"
import { setSpacesSidebarOpen } from "./spacesSidebar"

type Item = {
  to: string
  label: string
  icon: ReactElement
  current: boolean
}

export function ActivityBar(): ReactElement {
  const path: string = useLocation().pathname
  const items: Item[] = [
    {
      to: "/",
      label: "Spaces",
      icon: <SpaceIcon />,
      current: path === "/" || path.startsWith("/spaces") || path.startsWith("/scopes") || path.startsWith("/registry/"),
    },
    {
      to: "/catalog",
      label: "Catalog",
      icon: <CatalogIcon />,
      current: path.startsWith("/catalog"),
    },
    {
      to: "/airflow",
      label: "Airflow",
      icon: <AirflowIcon />,
      current: path.startsWith("/airflow"),
    },
    {
      to: "/search",
      label: "Search",
      icon: <QueryIcon />,
      current: path.startsWith("/search"),
    },
    {
      to: "/sources",
      label: "Sources",
      icon: <GraphIcon />,
      current: path.startsWith("/sources"),
    },
    {
      to: "/query",
      label: "Query",
      icon: <TableIcon />,
      current: path === "/query",
    },
  ]
  return (
    <nav className="activity" aria-label="Views">
      <div className="activity-brand" aria-hidden="true">
        <BrandMark />
      </div>
      {items.map((item: Item) => (
        <Link
          key={item.to}
          to={item.to}
          className="activity-button"
          aria-current={item.current ? "page" : undefined}
          aria-label={item.label}
          title={item.label}
          onClick={() => {
            if (item.to === "/") {
              setSpacesSidebarOpen(true)
            }
          }}
        >
          {item.icon}
          <span>{item.label}</span>
        </Link>
      ))}
    </nav>
  )
}
