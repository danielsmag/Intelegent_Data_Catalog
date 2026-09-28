import type { CatalogTree, QueryResult, TableDetail } from "./types"

export async function fetchTree(signal: AbortSignal): Promise<CatalogTree> {
  const response: Response = await fetch("/api/v1/tree", { signal })
  if (!response.ok) {
    const detail: string = await readDetail(response)
    throw new Error(detail)
  }
  return (await response.json()) as CatalogTree
}

export async function fetchTable(
  catalog: string,
  namespace: string,
  table: string,
  signal: AbortSignal,
): Promise<TableDetail> {
  const path: string = `/api/v1/tables/${encodeURIComponent(catalog)}/${encodeURIComponent(namespace)}/${encodeURIComponent(table)}`
  const response: Response = await fetch(path, { signal })
  if (!response.ok) {
    const detail: string = await readDetail(response)
    throw new Error(detail)
  }
  return (await response.json()) as TableDetail
}

export async function fetchSample(
  catalog: string,
  namespace: string,
  table: string,
  signal: AbortSignal,
): Promise<QueryResult> {
  const path: string = `/api/v1/tables/${encodeURIComponent(catalog)}/${encodeURIComponent(namespace)}/${encodeURIComponent(table)}/sample`
  const response: Response = await fetch(path, { signal })
  if (!response.ok) {
    const detail: string = await readDetail(response)
    throw new Error(detail)
  }
  return (await response.json()) as QueryResult
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

export function queryPagePath(catalog: string, namespace: string, table: string): string {
  const sql: string = `SELECT * FROM ${catalog}.${namespace}.${table} LIMIT 100`
  const params: URLSearchParams = new URLSearchParams({ sql })
  return `/query?${params.toString()}`
}

export function tablePath(catalog: string, namespace: string, table: string): string {
  return `/catalog/${encodeURIComponent(catalog)}/${encodeURIComponent(namespace)}/${encodeURIComponent(table)}`
}

export function formatTimestamp(lastUpdatedMs: number | null): string {
  if (lastUpdatedMs === null) {
    return "Unknown"
  }
  return new Date(lastUpdatedMs).toLocaleString()
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
