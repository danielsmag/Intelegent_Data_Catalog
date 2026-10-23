Sign in
=======

Compose starts Keycloak and turns catalog auth on. The realm is ``catalog``.
The UI client is ``catalog-ui`` (public, PKCE).

Open the catalog UI and sign in with one of the local users:

* ``admin`` / ``admin``, realm role ``catalog-admin``
* ``analyst`` / ``analyst``

Keycloak itself is at http://localhost:8180. The master admin uses
``KEYCLOAK_ADMIN`` and ``KEYCLOAK_ADMIN_PASSWORD`` from ``.env``. The defaults
are ``admin`` / ``admin``.

The UI sends the active workspace as ``X-Workspace-Id``, a workspace UUID.
Omit the header and a platform admin can see every space. Any other caller
sees ``OPEN`` spaces. An unknown workspace id is rejected. A member can
select a workspace they belong to, plus the built-in Shared workspace.

A host ``uv run`` leaves ``[auth] enabled`` false, so the API uses the dev
principal from ``[auth]`` (``dev_subject``, ``dev_email``, ``dev_name``,
``dev_is_admin``) and does not check tokens. To verify tokens outside
Compose, set:

.. code-block:: bash

   CATALOG_AUTH__ENABLED=true
   CATALOG_AUTH__ISSUER=http://localhost:8180/realms/catalog
   CATALOG_AUTH__JWKS_URL=http://localhost:8180/realms/catalog/protocol/openid-connect/certs

``issuer`` and ``jwks_url`` are required when auth is enabled. The audience
default is ``catalog-ui``.
