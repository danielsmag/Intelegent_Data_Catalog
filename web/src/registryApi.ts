import { apiFetch } from "./http"
import { isAbortError } from "./api"


export type CatalogTableRecord = {
  id: string
  asset_id?: string | null
  data_source_id: string
  name: string
  display_name: string | null
  description: string | null
  status: string
  source_key: string
  source_description: string | null
  context: string | null
  context_origin: "USER" | "LLM" | null
  physical_catalog: string
  physical_namespace: string
  physical_table: string
  table_format: string | null
  table_type: string
  last_seen_at: string | null
  last_synced_at: string | null
}

export type CatalogColumnRecord = {
  id: string
  name: string
  data_type: string | null
  discovered_data_type: string | null
  nullable: boolean | null
  is_primary_key: boolean | null
  description: string | null
  context: string | null
  context_origin: "USER" | "LLM" | null
  last_seen_at: string | null
}

export type ColumnMappingRecord = {
  source_column_name: string
  target_column_name: string
  position: number
}

export type RelationshipRecord = {
  id: string
  relationship_type: string
  origin: string
  status: string
  column_mappings: ColumnMappingRecord[]
}

export type TableRelationships = {
  incoming: RelationshipRecord[]
  outgoing: RelationshipRecord[]
}

export type SparkQueryAvailability = {
  available: boolean
  message: string | null
  sql: string | null
  engine_ref_key: string | null
}

export type GraphNodeRecord = {
  id: string
  kind: string
  label: string
  catalog_id: string | null
  resolved: boolean
}

export type GraphEdgeRecord = {
  source: string
  target: string
  kind: string
  style: string
  reason?: string | null
}

export type GraphViewRecord = {
  nodes: GraphNodeRecord[]
  edges: GraphEdgeRecord[]
  truncated: boolean
}

export type SearchHitRecord = {
  kind: string
  id: string
  name: string
  qualified_name: string
  description: string | null
}

export type AssetBindingRecord = {
  id: string
  role: string
  engine_type: string
  engine_ref_key: string
  state: string
  is_selected: boolean
}

export type TagRecord = {
  id: string
  name: string
  origin: string | null
}


export async function fetchCatalogTable(
  tableId: string,
  signal: AbortSignal,
): Promise<CatalogTableRecord> {
  return readJson<CatalogTableRecord>(`/api/v1/tables/${tableId}`, signal)
}

export async function fetchCatalogColumns(
  tableId: string,
  signal: AbortSignal,
): Promise<CatalogColumnRecord[]> {
  return readJson<CatalogColumnRecord[]>(`/api/v1/tables/${tableId}/columns`, signal)
}

export async function fetchRelationships(
  tableId: string,
  signal: AbortSignal,
): Promise<TableRelationships> {
  return readJson<TableRelationships>(`/api/v1/tables/${tableId}/relationships`, signal)
}

export async function fetchSparkQuery(
  tableId: string,
  signal: AbortSignal,
): Promise<SparkQueryAvailability> {
  return readJson<SparkQueryAvailability>(`/api/v1/tables/${tableId}/spark-query`, signal)
}

export async function fetchLineage(tableId: string, signal: AbortSignal): Promise<GraphViewRecord> {
  return readJson<GraphViewRecord>(`/api/v1/tables/${tableId}/lineage?depth=2&direction=both`, signal)
}

export async function fetchRelationshipGraph(
  tableId: string,
  signal: AbortSignal,
): Promise<GraphViewRecord> {
  return readJson<GraphViewRecord>(`/api/v1/tables/${tableId}/relationships/graph?depth=2`, signal)
}

export async function fetchContextGraph(
  tableId: string,
  signal: AbortSignal,
): Promise<GraphViewRecord> {
  return readJson<GraphViewRecord>(`/api/v1/tables/${tableId}/context-graph?depth=2`, signal)
}

export async function fetchKnowledgeGraph(
  tableId: string,
  signal: AbortSignal,
): Promise<GraphViewRecord> {
  return readJson<GraphViewRecord>(`/api/v1/tables/${tableId}/knowledge-graph`, signal)
}

export async function fetchBindings(
  tableId: string,
  signal: AbortSignal,
): Promise<AssetBindingRecord[]> {
  return readJson<AssetBindingRecord[]>(`/api/v1/tables/${tableId}/bindings`, signal)
}

export async function fetchTableTags(tableId: string, signal: AbortSignal): Promise<TagRecord[]> {
  return readJson<TagRecord[]>(`/api/v1/tables/${tableId}/tags`, signal)
}

export type KnowledgeColumn = {
  id: string
  name: string
  data_type: string | null
  discovered_data_type: string | null
  is_primary_key: boolean | null
  is_unique: boolean | null
  is_foreign_key: boolean
}

export type KnowledgeRelationship = {
  id: string
  relationship_type: string
  status: string
  source_table_id: string
  target_table_id: string
  column_mappings: { source_column_id: string; target_column_id: string }[]
}

export type KnowledgeNeighbor = {
  table_id: string
  name: string
  direction: string
}

export type KnowledgeDataset = {
  namespace: string
  name: string
  resolved: boolean
}

export type KnowledgeJob = {
  namespace: string
  name: string
}

export type KnowledgeContext = {
  table: {
    name: string
    description: string | null
    source_description: string | null
    space: { name: string }
    scope: { name: string }
    tags: { name: string }[]
  }
  columns: KnowledgeColumn[]
  data_profile: { access_mode: string; row_count: number | null } | null
  existing_relationships: KnowledgeRelationship[]
  graph_neighbors: KnowledgeNeighbor[]
  lineage: {
    upstream_datasets: KnowledgeDataset[]
    downstream_datasets: KnowledgeDataset[]
    producer_jobs: KnowledgeJob[]
    consumer_jobs: KnowledgeJob[]
  } | null
}

export async function fetchKnowledgeContext(
  tableId: string,
  signal: AbortSignal,
): Promise<KnowledgeContext | null> {
  const response: Response = await apiFetch(`/api/v1/tables/${tableId}/knowledge-context`, { signal })
  if (response.status === 404) {
    return null
  }
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as KnowledgeContext
}

export type KnowledgeBuildJob = {
  id: string
  table_id: string
  table_name: string
  status: "running" | "succeeded" | "failed"
  error: string | null
}

export type TableBuildOptions = {
  include_data_profile: boolean
  include_graph: boolean
  include_lineage: boolean
}

export async function startKnowledgeBuild(
  tableId: string,
  options: TableBuildOptions,
): Promise<KnowledgeBuildJob> {
  const response: Response = await apiFetch(`/api/v1/tables/${tableId}/knowledge-context`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options),
  })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as KnowledgeBuildJob
}

export async function fetchKnowledgeBuild(jobId: string): Promise<KnowledgeBuildJob> {
  const response: Response = await apiFetch(`/api/v1/knowledge-builds/${jobId}`)
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as KnowledgeBuildJob
}

export async function fetchLatestKnowledgeBuild(
  tableId: string,
  signal: AbortSignal,
): Promise<KnowledgeBuildJob | null> {
  const response: Response = await apiFetch(`/api/v1/tables/${tableId}/knowledge-context/build`, {
    signal,
  })
  if (response.status === 404) {
    return null
  }
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as KnowledgeBuildJob
}


export type CatalogJobCancel = {
  id: string
  status: "running" | "succeeded" | "failed"
  error: string | null
}

export async function cancelCatalogJob(jobId: string): Promise<CatalogJobCancel> {
  const response: Response = await apiFetch(`/api/v1/catalog-jobs/${jobId}/cancel`, {
    method: "POST",
  })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as CatalogJobCancel
}

export type ContextSubjectKind = "table" | "table_columns" | "column" | "airflow" | "asset"

export type ContextEntityKind = "table" | "column" | "airflow" | "asset"

export type ContextGenerationJob = {
  id: string
  subject_kind: ContextSubjectKind
  subject_id: string
  subject_name: string
  status: "running" | "succeeded" | "failed"
  error: string | null
}

export async function startContextGeneration(
  kind: ContextSubjectKind,
  id: string,
): Promise<ContextGenerationJob> {
  const response: Response = await apiFetch(contextGeneratePath(kind, id), { method: "POST" })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as ContextGenerationJob
}

export async function fetchContextGeneration(jobId: string): Promise<ContextGenerationJob> {
  const response: Response = await apiFetch(`/api/v1/context-generations/${jobId}`)
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as ContextGenerationJob
}

export async function fetchLatestContextGeneration(
  kind: ContextSubjectKind,
  id: string,
  signal: AbortSignal,
): Promise<ContextGenerationJob | null> {
  const response: Response = await apiFetch(contextLatestPath(kind, id), { signal })
  if (response.status === 404) {
    return null
  }
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as ContextGenerationJob
}

export async function updateEntityContext(
  kind: ContextEntityKind,
  id: string,
  context: string | null,
): Promise<void> {
  const response: Response = await apiFetch(contextPatchPath(kind, id), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ context }),
  })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
}

function contextGeneratePath(kind: ContextSubjectKind, id: string): string {
  switch (kind) {
    case "table":
      return `/api/v1/tables/${id}/context/generate`
    case "table_columns":
      return `/api/v1/tables/${id}/columns/context/generate`
    case "column":
      return `/api/v1/columns/${id}/context/generate`
    case "airflow":
      return `/api/v1/airflow/instances/${id}/context/generate`
    case "asset":
      return `/api/v1/assets/${id}/context/generate`
  }
}

function contextLatestPath(kind: ContextSubjectKind, id: string): string {
  switch (kind) {
    case "table":
      return `/api/v1/tables/${id}/context/generation?lane=table`
    case "table_columns":
      return `/api/v1/tables/${id}/context/generation?lane=table_columns`
    case "column":
      return `/api/v1/columns/${id}/context/generation`
    case "airflow":
      return `/api/v1/airflow/instances/${id}/context/generation`
    case "asset":
      return `/api/v1/assets/${id}/context/generation`
  }
}

function contextPatchPath(kind: ContextEntityKind, id: string): string {
  switch (kind) {
    case "table":
      return `/api/v1/tables/${id}`
    case "column":
      return `/api/v1/columns/${id}`
    case "airflow":
      return `/api/v1/airflow/instances/${id}`
    case "asset":
      return `/api/v1/assets/${id}`
  }
}

export async function searchCatalog(query: string, signal: AbortSignal): Promise<SearchHitRecord[]> {
  const params: URLSearchParams = new URLSearchParams({ q: query })
  const body: { hits: SearchHitRecord[] } = await readJson<{ hits: SearchHitRecord[] }>(
    `/api/v1/search?${params.toString()}`,
    signal,
  )
  return body.hits
}

export type BuildTraceKind = "table" | "scope" | "space" | "airflow" | "asset"

export type BuildTraceEvent = {
  id: string
  at: string
  kind: BuildTraceKind
  subject_name: string
  subject_id: string
  job_id: string
  agent: string
  step: string
  title: string
  detail: string
}

export type BuildTraceJob = {
  job_id: string
  status: "running" | "succeeded" | "failed"
  error: string | null
  finished_at: string | null
}

export type BuildTracePage = {
  events: BuildTraceEvent[]
  cursor: string | null
  jobs: BuildTraceJob[]
}

export async function fetchBuildTraces(
  after: string | null,
  openJobIds: readonly string[],
  signal: AbortSignal,
): Promise<BuildTracePage> {
  const params: URLSearchParams = new URLSearchParams()
  if (after !== null && after !== "") {
    params.set("after", after)
  }
  if (openJobIds.length > 0) {
    params.set("jobs", openJobIds.slice(0, 80).join(","))
  }
  const query: string = params.toString()
  const path: string = query === "" ? "/api/v1/build-traces" : `/api/v1/build-traces?${query}`
  const page: BuildTracePage = await readJson<BuildTracePage>(path, signal)
  return {
    events: page.events,
    cursor: page.cursor,
    jobs: page.jobs ?? [],
  }
}

export { isAbortError }

async function readJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response: Response = await apiFetch(path, { signal })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as T
}

async function readDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (typeof body === "object" && body !== null && "detail" in body) {
      return detailMessage(body.detail) ?? `Request failed (${response.status})`
    }
  } catch {
    return `Request failed (${response.status})`
  }
  return `Request failed (${response.status})`
}

function detailMessage(detail: unknown): string | null {
  if (typeof detail === "string" && detail !== "") {
    return detail
  }
  if (!Array.isArray(detail)) {
    return null
  }
  const messages: string[] = []
  for (const item of detail) {
    if (typeof item === "object" && item !== null && "msg" in item) {
      const msg: unknown = item.msg
      if (typeof msg === "string" && msg !== "") {
        messages.push(msg)
      }
    }
  }
  if (messages.length === 0) {
    return null
  }
  return messages.join("; ")
}
