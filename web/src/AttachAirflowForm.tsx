import { useEffect, useState, type ReactElement } from "react"
import { errorMessage, isAbortError } from "./api"
import {
  attachAirflow,
  fetchAirflowDags,
  fetchAirflowInstances,
  type AirflowAttachmentTarget,
  type AirflowDag,
  type AirflowInstance,
} from "./airflowApi"

type AttachAirflowFormProps = {
  targetKind: AirflowAttachmentTarget
  targetId: string
  onCancel: () => void
  onAttached: () => void
}

export function AttachAirflowForm(props: AttachAirflowFormProps): ReactElement {
  const [instances, setInstances] = useState<AirflowInstance[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [instanceId, setInstanceId] = useState<string>("")
  const [wholeInstance, setWholeInstance] = useState<boolean>(true)
  const [dags, setDags] = useState<AirflowDag[] | null>(null)
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const [pending, setPending] = useState<boolean>(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller: AbortController = new AbortController()
    void fetchAirflowInstances(controller.signal)
      .then((loaded: AirflowInstance[]) => {
        setInstances(loaded)
        setLoadError(null)
      })
      .catch((err: unknown) => {
        if (!isAbortError(err)) {
          setLoadError(errorMessage(err))
        }
      })
    return () => {
      controller.abort()
    }
  }, [])

  useEffect(() => {
    if (instanceId === "" || wholeInstance) {
      setDags(null)
      setSelected(new Set())
      return
    }
    const controller: AbortController = new AbortController()
    setDags(null)
    void fetchAirflowDags(instanceId, controller.signal)
      .then((loaded: AirflowDag[]) => {
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
  }, [instanceId, wholeInstance])

  function toggleDag(dagId: string): void {
    setSelected((current: ReadonlySet<string>) => {
      const next: Set<string> = new Set(current)
      if (next.has(dagId)) {
        next.delete(dagId)
      } else {
        next.add(dagId)
      }
      return next
    })
  }

  function attach(): void {
    if (instanceId === "" || pending) {
      return
    }
    const dagIds: Array<string | null> = wholeInstance ? [null] : [...selected]
    if (dagIds.length === 0) {
      return
    }
    setPending(true)
    setError(null)
    setMessage(null)
    void Promise.all(
      dagIds.map((dagId: string | null) =>
        attachAirflow(props.targetKind, props.targetId, { instance_id: instanceId, dag_id: dagId }),
      ),
    )
      .then(() => {
        setMessage(wholeInstance ? "Attached the Airflow instance." : `Attached ${dagIds.length} DAG${dagIds.length === 1 ? "" : "s"}.`)
        setSelected(new Set())
        props.onAttached()
        setPending(false)
      })
      .catch((err: unknown) => {
        setError(errorMessage(err))
        setPending(false)
      })
  }

  const dagList: AirflowDag[] = dags ?? []
  return (
    <form
      className="attach-panel"
      onSubmit={(event) => {
        event.preventDefault()
        attach()
      }}
    >
      <p className="note">Attach a whole Airflow instance, or pick DAGs from one instance.</p>
      {loadError !== null ? <p className="error-text">{loadError}</p> : null}
      {instances === null && loadError === null ? <p className="note">Loading Airflow instances…</p> : null}
      <div className="attach-grid">
        <label>
          Instance
          <select
            aria-label="Airflow instance"
            value={instanceId}
            disabled={pending || instances === null}
            onChange={(event) => {
              setInstanceId(event.target.value)
              setMessage(null)
              setError(null)
            }}
          >
            <option value="">Select instance</option>
            {(instances ?? []).map((instance: AirflowInstance) => (
              <option key={instance.id} value={instance.id}>
                {instance.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label>
        <input
          type="radio"
          name="airflow-attach-mode"
          checked={wholeInstance}
          disabled={pending || instanceId === ""}
          onChange={() => {
            setWholeInstance(true)
          }}
        />
        Whole instance
      </label>
      <label>
        <input
          type="radio"
          name="airflow-attach-mode"
          checked={!wholeInstance}
          disabled={pending || instanceId === ""}
          onChange={() => {
            setWholeInstance(false)
          }}
        />
        Selected DAGs
      </label>
      {!wholeInstance && instanceId !== "" ? (
        <div className="attach-tables">
          {dags === null ? <p className="note">Loading DAGs…</p> : null}
          {dags !== null && dagList.length === 0 ? <p className="note">No DAGs synced yet.</p> : null}
          {dagList.map((dag: AirflowDag) => (
            <label key={dag.dag_id}>
              <input
                type="checkbox"
                checked={selected.has(dag.dag_id)}
                disabled={pending}
                onChange={() => {
                  toggleDag(dag.dag_id)
                }}
              />
              {dag.dag_id}
            </label>
          ))}
        </div>
      ) : null}
      {message !== null ? <p className="note">{message}</p> : null}
      {error !== null ? <p className="error-text">{error}</p> : null}
      <div className="create-actions">
        <button
          className="button"
          type="submit"
          disabled={pending || instanceId === "" || (!wholeInstance && selected.size === 0)}
        >
          {pending ? "Attaching…" : "Attach"}
        </button>
        <button className="button secondary" type="button" disabled={pending} onClick={props.onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}
