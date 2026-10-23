Declare assets
==============

Put one Python file in each definition folder. The scanner executes the file
and applies what it constructs. Upload the object under the configured
prefix (default ``asset-definitions/``) in the definition bucket.

Space
-----

.. literalinclude:: ../../examples/asset-definitions/spaces/space_analytics.py
   :language: python
   :caption: examples/asset-definitions/spaces/space_analytics.py

Object key: ``asset-definitions/spaces/space_analytics.py``.

Scope
-----

.. literalinclude:: ../../examples/asset-definitions/scopes/analytics/scope_finance.py
   :language: python
   :caption: examples/asset-definitions/scopes/analytics/scope_finance.py

Object key: ``asset-definitions/scopes/analytics/scope_finance.py``. The
parent space slug is ``analytics``. Create the space file first. A scope
whose space is still missing stays unresolved and is retried on a later scan.

Asset
-----

.. literalinclude:: ../../examples/asset-definitions/assets/sales/asset_orders.py
   :language: python
   :caption: examples/asset-definitions/assets/sales/asset_orders.py

Object key: ``asset-definitions/assets/sales/asset_orders.py``. ``orders`` is
the stable key. The cron runs the operator at 02:00. Omit ``schedule`` to run
once when the file is applied.

Scan
----

With ``[asset_definitions] enabled`` true, Beat polls
``poll_interval_seconds``. To scan once, call
``POST /api/v1/asset-definitions/scan``. The Assets view lists the same
objects. See :doc:`/concepts/assets`, :doc:`/concepts/operators`,
:doc:`/concepts/definitions`, and :doc:`/concepts/execution`.
