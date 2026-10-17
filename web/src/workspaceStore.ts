import { useSyncExternalStore } from "react"
import { bumpCatalogRevision } from "./catalogRevision"

const storageKey: string = "active-workspace-id"
const listeners: Set<() => void> = new Set()

let workspaceId: string | null = readStored()

function readStored(): string | null {
  try {
    return window.localStorage.getItem(storageKey)
  } catch {
    return null
  }
}

function emit(): void {
  for (const listener of listeners) {
    listener()
  }
}

export function getActiveWorkspaceId(): string | null {
  return workspaceId
}

export function setActiveWorkspaceId(next: string | null): void {
  workspaceId = next
  try {
    if (next === null) {
      window.localStorage.removeItem(storageKey)
    } else {
      window.localStorage.setItem(storageKey, next)
    }
  } catch {
    // Private mode can block storage. In-memory state still updates.
  }
  emit()
  bumpCatalogRevision()
}

export function subscribeActiveWorkspace(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useActiveWorkspaceId(): string | null {
  return useSyncExternalStore(subscribeActiveWorkspace, getActiveWorkspaceId, () => null)
}
