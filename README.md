# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/alexfayers/mcp-memory/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                 |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------------------- | -------: | -------: | ------: | --------: |
| src/mcp\_memory/\_\_init\_\_.py                      |        0 |        0 |    100% |           |
| src/mcp\_memory/activity.py                          |      113 |        3 |     97% |145-146, 195 |
| src/mcp\_memory/api.py                               |       90 |       10 |     89% |62, 65, 93-94, 121-122, 131, 158, 162-163 |
| src/mcp\_memory/api\_client.py                       |       22 |        3 |     86% | 27, 31-32 |
| src/mcp\_memory/atomic\_write.py                     |       16 |        3 |     81% |     20-22 |
| src/mcp\_memory/audit.py                             |      108 |        0 |    100% |           |
| src/mcp\_memory/backup.py                            |       99 |        4 |     96% |   130-133 |
| src/mcp\_memory/cli.py                               |      649 |      121 |     81% |88, 183, 212-217, 235-248, 253-262, 271, 283, 298, 354-358, 363-374, 486-487, 494-495, 500-502, 506-508, 526-527, 534-535, 539-541, 616-618, 639, 655-656, 678-679, 682-685, 691-694, 700-703, 761-762, 796-797, 832-833, 860-869, 877-879, 897-898, 922-924, 1071, 1073, 1075, 1079-1081, 1102, 1104, 1106, 1109-1120 |
| src/mcp\_memory/config.py                            |      135 |        7 |     95% |149-153, 158, 265 |
| src/mcp\_memory/dedupe.py                            |       35 |        1 |     97% |        42 |
| src/mcp\_memory/dream.py                             |      461 |       18 |     96% |247-249, 259-261, 561, 566, 610, 660, 769-770, 871-873, 878, 899-900 |
| src/mcp\_memory/dream\_status.py                     |      138 |        2 |     99% |  261, 295 |
| src/mcp\_memory/eval.py                              |      135 |        0 |    100% |           |
| src/mcp\_memory/export\_import.py                    |       69 |        4 |     94% |52-53, 88, 90 |
| src/mcp\_memory/hooks/\_\_init\_\_.py                |        0 |        0 |    100% |           |
| src/mcp\_memory/hooks/plugin.py                      |      466 |       25 |     95% |222, 224, 252, 400, 629-630, 635-643, 665, 708, 747, 750-752, 762, 769-773, 825 |
| src/mcp\_memory/hooks/review\_tracker.py             |       28 |        0 |    100% |           |
| src/mcp\_memory/hooks/tracker.py                     |      111 |        1 |     99% |       130 |
| src/mcp\_memory/jsonc.py                             |       64 |       13 |     80% |13, 15, 37-42, 58-60, 77-78 |
| src/mcp\_memory/metrics.py                           |       86 |        2 |     98% |     64-65 |
| src/mcp\_memory/migrations/\_\_init\_\_.py           |        0 |        0 |    100% |           |
| src/mcp\_memory/migrations/runner.py                 |       32 |        4 |     88% |     24-27 |
| src/mcp\_memory/migrations/schema.py                 |       23 |        0 |    100% |           |
| src/mcp\_memory/models.py                            |       42 |        0 |    100% |           |
| src/mcp\_memory/path\_resolver.py                    |       22 |        1 |     95% |        15 |
| src/mcp\_memory/payload.py                           |       12 |        0 |    100% |           |
| src/mcp\_memory/relocate.py                          |       50 |        2 |     96% |     33-34 |
| src/mcp\_memory/server.py                            |      555 |       43 |     92% |61-62, 484, 494, 530, 543, 599-601, 605, 679-680, 710-711, 719, 727, 739-740, 802-803, 817-818, 1011-1012, 1032, 1050-1055, 1094-1101, 1140-1141, 1191-1192, 1419, 1423 |
| src/mcp\_memory/storage/\_\_init\_\_.py              |        5 |        0 |    100% |           |
| src/mcp\_memory/storage/bootstrap.py                 |       37 |        0 |    100% |           |
| src/mcp\_memory/storage/connection.py                |       69 |        0 |    100% |           |
| src/mcp\_memory/storage/operations/\_\_init\_\_.py   |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/operations/maintenance.py    |       49 |        3 |     94% |123-124, 131 |
| src/mcp\_memory/storage/operations/reads.py          |      144 |        0 |    100% |           |
| src/mcp\_memory/storage/operations/transfer.py       |      118 |        2 |     98% |  212, 237 |
| src/mcp\_memory/storage/pure/\_\_init\_\_.py         |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/pure/ranking.py              |       18 |        0 |    100% |           |
| src/mcp\_memory/storage/pure/rows.py                 |       40 |        0 |    100% |           |
| src/mcp\_memory/storage/pure/sql.py                  |       18 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/\_\_init\_\_.py |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/entities.py     |      157 |        6 |     96% |35, 40, 96-98, 100 |
| src/mcp\_memory/storage/repositories/observations.py |      128 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/projects.py     |       97 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/relations.py    |       76 |        3 |     96% |97, 100, 123 |
| src/mcp\_memory/storage/repositories/telemetry.py    |       49 |        1 |     98% |        74 |
| src/mcp\_memory/storage/services/\_\_init\_\_.py     |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/services/fts.py              |       11 |        0 |    100% |           |
| src/mcp\_memory/storage/services/ids.py              |       29 |        0 |    100% |           |
| src/mcp\_memory/storage/services/integrity.py        |       24 |        0 |    100% |           |
| src/mcp\_memory/tool\_names.py                       |        3 |        0 |    100% |           |
| src/mcp\_memory/usefulness.py                        |       72 |        2 |     97% |     50-51 |
| src/mcp\_memory/visualise.py                         |      184 |        5 |     97% |165, 234-235, 308, 312 |
| tests/\_\_init\_\_.py                                |       43 |        1 |     98% |        85 |
| tests/conftest.py                                    |       40 |        0 |    100% |           |
| tests/eval/\_\_init\_\_.py                           |        0 |        0 |    100% |           |
| tests/eval/badge\_endpoints.py                       |       27 |        1 |     96% |        48 |
| tests/eval/compare\_baseline.py                      |      178 |        2 |     99% |  115, 305 |
| tests/eval/eval\_baseline.py                         |       21 |        0 |    100% |           |
| tests/eval/eval\_fixture.py                          |      246 |        3 |     99% |575-576, 594 |
| tests/eval/eval\_harness.py                          |       78 |        0 |    100% |           |
| tests/eval/link\_scenarios.py                        |       80 |        0 |    100% |           |
| tests/eval/ranking\_replay.py                        |       24 |        0 |    100% |           |
| tests/eval/regen\_baseline.py                        |       27 |        1 |     96% |        61 |
| tests/eval/size\_baseline.py                         |       45 |        0 |    100% |           |
| tests/eval/test\_badge\_endpoints.py                 |       23 |        0 |    100% |           |
| tests/eval/test\_benchmark\_scenarios.py             |       58 |        0 |    100% |           |
| tests/eval/test\_compare\_baseline.py                |      241 |        0 |    100% |           |
| tests/eval/test\_eval\_baseline.py                   |       26 |        0 |    100% |           |
| tests/eval/test\_eval\_fixture.py                    |      172 |        1 |     99% |       150 |
| tests/eval/test\_eval\_harness.py                    |       95 |        0 |    100% |           |
| tests/eval/test\_link\_scenarios.py                  |       14 |        0 |    100% |           |
| tests/eval/test\_ranking\_eval.py                    |      363 |        0 |    100% |           |
| tests/eval/test\_ranking\_replay.py                  |       50 |        0 |    100% |           |
| tests/eval/test\_regen\_baseline.py                  |       52 |        0 |    100% |           |
| tests/eval/test\_size\_baseline.py                   |       42 |        0 |    100% |           |
| tests/naming\_check.py                               |      105 |      105 |      0% |    12-162 |
| tests/test\_activity.py                              |      149 |        0 |    100% |           |
| tests/test\_api\_client.py                           |       47 |        0 |    100% |           |
| tests/test\_audit.py                                 |      232 |        0 |    100% |           |
| tests/test\_backup.py                                |      140 |        0 |    100% |           |
| tests/test\_cli.py                                   |      657 |        2 |     99% |   70, 263 |
| tests/test\_config.py                                |      173 |        0 |    100% |           |
| tests/test\_database.py                              |     1860 |        2 |     99% | 609, 1764 |
| tests/test\_db\_access.py                            |       13 |        0 |    100% |           |
| tests/test\_dream.py                                 |      782 |        4 |     99% |486-487, 584, 1032 |
| tests/test\_dream\_status.py                         |      236 |        0 |    100% |           |
| tests/test\_export\_import.py                        |      271 |        0 |    100% |           |
| tests/test\_hooks\_plugin.py                         |     1067 |       66 |     94% |1670-1675, 1680-1683, 1688-1696, 1707-1710, 1739-1742, 1784-1787, 1791-1794, 1798-1802, 1806-1808, 1813-1815, 1820-1825, 1829-1830, 1834-1836, 1841-1849 |
| tests/test\_metrics.py                               |      183 |        0 |    100% |           |
| tests/test\_models.py                                |       13 |        0 |    100% |           |
| tests/test\_path\_resolver.py                        |       41 |        2 |     95% |     56-57 |
| tests/test\_payload.py                               |       68 |        0 |    100% |           |
| tests/test\_relocate.py                              |      111 |        0 |    100% |           |
| tests/test\_review\_tracker.py                       |       42 |        0 |    100% |           |
| tests/test\_server.py                                |      915 |        1 |     99% |      1573 |
| tests/test\_server\_http.py                          |       73 |        0 |    100% |           |
| tests/test\_tool\_annotations.py                     |       17 |        0 |    100% |           |
| tests/test\_tool\_schemas.py                         |       66 |        0 |    100% |           |
| tests/test\_tracker.py                               |       74 |        0 |    100% |           |
| tests/test\_usefulness.py                            |       84 |        0 |    100% |           |
| tests/test\_visualise.py                             |      656 |        0 |    100% |           |
| **TOTAL**                                            | **14909** |  **480** | **97%** |           |


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