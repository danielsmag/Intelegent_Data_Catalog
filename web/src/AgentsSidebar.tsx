import { type ReactElement } from "react"
import { AgentDefinitionFiles } from "./AgentDefinitionFiles"
import { PanelChevron } from "./icons"
import { ResizeHandle } from "./ResizeHandle"

export function AgentsSidebar(props: { collapsed: boolean; onToggleCollapsed: () => void }): ReactElement {
  const sidebarClass: string = props.collapsed ? "sidebar hierarchy collapsed" : "sidebar hierarchy"
  return (
    <aside className={sidebarClass} aria-label="Agents">
      <div className="sidebar-head">
        {props.collapsed ? (
          <span className="sidebar-rail-label">Agents</span>
        ) : (
          <div className="sidebar-title">
            <span>Agents</span>
          </div>
        )}
        <div className="sidebar-actions">
          <button
            type="button"
            className="sidebar-toggle"
            aria-expanded={!props.collapsed}
            aria-label={props.collapsed ? "Expand Agents sidebar" : "Minimize Agents sidebar"}
            title={props.collapsed ? "Expand sidebar" : "Minimize sidebar"}
            onClick={props.onToggleCollapsed}
          >
            <PanelChevron direction={props.collapsed ? "right" : "left"} />
          </button>
        </div>
      </div>
      {props.collapsed ? null : (
        <div className="agents-sidebar-body">
          <AgentDefinitionFiles />
        </div>
      )}
      <ResizeHandle pane="sidebar" edge="end" label="Resize sidebar" />
    </aside>
  )
}
