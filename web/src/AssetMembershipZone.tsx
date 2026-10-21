import { useEffect, useState, type ReactElement } from "react"
import { attachAssets, fetchScopeAssets, fetchSpaceAssets, type AssetRecord } from "./assetsApi"
import { registerDropZone, useEditorDrag } from "./editorDrag"
import { openInFocusedGroup } from "./editorLayout"
import { showPageAlert } from "./pageAlertStack"
import { bumpCatalogRevision, useCatalogRevision } from "./catalogRevision"

export function AssetMembershipZone(props: {
  targetKind: "scope" | "space"
  targetId: string
}): ReactElement {
  const revision: number = useCatalogRevision()
  const [assets, setAssets] = useState<AssetRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const drag = useEditorDrag()
  const zoneId: string = `${props.targetKind}:${props.targetId}`
  const active: boolean = drag?.target?.kind === "zone" && drag.target.zoneId === zoneId
  useEffect(() => {
    return registerDropZone(zoneId, (assetIds: string[]) => {
      void attachAssets(props.targetKind, props.targetId, assetIds)
        .then((result) => {
          if (result.changed === 0) {
            showPageAlert("Already attached", "info", "Assets")
          } else {
            showPageAlert(`Attached ${result.changed}`, "success", "Assets")
          }
          bumpCatalogRevision()
        })
        .catch((err: unknown) => {
          showPageAlert(err instanceof Error ? err.message : "Could not attach", "error", "Assets")
        })
    })
  }, [props.targetId, props.targetKind, zoneId])
  useEffect(() => {
    const controller: AbortController = new AbortController()
    const load: Promise<AssetRecord[]> =
      props.targetKind === "scope"
        ? fetchScopeAssets(props.targetId, controller.signal)
        : fetchSpaceAssets(props.targetId, controller.signal)
    void load
      .then((rows: AssetRecord[]) => {
        setAssets(rows)
        setError(null)
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "Could not load assets")
        }
      })
    return () => {
      controller.abort()
    }
  }, [props.targetId, props.targetKind, revision])
  return (
    <div className={active ? "drop-zone drop-zone--active" : "drop-zone"} data-drop-zone={zoneId}>
      <p className="note">Drop assets here to attach them.</p>
      {error !== null ? <p className="error-text">{error}</p> : null}
      {assets !== null && assets.length === 0 ? <p className="note">No assets attached.</p> : null}
      <ul className="asset-membership-list">
        {(assets ?? []).map((asset: AssetRecord) => (
          <li key={asset.id}>
            <button
              type="button"
              onClick={() => {
                openInFocusedGroup(`/assets/${asset.id}`)
              }}
            >
              <span className="filter-chip">{asset.kind}</span> {asset.name}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
