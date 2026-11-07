"""Sales space for the commerce warehouse."""

from intelegent_data_catalog.assets.dsl import Space

Space(
    "sales",
    name="Sales",
    description="Orders, customers, and spend for the commerce warehouse.",
    context=(
        "Commerce analytics owns this space. "
        "Tables live in the Polaris sales namespace."
    ),
)
