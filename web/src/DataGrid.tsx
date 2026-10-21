import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactElement,
  type ReactNode,
} from "react"

export type DataGridColumn<Row> = {
  id: string
  title: string
  width: number
  render: (row: Row) => ReactNode
}

type MenuState = {
  x: number
  y: number
  rowIndex: number
}

const ROW_HEIGHT: number = 36
const OVERSCAN: number = 10

export function DataGrid<Row>(props: {
  rows: Row[]
  columns: DataGridColumn<Row>[]
  rowKey: (row: Row) => string
  selected: ReadonlySet<string>
  onSelectedChange: (next: Set<string>) => void
  onOpen: (row: Row) => void
  onRowPointerDown?: (event: ReactPointerEvent<HTMLDivElement>, row: Row, index: number) => void
  empty: ReactNode
  loading: boolean
  density?: "compact" | "comfortable"
  storageKey: string
}): ReactElement {
  const height: number = props.density === "comfortable" ? 44 : ROW_HEIGHT
  const stored: string[] = useStoredOrder(props.storageKey, props.columns.map((column) => column.id))
  const [order, setOrder] = useState<string[]>(stored)
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set())
  const [widths, setWidths] = useState<Record<string, number>>(() => readWidths(props.storageKey))
  const [scrollTop, setScrollTop] = useState<number>(0)
  const [viewport, setViewport] = useState<number>(480)
  const [cursor, setCursor] = useState<number>(0)
  const [menu, setMenu] = useState<MenuState | null>(null)
  const scroller: React.RefObject<HTMLDivElement | null> = useRef<HTMLDivElement>(null)
  const visibleColumns: DataGridColumn<Row>[] = useMemo(() => {
    const byId: Map<string, DataGridColumn<Row>> = new Map(props.columns.map((column) => [column.id, column]))
    const chosen: DataGridColumn<Row>[] = []
    for (const id of order) {
      const column: DataGridColumn<Row> | undefined = byId.get(id)
      if (column !== undefined && !hidden.has(id)) {
        chosen.push(column)
      }
    }
    return chosen
  }, [hidden, order, props.columns])
  const start: number = Math.max(0, Math.floor(scrollTop / height) - OVERSCAN)
  const count: number = Math.ceil(viewport / height) + OVERSCAN * 2
  const slice: Row[] = props.rows.slice(start, start + count)
  useEffect(() => {
    const node: HTMLDivElement | null = scroller.current
    if (node === null) {
      return
    }
    const observer: ResizeObserver = new ResizeObserver(() => {
      setViewport(node.clientHeight)
    })
    observer.observe(node)
    setViewport(node.clientHeight)
    return () => {
      observer.disconnect()
    }
  }, [])
  function toggle(id: string, extend: boolean): void {
    const next: Set<string> = extend ? new Set(props.selected) : new Set()
    if (next.has(id) && extend) {
      next.delete(id)
    } else {
      next.add(id)
    }
    if (!extend) {
      next.add(id)
    }
    props.onSelectedChange(next)
  }
  function toggleAll(): void {
    if (props.selected.size === props.rows.length) {
      props.onSelectedChange(new Set())
      return
    }
    props.onSelectedChange(new Set(props.rows.map(props.rowKey)))
  }
  return (
    <div className="data-grid">
      <div className="data-grid__chooser">
        {props.columns.map((column: DataGridColumn<Row>) => (
          <label key={column.id}>
            <input
              type="checkbox"
              checked={!hidden.has(column.id)}
              onChange={() => {
                const next: Set<string> = new Set(hidden)
                if (next.has(column.id)) {
                  next.delete(column.id)
                } else {
                  next.add(column.id)
                }
                setHidden(next)
              }}
            />
            {column.title}
          </label>
        ))}
      </div>
      <div
        className="data-grid__scroller"
        ref={scroller}
        tabIndex={0}
        onScroll={(event) => {
          setScrollTop(event.currentTarget.scrollTop)
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault()
            setCursor((value: number) => Math.min(props.rows.length - 1, value + 1))
          }
          if (event.key === "ArrowUp") {
            event.preventDefault()
            setCursor((value: number) => Math.max(0, value - 1))
          }
          if (event.key === "Enter") {
            const row: Row | undefined = props.rows[cursor]
            if (row !== undefined) {
              props.onOpen(row)
            }
          }
          if (event.key === " ") {
            event.preventDefault()
            const row: Row | undefined = props.rows[cursor]
            if (row !== undefined) {
              toggle(props.rowKey(row), event.shiftKey)
            }
          }
        }}
      >
        <div className="data-grid__header" role="row">
          <label className="data-grid__check">
            <input
              type="checkbox"
              checked={props.rows.length > 0 && props.selected.size === props.rows.length}
              onChange={toggleAll}
            />
          </label>
          {visibleColumns.map((column: DataGridColumn<Row>, index: number) => (
            <div
              key={column.id}
              className="data-grid__cell data-grid__head"
              style={{ width: widths[column.id] ?? column.width }}
              draggable
              onDragStart={(event) => {
                event.dataTransfer.setData("text/column", column.id)
              }}
              onDragOver={(event) => {
                event.preventDefault()
              }}
              onDrop={(event) => {
                const from: string = event.dataTransfer.getData("text/column")
                if (from === "" || from === column.id) {
                  return
                }
                const next: string[] = order.filter((id: string) => id !== from)
                const at: number = next.indexOf(column.id)
                next.splice(at < 0 ? index : at, 0, from)
                setOrder(next)
                window.localStorage.setItem(`${props.storageKey}:order`, JSON.stringify(next))
              }}
            >
              {column.title}
              <span
                className="data-grid__resize"
                onPointerDown={(event) => {
                  event.preventDefault()
                  event.stopPropagation()
                  const startX: number = event.clientX
                  const startWidth: number = widths[column.id] ?? column.width
                  function move(ev: PointerEvent): void {
                    const width: number = Math.max(80, startWidth + ev.clientX - startX)
                    setWidths((current: Record<string, number>) => {
                      const next: Record<string, number> = { ...current, [column.id]: width }
                      window.localStorage.setItem(`${props.storageKey}:widths`, JSON.stringify(next))
                      return next
                    })
                  }
                  function up(): void {
                    window.removeEventListener("pointermove", move)
                    window.removeEventListener("pointerup", up)
                  }
                  window.addEventListener("pointermove", move)
                  window.addEventListener("pointerup", up)
                }}
              />
            </div>
          ))}
        </div>
        {props.loading ? <div className="data-grid__skeleton" /> : null}
        {!props.loading && props.rows.length === 0 ? <div className="data-grid__empty">{props.empty}</div> : null}
        <div style={{ height: props.rows.length * height, position: "relative" }}>
          {slice.map((row: Row, offset: number) => {
            const index: number = start + offset
            const id: string = props.rowKey(row)
            return (
              <div
                key={id}
                className="data-grid__row"
                role="row"
                aria-selected={props.selected.has(id)}
                style={{ top: index * height, height }}
                onPointerDown={(event) => {
                  setCursor(index)
                  props.onRowPointerDown?.(event, row, index)
                }}
                onDoubleClick={() => {
                  props.onOpen(row)
                }}
                onContextMenu={(event) => {
                  event.preventDefault()
                  setMenu({ x: event.clientX, y: event.clientY, rowIndex: index })
                }}
                onClick={(event) => {
                  if (event.metaKey || event.ctrlKey || event.shiftKey) {
                    toggle(id, true)
                  }
                }}
              >
                <label className="data-grid__check">
                  <input
                    type="checkbox"
                    checked={props.selected.has(id)}
                    onChange={() => {
                      toggle(id, true)
                    }}
                  />
                </label>
                {visibleColumns.map((column: DataGridColumn<Row>) => (
                  <div key={column.id} className="data-grid__cell" style={{ width: widths[column.id] ?? column.width }}>
                    {column.render(row)}
                  </div>
                ))}
              </div>
            )
          })}
        </div>
      </div>
      {menu !== null ? (
        <div className="data-grid__menu" style={{ left: menu.x, top: menu.y }} onMouseLeave={() => { setMenu(null) }}>
          <button
            type="button"
            onClick={() => {
              const row: Row | undefined = props.rows[menu.rowIndex]
              if (row !== undefined) {
                props.onOpen(row)
              }
              setMenu(null)
            }}
          >
            Open
          </button>
        </div>
      ) : null}
    </div>
  )
}

function useStoredOrder(storageKey: string, fallback: string[]): string[] {
  try {
    const raw: string | null = window.localStorage.getItem(`${storageKey}:order`)
    if (raw === null) {
      return fallback
    }
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) {
      return fallback
    }
    const ids: string[] = parsed.filter((item: unknown): item is string => typeof item === "string")
    for (const id of fallback) {
      if (!ids.includes(id)) {
        ids.push(id)
      }
    }
    return ids
  } catch {
    return fallback
  }
}

function readWidths(storageKey: string): Record<string, number> {
  try {
    const raw: string | null = window.localStorage.getItem(`${storageKey}:widths`)
    if (raw === null) {
      return {}
    }
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== "object" || parsed === null) {
      return {}
    }
    const widths: Record<string, number> = {}
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === "number") {
        widths[key] = value
      }
    }
    return widths
  } catch {
    return {}
  }
}
