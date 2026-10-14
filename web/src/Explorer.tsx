import { useEffect, useState, type ReactElement } from "react"
import { useParams } from "react-router-dom"
import { errorMessage, fetchExplorerTable, isAbortError } from "./api"
import { useExplorerTree } from "./CatalogTree"
import { useEditorStatus, useEditorTitle } from "./editorSlots"
import { TablePane } from "./TablePane"
import type { ExplorerSource, TableDetail } from "./types"

export function CatalogPage(): ReactElement {
  const params: Readonly<Record<string, string | undefined>> = useParams()
  const source: string | undefined = params.source
  const catalog: string | undefined = params.catalog
  const namespace: string | undefined = params.namespace
  const table: string | undefined = params.table
  const tree = useExplorerTree()
  const [detail, setDetail] = useState<TableDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [detailLoading, setDetailLoading] = useState<boolean>(false)
  const sourceName: string = tree?.sources.find((item: ExplorerSource) => item.id === source)?.name ?? source ?? "Catalog"
  const qualified: string | null =
    source !== undefined && catalog !== undefined && namespace !== undefined && table !== undefined
      ? `${sourceName} / ${catalog}.${namespace}.${table}`
      : null
  useEditorTitle(table ?? "Catalog")
  useEditorStatus("Catalog", qualified ?? "No table selected")

  useEffect(() => {
    if (source === undefined || catalog === undefined || namespace === undefined || table === undefined) {
      setDetail(null)
      setDetailError(null)
      setDetailLoading(false)
      return
    }
    const controller: AbortController = new AbortController()
    setDetail(null)
    setDetailError(null)
    setDetailLoading(true)
    void fetchExplorerTable(source, catalog, namespace, table, controller.signal)
      .then((loaded: TableDetail) => {
        setDetail(loaded)
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) {
          return
        }
        setDetail(null)
        setDetailError(errorMessage(err))
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setDetailLoading(false)
        }
      })
    return () => {
      controller.abort()
    }
  }, [source, catalog, namespace, table])

  return (
    <TablePane
      key={qualified ?? "none"}
      source={source}
      sourceName={sourceName}
      catalog={catalog}
      namespace={namespace}
      table={table}
      detail={detail}
      loading={detailLoading}
      error={detailError}
    />
  )
}
