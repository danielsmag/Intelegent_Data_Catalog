"""Process-wide container used by Celery tasks.

The API process installs the test or request container. A worker process installs
its own container so a forked engine is not reused.
"""

from intelegent_data_catalog.api.container import AppContainer

_container: AppContainer | None = None


def set_job_container(container: AppContainer) -> None:
    """Install the container tasks use to open sessions and services."""
    global _container
    _container = container


def job_container() -> AppContainer:
    """Return the container installed for this process."""
    if _container is None:
        raise RuntimeError("job container is not configured")
    return _container
