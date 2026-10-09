# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/alexfayers/mcp-memory/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                 |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------------------- | -------: | -------: | ------: | --------: |
| src/mcp\_memory/\_\_init\_\_.py                      |        0 |        0 |    100% |           |
| src/mcp\_memory/activity.py                          |      113 |        3 |     97% |145-146, 195 |
| src/mcp\_memory/agent.py                             |      285 |       13 |     95% |462-477, 835-836, 907 |
| src/mcp\_memory/atomic\_write.py                     |       16 |        3 |     81% |     20-22 |
| src/mcp\_memory/audit.py                             |      108 |        0 |    100% |           |
| src/mcp\_memory/cli.py                               |      476 |      178 |     63% |61, 108-112, 173, 194-206, 224-237, 242-251, 261-265, 270-281, 309-310, 317-318, 323-325, 329-331, 343-344, 441-443, 456-458, 466-509, 521, 542-548, 568-574, 599-611, 633-642, 654-655, 659-660, 670-676, 694-695, 710-716, 844-857, 866, 868, 870, 872, 877-886 |
| src/mcp\_memory/config.py                            |      118 |        2 |     98% |  141, 263 |
| src/mcp\_memory/dream\_status.py                     |      113 |        2 |     98% |  227, 253 |
| src/mcp\_memory/eval.py                              |      135 |        0 |    100% |           |
| src/mcp\_memory/export\_import.py                    |       69 |        4 |     94% |52-53, 85, 87 |
| src/mcp\_memory/hooks/\_\_init\_\_.py                |        0 |        0 |    100% |           |
| src/mcp\_memory/hooks/plugin.py                      |      418 |       26 |     94% |182, 229, 347, 354, 510, 572-573, 578-586, 608, 635, 675, 678-680, 690, 697-701, 739 |
| src/mcp\_memory/hooks/review\_tracker.py             |       28 |        0 |    100% |           |
| src/mcp\_memory/hooks/tracker.py                     |       90 |        1 |     99% |       108 |
| src/mcp\_memory/jsonc.py                             |       64 |       13 |     80% |13, 15, 37-42, 58-60, 77-78 |
| src/mcp\_memory/metrics.py                           |       86 |        0 |    100% |           |
| src/mcp\_memory/migrations/\_\_init\_\_.py           |        0 |        0 |    100% |           |
| src/mcp\_memory/migrations/runner.py                 |       30 |        3 |     90% |     24-26 |
| src/mcp\_memory/migrations/schema.py                 |       23 |        0 |    100% |           |
| src/mcp\_memory/models.py                            |       42 |        0 |    100% |           |
| src/mcp\_memory/path\_resolver.py                    |       35 |        1 |     97% |        18 |
| src/mcp\_memory/payload.py                           |       11 |        0 |    100% |           |
| src/mcp\_memory/recall\_efficiency.py                |       18 |        0 |    100% |           |
| src/mcp\_memory/recall\_status.py                    |       60 |        1 |     98% |       133 |
| src/mcp\_memory/relocate.py                          |       50 |        2 |     96% |     33-34 |
| src/mcp\_memory/server.py                            |      516 |       46 |     91% |52-53, 349, 446, 456, 494, 554-556, 560, 570, 627-628, 653-654, 662, 670, 682-683, 743-744, 758-759, 940-941, 966, 982-987, 1022-1029, 1059-1060, 1083-1084, 1104-1105, 1300, 1304 |
| src/mcp\_memory/storage/\_\_init\_\_.py              |        5 |        0 |    100% |           |
| src/mcp\_memory/storage/bootstrap.py                 |       37 |        0 |    100% |           |
| src/mcp\_memory/storage/connection.py                |       60 |        0 |    100% |           |
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
| src/mcp\_memory/tool\_names.py                       |        2 |        0 |    100% |           |
| src/mcp\_memory/usefulness.py                        |       72 |        2 |     97% |     50-51 |
| src/mcp\_memory/visualise.py                         |      175 |        5 |     97% |161, 221-222, 310, 314 |
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
| tests/eval/test\_benchmark\_scenarios.py             |       75 |        0 |    100% |           |
| tests/eval/test\_compare\_baseline.py                |      241 |        0 |    100% |           |
| tests/eval/test\_eval\_baseline.py                   |       26 |        0 |    100% |           |
| tests/eval/test\_eval\_fixture.py                    |      172 |        1 |     99% |       150 |
| tests/eval/test\_eval\_harness.py                    |       95 |        0 |    100% |           |
| tests/eval/test\_ranking\_eval.py                    |      418 |        0 |    100% |           |
| tests/eval/test\_ranking\_replay.py                  |       50 |        0 |    100% |           |
| tests/eval/test\_recall\_efficiency.py               |       39 |        0 |    100% |           |
| tests/eval/test\_regen\_baseline.py                  |       52 |        0 |    100% |           |
| tests/eval/test\_size\_baseline.py                   |       42 |        0 |    100% |           |
| tests/naming\_check.py                               |      105 |      105 |      0% |    12-162 |
| tests/test\_activity.py                              |      148 |        0 |    100% |           |
| tests/test\_agent.py                                 |      749 |        9 |     99% |560-561, 688, 929-930, 976-977, 1041-1042 |
| tests/test\_audit.py                                 |      251 |        0 |    100% |           |
| tests/test\_cli.py                                   |      278 |        0 |    100% |           |
| tests/test\_config.py                                |      191 |        0 |    100% |           |
| tests/test\_database.py                              |     1850 |        2 |     99% | 589, 1744 |
| tests/test\_dream\_status.py                         |      200 |        0 |    100% |           |
| tests/test\_export\_import.py                        |      280 |        0 |    100% |           |
| tests/test\_hooks\_plugin.py                         |      963 |       67 |     93% |682, 1633-1638, 1643-1646, 1651-1659, 1670-1673, 1702-1705, 1747-1750, 1754-1757, 1761-1765, 1769-1771, 1776-1778, 1783-1788, 1792-1793, 1797-1799, 1804-1812 |
| tests/test\_metrics.py                               |      194 |        0 |    100% |           |
| tests/test\_models.py                                |       13 |        0 |    100% |           |
| tests/test\_path\_resolver.py                        |       70 |        2 |     97% |     59-60 |
| tests/test\_payload.py                               |       58 |        0 |    100% |           |
| tests/test\_recall\_status.py                        |       91 |        0 |    100% |           |
| tests/test\_relocate.py                              |      111 |        0 |    100% |           |
| tests/test\_review\_tracker.py                       |       42 |        0 |    100% |           |
| tests/test\_server.py                                |      747 |        0 |    100% |           |
| tests/test\_tool\_annotations.py                     |       12 |        0 |    100% |           |
| tests/test\_tool\_schemas.py                         |       50 |        0 |    100% |           |
| tests/test\_tracker.py                               |       66 |        0 |    100% |           |
| tests/test\_usefulness.py                            |       84 |        0 |    100% |           |
| tests/test\_visualise.py                             |      620 |        0 |    100% |           |
| **TOTAL**                                            | **13317** |  **514** | **96%** |           |


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