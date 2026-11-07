import { useEffect, useRef, type MutableRefObject } from "react"
import { errorMessage } from "./api"
import { showPageAlert, type PageAlertTone } from "./pageAlertStack"
import { fetchAgentBuild, startAgentBuild, type AgentBuildJob } from "./agentsApi"
import {
  fetchContextGeneration,
  fetchKnowledgeBuild,
  startContextGeneration,
  startKnowledgeBuild,
  type ContextGenerationJob,
  type ContextSubjectKind,
  type KnowledgeBuildJob,
  type TableBuildOptions,
} from "./registryApi"
import {
  fetchAirflowBuild,
  startAirflowBuild,
  type AirflowBuildJob,
  type AirflowBuildOptions,
} from "./airflowApi"

type BuildStatus = {
  id: string
  status: "running" | "succeeded" | "failed"
  error: string | null
}

export const BUILD_CANCELLED: string = "build cancelled"

export function isCancelledBuild(job: BuildStatus): boolean {
  return job.error === BUILD_CANCELLED
}

type FinishedHandler<Job extends BuildStatus> = (job: Job) => void

type BuildFollower<Job extends BuildStatus> = {
  onFinished: (handler: FinishedHandler<Job>) => () => void
  follow: (job: Job) => void
}

function createFollower<Job extends BuildStatus>(
  fetchJob: (jobId: string) => Promise<Job>,
  message: (job: Job) => string,
  operation: (job: Job) => string,
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
    const cancelled: boolean = isCancelledBuild(job)
    const tone: PageAlertTone = job.status === "succeeded" ? "success" : cancelled ? "info" : "error"
    const text: string = cancelled ? "Build cancelled." : message(job)
    showPageAlert(text, tone, operation(job))
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
          showPageAlert(errorMessage(err), "error", operation(job))
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
  (job: KnowledgeBuildJob): string => `Build context for ${job.table_name}`,
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

const airflowBuilds: BuildFollower<AirflowBuildJob> = createFollower(
  fetchAirflowBuild,
  (job: AirflowBuildJob): string => {
    const detail: string = job.error ?? "Build failed."
    if (job.status === "succeeded") {
      return `Airflow context for ${job.instance_name} is ready.`
    }
    return `Airflow context for ${job.instance_name} failed. ${detail}`
  },
  (job: AirflowBuildJob): string => `Analyze ${job.instance_name}`,
)

export function onAirflowBuildFinished(handler: FinishedHandler<AirflowBuildJob>): () => void {
  return airflowBuilds.onFinished(handler)
}

export async function requestAirflowBuild(
  instanceId: string,
  options: AirflowBuildOptions,
): Promise<AirflowBuildJob> {
  const job: AirflowBuildJob = await startAirflowBuild(instanceId, options)
  airflowBuilds.follow(job)
  return job
}

export function followAirflowBuild(job: AirflowBuildJob): void {
  airflowBuilds.follow(job)
}

function contextOperation(job: ContextGenerationJob): string {
  if (job.subject_kind === "table_columns") {
    return `Generate column contexts for ${job.subject_name}`
  }
  if (job.subject_kind === "airflow") {
    return `Analyze ${job.subject_name}`
  }
  return `Generate context for ${job.subject_name}`
}

const contextGenerations: BuildFollower<ContextGenerationJob> = createFollower(
  fetchContextGeneration,
  (job: ContextGenerationJob): string => outcome(job.subject_name, job.status, job.error),
  contextOperation,
)

export function onContextGenerationFinished(
  handler: FinishedHandler<ContextGenerationJob>,
): () => void {
  return contextGenerations.onFinished(handler)
}

const agentBuilds: BuildFollower<AgentBuildJob> = createFollower(
  fetchAgentBuild,
  (job: AgentBuildJob): string => outcome(job.agent_name, job.status, job.error),
  (job: AgentBuildJob): string => `Build agent context for ${job.agent_name}`,
)

export function onAgentBuildFinished(handler: FinishedHandler<AgentBuildJob>): () => void {
  return agentBuilds.onFinished(handler)
}

export async function requestAgentBuild(agentId: string): Promise<AgentBuildJob> {
  const job: AgentBuildJob = await startAgentBuild(agentId)
  agentBuilds.follow(job)
  return job
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
  markRunning: (job: Job) => void,
): void {
  const loadRef: MutableRefObject<(signal: AbortSignal) => Promise<Job | null>> = useRef(load)
  loadRef.current = load
  const followRef: MutableRefObject<(job: Job) => void> = useRef(follow)
  followRef.current = follow
  const markRef: MutableRefObject<(job: Job) => void> = useRef(markRunning)
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
          markRef.current(job)
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
