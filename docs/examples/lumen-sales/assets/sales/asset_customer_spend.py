"""Customer spend mart. Analyst context stays on later operator runs."""

from intelegent_data_catalog.assets.dsl import Asset, IcebergTableOperator

asset = Asset(
    key="customer_spend",
    description="Spend and order count per customer, built from customers and orders.",
    context=(
        "Daily mart of customer spend. Grain is one row per customer. "
        "total_amount sums order amount. order_count counts orders. "
        "Cancelled orders stay in the sum because the seed job does not filter status."
    ),
    operator=IcebergTableOperator(
        source_key="polaris.sales.customer_spend",
        data_source="Polaris",
        include_lineage=True,
    ),
    properties={"owner": "commerce-analytics", "tier": "gold"},
    tags=["gold", "customer"],
    schedule="0 2 * * *",
)
