import type { ReactElement } from "react"
import { signOut, useAuthState } from "./auth"
import { AirflowIcon, BrandMark, CatalogIcon, GraphIcon, QueryIcon, SpaceIcon, TableIcon } from "./icons"
import { openInFocusedGroup } from "./editorLayout"
import { setSidebarView, useSidebarView, type SidebarView } from "./sidebarView"
import { setSpacesSidebarOpen } from "./spacesSidebar"
import { setWorkspaceSidebarOpen } from "./workspaceSidebar"
import { useMe } from "./workspaceApi"

type Item = {
  view: SidebarView
  label: string
  icon: ReactElement
  gap?: boolean
}

export function ActivityBar(): ReactElement {
  const current: SidebarView = useSidebarView()
  const items: Item[] = [
    { view: "workspace", label: "Workspace", icon: <TableIcon /> },
    { view: "spaces", label: "Spaces", icon: <SpaceIcon /> },
    { view: "catalog", label: "Catalog", icon: <CatalogIcon /> },
    { view: "airflow", label: "Airflow", icon: <AirflowIcon /> },
    { view: "search", label: "Search", icon: <QueryIcon />, gap: true },
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
          className={item.gap === true ? "activity-button activity-gap" : "activity-button"}
          aria-current={current === item.view ? "page" : undefined}
          aria-label={item.label}
          title={item.label}
          onClick={() => {
            setSidebarView(item.view)
            if (item.view === "spaces") {
              setSpacesSidebarOpen(true)
            }
            if (item.view === "workspace") {
              setWorkspaceSidebarOpen(true)
            }
          }}
        >
          {item.icon}
          <span>{item.label}</span>
        </button>
      ))}
      <UserMenu />
    </nav>
  )
}

function UserMenu(): ReactElement {
  const auth = useAuthState()
  const me = useMe()
  const label: string = me?.name ?? auth.name
  return (
    <div className="activity-user">
      <span>{label}</span>
      <button type="button" className="button secondary" onClick={() => openInFocusedGroup("/workspaces")}>
        Manage
      </button>
      {auth.status === "signed-in" ? (
        <button type="button" className="button secondary" onClick={() => void signOut()}>
          Sign out
        </button>
      ) : null}
    </div>
  )
}
