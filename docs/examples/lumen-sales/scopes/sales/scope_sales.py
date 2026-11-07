"""Sales scope for the Polaris sales namespace."""

from intelegent_data_catalog.assets.dsl import Scope

Scope(
    "sales",
    space="sales",
    name="Sales",
    description="Polaris namespace sales.",
    context="Raw customers and orders, plus the customer_spend mart.",
)
