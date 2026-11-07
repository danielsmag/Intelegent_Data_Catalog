"""Airflow instance metadata. No language model."""

from typing import ClassVar

from pydantic import JsonValue

from intelegent_data_catalog.assets.dsl.operators.base import (
    Operator,
    OperatorContext,
    OperatorObserver,
    _run_step,
)
from intelegent_data_catalog.assets.dsl.operators.ports import AirflowReader
from intelegent_data_catalog.assets.dsl.operators.render import (
    as_json,
    bullet_list,
    heading,
    join_sections,
    json_block,
    model_json,
    table,
)
from intelegent_data_catalog.sources.airflow.models import (
    AirflowAsset,
    AirflowDag,
    AirflowDagRun,
    AirflowDagStats,
    AirflowHealth,
    AirflowImportError,
    AirflowPlugin,
    AirflowPool,
    AirflowProvider,
    AirflowTask,
    AirflowVersion,
)

_TASK_ID: str = "airflow_metadata"


class AirflowMetadata(Operator):
    """Write every Airflow metadata collection the flags leave on."""

    task_id: ClassVar[str] = _TASK_ID
    include_tasks: bool = True
    include_source: bool = True
    include_runs: int = 20
    max_source_chars: int = 20_000
    max_dags: int = 200

    def execute(self, context: OperatorContext, observer: OperatorObserver | None = None) -> None:
        """Read the instance and store ``airflow`` markdown plus the raw JSON."""

        def collect() -> None:
            reader: AirflowReader = context.airflow()
            version: AirflowVersion = reader.version()
            health: AirflowHealth = reader.health()
            dags: list[AirflowDag] = reader.list_dags()
            shown: list[AirflowDag] = dags[: self.max_dags]
            stats: list[AirflowDagStats] = reader.dag_stats([dag.dag_id for dag in shown])
            tasks: dict[str, list[AirflowTask]] = {}
            runs: dict[str, list[AirflowDagRun]] = {}
            sources: dict[str, str] = {}
            dag: AirflowDag
            for dag in shown:
                if self.include_tasks:
                    tasks[dag.dag_id] = reader.dag_tasks(dag.dag_id)
                if self.include_runs > 0:
                    runs[dag.dag_id] = reader.dag_runs(dag.dag_id, self.include_runs)
                if self.include_source:
                    content: str | None = reader.dag_source(dag.dag_id).content
                    sources[dag.dag_id] = _clip(content or "", self.max_source_chars)
            pools: list[AirflowPool] = reader.pools()
            providers: list[AirflowProvider] = reader.providers()
            plugins: list[AirflowPlugin] = reader.plugins()
            errors: list[AirflowImportError] = reader.import_errors()
            assets: list[AirflowAsset] = reader.assets()
            payload: dict[str, JsonValue] = {
                "version": model_json(version),
                "health": model_json(health),
                "dags": [model_json(item) for item in shown],
                "dag_count": len(dags),
                "truncated": len(dags) > len(shown),
                "stats": [model_json(item) for item in stats],
                "tasks": {
                    key: [model_json(item) for item in value] for key, value in tasks.items()
                },
                "runs": {key: [model_json(item) for item in value] for key, value in runs.items()},
                "sources": as_json(sources),
                "pools": [model_json(item) for item in pools],
                "providers": [model_json(item) for item in providers],
                "plugins": [model_json(item) for item in plugins],
                "import_errors": [model_json(item) for item in errors],
                "assets": [model_json(item) for item in assets],
            }
            markdown: str = _markdown(
                version,
                health,
                dags,
                shown,
                stats,
                tasks,
                runs,
                sources,
                pools,
                providers,
                plugins,
                errors,
                assets,
            )
            context.write_output("airflow", markdown, payload)

        _run_step(observer, self.task_id, collect)


def _markdown(
    version: AirflowVersion,
    health: AirflowHealth,
    dags: list[AirflowDag],
    shown: list[AirflowDag],
    stats: list[AirflowDagStats],
    tasks: dict[str, list[AirflowTask]],
    runs: dict[str, list[AirflowDagRun]],
    sources: dict[str, str],
    pools: list[AirflowPool],
    providers: list[AirflowProvider],
    plugins: list[AirflowPlugin],
    errors: list[AirflowImportError],
    assets: list[AirflowAsset],
) -> str:
    stat_by_dag: dict[str, str] = {}
    item: AirflowDagStats
    for item in stats:
        stat_by_dag[item.dag_id] = ", ".join(f"{stat.state}={stat.count}" for stat in item.stats)
    dag_rows: list[list[str]] = [
        [
            dag.dag_id,
            "paused" if dag.is_paused else "active",
            dag.timetable_summary or "",
            stat_by_dag.get(dag.dag_id, ""),
            dag.fileloc or "",
        ]
        for dag in shown
    ]
    sections: list[str] = [
        heading(1, "Airflow"),
        f"Version `{version.version}`.",
        heading(2, "Health"),
        bullet_list(
            [
                f"metadatabase: {health.metadatabase.status or 'unknown'}",
                f"scheduler: {health.scheduler.status or 'unknown'}",
                f"triggerer: {_component(health.triggerer)}",
                f"dag processor: {_component(health.dag_processor)}",
            ],
        ),
        heading(2, f"DAGs ({len(dags)})"),
        _truncation(len(dags), len(shown)),
        table(["dag", "state", "schedule", "run stats", "file"], dag_rows),
    ]
    dag: AirflowDag
    for dag in shown:
        sections.append(heading(3, dag.dag_id))
        if dag.description:
            sections.append(dag.description)
        dag_tasks: list[AirflowTask] = tasks.get(dag.dag_id, [])
        if len(dag_tasks) > 0:
            sections.append(
                table(
                    ["task", "operator", "downstream"],
                    [
                        [
                            task.task_id,
                            task.operator_name or "",
                            ", ".join(task.downstream_task_ids),
                        ]
                        for task in dag_tasks
                    ],
                ),
            )
        dag_runs: list[AirflowDagRun] = runs.get(dag.dag_id, [])
        if len(dag_runs) > 0:
            sections.append(
                table(
                    ["run", "state", "type", "logical date"],
                    [
                        [
                            run.dag_run_id,
                            run.state or "",
                            run.run_type or "",
                            "" if run.logical_date is None else run.logical_date.isoformat(),
                        ]
                        for run in dag_runs
                    ],
                ),
            )
        source: str = sources.get(dag.dag_id, "")
        if source != "":
            sections.append(heading(4, "Source"))
            sections.append(f"```python\n{source}\n```")
    sections.extend(
        [
            heading(2, "Pools"),
            table(
                ["name", "slots", "open", "description"],
                [
                    [pool.name, str(pool.slots), str(pool.open_slots), pool.description or ""]
                    for pool in pools
                ],
            ),
            heading(2, "Providers"),
            table(
                ["package", "version", "description"],
                [[item.package_name, item.version, item.description or ""] for item in providers],
            ),
            heading(2, "Plugins"),
            table(["name", "source"], [[item.name, item.source or ""] for item in plugins]),
            heading(2, "Import errors"),
            table(
                ["file", "bundle", "when"],
                [
                    [
                        item.filename,
                        item.bundle_name or "",
                        "" if item.timestamp is None else item.timestamp.isoformat(),
                    ]
                    for item in errors
                ],
            ),
            heading(2, "Assets"),
            table(
                ["uri", "name"],
                [[item.uri, item.name or ""] for item in assets],
            ),
            heading(2, "Raw"),
            json_block(
                {
                    "version": model_json(version),
                    "health": model_json(health),
                    "dag_count": len(dags),
                },
            ),
        ],
    )
    return join_sections(sections)


def _component(component: object) -> str:
    status: object = getattr(component, "status", None)
    if isinstance(status, str) and status != "":
        return status
    return "unknown"


def _truncation(total: int, shown: int) -> str:
    if total <= shown:
        return ""
    return f"Showing the first {shown} of {total} DAGs."


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n# truncated"
