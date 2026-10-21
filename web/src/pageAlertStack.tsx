import { useEffect, useState, type ReactElement } from "react"

export type PageAlertTone = "success" | "error" | "info"

export type PageAlert = {
  id: string
  operation: string
  message: string
  tone: PageAlertTone
  at: string
}

type PageAlertListener = (alerts: readonly PageAlert[]) => void

const HISTORY_LIMIT: number = 80

let visible: readonly PageAlert[] = []
let history: readonly PageAlert[] = []
const visibleListeners: Set<PageAlertListener> = new Set()
const historyListeners: Set<PageAlertListener> = new Set()

function publishVisible(): void {
  const snapshot: readonly PageAlert[] = visible
  visibleListeners.forEach((listener: PageAlertListener) => {
    listener(snapshot)
  })
}

function publishHistory(): void {
  const snapshot: readonly PageAlert[] = history
  historyListeners.forEach((listener: PageAlertListener) => {
    listener(snapshot)
  })
}

export function showPageAlert(
  message: string,
  tone: PageAlertTone = "info",
  operation: string = "",
): string {
  const alert: PageAlert = {
    id: crypto.randomUUID(),
    operation,
    message,
    tone,
    at: new Date().toISOString(),
  }
  visible = [...visible, alert]
  history = [alert, ...history].slice(0, HISTORY_LIMIT)
  publishVisible()
  publishHistory()
  return alert.id
}

export function dismissPageAlert(id: string): void {
  visible = visible.filter((alert: PageAlert) => alert.id !== id)
  publishVisible()
}

function subscribePageAlerts(listener: PageAlertListener): () => void {
  visibleListeners.add(listener)
  listener(visible)
  return () => {
    visibleListeners.delete(listener)
  }
}

export function subscribePageAlertHistory(listener: PageAlertListener): () => void {
  historyListeners.add(listener)
  listener(history)
  return () => {
    historyListeners.delete(listener)
  }
}

export function AlertPanel(): ReactElement {
  const [history, setHistory] = useState<readonly PageAlert[]>([])

  useEffect(() => subscribePageAlertHistory(setHistory), [])

  return (
    <div className="trace-panel">
      <div className="trace-feed">
        {history.length === 0 ? (
          <div className="trace-empty">
            <p>Alerts appear here when a build finishes.</p>
          </div>
        ) : (
          history.map((alert: PageAlert) => <AlertCard key={alert.id} alert={alert} />)
        )}
      </div>
    </div>
  )
}

function AlertCard(props: { alert: PageAlert }): ReactElement {
  const operation: string = props.alert.operation !== "" ? props.alert.operation : "Alert"
  return (
    <article className={`trace-turn trace-alert trace-alert-${props.alert.tone}`}>
      <div className="trace-bubble">
        <span className="trace-bubble-text">{operation}</span>
        <time className="trace-bubble-time" dateTime={props.alert.at}>
          {formatClock(props.alert.at)}
        </time>
      </div>
      <p className="trace-prose">{props.alert.message}</p>
    </article>
  )
}

export function PageAlertStack(): ReactElement {
  const [current, setCurrent] = useState<readonly PageAlert[]>([])

  useEffect(() => subscribePageAlerts(setCurrent), [])

  return (
    <div className="page-alerts" aria-live="polite">
      {current.map((alert: PageAlert) => (
        <article key={alert.id} className={`page-alert page-alert-${alert.tone}`}>
          <div className="page-alert-body">
            {alert.operation !== "" ? (
              <div className="page-alert-head">
                <p className="page-alert-operation">{alert.operation}</p>
                <time className="page-alert-time" dateTime={alert.at}>
                  {formatClock(alert.at)}
                </time>
              </div>
            ) : null}
            <p className="page-alert-message">{alert.message}</p>
          </div>
          <button
            type="button"
            className="page-alert-dismiss"
            aria-label="Dismiss alert"
            onClick={() => {
              dismissPageAlert(alert.id)
            }}
          >
            ×
          </button>
        </article>
      ))}
    </div>
  )
}

function formatClock(iso: string): string {
  const date: Date = new Date(iso)
  if (Number.isNaN(date.getTime())) {
    return ""
  }
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
}
