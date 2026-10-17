import { useEffect, useState, type ReactElement } from "react"
import { errorMessage } from "./api"
import { bumpCatalogRevision } from "./catalogRevision"
import type { SpaceRecord } from "./registryApi"
import { fetchSpaces } from "./registryApi"
import { useMe } from "./workspaceApi"
import { bindSpace, fetchWorkspaces, patchSpaceIsolation, unbindSpace, type WorkspaceRecord } from "./workspaceApi"

export function SpaceAccessPanel(props: { space: SpaceRecord }): ReactElement {
  const me = useMe()
  const [workspaces, setWorkspaces] = useState<WorkspaceRecord[]>([])
  const [error, setError] = useState<string | null>(null)
  const canEdit: boolean = me?.is_admin === true || me?.memberships.some((item) => item.role === "ADMIN") === true

  useEffect(() => {
    if (!canEdit) {
      return
    }
    void fetchWorkspaces()
      .then(setWorkspaces)
      .catch((err: unknown) => setError(errorMessage(err)))
  }, [canEdit])

  async function setMode(mode: "OPEN" | "ISOLATED"): Promise<void> {
    await patchSpaceIsolation(props.space.id, mode)
    bumpCatalogRevision()
  }

  return (
    <section className="panel">
      <h2>Workspace access</h2>
      <p>
        {props.space.isolation_mode === "OPEN" ? (
          <span className="badge">Shared</span>
        ) : (
          <span className="badge">Private</span>
        )}
      </p>
      {canEdit ? (
        <div className="create-actions">
          <button type="button" className="button secondary" onClick={() => void setMode("OPEN")}>
            Shared with all workspaces
          </button>
          <button type="button" className="button secondary" onClick={() => void setMode("ISOLATED")}>
            Private to bound workspaces
          </button>
        </div>
      ) : null}
      {error !== null ? <p className="error-text">{error}</p> : null}
      {canEdit && props.space.isolation_mode === "ISOLATED" ? (
        <ul>
          {workspaces.map((workspace: WorkspaceRecord) => {
            const bound: boolean = props.space.bound_workspace_ids.includes(workspace.id)
            return (
              <li key={workspace.id}>
                {workspace.name}
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => {
                    const action: Promise<void> = bound
                      ? unbindSpace(workspace.id, props.space.id)
                      : bindSpace(workspace.id, props.space.id)
                    void action.then(() => {
                      bumpCatalogRevision()
                      return fetchSpaces(new AbortController().signal)
                    })
                  }}
                >
                  {bound ? "Unbind" : "Bind"}
                </button>
              </li>
            )
          })}
        </ul>
      ) : null}
    </section>
  )
}
