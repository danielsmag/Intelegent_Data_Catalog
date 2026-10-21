"""Example space declaration. Upload under s3://<bucket>/<prefix>/spaces/."""

from intelegent_data_catalog.assets.dsl import Space

Space("analytics", name="Analytics", description="Analytics domain", context="Sales analytics.")
