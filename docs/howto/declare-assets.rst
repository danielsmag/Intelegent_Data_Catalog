Declare assets
==============

Put one Python file in each definition folder. The scanner executes the file
and applies what it constructs. Upload the object under the configured
prefix (default ``asset-definitions/``) in the definition bucket.

The commerce team's declarations are the ``lumen-sales`` project next to this
repository. Those files belong under the prefix in the definition bucket. The
catalog reads that prefix from object storage. The snippets below are those
files.

Space
-----

.. literalinclude:: ../../../lumen-sales/spaces/space_sales.py
   :language: python
   :caption: lumen-sales/spaces/space_sales.py

Object key: ``asset-definitions/spaces/space_sales.py``.

Scope
-----

.. literalinclude:: ../../../lumen-sales/scopes/sales/scope_sales.py
   :language: python
   :caption: lumen-sales/scopes/sales/scope_sales.py

Object key: ``asset-definitions/scopes/sales/scope_sales.py``. The
parent space slug is ``sales``. Create the space file first. A scope
whose space is still missing stays unresolved and is retried on a later scan.

Asset
-----

.. literalinclude:: ../../../lumen-sales/assets/sales/asset_customer_spend.py
   :language: python
   :caption: lumen-sales/assets/sales/asset_customer_spend.py

Object key: ``asset-definitions/assets/sales/asset_customer_spend.py``.
``customer_spend`` is the stable key. The cron runs the operator at 02:00.
``context`` is analyst text, so a later run keeps it.

``lumen-sales/assets/sales/asset_customers.py`` names
``polaris.sales.customers`` and runs at 01:00.
``lumen-sales/assets/sales/asset_orders.py`` names
``polaris.sales.orders`` and runs at 01:30.

Omit ``schedule`` to run once when the file is applied.

Scan
----

With ``[asset_definitions] enabled`` true, Beat polls
``poll_interval_seconds``. To scan once, call
``POST /api/v1/asset-definitions/scan``. The Assets view lists the same
objects. See :doc:`/concepts/assets`, :doc:`/concepts/operators`,
:doc:`/concepts/definitions`, and :doc:`/concepts/execution`.
