import { requestJson } from "./http"

export type AssetDefinitionObject = {
  key: string
  path: string
  size_bytes: number
  last_modified: string | null
  file_id: string | null
  asset_ids: string[]
}

export type AssetDefinitionItem = {
  id: string
  kind: string
  item_key: string
  status: string
  error: string | null
  asset_id: string | null
}

export type AssetDefinitionFileDetail = {
  file: {
    id: string
    key: string
    status: string
    error: string | null
    item_count: number
  }
  source: string
  items: AssetDefinitionItem[]
}

export type AssetDefinitionText = {
  key: string
  source: string
}

export function fetchAssetDefinitionFile(
  fileId: string,
  signal?: AbortSignal,
): Promise<AssetDefinitionFileDetail> {
  return requestJson<AssetDefinitionFileDetail>(`/api/v1/asset-definitions/files/${fileId}`, { signal })
}

export function fetchAssetDefinitionText(key: string, signal?: AbortSignal): Promise<AssetDefinitionText> {
  return requestJson<AssetDefinitionText>(
    `/api/v1/asset-definitions/text?key=${encodeURIComponent(key)}`,
    { signal },
  )
}

export function fetchAssetDefinitionObjects(
  folder: "assets",
  signal?: AbortSignal,
): Promise<AssetDefinitionObject[]> {
  return requestJson<AssetDefinitionObject[]>(
    `/api/v1/asset-definitions/objects?folder=${folder}`,
    { signal },
  )
}
