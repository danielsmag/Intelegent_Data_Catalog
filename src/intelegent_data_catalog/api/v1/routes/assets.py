"""Asset search, membership, context builds, and configuration."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from intelegent_data_catalog.api.dependencies import (
    AgentPackDep,
    AssetServiceDep,
    CatalogJobsDep,
    EntityConfigurationDep,
    PlatformDep,
)
from intelegent_data_catalog.assets.agent_pack.models import (
    AgentDetail,
    AgentPackRead,
    AgentWrite,
    PromptDetail,
    PromptWrite,
    RuleDetail,
    RuleWrite,
    SkillDetail,
    SkillWrite,
)
from intelegent_data_catalog.assets.execution.models import AssetRunRead, AssetRunTaskDetail
from intelegent_data_catalog.assets.models import (
    AssetAnalysisContext,
    AssetContextOptions,
    AssetIdsWrite,
    AssetPage,
    AssetQuery,
    AssetRead,
    AssetUpdate,
    BulkMembershipResult,
    BulkMembershipWrite,
)
from intelegent_data_catalog.entity_configuration import (
    EntityConfigurationRead,
    EntityConfigurationWrite,
)
from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.jobs.models import AssetBuildJobRead, ContextGenerationJobRead
from intelegent_data_catalog.knowledge.models import ContextGenerationOptions
from intelegent_data_catalog.persistence.enums import (
    AssetKind,
    AssetRefreshMode,
    AssetStatus,
    RefreshSubjectKind,
)

router: APIRouter = APIRouter()


@router.get("/assets", response_model=AssetPage)
def search_assets(
    service: AssetServiceDep,
    q: Annotated[str | None, Query()] = None,
    kinds: Annotated[list[AssetKind] | None, Query()] = None,
    formats: Annotated[list[str] | None, Query()] = None,
    owner: Annotated[str, Query()] = "all",
    workspace_id: Annotated[UUID | None, Query()] = None,
    scope_id: Annotated[UUID | None, Query()] = None,
    space_id: Annotated[UUID | None, Query()] = None,
    status: Annotated[AssetStatus | None, Query()] = None,
    has_context: Annotated[bool | None, Query()] = None,
    refresh_mode: Annotated[AssetRefreshMode | None, Query()] = None,
    sort: Annotated[str, Query()] = "updated_at",
    order: Annotated[str, Query()] = "desc",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AssetPage:
    """Search assets the caller can see."""
    selected_kinds: list[AssetKind] = [] if kinds is None else kinds
    selected_formats: list[str] = [] if formats is None else formats
    owner_filter: str = owner if owner in {"me", "shared", "all"} else "all"
    sort_key: str = sort if sort in {
        "name",
        "kind",
        "updated_at",
        "last_built_at",
        "membership_count",
        "freshness_score",
        "context_state",
    } else "updated_at"
    direction: str = "asc" if order == "asc" else "desc"
    return service.search(
        AssetQuery(
            q=q,
            kinds=selected_kinds,
            formats=selected_formats,
            owner=owner_filter,  # type: ignore[arg-type]
            workspace_id=workspace_id,
            scope_id=scope_id,
            space_id=space_id,
            status=status,
            has_context=has_context,
            refresh_mode=refresh_mode,
            sort=sort_key,  # type: ignore[arg-type]
            order=direction,  # type: ignore[arg-type]
            limit=limit,
            offset=offset,
        ),
    )


@router.post("/assets/bulk-membership", response_model=BulkMembershipResult)
def bulk_membership(body: BulkMembershipWrite, service: AssetServiceDep) -> BulkMembershipResult:
    """Attach or detach many assets."""
    return service.bulk(body)


@router.get("/assets/{asset_id}/runs", response_model=list[AssetRunRead])
def list_asset_runs(
    asset_id: UUID,
    service: AssetServiceDep,
    limit: Annotated[int, Query(ge=1, le=25)] = 25,
) -> list[AssetRunRead]:
    """Return the newest operator runs for one asset."""
    return service.list_runs(asset_id, limit)


@router.get(
    "/assets/{asset_id}/runs/{run_id}/tasks/{task_id}",
    response_model=AssetRunTaskDetail,
)
def read_asset_run_task(
    asset_id: UUID,
    run_id: UUID,
    task_id: str,
    service: AssetServiceDep,
) -> AssetRunTaskDetail:
    """Return one task's state, log, and trace events."""
    return service.get_run_task(asset_id, run_id, task_id)


@router.get("/assets/{asset_id}", response_model=AssetRead)
def read_asset(asset_id: UUID, service: AssetServiceDep) -> AssetRead:
    """Return one asset."""
    return service.get(asset_id)


@router.patch("/assets/{asset_id}", response_model=AssetRead)
def update_asset(asset_id: UUID, body: AssetUpdate, service: AssetServiceDep) -> AssetRead:
    """Patch an asset."""
    return service.update(asset_id, body)


@router.delete("/assets/{asset_id}", status_code=204)
def delete_asset(asset_id: UUID, service: AssetServiceDep) -> None:
    """Delete an asset. The source object stays."""
    service.delete(asset_id)


@router.get("/assets/{asset_id}/agent-pack", response_model=AgentPackRead)
def read_agent_pack(asset_id: UUID, service: AgentPackDep) -> AgentPackRead:
    """List prompts, rules, skills, and the agent card for one asset."""
    return service.list_pack(asset_id)


@router.get("/assets/{asset_id}/agent-pack/agent", response_model=AgentDetail)
def read_agent_card(asset_id: UUID, service: AgentPackDep) -> AgentDetail:
    """Return ``AGENTS.md``."""
    return service.get_agent(asset_id)


@router.put("/assets/{asset_id}/agent-pack/agent", response_model=AgentDetail)
def put_agent_card(asset_id: UUID, body: AgentWrite, service: AgentPackDep) -> AgentDetail:
    """Replace ``AGENTS.md``."""
    return service.put_agent(asset_id, body)


@router.delete("/assets/{asset_id}/agent-pack/agent", status_code=204)
def delete_agent_card(asset_id: UUID, service: AgentPackDep) -> None:
    """Remove ``AGENTS.md``."""
    service.delete(asset_id, "agent", None)


@router.get("/assets/{asset_id}/agent-pack/prompts/{slug}", response_model=PromptDetail)
def read_agent_prompt(asset_id: UUID, slug: str, service: AgentPackDep) -> PromptDetail:
    """Return one prompt."""
    return service.get_prompt(asset_id, slug)


@router.put("/assets/{asset_id}/agent-pack/prompts/{slug}", response_model=PromptDetail)
def put_agent_prompt(
    asset_id: UUID,
    slug: str,
    body: PromptWrite,
    service: AgentPackDep,
) -> PromptDetail:
    """Write one prompt."""
    return service.put_prompt(asset_id, slug, body)


@router.delete("/assets/{asset_id}/agent-pack/prompts/{slug}", status_code=204)
def delete_agent_prompt(asset_id: UUID, slug: str, service: AgentPackDep) -> None:
    """Remove one prompt."""
    service.delete(asset_id, "prompts", slug)


@router.get("/assets/{asset_id}/agent-pack/rules/{slug}", response_model=RuleDetail)
def read_agent_rule(asset_id: UUID, slug: str, service: AgentPackDep) -> RuleDetail:
    """Return one rule."""
    return service.get_rule(asset_id, slug)


@router.put("/assets/{asset_id}/agent-pack/rules/{slug}", response_model=RuleDetail)
def put_agent_rule(
    asset_id: UUID,
    slug: str,
    body: RuleWrite,
    service: AgentPackDep,
) -> RuleDetail:
    """Write one rule."""
    return service.put_rule(asset_id, slug, body)


@router.delete("/assets/{asset_id}/agent-pack/rules/{slug}", status_code=204)
def delete_agent_rule(asset_id: UUID, slug: str, service: AgentPackDep) -> None:
    """Remove one rule."""
    service.delete(asset_id, "rules", slug)


@router.get("/assets/{asset_id}/agent-pack/skills/{slug}", response_model=SkillDetail)
def read_agent_skill(asset_id: UUID, slug: str, service: AgentPackDep) -> SkillDetail:
    """Return one skill."""
    return service.get_skill(asset_id, slug)


@router.put("/assets/{asset_id}/agent-pack/skills/{slug}", response_model=SkillDetail)
def put_agent_skill(
    asset_id: UUID,
    slug: str,
    body: SkillWrite,
    service: AgentPackDep,
) -> SkillDetail:
    """Write one skill."""
    return service.put_skill(asset_id, slug, body)


@router.delete("/assets/{asset_id}/agent-pack/skills/{slug}", status_code=204)
def delete_agent_skill(asset_id: UUID, slug: str, service: AgentPackDep) -> None:
    """Remove one skill directory."""
    service.delete(asset_id, "skills", slug)


@router.get("/assets/{asset_id}/configuration", response_model=EntityConfigurationRead)
def read_asset_configuration(
    asset_id: UUID,
    service: EntityConfigurationDep,
) -> EntityConfigurationRead:
    """Return the refresh settings for one asset."""
    return service.get(RefreshSubjectKind.ASSET, asset_id)


@router.put("/assets/{asset_id}/configuration", response_model=EntityConfigurationRead)
def put_asset_configuration(
    asset_id: UUID,
    body: EntityConfigurationWrite,
    service: EntityConfigurationDep,
) -> EntityConfigurationRead:
    """Save the refresh settings for one asset."""
    return service.put(RefreshSubjectKind.ASSET, asset_id, body)


@router.get("/assets/{asset_id}/knowledge-context", response_model=AssetAnalysisContext)
def read_asset_context(asset_id: UUID, platform: PlatformDep) -> AssetAnalysisContext:
    """Return the context last built for this asset."""
    return platform.read_asset_context(asset_id)


@router.post(
    "/assets/{asset_id}/knowledge-context",
    response_model=AssetBuildJobRead,
    status_code=202,
)
def build_asset_context(
    asset_id: UUID,
    platform: PlatformDep,
    builds: CatalogJobsDep,
    configurations: EntityConfigurationDep,
    body: AssetContextOptions | None = None,
) -> AssetBuildJobRead:
    """Start an asset context build."""
    name: str = platform.asset_name(asset_id)
    saved: EntityConfigurationRead = configurations.get(RefreshSubjectKind.ASSET, asset_id)
    selected: AssetContextOptions = body if body is not None else AssetContextOptions(
        model=saved.model,
        effort=saved.effort,
        flags=saved.options,
    )
    return builds.start_asset(asset_id, name, selected)


@router.get("/asset-builds/{job_id}", response_model=AssetBuildJobRead)
def read_asset_build(job_id: UUID, builds: CatalogJobsDep) -> AssetBuildJobRead:
    """Return one asset build."""
    job: AssetBuildJobRead | None = builds.get_asset(job_id)
    if job is None:
        raise EntityNotFoundError("asset build", str(job_id))
    return job


@router.get("/assets/{asset_id}/knowledge-context/build", response_model=AssetBuildJobRead)
def read_latest_asset_build(asset_id: UUID, builds: CatalogJobsDep) -> AssetBuildJobRead:
    """Return the newest asset build."""
    job: AssetBuildJobRead | None = builds.latest_asset(asset_id)
    if job is None:
        raise EntityNotFoundError("asset build", str(asset_id))
    return job


@router.post(
    "/assets/{asset_id}/context/generate",
    response_model=ContextGenerationJobRead,
    status_code=202,
)
def generate_asset_context(
    asset_id: UUID,
    platform: PlatformDep,
    builds: CatalogJobsDep,
    configurations: EntityConfigurationDep,
) -> ContextGenerationJobRead:
    """Ask the asset agent for business context."""
    name: str = platform.asset_name(asset_id)
    saved: EntityConfigurationRead = configurations.get(RefreshSubjectKind.ASSET, asset_id)
    return builds.start_context(
        asset_id,
        name,
        ContextGenerationOptions(kind="asset", model=saved.model, effort=saved.effort),
        lane="asset",
    )


@router.get("/assets/{asset_id}/context/generation", response_model=ContextGenerationJobRead)
def read_asset_generation(asset_id: UUID, builds: CatalogJobsDep) -> ContextGenerationJobRead:
    """Return the newest context generation for an asset."""
    job: ContextGenerationJobRead | None = builds.latest_context(asset_id, "asset")
    if job is None:
        raise EntityNotFoundError("context generation", str(asset_id))
    return job


@router.get("/scopes/{scope_id}/assets", response_model=list[AssetRead])
def list_scope_assets(scope_id: UUID, service: AssetServiceDep) -> list[AssetRead]:
    """Assets attached to a scope."""
    return service.list_scope(scope_id)


@router.post("/scopes/{scope_id}/assets", response_model=BulkMembershipResult)
def attach_scope_assets(
    scope_id: UUID,
    body: AssetIdsWrite,
    service: AssetServiceDep,
) -> BulkMembershipResult:
    """Attach assets to a scope."""
    return service.attach_many("scope", scope_id, body.asset_ids)


@router.delete("/scopes/{scope_id}/assets/{asset_id}", status_code=204)
def detach_scope_asset(scope_id: UUID, asset_id: UUID, service: AssetServiceDep) -> None:
    """Remove an asset from a scope."""
    service.detach_one("scope", scope_id, asset_id)


@router.get("/spaces/{space_id}/assets", response_model=list[AssetRead])
def list_space_assets(space_id: UUID, service: AssetServiceDep) -> list[AssetRead]:
    """Assets attached to a space."""
    return service.list_space(space_id)


@router.post("/spaces/{space_id}/assets", response_model=BulkMembershipResult)
def attach_space_assets(
    space_id: UUID,
    body: AssetIdsWrite,
    service: AssetServiceDep,
) -> BulkMembershipResult:
    """Attach assets to a space."""
    return service.attach_many("space", space_id, body.asset_ids)


@router.delete("/spaces/{space_id}/assets/{asset_id}", status_code=204)
def detach_space_asset(space_id: UUID, asset_id: UUID, service: AssetServiceDep) -> None:
    """Remove an asset from a space."""
    service.detach_one("space", space_id, asset_id)
