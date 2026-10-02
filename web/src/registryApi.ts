import { isAbortError } from "./api"

export type SpaceRecord = {
  id: string
  name: string
  slug: string
  description: string | null
}

export type ScopeRecord = {
  id: string
  space_id: string
  name: string
  slug: string
  description: string | null
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

export async function fetchSpaces(signal: AbortSignal): Promise<SpaceRecord[]> {
  return readJson<SpaceRecord[]>("/api/v1/spaces", signal)
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

export async function fetchBindings(
  tableId: string,
  signal: AbortSignal,
): Promise<AssetBindingRecord[]> {
  return readJson<AssetBindingRecord[]>(`/api/v1/tables/${tableId}/bindings`, signal)
}

export async function fetchTableTags(tableId: string, signal: AbortSignal): Promise<TagRecord[]> {
  return readJson<TagRecord[]>(`/api/v1/tables/${tableId}/tags`, signal)
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
