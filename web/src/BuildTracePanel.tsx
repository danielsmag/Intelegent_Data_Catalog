import { useEffect, useLayoutEffect, useRef, useState, type ReactElement, type RefObject, type UIEvent } from "react"
import { errorMessage, isAbortError } from "./api"
import { getPaneWidth, setPaneWidth } from "./paneLayout"
import {
  fetchBuildTraces,
  type BuildTraceEvent,
  type BuildTraceJob,
  type BuildTraceKind,
  type BuildTracePage,
} from "./registryApi"

const RAIL_DEFAULT: number = 280
const RAIL_TRACE: number = 440
const POLL_MS: number = 1000
const EXPAND_KEY: string = "idc-trace-expand"

type TraceFilter = "all" | BuildTraceKind

type FilterChoice = {
  id: TraceFilter
  label: string
  empty: string
}

function readExpandAll(): boolean {
  try {
    return window.localStorage.getItem(EXPAND_KEY) === "true"
  } catch {
    return false
  }
}

function writeExpandAll(open: boolean): void {
  try {
    window.localStorage.setItem(EXPAND_KEY, open ? "true" : "false")
  } catch {
    return
  }
}

const FILTERS = [
  { id: "all", label: "All", empty: "No builds are in this window." },
  { id: "table", label: "Tables", empty: "No table builds are in this window." },
  { id: "scope", label: "Scopes", empty: "No scope builds are in this window." },
  { id: "space", label: "Spaces", empty: "No space builds are in this window." },
  { id: "airflow", label: "Airflow", empty: "No Airflow builds are in this window." },
] as const satisfies readonly FilterChoice[]

type CardStatus = "running" | "saved" | "failed"

type JobClock = {
  status: CardStatus
  error: string | null
  finishedAt: string | null
}

type StepKind = "thought" | "tool" | "prose" | "meta" | "saved" | "failed" | "retry"

type TraceStepView = {
  id: string
  at: string
  title: string
  line: string
  detail: string
  kind: StepKind
}

type TraceCard = {
  jobId: string
  kind: BuildTraceKind
  subjectName: string
  status: CardStatus
  startedAt: string
  endedAt: string
  outcome: string | null
  steps: TraceStepView[]
}

export function BuildTracePanel(): ReactElement {
  const [events, setEvents] = useState<BuildTraceEvent[]>([])
  const [clocks, setClocks] = useState<Record<string, JobClock>>({})
  const openJobsRef: RefObject<string[]> = useRef<string[]>([])
  const [filter, setFilter] = useState<TraceFilter>("all")
  const [forced, setForced] = useState<Record<string, boolean>>({})
  const [openSteps, setOpenSteps] = useState<Record<string, boolean>>({})
  const [expandIncoming, setExpandIncoming] = useState<boolean>(readExpandAll)
  const [stickNewest, setStickNewest] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)
  const [now, setNow] = useState<number>(() => Date.now())
  const feedRef: RefObject<HTMLDivElement | null> = useRef<HTMLDivElement>(null)
  const stickRef: RefObject<boolean> = useRef<boolean>(true)
  const scrollingRef: RefObject<boolean> = useRef<boolean>(false)

  useEffect(() => {
    if (getPaneWidth("rail") === RAIL_DEFAULT) {
      setPaneWidth("rail", RAIL_TRACE)
    }
  }, [])

  useEffect(() => {
    const timer: number = window.setInterval(() => {
      setNow(Date.now())
    }, 1000)
    return () => {
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    let stopped: boolean = false
    let cursor: string | null = null
    const tick = (): void => {
      void fetchBuildTraces(cursor, openJobsRef.current, AbortSignal.timeout(4000))
        .then((page: BuildTracePage) => {
          if (stopped) {
            return
          }
          setError(null)
          if (page.events.length > 0) {
            setEvents((current: BuildTraceEvent[]) => mergeEvents(current, page.events))
          }
          if (page.jobs.length > 0) {
            setClocks((current: Record<string, JobClock>) => mergeClocks(current, page.jobs))
          }
          if (page.cursor !== null) {
            cursor = page.cursor
          }
        })
        .catch((err: unknown) => {
          if (stopped || isAbortError(err)) {
            return
          }
          setError(errorMessage(err))
        })
    }
    tick()
    const timer: number = window.setInterval(tick, POLL_MS)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [])

  const cards: TraceCard[] = groupCards(events, clocks)
  openJobsRef.current = openJobIds(cards)
  const shown: TraceCard[] = cards.filter((card: TraceCard) => filter === "all" || card.kind === filter)
  const choice: FilterChoice = FILTERS.find((item: FilterChoice) => item.id === filter) ?? FILTERS[0]
  const newestCard: TraceCard | undefined = shown[0]
  const tailStep: TraceStepView | undefined =
    newestCard === undefined ? undefined : newestCard.steps[newestCard.steps.length - 1]
  const newestKey: string =
    newestCard === undefined || tailStep === undefined
      ? ""
      : `${newestCard.jobId}:${newestCard.status}:${newestCard.steps.length}:${tailStep.id}:${tailStep.detail.length}`

  useLayoutEffect(() => {
    if (!stickRef.current) {
      return
    }
    scrollingRef.current = true
    scrollNewest(feedRef.current)
    const timer: number = window.setTimeout(() => {
      scrollingRef.current = false
    }, 80)
    return () => {
      window.clearTimeout(timer)
      scrollingRef.current = false
    }
  }, [newestKey, expandIncoming, forced, openSteps, stickNewest])

  function onFeedScroll(event: UIEvent<HTMLDivElement>): void {
    if (scrollingRef.current || !stickRef.current) {
      return
    }
    const feed: HTMLDivElement = event.currentTarget
    const card: HTMLElement | null = feed.querySelector<HTMLElement>(".trace-turn")
    if (card === null) {
      return
    }
    const drift: number = Math.abs(newestTopDelta(feed, card))
    if (drift <= 48) {
      return
    }
    stickRef.current = false
    setStickNewest(false)
  }

  function toggleStick(): void {
    const next: boolean = !stickRef.current
    stickRef.current = next
    setStickNewest(next)
  }

  function setShownOpen(open: boolean): void {
    writeExpandAll(open)
    setExpandIncoming(open)
    const jobIds: string[] = shown.map((card: TraceCard) => card.jobId)
    const stepIds: string[] = shown.flatMap((card: TraceCard) => disclosureKeys(card))
    setForced((current: Record<string, boolean>) => {
      const next: Record<string, boolean> = { ...current }
      for (const jobId of jobIds) {
        next[jobId] = open
      }
      return next
    })
    setOpenSteps((current: Record<string, boolean>) => {
      const next: Record<string, boolean> = { ...current }
      for (const stepId of stepIds) {
        next[stepId] = open
      }
      return next
    })
  }

  return (
    <div className="trace-panel">
      <div className="trace-toolbar">
        <div className="trace-filters" role="group" aria-label="Build kind">
          {FILTERS.map((item: FilterChoice) => (
            <button
              key={item.id}
              type="button"
              className="trace-filter"
              aria-pressed={item.id === filter}
              onClick={() => {
                setFilter(item.id)
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div className="trace-bulk">
          <button
            type="button"
            className="trace-bulk-button"
            disabled={shown.length === 0}
            aria-pressed={expandIncoming}
            onClick={() => {
              setShownOpen(true)
            }}
          >
            Expand all
          </button>
          <button
            type="button"
            className="trace-bulk-button"
            disabled={shown.length === 0}
            onClick={() => {
              setShownOpen(false)
            }}
          >
            Collapse all
          </button>
        </div>
        <button
          type="button"
          className="trace-bulk-button trace-stick"
          aria-pressed={stickNewest}
          title="Stick to newest message"
          onClick={toggleStick}
        >
          Newest
        </button>
      </div>
      <div className="trace-feed" ref={feedRef} onScroll={onFeedScroll}>
        {shown.length === 0 ? (
          <div className="trace-empty">
            {events.length === 0 && error !== null ? (
              <p>{error}</p>
            ) : events.length === 0 ? (
              <>
                <p>Context builds appear here while they run.</p>
                <p>Start a build from a table, scope, space, or Airflow instance.</p>
              </>
            ) : (
              <p>{choice.empty}</p>
            )}
          </div>
        ) : (
          shown.map((card: TraceCard) => {
            const forcedOpen: boolean | undefined = forced[card.jobId]
            const open: boolean = forcedOpen !== undefined ? forcedOpen : expandIncoming || card.status === "running"
            return (
              <TraceTurn
                key={card.jobId}
                card={card}
                now={now}
                open={open}
                openSteps={openSteps}
                expandIncoming={expandIncoming}
                onToggleRun={() => {
                  setForced((current: Record<string, boolean>) => ({
                    ...current,
                    [card.jobId]: !open,
                  }))
                }}
                onToggleStep={(id: string, stepOpen: boolean) => {
                  setOpenSteps((current: Record<string, boolean>) => ({
                    ...current,
                    [id]: !stepOpen,
                  }))
                }}
              />
            )
          })
        )}
      </div>
    </div>
  )
}

function TraceTurn(props: {
  card: TraceCard
  now: number
  open: boolean
  openSteps: Record<string, boolean>
  expandIncoming: boolean
  onToggleRun: () => void
  onToggleStep: (id: string, open: boolean) => void
}): ReactElement {
  const label: string = workLabel(props.card, props.now)
  const failed: boolean = props.card.status === "failed" && props.card.outcome !== null
  return (
    <article className={`trace-turn trace-kind-${props.card.kind}`}>
      <div className="trace-bubble">{requestCopy(props.card)}</div>
      <button
        type="button"
        className="trace-run"
        data-status={props.card.status}
        aria-expanded={props.open}
        onClick={props.onToggleRun}
      >
        <span className="trace-run-label">{label}</span>
        <TraceChevron open={props.open} />
      </button>
      {props.open ? (
        <div className="trace-run-body">
          {renderSteps(
            props.card.steps,
            props.card.status === "running",
            props.now,
            props.card.status === "running" ? undefined : props.card.endedAt,
            props.openSteps,
            props.expandIncoming,
            props.onToggleStep,
          )}
        </div>
      ) : failed ? (
        <p className="trace-outcome trace-outcome-failed">{props.card.outcome}</p>
      ) : null}
    </article>
  )
}

function TraceChevron(props: { open: boolean }): ReactElement {
  const className: string = props.open ? "trace-chevron open" : "trace-chevron"
  return <span className={className} aria-hidden="true" />
}

function renderSteps(
  steps: readonly TraceStepView[],
  running: boolean,
  now: number,
  endedAt: string | undefined,
  openSteps: Record<string, boolean>,
  expandIncoming: boolean,
  onToggleStep: (id: string, open: boolean) => void,
): ReactElement[] {
  const blocks: ReactElement[] = []
  let index: number = 0
  while (index < steps.length) {
    const step: TraceStepView | undefined = steps[index]
    if (step === undefined) {
      break
    }
    if (step.kind === "tool") {
      const tools: TraceStepView[] = []
      while (index < steps.length) {
        const next: TraceStepView | undefined = steps[index]
        if (next === undefined || next.kind !== "tool") {
          break
        }
        tools.push(next)
        index += 1
      }
      const first: TraceStepView | undefined = tools[0]
      if (first !== undefined) {
        const key: string = `tools:${first.id}`
        const open: boolean = disclosureOpen(openSteps, key, expandIncoming)
        blocks.push(
          <ToolGroup
            key={key}
            tools={tools}
            open={open}
            onToggle={() => {
              onToggleStep(key, open)
            }}
          />,
        )
      }
      continue
    }
    const following: TraceStepView | undefined = steps[index + 1]
    const open: boolean = disclosureOpen(openSteps, step.id, expandIncoming)
    blocks.push(
      <TraceStep
        key={step.id}
        step={step}
        nextAt={following === undefined ? undefined : following.at}
        running={running}
        endedAt={endedAt}
        now={now}
        open={open}
        onToggle={() => {
          onToggleStep(step.id, open)
        }}
      />,
    )
    index += 1
  }
  return blocks
}

function TraceStep(props: {
  step: TraceStepView
  nextAt: string | undefined
  running: boolean
  endedAt: string | undefined
  now: number
  open: boolean
  onToggle: () => void
}): ReactElement {
  if (props.step.kind === "prose") {
    if (isStructured(props.step.detail)) {
      return <CodeBlock title="answer" body={props.step.detail} />
    }
    return <p className="trace-prose">{props.step.detail}</p>
  }
  if (props.step.kind === "failed") {
    return <p className="trace-prose trace-prose-failed">{props.step.detail || "Build failed."}</p>
  }
  if (props.step.kind === "saved") {
    return <p className="trace-meta">{props.step.detail || "Context saved"}</p>
  }
  if (props.step.kind === "meta") {
    return <p className="trace-meta">{props.step.detail}</p>
  }
  const label: string =
    props.step.kind === "thought"
      ? thoughtLabel(props.step, props.nextAt, props.now, props.running, props.endedAt)
      : props.step.title
  if (props.step.detail === "") {
    return (
      <div className="trace-row">
        <span className="trace-row-title">{label}</span>
      </div>
    )
  }
  return (
    <div className="trace-group">
      <button type="button" className="trace-row" aria-expanded={props.open} onClick={props.onToggle}>
        <span className="trace-row-title">{label}</span>
        <TraceChevron open={props.open} />
      </button>
      {props.open ? <TraceBody detail={props.step.detail} framed /> : null}
    </div>
  )
}

function CodeBlock(props: { title: string; body: string }): ReactElement {
  return (
    <div className="trace-block">
      <div className="trace-block-head">
        <span className="trace-mark" aria-hidden="true" />
        <span>{props.title}</span>
      </div>
      {props.body !== "" ? <TraceBody detail={props.body} /> : null}
    </div>
  )
}

function ToolGroup(props: { tools: TraceStepView[]; open: boolean; onToggle: () => void }): ReactElement | null {
  const first: TraceStepView | undefined = props.tools[0]
  if (first === undefined) {
    return null
  }
  const single: boolean = props.tools.length === 1
  const label: string = single ? first.title : `Explored ${props.tools.length} tools`
  const line: string = single ? first.line : props.tools.map((tool: TraceStepView) => tool.title).join(", ")
  return (
    <div className="trace-group">
      <button type="button" className="trace-row" aria-expanded={props.open} onClick={props.onToggle}>
        <span className="trace-row-title">{label}</span>
        <TraceChevron open={props.open} />
        {!props.open && line !== "" ? <span className="trace-row-line">{line}</span> : null}
      </button>
      {props.open && single && first.detail !== "" ? (
        <CodeBlock title={first.title} body={first.detail} />
      ) : null}
      {props.open && !single
        ? props.tools.map((tool: TraceStepView) => (
            <CodeBlock key={tool.id} title={tool.title} body={tool.detail} />
          ))
        : null}
    </div>
  )
}

function scrollNewest(feed: HTMLDivElement | null): void {
  if (feed === null) {
    return
  }
  const card: HTMLElement | null = feed.querySelector<HTMLElement>(".trace-turn")
  if (card === null) {
    return
  }
  const delta: number = newestTopDelta(feed, card)
  if (Math.abs(delta) <= 1) {
    return
  }
  feed.scrollTop += delta
}

function newestTopDelta(feed: HTMLDivElement, card: HTMLElement): number {
  const feedStyle: CSSStyleDeclaration = window.getComputedStyle(feed)
  const padTop: number = Number.parseFloat(feedStyle.paddingTop)
  const topInset: number = Number.isFinite(padTop) ? padTop : 0
  const feedRect: DOMRect = feed.getBoundingClientRect()
  const cardRect: DOMRect = card.getBoundingClientRect()
  return cardRect.top - (feedRect.top + topInset)
}

function disclosureOpen(openSteps: Record<string, boolean>, id: string, expandIncoming: boolean): boolean {
  const forced: boolean | undefined = openSteps[id]
  if (forced !== undefined) {
    return forced
  }
  return expandIncoming
}

function disclosureKeys(card: TraceCard): string[] {
  const keys: string[] = []
  let index: number = 0
  const steps: readonly TraceStepView[] = card.steps
  while (index < steps.length) {
    const step: TraceStepView | undefined = steps[index]
    if (step === undefined) {
      break
    }
    if (step.kind === "tool") {
      keys.push(`tools:${step.id}`)
      index += 1
      while (index < steps.length) {
        const next: TraceStepView | undefined = steps[index]
        if (next === undefined || next.kind !== "tool") {
          break
        }
        index += 1
      }
      continue
    }
    if ((step.kind === "thought" || step.kind === "retry") && step.detail !== "") {
      keys.push(step.id)
    }
    index += 1
  }
  return keys
}

function requestCopy(card: TraceCard): string {
  if (card.kind === "airflow") {
    return `Analyze ${card.subjectName}`
  }
  if (card.kind === "table") {
    return `Build context for ${card.subjectName}`
  }
  return `Build ${card.kind} context for ${card.subjectName}`
}

function workLabel(card: TraceCard, now: number): string {
  const end: string = card.status === "running" ? new Date(now).toISOString() : card.endedAt
  const seconds: number | null = elapsedSeconds(card.startedAt, end)
  if (seconds === null || seconds === 0) {
    return card.status === "running" ? "Working…" : "Worked for <1s"
  }
  const span: string = formatSpan(seconds)
  return card.status === "running" ? `Working for ${span}` : `Worked for ${span}`
}

function thoughtLabel(
  step: TraceStepView,
  nextAt: string | undefined,
  now: number,
  running: boolean,
  endedAt: string | undefined,
): string {
  const end: string | undefined = nextAt ?? (running ? new Date(now).toISOString() : endedAt)
  if (end === undefined) {
    return "Thought"
  }
  const seconds: number | null = elapsedSeconds(step.at, end)
  if (seconds === null || seconds <= 0) {
    return "Thought"
  }
  return `Thought ${formatSpan(seconds)}`
}

function elapsedSeconds(start: string, end: string): number | null {
  const startMs: number = new Date(start).getTime()
  const endMs: number = new Date(end).getTime()
  if (Number.isNaN(startMs) || Number.isNaN(endMs)) {
    return null
  }
  return Math.max(0, Math.round((endMs - startMs) / 1000))
}

function formatSpan(totalSeconds: number): string {
  const seconds: number = Math.max(0, Math.round(totalSeconds))
  if (seconds < 60) {
    return `${seconds}s`
  }
  const minutes: number = Math.floor(seconds / 60)
  const rest: number = seconds % 60
  if (rest === 0) {
    return `${minutes}m`
  }
  return `${minutes}m ${rest}s`
}

function sameBody(left: string, right: string): boolean {
  const leftTrim: string = left.trim()
  const rightTrim: string = right.trim()
  if (leftTrim === rightTrim) {
    return true
  }
  if (!isStructured(leftTrim) || !isStructured(rightTrim)) {
    return false
  }
  try {
    const leftParsed: unknown = JSON.parse(leftTrim)
    const rightParsed: unknown = JSON.parse(rightTrim)
    return JSON.stringify(leftParsed) === JSON.stringify(rightParsed)
  } catch {
    return false
  }
}

function isStructured(detail: string): boolean {
  const trimmed: string = detail.trim()
  return trimmed.startsWith("{") || trimmed.startsWith("[")
}

const PROSE_KEYS: readonly string[] = [
  "summary",
  "description",
  "context",
  "instance_context",
  "space_context",
  "scope_context",
  "reason",
]

type TraceNote = {
  label: string
  text: string
}

type TraceBodyView = {
  notes: TraceNote[]
  code: string
}

type TokenKind = "key" | "string" | "number" | "punct" | "literal" | "text"

type TraceToken = {
  kind: TokenKind
  text: string
}

function TraceBody(props: { detail: string; framed?: boolean }): ReactElement | null {
  const view: TraceBodyView = presentDetail(props.detail)
  if (view.notes.length === 0 && view.code === "") {
    return null
  }
  const className: string = props.framed === true ? "trace-detail trace-detail-framed" : "trace-detail"
  return (
    <div className={className}>
      {view.notes.map((note: TraceNote) => (
        <p key={note.label} className="trace-readable">
          {note.label !== "" ? <span className="trace-readable-label">{note.label}</span> : null}
          {note.text}
        </p>
      ))}
      {view.code !== "" ? <pre className="trace-code">{colorTokens(view.code)}</pre> : null}
    </div>
  )
}

function presentDetail(detail: string): TraceBodyView {
  const trimmed: string = detail.trim()
  if (trimmed === "") {
    return { notes: [], code: "" }
  }
  let parsed: unknown
  let valid: boolean = false
  try {
    parsed = JSON.parse(trimmed)
    valid = true
  } catch {
    parsed = undefined
  }
  if (valid && isRecord(parsed)) {
    return presentRecord(parsed)
  }
  if (valid && typeof parsed === "string") {
    return { notes: [{ label: "", text: parsed }], code: "" }
  }
  if (valid) {
    return { notes: [], code: JSON.stringify(parsed, null, 2) }
  }
  return { notes: [], code: loosen(trimmed) }
}

function presentRecord(parsed: Record<string, unknown>): TraceBodyView {
  const notes: TraceNote[] = []
  const rest: Record<string, unknown> = {}
  for (const key of Object.keys(parsed)) {
    const value: unknown = parsed[key]
    if (isProse(key, value)) {
      notes.push({ label: key.replaceAll("_", " "), text: value.trim() })
      continue
    }
    rest[key] = value
  }
  const code: string = Object.keys(rest).length === 0 ? "" : JSON.stringify(rest, null, 2)
  return { notes, code }
}

function isProse(key: string, value: unknown): value is string {
  return PROSE_KEYS.includes(key) && typeof value === "string" && value.trim() !== ""
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function colorTokens(source: string): ReactElement[] {
  const tokens: TraceToken[] = scanTokens(source)
  return tokens.map((token: TraceToken, index: number) => (
    <span key={`${token.kind}-${index}`} className={`trace-tok trace-tok-${token.kind}`}>
      {token.text}
    </span>
  ))
}

function scanTokens(source: string): TraceToken[] {
  const tokens: TraceToken[] = []
  let index: number = 0
  while (index < source.length) {
    const char: string = source[index] ?? ""
    if (char === '"') {
      const end: number = endString(source, index)
      const raw: string = source.slice(index, end)
      const next: number = skipSpace(source, end)
      const kind: TokenKind = source[next] === ":" ? "key" : "string"
      tokens.push({ kind, text: raw })
      index = end
      continue
    }
    if (char === " " || char === "\n" || char === "\t") {
      const start: number = index
      index += 1
      while (index < source.length) {
        const space: string = source[index] ?? ""
        if (space !== " " && space !== "\n" && space !== "\t") {
          break
        }
        index += 1
      }
      tokens.push({ kind: "text", text: source.slice(start, index) })
      continue
    }
    if ("{}[]:,".includes(char)) {
      tokens.push({ kind: "punct", text: char })
      index += 1
      continue
    }
    if (char === "-" || (char >= "0" && char <= "9")) {
      const start: number = index
      index += 1
      while (index < source.length && "0123456789.eE+-".includes(source[index] ?? "")) {
        index += 1
      }
      tokens.push({ kind: "number", text: source.slice(start, index) })
      continue
    }
    const word: string = readWord(source, index)
    if (word === "true" || word === "false" || word === "null") {
      tokens.push({ kind: "literal", text: word })
      index += word.length
      continue
    }
    if (word !== "") {
      tokens.push({ kind: "text", text: word })
      index += word.length
      continue
    }
    tokens.push({ kind: "text", text: char })
    index += 1
  }
  return tokens
}

function endString(source: string, start: number): number {
  let index: number = start + 1
  let escaped: boolean = false
  while (index < source.length) {
    const char: string = source[index] ?? ""
    index += 1
    if (escaped) {
      escaped = false
      continue
    }
    if (char === "\\") {
      escaped = true
      continue
    }
    if (char === '"') {
      return index
    }
  }
  return source.length
}

function skipSpace(source: string, start: number): number {
  let index: number = start
  while (index < source.length) {
    const char: string = source[index] ?? ""
    if (char !== " " && char !== "\n" && char !== "\t") {
      return index
    }
    index += 1
  }
  return index
}

function readWord(source: string, start: number): string {
  let index: number = start
  while (index < source.length) {
    const char: string = source[index] ?? ""
    if (!/[A-Za-z_]/.test(char)) {
      break
    }
    index += 1
  }
  return source.slice(start, index)
}

function loosen(detail: string): string {
  const trimmed: string = detail.trim()
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) {
    return detail
  }
  let result: string = ""
  let depth: number = 0
  let inString: boolean = false
  let escaped: boolean = false
  for (const char of trimmed) {
    if (inString) {
      result += char
      if (escaped) {
        escaped = false
        continue
      }
      if (char === "\\") {
        escaped = true
        continue
      }
      if (char === '"') {
        inString = false
      }
      continue
    }
    if (char === '"') {
      inString = true
      result += char
      continue
    }
    if (char === "{" || char === "[") {
      depth += 1
      result += `${char}\n${"  ".repeat(depth)}`
      continue
    }
    if (char === "}" || char === "]") {
      depth = Math.max(0, depth - 1)
      result += `\n${"  ".repeat(depth)}${char}`
      continue
    }
    if (char === ",") {
      result += `,\n${"  ".repeat(depth)}`
      continue
    }
    if (char === ":") {
      result += ": "
      continue
    }
    if (char === " " || char === "\n" || char === "\t") {
      continue
    }
    result += char
  }
  return result
}

function previewLine(detail: string): string {
  const flat: string = detail.replace(/\s+/g, " ").trim()
  if (flat.length <= 96) {
    return flat
  }
  return `${flat.slice(0, 96)}…`
}

function mergeEvents(current: BuildTraceEvent[], incoming: BuildTraceEvent[]): BuildTraceEvent[] {
  const byId: Map<string, BuildTraceEvent> = new Map()
  for (const event of current) {
    byId.set(event.id, event)
  }
  for (const event of incoming) {
    byId.set(event.id, event)
  }
  const merged: BuildTraceEvent[] = [...byId.values()]
  merged.sort((left: BuildTraceEvent, right: BuildTraceEvent) => (left.id < right.id ? -1 : 1))
  if (merged.length <= 400) {
    return merged
  }
  return merged.slice(merged.length - 400)
}

function mergeClocks(
  current: Record<string, JobClock>,
  incoming: readonly BuildTraceJob[],
): Record<string, JobClock> {
  const next: Record<string, JobClock> = { ...current }
  for (const job of incoming) {
    const status: CardStatus =
      job.status === "failed" ? "failed" : job.status === "succeeded" ? "saved" : "running"
    next[job.job_id] = {
      status,
      error: job.error,
      finishedAt: job.finished_at,
    }
  }
  return next
}

function openJobIds(cards: readonly TraceCard[]): string[] {
  const ids: string[] = []
  for (const card of cards) {
    if (card.status !== "running") {
      continue
    }
    ids.push(card.jobId)
    if (ids.length >= 80) {
      break
    }
  }
  return ids
}

function groupCards(events: BuildTraceEvent[], clocks: Record<string, JobClock>): TraceCard[] {
  const groups: Map<string, BuildTraceEvent[]> = new Map()
  for (const event of events) {
    const bucket: BuildTraceEvent[] = groups.get(event.job_id) ?? []
    bucket.push(event)
    groups.set(event.job_id, bucket)
  }
  const cards: TraceCard[] = []
  for (const [jobId, steps] of groups) {
    const ordered: BuildTraceEvent[] = [...steps].sort((left: BuildTraceEvent, right: BuildTraceEvent) =>
      left.id < right.id ? -1 : 1,
    )
    const first: BuildTraceEvent | undefined = ordered[0]
    const last: BuildTraceEvent | undefined = ordered[ordered.length - 1]
    if (first === undefined || last === undefined) {
      continue
    }
    const clock: JobClock | undefined = clocks[jobId]
    const status: CardStatus = cardStatus(ordered, clock)
    const finishedAt: string | null = clock === undefined ? null : clock.finishedAt
    cards.push({
      jobId,
      kind: first.kind,
      subjectName: first.subject_name,
      status,
      startedAt: first.at,
      endedAt: finishedAt ?? last.at,
      outcome: cardOutcome(ordered, clock),
      steps: stepViews(ordered),
    })
  }
  cards.sort(compareCards)
  return cards
}

function cardStatus(events: readonly BuildTraceEvent[], clock: JobClock | undefined): CardStatus {
  if (events.some((event: BuildTraceEvent) => event.step === "failed") || clock?.status === "failed") {
    return "failed"
  }
  if (events.some((event: BuildTraceEvent) => event.step === "succeeded") || clock?.status === "saved") {
    return "saved"
  }
  return "running"
}

function cardOutcome(events: readonly BuildTraceEvent[], clock: JobClock | undefined): string | null {
  const ordered: BuildTraceEvent[] = [...events].reverse()
  const failed: BuildTraceEvent | undefined = ordered.find((event: BuildTraceEvent) => event.step === "failed")
  if (failed !== undefined) {
    return failed.detail !== "" ? failed.detail : "Build failed."
  }
  const saved: BuildTraceEvent | undefined = ordered.find((event: BuildTraceEvent) => event.step === "succeeded")
  if (saved !== undefined) {
    return saved.detail !== "" ? saved.detail : "Context saved"
  }
  if (clock?.status === "failed") {
    return clock.error !== null && clock.error !== "" ? clock.error : "Build failed."
  }
  if (clock?.status === "saved") {
    return "Context saved"
  }
  return null
}

function stepViews(events: readonly BuildTraceEvent[]): TraceStepView[] {
  const views: TraceStepView[] = []
  for (const event of events) {
    const kind: StepKind | null = viewKind(event.step)
    if (kind === null) {
      continue
    }
    const previous: TraceStepView | undefined = views[views.length - 1]
    if (kind === "prose" && previous !== undefined && previous.kind === "prose" && sameBody(previous.detail, event.detail)) {
      continue
    }
    if (
      event.step === "tool_result" &&
      previous !== undefined &&
      previous.kind === "tool" &&
      previous.title === event.title
    ) {
      previous.line = previewLine(event.detail)
      previous.detail = event.detail
      continue
    }
    views.push({
      id: event.id,
      at: event.at,
      title: event.title,
      line: kind === "tool" ? previewLine(event.detail) : "",
      detail: event.detail,
      kind,
    })
  }
  return withoutSupersededAnswers(views)
}

function withoutSupersededAnswers(views: TraceStepView[]): TraceStepView[] {
  const kept: TraceStepView[] = []
  let index: number = 0
  while (index < views.length) {
    const step: TraceStepView | undefined = views[index]
    if (step === undefined) {
      break
    }
    if (step.kind === "prose" && supersededByLaterAnswer(views, index)) {
      index += 1
      continue
    }
    kept.push(step)
    index += 1
  }
  return kept
}

function supersededByLaterAnswer(views: readonly TraceStepView[], index: number): boolean {
  let cursor: number = index + 1
  while (cursor < views.length) {
    const step: TraceStepView | undefined = views[cursor]
    if (step === undefined) {
      return false
    }
    if (step.kind === "prose") {
      return true
    }
    if (step.kind === "retry" || step.kind === "meta") {
      cursor += 1
      continue
    }
    return false
  }
  return false
}

function viewKind(step: string): StepKind | null {
  if (step === "ask") {
    return "thought"
  }
  if (step === "tool_call" || step === "tool_result") {
    return "tool"
  }
  if (step === "text" || step === "output") {
    return "prose"
  }
  if (step === "usage") {
    return "meta"
  }
  if (step === "succeeded") {
    return "saved"
  }
  if (step === "failed") {
    return "failed"
  }
  if (step === "retry") {
    return "retry"
  }
  return null
}

function compareCards(left: TraceCard, right: TraceCard): number {
  const leftRank: number = left.status === "running" ? 0 : 1
  const rightRank: number = right.status === "running" ? 0 : 1
  if (leftRank !== rightRank) {
    return leftRank - rightRank
  }
  if (left.startedAt === right.startedAt) {
    return right.jobId < left.jobId ? -1 : 1
  }
  return right.startedAt < left.startedAt ? -1 : 1
}
