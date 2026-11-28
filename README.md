# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/alexfayers/mcp-memory/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                 |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------------------- | -------: | -------: | ------: | --------: |
| conftest.py                                          |       11 |        0 |    100% |           |
| src/mcp\_memory/\_\_init\_\_.py                      |        0 |        0 |    100% |           |
| src/mcp\_memory/activity.py                          |      113 |        3 |     97% |152-153, 203 |
| src/mcp\_memory/api.py                               |      109 |        8 |     93% |66, 75, 102-103, 158, 188, 192-193 |
| src/mcp\_memory/api\_client.py                       |       22 |        3 |     86% | 27, 31-32 |
| src/mcp\_memory/atomic\_write.py                     |       16 |        3 |     81% |     20-22 |
| src/mcp\_memory/audit.py                             |      192 |        1 |     99% |       437 |
| src/mcp\_memory/backup.py                            |       99 |        4 |     96% |   130-133 |
| src/mcp\_memory/cli.py                               |      752 |      107 |     86% |99, 194, 223-228, 246-259, 264-273, 306, 318, 333, 395, 397, 403-414, 575, 597-598, 615-617, 635-636, 643-644, 648-650, 725-727, 748, 764-765, 787-788, 791-794, 800-803, 809-812, 870-871, 905-906, 941-942, 969-978, 986-988, 1036-1038, 1229, 1231, 1233, 1239-1241, 1265, 1267, 1271, 1273, 1276-1281 |
| src/mcp\_memory/config.py                            |      153 |        5 |     97% |142-143, 178-179, 290 |
| src/mcp\_memory/dedupe.py                            |       35 |        1 |     97% |        45 |
| src/mcp\_memory/dream.py                             |      571 |       13 |     98% |272, 360-362, 372-374, 684, 689, 734, 801, 941-942 |
| src/mcp\_memory/dream\_status.py                     |      150 |        2 |     99% |  298, 332 |
| src/mcp\_memory/eval.py                              |      135 |        0 |    100% |           |
| src/mcp\_memory/export\_import.py                    |       74 |        4 |     95% |52-53, 88, 90 |
| src/mcp\_memory/hooks/\_\_init\_\_.py                |        0 |        0 |    100% |           |
| src/mcp\_memory/hooks/plugin.py                      |      565 |       27 |     95% |183-184, 308, 310, 338, 489, 728-729, 734-742, 764, 808, 864, 867-869, 886, 893-897, 959 |
| src/mcp\_memory/hooks/recall.py                      |      175 |        5 |     97% |93, 119, 139, 144, 205 |
| src/mcp\_memory/hooks/review\_tracker.py             |       28 |        0 |    100% |           |
| src/mcp\_memory/hooks/tracker.py                     |      143 |        0 |    100% |           |
| src/mcp\_memory/jsonc.py                             |       64 |       13 |     80% |13, 15, 37-42, 58-60, 77-78 |
| src/mcp\_memory/metrics.py                           |       86 |        0 |    100% |           |
| src/mcp\_memory/migrations/\_\_init\_\_.py           |        0 |        0 |    100% |           |
| src/mcp\_memory/migrations/runner.py                 |       32 |        4 |     88% |     24-27 |
| src/mcp\_memory/migrations/schema.py                 |       23 |        0 |    100% |           |
| src/mcp\_memory/models.py                            |       81 |        1 |     99% |        68 |
| src/mcp\_memory/nudge\_count.py                      |       43 |        0 |    100% |           |
| src/mcp\_memory/path\_migration.py                   |       77 |       18 |     77% |27, 53-63, 67, 107, 109, 113-114, 150 |
| src/mcp\_memory/path\_resolver.py                    |       22 |        1 |     95% |        15 |
| src/mcp\_memory/payload.py                           |       12 |        0 |    100% |           |
| src/mcp\_memory/plan\_apply.py                       |       43 |        1 |     98% |        44 |
| src/mcp\_memory/provenance\_backfill.py              |       26 |        0 |    100% |           |
| src/mcp\_memory/relocate.py                          |       50 |        2 |     96% |     33-34 |
| src/mcp\_memory/server.py                            |      683 |       35 |     95% |84-85, 547, 557, 593, 606, 697-699, 703, 797-798, 828-829, 837, 845, 861-862, 930-931, 945-946, 1145-1146, 1291, 1334-1335, 1686-1687, 1719-1723, 1727 |
| src/mcp\_memory/storage/\_\_init\_\_.py              |        5 |        0 |    100% |           |
| src/mcp\_memory/storage/bootstrap.py                 |       37 |        0 |    100% |           |
| src/mcp\_memory/storage/connection.py                |       69 |        0 |    100% |           |
| src/mcp\_memory/storage/operations/\_\_init\_\_.py   |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/operations/maintenance.py    |       53 |        3 |     94% |166-167, 174 |
| src/mcp\_memory/storage/operations/reads.py          |      160 |        3 |     98% |301, 318-319 |
| src/mcp\_memory/storage/operations/transfer.py       |      126 |        2 |     98% |  224, 253 |
| src/mcp\_memory/storage/pure/\_\_init\_\_.py         |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/pure/ranking.py              |       27 |        0 |    100% |           |
| src/mcp\_memory/storage/pure/rows.py                 |       66 |        4 |     94% |59, 67, 72, 82 |
| src/mcp\_memory/storage/pure/sql.py                  |       22 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/\_\_init\_\_.py |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/entities.py     |      185 |        6 |     97% |50, 55, 119-121, 123 |
| src/mcp\_memory/storage/repositories/observations.py |      178 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/projects.py     |      125 |        1 |     99% |       157 |
| src/mcp\_memory/storage/repositories/relations.py    |       88 |        4 |     95% |100, 103, 129, 141 |
| src/mcp\_memory/storage/repositories/telemetry.py    |       49 |        1 |     98% |        75 |
| src/mcp\_memory/storage/services/\_\_init\_\_.py     |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/services/fts.py              |       11 |        0 |    100% |           |
| src/mcp\_memory/storage/services/ids.py              |       29 |        0 |    100% |           |
| src/mcp\_memory/storage/services/integrity.py        |       24 |        0 |    100% |           |
| src/mcp\_memory/storage/services/provenance.py       |        9 |        0 |    100% |           |
| src/mcp\_memory/storage/services/triggers.py         |        9 |        0 |    100% |           |
| src/mcp\_memory/telemetry.py                         |      191 |        6 |     97% |64-65, 145-146, 163-164 |
| src/mcp\_memory/tool\_names.py                       |        4 |        0 |    100% |           |
| src/mcp\_memory/tool\_paths.py                       |       14 |        3 |     79% | 23-24, 34 |
| src/mcp\_memory/usefulness.py                        |       73 |        2 |     97% |     51-52 |
| src/mcp\_memory/visualise.py                         |      190 |        5 |     97% |169, 241-242, 315, 319 |
| tests/\_\_init\_\_.py                                |       43 |        1 |     98% |        85 |
| tests/conftest.py                                    |       53 |        0 |    100% |           |
| tests/eval/\_\_init\_\_.py                           |        0 |        0 |    100% |           |
| tests/eval/badge\_endpoints.py                       |       27 |        1 |     96% |        48 |
| tests/eval/compare\_baseline.py                      |      206 |        3 |     99% |123, 135, 349 |
| tests/eval/eval\_baseline.py                         |       21 |        0 |    100% |           |
| tests/eval/eval\_fixture.py                          |      255 |        3 |     99% |590-591, 609 |
| tests/eval/eval\_harness.py                          |       72 |        0 |    100% |           |
| tests/eval/latency\_baseline.py                      |       24 |        0 |    100% |           |
| tests/eval/link\_scenarios.py                        |       79 |        0 |    100% |           |
| tests/eval/ranking\_replay.py                        |       24 |        0 |    100% |           |
| tests/eval/regen\_baseline.py                        |       28 |        1 |     96% |        71 |
| tests/eval/size\_baseline.py                         |       45 |        0 |    100% |           |
| tests/eval/test\_badge\_endpoints.py                 |       23 |        0 |    100% |           |
| tests/eval/test\_benchmark\_scenarios.py             |       58 |        0 |    100% |           |
| tests/eval/test\_compare\_baseline.py                |      258 |        0 |    100% |           |
| tests/eval/test\_eval\_baseline.py                   |       26 |        0 |    100% |           |
| tests/eval/test\_eval\_fixture.py                    |      172 |        1 |     99% |       150 |
| tests/eval/test\_eval\_harness.py                    |       96 |        0 |    100% |           |
| tests/eval/test\_latency\_baseline.py                |       18 |        0 |    100% |           |
| tests/eval/test\_link\_scenarios.py                  |       14 |        0 |    100% |           |
| tests/eval/test\_ranking\_eval.py                    |      376 |        0 |    100% |           |
| tests/eval/test\_ranking\_replay.py                  |       50 |        0 |    100% |           |
| tests/eval/test\_regen\_baseline.py                  |       56 |        0 |    100% |           |
| tests/eval/test\_size\_baseline.py                   |       42 |        0 |    100% |           |
| tests/naming\_check.py                               |      105 |      105 |      0% |    12-162 |
| tests/test\_activity.py                              |      153 |        0 |    100% |           |
| tests/test\_api\_client.py                           |       69 |        0 |    100% |           |
| tests/test\_audit.py                                 |      353 |        0 |    100% |           |
| tests/test\_backup.py                                |      140 |        0 |    100% |           |
| tests/test\_cli.py                                   |      809 |        1 |     99% |       363 |
| tests/test\_config.py                                |      176 |        0 |    100% |           |
| tests/test\_database.py                              |     2149 |        1 |     99% |       627 |
| tests/test\_db\_access.py                            |       13 |        0 |    100% |           |
| tests/test\_dream.py                                 |      895 |        5 |     99% |506-507, 604, 1067, 1126 |
| tests/test\_dream\_status.py                         |      254 |        0 |    100% |           |
| tests/test\_export\_import.py                        |      335 |        0 |    100% |           |
| tests/test\_hooks\_plugin.py                         |     1248 |       66 |     95% |1787-1792, 1797-1800, 1805-1813, 1824-1827, 1856-1859, 1901-1904, 1908-1911, 1915-1919, 1923-1925, 1930-1932, 1937-1942, 1946-1947, 1951-1953, 1958-1966 |
| tests/test\_metrics.py                               |      183 |        0 |    100% |           |
| tests/test\_models.py                                |       13 |        0 |    100% |           |
| tests/test\_nudge\_count.py                          |       12 |        0 |    100% |           |
| tests/test\_path\_resolver.py                        |       41 |        2 |     95% |     56-57 |
| tests/test\_payload.py                               |       68 |        0 |    100% |           |
| tests/test\_plan\_apply.py                           |       27 |        0 |    100% |           |
| tests/test\_provenance\_backfill.py                  |       39 |        0 |    100% |           |
| tests/test\_recall.py                                |       52 |        0 |    100% |           |
| tests/test\_relocate.py                              |      174 |        0 |    100% |           |
| tests/test\_review\_tracker.py                       |       42 |        0 |    100% |           |
| tests/test\_server.py                                |     1152 |        1 |     99% |      1919 |
| tests/test\_server\_http.py                          |      121 |        0 |    100% |           |
| tests/test\_telemetry.py                             |      451 |        1 |     99% |       757 |
| tests/test\_telemetry\_contract.py                   |       11 |        0 |    100% |           |
| tests/test\_tool\_annotations.py                     |       17 |        0 |    100% |           |
| tests/test\_tool\_schemas.py                         |       66 |        0 |    100% |           |
| tests/test\_tracker.py                               |       88 |        0 |    100% |           |
| tests/test\_usefulness.py                            |       84 |        0 |    100% |           |
| tests/test\_visualise.py                             |      708 |        0 |    100% |           |
| **TOTAL**                                            | **18443** |  **493** | **97%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/alexfayers/mcp-memory/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/alexfayers/mcp-memory/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/alexfayers/mcp-memory/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/alexfayers/mcp-memory/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Falexfayers%2Fmcp-memory%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/alexfayers/mcp-memory/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.