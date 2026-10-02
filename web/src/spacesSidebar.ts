import { useSyncExternalStore } from "react"

const storageKey: string = "spaces-sidebar-open"

let open: boolean = readStoredOpen()
const listeners: Set<() => void> = new Set()

function readStoredOpen(): boolean {
  try {
    return sessionStorage.getItem(storageKey) !== "false"
  } catch {
    return true
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
    sessionStorage.setItem(storageKey, next ? "true" : "false")
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
  return useSyncExternalStore(subscribeSpacesSidebar, getSpacesSidebarOpen, () => true)
}
