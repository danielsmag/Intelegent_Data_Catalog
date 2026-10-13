import type { ReactElement } from "react"
import { Link } from "react-router-dom"
import type { PipelineSummary, SpaceKnowledgeContext, SpaceScopeSummary, SpaceTableLink } from "./registryApi"

export function SpaceContextPanel(props: { context: SpaceKnowledgeContext }): ReactElement {
  const context: SpaceKnowledgeContext = props.context
  const names: Map<string, string> = nameIndex(context.scopes)
  const summary: string = context.summary ?? "No summary yet."
  const missing: string[] = context.scopes_without_context
  return (
    <section className="context-block" aria-label="Space context">
      <h2>Space context</h2>
      <p>{summary}</p>
      <dl className="facts">
        <dt>Scopes</dt>
        <dd>{context.scopes.length}</dd>
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
            ? "1 scope has no built context."
            : `${missing.length} scopes have no built context.`}{" "}
          {missing.map((scopeId: string, index: number) => (
            <span key={scopeId}>
              {index > 0 ? ", " : null}
              <Link to={`/scopes/${scopeId}`}>{names.get(scopeId) ?? scopeId}</Link>
            </span>
          ))}
        </p>
      ) : null}
      <h3>Cross-scope links</h3>
      {context.table_links.length === 0 ? <p className="note">No cross-scope links.</p> : null}
      <ul className="registry-list scope-links">
        {context.table_links.map((link: SpaceTableLink) => (
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

function nameIndex(scopes: SpaceScopeSummary[]): Map<string, string> {
  const names: Map<string, string> = new Map()
  for (const scope of scopes) {
    names.set(scope.id, scope.name)
    for (const table of scope.tables) {
      names.set(table.id, table.display_name ?? table.name)
      for (const column of table.columns) {
        names.set(column.id, `${table.name}.${column.name}`)
      }
    }
  }
  return names
}
