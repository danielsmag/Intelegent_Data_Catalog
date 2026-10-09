import { useSearchParams, type SetURLSearchParams } from "react-router-dom"

export function useTabParam<Tab extends string>(
  allowed: readonly Tab[],
  fallback: Tab,
): readonly [Tab, (next: Tab) => void] {
  const [searchParams, setSearchParams]: [URLSearchParams, SetURLSearchParams] = useSearchParams()
  const raw: string | null = searchParams.get("tab")
  const tab: Tab = isAllowedTab(allowed, raw) ? raw : fallback

  function selectTab(next: Tab): void {
    const params: URLSearchParams = new URLSearchParams(searchParams)
    if (next === fallback) {
      params.delete("tab")
    } else {
      params.set("tab", next)
    }
    setSearchParams(params, { replace: true })
  }

  return [tab, selectTab]
}

function isAllowedTab<Tab extends string>(allowed: readonly Tab[], value: string | null): value is Tab {
  if (value === null) {
    return false
  }
  const names: readonly string[] = allowed
  return names.includes(value)
}
