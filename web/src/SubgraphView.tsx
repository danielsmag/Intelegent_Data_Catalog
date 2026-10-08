import { useEffect, useRef, useState, type PointerEvent, type ReactElement } from "react"
import type { GraphEdgeRecord, GraphNodeRecord, GraphViewRecord } from "./registryApi"

const VIEW_WIDTH: number = 1000
const VIEW_HEIGHT: number = 640
const TABLE_RADIUS: number = 28
const COLUMN_RADIUS: number = 20

type Point = {
  x: number
  y: number
}

type Camera = {
  x: number
  y: number
  scale: number
}

type Drag =
  | { mode: "node"; id: string }
  | { mode: "pan" }

export function SubgraphView(props: {
  graph: GraphViewRecord | null
  error: string | null
  focusTableId: string
}): ReactElement {
  if (props.error !== null) {
    return <p className="error-text">{props.error}</p>
  }
  if (props.graph === null) {
    return <p className="note">Loading subgraph…</p>
  }
  if (props.graph.nodes.length === 0) {
    return <p className="note">No subgraph for this table. Build context to write one.</p>
  }
  return <SubgraphCanvas graph={props.graph} focusTableId={props.focusTableId} />
}

function SubgraphCanvas(props: { graph: GraphViewRecord; focusTableId: string }): ReactElement {
  const graph: GraphViewRecord = props.graph
  const svgRef = useRef<SVGSVGElement | null>(null)
  const dragRef = useRef<Drag | null>(null)
  const movedRef = useRef<boolean>(false)
  const [positions, setPositions] = useState<Map<string, Point>>(() => layoutGraph(graph.nodes, graph.edges))
  const [selected, setSelected] = useState<string | null>(null)
  const [camera, setCamera] = useState<Camera>({ x: 0, y: 0, scale: 1 })
  const cameraRef = useRef<Camera>(camera)
  cameraRef.current = camera
  useEffect(() => {
    setPositions(layoutGraph(graph.nodes, graph.edges))
    const focus: GraphNodeRecord | undefined = graph.nodes.find(
      (node: GraphNodeRecord) => node.kind === "table" && node.catalog_id === props.focusTableId,
    )
    setSelected(focus?.id ?? graph.nodes[0]?.id ?? null)
    setCamera({ x: 0, y: 0, scale: 1 })
  }, [graph, props.focusTableId])
  useEffect(() => {
    const svg: SVGSVGElement | null = svgRef.current
    if (svg === null) {
      return
    }
    const onWheel = (event: WheelEvent): void => {
      event.preventDefault()
      const current: Camera = cameraRef.current
      const nextScale: number = event.deltaY > 0 ? current.scale * 0.9 : current.scale * 1.1
      const scale: number = Math.min(2.6, Math.max(0.45, nextScale))
      setCamera({ ...current, scale })
    }
    svg.addEventListener("wheel", onWheel, { passive: false })
    return () => {
      svg.removeEventListener("wheel", onWheel)
    }
  }, [])
  const nodesById: Map<string, GraphNodeRecord> = new Map(
    graph.nodes.map((node: GraphNodeRecord) => [node.id, node]),
  )
  const selectedNode: GraphNodeRecord | null =
    selected === null ? null : (nodesById.get(selected) ?? null)
  const tableCount: number = graph.nodes.filter((node: GraphNodeRecord) => node.kind === "table").length
  const columnCount: number = graph.nodes.filter((node: GraphNodeRecord) => node.kind === "column").length
  const kinds: string[] = [...new Set(graph.edges.map((edge: GraphEdgeRecord) => edge.kind))]
  function viewBoxPoint(clientX: number, clientY: number): Point | null {
    const svg: SVGSVGElement | null = svgRef.current
    if (svg === null) {
      return null
    }
    const matrix: DOMMatrix | null = svg.getScreenCTM()
    if (matrix === null) {
      return null
    }
    const point: DOMPoint = svg.createSVGPoint()
    point.x = clientX
    point.y = clientY
    const local: DOMPoint = point.matrixTransform(matrix.inverse())
    return { x: local.x, y: local.y }
  }
  function onPointerDown(event: PointerEvent<SVGSVGElement>): void {
    const target: EventTarget | null = event.target
    const nodeId: string | null =
      target instanceof Element ? target.closest("[data-node-id]")?.getAttribute("data-node-id") ?? null : null
    movedRef.current = false
    event.currentTarget.setPointerCapture(event.pointerId)
    if (nodeId !== null) {
      dragRef.current = { mode: "node", id: nodeId }
      setSelected(nodeId)
      return
    }
    dragRef.current = { mode: "pan" }
  }
  function onPointerMove(event: PointerEvent<SVGSVGElement>): void {
    const drag: Drag | null = dragRef.current
    if (drag === null) {
      return
    }
    const currentPoint: Point | null = viewBoxPoint(event.clientX, event.clientY)
    const previousPoint: Point | null = viewBoxPoint(event.clientX - event.movementX, event.clientY - event.movementY)
    if (currentPoint === null || previousPoint === null) {
      return
    }
    const dx: number = currentPoint.x - previousPoint.x
    const dy: number = currentPoint.y - previousPoint.y
    if (dx !== 0 || dy !== 0) {
      movedRef.current = true
    }
    if (drag.mode === "pan") {
      setCamera((current: Camera) => ({ ...current, x: current.x + dx, y: current.y + dy }))
      return
    }
    const scale: number = cameraRef.current.scale
    setPositions((current: Map<string, Point>) => {
      const point: Point | undefined = current.get(drag.id)
      if (point === undefined) {
        return current
      }
      const next: Map<string, Point> = new Map(current)
      next.set(drag.id, { x: point.x + dx / scale, y: point.y + dy / scale })
      return next
    })
  }
  function onPointerUp(): void {
    const drag: Drag | null = dragRef.current
    if (drag?.mode === "pan" && !movedRef.current) {
      setSelected(null)
    }
    dragRef.current = null
  }
  return (
    <div className="subgraph">
      <div className="subgraph-stage">
        <div className="subgraph-legend" aria-label="Subgraph legend">
          <span>
            <i className="swatch swatch-table" /> Table {tableCount}
          </span>
          <span>
            <i className="swatch swatch-column" /> Column {columnCount}
          </span>
          {kinds.map((kind: string) => (
            <span key={kind} className={`edge-chip edge-chip-${kind}`}>
              {kind}
            </span>
          ))}
        </div>
        <svg
          ref={svgRef}
          className="subgraph-svg"
          viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
          role="img"
          aria-label="Table subgraph"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          <rect className="subgraph-bg" width={VIEW_WIDTH} height={VIEW_HEIGHT} />
          <g transform={`translate(${camera.x} ${camera.y}) scale(${camera.scale})`}>
            {graph.edges.map((edge: GraphEdgeRecord, index: number) => (
              <SubgraphEdge
                key={`${edge.source}-${edge.target}-${edge.kind}-${index}`}
                edge={edge}
                positions={positions}
                nodesById={nodesById}
              />
            ))}
            {graph.nodes.map((node: GraphNodeRecord) => {
              const point: Point | undefined = positions.get(node.id)
              if (point === undefined) {
                return null
              }
              return (
                <SubgraphNode
                  key={node.id}
                  node={node}
                  point={point}
                  selected={node.id === selected}
                />
              )
            })}
          </g>
        </svg>
        {graph.truncated ? <p className="comment">Graph truncated.</p> : null}
      </div>
      <NodeDetails node={selectedNode} graph={graph} nodesById={nodesById} />
    </div>
  )
}

function SubgraphEdge(props: {
  edge: GraphEdgeRecord
  positions: Map<string, Point>
  nodesById: Map<string, GraphNodeRecord>
}): ReactElement | null {
  const source: Point | undefined = props.positions.get(props.edge.source)
  const target: Point | undefined = props.positions.get(props.edge.target)
  const sourceNode: GraphNodeRecord | undefined = props.nodesById.get(props.edge.source)
  const targetNode: GraphNodeRecord | undefined = props.nodesById.get(props.edge.target)
  if (source === undefined || target === undefined || sourceNode === undefined || targetNode === undefined) {
    return null
  }
  const trimmed: Point[] = trimLine(
    source,
    target,
    radiusFor(sourceNode),
    radiusFor(targetNode),
  )
  const start: Point | undefined = trimmed[0]
  const end: Point | undefined = trimmed[1]
  if (start === undefined || end === undefined) {
    return null
  }
  const midX: number = (start.x + end.x) / 2
  const midY: number = (start.y + end.y) / 2
  const showLabel: boolean = props.edge.kind !== "HAS_COLUMN"
  return (
    <g className={`subgraph-edge subgraph-edge-${props.edge.kind}`}>
      <line x1={start.x} y1={start.y} x2={end.x} y2={end.y} />
      {showLabel ? (
        <text x={midX} y={midY - 6} textAnchor="middle">
          {props.edge.kind}
        </text>
      ) : null}
    </g>
  )
}

function SubgraphNode(props: {
  node: GraphNodeRecord
  point: Point
  selected: boolean
}): ReactElement {
  const radius: number = radiusFor(props.node)
  return (
    <g
      className={props.selected ? "subgraph-node selected" : "subgraph-node"}
      data-node-id={props.node.id}
      transform={`translate(${props.point.x} ${props.point.y})`}
    >
      <circle className={`node-disk node-${props.node.kind}`} r={radius} />
      {props.selected ? <circle className="node-ring" r={radius + 6} /> : null}
      <text textAnchor="middle" dy="0.35em">
        {shortName(props.node.label)}
      </text>
      <title>{props.node.label}</title>
    </g>
  )
}

function NodeDetails(props: {
  node: GraphNodeRecord | null
  graph: GraphViewRecord
  nodesById: Map<string, GraphNodeRecord>
}): ReactElement {
  const node: GraphNodeRecord | null = props.node
  if (node === null) {
    return (
      <aside className="subgraph-details">
        <h2>Node details</h2>
        <p className="note">Select a node.</p>
      </aside>
    )
  }
  const links: GraphEdgeRecord[] = props.graph.edges.filter(
    (edge: GraphEdgeRecord) => edge.source === node.id || edge.target === node.id,
  )
  return (
    <aside className="subgraph-details" aria-label="Node details">
      <h2>Node details</h2>
      <span className={node.kind === "table" ? "badge badge-table" : "badge badge-column"}>{node.kind}</span>
      <dl className="facts">
        <dt>name</dt>
        <dd>{node.label}</dd>
        <dt>catalog_id</dt>
        <dd>{node.catalog_id ?? "None"}</dd>
        <dt>node_id</dt>
        <dd>{node.id}</dd>
        <dt>resolved</dt>
        <dd>{node.resolved ? "true" : "false"}</dd>
      </dl>
      <h3>Relationships</h3>
      {links.length === 0 ? <p className="note">No relationships.</p> : null}
      <ul className="registry-list">
        {links.map((edge: GraphEdgeRecord, index: number) => {
          const otherId: string = edge.source === node.id ? edge.target : edge.source
          const other: GraphNodeRecord | undefined = props.nodesById.get(otherId)
          const outgoing: boolean = edge.source === node.id
          return (
            <li key={`${edge.kind}-${otherId}-${index}`}>
              <span className="badge">{edge.kind}</span>
              <span>
                <span aria-label={outgoing ? "outgoing" : "incoming"}>{outgoing ? "→" : "←"}</span>{" "}
                {other?.label ?? otherId}
              </span>
              {edge.reason !== null && edge.reason !== undefined && edge.reason !== "" ? (
                <span className="comment">{edge.reason}</span>
              ) : null}
            </li>
          )
        })}
      </ul>
    </aside>
  )
}

function radiusFor(node: GraphNodeRecord): number {
  return node.kind === "table" ? TABLE_RADIUS : COLUMN_RADIUS
}

function shortName(name: string): string {
  if (name.length <= 12) {
    return name
  }
  return `${name.slice(0, 11)}…`
}

function trimLine(source: Point, target: Point, sourceRadius: number, targetRadius: number): Point[] {
  const dx: number = target.x - source.x
  const dy: number = target.y - source.y
  const distance: number = Math.hypot(dx, dy) || 1
  return [
    { x: source.x + (dx / distance) * sourceRadius, y: source.y + (dy / distance) * sourceRadius },
    { x: target.x - (dx / distance) * targetRadius, y: target.y - (dy / distance) * targetRadius },
  ]
}

function layoutGraph(nodes: GraphNodeRecord[], edges: GraphEdgeRecord[]): Map<string, Point> {
  const placed: Array<{ id: string; x: number; y: number; vx: number; vy: number }> = nodes.map(
    (node: GraphNodeRecord, index: number) => {
      const angle: number = (Math.PI * 2 * index) / Math.max(nodes.length, 1)
      const ring: number = node.kind === "table" ? 80 : 180
      return {
        id: node.id,
        x: VIEW_WIDTH / 2 + Math.cos(angle) * ring,
        y: VIEW_HEIGHT / 2 + Math.sin(angle) * ring,
        vx: 0,
        vy: 0,
      }
    },
  )
  const indexById: Map<string, number> = new Map(placed.map((node, index: number) => [node.id, index]))
  let step: number = 0
  for (step = 0; step < 220; step += 1) {
    let left: number = 0
    for (left = 0; left < placed.length; left += 1) {
      let right: number = left + 1
      for (right = left + 1; right < placed.length; right += 1) {
        const leftNode: { id: string; x: number; y: number; vx: number; vy: number } | undefined =
          placed[left]
        const rightNode: { id: string; x: number; y: number; vx: number; vy: number } | undefined =
          placed[right]
        if (leftNode === undefined || rightNode === undefined) {
          continue
        }
        const dx: number = rightNode.x - leftNode.x
        const dy: number = rightNode.y - leftNode.y
        const distance: number = Math.hypot(dx, dy) || 0.01
        const force: number = 4200 / (distance * distance)
        const fx: number = (dx / distance) * force
        const fy: number = (dy / distance) * force
        leftNode.vx -= fx
        leftNode.vy -= fy
        rightNode.vx += fx
        rightNode.vy += fy
      }
    }
    for (const edge of edges) {
      const sourceIndex: number | undefined = indexById.get(edge.source)
      const targetIndex: number | undefined = indexById.get(edge.target)
      if (sourceIndex === undefined || targetIndex === undefined) {
        continue
      }
      const source: { id: string; x: number; y: number; vx: number; vy: number } | undefined =
        placed[sourceIndex]
      const target: { id: string; x: number; y: number; vx: number; vy: number } | undefined =
        placed[targetIndex]
      if (source === undefined || target === undefined) {
        continue
      }
      const dx: number = target.x - source.x
      const dy: number = target.y - source.y
      const distance: number = Math.hypot(dx, dy) || 0.01
      const rest: number = edge.kind === "HAS_COLUMN" ? 96 : 170
      const force: number = (distance - rest) * 0.045
      const fx: number = (dx / distance) * force
      const fy: number = (dy / distance) * force
      source.vx += fx
      source.vy += fy
      target.vx -= fx
      target.vy -= fy
    }
    for (const node of placed) {
      node.vx += (VIEW_WIDTH / 2 - node.x) * 0.012
      node.vy += (VIEW_HEIGHT / 2 - node.y) * 0.012
      node.vx *= 0.82
      node.vy *= 0.82
      node.x += Math.max(-24, Math.min(24, node.vx))
      node.y += Math.max(-24, Math.min(24, node.vy))
    }
  }
  fit(placed)
  return new Map(placed.map((node) => [node.id, { x: node.x, y: node.y }]))
}

function fit(nodes: Array<{ x: number; y: number }>): void {
  if (nodes.length === 0) {
    return
  }
  let minX: number = Number.POSITIVE_INFINITY
  let minY: number = Number.POSITIVE_INFINITY
  let maxX: number = Number.NEGATIVE_INFINITY
  let maxY: number = Number.NEGATIVE_INFINITY
  for (const node of nodes) {
    minX = Math.min(minX, node.x)
    minY = Math.min(minY, node.y)
    maxX = Math.max(maxX, node.x)
    maxY = Math.max(maxY, node.y)
  }
  const spanX: number = Math.max(maxX - minX, 1)
  const spanY: number = Math.max(maxY - minY, 1)
  const pad: number = 72
  for (const node of nodes) {
    node.x = pad + ((node.x - minX) / spanX) * (VIEW_WIDTH - pad * 2)
    node.y = pad + ((node.y - minY) / spanY) * (VIEW_HEIGHT - pad * 2)
  }
}
