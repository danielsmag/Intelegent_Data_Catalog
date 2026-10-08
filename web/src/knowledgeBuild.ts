import { errorMessage } from "./api"
import {
  fetchKnowledgeBuild,
  startKnowledgeBuild,
  type KnowledgeBuildJob,
} from "./registryApi"

type FinishedHandler = (job: KnowledgeBuildJob) => void

const handlers: Set<FinishedHandler> = new Set()
const polling: Set<string> = new Set()
const announced: Set<string> = new Set()

export function onKnowledgeBuildFinished(handler: FinishedHandler): () => void {
  handlers.add(handler)
  return () => {
    handlers.delete(handler)
  }
}

export async function requestKnowledgeBuild(tableId: string): Promise<KnowledgeBuildJob> {
  const job: KnowledgeBuildJob = await startKnowledgeBuild(tableId)
  followKnowledgeBuild(job)
  return job
}

export function followKnowledgeBuild(job: KnowledgeBuildJob): void {
  if (job.status !== "running") {
    announce(job)
    return
  }
  if (polling.has(job.id)) {
    return
  }
  polling.add(job.id)
  const tick = (): void => {
    void fetchKnowledgeBuild(job.id)
      .then((current: KnowledgeBuildJob) => {
        if (current.status === "running") {
          window.setTimeout(tick, 2000)
          return
        }
        polling.delete(job.id)
        announce(current)
      })
      .catch((err: unknown) => {
        polling.delete(job.id)
        window.alert(errorMessage(err))
      })
  }
  window.setTimeout(tick, 2000)
}

function announce(job: KnowledgeBuildJob): void {
  if (announced.has(job.id)) {
    return
  }
  announced.add(job.id)
  handlers.forEach((handler: FinishedHandler) => {
    handler(job)
  })
  const detail: string = job.error ?? "Build failed."
  const message: string =
    job.status === "succeeded"
      ? `Context for ${job.table_name} is ready.`
      : `Context for ${job.table_name} failed. ${detail}`
  window.alert(message)
}
