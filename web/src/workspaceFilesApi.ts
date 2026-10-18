import { apiFetch, readDetail, requestJson } from "./http"

export type WorkspaceNode = {
  id: string
  workspace_id: string
  parent_id: string | null
  name: string
  path: string
  kind: "FOLDER" | "FILE"
  content_type: string | null
  size_bytes: number | null
  owner_user_id: string | null
  created_at: string
  updated_at: string
}

export async function fetchNodes(workspaceId: string, parentId: string | null): Promise<WorkspaceNode[]> {
  const query: string = parentId === null ? "" : `?parent_id=${encodeURIComponent(parentId)}`
  return requestJson<WorkspaceNode[]>(`/api/v1/workspaces/${workspaceId}/nodes${query}`)
}

export async function createFolder(workspaceId: string, parentId: string, name: string): Promise<WorkspaceNode> {
  return requestJson<WorkspaceNode>(`/api/v1/workspaces/${workspaceId}/nodes/folders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parent_id: parentId, name }),
  })
}

export async function createTextFile(
  workspaceId: string,
  parentId: string,
  name: string,
  text: string,
): Promise<WorkspaceNode> {
  return requestJson<WorkspaceNode>(`/api/v1/workspaces/${workspaceId}/nodes/files/text`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parent_id: parentId, name, text }),
  })
}

export async function uploadFile(workspaceId: string, parentId: string, file: File): Promise<WorkspaceNode> {
  const body: FormData = new FormData()
  body.set("parent_id", parentId)
  body.set("file", file)
  const response: Response = await apiFetch(`/api/v1/workspaces/${workspaceId}/nodes/files`, {
    method: "POST",
    body,
  })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return (await response.json()) as WorkspaceNode
}

export async function fetchNode(workspaceId: string, nodeId: string): Promise<WorkspaceNode> {
  return requestJson<WorkspaceNode>(`/api/v1/workspaces/${workspaceId}/nodes/${nodeId}`)
}

export async function fetchNodeBlob(workspaceId: string, nodeId: string): Promise<Blob> {
  const response: Response = await apiFetch(`/api/v1/workspaces/${workspaceId}/nodes/${nodeId}/content`)
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  return response.blob()
}

export async function saveNodeText(workspaceId: string, nodeId: string, text: string): Promise<WorkspaceNode> {
  return requestJson<WorkspaceNode>(`/api/v1/workspaces/${workspaceId}/nodes/${nodeId}/content`, {
    method: "PUT",
    headers: { "Content-Type": "text/plain; charset=utf-8" },
    body: text,
  })
}

export type NodeLabel = {
  id: string
  name: string
  path: string
}

const labelListeners: Set<(label: NodeLabel) => void> = new Set()

export function subscribeNodeLabel(listener: (label: NodeLabel) => void): () => void {
  labelListeners.add(listener)
  return () => {
    labelListeners.delete(listener)
  }
}

export function publishNodeLabel(node: WorkspaceNode): void {
  const label: NodeLabel = { id: node.id, name: node.name, path: node.path }
  for (const listener of labelListeners) {
    listener(label)
  }
}

export async function moveNode(
  workspaceId: string,
  nodeId: string,
  patch: { name?: string; parent_id?: string },
): Promise<WorkspaceNode> {
  return requestJson<WorkspaceNode>(`/api/v1/workspaces/${workspaceId}/nodes/${nodeId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  })
}

export async function deleteNode(workspaceId: string, nodeId: string): Promise<void> {
  await requestJson<void>(`/api/v1/workspaces/${workspaceId}/nodes/${nodeId}`, { method: "DELETE" })
}
