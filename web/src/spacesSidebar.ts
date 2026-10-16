import { useSyncExternalStore } from "react"

const storageKey: string = "spaces-sidebar-open"

let open: boolean = readStoredOpen()
const listeners: Set<() => void> = new Set()

function readStoredOpen(): boolean {
  try {
    const stored: string | null = window.localStorage.getItem(storageKey)
    if (stored === "true" || stored === "false") {
      return stored === "true"
    }
    const legacy: string | null = window.sessionStorage.getItem(storageKey)
    if (legacy === "true" || legacy === "false") {
      window.localStorage.setItem(storageKey, legacy)
      return legacy === "true"
    }
    return false
  } catch {
    return false
  }
}

function emit(): void {
  for (const listener of listeners) {
    listener()
  }
}

export function getSpacesSidebarOpen(): boolean {
  return open
}

export function setSpacesSidebarOpen(next: boolean): void {
  open = next
  try {
    window.localStorage.setItem(storageKey, next ? "true" : "false")
  } catch {
    // Private mode can block storage. In-memory state still updates.
  }
  emit()
}

export function subscribeSpacesSidebar(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useSpacesSidebarOpen(): boolean {
  return useSyncExternalStore(subscribeSpacesSidebar, getSpacesSidebarOpen, () => false)
}
