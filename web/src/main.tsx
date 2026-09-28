import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { App } from "./App"
import "./styles.css"

const rootElement: HTMLElement | null = document.querySelector("#root")
if (rootElement === null) {
  throw new Error("root element not found")
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
