"""Agent search, context builds, and configuration."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from intelegent_data_catalog.agents.agent_pack.models import (
    AgentDetail,
    AgentPackRead,
    AgentWrite,
    MemoryFileDetail,
    MemoryFileRead,
    PromptDetail,
    PromptWrite,
    RuleDetail,
    RuleWrite,
    SkillDetail,
    SkillWrite,
)
from intelegent_data_catalog.agents.execution.models import AgentRunRead, AgentRunTaskDetail
from intelegent_data_catalog.agents.models import (
    AgentAnalysisContext,
    AgentContextOptions,
    AgentPage,
    AgentQuery,
    AgentRead,
    AgentUpdate,
)
from intelegent_data_catalog.api.dependencies import (
    AgentPackDep,
    AgentServiceDep,
    CatalogJobsDep,
    EntityConfigurationDep,
    PlatformDep,
)
from intelegent_data_catalog.entity_configuration import (
    EntityConfigurationRead,
    EntityConfigurationWrite,
)
from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.jobs.models import AgentBuildJobRead, ContextGenerationJobRead
from intelegent_data_catalog.knowledge.models import ContextGenerationOptions
from intelegent_data_catalog.persistence.enums import (
    AgentKind,
    AgentRefreshMode,
    AgentStatus,
    RefreshSubjectKind,
)

router: APIRouter = APIRouter()


@router.get("/agents", response_model=AgentPage)
def search_agents(
    service: AgentServiceDep,
    q: Annotated[str | None, Query()] = None,
    kinds: Annotated[list[AgentKind] | None, Query()] = None,
    formats: Annotated[list[str] | None, Query()] = None,
    owner: Annotated[str, Query()] = "all",
    workspace_id: Annotated[UUID | None, Query()] = None,
    status: Annotated[AgentStatus | None, Query()] = None,
    has_context: Annotated[bool | None, Query()] = None,
    refresh_mode: Annotated[AgentRefreshMode | None, Query()] = None,
    sort: Annotated[str, Query()] = "updated_at",
    order: Annotated[str, Query()] = "desc",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AgentPage:
    """Search agents the caller can see."""
    selected_kinds: list[AgentKind] = [] if kinds is None else kinds
    selected_formats: list[str] = [] if formats is None else formats
    owner_filter: str = owner if owner in {"me", "shared", "all"} else "all"
    sort_key: str = sort if sort in {
        "name",
        "kind",
        "updated_at",
        "last_built_at",
        "freshness_score",
        "context_state",
    } else "updated_at"
    direction: str = "asc" if order == "asc" else "desc"
    return service.search(
        AgentQuery(
            q=q,
            kinds=selected_kinds,
            formats=selected_formats,
            owner=owner_filter,  # type: ignore[arg-type]
            workspace_id=workspace_id,
            status=status,
            has_context=has_context,
            refresh_mode=refresh_mode,
            sort=sort_key,  # type: ignore[arg-type]
            order=direction,  # type: ignore[arg-type]
            limit=limit,
            offset=offset,
        ),
    )




@router.get("/agents/{agent_id}/runs", response_model=list[AgentRunRead])
def list_agent_runs(
    agent_id: UUID,
    service: AgentServiceDep,
    limit: Annotated[int, Query(ge=1, le=25)] = 25,
) -> list[AgentRunRead]:
    """Return the newest operator runs for one agent."""
    return service.list_runs(agent_id, limit)


@router.get(
    "/agents/{agent_id}/runs/{run_id}/tasks/{task_id}",
    response_model=AgentRunTaskDetail,
)
def read_agent_run_task(
    agent_id: UUID,
    run_id: UUID,
    task_id: str,
    service: AgentServiceDep,
) -> AgentRunTaskDetail:
    """Return one task's state, log, and trace events."""
    return service.get_run_task(agent_id, run_id, task_id)


@router.get("/agents/{agent_id}", response_model=AgentRead)
def read_agent(agent_id: UUID, service: AgentServiceDep) -> AgentRead:
    """Return one agent."""
    return service.get(agent_id)


@router.patch("/agents/{agent_id}", response_model=AgentRead)
def update_agent(agent_id: UUID, body: AgentUpdate, service: AgentServiceDep) -> AgentRead:
    """Patch an agent."""
    return service.update(agent_id, body)


@router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(agent_id: UUID, service: AgentServiceDep) -> None:
    """Delete an agent. The source object stays."""
    service.delete(agent_id)


@router.get("/agents/{agent_id}/agent-memory", response_model=list[MemoryFileRead])
def read_agent_memory(agent_id: UUID, service: AgentPackDep) -> list[MemoryFileRead]:
    """List the agent notebook."""
    return service.list_memory(agent_id)


@router.get("/agents/{agent_id}/agent-memory/{name}", response_model=MemoryFileDetail)
def read_agent_memory_file(
    agent_id: UUID,
    name: str,
    service: AgentPackDep,
) -> MemoryFileDetail:
    """Return one notebook file for the preview."""
    return service.get_memory(agent_id, name)


@router.get("/agents/{agent_id}/agent-pack", response_model=AgentPackRead)
def read_agent_pack(agent_id: UUID, service: AgentPackDep) -> AgentPackRead:
    """List prompts, rules, skills, and the agent card for one agent."""
    return service.list_pack(agent_id)


@router.get("/agents/{agent_id}/agent-pack/agent", response_model=AgentDetail)
def read_agent_card(agent_id: UUID, service: AgentPackDep) -> AgentDetail:
    """Return ``AGENTS.md``."""
    return service.get_agent(agent_id)


@router.put("/agents/{agent_id}/agent-pack/agent", response_model=AgentDetail)
def put_agent_card(
    agent_id: UUID,
    body: AgentWrite,
    service: AgentPackDep,
    layer: Annotated[str | None, Query()] = None,
) -> AgentDetail:
    """Replace ``AGENTS.md``. ``layer`` defaults to the most specific directory."""
    return service.put_agent(agent_id, body, layer)


@router.delete("/agents/{agent_id}/agent-pack/agent", status_code=204)
def delete_agent_card(
    agent_id: UUID,
    service: AgentPackDep,
    layer: Annotated[str | None, Query()] = None,
) -> None:
    """Remove ``AGENTS.md`` from ``layer``."""
    service.delete(agent_id, "agent", None, layer)


@router.get("/agents/{agent_id}/agent-pack/prompts/{slug}", response_model=PromptDetail)
def read_agent_prompt(agent_id: UUID, slug: str, service: AgentPackDep) -> PromptDetail:
    """Return one prompt."""
    return service.get_prompt(agent_id, slug)


@router.put("/agents/{agent_id}/agent-pack/prompts/{slug}", response_model=PromptDetail)
def put_agent_prompt(
    agent_id: UUID,
    slug: str,
    body: PromptWrite,
    service: AgentPackDep,
    layer: Annotated[str | None, Query()] = None,
) -> PromptDetail:
    """Write one prompt. ``layer`` defaults to the most specific directory."""
    return service.put_prompt(agent_id, slug, body, layer)


@router.delete("/agents/{agent_id}/agent-pack/prompts/{slug}", status_code=204)
def delete_agent_prompt(
    agent_id: UUID,
    slug: str,
    service: AgentPackDep,
    layer: Annotated[str | None, Query()] = None,
) -> None:
    """Remove one prompt from ``layer``."""
    service.delete(agent_id, "prompts", slug, layer)


@router.get("/agents/{agent_id}/agent-pack/rules/{slug}", response_model=RuleDetail)
def read_agent_rule(agent_id: UUID, slug: str, service: AgentPackDep) -> RuleDetail:
    """Return one rule."""
    return service.get_rule(agent_id, slug)


@router.put("/agents/{agent_id}/agent-pack/rules/{slug}", response_model=RuleDetail)
def put_agent_rule(
    agent_id: UUID,
    slug: str,
    body: RuleWrite,
    service: AgentPackDep,
    layer: Annotated[str | None, Query()] = None,
) -> RuleDetail:
    """Write one rule. ``layer`` defaults to the most specific directory."""
    return service.put_rule(agent_id, slug, body, layer)


@router.delete("/agents/{agent_id}/agent-pack/rules/{slug}", status_code=204)
def delete_agent_rule(
    agent_id: UUID,
    slug: str,
    service: AgentPackDep,
    layer: Annotated[str | None, Query()] = None,
) -> None:
    """Remove one rule from ``layer``."""
    service.delete(agent_id, "rules", slug, layer)


@router.get("/agents/{agent_id}/agent-pack/skills/{slug}", response_model=SkillDetail)
def read_agent_skill(agent_id: UUID, slug: str, service: AgentPackDep) -> SkillDetail:
    """Return one skill."""
    return service.get_skill(agent_id, slug)


@router.put("/agents/{agent_id}/agent-pack/skills/{slug}", response_model=SkillDetail)
def put_agent_skill(
    agent_id: UUID,
    slug: str,
    body: SkillWrite,
    service: AgentPackDep,
    layer: Annotated[str | None, Query()] = None,
) -> SkillDetail:
    """Write one skill. ``layer`` defaults to the most specific directory."""
    return service.put_skill(agent_id, slug, body, layer)


@router.delete("/agents/{agent_id}/agent-pack/skills/{slug}", status_code=204)
def delete_agent_skill(
    agent_id: UUID,
    slug: str,
    service: AgentPackDep,
    layer: Annotated[str | None, Query()] = None,
) -> None:
    """Remove one skill directory from ``layer``."""
    service.delete(agent_id, "skills", slug, layer)


@router.get("/agents/{agent_id}/configuration", response_model=EntityConfigurationRead)
def read_agent_configuration(
    agent_id: UUID,
    service: EntityConfigurationDep,
) -> EntityConfigurationRead:
    """Return the refresh settings for one agent."""
    return service.get(RefreshSubjectKind.AGENT, agent_id)


@router.put("/agents/{agent_id}/configuration", response_model=EntityConfigurationRead)
def put_agent_configuration(
    agent_id: UUID,
    body: EntityConfigurationWrite,
    service: EntityConfigurationDep,
) -> EntityConfigurationRead:
    """Save the refresh settings for one agent."""
    return service.put(RefreshSubjectKind.AGENT, agent_id, body)


@router.get("/agents/{agent_id}/knowledge-context", response_model=AgentAnalysisContext)
def read_agent_context(agent_id: UUID, platform: PlatformDep) -> AgentAnalysisContext:
    """Return the context last built for this agent."""
    return platform.read_agent_context(agent_id)


@router.post(
    "/agents/{agent_id}/knowledge-context",
    response_model=AgentBuildJobRead,
    status_code=202,
)
def build_agent_context(
    agent_id: UUID,
    platform: PlatformDep,
    builds: CatalogJobsDep,
    configurations: EntityConfigurationDep,
    body: AgentContextOptions | None = None,
) -> AgentBuildJobRead:
    """Start an agent context build."""
    name: str = platform.agent_name(agent_id)
    saved: EntityConfigurationRead = configurations.get(RefreshSubjectKind.AGENT, agent_id)
    selected: AgentContextOptions = body if body is not None else AgentContextOptions(
        model=saved.model,
        effort=saved.effort,
        flags=saved.options,
    )
    return builds.start_agent(agent_id, name, selected)


@router.get("/agent-builds/{job_id}", response_model=AgentBuildJobRead)
def read_agent_build(job_id: UUID, builds: CatalogJobsDep) -> AgentBuildJobRead:
    """Return one agent build."""
    job: AgentBuildJobRead | None = builds.get_agent(job_id)
    if job is None:
        raise EntityNotFoundError("agent build", str(job_id))
    return job


@router.get("/agents/{agent_id}/knowledge-context/build", response_model=AgentBuildJobRead)
def read_latest_agent_build(agent_id: UUID, builds: CatalogJobsDep) -> AgentBuildJobRead:
    """Return the newest agent build."""
    job: AgentBuildJobRead | None = builds.latest_agent(agent_id)
    if job is None:
        raise EntityNotFoundError("agent build", str(agent_id))
    return job


@router.post(
    "/agents/{agent_id}/context/generate",
    response_model=ContextGenerationJobRead,
    status_code=202,
)
def generate_agent_context(
    agent_id: UUID,
    platform: PlatformDep,
    builds: CatalogJobsDep,
    configurations: EntityConfigurationDep,
) -> ContextGenerationJobRead:
    """Ask the agent agent for business context."""
    name: str = platform.agent_name(agent_id)
    saved: EntityConfigurationRead = configurations.get(RefreshSubjectKind.AGENT, agent_id)
    return builds.start_context(
        agent_id,
        name,
        ContextGenerationOptions(kind="agent", model=saved.model, effort=saved.effort),
        lane="agent",
    )


@router.get("/agents/{agent_id}/context/generation", response_model=ContextGenerationJobRead)
def read_agent_generation(agent_id: UUID, builds: CatalogJobsDep) -> ContextGenerationJobRead:
    """Return the newest context generation for an agent."""
    job: ContextGenerationJobRead | None = builds.latest_context(agent_id, "agent")
    if job is None:
        raise EntityNotFoundError("context generation", str(agent_id))
    return job












