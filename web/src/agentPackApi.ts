import { requestJson } from "./http"

export type PackLayer = {
  name: string
  prefix: string
}

export type PackLimits = {
  max_files: number
  single_active_prompt: boolean
}

export type AgentCard = {
  path: string
  present: boolean
  layer: string
  updated_at: string | null
  body?: string
}

export type PromptFile = {
  slug: string
  name: string
  description: string
  enabled: boolean
  active: boolean
  layer: string
  updated_at: string | null
  body?: string
}

export type RuleFile = {
  slug: string
  name: string
  description: string
  enabled: boolean
  order: number
  layer: string
  updated_at: string | null
  body?: string
}

export type SkillReference = {
  name: string
  body: string
}

export type SkillFile = {
  slug: string
  name: string
  description: string
  enabled: boolean
  tools: string[]
  layer: string
  updated_at: string | null
  body?: string
  references?: SkillReference[]
}

export type AgentPack = {
  prefix: string | null
  writable: boolean
  message: string | null
  layers: PackLayer[]
  limits: PackLimits
  agent: AgentCard
  prompts: PromptFile[]
  rules: RuleFile[]
  skills: SkillFile[]
}

export type MemoryFile = {
  name: string
  updated_at: string | null
  body?: string
  updated_by?: string | null
}

export function fetchMemory(assetId: string, signal?: AbortSignal): Promise<MemoryFile[]> {
  return requestJson<MemoryFile[]>(`/api/v1/assets/${assetId}/agent-memory`, { signal })
}

export function fetchMemoryFile(assetId: string, name: string): Promise<MemoryFile> {
  return requestJson<MemoryFile>(
    `/api/v1/assets/${assetId}/agent-memory/${encodeURIComponent(name)}`,
  )
}

export function fetchAgentPack(assetId: string, signal?: AbortSignal): Promise<AgentPack> {
  return requestJson<AgentPack>(`/api/v1/assets/${assetId}/agent-pack`, { signal })
}

export function fetchAgentCard(assetId: string): Promise<AgentCard> {
  return requestJson<AgentCard>(`/api/v1/assets/${assetId}/agent-pack/agent`)
}

export function saveAgentCard(assetId: string, body: string, layer?: string): Promise<AgentCard> {
  return requestJson<AgentCard>(`/api/v1/assets/${assetId}/agent-pack/agent${layerQuery(layer)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body }),
  })
}

export function fetchPrompt(assetId: string, slug: string): Promise<PromptFile> {
  return requestJson<PromptFile>(`/api/v1/assets/${assetId}/agent-pack/prompts/${slug}`)
}

export function savePrompt(
  assetId: string,
  slug: string,
  file: Pick<PromptFile, "name" | "description" | "enabled" | "active" | "body">,
  layer?: string,
): Promise<PromptFile> {
  return requestJson<PromptFile>(`/api/v1/assets/${assetId}/agent-pack/prompts/${slug}${layerQuery(layer)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(file),
  })
}

export function deletePrompt(assetId: string, slug: string, layer?: string): Promise<void> {
  return requestJson<void>(`/api/v1/assets/${assetId}/agent-pack/prompts/${slug}${layerQuery(layer)}`, {
    method: "DELETE",
  })
}

export function fetchRule(assetId: string, slug: string): Promise<RuleFile> {
  return requestJson<RuleFile>(`/api/v1/assets/${assetId}/agent-pack/rules/${slug}`)
}

export function saveRule(
  assetId: string,
  slug: string,
  file: Pick<RuleFile, "name" | "description" | "enabled" | "order" | "body">,
  layer?: string,
): Promise<RuleFile> {
  return requestJson<RuleFile>(`/api/v1/assets/${assetId}/agent-pack/rules/${slug}${layerQuery(layer)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(file),
  })
}

export function deleteRule(assetId: string, slug: string, layer?: string): Promise<void> {
  return requestJson<void>(`/api/v1/assets/${assetId}/agent-pack/rules/${slug}${layerQuery(layer)}`, {
    method: "DELETE",
  })
}

export function fetchSkill(assetId: string, slug: string): Promise<SkillFile> {
  return requestJson<SkillFile>(`/api/v1/assets/${assetId}/agent-pack/skills/${slug}`)
}

export function saveSkill(
  assetId: string,
  slug: string,
  file: Pick<SkillFile, "name" | "description" | "enabled" | "tools" | "body">,
  layer?: string,
): Promise<SkillFile> {
  return requestJson<SkillFile>(`/api/v1/assets/${assetId}/agent-pack/skills/${slug}${layerQuery(layer)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(file),
  })
}

export function deleteSkill(assetId: string, slug: string, layer?: string): Promise<void> {
  return requestJson<void>(`/api/v1/assets/${assetId}/agent-pack/skills/${slug}${layerQuery(layer)}`, {
    method: "DELETE",
  })
}

function layerQuery(layer?: string): string {
  if (layer === undefined || layer === "") {
    return ""
  }
  return `?layer=${encodeURIComponent(layer)}`
}
