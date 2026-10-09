import { useEffect, useRef, useState, type FocusEvent, type ReactElement, type RefObject } from "react"
import { errorMessage, isAbortError } from "./api"
import { MoreIcon } from "./icons"
import {
  followContextGeneration,
  onContextGenerationFinished,
  requestContextGeneration,
} from "./knowledgeBuild"
import {
  fetchLatestContextGeneration,
  updateEntityContext,
  type ContextEntityKind,
  type ContextGenerationJob,
} from "./registryApi"

type MenuBox = {
  top: number
  left: number
}

const MENU_WIDTH: number = 160
const MENU_HEIGHT: number = 76

function ContextMenu(props: { label: string; children: ReactElement }): ReactElement {
  const triggerRef: RefObject<HTMLButtonElement | null> = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState<boolean>(false)
  const [box, setBox] = useState<MenuBox | null>(null)
  function place(): void {
    const trigger: HTMLButtonElement | null = triggerRef.current
    if (trigger === null) {
      return
    }
    const rect: DOMRect = trigger.getBoundingClientRect()
    const left: number = Math.max(8, Math.min(rect.right - MENU_WIDTH, window.innerWidth - MENU_WIDTH - 8))
    const below: number = rect.bottom - 1
    const top: number = below + MENU_HEIGHT > window.innerHeight ? Math.max(8, rect.top - MENU_HEIGHT) : below
    setBox({ top, left })
  }
  function show(): void {
    place()
    setOpen(true)
  }
  function hide(): void {
    setOpen(false)
  }
  function onBlur(event: FocusEvent<HTMLDivElement>): void {
    const next: EventTarget | null = event.relatedTarget
    if (next instanceof Node && event.currentTarget.contains(next)) {
      return
    }
    hide()
  }
  useEffect(() => {
    if (!open) {
      return
    }
    function close(): void {
      setOpen(false)
    }
    window.addEventListener("scroll", close, true)
    window.addEventListener("resize", close)
    return () => {
      window.removeEventListener("scroll", close, true)
      window.removeEventListener("resize", close)
    }
  }, [open])
  return (
    <div className={open ? "context-menu is-open" : "context-menu"} onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={onBlur}>
      <button
        ref={triggerRef}
        className="context-menu-trigger"
        type="button"
        aria-label={props.label}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <MoreIcon />
      </button>
      {open && box !== null ? (
        <div className="context-menu-panel" role="menu" aria-label={props.label} style={{ position: "fixed", top: box.top, left: box.left }}>
          {props.children}
        </div>
      ) : null}
    </div>
  )
}

type ContextEditorProps = {
  kind: ContextEntityKind
  id: string
  name: string
  context: string | null
  origin: "USER" | "LLM" | null
  onChanged: () => void
  compact?: boolean
}

export function ContextEditor(props: ContextEditorProps): ReactElement {
  const [running, setRunning] = useState<boolean>(false)
  const [editing, setEditing] = useState<boolean>(false)
  const [draft, setDraft] = useState<string>("")
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState<boolean>(false)
  useEffect(() => {
    const controller: AbortController = new AbortController()
    setRunning(false)
    setEditing(false)
    void fetchLatestContextGeneration(props.kind, props.id, controller.signal)
      .then((job: ContextGenerationJob | null) => {
        if (controller.signal.aborted || job === null || job.status !== "running") {
          return
        }
        setRunning(true)
        followContextGeneration(job)
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) {
          return
        }
        setError(errorMessage(err))
      })
    return () => {
      controller.abort()
    }
  }, [props.kind, props.id])
  useEffect(() => {
    return onContextGenerationFinished((job: ContextGenerationJob) => {
      if (job.subject_kind !== props.kind || job.subject_id !== props.id) {
        return
      }
      setRunning(false)
      if (job.status === "succeeded") {
        props.onChanged()
      }
    })
  }, [props.kind, props.id, props.onChanged])
  function generate(): void {
    setRunning(true)
    setError(null)
    void requestContextGeneration(props.kind, props.id)
      .then((job: ContextGenerationJob) => {
        setRunning(job.status === "running")
        if (job.status === "succeeded") {
          props.onChanged()
        }
      })
      .catch((err: unknown) => {
        setRunning(false)
        setError(errorMessage(err))
      })
  }
  function save(): void {
    const text: string = draft.trim()
    setSaving(true)
    setError(null)
    void updateEntityContext(props.kind, props.id, text === "" ? null : text)
      .then(() => {
        setEditing(false)
        setSaving(false)
        props.onChanged()
      })
      .catch((err: unknown) => {
        setSaving(false)
        setError(errorMessage(err))
      })
  }
  const shown: string = props.context ?? "No context yet."
  const rootClass: string = [
    "context-editor",
    props.compact === true ? "context-editor-compact" : "",
    running ? "is-running" : "",
  ]
    .filter((part: string) => part !== "")
    .join(" ")
  const originBadge: ReactElement | null =
    props.origin !== null ? <span className="badge context-origin">{props.origin}</span> : null
  const generateLabel: string = running
    ? "Generating…"
    : props.context === null
      ? "Generate"
      : "Regenerate"
  const actionButtons: ReactElement = (
    <>
      <button className="button secondary" type="button" disabled={running || saving} onClick={generate}>
        {generateLabel}
      </button>
      <button
        className="button secondary"
        type="button"
        disabled={running || saving}
        onClick={() => {
          setDraft(props.context ?? "")
          setEditing(true)
        }}
      >
        Edit
      </button>
    </>
  )
  return (
    <div className={rootClass}>
      {editing ? (
        <>
          <textarea
            value={draft}
            aria-label={`Context for ${props.name}`}
            onChange={(event) => {
              setDraft(event.target.value)
            }}
          />
          <div className="context-editor-bar">
            <button className="button" type="button" disabled={saving} onClick={save}>
              {saving ? "Saving…" : "Save"}
            </button>
            <button
              className="button secondary"
              type="button"
              disabled={saving}
              onClick={() => {
                setEditing(false)
              }}
            >
              Cancel
            </button>
          </div>
        </>
      ) : props.compact === true ? (
        <div className="context-view">
          <div className="context-copy">
            {originBadge}
            <p className={props.context === null ? "comment" : undefined}>{shown}</p>
          </div>
          <ContextMenu label={`Context actions for ${props.name}`}>{actionButtons}</ContextMenu>
        </div>
      ) : (
        <>
          <div className="context-editor-bar">
            <h2>Context</h2>
            {originBadge}
            {actionButtons}
          </div>
          <p className={props.context === null ? "comment" : undefined}>{shown}</p>
        </>
      )}
      {error !== null ? <p className="error-text">{error}</p> : null}
    </div>
  )
}
