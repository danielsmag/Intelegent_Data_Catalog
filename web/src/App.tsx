import type { ReactElement } from "react"
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"
import { Explorer } from "./Explorer"
import { DataSourcesPage, RegistryTablePage, ScopePage, SearchPage, SpacePage, SpacesPage } from "./RegistryPages"
import { QueryPage } from "./QueryPage"

export function App(): ReactElement {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<SpacesPage />} />
        <Route path="/catalog" element={<Explorer />} />
        <Route path="/catalog/:source/:catalog/:namespace/:table" element={<Explorer />} />
        <Route path="/query" element={<QueryPage />} />
        <Route path="/spaces" element={<SpacesPage />} />
        <Route path="/spaces/:spaceId" element={<SpacePage />} />
        <Route path="/scopes/:scopeId" element={<ScopePage />} />
        <Route path="/registry/tables/:tableId" element={<RegistryTablePage />} />
        <Route path="/search" element={<SearchPage />} />
        <Route path="/sources" element={<DataSourcesPage />} />
        <Route path="/iceberg" element={<Navigate to="/catalog" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
