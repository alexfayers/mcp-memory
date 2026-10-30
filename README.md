# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/alexfayers/mcp-memory/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                 |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------------------- | -------: | -------: | ------: | --------: |
| src/mcp\_memory/\_\_init\_\_.py                      |        0 |        0 |    100% |           |
| src/mcp\_memory/activity.py                          |      113 |        3 |     97% |145-146, 195 |
| src/mcp\_memory/atomic\_write.py                     |       16 |        3 |     81% |     20-22 |
| src/mcp\_memory/audit.py                             |      108 |        0 |    100% |           |
| src/mcp\_memory/cli.py                               |      578 |      125 |     78% |67, 87-91, 162, 191-196, 214-227, 232-241, 250, 262, 277, 333-337, 342-353, 381-382, 389-390, 395-397, 401-403, 415-416, 513-515, 528-530, 543, 559-560, 582-583, 586-589, 595-598, 604-607, 665-666, 700-701, 736-737, 764-773, 781-783, 801-802, 826-828, 960, 962, 964, 968-970, 979, 981, 983, 985, 990-1001 |
| src/mcp\_memory/config.py                            |      105 |        1 |     99% |       226 |
| src/mcp\_memory/dedupe.py                            |       35 |        1 |     97% |        42 |
| src/mcp\_memory/dream.py                             |      201 |       10 |     95% |   340-355 |
| src/mcp\_memory/dream\_status.py                     |      113 |        2 |     98% |  226, 252 |
| src/mcp\_memory/eval.py                              |      135 |        0 |    100% |           |
| src/mcp\_memory/export\_import.py                    |       69 |        4 |     94% |52-53, 85, 87 |
| src/mcp\_memory/hooks/\_\_init\_\_.py                |        0 |        0 |    100% |           |
| src/mcp\_memory/hooks/plugin.py                      |      489 |       27 |     94% |225, 389, 432, 435, 466-467, 673-674, 679-687, 709, 752, 792, 795-797, 807, 814-818, 870 |
| src/mcp\_memory/hooks/review\_tracker.py             |       28 |        0 |    100% |           |
| src/mcp\_memory/hooks/tracker.py                     |      111 |        1 |     99% |       130 |
| src/mcp\_memory/jsonc.py                             |       64 |       13 |     80% |13, 15, 37-42, 58-60, 77-78 |
| src/mcp\_memory/metrics.py                           |       86 |        2 |     98% |     64-65 |
| src/mcp\_memory/migrations/\_\_init\_\_.py           |        0 |        0 |    100% |           |
| src/mcp\_memory/migrations/runner.py                 |       30 |        3 |     90% |     24-26 |
| src/mcp\_memory/migrations/schema.py                 |       23 |        0 |    100% |           |
| src/mcp\_memory/models.py                            |       42 |        0 |    100% |           |
| src/mcp\_memory/path\_resolver.py                    |       35 |        1 |     97% |        18 |
| src/mcp\_memory/payload.py                           |       12 |        0 |    100% |           |
| src/mcp\_memory/relocate.py                          |       50 |        2 |     96% |     33-34 |
| src/mcp\_memory/server.py                            |      524 |       45 |     91% |55-56, 350, 447, 457, 495, 555-557, 561, 635-636, 666-667, 675, 683, 695-696, 758-759, 773-774, 967-968, 995, 1013-1018, 1057-1064, 1103-1104, 1130-1131, 1153-1154, 1370, 1374 |
| src/mcp\_memory/storage/\_\_init\_\_.py              |        5 |        0 |    100% |           |
| src/mcp\_memory/storage/bootstrap.py                 |       37 |        0 |    100% |           |
| src/mcp\_memory/storage/connection.py                |       69 |        0 |    100% |           |
| src/mcp\_memory/storage/operations/\_\_init\_\_.py   |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/operations/maintenance.py    |       49 |        3 |     94% |123-124, 131 |
| src/mcp\_memory/storage/operations/reads.py          |      148 |        0 |    100% |           |
| src/mcp\_memory/storage/operations/transfer.py       |      118 |        2 |     98% |  212, 237 |
| src/mcp\_memory/storage/pure/\_\_init\_\_.py         |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/pure/ranking.py              |       18 |        0 |    100% |           |
| src/mcp\_memory/storage/pure/rows.py                 |       36 |        0 |    100% |           |
| src/mcp\_memory/storage/pure/sql.py                  |       18 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/\_\_init\_\_.py |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/entities.py     |      157 |        6 |     96% |35, 40, 96-98, 100 |
| src/mcp\_memory/storage/repositories/observations.py |      127 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/projects.py     |       97 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/relations.py    |       76 |        3 |     96% |97, 100, 123 |
| src/mcp\_memory/storage/repositories/telemetry.py    |       49 |        1 |     98% |        74 |
| src/mcp\_memory/storage/services/\_\_init\_\_.py     |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/services/fts.py              |       11 |        0 |    100% |           |
| src/mcp\_memory/storage/services/ids.py              |       29 |        0 |    100% |           |
| src/mcp\_memory/storage/services/integrity.py        |       24 |        0 |    100% |           |
| src/mcp\_memory/tool\_names.py                       |        3 |        0 |    100% |           |
| src/mcp\_memory/usefulness.py                        |       72 |        2 |     97% |     50-51 |
| src/mcp\_memory/visualise.py                         |      160 |        5 |     97% |136, 195-196, 269, 273 |
| tests/\_\_init\_\_.py                                |       43 |        1 |     98% |        85 |
| tests/conftest.py                                    |       10 |        0 |    100% |           |
| tests/eval/\_\_init\_\_.py                           |        0 |        0 |    100% |           |
| tests/eval/badge\_endpoints.py                       |       27 |        1 |     96% |        48 |
| tests/eval/compare\_baseline.py                      |      178 |        2 |     99% |  115, 305 |
| tests/eval/eval\_baseline.py                         |       21 |        0 |    100% |           |
| tests/eval/eval\_fixture.py                          |      246 |        3 |     99% |575-576, 594 |
| tests/eval/eval\_harness.py                          |       78 |        0 |    100% |           |
| tests/eval/ranking\_replay.py                        |       24 |        0 |    100% |           |
| tests/eval/regen\_baseline.py                        |       27 |        1 |     96% |        61 |
| tests/eval/size\_baseline.py                         |       45 |        0 |    100% |           |
| tests/eval/test\_badge\_endpoints.py                 |       23 |        0 |    100% |           |
| tests/eval/test\_benchmark\_scenarios.py             |       58 |        0 |    100% |           |
| tests/eval/test\_compare\_baseline.py                |      241 |        0 |    100% |           |
| tests/eval/test\_eval\_baseline.py                   |       26 |        0 |    100% |           |
| tests/eval/test\_eval\_fixture.py                    |      172 |        1 |     99% |       150 |
| tests/eval/test\_eval\_harness.py                    |       95 |        0 |    100% |           |
| tests/eval/test\_ranking\_eval.py                    |      418 |        0 |    100% |           |
| tests/eval/test\_ranking\_replay.py                  |       50 |        0 |    100% |           |
| tests/eval/test\_regen\_baseline.py                  |       52 |        0 |    100% |           |
| tests/eval/test\_size\_baseline.py                   |       42 |        0 |    100% |           |
| tests/naming\_check.py                               |      105 |      105 |      0% |    12-162 |
| tests/test\_activity.py                              |      148 |        0 |    100% |           |
| tests/test\_audit.py                                 |      251 |        0 |    100% |           |
| tests/test\_cli.py                                   |      513 |        2 |     99% |   56, 249 |
| tests/test\_config.py                                |      173 |        0 |    100% |           |
| tests/test\_database.py                              |     1850 |        2 |     99% | 589, 1744 |
| tests/test\_dream.py                                 |      483 |        3 |     99% |429-430, 527 |
| tests/test\_dream\_status.py                         |      200 |        0 |    100% |           |
| tests/test\_export\_import.py                        |      280 |        0 |    100% |           |
| tests/test\_hooks\_plugin.py                         |     1065 |       67 |     94% |708, 1694-1699, 1704-1707, 1712-1720, 1731-1734, 1763-1766, 1808-1811, 1815-1818, 1822-1826, 1830-1832, 1837-1839, 1844-1849, 1853-1854, 1858-1860, 1865-1873 |
| tests/test\_metrics.py                               |      194 |        0 |    100% |           |
| tests/test\_models.py                                |       13 |        0 |    100% |           |
| tests/test\_path\_resolver.py                        |       70 |        2 |     97% |     59-60 |
| tests/test\_payload.py                               |       68 |        0 |    100% |           |
| tests/test\_relocate.py                              |      111 |        0 |    100% |           |
| tests/test\_review\_tracker.py                       |       42 |        0 |    100% |           |
| tests/test\_server.py                                |      796 |        1 |     99% |      1410 |
| tests/test\_server\_http.py                          |       59 |        0 |    100% |           |
| tests/test\_tool\_annotations.py                     |       17 |        0 |    100% |           |
| tests/test\_tool\_schemas.py                         |       56 |        0 |    100% |           |
| tests/test\_tracker.py                               |       74 |        0 |    100% |           |
| tests/test\_usefulness.py                            |       84 |        0 |    100% |           |
| tests/test\_visualise.py                             |      558 |        0 |    100% |           |
| **TOTAL**                                            | **13356** |  **456** | **97%** |           |


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