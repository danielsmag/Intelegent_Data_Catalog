import type { ReactElement } from "react"
import { Link } from "react-router-dom"
import type {
  PipelineSummary,
  PipelineTableRef,
  PipelineTask,
  ScopeKnowledgeContext,
  ScopeTableLink,
  ScopeTableSummary,
} from "./registryApi"

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
        {context.pipelines.length > 0 ? <AirflowOverview pipelines={context.pipelines} /> : null}
        {context.pipelines.length > 0 ? (
          <PipelineList pipelines={context.pipelines} showOverview={false} />
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

function AirflowOverview(props: { pipelines: PipelineSummary[] }): ReactElement | null {
  const groups: PipelineGroup[] = groupPipelines(props.pipelines).filter(
    (group: PipelineGroup) => group.summary !== null,
  )
  if (groups.length === 0) {
    return null
  }
  return (
    <section className="context-block airflow-overview" aria-label="Airflow overview">
      <h2>Airflow overview</h2>
      {groups.map((group: PipelineGroup) => (
        <div key={group.name} className="pipeline-instance">
          <span className="badge badge-airflow">{group.name}</span>
          <p>{group.summary}</p>
        </div>
      ))}
    </section>
  )
}

export function PipelineList(props: { pipelines: PipelineSummary[]; showOverview?: boolean }): ReactElement {
  const showOverview: boolean = props.showOverview ?? true
  const groups: PipelineGroup[] = groupPipelines(props.pipelines)
  return (
    <div className="pipeline-groups">
      {groups.map((group: PipelineGroup) => (
        <section key={group.name} className="pipeline-group" aria-label={group.name}>
          {showOverview && group.summary !== null ? (
            <div className="pipeline-instance">
              <span className="badge badge-airflow">{group.name}</span>
              <p>{group.summary}</p>
            </div>
          ) : null}
          <ul className="registry-list pipeline-list">
            {group.pipelines.map((pipeline: PipelineSummary) => (
              <PipelineItem key={`${pipeline.instance_name}-${pipeline.dag_id}`} pipeline={pipeline} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}

function PipelineItem(props: { pipeline: PipelineSummary }): ReactElement {
  const pipeline: PipelineSummary = props.pipeline
  const tasks: PipelineTask[] = pipeline.tasks ?? []
  const tables: PipelineTableRef[] = pipeline.tables ?? []
  return (
    <li>
      <span className="pipeline-title">
        <span className="badge badge-airflow">{pipeline.instance_name}</span>
        <span>{pipeline.dag_id}</span>
        {pipeline.schedule !== null && pipeline.schedule !== undefined && pipeline.schedule !== "" ? (
          <span className="comment">{pipeline.schedule}</span>
        ) : null}
      </span>
      {pipeline.summary !== null && pipeline.summary !== undefined && pipeline.summary !== "" ? (
        <p className="comment">{pipeline.summary}</p>
      ) : null}
      {pipeline.description !== null &&
      pipeline.description !== undefined &&
      pipeline.description !== "" &&
      pipeline.description !== pipeline.summary ? (
        <p className="comment">{pipeline.description}</p>
      ) : null}
      {tasks.length > 0 ? (
        <p className="comment">
          Tasks:{" "}
          {tasks
            .map((task: PipelineTask) =>
              task.operator_name !== null && task.operator_name !== ""
                ? `${task.task_id} (${task.operator_name})`
                : task.task_id,
            )
            .join(", ")}
        </p>
      ) : null}
      {tables.length > 0 ? (
        <p className="pipeline-tables">
          {tables.map((table: PipelineTableRef) => (
            <span key={`${table.direction}-${table.table_id}`}>
              <span className="badge">{table.direction}</span> {table.name}
            </span>
          ))}
        </p>
      ) : null}
    </li>
  )
}

type PipelineGroup = {
  name: string
  summary: string | null
  pipelines: PipelineSummary[]
}

function groupPipelines(pipelines: PipelineSummary[]): PipelineGroup[] {
  const groups: PipelineGroup[] = []
  const index: Map<string, PipelineGroup> = new Map()
  for (const pipeline of pipelines) {
    const summary: string | null = present(pipeline.instance_summary)
    const existing: PipelineGroup | undefined = index.get(pipeline.instance_name)
    if (existing === undefined) {
      const created: PipelineGroup = {
        name: pipeline.instance_name,
        summary,
        pipelines: [pipeline],
      }
      index.set(pipeline.instance_name, created)
      groups.push(created)
      continue
    }
    existing.pipelines.push(pipeline)
    if (existing.summary === null && summary !== null) {
      existing.summary = summary
    }
  }
  return groups
}

function present(value: string | null | undefined): string | null {
  if (value === null || value === undefined || value.trim() === "") {
    return null
  }
  return value
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
