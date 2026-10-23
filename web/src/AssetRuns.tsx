import { useEffect, useState, type ReactElement } from "react"
import { errorMessage, isAbortError } from "./api"
import {
  fetchAssetRuns,
  fetchAssetRunTask,
  type AssetRun,
  type AssetRunTaskDetail,
  type AssetRunTaskSummary,
} from "./assetsApi"

const TASKS: readonly { id: string; label: string }[] = [
  { id: "build_context", label: "build context" },
  { id: "describe", label: "describe" },
]
const POLL_MS: number = 2000

type PanelTab = "details" | "logs"

export function AssetRuns(props: { assetId: string }): ReactElement {
  const assetId: string = props.assetId
  const [runs, setRuns] = useState<AssetRun[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [selectedTaskId, setSelectedTaskId] = useState<string>(TASKS[0]?.id ?? "build_context")
  const [panel, setPanel] = useState<PanelTab>("details")
  const [detail, setDetail] = useState<AssetRunTaskDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const selected: AssetRun | null = pickRun(runs, selectedRunId)

  useEffect(() => {
    let alive: boolean = true
    let timer: number = 0
    const controller: AbortController = new AbortController()

    function watch(rows: AssetRun[]): void {
      window.clearInterval(timer)
      const active: boolean = rows.some(
        (run: AssetRun) => run.status === "running" || run.status === "queued",
      )
      if (!active) {
        return
      }
      timer = window.setInterval(() => {
        void load().then((next: AssetRun[] | null) => {
          if (next !== null) {
            watch(next)
          }
        })
      }, POLL_MS)
    }

    async function load(): Promise<AssetRun[] | null> {
      try {
        const rows: AssetRun[] = await fetchAssetRuns(assetId, controller.signal)
        if (!alive) {
          return null
        }
        setRuns(rows)
        setError(null)
        return rows
      } catch (err: unknown) {
        if (!alive || controller.signal.aborted || isAbortError(err)) {
          return null
        }
        setError(errorMessage(err))
        setRuns([])
        return null
      }
    }

    void load().then((rows: AssetRun[] | null) => {
      if (rows !== null) {
        watch(rows)
      }
    })
    return () => {
      alive = false
      controller.abort()
      window.clearInterval(timer)
    }
  }, [assetId])

  useEffect(() => {
    if (selected === null) {
      setDetail(null)
      return
    }
    const controller: AbortController = new AbortController()
    const runId: string = selected.id
    void fetchAssetRunTask(assetId, runId, selectedTaskId, controller.signal)
      .then((loaded: AssetRunTaskDetail) => {
        setDetail(loaded)
        setDetailError(null)
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted || isAbortError(err)) {
          return
        }
        setDetail(null)
        setDetailError(errorMessage(err))
      })
    return () => {
      controller.abort()
    }
  }, [assetId, selected, selectedTaskId])

  function choose(runId: string, taskId: string): void {
    setSelectedRunId(runId)
    setSelectedTaskId(taskId)
  }

  return (
    <div className="run-board">
      {error !== null ? <p className="error-text">{error}</p> : null}
      <RunGraph
        run={selected}
        taskId={selectedTaskId}
        onSelect={(taskId: string) => {
          if (selected !== null) {
            choose(selected.id, taskId)
          } else {
            setSelectedTaskId(taskId)
          }
        }}
      />
      {runs === null ? <p className="note">Loading runs…</p> : null}
      {runs !== null && runs.length === 0 ? (
        <p className="note">No runs yet. Apply the definition or build context.</p>
      ) : null}
      {runs !== null && runs.length > 0 ? (
        <div className="run-grid-wrap">
          <table className="run-grid">
            <thead>
              <tr>
                <th>Task</th>
                {runs.map((run: AssetRun) => (
                  <th key={run.id}>
                    <button
                      type="button"
                      className={run.id === selected?.id ? "run-column is-selected" : "run-column"}
                      onClick={() => {
                        choose(run.id, selectedTaskId)
                      }}
                    >
                      {`#${run.try_number}`}
                      <small>{run.trigger}</small>
                      <small>{formatWhen(run.started_at ?? run.queued_at)}</small>
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {TASKS.map((task) => (
                <tr key={task.id}>
                  <th>{task.label}</th>
                  {runs.map((run: AssetRun) => {
                    const state: string = stateOf(run, task.id)
                    const on: boolean = run.id === selected?.id && task.id === selectedTaskId
                    return (
                      <td key={`${run.id}-${task.id}`}>
                        <button
                          type="button"
                          className={on ? `run-cell is-selected ${stateClass(state)}` : `run-cell ${stateClass(state)}`}
                          onClick={() => {
                            choose(run.id, task.id)
                          }}
                        >
                          {state}
                        </button>
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {selected !== null ? (
        <section className="run-panel" aria-label="Task detail">
          <div className="tab-row">
            <button type="button" className={panel === "details" ? "is-on" : ""} onClick={() => { setPanel("details") }}>
              Details
            </button>
            <button type="button" className={panel === "logs" ? "is-on" : ""} onClick={() => { setPanel("logs") }}>
              Logs
            </button>
          </div>
          {detailError !== null ? <p className="error-text">{detailError}</p> : null}
          {panel === "details" ? <Details run={selected} detail={detail} taskId={selectedTaskId} /> : null}
          {panel === "logs" ? <Logs detail={detail} /> : null}
        </section>
      ) : null}
    </div>
  )
}

function RunGraph(props: {
  run: AssetRun | null
  taskId: string
  onSelect: (taskId: string) => void
}): ReactElement {
  return (
    <div className="run-graph" aria-label="Operator tasks">
      {TASKS.map((task, index: number) => {
        const state: string = stateOf(props.run, task.id)
        const selected: boolean = task.id === props.taskId
        return (
          <span key={task.id} className="run-graph">
            {index > 0 ? <span className="run-arrow" aria-hidden="true">→</span> : null}
            <button
              type="button"
              className={selected ? `run-node is-selected ${stateClass(state)}` : `run-node ${stateClass(state)}`}
              onClick={() => {
                props.onSelect(task.id)
              }}
            >
              {task.label}
              <small>{state === "none" ? "no status" : state}</small>
            </button>
          </span>
        )
      })}
    </div>
  )
}

function Details(props: {
  run: AssetRun
  detail: AssetRunTaskDetail | null
  taskId: string
}): ReactElement {
  const task: AssetRunTaskSummary | null =
    props.run.tasks.find((item: AssetRunTaskSummary) => item.task_id === props.taskId) ?? null
  const params: string = JSON.stringify(props.run.operator_params, null, 2)
  return (
    <dl className="facts">
      <dt>State</dt>
      <dd>{task?.state ?? "none"}</dd>
      <dt>Trigger</dt>
      <dd>{props.run.trigger}</dd>
      <dt>Try</dt>
      <dd>{props.run.try_number}</dd>
      <dt>Start</dt>
      <dd>{formatWhen(task?.started_at ?? null)}</dd>
      <dt>End</dt>
      <dd>{formatWhen(task?.finished_at ?? null)}</dd>
      <dt>Duration</dt>
      <dd>{formatDuration(task?.duration ?? null)}</dd>
      <dt>Operator</dt>
      <dd>{props.run.operator_type ?? "—"}</dd>
      <dt>Error</dt>
      <dd>{task?.error ?? props.run.error ?? "—"}</dd>
      <dt>Params</dt>
      <dd><pre className="run-log">{params === "{}" ? "—" : params}</pre></dd>
      {props.detail === null ? null : (
        <>
          <dt>Run</dt>
          <dd>{props.detail.run_status}</dd>
        </>
      )}
    </dl>
  )
}

function Logs(props: { detail: AssetRunTaskDetail | null }): ReactElement {
  if (props.detail === null) {
    return <p className="note">Loading logs…</p>
  }
  return (
    <div>
      <pre className="run-log">{props.detail.log === "" ? "No log lines." : props.detail.log}</pre>
      {props.detail.events.length === 0 ? <p className="note">No trace steps.</p> : null}
      <ul className="run-events">
        {props.detail.events.map((event, index: number) => (
          <li key={`${event.at}-${event.step}-${index}`}>
            <strong>{event.title}</strong>
            <small>{` ${event.step} · ${formatWhen(event.at)}`}</small>
            {event.detail === "" ? null : <pre className="run-log">{event.detail}</pre>}
          </li>
        ))}
      </ul>
    </div>
  )
}

function pickRun(runs: AssetRun[] | null, selectedId: string | null): AssetRun | null {
  if (runs === null || runs.length === 0) {
    return null
  }
  return runs.find((run: AssetRun) => run.id === selectedId) ?? runs[0] ?? null
}

function stateOf(run: AssetRun | null, taskId: string): string {
  if (run === null) {
    return "none"
  }
  const task: AssetRunTaskSummary | undefined = run.tasks.find(
    (item: AssetRunTaskSummary) => item.task_id === taskId,
  )
  return task?.state ?? "none"
}

function stateClass(state: string): string {
  if (state === "succeeded") {
    return "state-success"
  }
  if (state === "failed" || state === "upstream_failed") {
    return "state-failed"
  }
  if (state === "running") {
    return "state-running"
  }
  if (state === "queued") {
    return "state-queued"
  }
  if (state === "skipped") {
    return "state-skipped"
  }
  return "state-none"
}

function formatWhen(value: string | null): string {
  if (value === null || value === "") {
    return "—"
  }
  const parsed: Date = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }
  return parsed.toLocaleString()
}

function formatDuration(value: number | null): string {
  if (value === null) {
    return "—"
  }
  if (value < 60) {
    return `${value.toFixed(1)}s`
  }
  const minutes: number = Math.floor(value / 60)
  const seconds: number = Math.round(value % 60)
  return `${minutes}m ${seconds}s`
}
