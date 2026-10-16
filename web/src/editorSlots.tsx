import { createContext, useContext, useEffect, useSyncExternalStore, type Context, type MouseEvent, type PointerEvent, type ReactElement, type ReactNode } from "react"
import { createPortal } from "react-dom"
import { watchPathDrag } from "./editorDrag"
import { openInFocusedGroup, setTabTitle, titleFromPath } from "./editorLayout"

export type EditorScope = {
  groupId: string
  tabId: string
  focused: boolean
}

export const EditorScopeContext: Context<EditorScope | null> = createContext<EditorScope | null>(null)
export const RailTargetContext: Context<HTMLElement | null> = createContext<HTMLElement | null>(null)

type StatusText = {
  area: string
  detail: string
}

let statusText: StatusText = { area: "Spaces", detail: "Spaces" }
const statusListeners: Set<() => void> = new Set()

function subscribeStatus(listener: () => void): () => void {
  statusListeners.add(listener)
  return () => {
    statusListeners.delete(listener)
  }
}

function getStatus(): StatusText {
  return statusText
}

function setStatus(area: string, detail: string): void {
  if (statusText.area === area && statusText.detail === detail) {
    return
  }
  statusText = { area, detail }
  for (const listener of statusListeners) {
    listener()
  }
}

export function useEditorStatusText(): StatusText {
  return useSyncExternalStore(subscribeStatus, getStatus, getStatus)
}

export function useEditorTitle(title: string | null): void {
  const scope: EditorScope | null = useContext(EditorScopeContext)
  const groupId: string | null = scope?.groupId ?? null
  const tabId: string | null = scope?.tabId ?? null
  useEffect(() => {
    if (groupId === null || tabId === null || title === null || title === "") {
      return
    }
    setTabTitle(groupId, tabId, title)
  }, [groupId, tabId, title])
}

export function useEditorStatus(area: string, detail: string): void {
  const scope: EditorScope | null = useContext(EditorScopeContext)
  const focused: boolean = scope?.focused ?? false
  useEffect(() => {
    if (!focused) {
      return
    }
    setStatus(area, detail)
  }, [focused, area, detail])
}

export function EditorRail(props: { children: ReactNode }): ReactElement | null {
  const scope: EditorScope | null = useContext(EditorScopeContext)
  const target: HTMLElement | null = useContext(RailTargetContext)
  if (scope === null || !scope.focused || target === null) {
    return null
  }
  return createPortal(props.children, target)
}

type WorkbenchLinkProps = {
  to: string
  className?: string
  role?: string
  title?: string
  children: ReactNode
  "aria-label"?: string
  "aria-current"?: "page" | undefined
  onClick?: () => void
}

export function WorkbenchLink(props: WorkbenchLinkProps): ReactElement {
  function onClick(event: MouseEvent<HTMLAnchorElement>): void {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) {
      return
    }
    event.preventDefault()
    props.onClick?.()
    openInFocusedGroup(props.to)
  }
  function onPointerDown(event: PointerEvent<HTMLAnchorElement>): void {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return
    }
    const label: string = props.title ?? event.currentTarget.innerText.replace(/\s+/g, " ").trim()
    watchPathDrag(event, { path: props.to, title: label === "" ? titleFromPath(props.to) : label })
  }
  return (
    <a
      href={props.to}
      className={props.className}
      role={props.role}
      title={props.title}
      aria-label={props["aria-label"]}
      aria-current={props["aria-current"]}
      draggable={false}
      onClick={onClick}
      onPointerDown={onPointerDown}
      onDragStart={(event) => {
        event.preventDefault()
      }}
    >
      {props.children}
    </a>
  )
}
