# TODO

Outstanding cleanups noted during the observation-hashing design. These are
parallel-array / duplicated-source-of-truth code smells: the same information
is kept in two places and hand-synchronised, so the two can silently drift.
Each item names the current location so it can be picked up cold.

- [ ] Observation-merge UI in the visualiser. `get_all_graph_data` and
      `search_graph` (src/mcp_memory/visualise.py) now ship each observation as a
      `{content, content_hash, vote_score}` object, and the browser-side JS in
      src/mcp_memory/templates/visualise.html reads those fields directly (the
      old parallel `observation_votes` array is gone). Still deferred: a
      browser-side UI to fold one observation into another (source/target pick +
      two-step confirm) driving `merge_observations`. No existing two-step-confirm
      flow exists in visualise.html to model it on yet.
- [ ] models.py duplicates the status list. `EntityStatus` (a `Literal`) and
      `VALID_STATUSES` (a tuple) in src/mcp_memory/models.py both spell out the
      same five status strings, kept in sync by hand. Derive one from the other.
- [ ] The garbage-collection downvote floor is duplicated as a literal.
      `_GC_DOWNVOTE_FLOOR = -10` in src/mcp_memory/database.py is repeated as the
      bare literal `-10` ("at or below -10" / "a score of -10") in two dream
      prompt strings in src/mcp_memory/agent.py. Reference the constant instead.
- [ ] Two exempt-type sets duplicate the same values. `_GC_EXEMPT_ENTITY_TYPES`
      in src/mcp_memory/database.py holds the same value set as
      `RELATION_EXEMPT_TYPES` in src/mcp_memory/server.py (currently duplicated
      to dodge an import cycle) rather than sharing one definition.
