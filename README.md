# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/alexfayers/mcp-memory/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                 |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------------------- | -------: | -------: | ------: | --------: |
| conftest.py                                          |       11 |        0 |    100% |           |
| src/mcp\_memory/\_\_init\_\_.py                      |        0 |        0 |    100% |           |
| src/mcp\_memory/activity.py                          |      113 |        3 |     97% |147-148, 198 |
| src/mcp\_memory/api.py                               |       98 |        8 |     92% |64, 67, 95-96, 139, 167, 171-172 |
| src/mcp\_memory/api\_client.py                       |       22 |        3 |     86% | 27, 31-32 |
| src/mcp\_memory/atomic\_write.py                     |       16 |        3 |     81% |     20-22 |
| src/mcp\_memory/audit.py                             |      166 |        1 |     99% |       403 |
| src/mcp\_memory/backup.py                            |       99 |        4 |     96% |   130-133 |
| src/mcp\_memory/cli.py                               |      747 |      112 |     85% |98, 193, 222-227, 245-258, 263-272, 305, 317, 332, 389, 391, 397-408, 569, 591-592, 609-611, 629-630, 637-638, 642-644, 719-721, 742, 758-759, 781-782, 785-788, 794-797, 803-806, 864-865, 899-900, 935-936, 963-972, 980-982, 1030-1032, 1223, 1225, 1227, 1233-1235, 1257, 1259, 1261, 1264-1275 |
| src/mcp\_memory/config.py                            |      151 |        5 |     97% |142-143, 178-179, 290 |
| src/mcp\_memory/dedupe.py                            |       35 |        1 |     97% |        45 |
| src/mcp\_memory/dream.py                             |      571 |       13 |     98% |271, 359-361, 371-373, 683, 688, 733, 800, 940-941 |
| src/mcp\_memory/dream\_status.py                     |      150 |        2 |     99% |  298, 332 |
| src/mcp\_memory/eval.py                              |      135 |        0 |    100% |           |
| src/mcp\_memory/export\_import.py                    |       73 |        4 |     95% |52-53, 88, 90 |
| src/mcp\_memory/hooks/\_\_init\_\_.py                |        0 |        0 |    100% |           |
| src/mcp\_memory/hooks/plugin.py                      |      534 |       27 |     95% |175-176, 300, 302, 330, 478, 707-708, 713-721, 743, 787, 840, 843-845, 856, 863-867, 919 |
| src/mcp\_memory/hooks/review\_tracker.py             |       28 |        0 |    100% |           |
| src/mcp\_memory/hooks/tracker.py                     |      126 |        1 |     99% |       130 |
| src/mcp\_memory/jsonc.py                             |       64 |       13 |     80% |13, 15, 37-42, 58-60, 77-78 |
| src/mcp\_memory/metrics.py                           |       86 |        0 |    100% |           |
| src/mcp\_memory/migrations/\_\_init\_\_.py           |        0 |        0 |    100% |           |
| src/mcp\_memory/migrations/runner.py                 |       32 |        4 |     88% |     24-27 |
| src/mcp\_memory/migrations/schema.py                 |       23 |        0 |    100% |           |
| src/mcp\_memory/models.py                            |       53 |        0 |    100% |           |
| src/mcp\_memory/nudge\_count.py                      |       43 |        0 |    100% |           |
| src/mcp\_memory/path\_migration.py                   |       77 |       18 |     77% |27, 53-63, 67, 107, 109, 113-114, 150 |
| src/mcp\_memory/path\_resolver.py                    |       22 |        1 |     95% |        15 |
| src/mcp\_memory/payload.py                           |       12 |        0 |    100% |           |
| src/mcp\_memory/plan\_apply.py                       |       39 |        1 |     97% |        40 |
| src/mcp\_memory/provenance\_backfill.py              |       26 |        0 |    100% |           |
| src/mcp\_memory/relocate.py                          |       50 |        2 |     96% |     33-34 |
| src/mcp\_memory/server.py                            |      668 |       35 |     95% |83-84, 521, 531, 567, 580, 656-658, 662, 752-753, 783-784, 792, 800, 812-813, 875-876, 890-891, 1090-1091, 1236, 1279-1280, 1618-1619, 1651-1655, 1659 |
| src/mcp\_memory/storage/\_\_init\_\_.py              |        5 |        0 |    100% |           |
| src/mcp\_memory/storage/bootstrap.py                 |       37 |        0 |    100% |           |
| src/mcp\_memory/storage/connection.py                |       69 |        0 |    100% |           |
| src/mcp\_memory/storage/operations/\_\_init\_\_.py   |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/operations/maintenance.py    |       53 |        3 |     94% |152-153, 160 |
| src/mcp\_memory/storage/operations/reads.py          |      144 |        0 |    100% |           |
| src/mcp\_memory/storage/operations/transfer.py       |      122 |        2 |     98% |  220, 247 |
| src/mcp\_memory/storage/pure/\_\_init\_\_.py         |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/pure/ranking.py              |       18 |        0 |    100% |           |
| src/mcp\_memory/storage/pure/rows.py                 |       40 |        0 |    100% |           |
| src/mcp\_memory/storage/pure/sql.py                  |       19 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/\_\_init\_\_.py |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/entities.py     |      177 |        6 |     97% |47, 52, 110-112, 114 |
| src/mcp\_memory/storage/repositories/observations.py |      164 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/projects.py     |      101 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/relations.py    |       88 |        4 |     95% |100, 103, 129, 141 |
| src/mcp\_memory/storage/repositories/telemetry.py    |       49 |        1 |     98% |        74 |
| src/mcp\_memory/storage/services/\_\_init\_\_.py     |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/services/fts.py              |       11 |        0 |    100% |           |
| src/mcp\_memory/storage/services/ids.py              |       29 |        0 |    100% |           |
| src/mcp\_memory/storage/services/integrity.py        |       24 |        0 |    100% |           |
| src/mcp\_memory/storage/services/provenance.py       |        9 |        0 |    100% |           |
| src/mcp\_memory/telemetry.py                         |      191 |        6 |     97% |64-65, 145-146, 163-164 |
| src/mcp\_memory/tool\_names.py                       |        4 |        0 |    100% |           |
| src/mcp\_memory/tool\_paths.py                       |       14 |        3 |     79% | 23-24, 34 |
| src/mcp\_memory/usefulness.py                        |       73 |        2 |     97% |     51-52 |
| src/mcp\_memory/visualise.py                         |      184 |        5 |     97% |166, 235-236, 309, 313 |
| tests/\_\_init\_\_.py                                |       43 |        1 |     98% |        85 |
| tests/conftest.py                                    |       48 |        0 |    100% |           |
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
| tests/test\_activity.py                              |      149 |        0 |    100% |           |
| tests/test\_api\_client.py                           |       53 |        0 |    100% |           |
| tests/test\_audit.py                                 |      306 |        0 |    100% |           |
| tests/test\_backup.py                                |      140 |        0 |    100% |           |
| tests/test\_cli.py                                   |      784 |        1 |     99% |       328 |
| tests/test\_config.py                                |      176 |        0 |    100% |           |
| tests/test\_database.py                              |     2049 |        2 |     99% | 617, 2038 |
| tests/test\_db\_access.py                            |       13 |        0 |    100% |           |
| tests/test\_dream.py                                 |      895 |        5 |     99% |506-507, 604, 1067, 1126 |
| tests/test\_dream\_status.py                         |      254 |        0 |    100% |           |
| tests/test\_export\_import.py                        |      319 |        0 |    100% |           |
| tests/test\_hooks\_plugin.py                         |     1110 |       66 |     94% |1709-1714, 1719-1722, 1727-1735, 1746-1749, 1778-1781, 1823-1826, 1830-1833, 1837-1841, 1845-1847, 1852-1854, 1859-1864, 1868-1869, 1873-1875, 1880-1888 |
| tests/test\_metrics.py                               |      183 |        0 |    100% |           |
| tests/test\_models.py                                |       13 |        0 |    100% |           |
| tests/test\_nudge\_count.py                          |       12 |        0 |    100% |           |
| tests/test\_path\_resolver.py                        |       41 |        2 |     95% |     56-57 |
| tests/test\_payload.py                               |       68 |        0 |    100% |           |
| tests/test\_plan\_apply.py                           |       27 |        0 |    100% |           |
| tests/test\_provenance\_backfill.py                  |       39 |        0 |    100% |           |
| tests/test\_relocate.py                              |      174 |        0 |    100% |           |
| tests/test\_review\_tracker.py                       |       42 |        0 |    100% |           |
| tests/test\_server.py                                |     1091 |        1 |     99% |      1824 |
| tests/test\_server\_http.py                          |      121 |        0 |    100% |           |
| tests/test\_telemetry.py                             |      453 |        1 |     99% |       758 |
| tests/test\_telemetry\_contract.py                   |       11 |        0 |    100% |           |
| tests/test\_tool\_annotations.py                     |       17 |        0 |    100% |           |
| tests/test\_tool\_schemas.py                         |       66 |        0 |    100% |           |
| tests/test\_tracker.py                               |       68 |        0 |    100% |           |
| tests/test\_usefulness.py                            |       84 |        0 |    100% |           |
| tests/test\_visualise.py                             |      661 |        0 |    100% |           |
| **TOTAL**                                            | **17480** |  **486** | **97%** |           |


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