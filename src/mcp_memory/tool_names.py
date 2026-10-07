"""Classification of the registered memory tools into mutating and read-only."""

MUTATING_TOOLS: tuple[str, ...] = (
    "create_entities",
    "create_relations",
    "delete_entity",
    "delete_relation",
    "delete_project",
    "add_observations",
    "delete_observations",
    "edit_observation",
    "set_entity_status",
    "set_metadata",
    "move_project_entities",
    "merge_entities",
    "merge_observations",
    "restore_entity",
    "trim_observations_to_outcome",
    "rename_entity",
    "move_entity_cross_scope",
    "vote",
)
READ_ONLY_TOOLS: tuple[str, ...] = (
    "search_nodes",
    "read_graph",
    "list_metadata",
    "get_project_for_path",
    "get_group_members",
    "search_all_projects",
    "get_entity_with_relations",
)
