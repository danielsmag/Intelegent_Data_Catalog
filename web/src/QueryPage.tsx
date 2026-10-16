import { useEffect, useRef, useState, type KeyboardEvent, type ReactElement, type RefObject } from "react"
import { errorMessage, isAbortError, runQuery } from "./api"
import { ResultGrid } from "./ResultGrid"
import { peekQueryDraft, rememberQueryDraft, takePendingQuerySql, usePendingQuerySql } from "./sidebarView"
import type { QueryResult } from "./types"

const DEFAULT_SQL: string = "SELECT * FROM catalog.sales.customers LIMIT 100"

export function QueryPage(): ReactElement {
  const pending = usePendingQuerySql()
  const [sql, setSql] = useState<string>(() => peekQueryDraft() ?? pending?.sql ?? DEFAULT_SQL)
  const [result, setResult] = useState<QueryResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState<boolean>(false)
  const [runId, setRunId] = useState<number>(0)
  const sqlRef: RefObject<string> = useRef<string>(sql)
  sqlRef.current = sql

  useEffect(() => {
    const next: { sql: string; token: number } | null = takePendingQuerySql()
    if (next === null || next.sql === "") {
      return
    }
    setSql(next.sql)
    rememberQueryDraft(next.sql)
    setResult(null)
    setError(null)
  }, [pending])

  useEffect(() => {
    if (runId === 0) {
      return
    }
    const controller: AbortController = new AbortController()
    const statement: string = sqlRef.current
    setLoading(true)
    setError(null)
    void runQuery(statement, controller.signal)
      .then((loaded: QueryResult) => {
        setResult(loaded)
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) {
          return
        }
        setResult(null)
        setError(errorMessage(err))
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false)
        }
      })
    return () => {
      controller.abort()
    }
  }, [runId])

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault()
      setRunId((current: number) => current + 1)
    }
  }

  return (
    <div className="query-layout">
      <h1>Query</h1>
      <textarea
        className="query-editor"
        aria-label="SQL"
        spellCheck={false}
        value={sql}
        onChange={(event) => {
          const next: string = event.target.value
          setSql(next)
          rememberQueryDraft(next)
        }}
        onKeyDown={onKeyDown}
      />
      <div className="query-actions">
        <button
          type="button"
          className="button"
          disabled={loading || sql.trim() === ""}
          onClick={() => {
            setRunId((current: number) => current + 1)
          }}
        >
          {loading ? "Running…" : "Run"}
        </button>
        <span className="note">Ctrl/Cmd+Enter</span>
      </div>
      {error !== null ? <p className="error-text">{error}</p> : null}
      {result !== null ? <ResultGrid result={result} /> : null}
    </div>
  )
}
