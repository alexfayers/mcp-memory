# TODO

Outstanding cleanups noted during the observation-hashing design. These are
parallel-array / duplicated-source-of-truth code smells: the same information
is kept in two places and hand-synchronised, so the two can silently drift.
Each item names the current location so it can be picked up cold.

- [ ] Entity.observations / observation vote scores parallel arrays. `Entity`
      (src/mcp_memory/models.py) holds observation strings, while their vote
      scores live separately (e.g. `DatabaseManager.observation_scores` in
      src/mcp_memory/database.py) and are matched back up by list index. The
      in-flight observation-hashing change makes a content-hashed observation
      the single source of truth; close this item once that work lands.
- [ ] Visualiser JSON boundary emits parallel arrays. `get_all_graph_data`
      (src/mcp_memory/visualise.py, the `observations` / `observation_votes`
      lists) and `search_graph` (same file) still ship two index-aligned
      arrays, and the browser-side JS in src/mcp_memory/templates/visualise.html
      matches them by index (e.g. `d.observations` / `d.observation_votes`).
      Deliberately deferred: the MCP tool and dream capability shipped without a
      browser-side merge-observations UI. A future change should carry
      `{content, content_hash, vote_score}` objects to the browser and add an
      observation-merge UI.
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
