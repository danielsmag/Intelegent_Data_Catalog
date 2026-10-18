import { useSyncExternalStore } from "react"
import { UserManager, type User } from "oidc-client-ts"
import { syncFocusedTabUrl } from "./editorLayout"

export type AuthStatus = "disabled" | "loading" | "anonymous" | "signed-in"

type AuthState = {
  status: AuthStatus
  name: string
  email: string | null
}

const listeners: Set<() => void> = new Set()
let user: User | null = null
let status: AuthStatus = authEnabled() ? "loading" : "disabled"
let snapshot: AuthState = {
  status,
  name: "Signed in",
  email: null,
}
let manager: UserManager | null = null
let loginStarted: boolean = false
let callbackStarted: boolean = false
let renewInFlight: Promise<boolean> | null = null

function emit(): void {
  for (const listener of listeners) {
    listener()
  }
}

export function authEnabled(): boolean {
  return import.meta.env.VITE_AUTH_ENABLED === "true"
}

function userManager(): UserManager {
  if (manager !== null) {
    return manager
  }
  const authority: string = import.meta.env.VITE_OIDC_AUTHORITY ?? "http://localhost:8180/realms/catalog"
  const clientId: string = import.meta.env.VITE_OIDC_CLIENT_ID ?? "catalog-ui"
  const origin: string = window.location.origin
  manager = new UserManager({
    authority,
    client_id: clientId,
    redirect_uri: `${origin}/callback`,
    response_type: "code",
    scope: "openid profile email",
    automaticSilentRenew: false,
  })
  manager.events.addUserLoaded((next: User) => {
    applyUser(next)
  })
  manager.events.addUserUnloaded(() => {
    applyUser(null)
  })
  manager.events.addAccessTokenExpiring(() => {
    void renewSession()
  })
  manager.events.addAccessTokenExpired(() => {
    void renewSession()
  })
  return manager
}

function applyUser(next: User | null): void {
  user = next
  if (!authEnabled()) {
    status = "disabled"
  } else if (next === null || next.expired === true) {
    status = "anonymous"
  } else {
    status = "signed-in"
    loginStarted = false
  }
  const profile: Record<string, unknown> = next?.profile ?? {}
  const rawName: unknown = profile.name ?? profile.preferred_username
  const rawEmail: unknown = profile.email
  const nextSnapshot: AuthState = {
    status,
    name: typeof rawName === "string" && rawName !== "" ? rawName : "Signed in",
    email: typeof rawEmail === "string" ? rawEmail : null,
  }
  if (
    snapshot.status === nextSnapshot.status &&
    snapshot.name === nextSnapshot.name &&
    snapshot.email === nextSnapshot.email
  ) {
    return
  }
  snapshot = nextSnapshot
  emit()
}

export function getAccessToken(): string | null {
  if (!authEnabled() || user === null || user.expired === true) {
    return null
  }
  return user.access_token
}

export function getAuthState(): AuthState {
  return snapshot
}

export function subscribeAuth(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useAuthState(): AuthState {
  return useSyncExternalStore(subscribeAuth, getAuthState, getAuthState)
}

export async function renewSession(): Promise<boolean> {
  if (!authEnabled() || window.location.pathname === "/callback") {
    return false
  }
  if (renewInFlight !== null) {
    return renewInFlight
  }
  const pending: Promise<boolean> = refreshWithToken()
    .catch(() => false)
    .finally(() => {
      renewInFlight = null
    })
  renewInFlight = pending
  return pending
}

async function refreshWithToken(): Promise<boolean> {
  const stored: User | null = user ?? (await userManager().getUser())
  if (!hasRefreshToken(stored)) {
    return false
  }
  const next: User | null = await userManager().signinSilent()
  if (next === null) {
    return false
  }
  applyUser(next)
  return true
}

export async function ensureSignedIn(): Promise<void> {
  if (!authEnabled()) {
    applyUser(null)
    return
  }
  if (window.location.pathname === "/callback") {
    if (callbackStarted) {
      return
    }
    callbackStarted = true
    try {
      const signedIn: User = await userManager().signinRedirectCallback()
      applyUser(signedIn)
      syncFocusedTabUrl()
    } catch (err: unknown) {
      callbackStarted = false
      throw err
    }
    return
  }
  const existing: User | null = await userManager().getUser()
  if (existing !== null && existing.expired === true && hasRefreshToken(existing)) {
    const renewed: boolean = await renewSession()
    if (renewed) {
      return
    }
  }
  applyUser(existing)
}

function hasRefreshToken(account: User | null): boolean {
  return account !== null && typeof account.refresh_token === "string" && account.refresh_token !== ""
}

export async function startLogin(): Promise<void> {
  if (!authEnabled() || loginStarted || window.location.pathname === "/callback") {
    return
  }
  loginStarted = true
  try {
    await userManager().signinRedirect()
  } catch (err: unknown) {
    loginStarted = false
    throw err
  }
}

export async function signOut(): Promise<void> {
  if (!authEnabled()) {
    return
  }
  await userManager().signoutRedirect()
}
