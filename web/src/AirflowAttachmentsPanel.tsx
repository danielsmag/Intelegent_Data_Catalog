import { useEffect, useState, type ReactElement } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { errorMessage, isAbortError } from "./api"
import {
  detachAirflow,
  fetchAirflowAttachments,
  fetchAttachedDags,
  type AirflowAttachedDag,
  type AirflowAttachment,
  type AirflowAttachmentTarget,
} from "./airflowApi"

type AirflowAttachmentsPanelProps = {
  targetKind: AirflowAttachmentTarget
  targetId: string
  revision: number
  onChanged: () => void
}

export function AirflowAttachmentsPanel(props: AirflowAttachmentsPanelProps): ReactElement {
  const [attachments, setAttachments] = useState<AirflowAttachment[] | null>(null)
  const [dags, setDags] = useState<AirflowAttachedDag[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pendingId, setPendingId] = useState<string | null>(null)
  const [searchParams] = useSearchParams()
  const focusInstance: string | null = searchParams.get("instance")
  const focusDag: string | null = searchParams.get("dag")

  useEffect(() => {
    const controller: AbortController = new AbortController()
    void fetchAirflowAttachments(props.targetKind, props.targetId, controller.signal)
      .then((loaded: AirflowAttachment[]) => {
        setAttachments(loaded)
        setError(null)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setError(errorMessage(err))
        }
      })
    void fetchAttachedDags(props.targetKind, props.targetId, controller.signal)
      .then((loaded: AirflowAttachedDag[]) => {
        setDags(loaded)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setDags([])
          setError(errorMessage(err))
        }
      })
    return () => {
      controller.abort()
    }
  }, [props.targetKind, props.targetId, props.revision])

  function detach(attachmentId: string): void {
    setPendingId(attachmentId)
    setError(null)
    void detachAirflow(props.targetKind, props.targetId, attachmentId)
      .then(() => {
        props.onChanged()
      })
      .catch((err: unknown) => {
        setError(errorMessage(err))
      })
      .finally(() => {
        setPendingId(null)
      })
  }

  return (
    <div>
      {error !== null ? <p className="error-text">{error}</p> : null}
      <h2>Attachments</h2>
      {attachments !== null && attachments.length === 0 ? (
        <p className="note">No Airflow attachments yet.</p>
      ) : null}
      <ul className="registry-list">
        {(attachments ?? []).map((attachment: AirflowAttachment) => {
          const label: string =
            attachment.dag_id === null
              ? `${attachment.instance_name} (whole instance)`
              : `${attachment.instance_name} / ${attachment.dag_id}`
          const focused: boolean =
            focusDag === null && focusInstance === attachment.instance_id
          return (
            <li key={attachment.id} className={focused ? "focused" : undefined}>
              <Link to={`/airflow/${attachment.instance_id}`}>{label}</Link>
              <button
                type="button"
                className="button secondary"
                disabled={pendingId === attachment.id}
                onClick={() => {
                  detach(attachment.id)
                }}
              >
                Detach
              </button>
            </li>
          )
        })}
      </ul>
      <h2>DAGs</h2>
      {dags !== null && dags.length === 0 ? <p className="note">No DAGs in these attachments.</p> : null}
      {dags !== null && dags.length > 0 ? (
        <table className="columns">
          <thead>
            <tr>
              <th>DAG</th>
              <th>Instance</th>
              <th>Via</th>
              <th>Schedule</th>
              <th>Tables</th>
              <th>Summary</th>
            </tr>
          </thead>
          <tbody>
            {dags.map((dag: AirflowAttachedDag) => (
              <tr
                key={dag.dag_row_id}
                className={
                  focusInstance === dag.instance_id && focusDag === dag.dag_id ? "focused" : undefined
                }
              >
                <td>
                  <Link to={`/airflow/${dag.instance_id}/dags/${encodeURIComponent(dag.dag_id)}`}>{dag.dag_id}</Link>
                </td>
                <td>{dag.instance_name}</td>
                <td>
                  <span className="badge">{dag.via}</span>
                </td>
                <td>{dag.timetable_summary ?? "—"}</td>
                <td>{dag.table_count}</td>
                <td>{dag.summary ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  )
}
