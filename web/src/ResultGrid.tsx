import type { ReactElement } from "react"
import type { QueryCell, QueryColumn, QueryResult } from "./types"

export function ResultGrid(props: { result: QueryResult }): ReactElement {
  if (props.result.columns.length === 0) {
    return <p className="note">The query returned no columns.</p>
  }
  return (
    <div className="result-wrap">
      {props.result.truncated ? (
        <p className="note">Showing the first {props.result.rows.length} rows.</p>
      ) : null}
      <table className="columns">
        <thead>
          <tr>
            {props.result.columns.map((column: QueryColumn) => (
              <th key={column.name}>{column.name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {props.result.rows.length === 0 ? (
            <tr>
              <td className="comment" colSpan={props.result.columns.length}>
                No rows.
              </td>
            </tr>
          ) : (
            props.result.rows.map((row: QueryCell[], rowIndex: number) => (
              <tr key={rowIndex}>
                {row.map((cell: QueryCell, cellIndex: number) => (
                  <td key={`${rowIndex}-${cellIndex}`} className={cell === null ? "cell-null" : "mono"}>
                    {formatCell(cell)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}

function formatCell(value: QueryCell): string {
  if (value === null) {
    return "null"
  }
  if (typeof value === "object") {
    return JSON.stringify(value)
  }
  return String(value)
}
