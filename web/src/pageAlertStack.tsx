import { useEffect, useState, type ReactElement } from "react"

export type PageAlertTone = "success" | "error" | "info"

export type PageAlert = {
  id: string
  message: string
  tone: PageAlertTone
}

type PageAlertListener = (alerts: readonly PageAlert[]) => void

let alerts: readonly PageAlert[] = []
const listeners: Set<PageAlertListener> = new Set()

function publish(): void {
  const snapshot: readonly PageAlert[] = alerts
  listeners.forEach((listener: PageAlertListener) => {
    listener(snapshot)
  })
}

export function showPageAlert(message: string, tone: PageAlertTone = "info"): string {
  const alert: PageAlert = {
    id: crypto.randomUUID(),
    message,
    tone,
  }
  alerts = [...alerts, alert]
  publish()
  return alert.id
}

export function dismissPageAlert(id: string): void {
  alerts = alerts.filter((alert: PageAlert) => alert.id !== id)
  publish()
}

function subscribePageAlerts(listener: PageAlertListener): () => void {
  listeners.add(listener)
  listener(alerts)
  return () => {
    listeners.delete(listener)
  }
}

export function PageAlertStack(): ReactElement {
  const [current, setCurrent] = useState<readonly PageAlert[]>([])

  useEffect(() => subscribePageAlerts(setCurrent), [])

  return (
    <div className="page-alerts" aria-live="polite">
      {current.map((alert: PageAlert) => (
        <article key={alert.id} className={`page-alert page-alert-${alert.tone}`}>
          <p className="page-alert-message">{alert.message}</p>
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
