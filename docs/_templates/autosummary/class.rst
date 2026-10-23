{{ name | escape | underline }}

.. currentmodule:: {{ module }}

.. autoclass:: {{ objname }}
   :members:
   :show-inheritance:
   :special-members: __init__
   :exclude-members: model_config,model_fields,model_computed_fields,model_extra,model_fields_set,model_construct,model_copy,model_dump,model_dump_json,model_json_schema,model_parametrized_name,model_post_init,model_rebuild,model_validate,model_validate_json,model_validate_strings,construct,copy,dict,json,parse_obj,parse_raw,schema,schema_json,update_forward_refs,from_orm
