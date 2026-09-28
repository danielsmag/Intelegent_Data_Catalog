import type { ReactElement } from "react"
import { BrowserRouter, Route, Routes } from "react-router-dom"
import { Explorer } from "./Explorer"

export function App(): ReactElement {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Explorer />} />
        <Route path="/catalog/:catalog/:namespace/:table" element={<Explorer />} />
      </Routes>
    </BrowserRouter>
  )
}
