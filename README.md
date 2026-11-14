# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/alexfayers/mcp-memory/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                 |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------------------- | -------: | -------: | ------: | --------: |
| src/mcp\_memory/\_\_init\_\_.py                      |        0 |        0 |    100% |           |
| src/mcp\_memory/activity.py                          |      113 |        3 |     97% |147-148, 198 |
| src/mcp\_memory/api.py                               |       97 |        8 |     92% |63, 66, 94-95, 138, 165, 169-170 |
| src/mcp\_memory/api\_client.py                       |       22 |        3 |     86% | 27, 31-32 |
| src/mcp\_memory/atomic\_write.py                     |       16 |        3 |     81% |     20-22 |
| src/mcp\_memory/audit.py                             |      164 |        1 |     99% |       401 |
| src/mcp\_memory/backup.py                            |       99 |        4 |     96% |   130-133 |
| src/mcp\_memory/cli.py                               |      650 |      121 |     81% |88, 183, 212-217, 235-248, 253-262, 271, 283, 298, 354-358, 363-374, 486-487, 494-495, 500-502, 506-508, 526-527, 534-535, 539-541, 616-618, 639, 655-656, 678-679, 682-685, 691-694, 700-703, 761-762, 796-797, 832-833, 860-869, 877-879, 897-898, 922-924, 1076, 1078, 1080, 1084-1086, 1107, 1109, 1111, 1114-1125 |
| src/mcp\_memory/config.py                            |      135 |        7 |     95% |149-153, 158, 265 |
| src/mcp\_memory/dedupe.py                            |       35 |        1 |     97% |        42 |
| src/mcp\_memory/dream.py                             |      461 |       18 |     96% |246-248, 258-260, 560, 565, 609, 659, 768-769, 870-872, 877, 898-899 |
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
| src/mcp\_memory/models.py                            |       46 |        0 |    100% |           |
| src/mcp\_memory/path\_resolver.py                    |       22 |        1 |     95% |        15 |
| src/mcp\_memory/payload.py                           |       12 |        0 |    100% |           |
| src/mcp\_memory/plan\_apply.py                       |       39 |        1 |     97% |        40 |
| src/mcp\_memory/relocate.py                          |       50 |        2 |     96% |     33-34 |
| src/mcp\_memory/server.py                            |      575 |       38 |     93% |61-62, 484, 494, 530, 543, 599-601, 605, 679-680, 710-711, 719, 727, 739-740, 802-803, 817-818, 1011-1012, 1032, 1053-1058, 1150, 1193-1194, 1248-1249, 1480, 1484 |
| src/mcp\_memory/storage/\_\_init\_\_.py              |        5 |        0 |    100% |           |
| src/mcp\_memory/storage/bootstrap.py                 |       37 |        0 |    100% |           |
| src/mcp\_memory/storage/connection.py                |       69 |        0 |    100% |           |
| src/mcp\_memory/storage/operations/\_\_init\_\_.py   |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/operations/maintenance.py    |       53 |        3 |     94% |150-151, 158 |
| src/mcp\_memory/storage/operations/reads.py          |      144 |        0 |    100% |           |
| src/mcp\_memory/storage/operations/transfer.py       |      119 |        2 |     98% |  215, 240 |
| src/mcp\_memory/storage/pure/\_\_init\_\_.py         |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/pure/ranking.py              |       18 |        0 |    100% |           |
| src/mcp\_memory/storage/pure/rows.py                 |       40 |        0 |    100% |           |
| src/mcp\_memory/storage/pure/sql.py                  |       19 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/\_\_init\_\_.py |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/entities.py     |      166 |        6 |     96% |36, 41, 97-99, 101 |
| src/mcp\_memory/storage/repositories/observations.py |      146 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/projects.py     |      101 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/relations.py    |       88 |        4 |     95% |98, 101, 125, 137 |
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
| tests/test\_audit.py                                 |      302 |        0 |    100% |           |
| tests/test\_backup.py                                |      140 |        0 |    100% |           |
| tests/test\_cli.py                                   |      657 |        2 |     99% |   70, 263 |
| tests/test\_config.py                                |      173 |        0 |    100% |           |
| tests/test\_database.py                              |     2032 |        2 |     99% | 617, 2012 |
| tests/test\_db\_access.py                            |       13 |        0 |    100% |           |
| tests/test\_dream.py                                 |      782 |        4 |     99% |486-487, 584, 1032 |
| tests/test\_dream\_status.py                         |      236 |        0 |    100% |           |
| tests/test\_export\_import.py                        |      271 |        0 |    100% |           |
| tests/test\_hooks\_plugin.py                         |     1075 |       66 |     94% |1682-1687, 1692-1695, 1700-1708, 1719-1722, 1751-1754, 1796-1799, 1803-1806, 1810-1814, 1818-1820, 1825-1827, 1832-1837, 1841-1842, 1846-1848, 1853-1861 |
| tests/test\_metrics.py                               |      183 |        0 |    100% |           |
| tests/test\_models.py                                |       13 |        0 |    100% |           |
| tests/test\_path\_resolver.py                        |       41 |        2 |     95% |     56-57 |
| tests/test\_payload.py                               |       68 |        0 |    100% |           |
| tests/test\_plan\_apply.py                           |       27 |        0 |    100% |           |
| tests/test\_relocate.py                              |      111 |        0 |    100% |           |
| tests/test\_review\_tracker.py                       |       42 |        0 |    100% |           |
| tests/test\_server.py                                |      947 |        1 |     99% |      1619 |
| tests/test\_server\_http.py                          |       73 |        0 |    100% |           |
| tests/test\_tool\_annotations.py                     |       17 |        0 |    100% |           |
| tests/test\_tool\_schemas.py                         |       66 |        0 |    100% |           |
| tests/test\_tracker.py                               |       74 |        0 |    100% |           |
| tests/test\_usefulness.py                            |       84 |        0 |    100% |           |
| tests/test\_visualise.py                             |      656 |        0 |    100% |           |
| **TOTAL**                                            | **15394** |  **476** | **97%** |           |


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