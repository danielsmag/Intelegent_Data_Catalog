Declare agents
==============

Put one Python file under ``agents/``. The scanner executes the file
and applies what it constructs. Upload the object under the configured
prefix (default ``agent-definitions/``) in the definition bucket.

The commerce team's declarations are the ``lumen-sales`` project next to this
repository. Those files belong under the prefix in the definition bucket. The
catalog reads that prefix from object storage. The snippets below are copies
shipped with this manual, so the HTML build does not need that checkout.

Agent
-----

.. literalinclude:: ../examples/lumen-sales/agents/sales/agent_customer_spend.py
   :language: python
   :caption: lumen-sales/agents/sales/agent_customer_spend.py

Object key: ``agent-definitions/agents/sales/agent_customer_spend.py``.
``customer_spend`` is the stable key. The cron runs the operator at 02:00.
``context`` is analyst text, so a later run keeps it. ``tags`` are labels
on the agent. Omit ``tags`` and the stored labels stay.

``lumen-sales/agents/sales/agent_customers.py`` names
``polaris.sales.customers`` and runs at 01:00.
``lumen-sales/agents/sales/agent_orders.py`` names
``polaris.sales.orders`` and runs at 01:30.

Omit ``schedule`` to run once when the file is applied.

``Schema`` in that file is one agent for the namespace. Agent files for it
live in the directory named like the declaration file. See :doc:`/concepts/operators`.

Scan
----

With ``[agent_definitions] enabled`` true, Beat polls
``poll_interval_seconds``. To scan once, call
``POST /api/v1/agent-definitions/scan``. The Agents view lists the same
objects. See :doc:`/concepts/agents`, :doc:`/concepts/operators`,
:doc:`/concepts/definitions`, and :doc:`/concepts/execution`.
