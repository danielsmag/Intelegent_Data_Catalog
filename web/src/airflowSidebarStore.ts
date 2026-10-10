import { useSyncExternalStore } from "react"

const storageKey: string = "airflow-sidebar-open"

let open: boolean = readStoredOpen()
let revision: number = 0
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

export function getAirflowSidebarOpen(): boolean {
  return open
}

export function setAirflowSidebarOpen(next: boolean): void {
  open = next
  try {
    sessionStorage.setItem(storageKey, next ? "true" : "false")
  } catch {
    // Private mode can block storage. In-memory state still updates.
  }
  emit()
}

export function bumpAirflowRevision(): void {
  revision += 1
  emit()
}

export function getAirflowRevision(): number {
  return revision
}

export function subscribeAirflowSidebar(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useAirflowSidebarOpen(): boolean {
  return useSyncExternalStore(subscribeAirflowSidebar, getAirflowSidebarOpen, () => true)
}

export function useAirflowRevision(): number {
  return useSyncExternalStore(subscribeAirflowSidebar, getAirflowRevision, () => 0)
}
