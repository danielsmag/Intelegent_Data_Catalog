"""Customer spend mart. Context is edited on the agent, not by an operator."""

from intelegent_data_catalog.agents.dsl import Agent, IcebergTable

agent = Agent(
    key="customer_spend",
    description="Spend and order count per customer, built from customers and orders.",
    context=(
        "Daily mart of customer spend. Grain is one row per customer. "
        "total_amount sums order amount. order_count counts orders. "
        "Cancelled orders stay in the sum because the seed job does not filter status."
    ),
    source=IcebergTable(
        source_key="polaris.sales.customer_spend",
        data_source="Polaris",
    ),
    properties={"owner": "commerce-analytics", "tier": "gold"},
    tags=["gold", "customer"],
)
