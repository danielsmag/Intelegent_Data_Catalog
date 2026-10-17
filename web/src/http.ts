import { getAccessToken, authEnabled, startLogin } from "./auth"
import { getActiveWorkspaceId } from "./workspaceStore"

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers: Headers = new Headers(init.headers)
  const token: string | null = getAccessToken()
  if (token !== null && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`)
  }
  const workspaceId: string | null = getActiveWorkspaceId()
  if (workspaceId !== null && !headers.has("X-Workspace-Id")) {
    headers.set("X-Workspace-Id", workspaceId)
  }
  const response: Response = await fetch(path, { ...init, headers })
  if (response.status === 401 && authEnabled()) {
    await startLogin()
  }
  return response
}

export async function readDetail(response: Response): Promise<string> {
  try {
    const payload: unknown = await response.json()
    if (typeof payload === "object" && payload !== null && "detail" in payload) {
      const detail: unknown = payload.detail
      if (typeof detail === "string" && detail !== "") {
        return detail
      }
      if (Array.isArray(detail)) {
        const messages: string[] = []
        for (const item of detail) {
          if (typeof item === "object" && item !== null && "msg" in item) {
            const message: unknown = item.msg
            if (typeof message === "string") {
              messages.push(message)
            }
          }
        }
        if (messages.length > 0) {
          return messages.join("; ")
        }
      }
    }
  } catch {
    return response.statusText || "request failed"
  }
  return response.statusText || "request failed"
}

export async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response: Response = await apiFetch(path, init)
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}
