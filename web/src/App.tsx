import type { ReactElement } from "react"
import { BrowserRouter, Route, Routes } from "react-router-dom"
import { DataSourcesPage, RegistryTablePage, ScopePage, SearchPage, SpacePage, SpacesPage } from "./RegistryPages"
import { Explorer } from "./Explorer"
import { QueryPage } from "./QueryPage"

export function App(): ReactElement {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<SpacesPage />} />
        <Route path="/iceberg" element={<Explorer />} />
        <Route path="/catalog/:catalog/:namespace/:table" element={<Explorer />} />
        <Route path="/query" element={<QueryPage />} />
        <Route path="/spaces" element={<SpacesPage />} />
        <Route path="/spaces/:spaceId" element={<SpacePage />} />
        <Route path="/scopes/:scopeId" element={<ScopePage />} />
        <Route path="/registry/tables/:tableId" element={<RegistryTablePage />} />
        <Route path="/search" element={<SearchPage />} />
        <Route path="/sources" element={<DataSourcesPage />} />
      </Routes>
    </BrowserRouter>
  )
}
