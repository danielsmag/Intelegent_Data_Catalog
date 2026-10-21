"""Execute one definition file and return the declarations it registered."""

import threading
import traceback
from types import CodeType

from intelegent_data_catalog.assets.definitions.models import DefinitionSpec
from intelegent_data_catalog.assets.dsl.collector import (
    DefinitionCollector,
    bind_collector,
    reset_collector,
)
from intelegent_data_catalog.assets.dsl.errors import (
    AssetDefinitionError,
    AssetDefinitionImportError,
)
from intelegent_data_catalog.persistence.enums import DefinitionKind


def collect_definitions(
    source: str,
    filename: str,
    expected: DefinitionKind,
    *,
    timeout_seconds: float,
) -> DefinitionSpec:
    """Run ``source`` and return its declarations. Failures become import errors.

    The file is trusted the same way an Airflow DAG file is trusted: only the worker runs it.
    """
    if timeout_seconds <= 0:
        raise AssetDefinitionError("timeout must be positive")
    outcome: list[DefinitionSpec | Exception] = []
    finished: threading.Event = threading.Event()

    def run() -> None:
        try:
            outcome.append(_execute(source, filename, expected))
        except Exception as err:
            outcome.append(err)
        finally:
            finished.set()

    worker: threading.Thread = threading.Thread(
        target=run,
        name="asset-definition-exec",
        daemon=True,
    )
    worker.start()
    completed: bool = finished.wait(timeout_seconds)
    if not completed:
        raise AssetDefinitionImportError(
            f"definition timed out after {timeout_seconds:g} seconds",
            "",
        )
    if len(outcome) == 0:
        raise AssetDefinitionImportError("definition produced no result", "")
    return _unwrap(outcome[0])


def _execute(source: str, filename: str, expected: DefinitionKind) -> DefinitionSpec:
    collector: DefinitionCollector = DefinitionCollector(expected)
    token = bind_collector(collector)
    try:
        try:
            compiled: CodeType = compile(source, filename, "exec")
        except SyntaxError as err:
            message: str = err.msg if err.msg is not None else "syntax error"
            raise AssetDefinitionError(f"syntax error: {message}") from err
        namespace: dict[str, object] = {
            "__name__": "__asset_definition__",
            "__file__": filename,
        }
        exec(compiled, namespace)
        return collector.spec()
    finally:
        reset_collector(token)


def _unwrap(result: DefinitionSpec | Exception) -> DefinitionSpec:
    if isinstance(result, DefinitionSpec):
        return result
    text: str = "".join(traceback.format_exception(result))
    if isinstance(result, AssetDefinitionImportError):
        raise result
    raise AssetDefinitionImportError(str(result), text) from result
