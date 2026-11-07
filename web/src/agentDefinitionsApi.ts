import { requestJson } from "./http"

export type AgentDefinitionObject = {
  key: string
  path: string
  size_bytes: number
  last_modified: string | null
  file_id: string | null
  agent_ids: string[]
}

export type AgentDefinitionItem = {
  id: string
  kind: string
  item_key: string
  status: string
  error: string | null
  agent_id: string | null
}

export type AgentDefinitionFileDetail = {
  file: {
    id: string
    key: string
    status: string
    error: string | null
    item_count: number
  }
  source: string
  items: AgentDefinitionItem[]
}

export type AgentDefinitionText = {
  key: string
  source: string
}

export function fetchAgentDefinitionFile(
  fileId: string,
  signal?: AbortSignal,
): Promise<AgentDefinitionFileDetail> {
  return requestJson<AgentDefinitionFileDetail>(`/api/v1/agent-definitions/files/${fileId}`, { signal })
}

export function fetchAgentDefinitionText(key: string, signal?: AbortSignal): Promise<AgentDefinitionText> {
  return requestJson<AgentDefinitionText>(
    `/api/v1/agent-definitions/text?key=${encodeURIComponent(key)}`,
    { signal },
  )
}

export function fetchAgentDefinitionObjects(
  folder: "agents",
  signal?: AbortSignal,
): Promise<AgentDefinitionObject[]> {
  return requestJson<AgentDefinitionObject[]>(
    `/api/v1/agent-definitions/objects?folder=${folder}`,
    { signal },
  )
}
