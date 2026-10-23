import { type ReactElement } from "react"
import { AssetsExplorer } from "./AssetsExplorer"
import { PanelChevron } from "./icons"
import { ResizeHandle } from "./ResizeHandle"

export function AssetsSidebar(props: { collapsed: boolean; onToggleCollapsed: () => void }): ReactElement {
  const sidebarClass: string = props.collapsed ? "sidebar hierarchy collapsed" : "sidebar hierarchy"
  return (
    <aside className={sidebarClass} aria-label="Assets">
      <div className="sidebar-head">
        {props.collapsed ? (
          <span className="sidebar-rail-label">Assets</span>
        ) : (
          <div className="sidebar-title">
            <span>Assets</span>
          </div>
        )}
        <div className="sidebar-actions">
          <button
            type="button"
            className="sidebar-toggle"
            aria-expanded={!props.collapsed}
            aria-label={props.collapsed ? "Expand Assets sidebar" : "Minimize Assets sidebar"}
            title={props.collapsed ? "Expand sidebar" : "Minimize sidebar"}
            onClick={props.onToggleCollapsed}
          >
            <PanelChevron direction={props.collapsed ? "right" : "left"} />
          </button>
        </div>
      </div>
      {props.collapsed ? null : (
        <div className="assets-sidebar-body">
          <AssetsExplorer />
        </div>
      )}
      <ResizeHandle pane="sidebar" edge="end" label="Resize sidebar" />
    </aside>
  )
}
