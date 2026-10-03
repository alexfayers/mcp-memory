# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/alexfayers/mcp-memory/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                 |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------------------- | -------: | -------: | ------: | --------: |
| src/mcp\_memory/\_\_init\_\_.py                      |        0 |        0 |    100% |           |
| src/mcp\_memory/activity.py                          |      113 |        3 |     97% |144-145, 193 |
| src/mcp\_memory/agent.py                             |      286 |       13 |     95% |495-510, 868-869, 940 |
| src/mcp\_memory/atomic\_write.py                     |       16 |        3 |     81% |     20-22 |
| src/mcp\_memory/audit.py                             |      108 |        0 |    100% |           |
| src/mcp\_memory/cli.py                               |      462 |      178 |     61% |59, 111-115, 174-187, 192-205, 210-219, 229-233, 238-249, 277-278, 285-286, 291-293, 297-299, 311-312, 409-411, 424-426, 434-477, 489, 510-516, 536-542, 567-579, 601-610, 622-623, 627-628, 638-644, 662-663, 678-684, 812-825, 834, 836, 838, 840, 845-854 |
| src/mcp\_memory/config.py                            |      117 |        2 |     98% |  138, 260 |
| src/mcp\_memory/dream\_status.py                     |      113 |        2 |     98% |  227, 253 |
| src/mcp\_memory/eval.py                              |      131 |        0 |    100% |           |
| src/mcp\_memory/export\_import.py                    |       69 |        4 |     94% |52-53, 85, 87 |
| src/mcp\_memory/hooks/\_\_init\_\_.py                |        0 |        0 |    100% |           |
| src/mcp\_memory/hooks/plugin.py                      |      414 |       29 |     93% |204, 249, 364, 371, 461, 523-524, 529-537, 571, 577, 617, 620-622, 632, 639-643, 670-675, 680 |
| src/mcp\_memory/hooks/review\_tracker.py             |       28 |        0 |    100% |           |
| src/mcp\_memory/hooks/tracker.py                     |       72 |        1 |     99% |       108 |
| src/mcp\_memory/jsonc.py                             |       64 |       13 |     80% |13, 15, 37-42, 58-60, 77-78 |
| src/mcp\_memory/metrics.py                           |       86 |        2 |     98% |     64-65 |
| src/mcp\_memory/migrations/\_\_init\_\_.py           |        0 |        0 |    100% |           |
| src/mcp\_memory/migrations/runner.py                 |       30 |        3 |     90% |     24-26 |
| src/mcp\_memory/migrations/schema.py                 |       23 |        0 |    100% |           |
| src/mcp\_memory/models.py                            |       42 |        0 |    100% |           |
| src/mcp\_memory/path\_resolver.py                    |       35 |        1 |     97% |        18 |
| src/mcp\_memory/payload.py                           |       11 |        0 |    100% |           |
| src/mcp\_memory/recall\_efficiency.py                |       18 |        0 |    100% |           |
| src/mcp\_memory/recall\_status.py                    |       60 |        1 |     98% |       133 |
| src/mcp\_memory/relocate.py                          |       50 |        2 |     96% |     33-34 |
| src/mcp\_memory/server.py                            |      451 |       48 |     89% |51-52, 314, 406, 416, 454, 512-514, 518, 524-527, 584-585, 610-611, 619, 627, 639-640, 682-683, 697-698, 867-868, 893, 909-914, 949-956, 986-987, 1010-1011, 1031-1032, 1188, 1192 |
| src/mcp\_memory/storage/\_\_init\_\_.py              |        5 |        0 |    100% |           |
| src/mcp\_memory/storage/bootstrap.py                 |       37 |        0 |    100% |           |
| src/mcp\_memory/storage/connection.py                |       60 |        0 |    100% |           |
| src/mcp\_memory/storage/operations/\_\_init\_\_.py   |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/operations/maintenance.py    |       49 |        3 |     94% |123-124, 131 |
| src/mcp\_memory/storage/operations/reads.py          |      145 |        0 |    100% |           |
| src/mcp\_memory/storage/operations/transfer.py       |      118 |        2 |     98% |  212, 237 |
| src/mcp\_memory/storage/pure/\_\_init\_\_.py         |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/pure/ranking.py              |       18 |        0 |    100% |           |
| src/mcp\_memory/storage/pure/rows.py                 |       36 |        0 |    100% |           |
| src/mcp\_memory/storage/pure/sql.py                  |       18 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/\_\_init\_\_.py |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/entities.py     |      155 |        6 |     96% |35, 40, 96-98, 100 |
| src/mcp\_memory/storage/repositories/observations.py |       95 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/projects.py     |       98 |        1 |     99% |        58 |
| src/mcp\_memory/storage/repositories/relations.py    |       76 |        4 |     95% |66, 97, 100, 123 |
| src/mcp\_memory/storage/repositories/telemetry.py    |       49 |        1 |     98% |        74 |
| src/mcp\_memory/storage/services/\_\_init\_\_.py     |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/services/fts.py              |       11 |        0 |    100% |           |
| src/mcp\_memory/storage/services/ids.py              |       29 |        0 |    100% |           |
| src/mcp\_memory/storage/services/integrity.py        |       24 |        0 |    100% |           |
| src/mcp\_memory/usefulness.py                        |       66 |        2 |     97% |     43-44 |
| src/mcp\_memory/visualise.py                         |      175 |        5 |     97% |161, 221-222, 310, 314 |
| tests/\_\_init\_\_.py                                |       43 |        1 |     98% |        85 |
| tests/conftest.py                                    |       10 |        0 |    100% |           |
| tests/eval/\_\_init\_\_.py                           |        0 |        0 |    100% |           |
| tests/eval/eval\_baseline.py                         |       58 |        0 |    100% |           |
| tests/eval/eval\_fixture.py                          |      229 |        3 |     99% |542-543, 561 |
| tests/eval/eval\_harness.py                          |       92 |        0 |    100% |           |
| tests/eval/regen\_baseline.py                        |       60 |        1 |     98% |       124 |
| tests/eval/size\_baseline.py                         |       63 |        1 |     98% |       119 |
| tests/eval/test\_benchmark\_scenarios.py             |       75 |        0 |    100% |           |
| tests/eval/test\_eval\_baseline.py                   |       83 |        0 |    100% |           |
| tests/eval/test\_eval\_fixture.py                    |      161 |        1 |     99% |       142 |
| tests/eval/test\_eval\_harness.py                    |      118 |        0 |    100% |           |
| tests/eval/test\_ranking\_eval.py                    |      403 |        0 |    100% |           |
| tests/eval/test\_recall\_efficiency.py               |       39 |        0 |    100% |           |
| tests/eval/test\_regen\_baseline.py                  |      101 |        0 |    100% |           |
| tests/eval/test\_size\_baseline.py                   |       81 |        0 |    100% |           |
| tests/naming\_check.py                               |      105 |      105 |      0% |    12-162 |
| tests/test\_activity.py                              |      145 |        0 |    100% |           |
| tests/test\_agent.py                                 |      749 |        9 |     99% |560-561, 688, 929-930, 976-977, 1041-1042 |
| tests/test\_audit.py                                 |      251 |        0 |    100% |           |
| tests/test\_cli.py                                   |      245 |        0 |    100% |           |
| tests/test\_config.py                                |      198 |        0 |    100% |           |
| tests/test\_database.py                              |     1733 |        2 |     99% | 589, 1726 |
| tests/test\_dream\_status.py                         |      200 |        0 |    100% |           |
| tests/test\_export\_import.py                        |      280 |        0 |    100% |           |
| tests/test\_hooks\_plugin.py                         |      846 |       67 |     92% |514, 1434-1439, 1444-1447, 1452-1460, 1471-1474, 1503-1506, 1548-1551, 1555-1558, 1562-1566, 1570-1572, 1577-1579, 1584-1589, 1593-1594, 1598-1600, 1605-1613 |
| tests/test\_metrics.py                               |      194 |        0 |    100% |           |
| tests/test\_models.py                                |       13 |        0 |    100% |           |
| tests/test\_path\_resolver.py                        |       70 |        2 |     97% |     59-60 |
| tests/test\_payload.py                               |       58 |        0 |    100% |           |
| tests/test\_recall\_status.py                        |       91 |        0 |    100% |           |
| tests/test\_relocate.py                              |      111 |        0 |    100% |           |
| tests/test\_review\_tracker.py                       |       42 |        0 |    100% |           |
| tests/test\_server.py                                |      591 |        0 |    100% |           |
| tests/test\_tool\_annotations.py                     |       12 |        0 |    100% |           |
| tests/test\_tracker.py                               |       51 |        0 |    100% |           |
| tests/test\_usefulness.py                            |       69 |        0 |    100% |           |
| tests/test\_visualise.py                             |      620 |        0 |    100% |           |
| **TOTAL**                                            | **12353** |  **521** | **96%** |           |


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