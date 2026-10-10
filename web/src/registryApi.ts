import { isAbortError } from "./api"

export type SpaceRecord = {
  id: string
  name: string
  slug: string
  description: string | null
  context: string | null
  context_origin: "USER" | "LLM" | null
}

export type ScopeRecord = {
  id: string
  space_id: string
  name: string
  slug: string
  description: string | null
  context: string | null
  context_origin: "USER" | "LLM" | null
}

export type CatalogTableRecord = {
  id: string
  scope_id: string
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

export type DataSourceRecord = {
  id: string
  name: string
  type: string
  credential_ref: string | null
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

export type NamedCreate = {
  name: string
  description: string | null
}

export type AttachmentOutcome = "created" | "already_in_scope" | "already_elsewhere"

export type AttachmentItem = {
  name: string
  source_key: string
  outcome: AttachmentOutcome
  table_id: string | null
  scope_id: string | null
}

export type ScopeAttachment = {
  source_id: string
  catalog: string
  namespace: string
  tables: string[] | null
}

export async function fetchSpaces(signal: AbortSignal): Promise<SpaceRecord[]> {
  return readJson<SpaceRecord[]>("/api/v1/spaces", signal)
}

export async function createSpace(body: NamedCreate): Promise<SpaceRecord> {
  return writeJson<SpaceRecord>("/api/v1/spaces", body)
}

export async function createScope(spaceId: string, body: NamedCreate): Promise<ScopeRecord> {
  return writeJson<ScopeRecord>(`/api/v1/spaces/${spaceId}/scopes`, body)
}

export async function fetchScopes(spaceId: string, signal: AbortSignal): Promise<ScopeRecord[]> {
  return readJson<ScopeRecord[]>(`/api/v1/spaces/${spaceId}/scopes`, signal)
}

export async function fetchSpace(spaceId: string, signal: AbortSignal): Promise<SpaceRecord> {
  return readJson<SpaceRecord>(`/api/v1/spaces/${spaceId}`, signal)
}

export async function fetchScope(scopeId: string, signal: AbortSignal): Promise<ScopeRecord> {
  return readJson<ScopeRecord>(`/api/v1/scopes/${scopeId}`, signal)
}

export async function attachScopeCatalog(
  scopeId: string,
  body: ScopeAttachment,
): Promise<AttachmentItem[]> {
  const response: Response = await fetch(`/api/v1/scopes/${scopeId}/attachments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  const payload: { items: AttachmentItem[] } = (await response.json()) as { items: AttachmentItem[] }
  return payload.items
}

export async function fetchScopeTables(
  scopeId: string,
  signal: AbortSignal,
): Promise<CatalogTableRecord[]> {
  return readJson<CatalogTableRecord[]>(`/api/v1/scopes/${scopeId}/tables`, signal)
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
  const response: Response = await fetch(`/api/v1/tables/${tableId}/knowledge-context`, { signal })
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

export async function startKnowledgeBuild(tableId: string): Promise<KnowledgeBuildJob> {
  const response: Response = await fetch(`/api/v1/tables/${tableId}/knowledge-context`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      include_data_profile: false,
      include_graph: true,
      include_lineage: true,
    }),
  })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as KnowledgeBuildJob
}

export async function fetchKnowledgeBuild(jobId: string): Promise<KnowledgeBuildJob> {
  const response: Response = await fetch(`/api/v1/knowledge-builds/${jobId}`)
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as KnowledgeBuildJob
}

export async function fetchLatestKnowledgeBuild(
  tableId: string,
  signal: AbortSignal,
): Promise<KnowledgeBuildJob | null> {
  const response: Response = await fetch(`/api/v1/tables/${tableId}/knowledge-context/build`, {
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

export type ScopeTableColumn = {
  id: string
  name: string
}

export type ScopeTableSummary = {
  id: string
  name: string
  display_name: string | null
  description: string | null
  status: string
  columns: ScopeTableColumn[]
}

export type ScopeTableLink = {
  source_table_id: string
  target_table_id: string
  source_column_id: string
  target_column_id: string
  kind: string
  reason: string
}

export type ScopeKnowledgeContext = {
  scope: { id: string; name: string; description: string | null }
  space: { id: string; name: string }
  tables: ScopeTableSummary[]
  existing_relationships: { id: string; relationship_type: string; origin: string }[]
  table_links: ScopeTableLink[]
  summary: string | null
  tables_without_context: string[]
  previous_links_kept: number
}

export async function fetchScopeGraph(scopeId: string, signal: AbortSignal): Promise<GraphViewRecord> {
  return readJson<GraphViewRecord>(`/api/v1/scopes/${scopeId}/graph`, signal)
}

export async function fetchSpaceGraph(spaceId: string, signal: AbortSignal): Promise<GraphViewRecord> {
  return readJson<GraphViewRecord>(`/api/v1/spaces/${spaceId}/graph`, signal)
}

export async function fetchScopeKnowledgeContext(
  scopeId: string,
  signal: AbortSignal,
): Promise<ScopeKnowledgeContext | null> {
  const response: Response = await fetch(`/api/v1/scopes/${scopeId}/knowledge-context`, { signal })
  if (response.status === 404) {
    return null
  }
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as ScopeKnowledgeContext
}

export type ScopeBuildJob = {
  id: string
  scope_id: string
  scope_name: string
  status: "running" | "succeeded" | "failed"
  error: string | null
}

export async function startScopeBuild(scopeId: string): Promise<ScopeBuildJob> {
  const response: Response = await fetch(`/api/v1/scopes/${scopeId}/knowledge-context`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      include_table_knowledge: true,
      include_previous: true,
      include_summary: true,
    }),
  })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as ScopeBuildJob
}

export async function fetchScopeBuild(jobId: string): Promise<ScopeBuildJob> {
  const response: Response = await fetch(`/api/v1/scope-builds/${jobId}`)
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as ScopeBuildJob
}

export async function fetchLatestScopeBuild(
  scopeId: string,
  signal: AbortSignal,
): Promise<ScopeBuildJob | null> {
  const response: Response = await fetch(`/api/v1/scopes/${scopeId}/knowledge-context/build`, {
    signal,
  })
  if (response.status === 404) {
    return null
  }
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as ScopeBuildJob
}

export type SpaceScopeTable = {
  id: string
  name: string
  display_name: string | null
  columns: { id: string; name: string }[]
}

export type SpaceScopeSummary = {
  id: string
  name: string
  tables: SpaceScopeTable[]
}

export type SpaceTableLink = {
  source_table_id: string
  target_table_id: string
  source_column_id: string
  target_column_id: string
  kind: string
  reason: string
}

export type SpaceKnowledgeContext = {
  space: { id: string; name: string; description: string | null }
  scopes: SpaceScopeSummary[]
  existing_relationships: { id: string; relationship_type: string; origin: string }[]
  table_links: SpaceTableLink[]
  summary: string | null
  scopes_without_context: string[]
  previous_links_kept: number
}

export async function fetchSpaceKnowledgeContext(
  spaceId: string,
  signal: AbortSignal,
): Promise<SpaceKnowledgeContext | null> {
  const response: Response = await fetch(`/api/v1/spaces/${spaceId}/knowledge-context`, { signal })
  if (response.status === 404) {
    return null
  }
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as SpaceKnowledgeContext
}

export type SpaceBuildJob = {
  id: string
  space_id: string
  space_name: string
  status: "running" | "succeeded" | "failed"
  error: string | null
}

export async function startSpaceBuild(spaceId: string): Promise<SpaceBuildJob> {
  const response: Response = await fetch(`/api/v1/spaces/${spaceId}/knowledge-context`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      include_scope_knowledge: true,
      include_previous: true,
      include_summary: true,
    }),
  })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as SpaceBuildJob
}

export async function fetchSpaceBuild(jobId: string): Promise<SpaceBuildJob> {
  const response: Response = await fetch(`/api/v1/space-builds/${jobId}`)
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as SpaceBuildJob
}

export async function fetchLatestSpaceBuild(
  spaceId: string,
  signal: AbortSignal,
): Promise<SpaceBuildJob | null> {
  const response: Response = await fetch(`/api/v1/spaces/${spaceId}/knowledge-context/build`, {
    signal,
  })
  if (response.status === 404) {
    return null
  }
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as SpaceBuildJob
}

export type ContextSubjectKind = "space" | "scope" | "table" | "table_columns" | "column"

export type ContextEntityKind = "space" | "scope" | "table" | "column"

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
  const response: Response = await fetch(contextGeneratePath(kind, id), { method: "POST" })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as ContextGenerationJob
}

export async function fetchContextGeneration(jobId: string): Promise<ContextGenerationJob> {
  const response: Response = await fetch(`/api/v1/context-generations/${jobId}`)
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
  const response: Response = await fetch(contextLatestPath(kind, id), { signal })
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
  const response: Response = await fetch(contextPatchPath(kind, id), {
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
    case "space":
      return `/api/v1/spaces/${id}/context/generate`
    case "scope":
      return `/api/v1/scopes/${id}/context/generate`
    case "table":
      return `/api/v1/tables/${id}/context/generate`
    case "table_columns":
      return `/api/v1/tables/${id}/columns/context/generate`
    case "column":
      return `/api/v1/columns/${id}/context/generate`
  }
}

function contextLatestPath(kind: ContextSubjectKind, id: string): string {
  switch (kind) {
    case "space":
      return `/api/v1/spaces/${id}/context/generation`
    case "scope":
      return `/api/v1/scopes/${id}/context/generation`
    case "table":
      return `/api/v1/tables/${id}/context/generation?lane=table`
    case "table_columns":
      return `/api/v1/tables/${id}/context/generation?lane=table_columns`
    case "column":
      return `/api/v1/columns/${id}/context/generation`
  }
}

function contextPatchPath(kind: ContextEntityKind, id: string): string {
  switch (kind) {
    case "space":
      return `/api/v1/spaces/${id}`
    case "scope":
      return `/api/v1/scopes/${id}`
    case "table":
      return `/api/v1/tables/${id}`
    case "column":
      return `/api/v1/columns/${id}`
  }
}

export async function fetchDataSources(signal: AbortSignal): Promise<DataSourceRecord[]> {
  return readJson<DataSourceRecord[]>("/api/v1/data-sources", signal)
}

export type HierarchyTable = {
  id: string
  name: string
  status: string
}

export type HierarchyScope = {
  id: string
  name: string
  tables: HierarchyTable[]
}

export type HierarchySpace = {
  id: string
  name: string
  scopes: HierarchyScope[]
}

export async function fetchHierarchy(signal: AbortSignal): Promise<HierarchySpace[]> {
  const spaces: SpaceRecord[] = await fetchSpaces(signal)
  const trees: HierarchySpace[] = await Promise.all(
    spaces.map(async (space: SpaceRecord): Promise<HierarchySpace> => {
      const scopes: ScopeRecord[] = await fetchScopes(space.id, signal)
      const nested: HierarchyScope[] = await Promise.all(
        scopes.map(async (scope: ScopeRecord): Promise<HierarchyScope> => {
          const tables: CatalogTableRecord[] = await fetchScopeTables(scope.id, signal)
          return {
            id: scope.id,
            name: scope.name,
            tables: tables.map((table: CatalogTableRecord) => ({
              id: table.id,
              name: table.name,
              status: table.status,
            })),
          }
        }),
      )
      return { id: space.id, name: space.name, scopes: nested }
    }),
  )
  return trees
}

export async function searchCatalog(query: string, signal: AbortSignal): Promise<SearchHitRecord[]> {
  const params: URLSearchParams = new URLSearchParams({ q: query })
  const body: { hits: SearchHitRecord[] } = await readJson<{ hits: SearchHitRecord[] }>(
    `/api/v1/search?${params.toString()}`,
    signal,
  )
  return body.hits
}

export { isAbortError }

async function readJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response: Response = await fetch(path, { signal })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as T
}

async function writeJson<T>(path: string, body: NamedCreate): Promise<T> {
  const payload: { name: string; description?: string } = { name: body.name }
  if (body.description !== null) {
    payload.description = body.description
  }
  const response: Response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
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
