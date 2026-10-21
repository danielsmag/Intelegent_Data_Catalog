"""Example scope declaration. Upload under s3://<bucket>/<prefix>/scopes/."""

from intelegent_data_catalog.assets.dsl import Scope

Scope("finance", space="analytics", description="Finance data")
