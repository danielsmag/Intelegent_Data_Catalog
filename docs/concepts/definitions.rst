Definitions
===========

Definition files are Python modules in an object-store prefix. The default
prefix is ``asset-definitions/``. Beat polls that prefix when
``[asset_definitions] enabled`` is true. A manual scan still runs from
``POST /api/v1/asset-definitions/scan``.

Layout
------

The first folder under the prefix selects the kind. The file name must use
that kind's prefix. Nested folders are allowed. Other folders and
non-Python objects are ignored. A Python file with the wrong name prefix is
stored as an error.

.. list-table::
   :header-rows: 1
   :widths: 25 40 35

   * - Folder
     - File name
     - Declares
   * - ``spaces/``
     - ``space_*.py``
     - :class:`~intelegent_data_catalog.assets.dsl.nodes.Space`
   * - ``scopes/``
     - ``scope_*.py``
     - :class:`~intelegent_data_catalog.assets.dsl.nodes.Scope`
   * - ``assets/``
     - ``asset_*.py``
     - :class:`~intelegent_data_catalog.assets.dsl.nodes.Asset`

``assets/sales/asset_orders.py`` is an asset file. ``notes.txt`` under the
same prefix is ignored. ``assets/orders.py`` fails the name check because the
name does not start with ``asset_``.

A spaces file may only construct spaces. A scopes file may only construct
scopes. An assets file may only construct assets. The worker executes the
file, the same way an Airflow worker executes a DAG file. Execution stops at
``exec_timeout_seconds``. A file larger than ``max_file_bytes`` is an error.

Create and patch
----------------

Constructing ``Space``, ``Scope``, or ``Asset`` registers the declaration
with the file that is running. A missing space or scope is created. An
existing one is patched.

Only arguments the file passed are written. Omit ``description`` and the
stored description stays as it is. Pass ``description=None`` and the stored
description is cleared. A new space with no ``name`` uses the slug as its
label.

The Lumen sales declarations are the ``lumen-sales`` project next to this
repository. They belong under the prefix in the definition bucket. The catalog
reads that prefix from object storage. The Assets view lists the objects the
scanner can see. See :doc:`/howto/declare-assets`.
