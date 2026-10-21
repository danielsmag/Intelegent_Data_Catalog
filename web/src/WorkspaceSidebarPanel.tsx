import { type ReactElement } from "react"
import { AssetsExplorer } from "./AssetsExplorer"
import { WorkspaceTree } from "./WorkspaceTree"
import { setWorkspaceSidebarTab, useWorkspaceSidebarTab, type WorkspaceSidebarTab } from "./workspaceSidebar"

export function WorkspaceSidebar(props: { collapsed: boolean; onToggleCollapsed: () => void }): ReactElement {
  const tab: WorkspaceSidebarTab = useWorkspaceSidebarTab()
  return (
    <div className="workspace-sidebar">
      <div className="workspace-sidebar__tabs" role="tablist" aria-label="Workspace">
        {(["files", "assets"] as const).map((name: WorkspaceSidebarTab) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={tab === name}
            className={tab === name ? "is-on" : ""}
            onClick={() => {
              setWorkspaceSidebarTab(name)
            }}
          >
            {name === "files" ? "Files" : "Assets"}
          </button>
        ))}
      </div>
      {tab === "files" ? (
        <WorkspaceTree collapsed={props.collapsed} onToggleCollapsed={props.onToggleCollapsed} />
      ) : (
        <AssetsExplorer />
      )}
    </div>
  )
}
