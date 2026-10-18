import { getAccessToken } from "./auth"
import { requestJson } from "./http"
import { getActiveWorkspaceId } from "./workspaceStore"

export type KernelSession = {
  kernel_id: string
  ticket: string
  session_token: string
  ws_path: string
  engines: string[]
  spark_catalogs: string[]
  warnings: string[]
}

export type KernelTicket = {
  ticket: string
  ws_path: string
}

export async function startKernel(workspaceId: string, nodeId: string): Promise<KernelSession> {
  return requestJson<KernelSession>(`/api/v1/workspaces/${workspaceId}/nodes/${nodeId}/kernel`, {
    method: "POST",
  })
}

export async function stopKernel(kernelId: string): Promise<void> {
  await requestJson<void>(`/api/v1/kernels/${kernelId}`, { method: "DELETE" })
}

export async function interruptKernel(kernelId: string): Promise<void> {
  await requestJson<void>(`/api/v1/kernels/${kernelId}/interrupt`, { method: "POST" })
}

export async function restartKernel(kernelId: string): Promise<void> {
  await requestJson<void>(`/api/v1/kernels/${kernelId}/restart`, { method: "POST" })
}

export async function refreshKernelTicket(kernelId: string): Promise<KernelTicket> {
  return requestJson<KernelTicket>(`/api/v1/kernels/${kernelId}/ticket`, { method: "POST" })
}

export function stopKernelKeepalive(kernelId: string): void {
  const headers: Headers = new Headers()
  const token: string | null = getAccessToken()
  if (token !== null) {
    headers.set("Authorization", `Bearer ${token}`)
  }
  const workspaceId: string | null = getActiveWorkspaceId()
  if (workspaceId !== null) {
    headers.set("X-Workspace-Id", workspaceId)
  }
  void fetch(`/api/v1/kernels/${kernelId}`, { method: "DELETE", headers, keepalive: true })
}
