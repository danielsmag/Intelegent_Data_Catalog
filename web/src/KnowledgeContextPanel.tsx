import type { ReactElement } from "react"
import type {
  KnowledgeColumn,
  KnowledgeContext,
  KnowledgeDataset,
  KnowledgeJob,
  KnowledgeRelationship,
} from "./registryApi"

export function KnowledgeContextPanel(props: { context: KnowledgeContext }): ReactElement {
  const context: KnowledgeContext = props.context
  const description: string =
    context.table.description ?? context.table.source_description ?? "No description"
  const tags: string = context.table.tags.map((tag) => tag.name).join(", ")
  return (
    <section className="context-block" aria-label="Built context">
      <h2>Built context</h2>
      <dl className="facts">
        <dt>Space</dt>
        <dd>{context.table.space.name}</dd>
        <dt>Scope</dt>
        <dd>{context.table.scope.name}</dd>
        <dt>Description</dt>
        <dd>{description}</dd>
        <dt>Tags</dt>
        <dd>{tags === "" ? "None" : tags}</dd>
        <dt>Data profile</dt>
        <dd>
          {context.data_profile === null
            ? "Not included. Physical profiler is not configured."
            : context.data_profile.access_mode}
        </dd>
      </dl>
      <h3>Columns</h3>
      {context.columns.length === 0 ? <p className="note">No columns.</p> : null}
      <ul className="registry-list">
        {context.columns.map((column: KnowledgeColumn) => (
          <li key={column.id}>
            <strong>{column.name}</strong>
            <span className="comment">
              {column.data_type ?? column.discovered_data_type ?? "Unknown type"}
            </span>
            <KeyMarks column={column} />
          </li>
        ))}
      </ul>
      <h3>Relationships</h3>
      {context.existing_relationships.length === 0 ? (
        <p className="note">No catalog relationships.</p>
      ) : (
        <ul className="registry-list">
          {context.existing_relationships.map((relation: KnowledgeRelationship) => (
            <li key={relation.id}>
              <span className="badge">{relation.relationship_type}</span>
              <span>{sourceColumn(context, relation)}</span>
              <span className="comment">{relation.status}</span>
            </li>
          ))}
        </ul>
      )}
      <h3>Graph neighbors</h3>
      {context.graph_neighbors.length === 0 ? (
        <p className="note">No neighboring tables.</p>
      ) : (
        <ul className="registry-list">
          {context.graph_neighbors.map((neighbor) => (
            <li key={`${neighbor.table_id}-${neighbor.direction}`}>
              <strong>{neighbor.name}</strong>
              <span className="comment">{neighbor.direction}</span>
            </li>
          ))}
        </ul>
      )}
      <h3>Lineage</h3>
      {context.lineage === null ? (
        <p className="note">Lineage was not requested.</p>
      ) : (
        <ul className="registry-list">
          <li>Upstream: {datasetList(context.lineage.upstream_datasets)}</li>
          <li>Downstream: {datasetList(context.lineage.downstream_datasets)}</li>
          <li>Producer jobs: {jobList(context.lineage.producer_jobs)}</li>
          <li>Consumer jobs: {jobList(context.lineage.consumer_jobs)}</li>
        </ul>
      )}
    </section>
  )
}

function KeyMarks(props: { column: KnowledgeColumn }): ReactElement | null {
  const marks: string[] = []
  if (props.column.is_primary_key === true) {
    marks.push("PK")
  }
  if (props.column.is_foreign_key) {
    marks.push("FK")
  }
  if (props.column.is_unique === true) {
    marks.push("unique")
  }
  if (marks.length === 0) {
    return null
  }
  return <span className="badge">{marks.join(" ")}</span>
}

function sourceColumn(context: KnowledgeContext, relation: KnowledgeRelationship): string {
  const sourceId: string | undefined = relation.column_mappings[0]?.source_column_id
  if (sourceId === undefined) {
    return context.table.name
  }
  const column: KnowledgeColumn | undefined = context.columns.find(
    (item: KnowledgeColumn) => item.id === sourceId,
  )
  return column?.name ?? context.table.name
}

function datasetList(datasets: KnowledgeDataset[]): string {
  if (datasets.length === 0) {
    return "None"
  }
  return datasets
    .map((dataset: KnowledgeDataset) =>
      dataset.resolved ? `${dataset.namespace}.${dataset.name}` : `${dataset.namespace}.${dataset.name} (unresolved)`,
    )
    .join(", ")
}

function jobList(jobs: KnowledgeJob[]): string {
  if (jobs.length === 0) {
    return "None"
  }
  return jobs.map((job: KnowledgeJob) => `${job.namespace}.${job.name}`).join(", ")
}
