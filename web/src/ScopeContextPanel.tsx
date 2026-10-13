import type { ReactElement } from "react"
import { Link } from "react-router-dom"
import type { PipelineSummary, ScopeKnowledgeContext, ScopeTableLink, ScopeTableSummary } from "./registryApi"

export function ScopeContextPanel(props: { context: ScopeKnowledgeContext }): ReactElement {
  const context: ScopeKnowledgeContext = props.context
  const names: Map<string, string> = nameIndex(context.tables)
  const summary: string = context.summary ?? "No summary yet."
  const missing: string[] = context.tables_without_context
  return (
    <section className="context-block" aria-label="Scope context">
      <h2>Scope context</h2>
      <p>{summary}</p>
      <dl className="facts">
        <dt>Space</dt>
        <dd>{context.space.name}</dd>
        <dt>Tables</dt>
        <dd>{context.tables.length}</dd>
        <dt>Catalog relationships</dt>
        <dd>{context.existing_relationships.length}</dd>
        <dt>Links found</dt>
        <dd>{context.table_links.length}</dd>
        <dt>Previous links kept</dt>
        <dd>{context.previous_links_kept}</dd>
        <dt>Pipelines</dt>
        <dd>{context.pipelines.length}</dd>
      </dl>
      {context.pipelines.length > 0 ? (
        <ul className="registry-list">
          {context.pipelines.map((pipeline: PipelineSummary) => (
            <li key={`${pipeline.instance_name}-${pipeline.dag_id}`}>
              <span className="badge">{pipeline.instance_name}</span>
              <span>{pipeline.dag_id}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {missing.length > 0 ? (
        <p className="comment">
          {missing.length === 1
            ? "1 table has no built context."
            : `${missing.length} tables have no built context.`}{" "}
          {missing.map((tableId: string, index: number) => (
            <span key={tableId}>
              {index > 0 ? ", " : null}
              <Link to={`/registry/tables/${tableId}`}>{names.get(tableId) ?? tableId}</Link>
            </span>
          ))}
        </p>
      ) : null}
      <h3>Cross-table links</h3>
      {context.table_links.length === 0 ? <p className="note">No cross-table links.</p> : null}
      <ul className="registry-list scope-links">
        {context.table_links.map((link: ScopeTableLink) => (
          <li key={`${link.source_column_id}-${link.target_column_id}-${link.kind}`}>
            <span className="badge">{link.kind}</span>
            <span>
              {names.get(link.source_column_id) ?? link.source_column_id}
              {" → "}
              {names.get(link.target_column_id) ?? link.target_column_id}
            </span>
            <span className="comment">{link.reason}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function nameIndex(tables: ScopeTableSummary[]): Map<string, string> {
  const names: Map<string, string> = new Map()
  for (const table of tables) {
    names.set(table.id, table.display_name ?? table.name)
    for (const column of table.columns) {
      names.set(column.id, `${table.name}.${column.name}`)
    }
  }
  return names
}
