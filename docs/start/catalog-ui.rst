Catalog UI
===========

The UI container serves the built explorer and proxies ``/api`` to the catalog
API. Catalog routes live under ``/api/v1``. That API calls Polaris at
``http://polaris:8181``.

Open http://localhost:5173 after Compose is up. The Spark seed must have run
so ``sales.customers``, ``sales.orders``, and ``sales.customer_spend`` exist.
See :doc:`compose`.

For a host dev server instead of the UI container:

.. code-block:: bash

   cd web
   npm install
   npm run dev

That Vite server proxies ``/api`` to ``http://127.0.0.1:8090``, so the API
process has to be listening there. The activity bar and the views it opens
are described in :doc:`/ui/index`.
