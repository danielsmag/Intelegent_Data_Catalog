import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactElement, type RefObject } from "react"
import {
  paneBounds,
  resetPaneWidth,
  setPaneDragging,
  setPaneWidth,
  usePaneWidth,
  type PaneId,
} from "./paneLayout"

type ResizeEdge = "start" | "end"

type DragSession = {
  startX: number
  startWidth: number
}

const NUDGE_PX: number = 16

export function ResizeHandle(props: { pane: PaneId; edge: ResizeEdge; label: string }): ReactElement {
  const width: number = usePaneWidth(props.pane)
  const bounds: { min: number; max: number } = paneBounds(props.pane)
  const drag: RefObject<DragSession | null> = useRef<DragSession | null>(null)
  const [dragging, setDragging] = useState<boolean>(false)

  useEffect(() => {
    return () => {
      if (drag.current !== null) {
        drag.current = null
        setPaneDragging(false)
      }
    }
  }, [])

  function signedDelta(clientX: number, session: DragSession): number {
    const delta: number = clientX - session.startX
    return props.edge === "end" ? delta : -delta
  }

  function onPointerDown(event: PointerEvent<HTMLDivElement>): void {
    if (event.button !== 0) {
      return
    }
    const handle: HTMLDivElement = event.currentTarget
    try {
      handle.setPointerCapture(event.pointerId)
    } catch {
      // Capture needs an active pointer. Keyboard and tests still resize.
    }
    const parentWidth: number = handle.parentElement?.clientWidth ?? width
    const startWidth: number = props.pane === "page" && width > parentWidth + 1 ? parentWidth : width
    if (startWidth !== width) {
      setPaneWidth(props.pane, startWidth)
    }
    drag.current = { startX: event.clientX, startWidth }
    setDragging(true)
    setPaneDragging(true)
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>): void {
    const session: DragSession | null = drag.current
    if (session === null) {
      return
    }
    setPaneWidth(props.pane, session.startWidth + signedDelta(event.clientX, session))
  }

  function endDrag(): void {
    if (drag.current === null) {
      return
    }
    drag.current = null
    setDragging(false)
    setPaneDragging(false)
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    const step: number = event.shiftKey ? NUDGE_PX * 2 : NUDGE_PX
    const growKey: string = props.edge === "end" ? "ArrowRight" : "ArrowLeft"
    const shrinkKey: string = props.edge === "end" ? "ArrowLeft" : "ArrowRight"
    if (event.key === growKey) {
      event.preventDefault()
      setPaneWidth(props.pane, width + step)
      return
    }
    if (event.key === shrinkKey) {
      event.preventDefault()
      setPaneWidth(props.pane, width - step)
      return
    }
    if (event.key === "Home") {
      event.preventDefault()
      resetPaneWidth(props.pane)
    }
  }

  return (
    <div
      className="pane-resize"
      data-edge={props.edge}
      data-dragging={dragging ? "true" : "false"}
      role="slider"
      tabIndex={0}
      aria-orientation="vertical"
      aria-label={props.label}
      aria-valuemin={bounds.min}
      aria-valuemax={bounds.max}
      aria-valuenow={width}
      title="Drag to resize. Double-click to reset."
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onDoubleClick={() => {
        resetPaneWidth(props.pane)
      }}
      onKeyDown={onKeyDown}
    />
  )
}
