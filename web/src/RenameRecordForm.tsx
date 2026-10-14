import { useState, type FormEvent, type ReactElement } from "react"
import { errorMessage } from "./api"

type RenameRecordFormProps = {
  idPrefix: string
  nameLabel: string
  initialName: string
  compact?: boolean
  onCancel: () => void
  onRename: (name: string) => Promise<void>
}

export function RenameRecordForm(props: RenameRecordFormProps): ReactElement {
  const [name, setName] = useState<string>(props.initialName)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState<boolean>(false)
  const trimmedName: string = name.trim()
  const unchanged: boolean = trimmedName === props.initialName.trim()
  const formClass: string = props.compact ? "create-form compact" : "create-form"

  function onSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (trimmedName === "" || unchanged || pending) {
      return
    }
    setPending(true)
    setError(null)
    void props.onRename(trimmedName).catch((err: unknown) => {
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
      {error !== null ? <p className="error-text">{error}</p> : null}
      <div className="create-actions">
        <button className="button" type="submit" disabled={pending || trimmedName === "" || unchanged}>
          {pending ? "Saving…" : "Rename"}
        </button>
        <button className="button secondary" type="button" disabled={pending} onClick={props.onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}
