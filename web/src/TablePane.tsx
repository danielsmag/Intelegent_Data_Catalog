import { useState, type ReactElement } from "react"
import { formatTimestamp } from "./api"
import type { ColumnDetail, DetailTab, TableDetail } from "./types"

type TablePaneProps = {
  catalog: string | undefined
  namespace: string | undefined
  table: string | undefined
  detail: TableDetail | null
  loading: boolean
  error: string | null
}

export function TablePane(props: TablePaneProps): ReactElement {
  const [tab, setTab] = useState<DetailTab>("overview")
  if (
    props.catalog === undefined ||
    props.namespace === undefined ||
    props.table === undefined
  ) {
    return (
      <section className="main">
        <div className="empty">
          <h1>Catalog</h1>
          <p>Select a table.</p>
        </div>
      </section>
    )
  }

  const qualified: string = `${props.catalog}.${props.namespace}.${props.table}`
  return (
    <section className="main">
      <div className="main-scroll">
        <p className="crumb">{qualified}</p>
        <div className="title-row">
          <h1>{props.table}</h1>
          <span className="badge">TABLE</span>
        </div>
        <div className="tabs" role="tablist" aria-label="Table sections">
          <TabButton id="overview" current={tab} onSelect={setTab} label="Overview" />
          <TabButton id="columns" current={tab} onSelect={setTab} label="Columns" />
          <TabButton id="sample" current={tab} onSelect={setTab} label="Sample Data" />
        </div>
        {props.loading ? <p className="note">Loading table…</p> : null}
        {props.error !== null ? <p className="error-text">{props.error}</p> : null}
        {props.detail !== null && tab === "overview" ? <Overview detail={props.detail} /> : null}
        {props.detail !== null && tab === "columns" ? <Columns columns={props.detail.columns} /> : null}
        {tab === "sample" ? <p className="note">Row preview is not available yet.</p> : null}
      </div>
    </section>
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

function Overview(props: { detail: TableDetail }): ReactElement {
  return (
    <dl className="facts">
      <dt>Location</dt>
      <dd>{props.detail.location ?? "Unknown"}</dd>
      <dt>Snapshot</dt>
      <dd className="mono">{props.detail.snapshot_id ?? "Unknown"}</dd>
      <dt>Last updated</dt>
      <dd>{formatTimestamp(props.detail.last_updated_ms)}</dd>
    </dl>
  )
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
