import { useState, type FormEvent, type ReactElement } from "react"
import { errorMessage } from "./api"
import type { NamedCreate } from "./registryApi"

type CreateRecordFormProps = {
  idPrefix: string
  nameLabel: string
  submitLabel: string
  compact?: boolean
  showVisibility?: boolean
  onCancel: () => void
  onCreate: (input: NamedCreate) => Promise<void>
}

export function CreateRecordForm(props: CreateRecordFormProps): ReactElement {
  const [name, setName] = useState<string>("")
  const [description, setDescription] = useState<string>("")
  const [visibility, setVisibility] = useState<"OPEN" | "ISOLATED">("ISOLATED")
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState<boolean>(false)
  const trimmedName: string = name.trim()
  const formClass: string = props.compact ? "create-form compact" : "create-form"

  function onSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (trimmedName === "" || pending) {
      return
    }
    const descriptionText: string = description.trim()
    const input: NamedCreate = {
      name: trimmedName,
      description: descriptionText === "" ? null : descriptionText,
    }
    if (props.showVisibility === true) {
      input.isolation_mode = visibility
    }
    setPending(true)
    setError(null)
    void props
      .onCreate(input)
      .catch((err: unknown) => {
        setError(errorMessage(err))
        setPending(false)
      })
  }

  return (
    <form className={formClass} onSubmit={onSubmit}>
      <label htmlFor={`${props.idPrefix}-name`}>
        {props.nameLabel}
        <input
          id={`${props.idPrefix}-name`}
          value={name}
          autoFocus
          disabled={pending}
          maxLength={255}
          onChange={(event) => {
            setName(event.target.value)
          }}
        />
      </label>
      <label htmlFor={`${props.idPrefix}-description`}>
        Description
        <textarea
          id={`${props.idPrefix}-description`}
          value={description}
          disabled={pending}
          maxLength={16000}
          rows={props.compact ? 2 : 3}
          onChange={(event) => {
            setDescription(event.target.value)
          }}
        />
      </label>
      {props.showVisibility === true ? (
        <fieldset className="visibility-choice">
          <legend>Visibility</legend>
          <label>
            <input
              type="radio"
              name={`${props.idPrefix}-visibility`}
              checked={visibility === "ISOLATED"}
              onChange={() => setVisibility("ISOLATED")}
            />
            Private to this workspace
          </label>
          <label>
            <input
              type="radio"
              name={`${props.idPrefix}-visibility`}
              checked={visibility === "OPEN"}
              onChange={() => setVisibility("OPEN")}
            />
            Shared with all workspaces
          </label>
        </fieldset>
      ) : null}
      {error !== null ? <p className="error-text">{error}</p> : null}
      <div className="create-actions">
        <button className="button" type="submit" disabled={pending || trimmedName === ""}>
          {pending ? "Saving…" : props.submitLabel}
        </button>
        <button className="button secondary" type="button" disabled={pending} onClick={props.onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}
