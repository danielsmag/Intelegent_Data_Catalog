import { useEffect, useRef, useState, type KeyboardEvent, type ReactElement } from "react"
import {
  catalogGrid,
  htmlText,
  newCell,
  parseNotebook,
  plainText,
  pngText,
  serializeNotebook,
  sparkTableSnippet,
  stripAnsi,
  trinoEngineSnippet,
  trinoSnippet,
  type CellOutput,
  type CodeCell,
  type NotebookCell,
  type NotebookDocument,
} from "./notebookFormat"
import {
  interruptKernel,
  refreshKernelTicket,
  restartKernel,
  startKernel,
  stopKernelKeepalive,
  type KernelSession,
} from "./notebookApi"
import { KernelConnection, useKernelPhase, type KernelPhase } from "./notebookKernel"
import { ResultGrid } from "./ResultGrid"

export function NotebookEditor(props: {
  source: string
  workspaceId: string
  nodeId: string
  onChange: (text: string) => void
}): ReactElement {
  const initial = parseNotebook(props.source)
  const [notebook, setNotebook] = useState<NotebookDocument>(initial.notebook)
  const [parseError, setParseError] = useState<string | null>(initial.error)
  const [connection, setConnection] = useState<KernelConnection | null>(null)
  const [info, setInfo] = useState<KernelSession | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [runningId, setRunningId] = useState<string | null>(null)
  const [focused, setFocused] = useState<number>(0)
  const [editing, setEditing] = useState<number | null>(null)
  const [connectionsOpen, setConnectionsOpen] = useState<boolean>(false)
  const emitted: { current: string } = useRef<string>(props.source)
  const notebookRef: { current: NotebookDocument } = useRef<NotebookDocument>(notebook)
  const connectionRef: { current: KernelConnection | null } = useRef<KernelConnection | null>(null)
  const releaseRef: { current: () => void } = useRef<() => void>(() => undefined)
  const focusedRef: { current: number } = useRef<number>(focused)
  const editingRef: { current: number | null } = useRef<number | null>(editing)
  const deleteArmed: { current: boolean } = useRef<boolean>(false)
  const rootRef: { current: HTMLDivElement | null } = useRef<HTMLDivElement | null>(null)
  focusedRef.current = focused
  editingRef.current = editing
  notebookRef.current = notebook
  const phase: KernelPhase = useKernelPhase(connection)

  useEffect(() => {
    if (props.source === emitted.current) {
      return
    }
    const parsed = parseNotebook(props.source)
    setNotebook(parsed.notebook)
    setParseError(parsed.error)
    emitted.current = props.source
  }, [props.source])

  useEffect(() => {
    releaseRef.current = () => {
      const current: KernelConnection | null = connectionRef.current
      connectionRef.current = null
      if (current === null) {
        return
      }
      const kernelId: string = current.kernelId
      current.close()
      stopKernelKeepalive(kernelId)
    }
    const onLeave = (): void => {
      releaseRef.current()
    }
    window.addEventListener("beforeunload", onLeave)
    return () => {
      window.removeEventListener("beforeunload", onLeave)
      releaseRef.current()
    }
  }, [])

  function commit(next: NotebookDocument): void {
    notebookRef.current = next
    setNotebook(next)
    const text: string = serializeNotebook(next)
    emitted.current = text
    props.onChange(text)
  }

  async function ensureKernel(): Promise<KernelConnection> {
    const current: KernelConnection | null = connectionRef.current
    if (current !== null && current.phase() !== "dead") {
      await current.ready()
      return current
    }
    if (current !== null) {
      current.close()
    }
    const session: KernelSession = await startKernel(props.workspaceId, props.nodeId)
    setInfo(session)
    const opened: KernelConnection = new KernelConnection(session.kernel_id, session.ticket, session.ws_path)
    opened.connect()
    connectionRef.current = opened
    setConnection(opened)
    await opened.ready()
    return opened
  }

  async function runAt(index: number): Promise<void> {
    const cell: NotebookCell | undefined = notebookRef.current.cells[index]
    if (cell === undefined || cell.cell_type !== "code") {
      return
    }
    setError(null)
    setRunningId(cell.id)
    const cleared: NotebookCell[] = notebookRef.current.cells.map((item: NotebookCell, itemIndex: number) =>
      itemIndex === index && item.cell_type === "code" ? { ...item, outputs: [] } : item,
    )
    commit({ ...notebookRef.current, cells: cleared })
    try {
      const kernel: KernelConnection = await ensureKernel()
      const result = await kernel.execute(cell.source)
      const next: NotebookCell[] = notebookRef.current.cells.map((item: NotebookCell) => {
        if (item.id !== cell.id || item.cell_type !== "code") {
          return item
        }
        return { ...item, outputs: result.outputs, execution_count: result.executionCount }
      })
      commit({ ...notebookRef.current, cells: next })
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "cell failed")
    } finally {
      setRunningId((current: string | null) => (current === cell.id ? null : current))
    }
  }

  async function runAndAdvance(index: number): Promise<void> {
    await runAt(index)
    const latest: NotebookDocument = notebookRef.current
    if (index >= latest.cells.length - 1) {
      const added: NotebookCell[] = [...latest.cells, newCell("code")]
      commit({ ...latest, cells: added })
      setFocused(added.length - 1)
      setEditing(null)
      return
    }
    setFocused(index + 1)
    setEditing(null)
  }

  async function runAll(): Promise<void> {
    const total: number = notebookRef.current.cells.length
    for (let index: number = 0; index < total; index += 1) {
      const cell: NotebookCell | undefined = notebookRef.current.cells[index]
      if (cell?.cell_type === "code") {
        await runAt(index)
      }
    }
  }

  function editCell(index: number, source: string): void {
    const latest: NotebookDocument = notebookRef.current
    const next: NotebookCell[] = latest.cells.map((cell: NotebookCell, cellIndex: number) =>
      cellIndex === index ? { ...cell, source } : cell,
    )
    commit({ ...latest, cells: next })
  }

  function insertCell(index: number, kind: "code" | "markdown"): void {
    const latest: NotebookDocument = notebookRef.current
    const next: NotebookCell[] = latest.cells.slice()
    const insertAt: number = Math.max(0, Math.min(index, next.length))
    next.splice(insertAt, 0, newCell(kind))
    commit({ ...latest, cells: next })
    setFocused(insertAt)
    setEditing(null)
  }

  function setKind(index: number, kind: "code" | "markdown"): void {
    const latest: NotebookDocument = notebookRef.current
    const cell: NotebookCell | undefined = latest.cells[index]
    if (cell === undefined || cell.cell_type === kind) {
      return
    }
    const replacement: NotebookCell = newCell(kind)
    const next: NotebookCell[] = latest.cells.slice()
    next[index] = { ...replacement, id: cell.id, source: cell.source }
    commit({ ...latest, cells: next })
    setEditing(kind === "code" ? index : null)
  }

  function move(index: number, direction: -1 | 1): void {
    const target: number = index + direction
    const latest: NotebookDocument = notebookRef.current
    const current: NotebookCell | undefined = latest.cells[index]
    const other: NotebookCell | undefined = latest.cells[target]
    if (current === undefined || other === undefined) {
      return
    }
    const next: NotebookCell[] = latest.cells.slice()
    next[index] = other
    next[target] = current
    commit({ ...latest, cells: next })
    setFocused(target)
  }

  function remove(index: number): void {
    const latest: NotebookDocument = notebookRef.current
    const next: NotebookCell[] = latest.cells.filter((_cell: NotebookCell, cellIndex: number) => cellIndex !== index)
    commit({ ...latest, cells: next.length === 0 ? [newCell("code")] : next })
    setFocused(Math.max(0, index - 1))
  }

  function insertSnippet(snippet: string): void {
    const latest: NotebookDocument = notebookRef.current
    const cell: NotebookCell | undefined = latest.cells[focused]
    if (cell === undefined || cell.cell_type !== "code") {
      const added: NotebookCell = newCell("code")
      const withSnippet: NotebookCell = { ...added, source: snippet }
      commit({ ...latest, cells: [...latest.cells, withSnippet] })
      setFocused(latest.cells.length)
      return
    }
    const source: string = cell.source.trim() === "" ? snippet : `${cell.source}\n${snippet}`
    editCell(focused, source)
    setConnectionsOpen(false)
  }

  async function onRestart(): Promise<void> {
    const current: KernelConnection | null = connectionRef.current
    if (current === null) {
      return
    }
    await restartKernel(current.kernelId)
    current.close()
    const ticket = await refreshKernelTicket(current.kernelId)
    const opened: KernelConnection = new KernelConnection(current.kernelId, ticket.ticket, ticket.ws_path)
    opened.connect()
    connectionRef.current = opened
    setConnection(opened)
  }

  function onEditorKeyDown(event: KeyboardEvent<HTMLTextAreaElement>, index: number): void {
    if (event.key === "Escape") {
      event.preventDefault()
      setEditing(null)
      rootRef.current?.focus()
      return
    }
    if (event.key !== "Enter") {
      return
    }
    if (event.shiftKey) {
      event.preventDefault()
      setEditing(null)
      void runAndAdvance(index)
      return
    }
    if (event.metaKey || event.ctrlKey) {
      event.preventDefault()
      void runAt(index)
    }
  }

  function onNotebookKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    const target: EventTarget | null = event.target
    if (target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement || target instanceof HTMLButtonElement) {
      return
    }
    const index: number = focusedRef.current
    if (event.key !== "d") {
      deleteArmed.current = false
    }
    if (event.key === "Enter" && (event.shiftKey || event.ctrlKey || event.metaKey)) {
      event.preventDefault()
      if (event.shiftKey) {
        void runAndAdvance(index)
      } else {
        void runAt(index)
      }
      return
    }
    if (event.key === "Enter") {
      event.preventDefault()
      setEditing(index)
      return
    }
    if (event.key === "ArrowUp") {
      event.preventDefault()
      setFocused(Math.max(0, index - 1))
      setEditing(null)
      return
    }
    if (event.key === "ArrowDown") {
      event.preventDefault()
      setFocused(Math.min(notebookRef.current.cells.length - 1, index + 1))
      setEditing(null)
      return
    }
    if (event.key === "a") {
      insertCell(index, "code")
      return
    }
    if (event.key === "b") {
      insertCell(index + 1, "code")
      return
    }
    if (event.key === "m") {
      setKind(index, "markdown")
      return
    }
    if (event.key === "y") {
      setKind(index, "code")
      return
    }
    if (event.key === "d") {
      if (deleteArmed.current) {
        deleteArmed.current = false
        remove(index)
      } else {
        deleteArmed.current = true
      }
    }
  }

  const focusedCell: NotebookCell | undefined = notebook.cells[focused]
  const kind: "code" | "markdown" = focusedCell?.cell_type ?? "code"
  const kernelName: string = connection === null ? "No kernel" : phase
  return (
    <div className="notebook-editor" ref={rootRef} tabIndex={0} onKeyDown={onNotebookKeyDown}>
      <div className="notebook-toolbar">
        <button type="button" className="nb-tool" title="Run selected cell (Shift+Enter)" disabled={parseError !== null} onClick={() => void runAt(focused)}>
          Run
        </button>
        <button
          type="button"
          className="nb-tool"
          title="Interrupt kernel"
          disabled={connection === null}
          onClick={() => {
            const current: KernelConnection | null = connectionRef.current
            if (current !== null) {
              void interruptKernel(current.kernelId)
            }
          }}
        >
          Interrupt
        </button>
        <button type="button" className="nb-tool" title="Restart kernel" disabled={connection === null} onClick={() => void onRestart()}>
          Restart
        </button>
        <span className="nb-sep" />
        <select
          className="nb-kind"
          aria-label="Cell type"
          value={kind}
          onChange={(event) => setKind(focused, event.target.value === "markdown" ? "markdown" : "code")}
        >
          <option value="code">Code</option>
          <option value="markdown">Markdown</option>
        </select>
        <button type="button" className="nb-tool" title="Insert cell below (B)" onClick={() => insertCell(focused + 1, "code")}>
          +
        </button>
        <button type="button" className="nb-tool" title="Move cell up" onClick={() => move(focused, -1)}>
          Up
        </button>
        <button type="button" className="nb-tool" title="Move cell down" onClick={() => move(focused, 1)}>
          Down
        </button>
        <button type="button" className="nb-tool" title="Delete cell (D, D)" onClick={() => remove(focused)}>
          Delete
        </button>
        <span className="nb-sep" />
        <button type="button" className="nb-tool" title="Run all cells" disabled={parseError !== null} onClick={() => void runAll()}>
          Run all
        </button>
        <button
          type="button"
          className="nb-tool"
          title="Insert a catalog query"
          onClick={() => {
            void ensureKernel()
              .then(() => {
                setConnectionsOpen((open: boolean) => !open)
              })
              .catch((err: unknown) => {
                setError(err instanceof Error ? err.message : "kernel failed")
              })
          }}
        >
          Connections
        </button>
        <span className={`kernel-status kernel-${connection === null ? "off" : phase}`}>
          <span className="kernel-dot" />
          {kernelName}
        </span>
      </div>
      {connectionsOpen && info !== null ? (
        <div className="notebook-popover">
          <p className="note">Spark catalogs</p>
          {info.spark_catalogs.map((catalog: string) => (
            <span key={catalog} className="notebook-connection">
              <button type="button" className="nb-tool" onClick={() => insertSnippet(sparkTableSnippet(catalog))}>
                spark {catalog}
              </button>
              <button type="button" className="nb-tool" onClick={() => insertSnippet(trinoSnippet(catalog))}>
                trino {catalog}
              </button>
            </span>
          ))}
          <p className="note">Trino engines</p>
          {info.engines.map((engine: string) => (
            <button key={engine} type="button" className="nb-tool" onClick={() => insertSnippet(trinoEngineSnippet(engine))}>
              {engine}
            </button>
          ))}
          {info.warnings.map((warning: string) => (
            <p key={warning} className="note">
              {warning}
            </p>
          ))}
        </div>
      ) : null}
      {parseError !== null ? <p className="error-text">{parseError}</p> : null}
      {error !== null ? <p className="error-text">{error}</p> : null}
      {notebook.cells.length === 0 ? <p className="note">No cells yet.</p> : null}
      <div className="jp-notebook">
        {notebook.cells.map((cell: NotebookCell, index: number) => {
          const selected: boolean = index === focused
          const active: boolean = editing === index
          const cellClass: string = active ? "jp-cell is-editing" : selected ? "jp-cell is-selected" : "jp-cell"
          return (
            <article
              key={cell.id}
              className={cellClass}
              onMouseDown={(event) => {
                if (event.target instanceof HTMLTextAreaElement) {
                  return
                }
                setFocused(index)
                setEditing(null)
              }}
            >
              <div className="jp-cell-input">
                <div className="jp-prompt">
                  {cell.cell_type === "code" ? (runningId === cell.id ? "In [*]" : inputPrompt(cell)) : ""}
                </div>
                {cell.cell_type === "markdown" && !active ? (
                  <MarkdownView
                    source={cell.source}
                    onEdit={() => {
                      setFocused(index)
                      setEditing(index)
                    }}
                  />
                ) : (
                  <textarea
                    className="cell-editor"
                    spellCheck={false}
                    aria-label={cell.cell_type === "code" ? "Code cell" : "Markdown cell"}
                    value={cell.source}
                    onFocus={() => {
                      setFocused(index)
                      setEditing(index)
                    }}
                    onChange={(event) => editCell(index, event.target.value)}
                    onKeyDown={(event) => onEditorKeyDown(event, index)}
                  />
                )}
              </div>
              {cell.cell_type === "code" ? <CellOutputs prompt={outputPrompt(cell)} outputs={cell.outputs} /> : null}
            </article>
          )
        })}
      </div>
    </div>
  )
}

function inputPrompt(cell: CodeCell): string {
  if (cell.execution_count === null) {
    return "In [ ]"
  }
  return `In [${cell.execution_count}]`
}

function outputPrompt(cell: CodeCell): string {
  if (cell.execution_count === null) {
    return ""
  }
  return `Out[${cell.execution_count}]`
}

function MarkdownView(props: { source: string; onEdit: () => void }): ReactElement {
  const lines: string[] = props.source.split("\n")
  return (
    <div className="cell-markdown" onDoubleClick={props.onEdit}>
      {lines.map((line: string, index: number) => {
        if (line.startsWith("### ")) {
          return <h3 key={index}>{line.slice(4)}</h3>
        }
        if (line.startsWith("## ")) {
          return <h2 key={index}>{line.slice(3)}</h2>
        }
        if (line.startsWith("# ")) {
          return <h1 key={index}>{line.slice(2)}</h1>
        }
        return <p key={index}>{line === "" ? "\u00a0" : line}</p>
      })}
    </div>
  )
}

function CellOutputs(props: { prompt: string; outputs: CellOutput[] }): ReactElement {
  if (props.outputs.length === 0) {
    return <></>
  }
  return (
    <div className="jp-cell-output">
      <div className="jp-prompt jp-out">{props.prompt}</div>
      <div className="cell-output">
        {props.outputs.map((output: CellOutput, index: number) => (
          <OutputView key={index} output={output} />
        ))}
      </div>
    </div>
  )
}

function OutputView(props: { output: CellOutput }): ReactElement {
  const output: CellOutput = props.output
  if (output.output_type === "stream") {
    return <pre className={output.name === "stderr" ? "cell-stderr" : "cell-stdout"}>{stripAnsi(output.text)}</pre>
  }
  if (output.output_type === "error") {
    const trace: string = output.traceback.length > 0 ? output.traceback.join("\n") : `${output.ename}: ${output.evalue}`
    return <pre className="cell-stderr">{stripAnsi(trace)}</pre>
  }
  const grid = catalogGrid(output.data)
  if (grid !== null) {
    return <ResultGrid result={grid} />
  }
  const html: string | null = htmlText(output.data)
  if (html !== null) {
    return <iframe className="cell-html" sandbox="" srcDoc={html} title="HTML output" />
  }
  const png: string | null = pngText(output.data)
  if (png !== null) {
    return <img className="cell-image" alt="" src={`data:image/png;base64,${png}`} />
  }
  const text: string = plainText(output.data)
  return text === "" ? <></> : <pre className="cell-stdout">{text}</pre>
}
