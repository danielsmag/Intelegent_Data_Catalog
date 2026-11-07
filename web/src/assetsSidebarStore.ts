import { useSyncExternalStore } from "react"

const storageKey: string = "assets-sidebar-open"

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

export function getAssetsSidebarOpen(): boolean {
  return open
}

export function setAssetsSidebarOpen(next: boolean): void {
  open = next
  try {
    sessionStorage.setItem(storageKey, next ? "true" : "false")
  } catch {
    // Private mode can block storage. In-memory state still updates.
  }
  emit()
}

export function subscribeAssetsSidebar(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useAssetsSidebarOpen(): boolean {
  return useSyncExternalStore(subscribeAssetsSidebar, getAssetsSidebarOpen, () => true)
}
