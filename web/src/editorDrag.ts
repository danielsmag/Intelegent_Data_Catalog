import { useSyncExternalStore } from "react"
import {
  fillTab,
  moveTab,
  openAt,
  openInFocusedGroup,
  openInGroup,
  openPathInGroupPreferBlank,
  splitOffTab,
  splitWithPath,
  type SplitDirection,
} from "./editorLayout"

export type DropTarget =
  | { kind: "insert"; groupId: string; index: number; blankTabId: string | null }
  | { kind: "edge"; groupId: string; direction: SplitDirection }
  | { kind: "group"; groupId: string }
  | { kind: "zone"; zoneId: string; accepts: "asset" }

export type DragSource =
  | { kind: "tab"; fromGroupId: string; tabId: string }
  | { kind: "path"; path: string }
  | { kind: "asset"; assetIds: string[]; path: string; openAll: boolean }

type DropHandler = (assetIds: string[]) => void

const dropZoneHandlers: Map<string, DropHandler> = new Map()

export function registerDropZone(zoneId: string, handler: DropHandler): () => void {
  dropZoneHandlers.set(zoneId, handler)
  return () => {
    if (dropZoneHandlers.get(zoneId) === handler) {
      dropZoneHandlers.delete(zoneId)
    }
  }
}

export type EditorDrag = {
  source: DragSource
  title: string
  x: number
  y: number
  target: DropTarget | null
}

const listeners: Set<() => void> = new Set()
let drag: EditorDrag | null = null
let watching: boolean = false

function emit(): void {
  for (const listener of listeners) {
    listener()
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useEditorDrag(): EditorDrag | null {
  return useSyncExternalStore(subscribe, () => drag, () => null)
}

function targetAt(x: number, y: number): DropTarget | null {
  const hit: Element | null = document.elementFromPoint(x, y)
  if (hit === null) {
    return null
  }
  if (drag?.source.kind === "asset") {
    const zone: Element | null = hit.closest("[data-drop-zone]")
    if (zone instanceof HTMLElement) {
      const zoneId: string | undefined = zone.dataset.dropZone
      if (zoneId !== undefined && zoneId !== "") {
        return { kind: "zone", zoneId, accepts: "asset" }
      }
    }
  }
  const edge: Element | null = hit.closest("[data-drop-edge]")
  if (edge instanceof HTMLElement) {
    const groupId: string | undefined = edge.dataset.groupId
    const direction: string | undefined = edge.dataset.dropEdge
    if (groupId !== undefined && (direction === "row" || direction === "column")) {
      return { kind: "edge", groupId, direction }
    }
  }
  const tab: Element | null = hit.closest("[data-editor-tab]")
  if (tab instanceof HTMLElement) {
    const groupId: string | undefined = tab.dataset.groupId
    const indexRaw: string | undefined = tab.dataset.tabIndex
    if (groupId === undefined || indexRaw === undefined) {
      return null
    }
    const index: number = Number(indexRaw)
    if (!Number.isFinite(index)) {
      return null
    }
    const rect: DOMRect = tab.getBoundingClientRect()
    const place: number = x < rect.left + rect.width / 2 ? index : index + 1
    const tabId: string | undefined = tab.dataset.tabId
    const blankTabId: string | null = tab.dataset.tabBlank === "true" && tabId !== undefined ? tabId : null
    return { kind: "insert", groupId, index: place, blankTabId }
  }
  const tabs: Element | null = hit.closest("[data-editor-tabs]")
  if (tabs instanceof HTMLElement) {
    const groupId: string | undefined = tabs.dataset.groupId
    const count: number = Number(tabs.dataset.tabCount ?? "0")
    if (groupId === undefined || !Number.isFinite(count)) {
      return null
    }
    return { kind: "insert", groupId, index: count, blankTabId: null }
  }
  const group: Element | null = hit.closest("[data-editor-group]")
  if (group instanceof HTMLElement) {
    const groupId: string | undefined = group.dataset.groupId
    if (groupId !== undefined) {
      return { kind: "group", groupId }
    }
  }
  return null
}

function swallowClick(): void {
  function eat(event: MouseEvent): void {
    event.preventDefault()
    event.stopPropagation()
    window.removeEventListener("click", eat, true)
  }
  window.addEventListener("click", eat, true)
  window.setTimeout(() => {
    window.removeEventListener("click", eat, true)
  }, 50)
}

function applyDrop(source: DragSource, target: DropTarget): void {
  if (source.kind === "asset") {
    if (target.kind === "zone") {
      const handler: DropHandler | undefined = dropZoneHandlers.get(target.zoneId)
      if (handler !== undefined) {
        handler(source.assetIds)
      }
      return
    }
    openIncludedAssets(source, target)
    return
  }
  if (source.kind === "tab") {
    if (target.kind === "insert") {
      moveTab(source.fromGroupId, source.tabId, target.groupId, target.index)
      return
    }
    if (target.kind === "edge") {
      splitOffTab(source.fromGroupId, source.tabId, target.direction, target.groupId)
    }
    return
  }
  applyPathDrop(source, target)
}

function openIncludedAssets(
  source: { assetIds: string[]; path: string; openAll: boolean },
  target: DropTarget,
): void {
  const paths: string[] = source.openAll
    ? source.assetIds.map((id: string) => `/assets/${id}`)
    : [source.path]
  const first: string | undefined = paths[0]
  if (first === undefined) {
    return
  }
  applyPathDrop({ kind: "path", path: first }, target)
  const rest: string[] = paths.slice(1)
  if (target.kind === "insert") {
    rest.forEach((path: string, offset: number) => {
      openAt(target.groupId, path, target.index + offset + 1)
    })
    return
  }
  for (const path of rest) {
    if (target.kind === "edge") {
      openInFocusedGroup(path)
      continue
    }
    if (target.kind === "group") {
      openInGroup(target.groupId, path, "push")
    }
  }
}

function applyPathDrop(source: { kind: "path"; path: string }, target: DropTarget): void {
  if (target.kind === "zone") {
    return
  }
  if (target.kind === "insert" && target.blankTabId !== null) {
    fillTab(target.groupId, target.blankTabId, source.path)
    return
  }
  if (target.kind === "insert") {
    openAt(target.groupId, source.path, target.index)
    return
  }
  if (target.kind === "edge") {
    splitWithPath(target.groupId, target.direction, source.path)
    return
  }
  openPathInGroupPreferBlank(target.groupId, source.path)
}

function watchDrag(event: { button: number; pointerId: number; clientX: number; clientY: number }, source: DragSource, title: string): void {
  if (event.button !== 0 || watching) {
    return
  }
  watching = true
  const startX: number = event.clientX
  const startY: number = event.clientY
  const pointerId: number = event.pointerId
  let started: boolean = false
  function move(ev: PointerEvent): void {
    if (ev.pointerId !== pointerId) {
      return
    }
    if (!started) {
      const dx: number = ev.clientX - startX
      const dy: number = ev.clientY - startY
      if (dx * dx + dy * dy < 16) {
        return
      }
      started = true
      document.documentElement.classList.add("editor-dragging")
    }
    drag = {
      source,
      title,
      x: ev.clientX,
      y: ev.clientY,
      target: targetAt(ev.clientX, ev.clientY),
    }
    emit()
  }
  function finish(ev: PointerEvent, drop: boolean): void {
    if (ev.pointerId !== pointerId) {
      return
    }
    window.removeEventListener("pointermove", move)
    window.removeEventListener("pointerup", up)
    window.removeEventListener("pointercancel", cancel)
    watching = false
    const target: DropTarget | null = drop && started ? targetAt(ev.clientX, ev.clientY) : null
    drag = null
    emit()
    document.documentElement.classList.remove("editor-dragging")
    if (!started) {
      return
    }
    swallowClick()
    if (target === null) {
      return
    }
    applyDrop(source, target)
  }
  function up(ev: PointerEvent): void {
    finish(ev, true)
  }
  function cancel(ev: PointerEvent): void {
    finish(ev, false)
  }
  window.addEventListener("pointermove", move)
  window.addEventListener("pointerup", up)
  window.addEventListener("pointercancel", cancel)
}

export function watchTabDrag(event: { button: number; pointerId: number; clientX: number; clientY: number }, source: { groupId: string; tabId: string; title: string }): void {
  watchDrag(event, { kind: "tab", fromGroupId: source.groupId, tabId: source.tabId }, source.title)
}

export function watchAssetDrag(
  event: { button: number; pointerId: number; clientX: number; clientY: number },
  source: { assetIds: string[]; title: string; openAll?: boolean },
): void {
  const first: string | undefined = source.assetIds[0]
  if (first === undefined) {
    return
  }
  const extra: string = source.assetIds.length > 1 ? ` +${source.assetIds.length - 1}` : ""
  watchDrag(
    event,
    {
      kind: "asset",
      assetIds: source.assetIds,
      path: `/assets/${first}`,
      openAll: source.openAll === true,
    },
    `${source.title}${extra}`,
  )
}

export function watchPathDrag(event: { button: number; pointerId: number; clientX: number; clientY: number }, source: { path: string; title: string }): void {
  if (source.path === "") {
    return
  }
  watchDrag(event, { kind: "path", path: source.path }, source.title)
}
