"""Read Marquez jobs. Dataset names are mapped later; they are not catalog ids."""

from typing import Protocol

import httpx

from intelegent_data_catalog.exceptions import CatalogError
from intelegent_data_catalog.graph.projection import LineageLink
from intelegent_data_catalog.sources.trino.client import sanitize_engine_message


class MarquezError(CatalogError):
    """Raised when Marquez cannot be read. The message is sanitized."""

    def __init__(self, message: str = "Marquez is unavailable") -> None:
        super().__init__(message)


class LineageReader(Protocol):
    """Supplies captured job inputs and outputs."""

    def read_links(self) -> list[LineageLink]:
        """Return one link per job input/output pair."""


class MarquezClient:
    """HTTP reader for the Marquez API."""

    def __init__(self, base_url: str, timeout_seconds: float = 10.0) -> None:
        self._base_url: str = base_url.rstrip("/")
        self._timeout_seconds: float = timeout_seconds

    def read_links(self) -> list[LineageLink]:
        """Read jobs and keep inputs and outputs that Marquez stored."""
        try:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                namespaces: list[str] = _names(
                    self._get(client, "/api/v1/namespaces"), "namespaces"
                )
                links: list[LineageLink] = []
                namespace: str
                for namespace in namespaces:
                    jobs: list[dict[str, object]] = _object_list(
                        self._get(client, f"/api/v1/namespaces/{namespace}/jobs"),
                        "jobs",
                    )
                    job: dict[str, object]
                    for job in jobs:
                        job_name: str = _nested_name(job)
                        detail: dict[str, object] = self._get(
                            client,
                            f"/api/v1/namespaces/{namespace}/jobs/{job_name}",
                        )
                        links.extend(_links(namespace, job_name, detail))
                return links
        except MarquezError:
            raise
        except httpx.HTTPError as err:
            raise MarquezError(sanitize_engine_message(str(err))) from err

    def _get(self, client: httpx.Client, path: str) -> dict[str, object]:
        response: httpx.Response = client.get(f"{self._base_url}{path}")
        if response.status_code >= 400:
            raise MarquezError(f"Marquez returned {response.status_code}")
        payload: object = response.json()
        if not isinstance(payload, dict):
            raise MarquezError("Marquez returned an unexpected payload")
        return payload


def _links(namespace: str, job_name: str, detail: dict[str, object]) -> list[LineageLink]:
    inputs: list[tuple[str, str]] = _datasets(detail.get("inputs"))
    outputs: list[tuple[str, str]] = _datasets(detail.get("outputs"))
    links: list[LineageLink] = []
    source: tuple[str, str]
    target: tuple[str, str]
    for source in inputs:
        for target in outputs:
            links.append(
                LineageLink(
                    input_namespace=source[0],
                    input_name=source[1],
                    job_namespace=namespace,
                    job_name=job_name,
                    output_namespace=target[0],
                    output_name=target[1],
                )
            )
    return links


def _datasets(value: object) -> list[tuple[str, str]]:
    if not isinstance(value, list):
        return []
    found: list[tuple[str, str]] = []
    item: object
    for item in value:
        if not isinstance(item, dict):
            continue
        namespace: object = item.get("namespace")
        name: object = item.get("name")
        if isinstance(namespace, str) and isinstance(name, str):
            found.append((namespace, name))
    return found


def _names(payload: dict[str, object], key: str) -> list[str]:
    raw: object = payload.get(key)
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    item: object
    for item in raw:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(str(item["name"]))
        elif isinstance(item, str):
            names.append(item)
    return names


def _object_list(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    raw: object = payload.get(key)
    if not isinstance(raw, list):
        return []
    found: list[dict[str, object]] = []
    item: object
    for item in raw:
        if isinstance(item, dict):
            found.append(item)
    return found


def _nested_name(job: dict[str, object]) -> str:
    name: object = job.get("name")
    if isinstance(name, str) and name != "":
        return name
    identifier: object = job.get("id")
    if isinstance(identifier, dict) and isinstance(identifier.get("name"), str):
        return str(identifier["name"])
    raise MarquezError("Marquez job is missing a name")
