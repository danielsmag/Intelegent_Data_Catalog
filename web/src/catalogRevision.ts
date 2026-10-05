import { useSyncExternalStore } from "react"

let revision: number = 0
const listeners: Set<() => void> = new Set()

function emit(): void {
  for (const listener of listeners) {
    listener()
  }
}

export function getCatalogRevision(): number {
  return revision
}

export function bumpCatalogRevision(): void {
  revision += 1
  emit()
}

export function subscribeCatalogRevision(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useCatalogRevision(): number {
  return useSyncExternalStore(subscribeCatalogRevision, getCatalogRevision, () => 0)
}
