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
| src/mcp\_memory/cli.py                               |      673 |      121 |     82% |93, 188, 217-222, 240-253, 258-267, 300, 312, 327, 383-387, 392-403, 524-525, 532-533, 538-540, 544-546, 564-565, 572-573, 577-579, 654-656, 677, 693-694, 716-717, 720-723, 729-732, 738-741, 799-800, 834-835, 870-871, 898-907, 915-917, 935-936, 960-962, 1116, 1118, 1120, 1124-1126, 1148, 1150, 1152, 1155-1166 |
| src/mcp\_memory/config.py                            |      135 |        7 |     95% |149-153, 158, 265 |
| src/mcp\_memory/dedupe.py                            |       35 |        1 |     97% |        42 |
| src/mcp\_memory/dream.py                             |      536 |       19 |     96% |265, 353-355, 365-367, 676, 681, 725, 775, 884-885, 988-990, 995, 1016-1017 |
| src/mcp\_memory/dream\_status.py                     |      142 |        2 |     99% |  284, 318 |
| src/mcp\_memory/eval.py                              |      135 |        0 |    100% |           |
| src/mcp\_memory/export\_import.py                    |       69 |        4 |     94% |52-53, 88, 90 |
| src/mcp\_memory/hooks/\_\_init\_\_.py                |        0 |        0 |    100% |           |
| src/mcp\_memory/hooks/plugin.py                      |      471 |       25 |     95% |229, 231, 259, 407, 636-637, 642-650, 672, 715, 754, 757-759, 770, 777-781, 833 |
| src/mcp\_memory/hooks/review\_tracker.py             |       28 |        0 |    100% |           |
| src/mcp\_memory/hooks/tracker.py                     |      111 |        1 |     99% |       130 |
| src/mcp\_memory/jsonc.py                             |       64 |       13 |     80% |13, 15, 37-42, 58-60, 77-78 |
| src/mcp\_memory/metrics.py                           |       86 |        0 |    100% |           |
| src/mcp\_memory/migrations/\_\_init\_\_.py           |        0 |        0 |    100% |           |
| src/mcp\_memory/migrations/runner.py                 |       32 |        4 |     88% |     24-27 |
| src/mcp\_memory/migrations/schema.py                 |       23 |        0 |    100% |           |
| src/mcp\_memory/models.py                            |       46 |        0 |    100% |           |
| src/mcp\_memory/nudge\_count.py                      |       43 |        0 |    100% |           |
| src/mcp\_memory/path\_resolver.py                    |       22 |        1 |     95% |        15 |
| src/mcp\_memory/payload.py                           |       12 |        0 |    100% |           |
| src/mcp\_memory/plan\_apply.py                       |       39 |        1 |     97% |        40 |
| src/mcp\_memory/relocate.py                          |       50 |        2 |     96% |     33-34 |
| src/mcp\_memory/server.py                            |      606 |       44 |     93% |71-72, 494, 504, 540, 553, 609-611, 615, 689-690, 720-721, 729, 737, 749-750, 812-813, 827-828, 1021-1022, 1042, 1063-1068, 1160, 1203-1204, 1258-1259, 1502-1503, 1534-1538, 1542 |
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
| src/mcp\_memory/storage/repositories/observations.py |      148 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/projects.py     |      101 |        0 |    100% |           |
| src/mcp\_memory/storage/repositories/relations.py    |       88 |        4 |     95% |98, 101, 127, 139 |
| src/mcp\_memory/storage/repositories/telemetry.py    |       49 |        1 |     98% |        74 |
| src/mcp\_memory/storage/services/\_\_init\_\_.py     |        0 |        0 |    100% |           |
| src/mcp\_memory/storage/services/fts.py              |       11 |        0 |    100% |           |
| src/mcp\_memory/storage/services/ids.py              |       29 |        0 |    100% |           |
| src/mcp\_memory/storage/services/integrity.py        |       24 |        0 |    100% |           |
| src/mcp\_memory/telemetry.py                         |      191 |        6 |     97% |64-65, 145-146, 163-164 |
| src/mcp\_memory/tool\_names.py                       |        4 |        0 |    100% |           |
| src/mcp\_memory/usefulness.py                        |       73 |        2 |     97% |     51-52 |
| src/mcp\_memory/visualise.py                         |      184 |        5 |     97% |165, 234-235, 308, 312 |
| tests/\_\_init\_\_.py                                |       43 |        1 |     98% |        85 |
| tests/conftest.py                                    |       43 |        0 |    100% |           |
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
| tests/test\_api\_client.py                           |       47 |        0 |    100% |           |
| tests/test\_audit.py                                 |      302 |        0 |    100% |           |
| tests/test\_backup.py                                |      140 |        0 |    100% |           |
| tests/test\_cli.py                                   |      716 |        1 |     99% |       328 |
| tests/test\_config.py                                |      173 |        0 |    100% |           |
| tests/test\_database.py                              |     2044 |        2 |     99% | 617, 2038 |
| tests/test\_db\_access.py                            |       13 |        0 |    100% |           |
| tests/test\_dream.py                                 |      866 |        5 |     99% |506-507, 604, 1052, 1111 |
| tests/test\_dream\_status.py                         |      239 |        0 |    100% |           |
| tests/test\_export\_import.py                        |      271 |        0 |    100% |           |
| tests/test\_hooks\_plugin.py                         |     1091 |       66 |     94% |1712-1717, 1722-1725, 1730-1738, 1749-1752, 1781-1784, 1826-1829, 1833-1836, 1840-1844, 1848-1850, 1855-1857, 1862-1867, 1871-1872, 1876-1878, 1883-1891 |
| tests/test\_metrics.py                               |      183 |        0 |    100% |           |
| tests/test\_models.py                                |       13 |        0 |    100% |           |
| tests/test\_nudge\_count.py                          |       12 |        0 |    100% |           |
| tests/test\_path\_resolver.py                        |       41 |        2 |     95% |     56-57 |
| tests/test\_payload.py                               |       68 |        0 |    100% |           |
| tests/test\_plan\_apply.py                           |       27 |        0 |    100% |           |
| tests/test\_relocate.py                              |      111 |        0 |    100% |           |
| tests/test\_review\_tracker.py                       |       42 |        0 |    100% |           |
| tests/test\_server.py                                |      963 |        1 |     99% |      1643 |
| tests/test\_server\_http.py                          |      110 |        0 |    100% |           |
| tests/test\_telemetry.py                             |      413 |        1 |     99% |       662 |
| tests/test\_telemetry\_contract.py                   |       11 |        0 |    100% |           |
| tests/test\_tool\_annotations.py                     |       17 |        0 |    100% |           |
| tests/test\_tool\_schemas.py                         |       66 |        0 |    100% |           |
| tests/test\_tracker.py                               |       74 |        0 |    100% |           |
| tests/test\_usefulness.py                            |       84 |        0 |    100% |           |
| tests/test\_visualise.py                             |      656 |        0 |    100% |           |
| **TOTAL**                                            | **16544** |  **489** | **97%** |           |


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