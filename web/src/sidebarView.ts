import { useSyncExternalStore } from "react"

export type SidebarView =
  | "spaces"
  | "catalog"
  | "search"
  | "query"
  | "workspace"
  | "assets"

type PendingSql = {
  sql: string
  token: number
}

const STORAGE_KEY: string = "idc-sidebar-view"
const listeners: Set<() => void> = new Set()

let view: SidebarView = readInitialView()
let pendingSql: PendingSql | null = readInitialSql()
let appliedSqlToken: number = 0
let queryDraft: string | null = null

function isSidebarView(value: string | null): value is SidebarView {
  return (
    value === "spaces" ||
    value === "catalog" ||
    value === "search" ||
    value === "query" ||
    value === "workspace" ||
    value === "assets"
  )
}

export function sidebarViewForPath(path: string): SidebarView | null {
  const hash: number = path.indexOf("#")
  const noHash: string = hash >= 0 ? path.slice(0, hash) : path
  const query: number = noHash.indexOf("?")
  const pathname: string = query >= 0 ? noHash.slice(0, query) : noHash
  if (pathname === "/query") {
    return "query"
  }
  if (pathname === "/search" || pathname.startsWith("/search/")) {
    return "search"
  }
  if (pathname === "/sources" || pathname.startsWith("/sources/")) {
    return "catalog"
  }
  if (pathname === "/workspace") {
    return "workspace"
  }
  return null
}

function readStored(): SidebarView {
  if (typeof window === "undefined") {
    return "spaces"
  }
  try {
    const stored: string | null = window.sessionStorage.getItem(STORAGE_KEY)
    if (stored === "airflow") {
      return "assets"
    }
    if (stored === "sources") {
      return "catalog"
    }
    if (isSidebarView(stored)) {
      return stored
    }
  } catch {
    return "spaces"
  }
  return "spaces"
}

function readInitialView(): SidebarView {
  if (typeof window !== "undefined") {
    const fromUrl: SidebarView | null = sidebarViewForPath(window.location.pathname)
    if (fromUrl !== null) {
      persist(fromUrl)
      return fromUrl
    }
  }
  return readStored()
}

function readInitialSql(): PendingSql | null {
  if (typeof window === "undefined") {
    return null
  }
  if (sidebarViewForPath(window.location.pathname) !== "query") {
    return null
  }
  const sql: string | null = new URLSearchParams(window.location.search).get("sql")
  if (sql === null || sql === "") {
    return null
  }
  return { sql, token: 1 }
}

function persist(next: SidebarView): void {
  if (typeof window === "undefined") {
    return
  }
  try {
    window.sessionStorage.setItem(STORAGE_KEY, next)
  } catch {
    // Private mode can block storage. In-memory view still applies.
  }
}

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

export function getSidebarView(): SidebarView {
  return view
}

export function setSidebarView(next: SidebarView): void {
  if (view === next) {
    return
  }
  view = next
  persist(next)
  emit()
}

export function useSidebarView(): SidebarView {
  return useSyncExternalStore(subscribe, getSidebarView, () => "spaces")
}

export function getPendingQuerySql(): PendingSql | null {
  return pendingSql
}

export function setPendingQuerySql(sql: string): void {
  const token: number = (pendingSql?.token ?? 0) + 1
  pendingSql = { sql, token }
  emit()
}

export function usePendingQuerySql(): PendingSql | null {
  return useSyncExternalStore(subscribe, getPendingQuerySql, () => null)
}

export function takePendingQuerySql(): PendingSql | null {
  if (pendingSql === null || pendingSql.token === appliedSqlToken) {
    return null
  }
  appliedSqlToken = pendingSql.token
  return pendingSql
}

export function peekQueryDraft(): string | null {
  return queryDraft
}

export function rememberQueryDraft(sql: string): void {
  queryDraft = sql
}

export function redirectSidebarPath(path: string): boolean {
  const viewForPath: SidebarView | null = sidebarViewForPath(path)
  if (viewForPath === null) {
    return false
  }
  setSidebarView(viewForPath)
  if (viewForPath === "query") {
    const query: number = path.indexOf("?")
    const search: string = query >= 0 ? path.slice(query) : ""
    const sql: string | null = new URLSearchParams(search).get("sql")
    if (sql !== null && sql !== "") {
      setPendingQuerySql(sql)
    }
  }
  return true
}
