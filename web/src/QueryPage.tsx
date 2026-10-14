import { useEffect, useRef, useState, type KeyboardEvent, type ReactElement, type RefObject } from "react"
import { useSearchParams } from "react-router-dom"
import { errorMessage, isAbortError, runQuery } from "./api"
import { useEditorStatus, useEditorTitle } from "./editorSlots"
import { ResultGrid } from "./ResultGrid"
import type { QueryResult } from "./types"

const DEFAULT_SQL: string = "SELECT * FROM catalog.sales.customers LIMIT 100"

export function QueryPage(): ReactElement {
  const [searchParams] = useSearchParams()
  const fromUrl: string | null = searchParams.get("sql")
  const [sql, setSql] = useState<string>(fromUrl !== null && fromUrl !== "" ? fromUrl : DEFAULT_SQL)
  const [result, setResult] = useState<QueryResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState<boolean>(false)
  const [runId, setRunId] = useState<number>(0)
  const sqlRef: RefObject<string> = useRef<string>(sql)
  sqlRef.current = sql

  useEffect(() => {
    if (fromUrl !== null && fromUrl !== "") {
      setSql(fromUrl)
      setResult(null)
      setError(null)
    }
  }, [fromUrl])

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

  useEditorTitle("Query")
  useEditorStatus("Spark SQL", loading ? "Running" : "Ready")
  return (
    <div className="query-layout">
      <h1>Query</h1>
      <textarea
        className="query-editor"
        aria-label="SQL"
        spellCheck={false}
        value={sql}
        onChange={(event) => {
          setSql(event.target.value)
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
