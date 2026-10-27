import { useSyncExternalStore } from "react"
import { redirectSidebarPath, sidebarViewForPath } from "./sidebarView"

export type SplitDirection = "row" | "column"

export type EditorTab = {
  id: string
  path: string
  title: string
}

export type EditorGroup = {
  id: string
  tabs: EditorTab[]
  activeId: string | null
}

export type LayoutNode =
  | { type: "group"; id: string; groupId: string }
  | { type: "split"; id: string; direction: SplitDirection; sizes: number[]; children: LayoutNode[] }

export type EditorLayout = {
  root: LayoutNode
  groups: Record<string, EditorGroup>
  focusedGroupId: string
}

export type RouteIds = {
  spaceId: string | null
  scopeId: string | null
  tableId: string | null
  assetId: string | null
  instanceId: string | null
  dagId: string | null
  source: string | null
  catalog: string | null
  namespace: string | null
  explorerTable: string | null
}

export type SidebarKind = "spaces" | "catalog" | "none"

type HistoryMode = "push" | "replace" | "none"

type HistoryRequest = {
  mode: "push" | "replace"
  groupId: string
  tabId: string
  path: string
}

type EditorHistoryMarker = {
  editorGroupId: string
  editorTabId: string
}

const STORAGE_KEY: string = "idc-editor-layout"
const listeners: Set<() => void> = new Set()
let persistTimer: number | undefined
let navigation: "POP" | "PUSH" | "REPLACE" = "POP"
let layout: EditorLayout = boot()

function newId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID()
  }
  return `ed-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

export function pathnameOf(path: string): string {
  const query: number = path.indexOf("?")
  return query >= 0 ? path.slice(0, query) : path
}

export function searchOf(path: string): string {
  const query: number = path.indexOf("?")
  return query >= 0 ? path.slice(query) : ""
}

export function normalizePath(path: string): string {
  const hash: number = path.indexOf("#")
  const withoutHash: string = hash >= 0 ? path.slice(0, hash) : path
  if (withoutHash === "") {
    return "/"
  }
  return withoutHash.startsWith("/") ? withoutHash : `/${withoutHash}`
}

export function titleFromPath(path: string): string {
  const pathname: string = pathnameOf(normalizePath(path))
  const parts: string[] = pathname.split("/").filter((part: string) => part.length > 0)
  const head: string | undefined = parts[0]
  if (head === undefined || (head === "spaces" && parts.length === 1)) {
    return "Spaces"
  }
  if (head === "spaces") {
    return "Space"
  }
  if (head === "scopes") {
    return "Scope"
  }
  if (head === "registry") {
    return "Table"
  }
  if (head === "airflow") {
    return "Assets"
  }
  if (head === "catalog") {
    const table: string | undefined = parts[4]
    return table === undefined ? "Catalog" : decodeURIComponent(table)
  }
  if (head === "query") {
    return "Query"
  }
  if (head === "search") {
    return "Search"
  }
  if (head === "sources") {
    return "Catalog"
  }
  if (head === "assets") {
    return parts[1] === undefined ? "Assets" : "Asset"
  }
  if (head === "asset-definitions") {
    if (parts[1] === "text") {
      const encodedKey: string | undefined = parts[2]
      if (encodedKey === undefined) {
        return "Definition"
      }
      const objectName: string | undefined = decodeURIComponent(encodedKey).split("/").pop()
      return objectName === undefined || objectName === "" ? "Definition" : objectName
    }
    const fileName: string | undefined = parts[2]
    return fileName === undefined ? "Definition" : decodeURIComponent(fileName)
  }
  if (head === "workspace" && parts[1] === "assets") {
    return "Assets"
  }
  if (head === "workspace") {
    return "File"
  }
  if (head === "workspaces") {
    return "Workspaces"
  }
  return "Page"
}

export function sidebarKind(pathname: string): SidebarKind {
  if (pathname === "/query" || pathname.startsWith("/search")) {
    return "none"
  }
  if (pathname.startsWith("/catalog")) {
    return "catalog"
  }
  return "spaces"
}

export function parseRouteIds(pathname: string): RouteIds {
  const parts: string[] = pathname.split("/").filter((part: string) => part.length > 0)
  const ids: RouteIds = {
    spaceId: null,
    scopeId: null,
    tableId: null,
    assetId: null,
    instanceId: null,
    dagId: null,
    source: null,
    catalog: null,
    namespace: null,
    explorerTable: null,
  }
  const head: string | undefined = parts[0]
  const second: string | undefined = parts[1]
  if (head === "spaces" && second !== undefined) {
    ids.spaceId = decodeURIComponent(second)
  }
  if (head === "scopes" && second !== undefined) {
    ids.scopeId = decodeURIComponent(second)
  }
  if (head === "registry" && second === "tables" && parts[2] !== undefined) {
    ids.tableId = decodeURIComponent(parts[2])
  }
  if (head === "assets" && second !== undefined) {
    ids.assetId = decodeURIComponent(second)
  }
  if (head === "airflow" && second !== undefined) {
    ids.instanceId = decodeURIComponent(second)
    if (parts[2] === "dags" && parts[3] !== undefined) {
      ids.dagId = decodeURIComponent(parts[3])
    }
  }
  if (head === "catalog" && parts[4] !== undefined && second !== undefined && parts[2] !== undefined && parts[3] !== undefined) {
    ids.source = decodeURIComponent(second)
    ids.catalog = decodeURIComponent(parts[2])
    ids.namespace = decodeURIComponent(parts[3])
    ids.explorerTable = decodeURIComponent(parts[4])
  }
  return ids
}

function currentUrl(): string {
  if (typeof window === "undefined") {
    return "/"
  }
  return normalizePath(`${window.location.pathname}${window.location.search}`)
}

function createInitial(path: string): EditorLayout {
  const groupId: string = newId()
  const tabId: string = newId()
  const tab: EditorTab = { id: tabId, path: normalizePath(path), title: titleFromPath(path) }
  return {
    root: { type: "group", id: newId(), groupId },
    groups: { [groupId]: { id: groupId, tabs: [tab], activeId: tabId } },
    focusedGroupId: groupId,
  }
}

function isAuthCallback(path: string): boolean {
  return pathnameOf(normalizePath(path)) === "/callback"
}

function boot(): EditorLayout {
  const url: string = currentUrl()
  const callback: boolean = isAuthCallback(url)
  const sidebarOnly: boolean = sidebarViewForPath(url) !== null
  const saved: EditorLayout | null = readStorage()
  const base: EditorLayout = saved ?? createInitial(sidebarOnly || callback ? "/" : url)
  const opened: EditorLayout =
    sidebarOnly || callback
      ? base
      : (pathOfGroup(base, base.focusedGroupId) ?? "/") === url
        ? base
        : openPath(base, base.focusedGroupId, url, "none").state
  const cleaned: EditorLayout = dropAuthTabs(dropSidebarTabs(opened))
  if (!callback) {
    stampCurrentEntry(cleaned)
  }
  return cleaned
}

function dropAuthTabs(state: EditorLayout): EditorLayout {
  return dropTabs(state, (tab: EditorTab) => isAuthCallback(tab.path))
}

function dropSidebarTabs(state: EditorLayout): EditorLayout {
  return dropTabs(state, (tab: EditorTab) => sidebarViewForPath(tab.path) !== null)
}

function dropTabs(state: EditorLayout, reject: (tab: EditorTab) => boolean): EditorLayout {
  let changed: boolean = false
  const groups: Record<string, EditorGroup> = {}
  for (const [id, group] of Object.entries(state.groups)) {
    const tabs: EditorTab[] = group.tabs.filter((tab: EditorTab) => !reject(tab))
    if (tabs.length !== group.tabs.length) {
      changed = true
    }
    let activeId: string | null = group.activeId
    if (activeId !== null && !tabs.some((tab: EditorTab) => tab.id === activeId)) {
      const last: EditorTab | undefined = tabs[tabs.length - 1]
      activeId = last?.id ?? null
      changed = true
    }
    groups[id] =
      tabs.length === group.tabs.length && activeId === group.activeId ? group : { ...group, tabs, activeId }
  }
  if (!changed) {
    return state
  }
  return { ...state, groups }
}

function pathOfGroup(state: EditorLayout, groupId: string): string | null {
  const group: EditorGroup | undefined = state.groups[groupId]
  if (group === undefined || group.activeId === null) {
    return null
  }
  const tab: EditorTab | undefined = group.tabs.find((item: EditorTab) => item.id === group.activeId)
  return tab?.path ?? null
}

function stampCurrentEntry(state: EditorLayout): void {
  if (typeof window === "undefined") {
    return
  }
  const group: EditorGroup | undefined = state.groups[state.focusedGroupId]
  if (group === undefined || group.activeId === null) {
    return
  }
  const tab: EditorTab | undefined = group.tabs.find((item: EditorTab) => item.id === group.activeId)
  if (tab === undefined || tab.path === "") {
    return
  }
  const marker: EditorHistoryMarker = { editorGroupId: group.id, editorTabId: tab.id }
  window.history.replaceState(marker, "", tab.path)
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function emit(): void {
  for (const listener of listeners) {
    listener()
  }
}

function schedulePersist(): void {
  if (typeof window === "undefined") {
    return
  }
  window.clearTimeout(persistTimer)
  persistTimer = window.setTimeout(() => {
    try {
      const payload: { version: number; root: LayoutNode; groups: Record<string, EditorGroup>; focusedGroupId: string } = {
        version: 1,
        root: layout.root,
        groups: layout.groups,
        focusedGroupId: layout.focusedGroupId,
      }
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
    } catch {
      // Private mode can block storage. In-memory layout still applies.
    }
  }, 120)
}

function commit(next: EditorLayout, history: HistoryRequest | null): void {
  layout = next
  if (history !== null) {
    navigation = history.mode === "push" ? "PUSH" : "REPLACE"
  }
  emit()
  schedulePersist()
  if (history !== null) {
    syncBrowser(history)
  }
}

function apply(next: { state: EditorLayout; history: HistoryRequest | null }): void {
  if (next.state === layout && next.history === null) {
    return
  }
  commit(next.state, next.history)
}

export function syncFocusedTabUrl(): void {
  if (typeof window === "undefined") {
    return
  }
  const request: HistoryRequest | null = historyForFocus(layout)
  if (request === null) {
    window.history.replaceState(null, "", "/")
    return
  }
  const marker: EditorHistoryMarker = { editorGroupId: request.groupId, editorTabId: request.tabId }
  window.history.replaceState(marker, "", request.path)
}

function syncBrowser(request: HistoryRequest): void {
  if (typeof window === "undefined") {
    return
  }
  const marker: EditorHistoryMarker = { editorGroupId: request.groupId, editorTabId: request.tabId }
  const current: string = `${window.location.pathname}${window.location.search}`
  if (request.mode === "push" && current !== request.path) {
    window.history.pushState(marker, "", request.path)
    return
  }
  window.history.replaceState(marker, "", request.path)
}

function historyForFocus(state: EditorLayout): HistoryRequest | null {
  const group: EditorGroup | undefined = state.groups[state.focusedGroupId]
  if (group === undefined || group.activeId === null) {
    return null
  }
  const tab: EditorTab | undefined = group.tabs.find((item: EditorTab) => item.id === group.activeId)
  if (tab === undefined || tab.path === "") {
    return null
  }
  return { mode: "replace", groupId: group.id, tabId: tab.id, path: tab.path }
}

function groupCount(node: LayoutNode): number {
  if (node.type === "group") {
    return 1
  }
  let count: number = 0
  for (const child of node.children) {
    count += groupCount(child)
  }
  return count
}

function firstGroupId(node: LayoutNode): string | null {
  if (node.type === "group") {
    return node.groupId
  }
  for (const child of node.children) {
    const found: string | null = firstGroupId(child)
    if (found !== null) {
      return found
    }
  }
  return null
}

function removeGroupNode(node: LayoutNode, groupId: string): LayoutNode | null {
  if (node.type === "group") {
    return node.groupId === groupId ? null : node
  }
  const children: LayoutNode[] = []
  const sizes: number[] = []
  node.children.forEach((child: LayoutNode, index: number) => {
    const next: LayoutNode | null = removeGroupNode(child, groupId)
    if (next !== null) {
      children.push(next)
      sizes.push(node.sizes[index] ?? 0)
    }
  })
  if (children.length === 0) {
    return null
  }
  const only: LayoutNode | undefined = children[0]
  if (children.length === 1 && only !== undefined) {
    return only
  }
  const total: number = sizes.reduce((sum: number, size: number) => sum + size, 0)
  const safe: number = total > 0 ? total : 1
  return { ...node, children, sizes: sizes.map((size: number) => size / safe) }
}

function placeBeside(
  node: LayoutNode,
  targetGroupId: string,
  direction: SplitDirection,
  leaf: LayoutNode,
): { node: LayoutNode; inserted: boolean } {
  if (node.type === "group") {
    if (node.groupId !== targetGroupId) {
      return { node, inserted: false }
    }
    return {
      inserted: true,
      node: { type: "split", id: newId(), direction, sizes: [0.5, 0.5], children: [node, leaf] },
    }
  }
  for (let index: number = 0; index < node.children.length; index += 1) {
    const child: LayoutNode | undefined = node.children[index]
    if (child === undefined) {
      continue
    }
    if (child.type === "group" && child.groupId === targetGroupId && node.direction === direction) {
      const sizes: number[] = node.sizes.slice()
      const current: number = sizes[index] ?? 1 / node.children.length
      sizes[index] = current / 2
      sizes.splice(index + 1, 0, current / 2)
      const children: LayoutNode[] = node.children.slice()
      children.splice(index + 1, 0, leaf)
      return { inserted: true, node: { ...node, children, sizes } }
    }
  }
  for (let index: number = 0; index < node.children.length; index += 1) {
    const child: LayoutNode | undefined = node.children[index]
    if (child === undefined) {
      continue
    }
    const inner: { node: LayoutNode; inserted: boolean } = placeBeside(child, targetGroupId, direction, leaf)
    if (!inner.inserted) {
      continue
    }
    const children: LayoutNode[] = node.children.slice()
    children[index] = inner.node
    return { inserted: true, node: { ...node, children } }
  }
  return { node, inserted: false }
}

function insertBesideNode(node: LayoutNode, targetGroupId: string, direction: SplitDirection, newGroupId: string): LayoutNode | null {
  const leaf: LayoutNode = { type: "group", id: newId(), groupId: newGroupId }
  const placed: { node: LayoutNode; inserted: boolean } = placeBeside(node, targetGroupId, direction, leaf)
  return placed.inserted ? placed.node : null
}

function openPath(
  state: EditorLayout,
  groupId: string,
  rawPath: string,
  mode: HistoryMode,
): { state: EditorLayout; history: HistoryRequest | null } {
  const group: EditorGroup | undefined = state.groups[groupId]
  if (group === undefined) {
    return { state, history: null }
  }
  if (redirectSidebarPath(rawPath) || isAuthCallback(rawPath)) {
    return { state, history: null }
  }
  const path: string = normalizePath(rawPath)
  const pathname: string = pathnameOf(path)
  const active: EditorTab | null =
    group.activeId === null ? null : group.tabs.find((item: EditorTab) => item.id === group.activeId) ?? null
  let nextTabs: EditorTab[] = group.tabs
  let activeId: string | null = group.activeId
  let historyMode: HistoryMode = mode
  if (active !== null && active.path === "") {
    nextTabs = group.tabs.map((item: EditorTab) =>
      item.id === active.id ? { ...item, path, title: titleFromPath(path) } : item,
    )
    activeId = active.id
  } else if (active !== null && pathnameOf(active.path) === pathname) {
    if (active.path === path && state.focusedGroupId === groupId) {
      return { state, history: null }
    }
    nextTabs = group.tabs.map((item: EditorTab) => (item.id === active.id ? { ...item, path } : item))
    activeId = active.id
    if (active.path === path) {
      historyMode = mode === "none" ? "none" : "replace"
    }
  } else if (mode === "replace" && active !== null) {
    nextTabs = group.tabs.map((item: EditorTab) =>
      item.id === active.id ? { ...item, path, title: titleFromPath(path) } : item,
    )
    activeId = active.id
  } else {
    const existing: EditorTab | undefined = group.tabs.find((item: EditorTab) => pathnameOf(item.path) === pathname)
    if (existing !== undefined) {
      nextTabs = group.tabs.map((item: EditorTab) => (item.id === existing.id ? { ...item, path } : item))
      activeId = existing.id
      if (existing.path === path && state.focusedGroupId === groupId && group.activeId === existing.id) {
        return { state, history: null }
      }
      if (existing.path === path) {
        historyMode = mode === "none" ? "none" : "replace"
      }
    } else {
      const tab: EditorTab = { id: newId(), path, title: titleFromPath(path) }
      nextTabs = [...group.tabs, tab]
      activeId = tab.id
    }
  }
  if (activeId === null) {
    return { state, history: null }
  }
  const next: EditorLayout = {
    ...state,
    focusedGroupId: groupId,
    groups: { ...state.groups, [groupId]: { ...group, tabs: nextTabs, activeId } },
  }
  const history: HistoryRequest | null =
    historyMode === "none" || historyMode === "push" || historyMode === "replace"
      ? historyMode === "none"
        ? null
        : { mode: historyMode, groupId, tabId: activeId, path }
      : null
  return { state: next, history }
}

export function getLayout(): EditorLayout {
  return layout
}

export function getNavigation(): "POP" | "PUSH" | "REPLACE" {
  return navigation
}

export function focusedPath(): string {
  return pathOfGroup(layout, layout.focusedGroupId) ?? "/"
}

export function useEditorLayout(): EditorLayout {
  return useSyncExternalStore(subscribe, getLayout, getLayout)
}

export function useFocusedPath(): string {
  const state: EditorLayout = useEditorLayout()
  return pathOfGroup(state, state.focusedGroupId) ?? "/"
}

let treeReveal: number = 0
const treeRevealListeners: Set<() => void> = new Set()

function subscribeTreeReveal(listener: () => void): () => void {
  treeRevealListeners.add(listener)
  return () => {
    treeRevealListeners.delete(listener)
  }
}

export function requestTreeReveal(): void {
  treeReveal += 1
  for (const listener of treeRevealListeners) {
    listener()
  }
}

export function useTreeReveal(): number {
  return useSyncExternalStore(subscribeTreeReveal, () => treeReveal, () => 0)
}

export function openInGroup(groupId: string, rawPath: string, mode: "push" | "replace"): void {
  apply(openPath(layout, groupId, rawPath, mode))
}

export function openInFocusedGroup(rawPath: string, mode: HistoryMode = "push"): void {
  apply(openPath(layout, layout.focusedGroupId, rawPath, mode))
}

export function openBlankTab(groupId: string): void {
  const group: EditorGroup | undefined = layout.groups[groupId]
  if (group === undefined) {
    return
  }
  const tab: EditorTab = { id: newId(), path: "", title: "New tab" }
  const next: EditorLayout = {
    ...layout,
    focusedGroupId: groupId,
    groups: { ...layout.groups, [groupId]: { ...group, tabs: [...group.tabs, tab], activeId: tab.id } },
  }
  commit(next, null)
}

export function openAt(groupId: string, rawPath: string, index: number): void {
  const group: EditorGroup | undefined = layout.groups[groupId]
  if (group === undefined || redirectSidebarPath(rawPath) || isAuthCallback(rawPath)) {
    return
  }
  const path: string = normalizePath(rawPath)
  const tab: EditorTab = { id: newId(), path, title: titleFromPath(path) }
  const tabs: EditorTab[] = group.tabs.slice()
  const clamped: number = Math.max(0, Math.min(index, tabs.length))
  tabs.splice(clamped, 0, tab)
  const next: EditorLayout = {
    ...layout,
    focusedGroupId: groupId,
    groups: { ...layout.groups, [groupId]: { ...group, tabs, activeId: tab.id } },
  }
  commit(next, { mode: "push", groupId, tabId: tab.id, path })
}

export function fillTab(groupId: string, tabId: string, rawPath: string): void {
  const group: EditorGroup | undefined = layout.groups[groupId]
  if (group === undefined || redirectSidebarPath(rawPath) || isAuthCallback(rawPath)) {
    return
  }
  const path: string = normalizePath(rawPath)
  const existing: EditorTab | undefined = group.tabs.find((item: EditorTab) => item.id === tabId)
  if (existing === undefined) {
    return
  }
  const tabs: EditorTab[] = group.tabs.map((item: EditorTab) =>
    item.id === tabId ? { ...item, path, title: titleFromPath(path) } : item,
  )
  const next: EditorLayout = {
    ...layout,
    focusedGroupId: groupId,
    groups: { ...layout.groups, [groupId]: { ...group, tabs, activeId: tabId } },
  }
  commit(next, { mode: "push", groupId, tabId, path })
}

export function openPathInGroupPreferBlank(groupId: string, rawPath: string): void {
  const group: EditorGroup | undefined = layout.groups[groupId]
  if (group === undefined) {
    return
  }
  const active: EditorTab | null =
    group.activeId === null ? null : group.tabs.find((item: EditorTab) => item.id === group.activeId) ?? null
  if (active !== null && active.path === "") {
    fillTab(groupId, active.id, rawPath)
    return
  }
  openInGroup(groupId, rawPath, "push")
}

export function splitWithPath(targetGroupId: string, direction: SplitDirection, rawPath: string): void {
  if (layout.groups[targetGroupId] === undefined || redirectSidebarPath(rawPath) || isAuthCallback(rawPath)) {
    return
  }
  const path: string = normalizePath(rawPath)
  const newGroupId: string = newId()
  const tab: EditorTab = { id: newId(), path, title: titleFromPath(path) }
  const newGroup: EditorGroup = { id: newGroupId, tabs: [tab], activeId: tab.id }
  const root: LayoutNode | null = insertBesideNode(layout.root, targetGroupId, direction, newGroupId)
  if (root === null) {
    return
  }
  commit(
    { root, groups: { ...layout.groups, [newGroupId]: newGroup }, focusedGroupId: newGroupId },
    { mode: "push", groupId: newGroupId, tabId: tab.id, path },
  )
}

export function focusGroup(groupId: string): void {
  if (layout.focusedGroupId === groupId || layout.groups[groupId] === undefined) {
    return
  }
  const next: EditorLayout = { ...layout, focusedGroupId: groupId }
  commit(next, historyForFocus(next))
}

export function focusTab(groupId: string, tabId: string): void {
  const group: EditorGroup | undefined = layout.groups[groupId]
  if (group === undefined || !group.tabs.some((item: EditorTab) => item.id === tabId)) {
    return
  }
  if (layout.focusedGroupId === groupId && group.activeId === tabId) {
    return
  }
  const next: EditorLayout = {
    ...layout,
    focusedGroupId: groupId,
    groups: { ...layout.groups, [groupId]: { ...group, activeId: tabId } },
  }
  commit(next, historyForFocus(next))
}

export function closeTab(groupId: string, tabId: string): void {
  const group: EditorGroup | undefined = layout.groups[groupId]
  if (group === undefined) {
    return
  }
  const index: number = group.tabs.findIndex((item: EditorTab) => item.id === tabId)
  if (index < 0) {
    return
  }
  const tabs: EditorTab[] = group.tabs.filter((item: EditorTab) => item.id !== tabId)
  if (tabs.length === 0) {
    if (groupCount(layout.root) <= 1) {
      commit({ ...layout, groups: { ...layout.groups, [groupId]: { ...group, tabs: [], activeId: null } } }, null)
      return
    }
    const groups: Record<string, EditorGroup> = { ...layout.groups }
    delete groups[groupId]
    const root: LayoutNode | null = removeGroupNode(layout.root, groupId)
    if (root === null) {
      return
    }
    let focused: string = layout.focusedGroupId
    if (focused === groupId || groups[focused] === undefined) {
      focused = firstGroupId(root) ?? focused
    }
    const next: EditorLayout = { root, groups, focusedGroupId: focused }
    commit(next, historyForFocus(next))
    return
  }
  let activeId: string | null = group.activeId
  if (activeId === tabId) {
    const neighbor: EditorTab | undefined = tabs[index] ?? tabs[index - 1]
    activeId = neighbor?.id ?? null
  }
  const next: EditorLayout = {
    ...layout,
    groups: { ...layout.groups, [groupId]: { ...group, tabs, activeId } },
  }
  const history: HistoryRequest | null =
    layout.focusedGroupId === groupId && group.activeId === tabId ? historyForFocus(next) : null
  commit(next, history)
}

export function closeOtherTabs(groupId: string, tabId: string): void {
  const group: EditorGroup | undefined = layout.groups[groupId]
  if (group === undefined) {
    return
  }
  const tab: EditorTab | undefined = group.tabs.find((item: EditorTab) => item.id === tabId)
  if (tab === undefined) {
    return
  }
  const next: EditorLayout = {
    ...layout,
    groups: { ...layout.groups, [groupId]: { ...group, tabs: [tab], activeId: tab.id } },
  }
  const history: HistoryRequest | null = layout.focusedGroupId === groupId ? historyForFocus(next) : null
  commit(next, history)
}

export function closeAllTabs(groupId: string): void {
  const group: EditorGroup | undefined = layout.groups[groupId]
  if (group === undefined) {
    return
  }
  if (groupCount(layout.root) <= 1) {
    commit({ ...layout, groups: { ...layout.groups, [groupId]: { ...group, tabs: [], activeId: null } } }, null)
    return
  }
  const groups: Record<string, EditorGroup> = { ...layout.groups }
  delete groups[groupId]
  const root: LayoutNode | null = removeGroupNode(layout.root, groupId)
  if (root === null) {
    return
  }
  const focused: string =
    layout.focusedGroupId === groupId ? firstGroupId(root) ?? layout.focusedGroupId : layout.focusedGroupId
  const next: EditorLayout = { root, groups, focusedGroupId: focused }
  commit(next, historyForFocus(next))
}

export function splitGroup(groupId: string, direction: SplitDirection): void {
  const group: EditorGroup | undefined = layout.groups[groupId]
  if (group === undefined) {
    return
  }
  const active: EditorTab | null =
    group.activeId === null ? null : group.tabs.find((item: EditorTab) => item.id === group.activeId) ?? null
  const newGroupId: string = newId()
  const tab: EditorTab | null = active === null ? null : { id: newId(), path: active.path, title: active.title }
  const newGroup: EditorGroup = { id: newGroupId, tabs: tab === null ? [] : [tab], activeId: tab?.id ?? null }
  const root: LayoutNode | null = insertBesideNode(layout.root, groupId, direction, newGroupId)
  if (root === null) {
    return
  }
  const next: EditorLayout = {
    root,
    groups: { ...layout.groups, [newGroupId]: newGroup },
    focusedGroupId: newGroupId,
  }
  const history: HistoryRequest | null =
    tab === null ? null : { mode: "replace", groupId: newGroupId, tabId: tab.id, path: tab.path }
  commit(next, history)
}

export function moveTab(fromGroupId: string, tabId: string, toGroupId: string, index: number): void {
  if (fromGroupId === toGroupId) {
    reorderTab(fromGroupId, tabId, index)
    return
  }
  const from: EditorGroup | undefined = layout.groups[fromGroupId]
  const to: EditorGroup | undefined = layout.groups[toGroupId]
  if (from === undefined || to === undefined) {
    return
  }
  const fromIndex: number = from.tabs.findIndex((item: EditorTab) => item.id === tabId)
  const tab: EditorTab | undefined = from.tabs[fromIndex]
  if (tab === undefined) {
    return
  }
  const fromTabs: EditorTab[] = from.tabs.filter((item: EditorTab) => item.id !== tabId)
  let fromActive: string | null = from.activeId
  if (fromActive === tabId) {
    const neighbor: EditorTab | undefined = fromTabs[fromIndex] ?? fromTabs[fromIndex - 1]
    fromActive = neighbor?.id ?? null
  }
  const toTabs: EditorTab[] = to.tabs.slice()
  const clamped: number = Math.max(0, Math.min(index, toTabs.length))
  toTabs.splice(clamped, 0, tab)
  const groups: Record<string, EditorGroup> = {
    ...layout.groups,
    [toGroupId]: { ...to, tabs: toTabs, activeId: tab.id },
    [fromGroupId]: { ...from, tabs: fromTabs, activeId: fromActive },
  }
  let root: LayoutNode = layout.root
  if (fromTabs.length === 0) {
    delete groups[fromGroupId]
    const removed: LayoutNode | null = removeGroupNode(root, fromGroupId)
    if (removed === null) {
      return
    }
    root = removed
  }
  const next: EditorLayout = { root, groups, focusedGroupId: toGroupId }
  commit(next, { mode: "replace", groupId: toGroupId, tabId: tab.id, path: tab.path })
}

function reorderTab(groupId: string, tabId: string, index: number): void {
  const group: EditorGroup | undefined = layout.groups[groupId]
  if (group === undefined) {
    return
  }
  const fromIndex: number = group.tabs.findIndex((item: EditorTab) => item.id === tabId)
  if (fromIndex < 0) {
    return
  }
  const tabs: EditorTab[] = group.tabs.slice()
  const removed: EditorTab | undefined = tabs.splice(fromIndex, 1)[0]
  if (removed === undefined) {
    return
  }
  const adjusted: number = index > fromIndex ? index - 1 : index
  const clamped: number = Math.max(0, Math.min(adjusted, tabs.length))
  if (clamped === fromIndex && layout.focusedGroupId === groupId && group.activeId === tabId) {
    return
  }
  tabs.splice(clamped, 0, removed)
  const next: EditorLayout = {
    ...layout,
    focusedGroupId: groupId,
    groups: { ...layout.groups, [groupId]: { ...group, tabs, activeId: removed.id } },
  }
  const history: HistoryRequest | null =
    layout.focusedGroupId === groupId && group.activeId === tabId ? null : historyForFocus(next)
  commit(next, history)
}

export function splitOffTab(fromGroupId: string, tabId: string, direction: SplitDirection, targetGroupId: string): void {
  const from: EditorGroup | undefined = layout.groups[fromGroupId]
  const target: EditorGroup | undefined = layout.groups[targetGroupId]
  if (from === undefined || target === undefined) {
    return
  }
  if (fromGroupId === targetGroupId && from.tabs.length < 2) {
    return
  }
  const tab: EditorTab | undefined = from.tabs.find((item: EditorTab) => item.id === tabId)
  if (tab === undefined) {
    return
  }
  const fromIndex: number = from.tabs.findIndex((item: EditorTab) => item.id === tabId)
  const fromTabs: EditorTab[] = from.tabs.filter((item: EditorTab) => item.id !== tabId)
  let fromActive: string | null = from.activeId
  if (fromActive === tabId) {
    const neighbor: EditorTab | undefined = fromTabs[fromIndex] ?? fromTabs[fromIndex - 1]
    fromActive = neighbor?.id ?? null
  }
  const newGroupId: string = newId()
  const newGroup: EditorGroup = { id: newGroupId, tabs: [{ ...tab }], activeId: tab.id }
  const groups: Record<string, EditorGroup> = {
    ...layout.groups,
    [newGroupId]: newGroup,
    [fromGroupId]: { ...from, tabs: fromTabs, activeId: fromActive },
  }
  const inserted: LayoutNode | null = insertBesideNode(layout.root, targetGroupId, direction, newGroupId)
  if (inserted === null) {
    return
  }
  let root: LayoutNode = inserted
  if (fromTabs.length === 0) {
    delete groups[fromGroupId]
    const removed: LayoutNode | null = removeGroupNode(root, fromGroupId)
    if (removed === null) {
      return
    }
    root = removed
  }
  commit(
    { root, groups, focusedGroupId: newGroupId },
    { mode: "replace", groupId: newGroupId, tabId: tab.id, path: tab.path },
  )
}

export function resizeSplit(splitId: string, boundary: number, leftFraction: number): void {
  const root: LayoutNode = mapSplit(layout.root, splitId, (node: Extract<LayoutNode, { type: "split" }>) => {
    if (boundary <= 0 || boundary >= node.children.length) {
      return node
    }
    const sizes: number[] = node.sizes.slice()
    const before: number = sizes[boundary - 1] ?? 0
    const after: number = sizes[boundary] ?? 0
    const sum: number = before + after
    const min: number = Math.min(0.12, sum / 2)
    let left: number = leftFraction
    if (left < min) {
      left = min
    }
    if (left > sum - min) {
      left = sum - min
    }
    sizes[boundary - 1] = left
    sizes[boundary] = sum - left
    return { ...node, sizes }
  })
  if (root === layout.root) {
    return
  }
  commit({ ...layout, root }, null)
}

function mapSplit(
  node: LayoutNode,
  splitId: string,
  update: (node: Extract<LayoutNode, { type: "split" }>) => Extract<LayoutNode, { type: "split" }>,
): LayoutNode {
  if (node.type === "group") {
    return node
  }
  if (node.id === splitId) {
    return update(node)
  }
  let changed: boolean = false
  const children: LayoutNode[] = node.children.map((child: LayoutNode) => {
    const next: LayoutNode = mapSplit(child, splitId, update)
    if (next !== child) {
      changed = true
    }
    return next
  })
  if (!changed) {
    return node
  }
  return { ...node, children }
}

export function setTabTitle(groupId: string, tabId: string, title: string): void {
  const group: EditorGroup | undefined = layout.groups[groupId]
  if (group === undefined) {
    return
  }
  const tab: EditorTab | undefined = group.tabs.find((item: EditorTab) => item.id === tabId)
  if (tab === undefined || tab.title === title) {
    return
  }
  const tabs: EditorTab[] = group.tabs.map((item: EditorTab) => (item.id === tabId ? { ...item, title } : item))
  commit({ ...layout, groups: { ...layout.groups, [groupId]: { ...group, tabs } } }, null)
}

export function readHistoryMarker(state: unknown): { groupId: string; tabId: string } | null {
  if (typeof state !== "object" || state === null) {
    return null
  }
  const record: Record<string, unknown> = state as Record<string, unknown>
  const nested: unknown = record.usr
  const source: Record<string, unknown> =
    typeof nested === "object" && nested !== null ? (nested as Record<string, unknown>) : record
  if (typeof source.editorGroupId !== "string" || typeof source.editorTabId !== "string") {
    return null
  }
  return { groupId: source.editorGroupId, tabId: source.editorTabId }
}

export function restoreHistory(groupId: string, tabId: string, rawPath: string): void {
  if (isAuthCallback(rawPath)) {
    return
  }
  const path: string = normalizePath(rawPath)
  const group: EditorGroup | undefined = layout.groups[groupId]
  const tab: EditorTab | undefined = group?.tabs.find((item: EditorTab) => item.id === tabId)
  if (group !== undefined && tab !== undefined) {
    const tabs: EditorTab[] = group.tabs.map((item: EditorTab) => (item.id === tabId ? { ...item, path } : item))
    navigation = "POP"
    commit(
      { ...layout, focusedGroupId: groupId, groups: { ...layout.groups, [groupId]: { ...group, tabs, activeId: tabId } } },
      null,
    )
    return
  }
  if (group !== undefined) {
    apply(openPath(layout, groupId, path, "none"))
    return
  }
  apply(openPath(layout, layout.focusedGroupId, path, "none"))
}

function readStorage(): EditorLayout | null {
  if (typeof window === "undefined") {
    return null
  }
  try {
    const raw: string | null = window.localStorage.getItem(STORAGE_KEY)
    if (raw === null) {
      return null
    }
    return parseLayout(JSON.parse(raw) as unknown)
  } catch {
    return null
  }
}

function parseLayout(value: unknown): EditorLayout | null {
  if (typeof value !== "object" || value === null) {
    return null
  }
  const record: Record<string, unknown> = value as Record<string, unknown>
  if (record.version !== 1 || typeof record.focusedGroupId !== "string") {
    return null
  }
  if (typeof record.groups !== "object" || record.groups === null) {
    return null
  }
  const groups: Record<string, EditorGroup> = {}
  for (const [id, raw] of Object.entries(record.groups as Record<string, unknown>)) {
    const group: EditorGroup | null = parseGroup(id, raw)
    if (group === null) {
      return null
    }
    groups[id] = group
  }
  const known: Set<string> = new Set(Object.keys(groups))
  const root: LayoutNode | null = parseNode(record.root, known)
  if (root === null || groups[record.focusedGroupId] === undefined) {
    return null
  }
  const seen: Set<string> = new Set()
  collectGroups(root, seen)
  if (seen.size !== known.size) {
    return null
  }
  return { root, groups, focusedGroupId: record.focusedGroupId }
}

function parseGroup(id: string, value: unknown): EditorGroup | null {
  if (typeof value !== "object" || value === null) {
    return null
  }
  const record: Record<string, unknown> = value as Record<string, unknown>
  if (!Array.isArray(record.tabs)) {
    return null
  }
  const tabs: EditorTab[] = []
  for (const raw of record.tabs) {
    if (typeof raw !== "object" || raw === null) {
      return null
    }
    const tab: Record<string, unknown> = raw as Record<string, unknown>
    if (typeof tab.id !== "string" || typeof tab.path !== "string" || typeof tab.title !== "string") {
      return null
    }
    const path: string = tab.path === "" ? "" : normalizePath(tab.path)
    tabs.push({ id: tab.id, path, title: tab.title })
  }
  const activeId: string | null = typeof record.activeId === "string" ? record.activeId : null
  if (activeId !== null && !tabs.some((item: EditorTab) => item.id === activeId)) {
    return null
  }
  if (tabs.length === 0 && activeId !== null) {
    return null
  }
  return { id, tabs, activeId }
}

function parseNode(value: unknown, groups: ReadonlySet<string>): LayoutNode | null {
  if (typeof value !== "object" || value === null) {
    return null
  }
  const record: Record<string, unknown> = value as Record<string, unknown>
  if (record.type === "group") {
    if (typeof record.id !== "string" || typeof record.groupId !== "string" || !groups.has(record.groupId)) {
      return null
    }
    return { type: "group", id: record.id, groupId: record.groupId }
  }
  if (record.type !== "split" || (record.direction !== "row" && record.direction !== "column")) {
    return null
  }
  if (typeof record.id !== "string" || !Array.isArray(record.children) || !Array.isArray(record.sizes)) {
    return null
  }
  if (record.children.length < 2 || record.sizes.length !== record.children.length) {
    return null
  }
  const children: LayoutNode[] = []
  for (const child of record.children) {
    const parsed: LayoutNode | null = parseNode(child, groups)
    if (parsed === null) {
      return null
    }
    children.push(parsed)
  }
  const sizes: number[] = []
  for (const size of record.sizes) {
    if (typeof size !== "number" || !Number.isFinite(size) || size <= 0) {
      return null
    }
    sizes.push(size)
  }
  return { type: "split", id: record.id, direction: record.direction, sizes, children }
}

function collectGroups(node: LayoutNode, seen: Set<string>): void {
  if (node.type === "group") {
    seen.add(node.groupId)
    return
  }
  for (const child of node.children) {
    collectGroups(child, seen)
  }
}
