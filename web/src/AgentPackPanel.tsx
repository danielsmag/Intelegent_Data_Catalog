import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent, type ReactElement, type RefObject } from "react"
import ReactMarkdown from "react-markdown"
import {
  fetchAgentCard,
  fetchAgentPack,
  fetchMemory,
  fetchMemoryFile,
  fetchPrompt,
  fetchRule,
  fetchSkill,
  saveRule,
  saveSkill,
  type AgentPack,
  type MemoryFile,
  type PromptFile,
  type RuleFile,
  type SkillFile,
  type SkillReference,
} from "./agentPackApi"

export function AgentPackPanel(props: { assetId: string; assetKind: string }): ReactElement {
  const [pack, setPack] = useState<AgentPack | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [memoryFiles, setMemoryFiles] = useState<MemoryFile[] | null>(null)
  const [memoryError, setMemoryError] = useState<string | null>(null)
  const [section, setSection] = useState<Tone>("agent")
  const [fileKey, setFileKey] = useState<string | null>(null)
  const [menuOpen, setMenuOpen] = useState<boolean>(false)
  const [flagError, setFlagError] = useState<string | null>(null)
  const [savingFlag, setSavingFlag] = useState<string | null>(null)
  const [treeWidth, setTreeWidth] = useState<number>(readTreeWidth)

  useEffect(() => {
    const controller: AbortController = new AbortController()
    setPack(null)
    setError(null)
    setSection("agent")
    setFileKey(null)
    setMenuOpen(false)
    setPreview(null)
    setPreviewError(null)
    setFlagError(null)
    void fetchAgentPack(props.assetId, controller.signal)
      .then((loaded: AgentPack) => {
        if (controller.signal.aborted) {
          return
        }
        setPack(loaded)
        if (loaded.writable && loaded.agent.present) {
          setFileKey("agent")
        }
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "Could not load agent files")
        }
      })
    return () => {
      controller.abort()
    }
  }, [props.assetId])

  useEffect(() => {
    const controller: AbortController = new AbortController()
    setMemoryFiles(null)
    setMemoryError(null)
    void fetchMemory(props.assetId, controller.signal)
      .then((loaded: MemoryFile[]) => {
        if (!controller.signal.aborted) {
          setMemoryFiles(loaded)
        }
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setMemoryError(err instanceof Error ? err.message : "Could not load memory")
        }
      })
    return () => {
      controller.abort()
    }
  }, [props.assetId])

  useEffect(() => {
    if (section !== "memory" || memoryFiles === null || pack === null) {
      return
    }
    const items: readonly Entry[] = sectionEntries(section, pack, memoryFiles)
    if (items.length === 0) {
      setFileKey(null)
      setMenuOpen(false)
      return
    }
    const stillThere: boolean = fileKey !== null && items.some((item: Entry) => item.key === fileKey)
    if (stillThere) {
      return
    }
    const first: Entry | undefined = items[0]
    if (first === undefined) {
      return
    }
    setFileKey(first.key)
    setMenuOpen(items.length > 1)
  }, [section, memoryFiles, pack, fileKey])

  useEffect(() => {
    if (fileKey === null) {
      setPreview(null)
      setPreviewError(null)
      return
    }
    let cancelled: boolean = false
    setPreview(null)
    setPreviewError(null)
    void loadPreview(props.assetId, section, fileKey)
      .then((loaded: Preview) => {
        if (!cancelled) {
          setPreview(loaded)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setPreviewError(err instanceof Error ? err.message : "Could not load file")
        }
      })
    return () => {
      cancelled = true
    }
  }, [props.assetId, section, fileKey])

  const sections: readonly Tone[] = pack !== null && pack.writable ? SECTIONS : READONLY_SECTIONS
  const entries: readonly Entry[] = pack === null ? [] : sectionEntries(section, pack, memoryFiles)
  const selected: Entry | undefined = entries.find((entry: Entry) => entry.key === fileKey)
  const lead: string = sectionLead(section, pack)
  const blank: string | null = pack === null ? null : emptyCopy(section, pack)
  const agentEntries: readonly Entry[] = pack === null ? [] : sectionEntries("agent", pack, memoryFiles)

  function chooseSection(next: Tone): void {
    if (pack === null) {
      return
    }
    const items: readonly Entry[] = sectionEntries(next, pack, memoryFiles)
    if (next === section && items.length > 1) {
      setMenuOpen((open: boolean) => !open)
      return
    }
    setSection(next)
    if (items.length <= 1) {
      const only: Entry | undefined = items[0]
      setFileKey(only === undefined ? null : only.key)
      setMenuOpen(false)
      return
    }
    const first: Entry | undefined = items[0]
    setFileKey(first === undefined ? null : first.key)
    setMenuOpen(true)
  }

  function toggleFile(tone: "rules" | "skills", slug: string, enabled: boolean): void {
    if (pack === null) {
      return
    }
    const token: string = `${tone}:${slug}`
    setSavingFlag(token)
    setFlagError(null)
    const write: Promise<RuleFile | SkillFile> =
      tone === "rules"
        ? fetchRule(props.assetId, slug).then((full: RuleFile) =>
            saveRule(props.assetId, slug, {
              name: full.name,
              description: full.description,
              enabled,
              order: full.order,
              body: full.body ?? "",
            }),
          )
        : fetchSkill(props.assetId, slug).then((full: SkillFile) =>
            saveSkill(props.assetId, slug, {
              name: full.name,
              description: full.description,
              enabled,
              tools: full.tools,
              body: full.body ?? "",
            }),
          )
    void write
      .then((saved: RuleFile | SkillFile) => {
        setPack((current: AgentPack | null) => {
          if (current === null) {
            return current
          }
          if (tone === "rules") {
            return {
              ...current,
              rules: current.rules.map((row: RuleFile) =>
                row.slug === saved.slug ? { ...row, enabled: saved.enabled, layer: saved.layer } : row,
              ),
            }
          }
          return {
            ...current,
            skills: current.skills.map((row: SkillFile) =>
              row.slug === saved.slug ? { ...row, enabled: saved.enabled, layer: saved.layer } : row,
            ),
          }
        })
      })
      .catch((err: unknown) => {
        setFlagError(err instanceof Error ? err.message : "Could not save switch")
      })
      .finally(() => {
        setSavingFlag((current: string | null) => (current === token ? null : current))
      })
  }

  return (
    <div className="asset-sheet">
      {error !== null ? <p className="error-text">{error}</p> : null}
      {pack === null && error === null ? <p className="catalog-lead">Loading agent files…</p> : null}
      {pack !== null ? (
        <div className="agent-pack">
          <p className="agent-note">
            <span className="agent-paths">
              {pack.layers.length > 0
                ? pack.layers.map((layer) => (
                    <span key={layer.prefix} className="agent-path">
                      {layer.prefix}
                    </span>
                  ))
                : pack.prefix !== null
                  ? <span className="agent-path">{pack.prefix}</span>
                  : null}
            </span>
            <span>
              {pack.writable
                ? "Rule and skill switches save. Prompts stay display-only."
                : (pack.message ?? "This page only shows these files.")}
            </span>
          </p>
          {flagError !== null ? <p className="error-text agent-flag-error">{flagError}</p> : null}
          <div className="agent-shell" style={{ "--agent-tree": `${treeWidth}px` } as CSSProperties}>
            <div className="agent-tree-frame">
            <nav className="agent-nav" aria-label="Agent files">
              {agentEntries.map((entry: Entry) => (
                <FileRow
                  key={entry.key}
                  entry={entry}
                  selected={section === "agent" && entry.key === fileKey}
                  onOpen={() => {
                    setSection("agent")
                    setFileKey(entry.key)
                    setMenuOpen(false)
                  }}
                />
              ))}
              {sections
                .filter((tone: Tone) => tone !== "agent")
                .map((tone: Tone) => {
                  const items: readonly Entry[] = sectionEntries(tone, pack, memoryFiles)
                  const count: number | null = tone === "memory" && memoryFiles === null ? null : items.length
                  const active: boolean = tone === section
                  const expanded: boolean = active && menuOpen && items.length > 1
                  return (
                    <div key={tone} className="agent-nav-group">
                      <div className={`agent-tree-row${active ? " is-active" : ""}`}>
                        <button
                          type="button"
                          className="agent-tree-hit"
                          aria-expanded={count !== null && count > 1 ? expanded : undefined}
                          onClick={() => chooseSection(tone)}
                        >
                          <span className="agent-tree-name">{SECTION_TITLE[tone]}</span>
                          <span className="agent-tree-meta">
                            {count !== null ? <span>{count}</span> : null}
                            {count !== null && count > 1 ? (
                              <span className={`agent-caret${expanded ? " is-open" : ""}`} aria-hidden="true" />
                            ) : null}
                          </span>
                        </button>
                      </div>
                      {expanded
                        ? items.map((entry: Entry) => (
                            <FileRow
                              key={entry.key}
                              entry={entry}
                              selected={entry.key === fileKey}
                              indent
                              onOpen={() => setFileKey(entry.key)}
                              onEnabled={
                                tone === "rules" || tone === "skills"
                                  ? (enabled: boolean) => toggleFile(tone, entry.key, enabled)
                                  : undefined
                              }
                              switchDisabled={savingFlag === `${tone}:${entry.key}`}
                            />
                          ))
                        : null}
                    </div>
                  )
                })}
            </nav>
            <TreeResize
              width={treeWidth}
              onWidth={(next: number) => {
                const clamped: number = clampTree(next)
                setTreeWidth(clamped)
                writeTreeWidth(clamped)
              }}
            />
            </div>
            <EditorPane
              fileKey={fileKey}
              path={selected === undefined ? null : entryPath(section, selected)}
              lead={selected === undefined ? null : lead}
              detail={selected === undefined ? null : fileDetail(selected)}
              enabled={selected?.enabled ?? null}
              switchLabel={selected === undefined ? "" : `${selected.name} enabled`}
              switchDisabled={selected === undefined || savingFlag === `${section}:${selected.key}`}
              onEnabled={
                selected !== undefined &&
                (section === "rules" || section === "skills") &&
                !(menuOpen && entries.length > 1)
                  ? (enabled: boolean) => toggleFile(section, selected.key, enabled)
                  : undefined
              }
              blank={
                entries.length === 0 && blank !== null && !(section === "memory" && memoryFiles === null)
                  ? blank
                  : null
              }
              status={
                section === "memory" && memoryFiles === null && memoryError === null
                  ? "Loading memory…"
                  : fileKey !== null && preview === null && previewError === null
                    ? "Loading…"
                    : null
              }
              memoryError={section === "memory" ? memoryError : null}
              previewError={previewError}
              preview={preview}
            />
          </div>
        </div>
      ) : null}
    </div>
  )
}

const SECTIONS: readonly Tone[] = ["agent", "prompts", "rules", "skills", "memory"]
const READONLY_SECTIONS: readonly Tone[] = ["agent", "memory"]

const SECTION_TITLE: Record<Tone, string> = {
  agent: "AGENTS.md",
  prompts: "prompts",
  rules: "rules",
  skills: "skills",
  memory: "memory",
}

type Tone = "agent" | "prompts" | "rules" | "skills" | "memory"

type Entry = {
  key: string
  name: string
  description: string
  meta: string
  enabled: boolean | null
  layer: string
}

type Preview = {
  title: string
  tone: Tone
  body: string
  references: SkillReference[]
}

function sectionLead(section: Tone, pack: AgentPack | null): string {
  if (section === "agent") {
    if (pack !== null && !pack.writable) {
      return pack.message ?? "This asset has no definition folder yet."
    }
    return "AGENTS.md is loaded on every run."
  }
  if (section === "prompts") {
    return "The active prompt is the task for a run."
  }
  if (section === "rules") {
    return "Enabled rules are added on every run."
  }
  if (section === "skills") {
    return "Enabled skills name the tools a run may use."
  }
  return "Written by the agent. Loaded on the next asset run."
}

function emptyCopy(section: Tone, pack: AgentPack): string | null {
  if (section === "agent") {
    if (!pack.writable) {
      return null
    }
    return "No AGENTS.md yet."
  }
  if (section === "prompts") {
    return "No prompts yet."
  }
  if (section === "rules") {
    return "No rules yet."
  }
  if (section === "skills") {
    return "No skills yet."
  }
  return "No memory yet."
}

function sectionEntries(section: Tone, pack: AgentPack, memoryFiles: MemoryFile[] | null): Entry[] {
  if (section === "agent") {
    if (!pack.writable || !pack.agent.present) {
      return []
    }
    return [
      {
        key: "agent",
        name: "AGENTS.md",
        description: "Agent card",
        meta: "",
        enabled: null,
        layer: pack.agent.layer,
      },
    ]
  }
  if (section === "prompts") {
    return pack.prompts.map((row: PromptFile) => ({
      key: row.slug,
      name: row.name,
      description: row.description,
      meta: promptMeta(row),
      enabled: null,
      layer: row.layer,
    }))
  }
  if (section === "rules") {
    return pack.rules.map((row: RuleFile) => ({
      key: row.slug,
      name: row.name,
      description: row.description,
      meta: String(row.order),
      enabled: row.enabled,
      layer: row.layer,
    }))
  }
  if (section === "skills") {
    return pack.skills.map((row: SkillFile) => ({
      key: row.slug,
      name: row.name,
      description: row.description,
      meta: row.tools.join(" "),
      enabled: row.enabled,
      layer: row.layer,
    }))
  }
  if (memoryFiles === null) {
    return []
  }
  return memoryFiles.map((file: MemoryFile) => ({
    key: file.name,
    name: file.name,
    description: file.updated_by === "agent" ? "Written by the agent" : "",
    meta: "",
    enabled: null,
    layer: "",
  }))
}

function promptMeta(row: PromptFile): string {
  if (row.active) {
    return row.enabled ? "on active" : "off active"
  }
  return row.enabled ? "on" : "off"
}

function entryPath(section: Tone, entry: Entry): string {
  if (section === "agent") {
    return "AGENTS.md"
  }
  if (section === "prompts") {
    return `prompts/${entry.key}.md`
  }
  if (section === "rules") {
    return `rules/${entry.key}.md`
  }
  if (section === "skills") {
    return `.agents/skills/${entry.key}/SKILL.md`
  }
  return entry.key
}

function loadPreview(assetId: string, section: Tone, fileKey: string): Promise<Preview> {
  if (section === "agent") {
    return fetchAgentCard(assetId).then((card) => ({
      title: "AGENTS.md",
      tone: "agent",
      body: card.body ?? "",
      references: [],
    }))
  }
  if (section === "prompts") {
    return fetchPrompt(assetId, fileKey).then((file: PromptFile) => ({
      title: file.name,
      tone: "prompts",
      body: file.body ?? "",
      references: [],
    }))
  }
  if (section === "rules") {
    return fetchRule(assetId, fileKey).then((file: RuleFile) => ({
      title: file.name,
      tone: "rules",
      body: file.body ?? "",
      references: [],
    }))
  }
  if (section === "skills") {
    return fetchSkill(assetId, fileKey).then((file: SkillFile) => ({
      title: file.name,
      tone: "skills",
      body: file.body ?? "",
      references: file.references ?? [],
    }))
  }
  return fetchMemoryFile(assetId, fileKey).then((loaded: MemoryFile) => ({
    title: loaded.name,
    tone: "memory",
    body: loaded.body ?? "",
    references: [],
  }))
}

function fileDetail(entry: Entry): string | null {
  const parts: string[] = []
  if (entry.description !== "") {
    parts.push(entry.description)
  }
  if (entry.meta !== "") {
    parts.push(entry.meta)
  }
  if (parts.length === 0) {
    return null
  }
  return parts.join(" · ")
}

function FileRow(props: {
  entry: Entry
  selected: boolean
  indent?: boolean
  onOpen: () => void
  onEnabled?: (enabled: boolean) => void
  switchDisabled?: boolean
}): ReactElement {
  return (
    <div className={`agent-tree-row${props.indent ? " agent-tree-file" : ""}${props.selected ? " is-active" : ""}`}>
      <button type="button" className="agent-tree-hit" onClick={props.onOpen}>
        <span className="agent-tree-name">
          {props.entry.name}
          {props.entry.layer !== "" ? <span className="agent-layer">{props.entry.layer}</span> : null}
        </span>
      </button>
      {props.onEnabled !== undefined && props.entry.enabled !== null ? (
        <FlagSwitch
          checked={props.entry.enabled}
          label={`${props.entry.name} enabled`}
          disabled={props.switchDisabled ?? false}
          onChange={props.onEnabled}
        />
      ) : null}
    </div>
  )
}

const TREE_KEY: string = "idc-agent-tree-width"
const TREE_DEFAULT: number = 220
const TREE_MIN: number = 160
const TREE_MAX: number = 480
const TREE_NUDGE: number = 16

function readTreeWidth(): number {
  try {
    const raw: string | null = window.localStorage.getItem(TREE_KEY)
    if (raw === null) {
      return TREE_DEFAULT
    }
    return clampTree(Number(raw))
  } catch {
    return TREE_DEFAULT
  }
}

function writeTreeWidth(width: number): void {
  try {
    window.localStorage.setItem(TREE_KEY, String(width))
  } catch {
    return
  }
}

function clampTree(value: number): number {
  if (!Number.isFinite(value)) {
    return TREE_DEFAULT
  }
  return Math.min(TREE_MAX, Math.max(TREE_MIN, Math.round(value)))
}

function TreeResize(props: { width: number; onWidth: (next: number) => void }): ReactElement {
  const drag: RefObject<{ startX: number; startWidth: number } | null> = useRef<{
    startX: number
    startWidth: number
  } | null>(null)
  const [dragging, setDragging] = useState<boolean>(false)

  function onPointerDown(event: PointerEvent<HTMLDivElement>): void {
    if (event.button !== 0) {
      return
    }
    const handle: HTMLDivElement = event.currentTarget
    try {
      handle.setPointerCapture(event.pointerId)
    } catch {
      // Capture needs an active pointer. Keyboard resize still works.
    }
    drag.current = { startX: event.clientX, startWidth: props.width }
    setDragging(true)
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>): void {
    const session: { startX: number; startWidth: number } | null = drag.current
    if (session === null) {
      return
    }
    props.onWidth(session.startWidth + (event.clientX - session.startX))
  }

  function endDrag(): void {
    if (drag.current === null) {
      return
    }
    drag.current = null
    setDragging(false)
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    const step: number = event.shiftKey ? TREE_NUDGE * 2 : TREE_NUDGE
    if (event.key === "ArrowRight") {
      event.preventDefault()
      props.onWidth(props.width + step)
      return
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault()
      props.onWidth(props.width - step)
      return
    }
    if (event.key === "Home") {
      event.preventDefault()
      props.onWidth(TREE_DEFAULT)
    }
  }

  return (
    <div
      className="pane-resize agent-tree-resize"
      data-edge="end"
      data-dragging={dragging ? "true" : "false"}
      role="slider"
      tabIndex={0}
      aria-orientation="vertical"
      aria-label="Resize agent files"
      aria-valuemin={TREE_MIN}
      aria-valuemax={TREE_MAX}
      aria-valuenow={props.width}
      title="Drag to resize. Double-click to reset."
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onDoubleClick={() => {
        props.onWidth(TREE_DEFAULT)
      }}
      onKeyDown={onKeyDown}
    />
  )
}

function FlagSwitch(props: {
  checked: boolean
  label: string
  disabled: boolean
  onChange: (checked: boolean) => void
}): ReactElement {
  return (
    <button
      type="button"
      className={`agent-switch${props.checked ? " is-on" : ""}`}
      role="switch"
      aria-checked={props.checked}
      aria-label={props.label}
      disabled={props.disabled}
      onClick={(event) => {
        event.stopPropagation()
        props.onChange(!props.checked)
      }}
    />
  )
}

function EditorPane(props: {
  fileKey: string | null
  path: string | null
  lead: string | null
  detail: string | null
  enabled: boolean | null
  switchLabel: string
  switchDisabled: boolean
  onEnabled?: (enabled: boolean) => void
  blank: string | null
  status: string | null
  memoryError: string | null
  previewError: string | null
  preview: Preview | null
}): ReactElement {
  const [source, setSource] = useState<boolean>(false)
  useEffect(() => {
    setSource(false)
  }, [props.fileKey])
  return (
    <section className="agent-editor" aria-label="Agent file">
      {props.path !== null ? (
        <header className="agent-editor-head">
          <span className="agent-editor-path">{props.path}</span>
          {props.onEnabled !== undefined && props.enabled !== null ? (
            <FlagSwitch
              checked={props.enabled}
              label={props.switchLabel}
              disabled={props.switchDisabled}
              onChange={props.onEnabled}
            />
          ) : null}
          <div className="agent-modes">
            <button type="button" className={source ? "" : "is-active"} onClick={() => setSource(false)}>
              Preview
            </button>
            <button type="button" className={source ? "is-active" : ""} onClick={() => setSource(true)}>
              Source
            </button>
          </div>
        </header>
      ) : null}
      {props.detail !== null ? <p className="agent-editor-detail">{props.detail}</p> : null}
      {props.lead !== null ? <p className="agent-editor-lead">{props.lead}</p> : null}
      {props.memoryError !== null ? <p className="error-text">{props.memoryError}</p> : null}
      {props.previewError !== null ? <p className="error-text">{props.previewError}</p> : null}
      {props.blank !== null ? <p className="agent-editor-status">{props.blank}</p> : null}
      {props.status !== null && props.blank === null ? <p className="agent-editor-status">{props.status}</p> : null}
      {props.preview !== null && source ? <pre className="agent-source">{props.preview.body}</pre> : null}
      {props.preview !== null && !source ? (
        <div className="agent-preview">
          <ReactMarkdown>{props.preview.body}</ReactMarkdown>
          {props.preview.references.map((reference: SkillReference) => (
            <section key={reference.name}>
              <h3>{reference.name}</h3>
              <ReactMarkdown>{reference.body}</ReactMarkdown>
            </section>
          ))}
        </div>
      ) : null}
    </section>
  )
}
