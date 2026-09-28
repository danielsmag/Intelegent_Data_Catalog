export type ColumnDetail = {
  name: string
  type: string
  required: boolean
  comment: string | null
}

export type TableSummary = {
  name: string
}

export type NamespaceNode = {
  name: string
  tables: TableSummary[]
}

export type CatalogNode = {
  name: string
  namespaces: NamespaceNode[]
}

export type CatalogTree = {
  catalogs: CatalogNode[]
}

export type TableDetail = {
  catalog: string
  namespace: string
  name: string
  location: string | null
  snapshot_id: string | null
  last_updated_ms: number | null
  columns: ColumnDetail[]
}

export type DetailTab = "overview" | "columns" | "sample"

export type QueryColumn = {
  name: string
  type: string
}

export type QueryCell = string | number | boolean | null | QueryCell[] | { [key: string]: QueryCell }

export type QueryResult = {
  columns: QueryColumn[]
  rows: QueryCell[][]
  truncated: boolean
}
