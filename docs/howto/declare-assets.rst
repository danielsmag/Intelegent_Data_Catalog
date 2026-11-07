Declare assets
==============

Put one Python file under ``assets/``. The scanner executes the file
and applies what it constructs. Upload the object under the configured
prefix (default ``asset-definitions/``) in the definition bucket.

The commerce team's declarations are the ``lumen-sales`` project next to this
repository. Those files belong under the prefix in the definition bucket. The
catalog reads that prefix from object storage. The snippets below are copies
shipped with this manual, so the HTML build does not need that checkout.

Asset
-----

.. literalinclude:: ../examples/lumen-sales/assets/sales/asset_customer_spend.py
   :language: python
   :caption: lumen-sales/assets/sales/asset_customer_spend.py

Object key: ``asset-definitions/assets/sales/asset_customer_spend.py``.
``customer_spend`` is the stable key. The cron runs the operator at 02:00.
``context`` is analyst text, so a later run keeps it. ``tags`` are labels
on the asset. Omit ``tags`` and the stored labels stay.

``lumen-sales/assets/sales/asset_customers.py`` names
``polaris.sales.customers`` and runs at 01:00.
``lumen-sales/assets/sales/asset_orders.py`` names
``polaris.sales.orders`` and runs at 01:30.

Omit ``schedule`` to run once when the file is applied.

``SchemaOperator`` in that file fans out to one table asset per catalog
table. Put table-specific agent files in ``asset_<table>/`` next to the
schema file. The ``asset_`` prefix marks the folder. See :doc:`/concepts/operators`.

Scan
----

With ``[asset_definitions] enabled`` true, Beat polls
``poll_interval_seconds``. To scan once, call
``POST /api/v1/asset-definitions/scan``. The Assets view lists the same
objects. See :doc:`/concepts/assets`, :doc:`/concepts/operators`,
:doc:`/concepts/definitions`, and :doc:`/concepts/execution`.
