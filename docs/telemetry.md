# Telemetry

When installed alongside [llm-prompts](https://github.com/alexfayers/llm-prompts), the server sends anonymous usage events to PostHog (EU) under llm-prompts' install id. It follows llm-prompts' opt-out and sends nothing without llm-prompts, or when no llm-prompts command has run in the last day. See the llm-prompts README for the opt-out settings.

- Sent once the server has been idle for 15 minutes, only if tools were called since the last report. A failed send is retried at the next idle report with the same calls:
  - per tool: the number of successful and failed calls, and the median input and output byte sizes
  - the number of searches, of searches whose results were later used (an edit followed within the window), of searches with no result, of memory writes, and of reminders or nudges the hooks showed, in total and by kind (completion, compact, review or reminder)
  - the number of links created, by relation type (a valid relation type such as `implements` or `part-of`, else `other`)
  - the database size in MB, the entity count, the project count, the link count and the number of linked entities, each as a power-of-ten range
  - OS and Python version
  - the version tags, so use can be compared by version: for llm-prompts, cline-hooks and mcp-memory (and overlays installed from GitHub), the newest `origin/main` commit the install contains (`base`), whether it is a local checkout, whether that checkout has uncommitted changes, and how many commits it is ahead of `base`. A local checkout never sends a commit that is missing from `origin/main`.
- Sent each time the server goes idle for 15 minutes after new searches since the last report (a failed send is retried at the next idle report): the search ranking quality, scored in the background on a read-only connection. Only the mean precision, MRR, recall, nDCG and success rate at k, k itself, the number of scored queries as a power-of-ten range, and the fewest words a scored query had. Alongside them: the database size, entity count, project count, link count, linked entity count and version tags described above.
- Sent after each dream pass: the tier, whether it succeeded, how long it took, the error class name on failure, the number of actions by kind, and the version tags.
- Sent on a crash of the server or its maintenance sweep: the exception types, their messages with this machine's paths, user and host names, URLs, email addresses, addresses and long tokens or numbers redacted and the length capped, and the mcp-memory frames (file, function, line).
- Never sent: project names, entity names, tool options, query text, memory contents, paths or hostnames.
