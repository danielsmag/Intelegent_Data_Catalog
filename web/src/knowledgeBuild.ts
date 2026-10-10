import { useEffect, useRef, type MutableRefObject } from "react"
import { errorMessage } from "./api"
import { showPageAlert, type PageAlertTone } from "./pageAlertStack"
import {
  fetchContextGeneration,
  fetchKnowledgeBuild,
  fetchScopeBuild,
  fetchSpaceBuild,
  startContextGeneration,
  startKnowledgeBuild,
  startScopeBuild,
  startSpaceBuild,
  type ContextGenerationJob,
  type ContextSubjectKind,
  type KnowledgeBuildJob,
  type ScopeBuildJob,
  type ScopeBuildOptions,
  type SpaceBuildJob,
  type SpaceBuildOptions,
  type TableBuildOptions,
} from "./registryApi"

type BuildStatus = {
  id: string
  status: "running" | "succeeded" | "failed"
  error: string | null
}

type FinishedHandler<Job extends BuildStatus> = (job: Job) => void

type BuildFollower<Job extends BuildStatus> = {
  onFinished: (handler: FinishedHandler<Job>) => () => void
  follow: (job: Job) => void
}

function createFollower<Job extends BuildStatus>(
  fetchJob: (jobId: string) => Promise<Job>,
  message: (job: Job) => string,
): BuildFollower<Job> {
  const handlers: Set<FinishedHandler<Job>> = new Set()
  const polling: Set<string> = new Set()
  const announced: Set<string> = new Set()

  function onFinished(handler: FinishedHandler<Job>): () => void {
    handlers.add(handler)
    return () => {
      handlers.delete(handler)
    }
  }

  function announce(job: Job): void {
    if (announced.has(job.id)) {
      return
    }
    announced.add(job.id)
    handlers.forEach((handler: FinishedHandler<Job>) => {
      handler(job)
    })
    const tone: PageAlertTone = job.status === "succeeded" ? "success" : "error"
    showPageAlert(message(job), tone)
  }

  function follow(job: Job): void {
    if (job.status !== "running") {
      announce(job)
      return
    }
    if (polling.has(job.id)) {
      return
    }
    polling.add(job.id)
    const tick = (): void => {
      void fetchJob(job.id)
        .then((current: Job) => {
          if (current.status === "running") {
            window.setTimeout(tick, 2000)
            return
          }
          polling.delete(job.id)
          announce(current)
        })
        .catch((err: unknown) => {
          polling.delete(job.id)
          showPageAlert(errorMessage(err), "error")
        })
    }
    window.setTimeout(tick, 2000)
  }

  return { onFinished, follow }
}

function outcome(name: string, status: BuildStatus["status"], error: string | null): string {
  const detail: string = error ?? "Build failed."
  if (status === "succeeded") {
    return `Context for ${name} is ready.`
  }
  return `Context for ${name} failed. ${detail}`
}

const tableBuilds: BuildFollower<KnowledgeBuildJob> = createFollower(
  fetchKnowledgeBuild,
  (job: KnowledgeBuildJob): string => outcome(job.table_name, job.status, job.error),
)

const scopeBuilds: BuildFollower<ScopeBuildJob> = createFollower(
  fetchScopeBuild,
  (job: ScopeBuildJob): string => {
    const detail: string = job.error ?? "Build failed."
    if (job.status === "succeeded") {
      return `Scope context for ${job.scope_name} is ready.`
    }
    return `Scope context for ${job.scope_name} failed. ${detail}`
  },
)

export function onKnowledgeBuildFinished(handler: FinishedHandler<KnowledgeBuildJob>): () => void {
  return tableBuilds.onFinished(handler)
}

export async function requestKnowledgeBuild(
  tableId: string,
  options: TableBuildOptions,
): Promise<KnowledgeBuildJob> {
  const job: KnowledgeBuildJob = await startKnowledgeBuild(tableId, options)
  tableBuilds.follow(job)
  return job
}

export function followKnowledgeBuild(job: KnowledgeBuildJob): void {
  tableBuilds.follow(job)
}

export function onScopeBuildFinished(handler: FinishedHandler<ScopeBuildJob>): () => void {
  return scopeBuilds.onFinished(handler)
}

export async function requestScopeBuild(
  scopeId: string,
  options: ScopeBuildOptions,
): Promise<ScopeBuildJob> {
  const job: ScopeBuildJob = await startScopeBuild(scopeId, options)
  scopeBuilds.follow(job)
  return job
}

export function followScopeBuild(job: ScopeBuildJob): void {
  scopeBuilds.follow(job)
}

const spaceBuilds: BuildFollower<SpaceBuildJob> = createFollower(
  fetchSpaceBuild,
  (job: SpaceBuildJob): string => {
    const detail: string = job.error ?? "Build failed."
    if (job.status === "succeeded") {
      return `Space context for ${job.space_name} is ready.`
    }
    return `Space context for ${job.space_name} failed. ${detail}`
  },
)

export function onSpaceBuildFinished(handler: FinishedHandler<SpaceBuildJob>): () => void {
  return spaceBuilds.onFinished(handler)
}

export async function requestSpaceBuild(
  spaceId: string,
  options: SpaceBuildOptions,
): Promise<SpaceBuildJob> {
  const job: SpaceBuildJob = await startSpaceBuild(spaceId, options)
  spaceBuilds.follow(job)
  return job
}

export function followSpaceBuild(job: SpaceBuildJob): void {
  spaceBuilds.follow(job)
}

const contextGenerations: BuildFollower<ContextGenerationJob> = createFollower(
  fetchContextGeneration,
  (job: ContextGenerationJob): string => outcome(job.subject_name, job.status, job.error),
)

export function onContextGenerationFinished(
  handler: FinishedHandler<ContextGenerationJob>,
): () => void {
  return contextGenerations.onFinished(handler)
}

export async function requestContextGeneration(
  kind: ContextSubjectKind,
  id: string,
): Promise<ContextGenerationJob> {
  const job: ContextGenerationJob = await startContextGeneration(kind, id)
  contextGenerations.follow(job)
  return job
}

export function followContextGeneration(job: ContextGenerationJob): void {
  contextGenerations.follow(job)
}

export function useRunningJobWatch<Job extends BuildStatus>(
  enabled: boolean,
  load: (signal: AbortSignal) => Promise<Job | null>,
  follow: (job: Job) => void,
  markRunning: () => void,
): void {
  const loadRef: MutableRefObject<(signal: AbortSignal) => Promise<Job | null>> = useRef(load)
  loadRef.current = load
  const followRef: MutableRefObject<(job: Job) => void> = useRef(follow)
  followRef.current = follow
  const markRef: MutableRefObject<() => void> = useRef(markRunning)
  markRef.current = markRunning
  useEffect(() => {
    if (!enabled) {
      return
    }
    let stopped: boolean = false
    let inflight: AbortController | null = null
    const timer: number = window.setInterval(() => {
      inflight?.abort()
      const controller: AbortController = new AbortController()
      inflight = controller
      void loadRef
        .current(controller.signal)
        .then((job: Job | null) => {
          if (stopped || controller.signal.aborted || job === null || job.status !== "running") {
            return
          }
          markRef.current()
          followRef.current(job)
        })
        .catch(() => {
          return
        })
    }, 2000)
    return () => {
      stopped = true
      inflight?.abort()
      window.clearInterval(timer)
    }
  }, [enabled])
}
