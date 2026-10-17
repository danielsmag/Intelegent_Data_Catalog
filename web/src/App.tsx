import { useEffect, type ReactElement, type ReactNode } from "react"
import { authEnabled, ensureSignedIn, startLogin, useAuthState } from "./auth"
import { EditorWorkbench } from "./EditorWorkbench"
import { PageAlertStack } from "./pageAlertStack"
import { loadMe } from "./workspaceApi"

export function App(): ReactElement {
  return (
    <AuthGate>
      <PageAlertStack />
      <EditorWorkbench />
    </AuthGate>
  )
}

function AuthGate(props: { children: ReactNode }): ReactElement {
  const auth = useAuthState()
  useEffect(() => {
    void ensureSignedIn()
  }, [])
  useEffect(() => {
    if (!authEnabled() || auth.status === "signed-in") {
      void loadMe()
    }
  }, [auth.status])
  if (!authEnabled()) {
    return <>{props.children}</>
  }
  if (auth.status === "loading") {
    return <p className="note">Signing in…</p>
  }
  if (auth.status === "anonymous") {
    return (
      <main className="page">
        <h1>Sign in</h1>
        <button type="button" className="button" onClick={() => void startLogin()}>
          Continue to Keycloak
        </button>
      </main>
    )
  }
  return <>{props.children}</>
}
