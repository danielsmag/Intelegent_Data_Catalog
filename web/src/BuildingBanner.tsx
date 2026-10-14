import { useState, type ReactElement } from "react"
import { errorMessage } from "./api"
import { cancelCatalogJob } from "./registryApi"

type BuildingBannerProps = {
  jobId: string | null
  onCancelled: () => void
}

export function BuildingBanner({ jobId, onCancelled }: BuildingBannerProps): ReactElement {
  const [cancelling, setCancelling] = useState<boolean>(false)
  const [cancelError, setCancelError] = useState<string | null>(null)

  function runCancel(): void {
    if (jobId === null || cancelling) {
      return
    }
    setCancelling(true)
    setCancelError(null)
    void cancelCatalogJob(jobId)
      .then(() => {
        onCancelled()
      })
      .catch((err: unknown) => {
        setCancelError(errorMessage(err))
      })
      .finally(() => {
        setCancelling(false)
      })
  }

  return (
    <p className="comment build-progress">
      <span>Building in the background. Keep using the catalog. An alert appears when it finishes.</span>
      <button
        type="button"
        className="button secondary"
        disabled={jobId === null || cancelling}
        onClick={runCancel}
      >
        {cancelling ? "Cancelling…" : "Cancel"}
      </button>
      {cancelError !== null ? <span className="error-text">{cancelError}</span> : null}
    </p>
  )
}
