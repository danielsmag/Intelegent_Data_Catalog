import { useState, type ReactElement } from "react"
import ReactMarkdown from "react-markdown"

type SkillDocument = {
  name: string
  description: string
  enabled: boolean
  tools: string[]
  body: string
}

type MarkdownDocument = {
  body: string
}

export function DefinitionDocument(props: { filename: string; objectKey: string; source: string }): ReactElement | null {
  if (props.filename === "SKILL.md") {
    return <SkillView objectKey={props.objectKey} source={props.source} />
  }
  if (props.filename.toLowerCase().endsWith(".md")) {
    const document: MarkdownDocument = parseMarkdown(props.source)
    return (
      <article className="skill-doc">
        <header className="skill-doc-head">
          <div>
            <p className="eyebrow">Reference</p>
            <h1>{props.filename}</h1>
          </div>
        </header>
        <InstructionBody body={document.body} source={props.source} />
      </article>
    )
  }
  return null
}

function SkillView(props: { objectKey: string; source: string }): ReactElement {
  const skill: SkillDocument | null = parseSkill(props.source)
  const directory: string = skillDirectory(props.objectKey)
  if (skill === null) {
    return (
      <article className="skill-doc">
        <header className="skill-doc-head">
          <div>
            <p className="eyebrow">Skill</p>
            <h1>{directory === "" ? "SKILL.md" : directory}</h1>
          </div>
        </header>
        <p className="error-text">Frontmatter is missing or not closed. Skills needs name and description between --- lines.</p>
        <pre className="source-code">{props.source}</pre>
      </article>
    )
  }
  const mismatch: boolean = directory !== "" && directory !== skill.name
  return (
    <article className="skill-doc">
      <header className="skill-doc-head">
        <div>
          <p className="eyebrow">Skill</p>
          <h1>{skill.name}</h1>
          <p className="skill-doc-lead">{skill.description}</p>
        </div>
        <span className={skill.enabled ? "state-pill state-success" : "state-pill"}>
          {skill.enabled ? "Enabled" : "Disabled"}
        </span>
      </header>
      <p className="skill-doc-path">{displayPath(props.objectKey)}</p>
      {mismatch ? (
        <p className="error-text">Directory is {directory}. The skill name must match that directory.</p>
      ) : null}
      <section className="skill-doc-section">
        <h2>Instructions</h2>
        <p className="skill-doc-caption">Loaded when the model uses this skill.</p>
        <InstructionBody body={skill.body} source={props.source} />
      </section>
      <section className="skill-doc-section">
        <h2>Catalog tools</h2>
        <p className="skill-doc-caption">Not part of the skill file the model receives. That file keeps name, description, and the instructions.</p>
        {skill.tools.length === 0 ? (
          <p className="note">No tools selected.</p>
        ) : (
          <ul className="skill-doc-tools">
            {skill.tools.map((tool: string) => (
              <li key={tool} className={`skill-tool skill-tool--${tool}`}>{tool}</li>
            ))}
          </ul>
        )}
      </section>
    </article>
  )
}

function InstructionBody(props: { body: string; source: string }): ReactElement {
  const [sourceOpen, setSourceOpen] = useState<boolean>(false)
  const text: string = props.body.trim() === "" ? "_No instructions yet._" : props.body
  return (
    <div>
      <div className="skill-doc-body">
        <ReactMarkdown>{text}</ReactMarkdown>
      </div>
      <button type="button" className="button secondary skill-doc-source" onClick={() => setSourceOpen((open: boolean) => !open)}>
        {sourceOpen ? "Hide source" : "Source"}
      </button>
      {sourceOpen ? <pre className="source-code">{props.source}</pre> : null}
    </div>
  )
}

function parseSkill(source: string): SkillDocument | null {
  const split: { fields: Map<string, string>; nested: Map<string, Map<string, string[]>>; body: string } | null = splitFrontmatter(source)
  if (split === null) {
    return null
  }
  const name: string = unquote(split.fields.get("name") ?? "")
  const description: string = unquote(split.fields.get("description") ?? "")
  if (name === "" || description === "") {
    return null
  }
  const enabledRaw: string = split.fields.get("enabled") ?? "true"
  const metadata: Map<string, string[]> | undefined = split.nested.get("metadata")
  const tools: string[] = metadata?.get("tools") ?? []
  return {
    name,
    description,
    enabled: enabledRaw !== "false",
    tools,
    body: split.body,
  }
}

function parseMarkdown(source: string): MarkdownDocument {
  const split: { fields: Map<string, string>; nested: Map<string, Map<string, string[]>>; body: string } | null = splitFrontmatter(source)
  if (split === null) {
    return { body: source }
  }
  return { body: split.body }
}

function splitFrontmatter(source: string): { fields: Map<string, string>; nested: Map<string, Map<string, string[]>>; body: string } | null {
  if (!source.startsWith("---\n")) {
    return null
  }
  const end: number = source.indexOf("\n---\n", 4)
  if (end < 0) {
    return null
  }
  const fields: Map<string, string> = new Map()
  const nested: Map<string, Map<string, string[]>> = new Map()
  const lines: string[] = source.slice(4, end).split("\n")
  let index: number = 0
  while (index < lines.length) {
    const line: string = lines[index] ?? ""
    if (line.trim() === "") {
      index += 1
      continue
    }
    const colon: number = line.indexOf(":")
    if (colon < 0 || line.startsWith(" ")) {
      return null
    }
    const key: string = line.slice(0, colon).trim()
    const value: string = line.slice(colon + 1).trim()
    if (value !== "") {
      fields.set(key, value)
      index += 1
      continue
    }
    const group: Map<string, string[]> = new Map()
    index += 1
    while (index < lines.length && (lines[index] ?? "").startsWith("  ") && !(lines[index] ?? "").startsWith("    ")) {
      const child: string = (lines[index] ?? "").trim()
      const childColon: number = child.indexOf(":")
      if (childColon < 0) {
        return null
      }
      const childKey: string = child.slice(0, childColon).trim()
      const childValue: string = child.slice(childColon + 1).trim()
      if (childValue !== "") {
        group.set(childKey, [unquote(childValue)])
        index += 1
        continue
      }
      const items: string[] = []
      index += 1
      while (index < lines.length && (lines[index] ?? "").startsWith("    - ")) {
        items.push(unquote((lines[index] ?? "").trim().slice(2).trim()))
        index += 1
      }
      group.set(childKey, items)
    }
    nested.set(key, group)
  }
  let body: string = source.slice(end + 5)
  if (body.startsWith("\n")) {
    body = body.slice(1)
  }
  return { fields, nested, body }
}

function unquote(value: string): string {
  if (value.startsWith('"') && value.endsWith('"') && value.length >= 2) {
    return value.slice(1, -1).replace(/\\"/g, '"').replace(/\\\\/g, "\\")
  }
  return value
}

function skillDirectory(objectKey: string): string {
  const parts: string[] = objectKey.split("/").filter((part: string) => part !== "")
  if (parts.length < 2) {
    return ""
  }
  return parts[parts.length - 2] ?? ""
}

function displayPath(objectKey: string): string {
  const marker: string = "agents/"
  const index: number = objectKey.indexOf(marker)
  if (index < 0) {
    return objectKey
  }
  return objectKey.slice(index + marker.length)
}
