import { useEffect, useState, type ReactElement } from "react"
import ReactMarkdown from "react-markdown"
import {
  deletePrompt,
  deleteRule,
  deleteSkill,
  fetchAgentCard,
  fetchAgentPack,
  fetchPrompt,
  fetchRule,
  fetchSkill,
  saveAgentCard,
  savePrompt,
  saveRule,
  saveSkill,
  type AgentPack,
  type PromptFile,
  type RuleFile,
  type SkillFile,
  type SkillReference,
} from "./agentPackApi"

const TABLE_TOOLS: readonly string[] = ["metadata", "profile", "lineage", "graph"]
const FILE_TOOLS: readonly string[] = ["source", "outputs", "table_links"]
const SOURCE_TOOLS: readonly string[] = ["source"]

type Kind = "prompts" | "rules" | "skills"

export function AgentPackPanel(props: { assetId: string; assetKind: string }): ReactElement {
  const [pack, setPack] = useState<AgentPack | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [editor, setEditor] = useState<EditorState | null>(null)
  const [reload, setReload] = useState<number>(0)

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
  }, [props.assetId, reload])

  const tools: readonly string[] = toolChoices(props.assetKind)

  return (
    <div className="asset-sheet agent-pack">
      {error !== null ? <p className="error-text">{error}</p> : null}
      {pack === null ? <p className="catalog-lead">Loading agent files…</p> : null}
      {pack !== null && !pack.writable ? (
        <section className="asset-card">
          <h2>Agent</h2>
          <p className="catalog-lead">
            {pack.message ?? "This asset has no definition folder yet."}
          </p>
          {pack.prefix !== null ? <p className="agent-path">{pack.prefix}</p> : null}
        </section>
      ) : null}
      {pack !== null && pack.writable ? (
        <>
          <section className="asset-card">
            <div className="agent-card-head">
              <div>
                <h2>Agent</h2>
                <p className="catalog-lead">AGENTS.md is loaded on every run.</p>
                {pack.prefix !== null ? <p className="agent-path">{pack.prefix}</p> : null}
              </div>
              <button
                type="button"
                className="button secondary"
                onClick={() => {
                  void fetchAgentCard(props.assetId).then((card) => {
                    setEditor({
                      kind: "agent",
                      slug: "AGENTS",
                      name: "AGENTS",
                      description: "",
                      enabled: true,
                      active: false,
                      order: 0,
                      tools: [],
                      body: card.body ?? "",
                    })
                  })
                }}
              >
                {pack.agent.present ? "Edit" : "Add"}
              </button>
            </div>
          </section>
          <FileTable
            title="Prompts"
            rows={pack.prompts.map((row: PromptFile) => ({
              slug: row.slug,
              name: row.name,
              description: row.description,
              enabled: row.enabled,
              extra: row.active ? "Active" : "",
            }))}
            onAdd={() => {
              setEditor(blankEditor("prompts"))
            }}
            onPreview={(slug: string) => {
              void fetchPrompt(props.assetId, slug).then((file: PromptFile) => {
                setPreview({ title: file.name, body: file.body ?? "", references: [] })
              })
            }}
            onEdit={(slug: string) => {
              void fetchPrompt(props.assetId, slug).then((file: PromptFile) => {
                setEditor({
                  kind: "prompts",
                  slug: file.slug,
                  name: file.name,
                  description: file.description,
                  enabled: file.enabled,
                  active: file.active,
                  order: 0,
                  tools: [],
                  body: file.body ?? "",
                })
              })
            }}
            onDelete={(slug: string) => {
              void deletePrompt(props.assetId, slug).then(() => {
                setReload((value: number) => value + 1)
              })
            }}
          />
          <FileTable
            title="Rules"
            rows={pack.rules.map((row: RuleFile) => ({
              slug: row.slug,
              name: row.name,
              description: row.description,
              enabled: row.enabled,
              extra: String(row.order),
            }))}
            onAdd={() => {
              setEditor(blankEditor("rules"))
            }}
            onPreview={(slug: string) => {
              void fetchRule(props.assetId, slug).then((file: RuleFile) => {
                setPreview({ title: file.name, body: file.body ?? "", references: [] })
              })
            }}
            onEdit={(slug: string) => {
              void fetchRule(props.assetId, slug).then((file: RuleFile) => {
                setEditor({
                  kind: "rules",
                  slug: file.slug,
                  name: file.name,
                  description: file.description,
                  enabled: file.enabled,
                  active: false,
                  order: file.order,
                  tools: [],
                  body: file.body ?? "",
                })
              })
            }}
            onDelete={(slug: string) => {
              void deleteRule(props.assetId, slug).then(() => {
                setReload((value: number) => value + 1)
              })
            }}
          />
          <FileTable
            title="Skills"
            rows={pack.skills.map((row: SkillFile) => ({
              slug: row.slug,
              name: row.name,
              description: row.description,
              enabled: row.enabled,
              extra: row.tools.join(", "),
            }))}
            onAdd={() => {
              setEditor(blankEditor("skills"))
            }}
            onPreview={(slug: string) => {
              void fetchSkill(props.assetId, slug).then((file: SkillFile) => {
                setPreview({
                  title: file.name,
                  body: file.body ?? "",
                  references: file.references ?? [],
                })
              })
            }}
            onEdit={(slug: string) => {
              void fetchSkill(props.assetId, slug).then((file: SkillFile) => {
                setEditor({
                  kind: "skills",
                  slug: file.slug,
                  name: file.name,
                  description: file.description,
                  enabled: file.enabled,
                  active: false,
                  order: 0,
                  tools: file.tools,
                  body: file.body ?? "",
                })
              })
            }}
            onDelete={(slug: string) => {
              void deleteSkill(props.assetId, slug).then(() => {
                setReload((value: number) => value + 1)
              })
            }}
          />
        </>
      ) : null}
      {preview !== null ? (
        <aside className="agent-drawer" aria-label="Markdown preview">
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
      {editor !== null ? (
        <Editor
          state={editor}
          tools={tools}
          onChange={setEditor}
          onCancel={() => setEditor(null)}
          onSave={() => {
            const current: EditorState = editor
            const action: Promise<unknown> =
              current.kind === "agent"
                ? saveAgentCard(props.assetId, current.body)
                : current.kind === "prompts"
                  ? savePrompt(props.assetId, slugOf(current), {
                      name: slugOf(current),
                      description: current.description,
                      enabled: current.enabled,
                      active: current.active,
                      body: current.body,
                    })
                  : current.kind === "rules"
                    ? saveRule(props.assetId, slugOf(current), {
                        name: slugOf(current),
                        description: current.description,
                        enabled: current.enabled,
                        order: current.order,
                        body: current.body,
                      })
                    : saveSkill(props.assetId, slugOf(current), {
                        name: slugOf(current),
                        description: current.description,
                        enabled: current.enabled,
                        tools: current.tools,
                        body: current.body,
                      })
            void action
              .then(() => {
                setEditor(null)
                setReload((value: number) => value + 1)
                setError(null)
              })
              .catch((err: unknown) => {
                setError(err instanceof Error ? err.message : "Could not save")
              })
          }}
        />
      ) : null}
    </div>
  )
}

type Row = {
  slug: string
  name: string
  description: string
  enabled: boolean
  extra: string
}

type Preview = {
  title: string
  body: string
  references: SkillReference[]
}

type EditorState = {
  kind: Kind | "agent"
  slug: string
  name: string
  description: string
  enabled: boolean
  active: boolean
  order: number
  tools: string[]
  body: string
}

function FileTable(props: {
  title: string
  rows: Row[]
  onAdd: () => void
  onPreview: (slug: string) => void
  onEdit: (slug: string) => void
  onDelete: (slug: string) => void
}): ReactElement {
  return (
    <section className="asset-card">
      <div className="agent-card-head">
        <h2>{props.title}</h2>
        <button type="button" className="button" onClick={props.onAdd}>
          Add
        </button>
      </div>
      {props.rows.length === 0 ? <p className="catalog-lead">None yet.</p> : null}
      {props.rows.length > 0 ? (
        <table className="columns">
          <thead>
            <tr>
              <th>Name</th>
              <th>Description</th>
              <th>State</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {props.rows.map((row: Row) => (
              <tr key={row.slug}>
                <td>{row.name}</td>
                <td>{row.description}</td>
                <td>
                  {row.enabled ? "On" : "Off"}
                  {row.extra !== "" ? ` · ${row.extra}` : ""}
                </td>
                <td className="agent-actions">
                  <button type="button" className="button secondary" onClick={() => props.onPreview(row.slug)}>
                    Preview
                  </button>
                  <button type="button" className="button secondary" onClick={() => props.onEdit(row.slug)}>
                    Edit
                  </button>
                  <button type="button" className="button secondary" onClick={() => props.onDelete(row.slug)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  )
}

function MarkdownView(props: { body: string }): ReactElement {
  const [source, setSource] = useState<boolean>(false)
  return (
    <div>
      <button type="button" className="button secondary" onClick={() => setSource((value: boolean) => !value)}>
        {source ? "Preview" : "Source"}
      </button>
      {source ? <pre className="agent-source">{props.body}</pre> : (
        <div className="agent-preview">
          <ReactMarkdown>{props.body}</ReactMarkdown>
        </div>
      )}
    </div>
  )
}

function Editor(props: {
  state: EditorState
  tools: readonly string[]
  onChange: (state: EditorState) => void
  onCancel: () => void
  onSave: () => void
}): ReactElement {
  const state: EditorState = props.state
  return (
    <section className="asset-card agent-editor">
      <h2>{state.kind === "agent" ? "AGENTS.md" : state.kind}</h2>
      {state.kind !== "agent" ? (
        <label className="asset-field">
          <span>Slug</span>
          <input
            value={state.name}
            onChange={(event) => props.onChange({ ...state, name: event.target.value })}
          />
        </label>
      ) : null}
      {state.kind === "skills" || state.kind === "prompts" || state.kind === "rules" ? (
        <label className="asset-field">
          <span>Description</span>
          <input
            value={state.description}
            onChange={(event) => props.onChange({ ...state, description: event.target.value })}
          />
        </label>
      ) : null}
      {state.kind !== "agent" ? (
        <label className="agent-check">
          <input
            type="checkbox"
            checked={state.enabled}
            onChange={(event) => props.onChange({ ...state, enabled: event.target.checked })}
          />
          Enabled
        </label>
      ) : null}
      {state.kind === "prompts" ? (
        <label className="agent-check">
          <input
            type="checkbox"
            checked={state.active}
            onChange={(event) => props.onChange({ ...state, active: event.target.checked })}
          />
          Active prompt
        </label>
      ) : null}
      {state.kind === "rules" ? (
        <label className="asset-field">
          <span>Order</span>
          <input
            type="number"
            value={state.order}
            onChange={(event) => props.onChange({ ...state, order: Number(event.target.value) })}
          />
        </label>
      ) : null}
      {state.kind === "skills" ? (
        <fieldset className="agent-tools">
          <legend>Tools</legend>
          {props.tools.map((tool: string) => (
            <label key={tool} className="agent-check">
              <input
                type="checkbox"
                checked={state.tools.includes(tool)}
                onChange={(event) => {
                  const next: string[] = event.target.checked
                    ? [...state.tools, tool]
                    : state.tools.filter((item: string) => item !== tool)
                  props.onChange({ ...state, tools: next })
                }}
              />
              {tool}
            </label>
          ))}
        </fieldset>
      ) : null}
      <label className="asset-field">
        <span>Markdown</span>
        <textarea
          value={state.body}
          rows={10}
          onChange={(event) => props.onChange({ ...state, body: event.target.value })}
        />
      </label>
      <div className="agent-preview">
        <ReactMarkdown>{state.body}</ReactMarkdown>
      </div>
      <div className="agent-actions">
        <button type="button" className="button" onClick={props.onSave}>
          Save
        </button>
        <button type="button" className="button secondary" onClick={props.onCancel}>
          Cancel
        </button>
      </div>
    </section>
  )
}

function blankEditor(kind: Kind): EditorState {
  return {
    kind,
    slug: "",
    name: "",
    description: kind === "skills" ? "Describe when to use this skill" : "",
    enabled: true,
    active: false,
    order: 0,
    tools: [],
    body: "",
  }
}

function slugOf(state: EditorState): string {
  if (state.slug !== "") {
    return state.slug
  }
  return state.name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")
}

function toolChoices(kind: string): readonly string[] {
  if (kind === "TABLE") {
    return TABLE_TOOLS
  }
  if (kind === "FILE" || kind === "NOTEBOOK") {
    return FILE_TOOLS
  }
  return SOURCE_TOOLS
}
