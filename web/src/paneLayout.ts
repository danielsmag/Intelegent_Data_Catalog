import { useSyncExternalStore } from "react"

export type PaneId = "sidebar" | "rail" | "page" | "details"

type PaneWidths = Record<PaneId, number>

const PANES: readonly PaneId[] = ["sidebar", "rail", "page", "details"]

const DEFAULTS: PaneWidths = {
  sidebar: 300,
  rail: 280,
  page: 1080,
  details: 280,
}

const MIN: PaneWidths = {
  sidebar: 200,
  rail: 200,
  page: 480,
  details: 200,
}

const MAX: PaneWidths = {
  sidebar: 720,
  rail: 640,
  page: 3200,
  details: 640,
}

const VARS: Record<PaneId, string> = {
  sidebar: "--sidebar-width",
  rail: "--rail-width",
  page: "--page-width",
  details: "--details-width",
}

const STORAGE_KEY: string = "idc-pane-widths"

let widths: PaneWidths = readWidths()
const listeners: Set<() => void> = new Set()
let persistTimer: number | undefined

function clamp(pane: PaneId, value: number): number {
  const rounded: number = Math.round(value)
  return Math.min(MAX[pane], Math.max(MIN[pane], rounded))
}

function readWidths(): PaneWidths {
  const next: PaneWidths = { ...DEFAULTS }
  try {
    const raw: string | null = window.localStorage.getItem(STORAGE_KEY)
    if (raw === null) {
      return next
    }
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== "object" || parsed === null) {
      return next
    }
    const record: Record<string, unknown> = parsed as Record<string, unknown>
    for (const pane of PANES) {
      const value: unknown = record[pane]
      if (typeof value === "number" && Number.isFinite(value)) {
        next[pane] = clamp(pane, value)
      }
    }
  } catch {
    return { ...DEFAULTS }
  }
  return next
}

function applyWidths(): void {
  const root: HTMLElement = document.documentElement
  for (const pane of PANES) {
    root.style.setProperty(VARS[pane], `${widths[pane]}px`)
  }
}

function emit(): void {
  for (const listener of listeners) {
    listener()
  }
}

function schedulePersist(): void {
  window.clearTimeout(persistTimer)
  persistTimer = window.setTimeout(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(widths))
    } catch {
      // Private mode can block storage. In-memory widths still apply.
    }
  }, 120)
}

applyWidths()

export function getPaneWidth(pane: PaneId): number {
  return widths[pane]
}

export function setPaneWidth(pane: PaneId, next: number): void {
  const width: number = clamp(pane, next)
  if (width === widths[pane]) {
    return
  }
  widths = { ...widths, [pane]: width }
  document.documentElement.style.setProperty(VARS[pane], `${width}px`)
  emit()
  schedulePersist()
}

export function resetPaneWidth(pane: PaneId): void {
  setPaneWidth(pane, DEFAULTS[pane])
}

export function paneBounds(pane: PaneId): { min: number; max: number } {
  return { min: MIN[pane], max: MAX[pane] }
}

export function setPaneDragging(active: boolean): void {
  document.documentElement.classList.toggle("pane-dragging", active)
}

export function subscribePaneWidths(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function usePaneWidth(pane: PaneId): number {
  return useSyncExternalStore(subscribePaneWidths, () => getPaneWidth(pane), () => DEFAULTS[pane])
}
