import { useEffect, useState, type FormEvent, type ReactElement } from "react"
import { useParams } from "react-router-dom"
import { errorMessage } from "./api"
import { openInFocusedGroup } from "./editorLayout"
import {
  addMember,
  createWorkspace,
  fetchMembers,
  fetchWorkspaces,
  removeMember,
  useMe,
  type WorkspaceMember,
  type WorkspaceRecord,
} from "./workspaceApi"

export function WorkspacesPage(): ReactElement {
  const me = useMe()
  const workspaceId: string | undefined = useParams().workspaceId
  const [workspaces, setWorkspaces] = useState<WorkspaceRecord[]>([])
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState<string>("")

  useEffect(() => {
    void fetchWorkspaces()
      .then(setWorkspaces)
      .catch((err: unknown) => setError(errorMessage(err)))
  }, [])

  async function onCreate(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    const created: WorkspaceRecord = await createWorkspace(name.trim(), null)
    setName("")
    setWorkspaces(await fetchWorkspaces())
    openInFocusedGroup(`/workspaces/${created.id}`)
  }

  return (
    <section className="page">
      <header className="page-header">
        <h1>Workspaces</h1>
      </header>
      {error !== null ? <p className="error-text">{error}</p> : null}
      <ul className="entity-list">
        {workspaces.map((workspace: WorkspaceRecord) => (
          <li key={workspace.id}>
            <button type="button" className="linkish" onClick={() => openInFocusedGroup(`/workspaces/${workspace.id}`)}>
              {workspace.name}
            </button>
            {workspace.is_system ? <span className="badge">Shared</span> : null}
          </li>
        ))}
      </ul>
      {me?.is_admin === true ? (
        <form className="create-form" onSubmit={(event) => void onCreate(event)}>
          <label>
            New workspace
            <input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <button className="button" type="submit" disabled={name.trim() === ""}>
            Create
          </button>
        </form>
      ) : null}
      {workspaceId !== undefined ? <Members workspaceId={workspaceId} /> : null}
    </section>
  )
}

function Members(props: { workspaceId: string }): ReactElement {
  const [members, setMembers] = useState<WorkspaceMember[]>([])
  const [email, setEmail] = useState<string>("")
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void fetchMembers(props.workspaceId)
      .then(setMembers)
      .catch((err: unknown) => setError(errorMessage(err)))
  }, [props.workspaceId])

  async function onAdd(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    await addMember(props.workspaceId, email.trim())
    setEmail("")
    setMembers(await fetchMembers(props.workspaceId))
  }

  return (
    <section>
      <h2>Members</h2>
      {error !== null ? <p className="error-text">{error}</p> : null}
      <ul>
        {members.map((member: WorkspaceMember) => (
          <li key={member.user_id}>
            {member.display_name} {member.email ?? ""} ({member.role})
            <button
              type="button"
              className="button secondary"
              onClick={() => {
                void removeMember(props.workspaceId, member.user_id).then(() =>
                  fetchMembers(props.workspaceId).then(setMembers),
                )
              }}
            >
              Remove
            </button>
          </li>
        ))}
      </ul>
      <form className="create-form" onSubmit={(event) => void onAdd(event)}>
        <label>
          Email
          <input value={email} onChange={(event) => setEmail(event.target.value)} />
        </label>
        <button className="button" type="submit" disabled={email.trim() === ""}>
          Add member
        </button>
      </form>
    </section>
  )
}
