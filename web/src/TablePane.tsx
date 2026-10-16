import { useEffect, useState, type ReactElement } from "react"
import { errorMessage, fetchExplorerSample, formatTimestamp, isAbortError, sparkSampleSql } from "./api"
import { ResultGrid } from "./ResultGrid"
import { setPendingQuerySql, setSidebarView } from "./sidebarView"
import type { ColumnDetail, DetailTab, QueryResult, TableDetail } from "./types"
import { useTabParam } from "./useTabParam"

const DETAIL_TABS: readonly DetailTab[] = ["overview", "columns", "sample"]

type TablePaneProps = {
  source: string | undefined
  sourceName: string
  catalog: string | undefined
  namespace: string | undefined
  table: string | undefined
  detail: TableDetail | null
  loading: boolean
  error: string | null
}

export function TablePane(props: TablePaneProps): ReactElement {
  const [tab, selectTab] = useTabParam<DetailTab>(DETAIL_TABS, "overview")
  const [sample, setSample] = useState<QueryResult | null>(null)
  const [sampleError, setSampleError] = useState<string | null>(null)
  const [sampleLoading, setSampleLoading] = useState<boolean>(false)
  useEffect(() => {
    if (
      tab !== "sample" ||
      props.source === undefined ||
      props.catalog === undefined ||
      props.namespace === undefined ||
      props.table === undefined
    ) {
      return
    }
    const source: string = props.source
    const catalog: string = props.catalog
    const namespace: string = props.namespace
    const table: string = props.table
    const controller: AbortController = new AbortController()
    setSample(null)
    setSampleError(null)
    setSampleLoading(true)
    void fetchExplorerSample(source, catalog, namespace, table, controller.signal)
      .then((loaded: QueryResult) => {
        setSample(loaded)
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) {
          return
        }
        setSample(null)
        setSampleError(errorMessage(err))
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setSampleLoading(false)
        }
      })
    return () => {
      controller.abort()
    }
  }, [tab, props.source, props.catalog, props.namespace, props.table])
  if (
    props.source === undefined ||
    props.catalog === undefined ||
    props.namespace === undefined ||
    props.table === undefined
  ) {
    return (
      <div className="empty">
        <h1>Catalog</h1>
        <p>Select a table. Each source lists the catalogs, schemas, and tables it contains.</p>
      </div>
    )
  }

  const catalog: string = props.catalog
  const namespace: string = props.namespace
  const tableName: string = props.table
  const qualified: string = `${catalog}.${namespace}.${tableName}`
  return (
    <>
        <p className="crumb">
          {props.sourceName}
          {" / "}
          {qualified}
        </p>
        <div className="title-row">
          <h1>{tableName}</h1>
          <span className="badge">TABLE</span>
          <button
            type="button"
            className="button"
            onClick={() => {
              setSidebarView("query")
              setPendingQuerySql(sparkSampleSql(catalog, namespace, tableName))
            }}
          >
            Query
          </button>
        </div>
        <div className="tabs" role="tablist" aria-label="Table sections">
          <TabButton id="overview" current={tab} onSelect={selectTab} label="Overview" />
          <TabButton id="columns" current={tab} onSelect={selectTab} label="Columns" />
          <TabButton id="sample" current={tab} onSelect={selectTab} label="Sample Data" />
        </div>
        {props.loading ? <p className="note">Loading table…</p> : null}
        {props.error !== null ? <p className="error-text">{props.error}</p> : null}
        {props.detail !== null && tab === "overview" ? <Overview detail={props.detail} sourceName={props.sourceName} /> : null}
        {props.detail !== null && tab === "columns" ? <Columns columns={props.detail.columns} /> : null}
        {tab === "sample" ? (
          <SamplePane loading={sampleLoading} error={sampleError} result={sample} />
        ) : null}
    </>
  )
}

function TabButton(props: {
  id: DetailTab
  current: DetailTab
  label: string
  onSelect: (tab: DetailTab) => void
}): ReactElement {
  const selected: boolean = props.current === props.id
  return (
    <button
      type="button"
      role="tab"
      aria-selected={selected}
      className={selected ? "tab selected" : "tab"}
      onClick={() => {
        props.onSelect(props.id)
      }}
    >
      {props.label}
    </button>
  )
}

function Overview(props: { detail: TableDetail; sourceName: string }): ReactElement {
  return (
    <dl className="facts">
      <dt>Source</dt>
      <dd>{props.sourceName}</dd>
      <dt>Location</dt>
      <dd>{props.detail.location ?? "Unknown"}</dd>
      <dt>Snapshot</dt>
      <dd className="mono">{props.detail.snapshot_id ?? "Unknown"}</dd>
      <dt>Last updated</dt>
      <dd>{formatTimestamp(props.detail.last_updated_ms)}</dd>
    </dl>
  )
}

function SamplePane(props: {
  loading: boolean
  error: string | null
  result: QueryResult | null
}): ReactElement {
  if (props.loading) {
    return <p className="note">Loading rows…</p>
  }
  if (props.error !== null) {
    return <p className="error-text">{props.error}</p>
  }
  if (props.result === null) {
    return <p className="note">Loading rows…</p>
  }
  return <ResultGrid result={props.result} />
}

function Columns(props: { columns: ColumnDetail[] }): ReactElement {
  if (props.columns.length === 0) {
    return <p className="note">This table has no columns.</p>
  }
  return (
    <table className="columns">
      <thead>
        <tr>
          <th>Name</th>
          <th>Type</th>
          <th>Nullable</th>
          <th>Comment</th>
        </tr>
      </thead>
      <tbody>
        {props.columns.map((column: ColumnDetail) => (
          <tr key={column.name}>
            <td>{column.name}</td>
            <td className="type">{column.type}</td>
            <td>{column.required ? "No" : "Yes"}</td>
            <td className="comment">{column.comment ?? ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
