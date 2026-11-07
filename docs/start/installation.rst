Installation
============

The package requires Python 3.13 or newer. `uv <https://docs.astral.sh/uv/>`_ installs
it and the dev tools:

.. code-block:: bash

   uv sync --group dev

Serve the catalog API:

.. code-block:: bash

   uv run intelegent-data-catalog-api

The process binds ``[api] host`` and ``[api] port``. The file defaults are
``127.0.0.1`` and ``8090``. ``GET /api/v1/health`` reports that the process is
up.

A host process leaves ``[auth] enabled`` false, so the API uses the dev
principal and does not check bearer tokens. Compose turns auth on. See
:doc:`configuration` and :doc:`/howto/sign-in`.

Build this manual with the ``docs`` group:

.. code-block:: bash

   uv sync --group docs
   uv run sphinx-build -W -b html docs docs/_build/html

Open ``docs/_build/html/index.html``. Compose serves the same build:

.. code-block:: bash

   docker compose up -d --build catalog-docs

Open http://localhost:8000. ``CATALOG_DOCS_PORT`` changes the host port.
The service does not start the rest of the stack. Declare-assets snippets
live under ``docs/examples/lumen-sales``.

Checks:

.. code-block:: bash

   uv run pytest
   uv run ruff check .
   uv run mypy
