import { Fragment, useEffect, useState, type ReactElement } from "react"
import { useSearchParams, type SetURLSearchParams } from "react-router-dom"
import { errorMessage, isAbortError } from "./api"
import {
  fetchAssetRuns,
  fetchAssetRunTask,
  type AssetRun,
  type AssetRunEvent,
  type AssetRunStatus,
  type AssetRunTaskDetail,
  type AssetRunTaskSummary,
  type AssetRunTrigger,
} from "./assetsApi"

const TASKS: readonly { id: string; label: string }[] = [
  { id: "build_context", label: "Build context" },
  { id: "describe", label: "Describe" },
]
const POLL_MS: number = 2000
const PAGE_SIZES: readonly number[] = [10, 25, 50]
const COLUMN_COUNT: number = 5 + TASKS.length

type StatusFilter = "all" | AssetRunStatus
type TriggerFilter = "all" | AssetRunTrigger

type RunQueryPatch = {
  status?: StatusFilter
  trigger?: TriggerFilter
  rows?: number
  page?: number
  run?: string | null
  task?: string
}

export function AssetRuns(props: { assetId: string }): ReactElement {
  const assetId: string = props.assetId
  const [runs, setRuns] = useState<AssetRun[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [searchParams, setSearchParams]: [URLSearchParams, SetURLSearchParams] = useSearchParams()
  const statusFilter: StatusFilter = readStatus(searchParams)
  const triggerFilter: TriggerFilter = readTrigger(searchParams)
  const pageSize: number = readRows(searchParams)
  const page: number = readPage(searchParams)
  const rawRun: string | null = searchParams.get("run")
  const openRunId: string | null = rawRun === null || rawRun === "" ? null : rawRun
  const taskId: string = readTask(searchParams)
  const [detail, setDetail] = useState<AssetRunTaskDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const filtered: AssetRun[] = (runs ?? []).filter((run: AssetRun) => matches(run, statusFilter, triggerFilter))
  const pageCount: number = Math.max(1, Math.ceil(filtered.length / pageSize))
  const safePage: number = Math.min(page, pageCount - 1)
  const start: number = safePage * pageSize
  const visible: AssetRun[] = filtered.slice(start, start + pageSize)
  const openRun: AssetRun | null = filtered.find((run: AssetRun) => run.id === openRunId) ?? null

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
        setRuns((current: AssetRun[] | null) =>
          current !== null && runsFingerprint(current) === runsFingerprint(rows) ? current : rows,
        )
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
    if (openRunId === null) {
      setDetail(null)
      return
    }
    const controller: AbortController = new AbortController()
    const runId: string = openRunId
    void fetchAssetRunTask(assetId, runId, taskId, controller.signal)
      .then((loaded: AssetRunTaskDetail) => {
        if (controller.signal.aborted) {
          return
        }
        setDetail((current: AssetRunTaskDetail | null) =>
          current !== null && detailFingerprint(current) === detailFingerprint(loaded) ? current : loaded,
        )
        setDetailError(null)
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted || isAbortError(err)) {
          return
        }
        setDetailError(errorMessage(err))
      })
    return () => {
      controller.abort()
    }
  }, [assetId, openRunId, taskId])

  function writeQuery(patch: RunQueryPatch): void {
    setSearchParams((current: URLSearchParams) => {
      const params: URLSearchParams = new URLSearchParams(current)
      const status: StatusFilter = patch.status ?? readStatus(params)
      const trigger: TriggerFilter = patch.trigger ?? readTrigger(params)
      const rows: number = patch.rows ?? readRows(params)
      const nextPage: number = patch.page ?? readPage(params)
      putParam(params, "status", status, "all")
      putParam(params, "trigger", trigger, "all")
      putParam(params, "rows", String(rows), "10")
      putParam(params, "page", String(nextPage), "0")
      if ("run" in patch) {
        if (patch.run === null || patch.run === undefined) {
          params.delete("run")
          params.delete("task")
        } else {
          params.set("run", patch.run)
          params.set("task", patch.task ?? readTask(params))
        }
      } else if (patch.task !== undefined) {
        params.set("task", patch.task)
      }
      return params
    }, { replace: true })
  }

  function toggleRun(runId: string): void {
    if (openRunId === runId) {
      writeQuery({ run: null })
      return
    }
    writeQuery({ run: runId, task: taskId })
  }

  function openTask(runId: string, nextTaskId: string): void {
    writeQuery({ run: runId, task: nextTaskId })
  }

  return (
    <div className="run-board">
      {error !== null ? <p className="error-text">{error}</p> : null}
      {runs === null ? <p className="note">Loading runs…</p> : null}
      {runs !== null && runs.length === 0 ? (
        <p className="note">No runs yet. Apply the definition or build context.</p>
      ) : null}
      {runs !== null && runs.length > 0 ? (
        <section className="asset-card asset-table-card">
          <div className="run-toolbar">
            <label>
              Status
              <select
                value={statusFilter}
                onChange={(event) => {
                  writeQuery({ status: event.target.value as StatusFilter, page: 0 })
                }}
              >
                <option value="all">All</option>
                <option value="succeeded">Succeeded</option>
                <option value="failed">Failed</option>
                <option value="running">Running</option>
                <option value="queued">Queued</option>
              </select>
            </label>
            <label>
              Trigger
              <select
                value={triggerFilter}
                onChange={(event) => {
                  writeQuery({ trigger: event.target.value as TriggerFilter, page: 0 })
                }}
              >
                <option value="all">All</option>
                <option value="manual">Manual</option>
                <option value="apply">Apply</option>
                <option value="schedule">Schedule</option>
              </select>
            </label>
            <label>
              Rows
              <select
                value={pageSize}
                onChange={(event) => {
                  writeQuery({ rows: Number(event.target.value), page: 0 })
                }}
              >
                {PAGE_SIZES.map((size: number) => (
                  <option key={size} value={size}>
                    {size}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {filtered.length === 0 ? <p className="note run-empty">No runs match these filters.</p> : null}
          {filtered.length > 0 ? (
            <table className="columns run-table">
              <thead>
                <tr>
                  <th>Try</th>
                  <th>Trigger</th>
                  <th>Started</th>
                  <th>Duration</th>
                  <th>Status</th>
                  {TASKS.map((task) => (
                    <th key={task.id}>{task.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visible.map((run: AssetRun) => {
                  const open: boolean = run.id === openRun?.id
                  return (
                    <Fragment key={run.id}>
                      <tr
                        className={open ? "is-selected" : ""}
                        aria-expanded={open}
                        onClick={() => {
                          toggleRun(run.id)
                        }}
                      >
                        <td>
                          <span className="run-caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
                          {`#${run.try_number}`}
                        </td>
                        <td>{run.trigger}</td>
                        <td>{formatWhen(run.started_at ?? run.queued_at)}</td>
                        <td>{formatDuration(run.duration)}</td>
                        <td>
                          <span className={`run-status ${stateClass(run.status)}`}>{run.status}</span>
                        </td>
                        {TASKS.map((task) => {
                          const state: string = stateOf(run, task.id)
                          const on: boolean = open && task.id === taskId
                          return (
                            <td key={task.id}>
                              <button
                                type="button"
                                className={on ? `run-status is-selected ${stateClass(state)}` : `run-status ${stateClass(state)}`}
                                onClick={(event) => {
                                  event.stopPropagation()
                                  openTask(run.id, task.id)
                                }}
                              >
                                {state}
                              </button>
                            </td>
                          )
                        })}
                      </tr>
                      {open ? (
                        <tr className="run-expand">
                          <td colSpan={COLUMN_COUNT}>
                            {detailError !== null ? <p className="error-text">{detailError}</p> : null}
                            <RunExpansion
                              run={run}
                              taskId={taskId}
                              detail={detail !== null && detail.run_id === run.id && detail.task_id === taskId ? detail : null}
                            />
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          ) : null}
          <div className="run-pager">
            <span>
              {filtered.length === 0
                ? "0 runs"
                : `${start + 1}–${Math.min(start + pageSize, filtered.length)} of ${filtered.length}`}
            </span>
            <button
              type="button"
              className="button secondary"
              disabled={safePage === 0}
              onClick={() => {
                writeQuery({ page: safePage - 1 })
              }}
            >
              Previous
            </button>
            <button
              type="button"
              className="button secondary"
              disabled={safePage >= pageCount - 1}
              onClick={() => {
                writeQuery({ page: safePage + 1 })
              }}
            >
              Next
            </button>
          </div>
        </section>
      ) : null}
    </div>
  )
}

function RunExpansion(props: {
  run: AssetRun
  taskId: string
  detail: AssetRunTaskDetail | null
}): ReactElement {
  const task: AssetRunTaskSummary | null =
    props.run.tasks.find((item: AssetRunTaskSummary) => item.task_id === props.taskId) ?? null
  const taskLabel: string = TASKS.find((item) => item.id === props.taskId)?.label ?? props.taskId
  const params: string = JSON.stringify(props.run.operator_params, null, 2)
  const rows: readonly { label: string; value: string }[] = [
    { label: "Task", value: taskLabel },
    { label: "State", value: task?.state ?? "none" },
    { label: "Trigger", value: props.run.trigger },
    { label: "Try", value: String(props.run.try_number) },
    { label: "Start", value: formatWhen(task?.started_at ?? null) },
    { label: "End", value: formatWhen(task?.finished_at ?? null) },
    { label: "Duration", value: formatDuration(task?.duration ?? null) },
    { label: "Operator", value: props.run.operator_type ?? "—" },
    { label: "Error", value: task?.error ?? props.run.error ?? "—" },
    { label: "Run", value: props.detail?.run_status ?? props.run.status },
  ]
  return (
    <div className="run-expand-grid">
      <section aria-label="Details">
        <h3>Details</h3>
        <table className="run-detail-table">
          <tbody>
            {rows.map((row) => (
              <tr key={row.label}>
                <th scope="row">{row.label}</th>
                <td>{row.value}</td>
              </tr>
            ))}
            <tr>
              <th scope="row">Params</th>
              <td>{params === "{}" ? "—" : <pre className="run-log">{params}</pre>}</td>
            </tr>
          </tbody>
        </table>
      </section>
      <LogList detail={props.detail} />
    </div>
  )
}

function LogList(props: { detail: AssetRunTaskDetail | null }): ReactElement {
  if (props.detail === null) {
    return (
      <section aria-label="Logs">
        <h3>Logs</h3>
        <p className="note">Loading logs…</p>
      </section>
    )
  }
  const events: AssetRunEvent[] = props.detail.events
  const log: string = props.detail.log
  return (
    <section aria-label="Logs">
      <h3>Logs</h3>
      {log === "" ? <p className="note">No log lines.</p> : <pre className="run-log">{log}</pre>}
      {events.length === 0 ? <p className="note">No trace steps.</p> : null}
      {events.length > 0 ? (
        <ul className="run-events">
          {events.map((event: AssetRunEvent, index: number) => (
            <li key={`${event.at}-${event.step}-${index}`}>
              <strong>{event.title}</strong>
              <small>{`${event.step} · ${formatWhen(event.at)}`}</small>
              {event.detail === "" ? null : <pre className="run-log">{event.detail}</pre>}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}

function readStatus(params: URLSearchParams): StatusFilter {
  const value: string | null = params.get("status")
  if (value === "succeeded" || value === "failed" || value === "running" || value === "queued") {
    return value
  }
  return "all"
}

function readTrigger(params: URLSearchParams): TriggerFilter {
  const value: string | null = params.get("trigger")
  if (value === "manual" || value === "apply" || value === "schedule") {
    return value
  }
  return "all"
}

function readRows(params: URLSearchParams): number {
  const value: number = Number(params.get("rows"))
  if (PAGE_SIZES.some((size: number) => size === value)) {
    return value
  }
  return 10
}

function readPage(params: URLSearchParams): number {
  const value: number = Number(params.get("page"))
  if (!Number.isInteger(value) || value < 0) {
    return 0
  }
  return value
}

function readTask(params: URLSearchParams): string {
  const value: string | null = params.get("task")
  if (value !== null && TASKS.some((task) => task.id === value)) {
    return value
  }
  return TASKS[0]?.id ?? "build_context"
}

function putParam(params: URLSearchParams, key: string, value: string, fallback: string): void {
  if (value === fallback) {
    params.delete(key)
  } else {
    params.set(key, value)
  }
}

function runsFingerprint(rows: AssetRun[]): string {
  return rows
    .map((run: AssetRun) => {
      const tasks: string = run.tasks
        .map((task: AssetRunTaskSummary) => `${task.task_id}:${task.state}:${task.duration ?? ""}`)
        .join(",")
      return `${run.id}|${run.status}|${run.duration ?? ""}|${tasks}`
    })
    .join(";")
}

function detailFingerprint(detail: AssetRunTaskDetail): string {
  const events: string = detail.events
    .map((event: AssetRunEvent) => `${event.at}:${event.step}:${event.title}`)
    .join(",")
  return `${detail.run_id}|${detail.task_id}|${detail.state}|${detail.log}|${events}`
}

function matches(run: AssetRun, status: StatusFilter, trigger: TriggerFilter): boolean {
  if (status !== "all" && run.status !== status) {
    return false
  }
  if (trigger !== "all" && run.trigger !== trigger) {
    return false
  }
  return true
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
