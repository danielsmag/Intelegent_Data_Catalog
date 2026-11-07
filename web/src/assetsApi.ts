import { apiFetch, readDetail } from "./http"

export type AssetKind = "TABLE" | "AIRFLOW_DAG" | "AIRFLOW_INSTANCE" | "NOTEBOOK" | "FILE"
export type AssetStatus = "ACTIVE" | "MISSING" | "DEPRECATED"
export type RefreshMode = "INTERVAL" | "ON_CHANGE" | "MANUAL"
export type ContextState = "none" | "user" | "llm"
export type AssetSort =
  | "name"
  | "kind"
  | "updated_at"
  | "last_built_at"
  | "freshness_score"
  | "context_state"

export type AssetRecord = {
  id: string
  kind: AssetKind
  name: string
  slug: string
  description: string | null
  context: string | null
  context_origin: "USER" | "LLM" | null
  workspace_id: string
  owner_user_id: string | null
  status: AssetStatus
  properties: Record<string, unknown>
  tags: string[]
  detail: Record<string, unknown>
  created_at: string
  updated_at: string
  last_synced_at: string | null
}

export type AssetListItem = {
  id: string
  kind: AssetKind
  name: string
  slug: string
  description: string | null
  workspace_id: string
  owner_user_id: string | null
  status: AssetStatus
  subtitle: string | null
  table_format: string | null
  last_built_at: string | null
  context_state: ContextState
  freshness_score: number
  build_running: boolean
  refresh_mode: RefreshMode
  interval_seconds: number
  schedule: string | null
  next_run_at: string | null
  updated_at: string
}

export type AssetPage = {
  items: AssetListItem[]
  total: number
}

export type AssetQuery = {
  q?: string
  kinds?: AssetKind[]
  formats?: string[]
  owner?: "me" | "shared" | "all"
  workspaceId?: string | null
  status?: AssetStatus
  hasContext?: boolean
  refreshMode?: RefreshMode
  sort?: AssetSort
  order?: "asc" | "desc"
  limit?: number
  offset?: number
}

export type AssetBuildJob = {
  id: string
  asset_id: string
  asset_name: string
  status: "running" | "succeeded" | "failed"
  error: string | null
}

export type AssetRunStatus = "queued" | "running" | "succeeded" | "failed"
export type AssetRunTrigger = "apply" | "schedule" | "manual"
export type AssetRunTaskState =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped"
  | "upstream_failed"

export type AssetRunTaskSummary = {
  task_id: string
  state: AssetRunTaskState
  started_at: string | null
  finished_at: string | null
  duration: number | null
  error: string | null
}

export type AssetRun = {
  id: string
  asset_id: string
  item_id: string | null
  catalog_job_id: string | null
  status: AssetRunStatus
  trigger: AssetRunTrigger
  try_number: number
  operator_type: string | null
  operator_params: Record<string, unknown>
  error: string | null
  queued_at: string
  started_at: string | null
  finished_at: string | null
  duration: number | null
  tasks: AssetRunTaskSummary[]
}

export type AssetRunEvent = {
  at: string
  step: string
  title: string
  detail: string
}

export type AssetRunTaskDetail = {
  run_id: string
  task_id: string
  state: AssetRunTaskState
  started_at: string | null
  finished_at: string | null
  duration: number | null
  error: string | null
  log: string
  events: AssetRunEvent[]
  trigger: AssetRunTrigger
  try_number: number
  operator_type: string | null
  operator_params: Record<string, unknown>
  run_status: AssetRunStatus
  run_error: string | null
}

export async function searchAssets(query: AssetQuery, signal?: AbortSignal): Promise<AssetPage> {
  const params: URLSearchParams = new URLSearchParams()
  if (query.q !== undefined && query.q !== "") {
    params.set("q", query.q)
  }
  for (const kind of query.kinds ?? []) {
    params.append("kinds", kind)
  }
  for (const format of query.formats ?? []) {
    params.append("formats", format)
  }
  if (query.owner !== undefined) {
    params.set("owner", query.owner)
  }
  if (query.workspaceId !== undefined && query.workspaceId !== null) {
    params.set("workspace_id", query.workspaceId)
  }
  if (query.status !== undefined) {
    params.set("status", query.status)
  }
  if (query.hasContext !== undefined) {
    params.set("has_context", query.hasContext ? "true" : "false")
  }
  if (query.refreshMode !== undefined) {
    params.set("refresh_mode", query.refreshMode)
  }
  if (query.sort !== undefined) {
    params.set("sort", query.sort)
  }
  if (query.order !== undefined) {
    params.set("order", query.order)
  }
  params.set("limit", String(query.limit ?? 100))
  params.set("offset", String(query.offset ?? 0))
  const response: Response = await apiFetch(`/api/v1/assets?${params.toString()}`, { signal })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as AssetPage
}

export async function updateAssetStatus(assetId: string, status: AssetStatus): Promise<AssetRecord> {
  const response: Response = await apiFetch(`/api/v1/assets/${assetId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as AssetRecord
}

export async function fetchAsset(assetId: string, signal?: AbortSignal): Promise<AssetRecord> {
  const response: Response = await apiFetch(`/api/v1/assets/${assetId}`, { signal })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as AssetRecord
}

export async function startAssetBuild(assetId: string): Promise<AssetBuildJob> {
  const response: Response = await apiFetch(`/api/v1/assets/${assetId}/knowledge-context`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as AssetBuildJob
}

export async function fetchAssetBuild(jobId: string): Promise<AssetBuildJob> {
  const response: Response = await apiFetch(`/api/v1/asset-builds/${jobId}`)
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as AssetBuildJob
}

export async function fetchAssetRuns(assetId: string, signal?: AbortSignal): Promise<AssetRun[]> {
  const response: Response = await apiFetch(`/api/v1/assets/${assetId}/runs?limit=25`, { signal })
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
  const response: Response = await apiFetch(
    `/api/v1/assets/${assetId}/runs/${runId}/tasks/${taskId}`,
    { signal },
  )
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as AssetRunTaskDetail
}

export function detailString(detail: Record<string, unknown>, key: string): string | null {
  const value: unknown = detail[key]
  return typeof value === "string" ? value : null
}
