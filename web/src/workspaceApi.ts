import { useSyncExternalStore } from "react"
import { requestJson } from "./http"
import { getActiveWorkspaceId, setActiveWorkspaceId } from "./workspaceStore"

export type Membership = {
  workspace_id: string
  name: string
  slug: string
  role: "ADMIN" | "MEMBER"
  is_system: boolean
}

export type Me = {
  subject: string
  email: string | null
  name: string
  user_id: string
  is_admin: boolean
  workspace_id: string | null
  memberships: Membership[]
}

export type WorkspaceRecord = {
  id: string
  name: string
  slug: string
  description: string | null
  is_system: boolean
  role: "ADMIN" | "MEMBER" | null
  created_at: string
  updated_at: string
}

export type WorkspaceMember = {
  user_id: string
  email: string | null
  display_name: string
  role: "ADMIN" | "MEMBER"
  created_at: string
}

const listeners: Set<() => void> = new Set()
let me: Me | null = null

function emit(): void {
  for (const listener of listeners) {
    listener()
  }
}

export function getMe(): Me | null {
  return me
}

export function subscribeMe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useMe(): Me | null {
  return useSyncExternalStore(subscribeMe, getMe, () => null)
}

export async function loadMe(): Promise<Me> {
  const loaded: Me = await requestJson<Me>("/api/v1/me")
  me = loaded
  emit()
  const ids: string[] = loaded.memberships.map((item: Membership) => item.workspace_id)
  const current: string | null = getActiveWorkspaceId()
  if (current === null || !ids.includes(current)) {
    const shared: Membership | undefined = loaded.memberships.find((item: Membership) => item.is_system)
    const next: string | null = shared?.workspace_id ?? loaded.memberships[0]?.workspace_id ?? null
    if (next !== current) {
      setActiveWorkspaceId(next)
    }
  }
  return loaded
}

export async function fetchWorkspaces(): Promise<WorkspaceRecord[]> {
  return requestJson<WorkspaceRecord[]>("/api/v1/workspaces")
}

export async function createWorkspace(name: string, description: string | null): Promise<WorkspaceRecord> {
  const body: { name: string; description?: string } = { name }
  if (description !== null) {
    body.description = description
  }
  return requestJson<WorkspaceRecord>("/api/v1/workspaces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
}

export async function fetchMembers(workspaceId: string): Promise<WorkspaceMember[]> {
  return requestJson<WorkspaceMember[]>(`/api/v1/workspaces/${workspaceId}/members`)
}

export async function addMember(workspaceId: string, email: string): Promise<WorkspaceMember> {
  return requestJson<WorkspaceMember>(`/api/v1/workspaces/${workspaceId}/members`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, role: "MEMBER" }),
  })
}

export async function removeMember(workspaceId: string, userId: string): Promise<void> {
  await requestJson<void>(`/api/v1/workspaces/${workspaceId}/members/${userId}`, { method: "DELETE" })
}

export async function fetchBoundSpaces(workspaceId: string): Promise<string[]> {
  return requestJson<string[]>(`/api/v1/workspaces/${workspaceId}/spaces`)
}

export async function bindSpace(workspaceId: string, spaceId: string): Promise<void> {
  await requestJson<void>(`/api/v1/workspaces/${workspaceId}/spaces/${spaceId}`, { method: "PUT" })
}

export async function unbindSpace(workspaceId: string, spaceId: string): Promise<void> {
  await requestJson<void>(`/api/v1/workspaces/${workspaceId}/spaces/${spaceId}`, { method: "DELETE" })
}

export async function patchSpaceIsolation(
  spaceId: string,
  isolationMode: "OPEN" | "ISOLATED",
): Promise<void> {
  await requestJson<unknown>(`/api/v1/spaces/${spaceId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ isolation_mode: isolationMode }),
  })
}
