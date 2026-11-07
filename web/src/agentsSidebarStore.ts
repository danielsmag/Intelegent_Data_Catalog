import { useSyncExternalStore } from "react"

const storageKey: string = "agents-sidebar-open"

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

export function getAgentsSidebarOpen(): boolean {
  return open
}

export function setAgentsSidebarOpen(next: boolean): void {
  open = next
  try {
    sessionStorage.setItem(storageKey, next ? "true" : "false")
  } catch {
    // Private mode can block storage. In-memory state still updates.
  }
  emit()
}

export function subscribeAgentsSidebar(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useAgentsSidebarOpen(): boolean {
  return useSyncExternalStore(subscribeAgentsSidebar, getAgentsSidebarOpen, () => true)
}
