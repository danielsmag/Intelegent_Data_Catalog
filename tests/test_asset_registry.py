"""Asset kind classes and file text extraction."""

from uuid import uuid4

from intelegent_data_catalog.assets.extract import extract_document, referenced_table_names
from intelegent_data_catalog.assets.models import AssetAnalysisContext, AssetContextOptions
from intelegent_data_catalog.knowledge.operations.dag_operation import AirflowDagAgent
from intelegent_data_catalog.knowledge.operations.file_operation import FileAgent
from intelegent_data_catalog.persistence.enums import AssetKind


def test_file_text_strips_html_and_reads_notebook_cells() -> None:
    """HTML tags drop out. Notebook cells and table names stay."""
    document = extract_document(
        "page.html",
        "text/html",
        b"<p>Hello <b>orders</b></p>",
        include_outputs=False,
    )
    html: str = document.text
    assert "Hello" in html
    assert "<p>" not in html
    notebook: bytes = (
        b'{"cells":[{"cell_type":"code","source":["spark.table(\\"catalog.sales.orders\\")\\n"],"outputs":[]}],'
        b'"nbformat":4}'
    )
    extracted = extract_document("job.ipynb", "application/json", notebook, include_outputs=False)
    assert "spark.table" in extracted.text
    assert "orders" in extracted.referenced_tables
    image = extract_document("pic.png", "image/png", b"\x89PNG", include_outputs=False)
    assert image.text == ""


def test_referenced_table_names_keep_the_last_identifier() -> None:
    """Qualified names contribute the table, not the catalog."""
    names: list[str] = referenced_table_names(
        'FROM "app.public.customers" JOIN catalog.sales.orders',
    )
    assert "customers" in names
    assert "orders" in names


class _Finding:
    def __init__(self, dag_row_id: object, summary: str) -> None:
        self.dag_row_id = dag_row_id
        self.summary = summary


class _Built:
    def __init__(self, dag_row_id: object) -> None:
        self.findings = [_Finding(dag_row_id, "loads orders")]
        self.dags_analyzed = 1


class _InstanceAgent:
    def __init__(self) -> None:
        self.seen: set[object] | None = None

    def build_context(self, options: object, dag_row_ids: set[object] | None = None) -> _Built:
        del options
        self.seen = dag_row_ids
        dag_id: object = next(iter(dag_row_ids or []))
        return _Built(dag_id)


class _Dag:
    def __init__(self) -> None:
        self.id = uuid4()
        self.instance_id = uuid4()
        self.asset_id = uuid4()
        self.dag_id = "daily"
        self.description = None
        self.source_code = "print(1)"
        self.tasks: list[dict[str, str]] = []


def test_dag_agent_scopes_the_instance_call_to_one_dag() -> None:
    """A DAG asset asks the instance agent for that DAG only."""
    dag: _Dag = _Dag()
    instance: _InstanceAgent = _InstanceAgent()
    agent: AirflowDagAgent = AirflowDagAgent(dag, instance, None)  # type: ignore[arg-type]
    built: AssetAnalysisContext = agent.build_context(AssetContextOptions())
    assert instance.seen == {dag.id}
    assert built.asset_id == dag.asset_id
    assert built.kind is AssetKind.AIRFLOW_DAG
    assert built.summary == "loads orders"


def test_file_agent_summary_uses_extracted_text_without_a_model() -> None:
    """A file build still returns a summary when no model is configured."""

    class _Node:
        name = "note.md"
        path = "Shared/note.md"
        content_type = "text/markdown"
        object_key = "key"
        asset_id = uuid4()

    class _Session:
        def scalar(self, statement: object) -> _Node:
            del statement
            return _Node()

        def scalars(self, statement: object) -> list[object]:
            del statement
            return []

    class _Objects:
        def get(self, key: str) -> bytes:
            del key
            return b"Customers spend by month"

    class _Asset:
        id = _Node.asset_id
        kind = AssetKind.FILE.value

    agent: FileAgent = FileAgent(_Asset(), _Session(), _Objects(), None)  # type: ignore[arg-type]
    built: AssetAnalysisContext = agent.build_context(
        AssetContextOptions(flags={"include_table_links": False}),
    )
    assert built.summary == "Customers spend by month"
    assert built.kind is AssetKind.FILE
