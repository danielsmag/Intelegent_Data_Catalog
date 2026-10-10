"""Pytest defaults. Eager Celery runs builds in-process so Redis is not required."""

import os

os.environ["CATALOG_CELERY__TASK_ALWAYS_EAGER"] = "true"
