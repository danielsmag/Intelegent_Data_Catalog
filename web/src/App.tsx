import type { ReactElement } from "react"
import { PageAlertStack } from "./pageAlertStack"
import { EditorWorkbench } from "./EditorWorkbench"

export function App(): ReactElement {
  return (
    <>
      <PageAlertStack />
      <EditorWorkbench />
    </>
  )
}
