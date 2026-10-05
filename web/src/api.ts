import type { ExplorerTree, QueryResult, TableDetail } from "./types"

export async function fetchExplorerTree(signal: AbortSignal): Promise<ExplorerTree> {
  return readJson<ExplorerTree>("/api/v1/explorer/tree", signal)
}

export async function fetchExplorerTable(
  source: string,
  catalog: string,
  namespace: string,
  table: string,
  signal: AbortSignal,
): Promise<TableDetail> {
  return readJson<TableDetail>(explorerTablePath(source, catalog, namespace, table), signal)
}

export async function fetchExplorerSample(
  source: string,
  catalog: string,
  namespace: string,
  table: string,
  signal: AbortSignal,
): Promise<QueryResult> {
  return readJson<QueryResult>(`${explorerTablePath(source, catalog, namespace, table)}/sample`, signal)
}

export function catalogTablePath(source: string, catalog: string, namespace: string, table: string): string {
  return `/catalog/${encodeURIComponent(source)}/${encodeURIComponent(catalog)}/${encodeURIComponent(namespace)}/${encodeURIComponent(table)}`
}

export function queryPagePath(catalog: string, namespace: string, table: string): string {
  const sql: string = `SELECT * FROM ${catalog}.${namespace}.${table} LIMIT 100`
  const params: URLSearchParams = new URLSearchParams({ sql })
  return `/query?${params.toString()}`
}

export function formatTimestamp(lastUpdatedMs: number | null): string {
  if (lastUpdatedMs === null) {
    return "Unknown"
  }
  return new Date(lastUpdatedMs).toLocaleString()
}

async function readJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response: Response = await fetch(path, { signal })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as T
}

function explorerTablePath(source: string, catalog: string, namespace: string, table: string): string {
  return (
    `/api/v1/explorer/sources/${encodeURIComponent(source)}` +
    `/tables/${encodeURIComponent(catalog)}/${encodeURIComponent(namespace)}/${encodeURIComponent(table)}`
  )
}

export async function runQuery(sql: string, signal: AbortSignal): Promise<QueryResult> {
  const response: Response = await fetch("/api/v1/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sql }),
    signal,
  })
  if (!response.ok) {
    const detail: string = await readDetail(response)
    throw new Error(detail)
  }
  return (await response.json()) as QueryResult
}

export function isAbortError(err: unknown): boolean {
  return err instanceof Error && err.name === "AbortError"
}

export function errorMessage(err: unknown): string {
  if (err instanceof Error && err.message !== "") {
    return err.message
  }
  return "Request failed"
}

async function readDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (isDetailBody(body)) {
      return body.detail
    }
  } catch {
    return `Request failed (${response.status})`
  }
  return `Request failed (${response.status})`
}

function isDetailBody(body: unknown): body is { detail: string } {
  if (typeof body !== "object" || body === null || !("detail" in body)) {
    return false
  }
  const detail: unknown = body.detail
  return typeof detail === "string" && detail !== ""
}
