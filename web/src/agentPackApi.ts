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

export function fetchMemory(agentId: string, signal?: AbortSignal): Promise<MemoryFile[]> {
  return requestJson<MemoryFile[]>(`/api/v1/agents/${agentId}/agent-memory`, { signal })
}

export function fetchMemoryFile(agentId: string, name: string): Promise<MemoryFile> {
  return requestJson<MemoryFile>(
    `/api/v1/agents/${agentId}/agent-memory/${encodeURIComponent(name)}`,
  )
}

export function fetchAgentPack(agentId: string, signal?: AbortSignal): Promise<AgentPack> {
  return requestJson<AgentPack>(`/api/v1/agents/${agentId}/agent-pack`, { signal })
}

export function fetchAgentCard(agentId: string): Promise<AgentCard> {
  return requestJson<AgentCard>(`/api/v1/agents/${agentId}/agent-pack/agent`)
}

export function saveAgentCard(agentId: string, body: string, layer?: string): Promise<AgentCard> {
  return requestJson<AgentCard>(`/api/v1/agents/${agentId}/agent-pack/agent${layerQuery(layer)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body }),
  })
}

export function fetchPrompt(agentId: string, slug: string): Promise<PromptFile> {
  return requestJson<PromptFile>(`/api/v1/agents/${agentId}/agent-pack/prompts/${slug}`)
}

export function savePrompt(
  agentId: string,
  slug: string,
  file: Pick<PromptFile, "name" | "description" | "enabled" | "active" | "body">,
  layer?: string,
): Promise<PromptFile> {
  return requestJson<PromptFile>(`/api/v1/agents/${agentId}/agent-pack/prompts/${slug}${layerQuery(layer)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(file),
  })
}

export function deletePrompt(agentId: string, slug: string, layer?: string): Promise<void> {
  return requestJson<void>(`/api/v1/agents/${agentId}/agent-pack/prompts/${slug}${layerQuery(layer)}`, {
    method: "DELETE",
  })
}

export function fetchRule(agentId: string, slug: string): Promise<RuleFile> {
  return requestJson<RuleFile>(`/api/v1/agents/${agentId}/agent-pack/rules/${slug}`)
}

export function saveRule(
  agentId: string,
  slug: string,
  file: Pick<RuleFile, "name" | "description" | "enabled" | "order" | "body">,
  layer?: string,
): Promise<RuleFile> {
  return requestJson<RuleFile>(`/api/v1/agents/${agentId}/agent-pack/rules/${slug}${layerQuery(layer)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(file),
  })
}

export function deleteRule(agentId: string, slug: string, layer?: string): Promise<void> {
  return requestJson<void>(`/api/v1/agents/${agentId}/agent-pack/rules/${slug}${layerQuery(layer)}`, {
    method: "DELETE",
  })
}

export function fetchSkill(agentId: string, slug: string): Promise<SkillFile> {
  return requestJson<SkillFile>(`/api/v1/agents/${agentId}/agent-pack/skills/${slug}`)
}

export function saveSkill(
  agentId: string,
  slug: string,
  file: Pick<SkillFile, "name" | "description" | "enabled" | "tools" | "body">,
  layer?: string,
): Promise<SkillFile> {
  return requestJson<SkillFile>(`/api/v1/agents/${agentId}/agent-pack/skills/${slug}${layerQuery(layer)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(file),
  })
}

export function deleteSkill(agentId: string, slug: string, layer?: string): Promise<void> {
  return requestJson<void>(`/api/v1/agents/${agentId}/agent-pack/skills/${slug}${layerQuery(layer)}`, {
    method: "DELETE",
  })
}

function layerQuery(layer?: string): string {
  if (layer === undefined || layer === "") {
    return ""
  }
  return `?layer=${encodeURIComponent(layer)}`
}
