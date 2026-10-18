import { useSyncExternalStore } from "react"
import type { CellOutput } from "./notebookFormat"
import { refreshKernelTicket } from "./notebookApi"

export type KernelPhase = "idle" | "busy" | "starting" | "dead"

export type ExecuteResult = {
  outputs: CellOutput[]
  executionCount: number | null
  status: "ok" | "error"
}

type Listener = () => void

type JupyterMessage = {
  header?: { msg_id?: string; msg_type?: string }
  parent_header?: { msg_id?: string }
  content?: Record<string, unknown>
  channel?: string
}

const EXECUTE_TIMEOUT_MS: number = 300_000

export class KernelConnection {
  readonly kernelId: string
  private ticket: string
  private readonly wsPath: string
  private socket: WebSocket | null = null
  private current: KernelPhase = "starting"
  private readonly listeners: Set<Listener> = new Set()
  private readonly pending: Map<string, PendingExecution> = new Map()
  private readonly session: string = crypto.randomUUID()
  private intentional: boolean = false
  private attempts: number = 0

  constructor(kernelId: string, ticket: string, wsPath: string) {
    this.kernelId = kernelId
    this.ticket = ticket
    this.wsPath = wsPath
  }

  phase(): KernelPhase {
    return this.current
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  connect(): void {
    this.intentional = false
    this.openSocket()
  }

  ready(): Promise<void> {
    const started: number = Date.now()
    return new Promise<void>((resolve, reject) => {
      const tick = (): void => {
        if (this.socket?.readyState === WebSocket.OPEN) {
          resolve()
          return
        }
        if (this.intentional || this.current === "dead" || Date.now() - started > 15_000) {
          reject(new Error("kernel is not connected"))
          return
        }
        window.setTimeout(tick, 50)
      }
      tick()
    })
  }

  close(): void {
    this.intentional = true
    this.failPending("kernel closed")
    this.socket?.close()
    this.socket = null
    this.setPhase("dead")
  }

  execute(code: string): Promise<ExecuteResult> {
    const socket: WebSocket | null = this.socket
    if (socket === null || socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error("kernel is not connected"))
    }
    const msgId: string = crypto.randomUUID()
    const pending: PendingExecution = new PendingExecution()
    this.pending.set(msgId, pending)
    socket.send(
      JSON.stringify({
        header: {
          msg_id: msgId,
          session: this.session,
          username: "notebook",
          date: new Date().toISOString(),
          msg_type: "execute_request",
          version: "5.3",
        },
        parent_header: {},
        metadata: {},
        content: {
          code,
          silent: false,
          store_history: true,
          user_expressions: {},
          allow_stdin: false,
          stop_on_error: true,
        },
        channel: "shell",
      }),
    )
    this.setPhase("busy")
    return pending.promise
  }

  private openSocket(): void {
    this.setPhase("starting")
    const protocol: string = window.location.protocol === "https:" ? "wss:" : "ws:"
    const url: string = `${protocol}//${window.location.host}${this.wsPath}?ticket=${encodeURIComponent(this.ticket)}`
    const socket: WebSocket = new WebSocket(url)
    this.socket = socket
    socket.onopen = () => {
      this.attempts = 0
      if (this.current === "starting") {
        this.setPhase("idle")
      }
    }
    socket.onmessage = (event: MessageEvent) => {
      if (typeof event.data !== "string") {
        return
      }
      this.onFrame(event.data)
    }
    socket.onclose = () => {
      if (this.socket !== socket) {
        return
      }
      this.socket = null
      if (this.intentional) {
        this.setPhase("dead")
        return
      }
      void this.reconnect()
    }
    socket.onerror = () => {
      socket.close()
    }
  }

  private async reconnect(): Promise<void> {
    if (this.intentional || this.attempts >= 1) {
      this.failPending("kernel disconnected")
      this.setPhase("dead")
      return
    }
    this.attempts += 1
    try {
      const refreshed = await refreshKernelTicket(this.kernelId)
      this.ticket = refreshed.ticket
      this.openSocket()
    } catch {
      this.failPending("kernel disconnected")
      this.setPhase("dead")
    }
  }

  private onFrame(raw: string): void {
    let message: JupyterMessage
    try {
      message = JSON.parse(raw) as JupyterMessage
    } catch {
      return
    }
    const msgType: string = message.header?.msg_type ?? ""
    const parentId: string = message.parent_header?.msg_id ?? ""
    const content: Record<string, unknown> = message.content ?? {}
    if (msgType === "status") {
      const state: unknown = content.execution_state
      if (state === "busy" || state === "idle" || state === "starting") {
        this.setPhase(state)
      }
      return
    }
    const execution: PendingExecution | undefined = this.pending.get(parentId)
    if (execution === undefined) {
      return
    }
    if (msgType === "stream") {
      execution.outputs.push({
        output_type: "stream",
        name: content.name === "stderr" ? "stderr" : "stdout",
        text: joinText(content.text),
      })
      return
    }
    if (msgType === "error") {
      execution.outputs.push(errorOutput(content))
      return
    }
    if (msgType === "execute_result" || msgType === "display_data") {
      execution.outputs.push({
        output_type: msgType,
        execution_count: typeof content.execution_count === "number" ? content.execution_count : null,
        data: isRecord(content.data) ? content.data : {},
        metadata: isRecord(content.metadata) ? content.metadata : {},
      })
      return
    }
    if (msgType === "execute_reply") {
      const status: "ok" | "error" = content.status === "error" ? "error" : "ok"
      if (status === "error" && !execution.outputs.some((output) => output.output_type === "error")) {
        execution.outputs.push(errorOutput(content))
      }
      const executionCount: number | null =
        typeof content.execution_count === "number" ? content.execution_count : null
      this.pending.delete(parentId)
      execution.finish({ outputs: execution.outputs, executionCount, status })
    }
  }

  private failPending(message: string): void {
    for (const execution of this.pending.values()) {
      execution.fail(new Error(message))
    }
    this.pending.clear()
  }

  private setPhase(phase: KernelPhase): void {
    if (this.current === phase) {
      return
    }
    this.current = phase
    for (const listener of this.listeners) {
      listener()
    }
  }
}

class PendingExecution {
  readonly outputs: CellOutput[] = []
  readonly promise: Promise<ExecuteResult>
  private resolvePromise: ((result: ExecuteResult) => void) | null = null
  private rejectPromise: ((error: Error) => void) | null = null

  constructor() {
    this.promise = new Promise<ExecuteResult>((resolve, reject) => {
      this.resolvePromise = resolve
      this.rejectPromise = reject
    })
    const timer: ReturnType<typeof setTimeout> = setTimeout(() => {
      this.fail(new Error("cell timed out"))
    }, EXECUTE_TIMEOUT_MS)
    void this.promise.finally(() => {
      clearTimeout(timer)
    })
  }

  finish(result: ExecuteResult): void {
    this.resolvePromise?.(result)
  }

  fail(error: Error): void {
    this.rejectPromise?.(error)
  }
}

export function useKernelPhase(connection: KernelConnection | null): KernelPhase {
  return useSyncExternalStore(
    (listener: () => void) => (connection === null ? () => undefined : connection.subscribe(listener)),
    () => (connection === null ? "dead" : connection.phase()),
    () => "dead",
  )
}

function errorOutput(content: Record<string, unknown>): CellOutput {
  const traceback: string[] = []
  if (Array.isArray(content.traceback)) {
    for (const line of content.traceback) {
      if (typeof line === "string") {
        traceback.push(line)
      }
    }
  }
  return {
    output_type: "error",
    ename: typeof content.ename === "string" ? content.ename : "Error",
    evalue: typeof content.evalue === "string" ? content.evalue : joinText(content.evalue),
    traceback,
  }
}

function joinText(value: unknown): string {
  if (typeof value === "string") {
    return value
  }
  if (!Array.isArray(value)) {
    return ""
  }
  return value.filter((part: unknown): part is string => typeof part === "string").join("")
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}
