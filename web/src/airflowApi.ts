export type AirflowInstanceStatus = "ACTIVE" | "DISABLED"
export type AirflowMappingOrigin = "ASSET" | "SOURCE_MATCH" | "MANUAL" | "LLM"
export type AirflowMappingDirection = "INPUT" | "OUTPUT" | "UNKNOWN"
export type AirflowSyncStatus = "RUNNING" | "SUCCEEDED" | "PARTIAL" | "FAILED"

export type AirflowInstance = {
  id: string
  name: string
  slug: string
  base_url: string
  username: string
  verify_tls: boolean
  description: string | null
  context: string | null
  context_origin: "USER" | "LLM" | null
  status: AirflowInstanceStatus
  has_credentials: boolean
  dag_count: number
  last_synced_at: string | null
  last_error: string | null
  created_at: string
  updated_at: string
}

export type AirflowInstanceInput = {
  name: string
  base_url: string
  username: string
  password: string
  verify_tls: boolean
  description: string | null
}

export type AirflowInstancePatch = {
  name?: string
  base_url?: string
  username?: string
  password?: string
  verify_tls?: boolean
  description?: string | null
  context?: string | null
  status?: AirflowInstanceStatus
}

export type AirflowHealth = {
  metadatabase: string | null
  scheduler: string | null
  triggerer: string | null
  dag_processor: string | null
}

export type AirflowConnectionTest = {
  ok: boolean
  version: string | null
  health: AirflowHealth | null
  error: string | null
}

export type AirflowPool = {
  name: string
  slots: number
  open_slots: number
  description: string | null
}

export type AirflowProvider = {
  package_name: string
  version: string
}

export type AirflowPlugin = {
  name: string
  source: string | null
}

export type AirflowImportError = {
  filename: string
  timestamp: string | null
  bundle_name: string | null
  stack_trace: string | null
}

export type AirflowOverview = {
  version: string | null
  version_error: string | null
  health: AirflowHealth | null
  health_error: string | null
  pools: AirflowPool[] | null
  pools_error: string | null
  providers: AirflowProvider[] | null
  providers_error: string | null
  plugins: AirflowPlugin[] | null
  plugins_error: string | null
  import_errors: AirflowImportError[] | null
  import_errors_error: string | null
}

export type AirflowTask = {
  task_id: string
  operator_name: string | null
  downstream_task_ids: string[]
}

export type AirflowLastRun = {
  dag_run_id: string
  state: string | null
  start_date: string | null
  end_date: string | null
  run_type: string | null
}

export type AirflowRunStat = {
  state: string
  count: number
}

export type AirflowDagTable = {
  id: string
  dag_row_id: string
  dag_id: string
  catalog_table_id: string
  table_name: string
  qualified_name: string
  origin: AirflowMappingOrigin
  direction: AirflowMappingDirection
  evidence: string | null
}

export type AirflowDag = {
  id: string
  instance_id: string
  dag_id: string
  display_name: string | null
  description: string | null
  is_paused: boolean
  is_stale: boolean
  has_import_errors: boolean
  timetable_summary: string | null
  timetable_description: string | null
  owners: string[]
  tags: string[]
  fileloc: string | null
  bundle_name: string | null
  max_active_runs: number | null
  next_dagrun_run_after: string | null
  last_parsed_time: string | null
  last_run: AirflowLastRun | null
  run_stats: AirflowRunStat[]
  tables: AirflowDagTable[]
  last_seen_at: string
}

export type AirflowDagDetail = AirflowDag & {
  tasks: AirflowTask[]
  source_code: string | null
  source_version: number | null
}

export type AirflowDagRun = {
  dag_run_id: string
  state: string | null
  run_type: string | null
  start_date: string | null
  end_date: string | null
  logical_date: string | null
  duration: number | null
}

export type AirflowSyncRun = {
  id: string
  instance_id: string
  started_at: string
  finished_at: string | null
  status: AirflowSyncStatus
  counts: {
    dags_seen: number
    dags_removed: number
    mappings: number
    dag_errors: number
  }
  sanitized_error: string | null
}

export type AirflowTree = {
  instance: AirflowInstance
  dags: AirflowDag[]
}

export async function fetchAirflowInstances(signal: AbortSignal): Promise<AirflowInstance[]> {
  return readJson<AirflowInstance[]>("/api/v1/airflow/instances", signal)
}

export async function fetchAirflowInstance(instanceId: string, signal: AbortSignal): Promise<AirflowInstance> {
  return readJson<AirflowInstance>(`/api/v1/airflow/instances/${instanceId}`, signal)
}

export async function createAirflowInstance(body: AirflowInstanceInput): Promise<AirflowInstance> {
  return sendJson<AirflowInstance>("/api/v1/airflow/instances", "POST", body)
}

export async function updateAirflowInstance(
  instanceId: string,
  body: AirflowInstancePatch,
): Promise<AirflowInstance> {
  return sendJson<AirflowInstance>(`/api/v1/airflow/instances/${instanceId}`, "PATCH", body)
}

export async function deleteAirflowInstance(instanceId: string): Promise<void> {
  await sendEmpty(`/api/v1/airflow/instances/${instanceId}`, "DELETE")
}

export async function testAirflowInstance(body: AirflowInstanceInput): Promise<AirflowConnectionTest> {
  return sendJson<AirflowConnectionTest>("/api/v1/airflow/instances/test", "POST", {
    base_url: body.base_url,
    username: body.username,
    password: body.password,
    verify_tls: body.verify_tls,
  })
}

export async function syncAirflowInstance(instanceId: string): Promise<AirflowSyncRun> {
  return sendJson<AirflowSyncRun>(`/api/v1/airflow/instances/${instanceId}/sync`, "POST", {})
}

export type SyncSchedule = {
  id: string
  source_engine_binding_id: string | null
  airflow_instance_id: string | null
  interval_seconds: number
  enabled: boolean
  next_run_at: string
}

export async function fetchAirflowSchedule(
  instanceId: string,
  signal: AbortSignal,
): Promise<SyncSchedule | null> {
  const response: Response = await fetch(`/api/v1/airflow/instances/${instanceId}/schedule`, { signal })
  if (response.status === 404) {
    return null
  }
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as SyncSchedule
}

export async function saveAirflowSchedule(
  instanceId: string,
  intervalSeconds: number,
  enabled: boolean,
): Promise<SyncSchedule> {
  return sendJson<SyncSchedule>(`/api/v1/airflow/instances/${instanceId}/schedule`, "PUT", {
    interval_seconds: intervalSeconds,
    enabled,
  })
}

export async function fetchAirflowOverview(instanceId: string, signal: AbortSignal): Promise<AirflowOverview> {
  return readJson<AirflowOverview>(`/api/v1/airflow/instances/${instanceId}/overview`, signal)
}

export async function fetchAirflowDags(instanceId: string, signal: AbortSignal): Promise<AirflowDag[]> {
  return readJson<AirflowDag[]>(`/api/v1/airflow/instances/${instanceId}/dags`, signal)
}

export async function fetchAirflowDag(
  instanceId: string,
  dagId: string,
  signal: AbortSignal,
): Promise<AirflowDagDetail> {
  return readJson<AirflowDagDetail>(dagPath(instanceId, dagId), signal)
}

export async function fetchAirflowDagRuns(
  instanceId: string,
  dagId: string,
  signal: AbortSignal,
): Promise<AirflowDagRun[]> {
  return readJson<AirflowDagRun[]>(`${dagPath(instanceId, dagId)}/runs?limit=25`, signal)
}

export async function addDagTable(
  instanceId: string,
  dagId: string,
  catalogTableId: string,
  direction: AirflowMappingDirection,
): Promise<AirflowDagTable> {
  return sendJson<AirflowDagTable>(`${dagPath(instanceId, dagId)}/tables`, "POST", {
    catalog_table_id: catalogTableId,
    direction,
  })
}

export async function removeDagTable(instanceId: string, dagId: string, tableId: string): Promise<void> {
  await sendEmpty(`${dagPath(instanceId, dagId)}/tables/${tableId}`, "DELETE")
}

export type AirflowAttachmentTarget = "space" | "scope"

export type AirflowAttachment = {
  id: string
  target_kind: AirflowAttachmentTarget
  target_id: string
  instance_id: string
  instance_name: string
  instance_slug: string
  dag_row_id: string | null
  dag_id: string | null
  dag_display_name: string | null
  created_at: string
}

export type AirflowAttachedDag = {
  attachment_id: string
  dag_row_id: string
  instance_id: string
  instance_name: string
  dag_id: string
  display_name: string | null
  description: string | null
  timetable_summary: string | null
  is_paused: boolean
  last_run: AirflowLastRun | null
  table_count: number
  via: "instance" | "dag"
  summary: string | null
}

export type AirflowDagLink = {
  dag_row_id: string
  table_id: string
  direction: AirflowMappingDirection
  reason: string
}

export type AirflowDagFinding = {
  dag_row_id: string
  dag_id: string
  summary: string
  links: AirflowDagLink[]
}

export type AirflowKnowledgeContext = {
  instance_id: string
  instance_name: string
  summary: string | null
  findings: AirflowDagFinding[]
  dags_analyzed: number
  previous_links_kept: number
}

export type AirflowBuildJob = {
  id: string
  instance_id: string
  instance_name: string
  status: "running" | "succeeded" | "failed"
  error: string | null
}

export type AirflowBuildOptions = {
  include_source: boolean
  include_previous: boolean
  include_summary: boolean
}

export async function fetchAirflowAttachments(
  kind: AirflowAttachmentTarget,
  targetId: string,
  signal: AbortSignal,
): Promise<AirflowAttachment[]> {
  return readJson<AirflowAttachment[]>(`/api/v1/${kind}s/${targetId}/airflow`, signal)
}

export async function attachAirflow(
  kind: AirflowAttachmentTarget,
  targetId: string,
  body: { instance_id: string; dag_id: string | null },
): Promise<AirflowAttachment> {
  return sendJson<AirflowAttachment>(`/api/v1/${kind}s/${targetId}/airflow`, "POST", body)
}

export async function detachAirflow(
  kind: AirflowAttachmentTarget,
  targetId: string,
  attachmentId: string,
): Promise<void> {
  await sendEmpty(`/api/v1/${kind}s/${targetId}/airflow/${attachmentId}`, "DELETE")
}

export async function fetchAttachedDags(
  kind: AirflowAttachmentTarget,
  targetId: string,
  signal: AbortSignal,
): Promise<AirflowAttachedDag[]> {
  return readJson<AirflowAttachedDag[]>(`/api/v1/${kind}s/${targetId}/airflow/dags`, signal)
}

export async function fetchAirflowKnowledgeContext(
  instanceId: string,
  signal: AbortSignal,
): Promise<AirflowKnowledgeContext | null> {
  const response: Response = await fetch(`/api/v1/airflow/instances/${instanceId}/knowledge-context`, {
    signal,
  })
  if (response.status === 404) {
    return null
  }
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as AirflowKnowledgeContext
}

export async function startAirflowBuild(
  instanceId: string,
  options: AirflowBuildOptions,
): Promise<AirflowBuildJob> {
  return sendJson<AirflowBuildJob>(`/api/v1/airflow/instances/${instanceId}/knowledge-context`, "POST", options)
}

export async function fetchAirflowBuild(jobId: string): Promise<AirflowBuildJob> {
  return readJson<AirflowBuildJob>(`/api/v1/airflow-builds/${jobId}`, new AbortController().signal)
}

export async function fetchLatestAirflowBuild(
  instanceId: string,
  signal: AbortSignal,
): Promise<AirflowBuildJob | null> {
  const response: Response = await fetch(
    `/api/v1/airflow/instances/${instanceId}/knowledge-context/build`,
    { signal },
  )
  if (response.status === 404) {
    return null
  }
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as AirflowBuildJob
}

export async function fetchAirflowTree(signal: AbortSignal): Promise<AirflowTree[]> {
  const instances: AirflowInstance[] = await fetchAirflowInstances(signal)
  return Promise.all(
    instances.map(async (instance: AirflowInstance): Promise<AirflowTree> => {
      const dags: AirflowDag[] = await fetchAirflowDags(instance.id, signal)
      return { instance, dags }
    }),
  )
}

function dagPath(instanceId: string, dagId: string): string {
  return `/api/v1/airflow/instances/${instanceId}/dags/${encodeURIComponent(dagId)}`
}

async function readJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response: Response = await fetch(path, { signal })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as T
}

async function sendJson<T>(path: string, method: string, body: unknown): Promise<T> {
  const response: Response = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as T
}

async function sendEmpty(path: string, method: string): Promise<void> {
  const response: Response = await fetch(path, { method })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
}

async function readDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (typeof body === "object" && body !== null && "detail" in body) {
      const detail: unknown = body.detail
      if (typeof detail === "string" && detail !== "") {
        return detail
      }
    }
  } catch {
    return `Request failed (${response.status})`
  }
  return `Request failed (${response.status})`
}
