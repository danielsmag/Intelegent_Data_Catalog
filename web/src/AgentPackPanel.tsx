import { useEffect, useState, type ReactElement } from "react"
import ReactMarkdown from "react-markdown"
import {
  fetchAgentCard,
  fetchAgentPack,
  fetchMemory,
  fetchMemoryFile,
  fetchPrompt,
  fetchRule,
  fetchSkill,
  type AgentPack,
  type MemoryFile,
  type PromptFile,
  type RuleFile,
  type SkillFile,
  type SkillReference,
} from "./agentPackApi"

export function AgentPackPanel(props: { assetId: string; assetKind: string }): ReactElement {
  const [pack, setPack] = useState<AgentPack | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [memoryFiles, setMemoryFiles] = useState<MemoryFile[] | null>(null)
  const [memoryError, setMemoryError] = useState<string | null>(null)

  useEffect(() => {
    const controller: AbortController = new AbortController()
    void fetchAgentPack(props.assetId, controller.signal)
      .then((loaded: AgentPack) => {
        setPack(loaded)
        setError(null)
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "Could not load agent files")
        }
      })
    return () => {
      controller.abort()
    }
  }, [props.assetId])

  useEffect(() => {
    const controller: AbortController = new AbortController()
    void fetchMemory(props.assetId, controller.signal)
      .then((loaded: MemoryFile[]) => {
        setMemoryFiles(loaded)
        setMemoryError(null)
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setMemoryError(err instanceof Error ? err.message : "Could not load memory")
        }
      })
    return () => {
      controller.abort()
    }
  }, [props.assetId])

  return (
    <div className="asset-sheet agent-pack">
      {error !== null ? <p className="error-text">{error}</p> : null}
      {pack === null ? <p className="catalog-lead">Loading agent files…</p> : null}
      {pack !== null ? (
        <p className="agent-note">
          Edit these files in VS Code. This page only shows them.
          {pack.prefix !== null ? <span className="agent-path">{pack.prefix}</span> : null}
        </p>
      ) : null}
      {pack !== null && !pack.writable ? (
        <section className="asset-card agent-section agent-section--agent">
          <SectionHead tone="agent" title="Agent" lead={pack.message ?? "This asset has no definition folder yet."} />
        </section>
      ) : null}
      {pack !== null && pack.writable ? (
        <>
          <section className="asset-card agent-section agent-section--agent">
            <SectionHead tone="agent" title="Agent" lead="AGENTS.md is loaded on every run." />
            {pack.agent.present ? (
              <FileButton
                name="AGENTS.md"
                description="Agent card"
                onOpen={() => {
                  void fetchAgentCard(props.assetId).then((card) => {
                    setPreview({ title: "AGENTS.md", tone: "agent", body: card.body ?? "", references: [] })
                  })
                }}
              />
            ) : (
              <p className="catalog-lead">No AGENTS.md yet.</p>
            )}
          </section>
          <section className="asset-card agent-section agent-section--prompts">
            <SectionHead tone="prompts" title="Prompts" lead="The active prompt is the task for a run." />
            {pack.prompts.length === 0 ? <p className="catalog-lead">No prompts yet.</p> : null}
            {pack.prompts.map((row: PromptFile) => (
              <FileButton
                key={row.slug}
                name={row.name}
                description={row.description}
                chips={promptChips(row)}
                onOpen={() => {
                  void fetchPrompt(props.assetId, row.slug).then((file: PromptFile) => {
                    setPreview({ title: file.name, tone: "prompts", body: file.body ?? "", references: [] })
                  })
                }}
              />
            ))}
          </section>
          <section className="asset-card agent-section agent-section--rules">
            <SectionHead tone="rules" title="Rules" lead="Enabled rules are added on every run." />
            {pack.rules.length === 0 ? <p className="catalog-lead">No rules yet.</p> : null}
            {pack.rules.map((row: RuleFile) => (
              <FileButton
                key={row.slug}
                name={row.name}
                description={row.description}
                chips={ruleChips(row)}
                onOpen={() => {
                  void fetchRule(props.assetId, row.slug).then((file: RuleFile) => {
                    setPreview({ title: file.name, tone: "rules", body: file.body ?? "", references: [] })
                  })
                }}
              />
            ))}
          </section>
          <section className="asset-card agent-section agent-section--skills">
            <SectionHead tone="skills" title="Skills" lead="Enabled skills name the tools a run may use." />
            {pack.skills.length === 0 ? <p className="catalog-lead">No skills yet.</p> : null}
            {pack.skills.map((row: SkillFile) => (
              <FileButton
                key={row.slug}
                name={row.name}
                description={row.description}
                chips={skillChips(row)}
                onOpen={() => {
                  void fetchSkill(props.assetId, row.slug).then((file: SkillFile) => {
                    setPreview({
                      title: file.name,
                      tone: "skills",
                      body: file.body ?? "",
                      references: file.references ?? [],
                    })
                  })
                }}
              />
            ))}
          </section>
        </>
      ) : null}
      <section className="asset-card agent-section agent-section--memory">
        <SectionHead tone="memory" title="Memory" lead="Written by the agent. Loaded on the next asset run." />
        {memoryError !== null ? <p className="error-text">{memoryError}</p> : null}
        {memoryFiles === null && memoryError === null ? <p className="catalog-lead">Loading memory…</p> : null}
        {memoryFiles !== null && memoryFiles.length === 0 ? <p className="catalog-lead">No memory yet.</p> : null}
        {memoryFiles !== null
          ? memoryFiles.map((file: MemoryFile) => (
              <FileButton
                key={file.name}
                name={file.name}
                description={file.updated_by === "agent" ? "Written by the agent" : ""}
                onOpen={() => {
                  void fetchMemoryFile(props.assetId, file.name).then((loaded: MemoryFile) => {
                    setPreview({
                      title: loaded.name,
                      tone: "memory",
                      body: loaded.body ?? "",
                      references: [],
                    })
                  })
                }}
              />
            ))
          : null}
      </section>
      {preview !== null ? (
        <aside className={`agent-drawer agent-drawer--${preview.tone}`} aria-label="Markdown preview">
          <header>
            <h2>{preview.title}</h2>
            <button type="button" className="button secondary" onClick={() => setPreview(null)}>
              Close
            </button>
          </header>
          <MarkdownView body={preview.body} />
          {preview.references.map((reference: SkillReference) => (
            <section key={reference.name}>
              <h3>{reference.name}</h3>
              <MarkdownView body={reference.body} />
            </section>
          ))}
        </aside>
      ) : null}
    </div>
  )
}

type Tone = "agent" | "prompts" | "rules" | "skills" | "memory"

type Chip = {
  label: string
  tone: string
}

type Preview = {
  title: string
  tone: Tone
  body: string
  references: SkillReference[]
}

function SectionHead(props: { tone: Tone; title: string; lead: string }): ReactElement {
  return (
    <div className="agent-section-head">
      <h2 className={`agent-kicker agent-kicker--${props.tone}`}>{props.title}</h2>
      <p className="catalog-lead">{props.lead}</p>
    </div>
  )
}

function FileButton(props: {
  name: string
  description: string
  chips?: readonly Chip[]
  onOpen: () => void
}): ReactElement {
  const chips: readonly Chip[] = props.chips ?? []
  return (
    <button type="button" className="agent-file" onClick={props.onOpen}>
      <span className="agent-file-name">{props.name}</span>
      {props.description !== "" ? <span className="agent-file-desc">{props.description}</span> : null}
      {chips.length > 0 ? (
        <span className="agent-file-chips">
          {chips.map((chip: Chip) => (
            <span key={chip.label} className={`agent-chip ${chip.tone}`}>
              {chip.label}
            </span>
          ))}
        </span>
      ) : null}
    </button>
  )
}

function promptChips(row: PromptFile): Chip[] {
  const chips: Chip[] = [{ label: row.enabled ? "On" : "Off", tone: row.enabled ? "agent-chip--on" : "agent-chip--off" }]
  if (row.active) {
    chips.push({ label: "Active", tone: "agent-chip--active" })
  }
  return chips
}

function ruleChips(row: RuleFile): Chip[] {
  return [
    { label: row.enabled ? "On" : "Off", tone: row.enabled ? "agent-chip--on" : "agent-chip--off" },
    { label: String(row.order), tone: "agent-chip--order" },
  ]
}

function skillChips(row: SkillFile): Chip[] {
  const chips: Chip[] = [{ label: row.enabled ? "On" : "Off", tone: row.enabled ? "agent-chip--on" : "agent-chip--off" }]
  row.tools.forEach((tool: string) => {
    chips.push({ label: tool, tone: `skill-tool skill-tool--${tool}` })
  })
  return chips
}

function MarkdownView(props: { body: string }): ReactElement {
  const [source, setSource] = useState<boolean>(false)
  return (
    <div>
      <button type="button" className="button secondary" onClick={() => setSource((value: boolean) => !value)}>
        {source ? "Preview" : "Source"}
      </button>
      {source ? (
        <pre className="agent-source">{props.body}</pre>
      ) : (
        <div className="agent-preview">
          <ReactMarkdown>{props.body}</ReactMarkdown>
        </div>
      )}
    </div>
  )
}
