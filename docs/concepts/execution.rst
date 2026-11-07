Execution
=========

Applying a changed agent declaration queues one operator run. What that run
writes is in :doc:`agents`. The executor comes from
``[agent_definitions] executor``:

* ``celery`` sends the workload to the catalog worker. This is the default.
* ``local`` runs it in the scanner process. Use this for dev and tests.

``execute`` builds the knowledge context, then writes the description.
Each run keeps those two tasks, their state, and their logs. The latest
status stays on the definition item, which is what the schedule reads.
The agent page shows the run history.

Schedule
--------

``schedule`` is a five-field cron, in the same shape as ``0 2 * * *``.
After a run finishes, the next fire time is stored on the item. A later tick
queues another run when that time has passed and the previous run has
finished. A queued or running item is left alone.

Omit ``schedule`` and the operator runs when the declaration is applied.
Nothing is queued again until the file changes.

A definition file is trusted code. Only the worker that scans the prefix
executes it.
