"""Kernel startup: Spark catalogs, Trino helper, and grid formatting.

Runs inside the user namespace before the first cell. ``spark`` is one local
session. Each Postgres source is its own JDBC catalog, named by binding key.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent))


def enable_pyspark() -> None:
    """Put the image's PySpark on the path. The kernel env does not inherit it."""
    spark_home: str = os.environ.get("SPARK_HOME", "/opt/spark")
    os.environ["SPARK_HOME"] = spark_home
    python_dir: Path = Path(spark_home) / "python"
    if python_dir.is_dir():
        sys.path.insert(0, str(python_dir))
    lib_dir: Path = python_dir / "lib"
    if lib_dir.is_dir():
        archive: Path
        for archive in sorted(lib_dir.glob("py4j-*.zip")):
            sys.path.insert(0, str(archive))


enable_pyspark()

from table_format import CATALOG_MIME, ROW_CAP, table_payload

_PROPERTIES: Path = Path("/tmp/spark-catalog.properties")
_NOTEBOOK_ENV: Path = Path("/bootstrap/notebook.env")
_JDBC_CATALOG: str = "org.apache.spark.sql.execution.datasources.v2.jdbc.JDBCTableCatalog"
_POSTGRES_DRIVER: str = "org.postgresql.Driver"


class EngineEndpoint:
    """One Trino coordinator the notebook can query."""

    def __init__(self, name: str, host: str, port: int, default: bool) -> None:
        self.name: str = name
        self.host: str = host
        self.port: int = port
        self.default: bool = default

    def __repr__(self) -> str:
        marker: str = " default" if self.default else ""
        return f"EngineEndpoint(name={self.name!r}, host={self.host!r}, port={self.port}{marker})"


def read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines. Missing file returns an empty mapping."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    line: str
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "" or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def load_properties(path: Path) -> dict[str, str]:
    """Read a Spark properties file written by the gateway entrypoint."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    line: str
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "" or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def load_engines(raw: str) -> dict[str, EngineEndpoint]:
    """Parse CATALOG_ENGINES_JSON. Bad entries are skipped."""
    engines: dict[str, EngineEndpoint] = {}
    try:
        payload: object = json.loads(raw) if raw.strip() != "" else []
    except json.JSONDecodeError:
        catalog_warnings.append("CATALOG_ENGINES_JSON is not valid JSON")
        return engines
    if not isinstance(payload, list):
        return engines
    entry: object
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        host = entry.get("host")
        port = entry.get("port")
        if not isinstance(name, str) or not isinstance(host, str) or not isinstance(port, int):
            continue
        engines[name] = EngineEndpoint(
            name=name,
            host=host,
            port=port,
            default=entry.get("default") is True,
        )
    return engines


def register_jdbc_catalogs(builder: object, raw: str) -> object:
    """Attach one Spark JDBC catalog per Postgres source."""
    try:
        payload: object = json.loads(raw) if raw.strip() != "" else []
    except json.JSONDecodeError:
        catalog_warnings.append("CATALOG_SPARK_CATALOGS_JSON is not valid JSON")
        return builder
    if not isinstance(payload, list):
        return builder
    entry: object
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or name == "":
            continue
        error = entry.get("error")
        if isinstance(error, str) and error != "":
            catalog_warnings.append(f"{name}: {error}")
            continue
        url = entry.get("url")
        user = entry.get("user")
        password = entry.get("password")
        driver = entry.get("driver")
        if not all(isinstance(value, str) and value != "" for value in (url, user, password)):
            catalog_warnings.append(f"{name}: incomplete JDBC catalog")
            continue
        driver_name: str = driver if isinstance(driver, str) and driver != "" else _POSTGRES_DRIVER
        prefix: str = f"spark.sql.catalog.{name}"
        builder = _config(builder, prefix, _JDBC_CATALOG)
        builder = _config(builder, f"{prefix}.url", cast(str, url))
        builder = _config(builder, f"{prefix}.driver", driver_name)
        builder = _config(builder, f"{prefix}.user", cast(str, user))
        builder = _config(builder, f"{prefix}.password", cast(str, password))
        builder = _config(builder, f"{prefix}.pushDownAggregate", "true")
        builder = _config(builder, f"{prefix}.pushDownLimit", "true")
        builder = _config(builder, f"{prefix}.pushDownOffset", "true")
    return builder


def build_spark() -> object | None:
    """Start a local Spark session. Failure leaves pandas and Trino usable."""
    try:
        from pyspark.sql import SparkSession
    except ImportError as err:
        catalog_warnings.append(f"pyspark is not installed: {err}")
        return None
    builder = SparkSession.builder.appName("notebook")
    builder = builder.config("spark.master", "local[1]")
    builder = builder.config("spark.driver.memory", "1g")
    builder = builder.config("spark.driver.host", "127.0.0.1")
    builder = builder.config("spark.driver.bindAddress", "127.0.0.1")
    builder = builder.config("spark.ui.enabled", "false")
    builder = builder.config("spark.sql.shuffle.partitions", "1")
    builder = builder.config("spark.sql.ansi.enabled", "true")
    properties: dict[str, str] = load_properties(_PROPERTIES)
    key: str
    value: str
    for key, value in properties.items():
        builder = builder.config(key, value)
    polaris: dict[str, str] = read_env_file(_NOTEBOOK_ENV)
    client_id: str = polaris.get("POLARIS_CLIENT_ID", "")
    client_secret: str = polaris.get("POLARIS_CLIENT_SECRET", "")
    catalog_name: str = properties.get("spark.sql.defaultCatalog", polaris.get("POLARIS_CATALOG_NAME", ""))
    if client_id != "" and client_secret != "" and catalog_name != "":
        builder = builder.config(
            f"spark.sql.catalog.{catalog_name}.credential",
            f"{client_id}:{client_secret}",
        )
    elif not properties:
        catalog_warnings.append("Iceberg catalog config is missing; spark has no lakehouse catalog")
    builder = register_jdbc_catalogs(builder, os.environ.get("CATALOG_SPARK_CATALOGS_JSON", "[]"))
    try:
        return builder.getOrCreate()
    except Exception as err:
        catalog_warnings.append(f"Spark session did not start: {err}")
        return None


def trino_pandas(sql: str, engine: str | None = None) -> object:
    """Run SQL on a Trino engine and return a pandas DataFrame capped at 500 rows."""
    import pandas
    import trino

    chosen: EngineEndpoint = _pick_engine(engine)
    connection = trino.dbapi.connect(
        host=chosen.host,
        port=chosen.port,
        user=user,
        http_scheme="http",
    )
    try:
        cursor = connection.cursor()
        cursor.execute(sql)
        fetched = cursor.fetchmany(ROW_CAP + 1)
        description = cursor.description or []
    finally:
        connection.close()
    names: list[str] = []
    column: object
    for column in description:
        label = getattr(column, "name", None)
        names.append(str(label) if isinstance(label, str) else "column")
    truncated: bool = len(fetched) > ROW_CAP
    kept = fetched[:ROW_CAP]
    frame = pandas.DataFrame(kept, columns=names)
    frame.attrs["truncated"] = truncated
    return frame


def _pick_engine(engine: str | None) -> EngineEndpoint:
    if len(engines) == 0:
        raise RuntimeError("no Trino engine is configured for this notebook")
    if engine is not None and engine != "":
        found: EngineEndpoint | None = engines.get(engine)
        if found is None:
            known: str = ", ".join(sorted(engines))
            raise RuntimeError(f"unknown Trino engine {engine!r}; known engines: {known}")
        return found
    defaults: list[EngineEndpoint] = [item for item in engines.values() if item.default]
    if len(defaults) == 1:
        return defaults[0]
    if len(engines) == 1:
        return next(iter(engines.values()))
    known = ", ".join(sorted(engines))
    raise RuntimeError(f"pass engine=; more than one Trino connection is available: {known}")


def _config(builder: object, key: str, value: str) -> object:
    configured = getattr(builder, "config")
    return configured(key, value)


def install_formatters() -> None:
    """Show Spark and pandas frames as a catalog grid plus plain text."""
    try:
        shell = get_ipython()  # type: ignore[name-defined]
    except NameError:
        return
    if shell is None:
        return
    formatter = shell.display_formatter.formatters.get(CATALOG_MIME)
    if formatter is None:
        from IPython.core.formatters import BaseFormatter

        class _CatalogFormatter(BaseFormatter):
            format_type = CATALOG_MIME

        formatter = _CatalogFormatter(parent=shell.display_formatter)
        shell.display_formatter.formatters[CATALOG_MIME] = formatter
    text = shell.display_formatter.formatters["text/plain"]
    _register_frame_types(formatter, text)


def _register_frame_types(json_formatter: object, text_formatter: object) -> None:
    types: list[type[object]] = []
    try:
        from pyspark.sql import DataFrame as SparkFrame

        types.append(SparkFrame)
    except ImportError:
        pass
    try:
        from pandas import DataFrame as PandasFrame

        types.append(PandasFrame)
    except ImportError:
        pass
    for_json = getattr(json_formatter, "for_type")
    for_text = getattr(text_formatter, "for_type")
    frame_type: type[object]
    for frame_type in types:
        for_json(frame_type, _frame_json)
        for_text(frame_type, _frame_text)


def _frame_json(frame: object) -> str:
    try:
        return json.dumps(_frame_body(frame))
    except Exception as err:
        return json.dumps({"columns": [], "rows": [], "truncated": False, "error": str(err)})


def _frame_text(frame: object) -> str:
    try:
        body = _frame_body(frame)
    except Exception as err:
        return str(err)
    rows = body.get("rows")
    count: int = len(rows) if isinstance(rows, list) else 0
    suffix: str = " (truncated)" if body.get("truncated") is True else ""
    return f"{count} row(s){suffix}"


def _frame_body(frame: object) -> dict[str, object]:
    if _is_spark_frame(frame):
        return _spark_body(frame)
    return _pandas_body(frame)


def _is_spark_frame(frame: object) -> bool:
    return type(frame).__module__.startswith("pyspark.sql")


def _spark_body(frame: object) -> dict[str, object]:
    limited = frame.limit(ROW_CAP + 1)  # type: ignore[attr-defined]
    rows = limited.collect()
    fields = list(limited.schema.fields)
    truncated: bool = len(rows) > ROW_CAP
    kept = rows[:ROW_CAP]
    columns: list[dict[str, str]] = [
        {"name": field.name, "type": field.dataType.simpleString()} for field in fields
    ]
    encoded: list[list[object]] = []
    row: object
    for row in kept:
        encoded.append([row[index] for index in range(len(fields))])  # type: ignore[index]
    return table_payload(columns, encoded, truncated)


def _pandas_body(frame: object) -> dict[str, object]:
    names = [str(name) for name in list(frame.columns)]  # type: ignore[attr-defined]
    columns: list[dict[str, str]] = [{"name": name, "type": "string"} for name in names]
    values = frame.head(ROW_CAP + 1)  # type: ignore[attr-defined]
    records = values.itertuples(index=False, name=None)
    encoded: list[list[object]] = [list(record) for record in records]
    truncated: bool = len(encoded) > ROW_CAP or bool(getattr(frame, "attrs", {}).get("truncated"))
    return table_payload(columns, encoded[:ROW_CAP], truncated)


catalog_warnings: list[str] = []
engines: dict[str, EngineEndpoint] = load_engines(os.environ.get("CATALOG_ENGINES_JSON", "[]"))
user: str = os.environ.get("CATALOG_USER", "notebook")
spark: object | None = None
try:
    spark = build_spark()
except Exception:
    catalog_warnings.append(traceback.format_exc())

F: object | None = None
try:
    import pyspark.sql.functions as spark_functions

    F = spark_functions
except ImportError:
    F = None

try:
    from IPython.display import display as display
except ImportError:

    def display(obj: object) -> None:
        """Print when IPython display is unavailable."""
        print(obj)


install_formatters()
if catalog_warnings:
    print("\n".join(catalog_warnings))
