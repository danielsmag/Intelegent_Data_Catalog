import type { ReactElement } from "react"
import { signOut, useAuthState } from "./auth"
import {
  AgentIcon,
  BrandMark,
  CatalogIcon,
  GearIcon,
  QueryIcon,
  SignOutIcon,
  TableIcon,
} from "./icons"
import { openInFocusedGroup } from "./editorLayout"
import { setSidebarView, useSidebarView, type SidebarView } from "./sidebarView"
import { setAgentsSidebarOpen } from "./agentsSidebarStore"
import { setAssetsSidebarOpen } from "./assetsSidebarStore"
import { setWorkspaceSidebarOpen } from "./workspaceSidebar"
import { useMe } from "./workspaceApi"

type Item = {
  view: SidebarView
  label: string
  icon: ReactElement
  gap?: boolean
}

function activityClass(item: Item): string {
  const names: string[] = ["activity-button"]
  if (item.gap === true) {
    names.push("activity-gap")
  }
  return names.join(" ")
}

export function ActivityBar(): ReactElement {
  const current: SidebarView = useSidebarView()
  const items: Item[] = [
    { view: "workspace", label: "Workspace", icon: <TableIcon /> },
    { view: "agents", label: "Agents", icon: <AgentIcon /> },
    { view: "assets", label: "Assets", icon: <CatalogIcon /> },
    { view: "catalog", label: "Catalog", icon: <CatalogIcon /> },
    { view: "search", label: "Search", icon: <QueryIcon />, gap: true },
    { view: "query", label: "Query", icon: <TableIcon /> },
  ]
  return (
    <nav className="activity" aria-label="Views">
      <div className="activity-scroll">
        <div className="activity-brand" aria-hidden="true">
          <BrandMark />
        </div>
        {items.map((item: Item) => (
          <button
            key={item.view}
            type="button"
            className={activityClass(item)}
            aria-current={current === item.view ? "page" : undefined}
            aria-label={item.label}
            title={item.label}
            onClick={() => {
              setSidebarView(item.view)
              if (item.view === "workspace") {
                setWorkspaceSidebarOpen(true)
              }
              if (item.view === "agents") {
                setAgentsSidebarOpen(true)
                openInFocusedGroup("/agents")
              }
              if (item.view === "assets") {
                setAssetsSidebarOpen(true)
                openInFocusedGroup("/assets")
              }
            }}
          >
            {item.icon}
            <span>{item.label}</span>
          </button>
        ))}
      </div>
      <UserMenu />
    </nav>
  )
}

function UserMenu(): ReactElement {
  const auth = useAuthState()
  const me = useMe()
  const label: string = displayName(me?.name ?? auth.name)
  const email: string | null = me?.email ?? auth.email
  const detail: string = email !== null && email !== "" ? `${label} · ${email}` : label
  return (
    <div className="activity-user">
      <span className="activity-avatar" title={detail}>
        {accountInitials(label)}
      </span>
      <span className="activity-user-name" title={detail}>
        {railLabel(label)}
      </span>
      <div className="activity-user-actions">
        <button
          type="button"
          className="activity-user-action"
          title="Manage workspaces"
          aria-label="Manage workspaces"
          onClick={() => openInFocusedGroup("/workspaces")}
        >
          <GearIcon />
        </button>
        {auth.status === "signed-in" ? (
          <button
            type="button"
            className="activity-user-action"
            title="Sign out"
            aria-label="Sign out"
            onClick={() => void signOut()}
          >
            <SignOutIcon />
          </button>
        ) : null}
      </div>
    </div>
  )
}

function railLabel(name: string): string {
  if (name.length <= 11) {
    return name
  }
  const first: string = name.split(/\s+/)[0] ?? name
  return first === "" ? name : first
}

function displayName(name: string): string {
  const trimmed: string = name.trim()
  if (trimmed === "" || trimmed !== trimmed.toLowerCase()) {
    return trimmed
  }
  return trimmed
    .split(/(\s+)/)
    .map((part: string) => (part.trim() === "" ? part : part.charAt(0).toUpperCase() + part.slice(1)))
    .join("")
}

function accountInitials(name: string): string {
  const parts: string[] = name.split(/\s+/).filter((part: string) => part.length > 0)
  const first: string = parts[0] ?? ""
  if (first === "") {
    return "?"
  }
  const last: string = parts.length > 1 ? (parts[parts.length - 1] ?? first) : ""
  if (last === "") {
    return first.slice(0, 2).toUpperCase()
  }
  return `${first.charAt(0)}${last.charAt(0)}`.toUpperCase()
}
