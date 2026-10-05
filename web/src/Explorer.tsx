import { useEffect, useState, type ReactElement } from "react"
import { useParams } from "react-router-dom"
import { ActivityBar } from "./ActivityBar"
import { errorMessage, fetchExplorerTable, fetchExplorerTree, isAbortError } from "./api"
import { CatalogTreePane } from "./CatalogTree"
import { TablePane } from "./TablePane"
import type { ExplorerTree, TableDetail } from "./types"

export function Explorer(): ReactElement {
  const params: Readonly<Record<string, string | undefined>> = useParams()
  const source: string | undefined = params.source
  const catalog: string | undefined = params.catalog
  const namespace: string | undefined = params.namespace
  const table: string | undefined = params.table
  const [tree, setTree] = useState<ExplorerTree | null>(null)
  const [treeError, setTreeError] = useState<string | null>(null)
  const [treeLoading, setTreeLoading] = useState<boolean>(true)
  const [query, setQuery] = useState<string>("")
  const [reload, setReload] = useState<number>(0)
  const [detail, setDetail] = useState<TableDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [detailLoading, setDetailLoading] = useState<boolean>(false)

  useEffect(() => {
    const controller: AbortController = new AbortController()
    setTreeLoading(true)
    setTreeError(null)
    void fetchExplorerTree(controller.signal)
      .then((loaded: ExplorerTree) => {
        setTree(loaded)
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) {
          return
        }
        setTree(null)
        setTreeError(errorMessage(err))
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setTreeLoading(false)
        }
      })
    return () => {
      controller.abort()
    }
  }, [reload])

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

  const qualified: string | null =
    source !== undefined && catalog !== undefined && namespace !== undefined && table !== undefined
      ? `${source} / ${catalog}.${namespace}.${table}`
      : null
  const sourceName: string =
    tree?.sources.find((item) => item.id === source)?.name ?? source ?? "Catalog"

  return (
    <div className="shell">
      <div className="workspace">
        <ActivityBar />
        <CatalogTreePane
          tree={tree}
          loading={treeLoading}
          error={treeError}
          query={query}
          onQueryChange={setQuery}
          onRetry={() => {
            setReload((current: number) => current + 1)
          }}
          selectedSource={source}
          selectedCatalog={catalog}
          selectedNamespace={namespace}
          selectedTable={table}
        />
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
      </div>
      <footer className="status">
        <span>Catalog</span>
        <span>{qualified ?? "No table selected"}</span>
      </footer>
    </div>
  )
}
