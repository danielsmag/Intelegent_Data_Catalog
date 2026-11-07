"""Default context-build flags. Operators and configuration share these values."""

TABLE_FLAGS: dict[str, bool] = {
    "include_graph": True,
    "include_lineage": True,
    "include_data_profile": False,
    "use_rules": True,
    "use_skills": True,
}
DAG_FLAGS: dict[str, bool] = {
    "include_source": True,
    "include_previous": True,
    "use_rules": True,
    "use_skills": True,
}
INSTANCE_FLAGS: dict[str, bool] = {
    "include_source": True,
    "include_previous": True,
    "use_rules": True,
    "use_skills": True,
}
FILE_FLAGS: dict[str, bool] = {
    "include_outputs": False,
    "include_table_links": True,
    "use_rules": True,
    "use_skills": True,
}
