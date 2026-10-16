import { useEffect, useState, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent, type ReactElement } from "react"
import { useEditorDrag, watchTabDrag, type DropTarget } from "./editorDrag"
import {
  closeAllTabs,
  closeOtherTabs,
  closeTab,
  focusTab,
  openBlankTab,
  splitGroup,
  useEditorLayout,
  type EditorGroup,
  type EditorTab,
} from "./editorLayout"
import { PlusIcon } from "./icons"

type MenuState = {
  x: number
  y: number
  tabId: string
}

type Point = {
  x: number
  y: number
}

export function EditorTabBar(props: { groupId: string }): ReactElement | null {
  const layout = useEditorLayout()
  const drag = useEditorDrag()
  const group: EditorGroup | undefined = layout.groups[props.groupId]
  const [menu, setMenu] = useState<MenuState | null>(null)
  const [splitMenu, setSplitMenu] = useState<Point | null>(null)
  if (group === undefined) {
    return null
  }
  const fillingBlank: boolean =
    drag?.source.kind === "path" && drag.target?.kind === "insert" && drag.target.blankTabId !== null
  const insertIndex: number | null =
    drag?.target?.kind === "insert" && drag.target.groupId === props.groupId && !fillingBlank ? drag.target.index : null
  const fillTabId: string | null =
    drag?.source.kind === "path" && drag.target?.kind === "insert" && drag.target.groupId === props.groupId
      ? drag.target.blankTabId
      : null
  return (
    <div className="editor-tabbar" role="tablist" aria-label="Open pages">
      <div className="editor-tabs" data-editor-tabs="" data-group-id={props.groupId} data-tab-count={group.tabs.length}>
        {group.tabs.map((tab: EditorTab, index: number) => (
          <EditorTabButton
            key={tab.id}
            groupId={props.groupId}
            tab={tab}
            index={index}
            active={tab.id === group.activeId}
            showInsert={insertIndex === index}
            filling={fillTabId === tab.id}
            onMenu={(next: MenuState) => {
              setSplitMenu(null)
              setMenu(next)
            }}
          />
        ))}
        {insertIndex === group.tabs.length ? <i className="editor-insert editor-insert-end" /> : null}
      </div>
      <div className="editor-tab-actions">
        <button
          type="button"
          className="editor-action"
          title="New tab"
          aria-label="New tab"
          onClick={() => {
            openBlankTab(props.groupId)
          }}
        >
          <PlusIcon />
        </button>
        <button
          type="button"
          className="editor-action"
          title="Split right"
          aria-label="Split right"
          onClick={() => {
            splitGroup(props.groupId, "row")
          }}
        >
          <SplitIcon />
        </button>
        <button
          type="button"
          className="editor-action"
          title="Split options"
          aria-label="Split options"
          aria-expanded={splitMenu !== null}
          onClick={(event: ReactMouseEvent<HTMLButtonElement>) => {
            const rect: DOMRect = event.currentTarget.getBoundingClientRect()
            setMenu(null)
            setSplitMenu({ x: rect.right - 168, y: rect.bottom + 4 })
          }}
        >
          <CaretIcon />
        </button>
      </div>
      {menu === null ? null : (
        <TabMenu
          groupId={props.groupId}
          menu={menu}
          onClose={() => {
            setMenu(null)
          }}
        />
      )}
      {splitMenu === null ? null : (
        <ActionMenu
          point={splitMenu}
          onClose={() => {
            setSplitMenu(null)
          }}
          items={[
            {
              label: "Split down",
              onSelect: () => {
                splitGroup(props.groupId, "column")
              },
            },
          ]}
        />
      )}
    </div>
  )
}

function EditorTabButton(props: {
  groupId: string
  tab: EditorTab
  index: number
  active: boolean
  showInsert: boolean
  filling: boolean
  onMenu: (menu: MenuState) => void
}): ReactElement {
  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>): void {
    if (event.button !== 0) {
      return
    }
    const target: EventTarget | null = event.target
    if (target instanceof Element && target.closest(".editor-tab-close") !== null) {
      return
    }
    watchTabDrag(event, { groupId: props.groupId, tabId: props.tab.id, title: props.tab.title })
  }
  return (
    <div className="editor-tab-slot">
      {props.showInsert ? <i className="editor-insert" /> : null}
      <div
        role="tab"
        aria-selected={props.active}
        title={props.tab.title}
        className={["editor-tab", props.active ? "active" : "", props.filling ? "editor-tab-fill" : ""].filter((name: string) => name !== "").join(" ")}
        data-editor-tab=""
        data-group-id={props.groupId}
        data-tab-id={props.tab.id}
        data-tab-index={props.index}
        data-tab-blank={props.tab.path === "" ? "true" : undefined}
        onClick={() => {
          focusTab(props.groupId, props.tab.id)
        }}
        onPointerDown={onPointerDown}
        onMouseDown={(event: ReactMouseEvent<HTMLDivElement>) => {
          if (event.button === 1) {
            event.preventDefault()
          }
        }}
        onAuxClick={(event: ReactMouseEvent<HTMLDivElement>) => {
          if (event.button !== 1) {
            return
          }
          event.preventDefault()
          closeTab(props.groupId, props.tab.id)
        }}
        onContextMenu={(event: ReactMouseEvent<HTMLDivElement>) => {
          event.preventDefault()
          props.onMenu({ x: event.clientX, y: event.clientY, tabId: props.tab.id })
        }}
      >
        <span className="editor-tab-label">{props.tab.title}</span>
        <button
          type="button"
          className="editor-tab-close"
          aria-label={`Close ${props.tab.title}`}
          onClick={(event: ReactMouseEvent<HTMLButtonElement>) => {
            event.stopPropagation()
            closeTab(props.groupId, props.tab.id)
          }}
        >
          ×
        </button>
      </div>
    </div>
  )
}

function TabMenu(props: { groupId: string; menu: MenuState; onClose: () => void }): ReactElement {
  const tabId: string = props.menu.tabId
  const groupId: string = props.groupId
  return (
    <ActionMenu
      point={{ x: props.menu.x, y: props.menu.y }}
      onClose={props.onClose}
      items={[
        { label: "Close", onSelect: () => closeTab(groupId, tabId) },
        { label: "Close others", onSelect: () => closeOtherTabs(groupId, tabId) },
        { label: "Close all", onSelect: () => closeAllTabs(groupId) },
        {
          label: "Split right",
          onSelect: () => {
            focusTab(groupId, tabId)
            splitGroup(groupId, "row")
          },
        },
        {
          label: "Split down",
          onSelect: () => {
            focusTab(groupId, tabId)
            splitGroup(groupId, "column")
          },
        },
      ]}
    />
  )
}

function ActionMenu(props: {
  point: Point
  items: readonly { label: string; onSelect: () => void }[]
  onClose: () => void
}): ReactElement {
  useEffect(() => {
    function onKey(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        props.onClose()
      }
    }
    function onPointer(event: PointerEvent): void {
      const target: EventTarget | null = event.target
      if (target instanceof Element && target.closest(".editor-menu") !== null) {
        return
      }
      props.onClose()
    }
    window.addEventListener("keydown", onKey)
    window.addEventListener("pointerdown", onPointer)
    return () => {
      window.removeEventListener("keydown", onKey)
      window.removeEventListener("pointerdown", onPointer)
    }
  }, [props])
  const x: number = Math.max(8, Math.min(props.point.x, window.innerWidth - 188))
  const y: number = Math.max(8, Math.min(props.point.y, window.innerHeight - props.items.length * 32 - 16))
  return (
    <div className="editor-menu" role="menu" style={{ left: x, top: y }}>
      {props.items.map((item) => (
        <button
          key={item.label}
          type="button"
          role="menuitem"
          className="editor-menu-item"
          onClick={() => {
            item.onSelect()
            props.onClose()
          }}
        >
          {item.label}
        </button>
      ))}
    </div>
  )
}

function SplitIcon(): ReactElement {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
      <rect x="1.5" y="2.5" width="5.5" height="11" rx="1" fill="none" stroke="currentColor" strokeWidth="1.2" />
      <rect x="9" y="2.5" width="5.5" height="11" rx="1" fill="none" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  )
}

function CaretIcon(): ReactElement {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <path d="M4 6.2 8 10.2 12 6.2" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  )
}

export function EditorDropEdges(props: { groupId: string }): ReactElement | null {
  const drag = useEditorDrag()
  if (drag === null) {
    return null
  }
  const target: DropTarget | null = drag.target
  const rightHot: boolean = target?.kind === "edge" && target.groupId === props.groupId && target.direction === "row"
  const downHot: boolean = target?.kind === "edge" && target.groupId === props.groupId && target.direction === "column"
  return (
    <>
      <div
        className={rightHot ? "editor-drop-edge right hot" : "editor-drop-edge right"}
        data-drop-edge="row"
        data-group-id={props.groupId}
      />
      <div
        className={downHot ? "editor-drop-edge bottom hot" : "editor-drop-edge bottom"}
        data-drop-edge="column"
        data-group-id={props.groupId}
      />
    </>
  )
}

export function EditorDragGhost(): ReactElement | null {
  const drag = useEditorDrag()
  if (drag === null) {
    return null
  }
  return (
    <div className="editor-drag-ghost" style={{ left: drag.x + 12, top: drag.y + 12 }}>
      {drag.title}
    </div>
  )
}
