import type { ReactElement } from "react"

type Token = {
  kind: string
  text: string
}

const PYTHON_KEYWORDS: ReadonlySet<string> = new Set([
  "False",
  "None",
  "True",
  "and",
  "as",
  "assert",
  "async",
  "await",
  "break",
  "class",
  "continue",
  "def",
  "del",
  "elif",
  "else",
  "except",
  "finally",
  "for",
  "from",
  "global",
  "if",
  "import",
  "in",
  "is",
  "lambda",
  "nonlocal",
  "not",
  "or",
  "pass",
  "raise",
  "return",
  "try",
  "while",
  "with",
  "yield",
])

type PythonState = {
  quote: string
  triple: boolean
}

export function HighlightedSource(props: { filename: string; source: string }): ReactElement {
  const lower: string = props.filename.toLowerCase()
  const lines: Token[][] = lower.endsWith(".py")
    ? highlightPython(props.source)
    : lower.endsWith(".md")
      ? highlightMarkdown(props.source)
      : plainLines(props.source)
  const gutter: number = String(Math.max(lines.length, 1)).length
  return (
    <pre className="source-code source-view">
      <code>
        {lines.map((tokens: Token[], index: number) => (
          <span className="source-line" key={index}>
            <span className="source-gutter" style={{ width: `${gutter + 1}ch` }}>
              {index + 1}
            </span>
            <span className="source-text">
              {tokens.map((token: Token, tokenIndex: number) =>
                token.kind === "plain" ? (
                  <span key={tokenIndex}>{token.text}</span>
                ) : (
                  <span key={tokenIndex} className={`tok tok-${token.kind}`}>
                    {token.text}
                  </span>
                ),
              )}
            </span>
          </span>
        ))}
      </code>
    </pre>
  )
}

function plainLines(source: string): Token[][] {
  return source.split("\n").map((line: string) => [{ kind: "plain", text: line }])
}

function highlightMarkdown(source: string): Token[][] {
  const rows: string[] = source.split("\n")
  const lines: Token[][] = []
  let frontmatter: boolean = false
  let closed: boolean = false
  for (const row of rows) {
    if (!closed && row.trim() === "---") {
      frontmatter = !frontmatter
      if (!frontmatter) {
        closed = true
      }
      lines.push([{ kind: "fence", text: row }])
      continue
    }
    if (frontmatter) {
      lines.push(highlightYamlLine(row))
      continue
    }
    lines.push(highlightMarkdownLine(row))
  }
  return lines
}

function highlightMarkdownLine(line: string): Token[] {
  if (line.startsWith("#")) {
    return [{ kind: "heading", text: line }]
  }
  return colorInline(line, "plain")
}

function highlightYamlLine(line: string): Token[] {
  const tokens: Token[] = []
  let index: number = 0
  while (line.startsWith("  ", index)) {
    tokens.push({ kind: "guide", text: "  " })
    index += 2
  }
  if (index < line.length && line[index] === " ") {
    tokens.push({ kind: "plain", text: " " })
    index += 1
  }
  if (line.startsWith("- ", index) || (line[index] === "-" && index === line.length - 1)) {
    tokens.push({ kind: "punct", text: "-" })
    index += 1
    if (line[index] === " ") {
      tokens.push({ kind: "plain", text: " " })
      index += 1
    }
  }
  const rest: string = line.slice(index)
  const colon: number = rest.indexOf(":")
  const quoted: boolean = rest.startsWith('"') || rest.startsWith("'")
  if (!quoted && colon > 0) {
    tokens.push({ kind: "key", text: rest.slice(0, colon) })
    tokens.push({ kind: "punct", text: ":" })
    tokens.push(...highlightYamlValue(rest.slice(colon + 1)))
    return tokens
  }
  tokens.push(...highlightYamlValue(rest))
  return tokens
}

function highlightYamlValue(value: string): Token[] {
  const trimmed: string = value.trim()
  if (trimmed === "true" || trimmed === "false" || trimmed === "null") {
    const lead: number = value.indexOf(trimmed)
    return [
      { kind: "plain", text: value.slice(0, lead) },
      { kind: "key", text: trimmed },
      { kind: "plain", text: value.slice(lead + trimmed.length) },
    ].filter((token: Token) => token.text !== "")
  }
  return colorInline(value, "plain")
}

function colorInline(text: string, plainKind: string): Token[] {
  const tokens: Token[] = []
  let index: number = 0
  while (index < text.length) {
    const mark: string = text[index] ?? ""
    if (mark === '"' || mark === "'") {
      const end: number = text.indexOf(mark, index + 1)
      const stop: number = end < 0 ? text.length : end + 1
      tokens.push({ kind: "string", text: text.slice(index, stop) })
      index = stop
      continue
    }
    if (mark === "`") {
      const end: number = text.indexOf("`", index + 1)
      const stop: number = end < 0 ? text.length : end + 1
      tokens.push({ kind: "string", text: text.slice(index, stop) })
      index = stop
      continue
    }
    let next: number = index + 1
    while (next < text.length && text[next] !== '"' && text[next] !== "'" && text[next] !== "`") {
      next += 1
    }
    tokens.push({ kind: plainKind, text: text.slice(index, next) })
    index = next
  }
  return tokens
}

function highlightPython(source: string): Token[][] {
  const rows: string[] = source.split("\n")
  const lines: Token[][] = []
  const state: PythonState = { quote: "", triple: false }
  let pending: "def" | "class" | "" = ""
  for (const row of rows) {
    const parsed: { tokens: Token[]; pending: "def" | "class" | "" } = highlightPythonLine(row, state, pending)
    pending = parsed.pending
    lines.push(parsed.tokens)
  }
  return lines
}

function highlightPythonLine(
  line: string,
  state: PythonState,
  pending: "def" | "class" | "",
): { tokens: Token[]; pending: "def" | "class" | "" } {
  const tokens: Token[] = []
  let index: number = 0
  let nextName: "def" | "class" | "" = pending
  if (state.triple) {
    const closed: number = closeString(line, 0, state.quote, true)
    tokens.push({ kind: "string", text: line.slice(0, closed) })
    if (closed < line.length || !line.slice(0, closed).endsWith(state.quote.repeat(3))) {
      return { tokens, pending: nextName }
    }
    state.triple = false
    state.quote = ""
    index = closed
  }
  while (index < line.length) {
    const mark: string = line[index] ?? ""
    if (mark === "#") {
      tokens.push({ kind: "comment", text: line.slice(index) })
      break
    }
    if (mark === '"' || mark === "'") {
      const triple: boolean = line.startsWith(mark.repeat(3), index)
      const closed: number = closeString(line, index, mark, triple)
      tokens.push({ kind: "string", text: line.slice(index, closed) })
      if (triple && !line.slice(index, closed).endsWith(mark.repeat(3))) {
        state.quote = mark
        state.triple = true
      }
      index = closed
      nextName = ""
      continue
    }
    if (mark === "@") {
      const end: number = readName(line, index + 1)
      tokens.push({ kind: "decorator", text: line.slice(index, end) })
      index = end
      nextName = ""
      continue
    }
    if (isNameStart(mark)) {
      const end: number = readName(line, index)
      const word: string = line.slice(index, end)
      if (PYTHON_KEYWORDS.has(word)) {
        tokens.push({ kind: "keyword", text: word })
        nextName = word === "def" || word === "class" ? word : ""
      } else if (nextName === "def") {
        tokens.push({ kind: "function", text: word })
        nextName = ""
      } else if (nextName === "class") {
        tokens.push({ kind: "class", text: word })
        nextName = ""
      } else {
        tokens.push({ kind: "plain", text: word })
      }
      index = end
      continue
    }
    if (isDigit(mark)) {
      let end: number = index + 1
      while (end < line.length && (isDigit(line[end] ?? "") || line[end] === ".")) {
        end += 1
      }
      tokens.push({ kind: "number", text: line.slice(index, end) })
      index = end
      nextName = ""
      continue
    }
    tokens.push({ kind: "plain", text: mark })
    if (!isSpace(mark)) {
      nextName = ""
    }
    index += 1
  }
  return { tokens, pending: nextName }
}

function closeString(line: string, start: number, quote: string, triple: boolean): number {
  const fence: string = triple ? quote.repeat(3) : quote
  let index: number = start + fence.length
  while (index < line.length) {
    if (line.startsWith(fence, index)) {
      return index + fence.length
    }
    if (line[index] === "\\") {
      index += 2
      continue
    }
    index += 1
  }
  return line.length
}

function readName(line: string, start: number): number {
  let index: number = start
  while (index < line.length && isNamePart(line[index] ?? "")) {
    index += 1
  }
  return index
}

function isNameStart(mark: string): boolean {
  return (mark >= "A" && mark <= "Z") || (mark >= "a" && mark <= "z") || mark === "_"
}

function isNamePart(mark: string): boolean {
  return isNameStart(mark) || isDigit(mark)
}

function isDigit(mark: string): boolean {
  return mark >= "0" && mark <= "9"
}

function isSpace(mark: string): boolean {
  return mark === " " || mark === "\t"
}
