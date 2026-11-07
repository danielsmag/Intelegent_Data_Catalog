import { useEffect, useState, type ReactElement } from "react"
import { useParams } from "react-router-dom"
import { detailString, fetchAgent, type AgentKind, type AgentRecord } from "./agentsApi"
import { AgentRuns } from "./AgentRuns"
import { ContextEditor } from "./ContextEditor"
import { useTabParam } from "./useTabParam"
import { optionsFromFields, type ConfigOption } from "./ConfigRail"
import { useEntityConfig, type OptionField, type RefreshMode } from "./refreshSettings"
import { onAgentBuildFinished, requestAgentBuild } from "./knowledgeBuild"
import { WorkspaceFilePage } from "./WorkspaceFilePage"
import { TableAgentView, type TableAgentTab } from "./TableDetail"
import { AirflowDagView, AirflowInstanceView } from "./AirflowPages"
import { openInFocusedGroup } from "./editorLayout"
import { AgentPackPanel } from "./AgentPackPanel"

const OTHER_TABS = ["overview", "source", "runs", "agent", "config"] as const
const TABLE_TABS = [
  "overview",
  "columns",
  "relationships",
  "lineage",
  "graph",
  "source",
  "runs",
  "agent",
  "config",
] as const
type AgentTab = (typeof TABLE_TABS)[number]

const TAB_LABELS: Record<AgentTab, string> = {
  overview: "Overview",
  columns: "Columns",
  relationships: "Relationships",
  lineage: "Lineage",
  graph: "Graph",
  source: "Source",
  runs: "Runs",
  agent: "Agent",
  config: "Config",
}

const TABLE_ASSET_FIELDS: readonly OptionField[] = [
  { key: "include_graph", label: "Include graph", defaultValue: true },
  { key: "include_lineage", label: "Include lineage", defaultValue: true },
  { key: "include_data_profile", label: "Include data profile", defaultValue: false },
]

const FILE_ASSET_FIELDS: readonly OptionField[] = [
  { key: "include_outputs", label: "Include notebook outputs", defaultValue: false },
  { key: "include_table_links", label: "Link mentioned tables", defaultValue: true },
]

const AIRFLOW_ASSET_FIELDS: readonly OptionField[] = [
  { key: "include_source", label: "Include source", defaultValue: true },
  { key: "include_previous", label: "Keep previous findings", defaultValue: true },
]

const TABLE_VIEW_TABS: readonly TableAgentTab[] = [
  "overview",
  "columns",
  "relationships",
  "lineage",
  "graph",
  "source",
]

export function AgentPage(): ReactElement {
  const agentId: string = useParams().agentId ?? ""
  const [agent, setAgent] = useState<AgentRecord | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [building, setBuilding] = useState<boolean>(false)
  const fields: readonly OptionField[] = optionFieldsForKind(agent?.kind)
  const config = useEntityConfig("agent", agentId, fields, agent !== null)
  useEffect(() => {
    return onAgentBuildFinished((job) => {
      if (job.agent_id !== agentId) {
        return
      }
      setBuilding(false)
    })
  }, [agentId])
  useEffect(() => {
    const controller: AbortController = new AbortController()
    void fetchAgent(agentId, controller.signal)
      .then((row: AgentRecord) => {
        setAgent(row)
        setError(null)
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "Could not load agent")
        }
      })
    return () => {
      controller.abort()
    }
  }, [agentId])
  const nodeId: string | null = agent === null ? null : detailString(agent.detail, "node_id")
  const tableId: string | null = agent === null ? null : detailString(agent.detail, "table_id")
  const isTable: boolean = agent?.kind === "TABLE" && tableId !== null
  const allowed: readonly AgentTab[] = isTable ? TABLE_TABS : OTHER_TABS
  const [tab, selectTab] = useTabParam<AgentTab>(allowed, "overview")
  const tableTab: TableAgentTab | null = isTable && isTableViewTab(tab) ? tab : null
  const sourceKey: string | null = agent === null ? null : propertyString(agent.properties, "source_key")
  const tags: string[] = agent === null ? [] : declaredTags(agent)
  const visibility: string = agent?.owner_user_id === null ? "Shared" : "Personal"
  const instanceId: string | null = agent === null ? null : detailString(agent.detail, "instance_id")
  const dagId: string | null = agent === null ? null : detailString(agent.detail, "dag_id")
  if (agent?.kind === "AIRFLOW_INSTANCE" && instanceId !== null) {
    return (
      <AirflowInstanceView
        instanceId={instanceId}
        agentId={agent.id}
        onDeleted={() => {
          openInFocusedGroup("/agents?service=airflow")
        }}
      />
    )
  }
  if (agent?.kind === "AIRFLOW_DAG" && instanceId !== null && dagId !== null) {
    return <AirflowDagView instanceId={instanceId} dagId={dagId} onBack={null} />
  }
  return (
    <section className={tab === "graph" ? "catalog-page section-fill" : "catalog-page"}>
      <header className="catalog-hero agent-hero">
        <div className="catalog-hero-top">
          <div className="catalog-hero-copy">
            <p className="catalog-kicker">Agent</p>
            <div className="catalog-title-line">
              <h1>{agent?.name ?? "Agent"}</h1>
              <span className="agent-chip">{visibility}</span>
              {agent !== null ? (
                <span className={`agent-chip agent-status agent-status-${agent.status.toLowerCase()}`}>
                  {agent.status}
                </span>
              ) : null}
            </div>
            {sourceKey !== null ? <p className="agent-qualified">{sourceKey}</p> : null}
            {agent?.kind === "SCHEMA" ? <SchemaSummary detail={agent.detail} /> : null}
            {agent?.description !== null && agent?.description !== undefined && agent.description !== "" ? (
              <p className="catalog-lead">{agent.description}</p>
            ) : null}
            {tags.length > 0 ? (
              <div className="agent-tags agent-hero-tags">
                {tags.map((tag: string) => (
                  <span key={tag} className="agent-chip agent-tag">
                    {tag}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
          <div className="catalog-hero-actions">
            <button
              className="button"
              type="button"
              disabled={building || agent === null}
              onClick={() => {
                setBuilding(true)
                setError(null)
                void requestAgentBuild(agentId).catch((err: unknown) => {
                  setBuilding(false)
                  setError(err instanceof Error ? err.message : "Could not build context")
                })
              }}
            >
              {building ? "Building context…" : "Build context"}
            </button>
          </div>
        </div>
      </header>
      {error !== null ? <p className="error-text agent-alert">{error}</p> : null}
      <div className="tabs" role="tablist" aria-label="Agent sections">
        {allowed.map((name: AgentTab) => (
          <button
            key={name}
            type="button"
            className={tab === name ? "tab selected" : "tab"}
            role="tab"
            aria-selected={tab === name}
            onClick={() => {
              selectTab(name)
            }}
          >
            {TAB_LABELS[name]}
          </button>
        ))}
      </div>
      {tableTab !== null && agent !== null && tableId !== null ? (
        <TableAgentView
          agent={agent}
          tableId={tableId}
          tab={tableTab}
          onAgentChanged={() => {
            void fetchAgent(agent.id).then(setAgent)
          }}
        />
      ) : null}
      {tab === "overview" && agent !== null && !isTable ? (
        <div className="agent-sheet">
          <section className="agent-card">
            <ContextEditor
              kind="agent"
              id={agent.id}
              name={agent.name}
              context={agent.context}
              origin={agent.context_origin}
              onChanged={() => {
                void fetchAgent(agent.id).then(setAgent)
              }}
            />
          </section>
        </div>
      ) : null}
      {tab === "source" && agent !== null && !isTable ? (
        nodeId !== null ? (
          <WorkspaceFilePage nodeId={nodeId} />
        ) : (
          <pre>{JSON.stringify(agent.detail, null, 2)}</pre>
        )
      ) : null}
      {tab === "runs" ? <AgentRuns agentId={agentId} /> : null}
      {tab === "agent" && agent !== null ? (
        <AgentPackPanel agentId={agent.id} agentKind={agent.kind} />
      ) : null}
      {tab === "config" ? (
        <div className="agent-sheet">
          <section className="agent-card agent-config">
            <h2>Refresh</h2>
            <p className="catalog-lead">Choose when this agent rebuilds its context.</p>
            <label className="agent-field">
              <span>Mode</span>
              <select
                value={config.refreshMode}
                onChange={(event) => {
                  const mode: RefreshMode = event.target.value === "ON_CHANGE" || event.target.value === "MANUAL"
                    ? event.target.value
                    : "INTERVAL"
                  config.commit(config.minutes, config.enabled, config.options, config.model, config.effort, mode)
                }}
              >
                <option value="INTERVAL">Interval</option>
                <option value="ON_CHANGE">On change</option>
                <option value="MANUAL">Manual</option>
              </select>
            </label>
            <div className="config-options">
              <h2>Context build</h2>
              {optionsFromFields(fields, config.options).map((option: ConfigOption) => (
                <label key={option.key} className="config-check">
                  <input
                    type="checkbox"
                    checked={option.checked}
                    disabled={config.busy || !config.loaded}
                    onChange={(event) => {
                      const next: Record<string, boolean> = {
                        ...config.options,
                        [option.key]: event.target.checked,
                      }
                      config.commit(
                        config.minutes,
                        config.enabled,
                        next,
                        config.model,
                        config.effort,
                        config.refreshMode,
                      )
                    }}
                  />
                  {option.label}
                </label>
              ))}
            </div>
          </section>
        </div>
      ) : null}
    </section>
  )
}

function SchemaSummary(props: { detail: Record<string, unknown> }): ReactElement {
  const catalog: string | null = detailString(props.detail, "catalog")
  const namespace: string | null = detailString(props.detail, "namespace")
  const tables: unknown = props.detail.tables
  const names: string[] = Array.isArray(tables)
    ? tables.filter((item: unknown): item is string => typeof item === "string")
    : []
  const qualified: string = catalog !== null && namespace !== null ? `${catalog}.${namespace}` : ""
  return (
    <p className="agent-qualified">
      {qualified}
      {names.length > 0 ? ` · ${names.join(", ")}` : ""}
    </p>
  )
}

function optionFieldsForKind(kind: AgentKind | undefined): readonly OptionField[] {
  if (kind === "TABLE" || kind === "SCHEMA") {
    return TABLE_ASSET_FIELDS
  }
  if (kind === "FILE" || kind === "NOTEBOOK") {
    return FILE_ASSET_FIELDS
  }
  if (kind === "AIRFLOW_DAG" || kind === "AIRFLOW_INSTANCE") {
    return AIRFLOW_ASSET_FIELDS
  }
  return []
}

function isTableViewTab(tab: AgentTab): tab is TableAgentTab {
  const names: readonly string[] = TABLE_VIEW_TABS
  return names.includes(tab)
}

function declaredTags(agent: AgentRecord): string[] {
  if (Array.isArray(agent.tags) && agent.tags.length > 0) {
    return agent.tags.filter((tag: string) => tag !== "")
  }
  const raw: unknown = agent.properties.tags
  if (!Array.isArray(raw)) {
    return []
  }
  return raw.filter((item: unknown): item is string => typeof item === "string" && item !== "")
}

function propertyString(properties: Record<string, unknown>, key: string): string | null {
  const value: unknown = properties[key]
  if (typeof value !== "string" || value === "") {
    return null
  }
  return value
}
