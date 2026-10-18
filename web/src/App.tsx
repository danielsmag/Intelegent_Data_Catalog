import { useEffect, useState, type ReactElement, type ReactNode } from "react"
import { authEnabled, ensureSignedIn, startLogin, useAuthState } from "./auth"
import { EditorWorkbench } from "./EditorWorkbench"
import { BrandMark } from "./icons"
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
  const [loginError, setLoginError] = useState<string | null>(null)
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
    return <SignInScreen busy />
  }
  if (auth.status === "anonymous") {
    return (
      <SignInScreen
        error={loginError}
        onSignIn={() => {
          setLoginError(null)
          void startLogin().catch((err: unknown) => {
            const message: string = err instanceof Error ? err.message : "Sign-in could not start."
            setLoginError(message)
          })
        }}
      />
    )
  }
  return <>{props.children}</>
}

function SignInScreen(props: {
  busy?: boolean
  error?: string | null
  onSignIn?: () => void
}): ReactElement {
  const busy: boolean = props.busy === true
  return (
    <main className="sign-in">
      <section className="sign-in-card" aria-busy={busy}>
        <div className="sign-in-mark" aria-hidden="true">
          <BrandMark />
        </div>
        <p className="sign-in-product">Intelegent Data Catalog</p>
        <h1>Sign in</h1>
        <p className="sign-in-copy">
          Use your organization account to open spaces, catalogs, and workspace files.
        </p>
        {busy ? (
          <p className="sign-in-status">Checking your session…</p>
        ) : (
          <button type="button" className="button sign-in-button" onClick={props.onSignIn}>
            Continue to Keycloak
          </button>
        )}
        {props.error ? (
          <p className="sign-in-error" role="alert">
            {props.error}
          </p>
        ) : null}
        <p className="sign-in-footnote">Sign-in is handled by Keycloak. This application does not store your password.</p>
      </section>
    </main>
  )
}
