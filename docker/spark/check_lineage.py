"""Fail the seed job unless Marquez stored customer_spend lineage."""

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


def main() -> None:
    """Require the customers + orders -> customer_spend edge, or only probe for it."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser()
    parser.add_argument(
        "--if-present",
        action="store_true",
        help="Exit 0 when the lineage edge exists, otherwise exit 1 without waiting.",
    )
    args: argparse.Namespace = parser.parse_args()
    base_url: str = os.environ["MARQUEZ_URL"].rstrip("/")
    namespace_name: str = os.environ.get("OPENLINEAGE_NAMESPACE", "catalog")
    if args.if_present:
        if _has_spend_edge(base_url):
            print(f"lineage already present namespace={namespace_name}")
            return
        raise SystemExit(1)

    attempt: int
    for attempt in range(1, 21):
        if _has_spend_edge(base_url):
            print(f"lineage ready namespace={namespace_name}")
            return
        print(f"waiting for lineage attempt={attempt}")
        time.sleep(2)
    raise SystemExit("Marquez has no customers/orders -> customer_spend edge")


def _namespaces(base_url: str) -> list[dict[str, object]]:
    payload: dict[str, object] = _get_json(f"{base_url}/api/v1/namespaces")
    raw_namespaces: object = payload.get("namespaces", [])
    if not isinstance(raw_namespaces, list):
        raise SystemExit("Marquez namespaces payload is not a list")
    namespaces: list[dict[str, object]] = []
    item: object
    for item in raw_namespaces:
        if isinstance(item, dict):
            namespaces.append(item)
    return namespaces


def _has_spend_edge(base_url: str) -> bool:
    namespace: dict[str, object]
    for namespace in _namespaces(base_url):
        raw_name: object = namespace.get("name")
        if not isinstance(raw_name, str) or raw_name == "":
            continue
        quoted_name: str = urllib.parse.quote(raw_name, safe="")
        payload: dict[str, object] = _get_json(f"{base_url}/api/v1/namespaces/{quoted_name}/jobs")
        raw_jobs: object = payload.get("jobs", [])
        if not isinstance(raw_jobs, list):
            continue
        job: object
        for job in raw_jobs:
            if not isinstance(job, dict):
                continue
            inputs: list[str] = _dataset_names(job.get("inputs"))
            outputs: list[str] = _dataset_names(job.get("outputs"))
            if _is_spend_edge(inputs, outputs):
                job_name: object = job.get("name")
                print(f"found job namespace={raw_name} name={job_name}")
                return True
    return False


def _dataset_names(raw_datasets: object) -> list[str]:
    if not isinstance(raw_datasets, list):
        return []
    names: list[str] = []
    dataset: object
    for dataset in raw_datasets:
        if not isinstance(dataset, dict):
            continue
        dataset_name: object = dataset.get("name")
        if isinstance(dataset_name, str):
            names.append(dataset_name)
    return names


def _is_spend_edge(inputs: list[str], outputs: list[str]) -> bool:
    input_names: str = " ".join(inputs)
    output_names: str = " ".join(outputs)
    return (
        "customers" in input_names
        and "orders" in input_names
        and "customer_spend" in output_names
    )


def _get_json(url: str) -> dict[str, object]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload: object = json.load(response)
    except urllib.error.URLError as err:
        raise SystemExit(f"Marquez request failed for {url}") from err
    if not isinstance(payload, dict):
        raise SystemExit(f"Marquez returned a non-object payload for {url}")
    return payload


if __name__ == "__main__":
    main()
