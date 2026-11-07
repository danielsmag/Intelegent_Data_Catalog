import { apiFetch, readDetail } from "./http"

export type AssetKind = "AIRFLOW_INSTANCE" | "ICEBERG_TABLE" | "ICEBERG_SCHEMA" | "GITHUB_REPO"
export type AssetStatus = "ACTIVE" | "MISSING"
export type AssetRunStatus = "queued" | "running" | "succeeded" | "failed"
export type AssetRunTrigger = "apply" | "schedule" | "manual"
export type AssetTaskState =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped"
  | "upstream_failed"

export type AssetRecord = {
  id: string
  kind: AssetKind
  key: string
  name: string
  description: string | null
  status: AssetStatus
  source: Record<string, unknown>
  properties: Record<string, unknown>
  schedule: string | null
  next_run_at: string | null
  run_status: AssetRunStatus | null
  ran_at: string | null
  created_at: string
  updated_at: string
}

export type AssetPage = {
  items: AssetRecord[]
  total: number
}

export type AssetRunTaskSummary = {
  task_id: string
  state: AssetTaskState
  started_at: string | null
  finished_at: string | null
  duration: number | null
  error: string | null
}

export type AssetRun = {
  id: string
  asset_id: string
  status: AssetRunStatus
  trigger: AssetRunTrigger
  force: boolean
  try_number: number
  error: string | null
  queued_at: string
  started_at: string | null
  finished_at: string | null
  duration: number | null
  tasks: AssetRunTaskSummary[]
}

export type AssetRunTaskDetail = {
  run_id: string
  task_id: string
  state: AssetTaskState
  error: string | null
  log: string
  trigger: AssetRunTrigger
  run_status: AssetRunStatus
  run_error: string | null
}

export type AssetOutput = {
  id: string
  asset_id: string
  run_id: string
  task_id: string
  name: string
  size_bytes: number
  created_at: string
}

export type AssetOutputDetail = AssetOutput & {
  markdown: string
  data: unknown
}

export type AssetSort = "name" | "kind" | "updated"

export type AssetQuery = {
  q?: string
  kind?: AssetKind
  sort?: AssetSort
  limit?: number
  offset?: number
}

export async function fetchAssets(query: AssetQuery = {}, signal?: AbortSignal): Promise<AssetPage> {
  const params: URLSearchParams = new URLSearchParams()
  const text: string = query.q?.trim() ?? ""
  if (text !== "") {
    params.set("q", text)
  }
  if (query.kind !== undefined) {
    params.set("kind", query.kind)
  }
  if (query.sort !== undefined && query.sort !== "name") {
    params.set("sort", query.sort)
  }
  params.set("limit", String(query.limit ?? 100))
  params.set("offset", String(query.offset ?? 0))
  const response: Response = await apiFetch(`/api/v1/assets?${params.toString()}`, { signal })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as AssetPage
}

export async function fetchAsset(assetId: string, signal?: AbortSignal): Promise<AssetRecord> {
  const response: Response = await apiFetch(`/api/v1/assets/${assetId}`, { signal })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as AssetRecord
}

export async function runAsset(assetId: string, force: boolean): Promise<void> {
  const response: Response = await apiFetch(`/api/v1/assets/${assetId}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force }),
  })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
}

export async function fetchAssetRuns(assetId: string, signal?: AbortSignal): Promise<AssetRun[]> {
  const response: Response = await apiFetch(`/api/v1/assets/${assetId}/runs?limit=50`, { signal })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as AssetRun[]
}

export async function fetchAssetRunTask(
  assetId: string,
  runId: string,
  taskId: string,
  signal?: AbortSignal,
): Promise<AssetRunTaskDetail> {
  const path: string = `/api/v1/assets/${assetId}/runs/${runId}/tasks/${encodeURIComponent(taskId)}`
  const response: Response = await apiFetch(path, { signal })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as AssetRunTaskDetail
}

export async function fetchAssetOutputs(assetId: string, signal?: AbortSignal): Promise<AssetOutput[]> {
  const response: Response = await apiFetch(`/api/v1/assets/${assetId}/outputs`, { signal })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as AssetOutput[]
}

export async function fetchAssetOutput(
  assetId: string,
  outputId: string,
  signal?: AbortSignal,
): Promise<AssetOutputDetail> {
  const response: Response = await apiFetch(`/api/v1/assets/${assetId}/outputs/${outputId}`, { signal })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as AssetOutputDetail
}
