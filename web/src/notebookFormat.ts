import type { QueryCell, QueryResult } from "./types"

export type StreamOutput = {
  output_type: "stream"
  name: string
  text: string
}

export type DisplayOutput = {
  output_type: "execute_result" | "display_data"
  execution_count: number | null
  data: Record<string, unknown>
  metadata: Record<string, unknown>
}

export type ErrorOutput = {
  output_type: "error"
  ename: string
  evalue: string
  traceback: string[]
}

export type CellOutput = StreamOutput | DisplayOutput | ErrorOutput

export type CodeCell = {
  id: string
  cell_type: "code"
  source: string
  metadata: Record<string, unknown>
  execution_count: number | null
  outputs: CellOutput[]
}

export type MarkdownCell = {
  id: string
  cell_type: "markdown"
  source: string
  metadata: Record<string, unknown>
}

export type NotebookCell = CodeCell | MarkdownCell

export type NotebookDocument = {
  nbformat: 4
  nbformat_minor: 5
  metadata: Record<string, unknown>
  cells: NotebookCell[]
}

export type ParsedNotebook = {
  notebook: NotebookDocument
  error: string | null
}

const IDENTIFIER: RegExp = /^[A-Za-z_][A-Za-z0-9_]*$/

export function parseNotebook(source: string): ParsedNotebook {
  if (source.trim() === "") {
    return { notebook: emptyNotebook(), error: null }
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(source)
  } catch {
    return { notebook: emptyNotebook(), error: "Notebook is not valid JSON" }
  }
  if (!isRecord(parsed) || !Array.isArray(parsed.cells)) {
    return { notebook: emptyNotebook(), error: "Notebook has no cells array" }
  }
  const cells: NotebookCell[] = []
  parsed.cells.forEach((cell: unknown, index: number) => {
    const read: NotebookCell | null = readCell(cell, index)
    if (read !== null) {
      cells.push(read)
    }
  })
  const metadata: Record<string, unknown> = isRecord(parsed.metadata) ? parsed.metadata : {}
  return {
    notebook: { nbformat: 4, nbformat_minor: 5, metadata, cells },
    error: null,
  }
}

export function serializeNotebook(notebook: NotebookDocument): string {
  const cells: object[] = notebook.cells.map((cell: NotebookCell) => {
    if (cell.cell_type === "markdown") {
      return {
        id: cell.id,
        cell_type: "markdown",
        metadata: cell.metadata,
        source: splitSource(cell.source),
      }
    }
    return {
      id: cell.id,
      cell_type: "code",
      metadata: cell.metadata,
      execution_count: cell.execution_count,
      source: splitSource(cell.source),
      outputs: cell.outputs.map(serializeOutput),
    }
  })
  return JSON.stringify(
    {
      cells,
      metadata: notebook.metadata,
      nbformat: notebook.nbformat,
      nbformat_minor: notebook.nbformat_minor,
    },
    null,
    1,
  )
}

export function newCell(kind: "code" | "markdown"): NotebookCell {
  const id: string = crypto.randomUUID()
  if (kind === "markdown") {
    return { id, cell_type: "markdown", source: "", metadata: {} }
  }
  return {
    id,
    cell_type: "code",
    source: "",
    metadata: {},
    execution_count: null,
    outputs: [],
  }
}

export function sparkTableSnippet(catalog: string): string {
  return `spark.table("${sparkIdent(catalog)}.<schema>.<table>").limit(100)`
}

export function trinoSnippet(catalog: string): string {
  return `trino_pandas("SELECT * FROM ${sparkIdent(catalog)}.<schema>.<table> LIMIT 100")`
}

export function trinoEngineSnippet(engine: string): string {
  return `trino_pandas("SELECT 1", engine=${JSON.stringify(engine)})`
}

export function catalogGrid(data: Record<string, unknown>): QueryResult | null {
  const raw: unknown = data["application/vnd.catalog.table+json"]
  let parsed: unknown = raw
  if (typeof raw === "string") {
    try {
      parsed = JSON.parse(raw)
    } catch {
      return null
    }
  }
  if (!isRecord(parsed) || !Array.isArray(parsed.columns) || !Array.isArray(parsed.rows)) {
    return null
  }
  const columns: { name: string; type: string }[] = []
  for (const column of parsed.columns) {
    if (!isRecord(column) || typeof column.name !== "string") {
      return null
    }
    const typeName: string = typeof column.type === "string" ? column.type : "string"
    columns.push({ name: column.name, type: typeName })
  }
  const rows: QueryCell[][] = []
  for (const row of parsed.rows) {
    if (!Array.isArray(row)) {
      return null
    }
    rows.push(row.map((cell: unknown) => asQueryCell(cell)))
  }
  return { columns, rows, truncated: parsed.truncated === true }
}

export function plainText(data: Record<string, unknown>): string {
  return joinText(data["text/plain"])
}

export function htmlText(data: Record<string, unknown>): string | null {
  const html: unknown = data["text/html"]
  return typeof html === "string" ? html : null
}

export function pngText(data: Record<string, unknown>): string | null {
  const png: unknown = data["image/png"]
  return typeof png === "string" ? png : null
}

export function stripAnsi(text: string): string {
  return text.replace(/\u001b\[[0-9;]*m/g, "")
}

function emptyNotebook(): NotebookDocument {
  return { nbformat: 4, nbformat_minor: 5, metadata: {}, cells: [] }
}

function readCell(cell: unknown, index: number): NotebookCell | null {
  if (!isRecord(cell) || typeof cell.cell_type !== "string") {
    return null
  }
  const id: string = typeof cell.id === "string" && cell.id !== "" ? cell.id : `cell-${index}`
  const metadata: Record<string, unknown> = isRecord(cell.metadata) ? cell.metadata : {}
  const source: string = joinText(cell.source)
  if (cell.cell_type === "markdown") {
    return { id, cell_type: "markdown", source, metadata }
  }
  if (cell.cell_type !== "code") {
    return null
  }
  const execution: number | null = typeof cell.execution_count === "number" ? cell.execution_count : null
  const outputs: CellOutput[] = []
  if (Array.isArray(cell.outputs)) {
    for (const output of cell.outputs) {
      const read: CellOutput | null = readOutput(output)
      if (read !== null) {
        outputs.push(read)
      }
    }
  }
  return { id, cell_type: "code", source, metadata, execution_count: execution, outputs }
}

function readOutput(output: unknown): CellOutput | null {
  if (!isRecord(output) || typeof output.output_type !== "string") {
    return null
  }
  if (output.output_type === "stream") {
    return {
      output_type: "stream",
      name: output.name === "stderr" ? "stderr" : "stdout",
      text: joinText(output.text),
    }
  }
  if (output.output_type === "error") {
    const traceback: string[] = []
    if (Array.isArray(output.traceback)) {
      for (const line of output.traceback) {
        if (typeof line === "string") {
          traceback.push(line)
        }
      }
    }
    return {
      output_type: "error",
      ename: typeof output.ename === "string" ? output.ename : "Error",
      evalue: typeof output.evalue === "string" ? output.evalue : "",
      traceback,
    }
  }
  if (output.output_type === "execute_result" || output.output_type === "display_data") {
    return {
      output_type: output.output_type,
      execution_count: typeof output.execution_count === "number" ? output.execution_count : null,
      data: isRecord(output.data) ? output.data : {},
      metadata: isRecord(output.metadata) ? output.metadata : {},
    }
  }
  return null
}

function serializeOutput(output: CellOutput): object {
  if (output.output_type === "stream") {
    return { output_type: "stream", name: output.name, text: splitSource(output.text) }
  }
  if (output.output_type === "error") {
    return {
      output_type: "error",
      ename: output.ename,
      evalue: output.evalue,
      traceback: output.traceback,
    }
  }
  return {
    output_type: output.output_type,
    execution_count: output.execution_count,
    data: output.data,
    metadata: output.metadata,
  }
}

function splitSource(source: string): string[] {
  if (source === "") {
    return []
  }
  const lines: string[] = source.split("\n")
  const parts: string[] = []
  lines.forEach((line: string, index: number) => {
    if (index < lines.length - 1) {
      parts.push(`${line}\n`)
    } else if (line !== "") {
      parts.push(line)
    }
  })
  return parts
}

function joinText(value: unknown): string {
  if (typeof value === "string") {
    return value
  }
  if (!Array.isArray(value)) {
    return ""
  }
  return value.filter((part: unknown): part is string => typeof part === "string").join("")
}

function sparkIdent(identifier: string): string {
  if (IDENTIFIER.test(identifier)) {
    return identifier
  }
  return `\`${identifier.replaceAll("`", "``")}\``
}

function asQueryCell(value: unknown): QueryCell {
  if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return value
  }
  if (Array.isArray(value)) {
    return value.map((item: unknown) => asQueryCell(item))
  }
  if (isRecord(value)) {
    const record: { [key: string]: QueryCell } = {}
    for (const [key, item] of Object.entries(value)) {
      record[key] = asQueryCell(item)
    }
    return record
  }
  return String(value)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}
