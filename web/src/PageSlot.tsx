import type { ReactElement, ReactNode } from "react"
import { ResizeHandle } from "./ResizeHandle"

export function PageSlot(props: { children: ReactNode }): ReactElement {
  return (
    <div className="page-slot">
      {props.children}
      <ResizeHandle pane="page" edge="end" label="Resize page" />
    </div>
  )
}
