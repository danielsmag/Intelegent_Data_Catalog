import { type ReactElement } from "react"
import { WorkspaceTree } from "./WorkspaceTree"

export function WorkspaceSidebar(props: { collapsed: boolean; onToggleCollapsed: () => void }): ReactElement {
  return <WorkspaceTree collapsed={props.collapsed} onToggleCollapsed={props.onToggleCollapsed} />
}
