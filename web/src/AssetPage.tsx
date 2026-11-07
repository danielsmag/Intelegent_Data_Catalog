import { Fragment, useEffect, useState, type ReactElement, type ReactNode } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { useParams } from "react-router-dom"
import {
  fetchAsset,
  fetchAssetOutput,
  fetchAssetOutputs,
  fetchAssetRuns,
  fetchAssetRunTask,
  runAsset,
  type AssetKind,
  type AssetOutput,
  type AssetOutputDetail,
  type AssetRecord,
  type AssetRun,
  type AssetRunTaskDetail,
  type AssetRunTaskSummary,
} from "./assetsApi"
import { useEditorTitle } from "./editorSlots"
import { useTabParam } from "./useTabParam"

const TABS = ["overview", "outputs", "runs"] as const
type AssetTab = (typeof TABS)[number]
const TAB_LABEL: Record<AssetTab, string> = {
  overview: "Overview",
  outputs: "Outputs",
  runs: "Runs",
}
const KIND_LABEL: Record<AssetKind, string> = {
  AIRFLOW_INSTANCE: "Airflow instance",
  ICEBERG_TABLE: "Iceberg table",
  ICEBERG_SCHEMA: "Iceberg schema",
  GITHUB_REPO: "GitHub repository",
}
const POLL_MS: number = 2000
const RUN_COLUMNS: number = 7

type Fact = {
  label: string
  value: string
  mono: boolean
}

export function AssetPage(): ReactElement {
  const assetId: string = useParams().assetId ?? ""
  const [asset, setAsset] = useState<AssetRecord | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState<boolean>(false)
  const [refreshKey, setRefreshKey] = useState<number>(0)
  const [tab, setTab] = useTabParam<AssetTab>(TABS, "overview")
  useEditorTitle(asset?.name ?? null)
  useEffect(() => {
    const controller: AbortController = new AbortController()
    void fetchAsset(assetId, controller.signal)
      .then((row: AssetRecord) => {
        setAsset(row)
        setError(null)
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) {
          return
        }
        setAsset(null)
        setError(err instanceof Error ? err.message : "Could not load asset")
      })
    return () => {
      controller.abort()
    }
  }, [assetId, refreshKey])

  async function trigger(force: boolean): Promise<void> {
    setRunning(true)
    setError(null)
    try {
      await runAsset(assetId, force)
      setTab("runs")
      setRefreshKey((current: number) => current + 1)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not run asset")
    } finally {
      setRunning(false)
    }
  }

  const kindLabel: string = asset === null ? "Asset" : KIND_LABEL[asset.kind]
  return (
    <section className="catalog-page asset-page">
      <header className="catalog-hero agent-hero asset-hero">
        <div className="catalog-hero-top">
          <div className="catalog-hero-copy">
            <p className="catalog-kicker">{kindLabel}</p>
            <div className="catalog-title-line">
              <h1>{asset?.name ?? "Asset"}</h1>
              {asset !== null ? (
                <span className={`agent-chip agent-status-${asset.status.toLowerCase()}`}>{asset.status}</span>
              ) : null}
              {asset?.run_status !== null && asset?.run_status !== undefined ? (
                <span className={`run-status ${stateClass(asset.run_status)}`}>{titleCase(asset.run_status)}</span>
              ) : null}
            </div>
            {asset?.description !== null && asset?.description !== undefined && asset.description !== "" ? (
              <p className="catalog-lead">{asset.description}</p>
            ) : null}
          </div>
          <div className="catalog-hero-actions">
            <button type="button" className="button" disabled={running || asset === null} onClick={() => void trigger(false)}>
              {running ? "Queuing" : "Run now"}
            </button>
            <button
              type="button"
              className="button secondary"
              disabled={running || asset === null}
              onClick={() => void trigger(true)}
            >
              Run forced
            </button>
          </div>
        </div>
      </header>
      {error !== null ? <p className="error-text agent-alert">{error}</p> : null}
      <div className="tabs" role="tablist" aria-label="Asset sections">
        {TABS.map((item: AssetTab) => (
          <button
            key={item}
            type="button"
            className={tab === item ? "tab selected" : "tab"}
            role="tab"
            aria-selected={tab === item}
            onClick={() => {
              setTab(item)
            }}
          >
            {TAB_LABEL[item]}
          </button>
        ))}
      </div>
      {tab === "overview" && asset !== null ? <Overview asset={asset} /> : null}
      {tab === "outputs" ? <Outputs assetId={assetId} /> : null}
      {tab === "runs" ? <Runs assetId={assetId} refreshKey={refreshKey} /> : null}
    </section>
  )
}

function Overview(props: { asset: AssetRecord }): ReactElement {
  const asset: AssetRecord = props.asset
  const sourceRows: Fact[] = recordFacts(asset.source)
  const propertyRows: Fact[] = recordFacts(asset.properties)
  return (
    <div className="asset-sheet">
      <ul className="agent-stats">
        <li>
          <span>Status</span>
          <strong>{asset.status}</strong>
        </li>
        <li>
          <span>Last run</span>
          <strong>{asset.run_status === null ? "None" : titleCase(asset.run_status)}</strong>
        </li>
        <li>
          <span>Schedule</span>
          <strong className="asset-stat-text">{asset.schedule ?? "Once on apply"}</strong>
        </li>
        <li>
          <span>Next run</span>
          <strong className="asset-stat-text">{formatWhen(asset.next_run_at)}</strong>
        </li>
      </ul>
      <div className="agent-overview-grid">
        <div className="agent-overview-main">
          {asset.description !== null && asset.description !== "" ? (
            <section className="agent-card">
              <h2>Description</h2>
              <p className="agent-prose">{asset.description}</p>
            </section>
          ) : null}
          <section className="agent-card">
            <h2>Execution</h2>
            <p className="agent-prose">{executionNote(asset.kind)}</p>
            <dl className="facts agent-facts asset-facts">
              <dt>Schedule</dt>
              <dd className="mono">{asset.schedule ?? "Once on apply"}</dd>
              <dt>Next run</dt>
              <dd>{formatWhen(asset.next_run_at)}</dd>
              <dt>Last status</dt>
              <dd>{asset.run_status === null ? "—" : titleCase(asset.run_status)}</dd>
              <dt>Last finished</dt>
              <dd>{formatWhen(asset.ran_at)}</dd>
            </dl>
          </section>
          {propertyRows.length > 0 ? (
            <section className="agent-card">
              <h2>Properties</h2>
              <FactList rows={propertyRows} />
            </section>
          ) : null}
        </div>
        <aside className="agent-card agent-properties" aria-label="Asset identity">
          <h2>Identity</h2>
          <dl className="facts agent-facts">
            <dt>Key</dt>
            <dd className="mono">{asset.key}</dd>
            <dt>Kind</dt>
            <dd>{KIND_LABEL[asset.kind]}</dd>
            <dt>Created</dt>
            <dd>{formatWhen(asset.created_at)}</dd>
            <dt>Updated</dt>
            <dd>{formatWhen(asset.updated_at)}</dd>
          </dl>
          <h2>Source</h2>
          {sourceRows.length === 0 ? <p className="note">No source fields.</p> : <FactList rows={sourceRows} />}
        </aside>
      </div>
    </div>
  )
}

function Outputs(props: { assetId: string }): ReactElement {
  const [rows, setRows] = useState<AssetOutput[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<AssetOutputDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    const controller: AbortController = new AbortController()
    void fetchAssetOutputs(props.assetId, controller.signal)
      .then((found: AssetOutput[]) => {
        setRows(found)
        setSelected((current: string | null) => current ?? found[0]?.id ?? null)
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "Could not load outputs")
        }
      })
    return () => {
      controller.abort()
    }
  }, [props.assetId])
  useEffect(() => {
    if (selected === null) {
      setDetail(null)
      return
    }
    const controller: AbortController = new AbortController()
    void fetchAssetOutput(props.assetId, selected, controller.signal)
      .then((found: AssetOutputDetail) => {
        setDetail(found)
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "Could not load output")
        }
      })
    return () => {
      controller.abort()
    }
  }, [props.assetId, selected])
  return (
    <div className="asset-output">
      {error !== null ? <p className="error-text">{error}</p> : null}
      <aside className="agent-card asset-output-rail" aria-label="Output files">
        <h2>Snapshots</h2>
        {rows.length === 0 ? <p className="note">No markdown yet. Run the asset.</p> : null}
        <ul className="asset-output-list">
          {rows.map((row: AssetOutput) => (
            <li key={row.id}>
              <button
                type="button"
                className={row.id === selected ? "asset-output-item is-selected" : "asset-output-item"}
                onClick={() => {
                  setSelected(row.id)
                }}
              >
                <strong>{row.name}</strong>
                <span>{row.task_id}</span>
                <small>{`${formatBytes(row.size_bytes)} · ${formatWhen(row.created_at)}`}</small>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <article className="asset-markdown" aria-label="Output document">
        {detail === null ? <p className="note">Select a snapshot.</p> : <MarkdownView markdown={detail.markdown} />}
      </article>
    </div>
  )
}

function Runs(props: { assetId: string; refreshKey: number }): ReactElement {
  const [rows, setRows] = useState<AssetRun[] | null>(null)
  const [openRunId, setOpenRunId] = useState<string | null>(null)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [detail, setDetail] = useState<AssetRunTaskDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive: boolean = true
    let timer: number = 0
    const controller: AbortController = new AbortController()

    function watch(found: AssetRun[]): void {
      window.clearInterval(timer)
      const active: boolean = found.some((run: AssetRun) => run.status === "queued" || run.status === "running")
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
        const found: AssetRun[] = await fetchAssetRuns(props.assetId, controller.signal)
        if (!alive) {
          return null
        }
        setRows((current: AssetRun[] | null) =>
          current !== null && runsFingerprint(current) === runsFingerprint(found) ? current : found,
        )
        setError(null)
        return found
      } catch (err: unknown) {
        if (!alive || controller.signal.aborted) {
          return null
        }
        setError(err instanceof Error ? err.message : "Could not load runs")
        setRows([])
        return null
      }
    }

    void load().then((found: AssetRun[] | null) => {
      if (found !== null) {
        watch(found)
      }
    })
    return () => {
      alive = false
      controller.abort()
      window.clearInterval(timer)
    }
  }, [props.assetId, props.refreshKey])

  useEffect(() => {
    if (openRunId === null || taskId === null) {
      setDetail(null)
      return
    }
    const controller: AbortController = new AbortController()
    void fetchAssetRunTask(props.assetId, openRunId, taskId, controller.signal)
      .then((found: AssetRunTaskDetail) => {
        if (!controller.signal.aborted) {
          setDetail(found)
        }
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "Could not load task")
        }
      })
    return () => {
      controller.abort()
    }
  }, [props.assetId, openRunId, taskId, rows])

  function toggleRun(run: AssetRun): void {
    if (openRunId === run.id) {
      setOpenRunId(null)
      setTaskId(null)
      return
    }
    setOpenRunId(run.id)
    setTaskId(run.tasks[0]?.task_id ?? null)
  }

  return (
    <div className="asset-sheet">
      {error !== null ? <p className="error-text">{error}</p> : null}
      {rows === null ? <p className="note">Loading runs…</p> : null}
      {rows !== null && rows.length === 0 ? (
        <section className="agent-empty" aria-label="Runs">
          <p className="agent-empty-title">No runs yet</p>
          <p>Run now queues the asset. Run forced repeats work even when the source has not changed.</p>
        </section>
      ) : null}
      {rows !== null && rows.length > 0 ? (
        <section className="agent-card agent-table-card">
          <table className="columns run-table">
            <thead>
              <tr>
                <th>Try</th>
                <th>Status</th>
                <th>Trigger</th>
                <th>Mode</th>
                <th>Started</th>
                <th>Duration</th>
                <th>Tasks</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((run: AssetRun) => {
                const open: boolean = run.id === openRunId
                return (
                  <Fragment key={run.id}>
                    <tr
                      className={open ? "is-selected" : ""}
                      
                      aria-expanded={open}
                      onClick={() => {
                        toggleRun(run)
                      }}
                    >
                      <td>
                        <span className="run-caret" aria-hidden="true">
                          {open ? "▾" : "▸"}
                        </span>
                        {`#${run.try_number}`}
                      </td>
                      <td>
                        <span className={`run-status ${stateClass(run.status)}`}>{titleCase(run.status)}</span>
                      </td>
                      <td>{titleCase(run.trigger)}</td>
                      <td>{run.force ? "Forced" : "Normal"}</td>
                      <td>{formatWhen(run.started_at ?? run.queued_at)}</td>
                      <td>{formatDuration(run.duration)}</td>
                      <td>
                        <div className="asset-task-row">
                          {run.tasks.length === 0 ? "—" : null}
                          {run.tasks.map((task: AssetRunTaskSummary) => (
                            <button
                              key={task.task_id}
                              type="button"
                              className={
                                open && task.task_id === taskId
                                  ? `run-status is-selected ${stateClass(task.state)}`
                                  : `run-status ${stateClass(task.state)}`
                              }
                              onClick={(event) => {
                                event.stopPropagation()
                                setOpenRunId(run.id)
                                setTaskId(task.task_id)
                              }}
                            >
                              {`${task.task_id} · ${task.state}`}
                            </button>
                          ))}
                        </div>
                      </td>
                    </tr>
                    {open ? (
                      <tr className="run-expand">
                        <td colSpan={RUN_COLUMNS}>
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
        </section>
      ) : null}
    </div>
  )
}

function RunExpansion(props: {
  run: AssetRun
  taskId: string | null
  detail: AssetRunTaskDetail | null
}): ReactElement {
  const task: AssetRunTaskSummary | null =
    props.run.tasks.find((item: AssetRunTaskSummary) => item.task_id === props.taskId) ?? null
  const log: string = props.detail?.log ?? ""
  const failure: string = props.detail?.error ?? task?.error ?? props.run.error ?? ""
  const rows: readonly Fact[] = [
    { label: "Task", value: task?.task_id ?? "—", mono: true },
    { label: "State", value: task === null ? "—" : titleCase(task.state), mono: false },
    { label: "Trigger", value: titleCase(props.run.trigger), mono: false },
    { label: "Try", value: String(props.run.try_number), mono: true },
    { label: "Mode", value: props.run.force ? "Forced" : "Normal", mono: false },
    { label: "Start", value: formatWhen(task?.started_at ?? props.run.started_at), mono: false },
    { label: "End", value: formatWhen(task?.finished_at ?? props.run.finished_at), mono: false },
    { label: "Duration", value: formatDuration(task?.duration ?? props.run.duration), mono: true },
    { label: "Error", value: failure === "" ? "—" : failure, mono: false },
  ]
  return (
    <div className="run-expand-grid">
      <section aria-label="Run details">
        <h3>Details</h3>
        <FactList rows={rows} />
      </section>
      <section aria-label="Task log">
        <h3>Log</h3>
        {props.detail === null ? <p className="note">Loading log…</p> : null}
        {props.detail !== null && log === "" && failure === "" ? <p className="note">No log lines.</p> : null}
        {log !== "" ? <pre className="run-log asset-log">{log}</pre> : null}
        {log === "" && failure !== "" ? <pre className="run-log asset-log">{failure}</pre> : null}
      </section>
    </div>
  )
}

function MarkdownView(props: { markdown: string }): ReactElement {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        table: MarkdownTable,
        a: MarkdownLink,
      }}
    >
      {props.markdown}
    </ReactMarkdown>
  )
}

function MarkdownTable(props: { children?: ReactNode }): ReactElement {
  return (
    <div className="asset-md-scroll">
      <table>{props.children}</table>
    </div>
  )
}

function MarkdownLink(props: { href?: string; children?: ReactNode }): ReactElement {
  return (
    <a href={props.href} target="_blank" rel="noreferrer">
      {props.children}
    </a>
  )
}

function FactList(props: { rows: readonly Fact[] }): ReactElement {
  return (
    <dl className="facts agent-facts asset-facts">
      {props.rows.map((row: Fact) => (
        <Fragment key={row.label}>
          <dt>{row.label}</dt>
          <dd className={row.mono ? "mono" : undefined}>
            {row.value.includes("\n") ? <pre className="run-log">{row.value}</pre> : row.value}
          </dd>
        </Fragment>
      ))}
    </dl>
  )
}

function recordFacts(record: Record<string, unknown>): Fact[] {
  const rows: Fact[] = []
  const key: string[] = Object.keys(record).sort()
  for (const name of key) {
    rows.push({
      label: titleCase(name),
      value: formatValue(record[name]),
      mono: name === "key" || name === "sha" || name === "owner" || name === "repo" || name === "branch",
    })
  }
  return rows
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "—"
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value)
  }
  if (Array.isArray(value) && value.every((item: unknown) => typeof item === "string" || typeof item === "number")) {
    return value.length === 0 ? "—" : value.join(", ")
  }
  return JSON.stringify(value, null, 2)
}

function executionNote(kind: AssetKind): string {
  if (kind === "GITHUB_REPO") {
    return "Each run snapshots repository metadata, the branch head, the file tree, and selected text files. A scheduled run skips when the head commit is unchanged. Run forced writes a new snapshot anyway."
  }
  if (kind === "AIRFLOW_INSTANCE") {
    return "Each run reads live Airflow metadata — version, health, DAGs, pools, providers, and import errors — and stores a markdown snapshot."
  }
  if (kind === "ICEBERG_TABLE") {
    return "Each run reads Iceberg table metadata and stores a markdown snapshot. A profile operator, when configured, adds column statistics."
  }
  return "Each run reads Iceberg namespace metadata and stores a markdown snapshot of the schema and its tables."
}

function titleCase(value: string): string {
  return value
    .split(/[_\s]+/)
    .filter((part: string) => part !== "")
    .map((part: string) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ")
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
  const year: string = String(parsed.getFullYear())
  const month: string = String(parsed.getMonth() + 1).padStart(2, "0")
  const day: string = String(parsed.getDate()).padStart(2, "0")
  const hour: string = String(parsed.getHours()).padStart(2, "0")
  const minute: string = String(parsed.getMinutes()).padStart(2, "0")
  const second: string = String(parsed.getSeconds()).padStart(2, "0")
  return `${year}-${month}-${day} ${hour}:${minute}:${second}`
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

function formatBytes(value: number): string {
  if (value < 1024) {
    return `${value} B`
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
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
