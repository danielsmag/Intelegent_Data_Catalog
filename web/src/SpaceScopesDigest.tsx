import type { ReactElement } from "react"
import { Link } from "react-router-dom"
import type { ScopeRecord } from "./registryApi"

const CONTEXT_LIMIT: number = 160

export function SpaceScopesDigest(props: {
  scopes: ScopeRecord[]
  tableCounts: Map<string, number> | null
}): ReactElement {
  return (
    <section className="context-block" aria-label="Scopes">
      <h2>Scopes</h2>
      {props.scopes.length === 0 ? <p className="note">No scopes yet.</p> : null}
      <ul className="registry-list">
        {props.scopes.map((scope: ScopeRecord) => {
          const count: number | undefined = props.tableCounts?.get(scope.id)
          const shown: string = clip(scope.context)
          return (
            <li key={scope.id}>
              <Link to={`/scopes/${scope.id}`}>{scope.name}</Link>
              <span className="badge">{count === undefined ? "…" : countLabel(count)}</span>
              {scope.context_origin !== null ? (
                <span className="badge context-origin">{scope.context_origin}</span>
              ) : null}
              <span className={shown === "No context yet." ? "comment" : undefined}>{shown}</span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function clip(value: string | null): string {
  if (value === null || value.trim() === "") {
    return "No context yet."
  }
  const text: string = value.trim()
  if (text.length <= CONTEXT_LIMIT) {
    return text
  }
  return `${text.slice(0, CONTEXT_LIMIT)}…`
}

function countLabel(count: number): string {
  return count === 1 ? "1 table" : `${count} tables`
}
