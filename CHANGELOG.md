# Changelog

## [0.7.0](https://github.com/alexfayers/mcp-memory/compare/v0.6.0...v0.7.0) (2026-11-28)


### Features

* **audit:** clean up observation labels that repeat their date or provenance ([#251](https://github.com/alexfayers/mcp-memory/issues/251)) ([7e271f4](https://github.com/alexfayers/mcp-memory/commit/7e271f4d528d5e59dddbf5f57b7967aec59670f3))
* **cli:** add a --version flag that prints the installed version ([#266](https://github.com/alexfayers/mcp-memory/issues/266)) ([b669791](https://github.com/alexfayers/mcp-memory/commit/b669791c2cacec9e94e149192011083408c55f31))
* **cli:** add a teardown command that removes the service and the agent config entries ([#248](https://github.com/alexfayers/mcp-memory/issues/248)) ([03f806f](https://github.com/alexfayers/mcp-memory/commit/03f806f5aaca1b91220deb2bfccdcef946325eeb))
* **cli:** let teardown cover opencode and only the chosen agents ([#256](https://github.com/alexfayers/mcp-memory/issues/256)) ([ba4d4ab](https://github.com/alexfayers/mcp-memory/commit/ba4d4abe3b335675dae215cfca4a457f498acda2))
* **cli:** skip the background service when MCP_MEMORY_NO_SERVICE=1 ([#260](https://github.com/alexfayers/mcp-memory/issues/260)) ([663b15b](https://github.com/alexfayers/mcp-memory/commit/663b15b0057926a46cd3dfdc7a9fb75570681a5a))
* **hooks:** block memory writes from subagents ([#249](https://github.com/alexfayers/mcp-memory/issues/249)) ([8c6cf73](https://github.com/alexfayers/mcp-memory/commit/8c6cf73f9b7cc8b0cc8718a0047357924d0c73cf))
* **maintenance:** set retention windows per project scope ([#259](https://github.com/alexfayers/mcp-memory/issues/259)) ([27d22a3](https://github.com/alexfayers/mcp-memory/commit/27d22a3a0fec4071bd52ccf632b717d2fd1e6d33))
* **server:** suggest close entity names and show expected arguments on bad calls ([#264](https://github.com/alexfayers/mcp-memory/issues/264)) ([62aebe8](https://github.com/alexfayers/mcp-memory/commit/62aebe86759dcc59982032be046bab2d826ed47e))
* **storage:** strip redundant date, time and provenance labels on write ([#252](https://github.com/alexfayers/mcp-memory/issues/252)) ([41d272f](https://github.com/alexfayers/mcp-memory/commit/41d272feea760956baf8d4034f9ee0baea4f8fa1))

## [0.6.0](https://github.com/alexfayers/mcp-memory/compare/v0.5.0...v0.6.0) (2026-11-27)


### Features

* **hooks:** drop tracker and recall state once a rewind, /tree or compaction removes its point from the context ([#242](https://github.com/alexfayers/mcp-memory/issues/242)) ([c88b82a](https://github.com/alexfayers/mcp-memory/commit/c88b82a5a1132c55561dfa325834fba4c84e03c3))
* **hooks:** name the memories that strongly match each user message ([#245](https://github.com/alexfayers/mcp-memory/issues/245)) ([a8c07d1](https://github.com/alexfayers/mcp-memory/commit/a8c07d11c5884be9f0bd2c2b58d90688fd3c3a7b))
* **hooks:** recall observations anchored to a commit's patch-id ([#243](https://github.com/alexfayers/mcp-memory/issues/243)) ([7a8022b](https://github.com/alexfayers/mcp-memory/commit/7a8022bf7eeac091143a14b94942818725c0b0c0))
* **hooks:** recall observations from their triggers ([#239](https://github.com/alexfayers/mcp-memory/issues/239)) ([1ce2dc3](https://github.com/alexfayers/mcp-memory/commit/1ce2dc37aec26a405ffbd30951dfbc25f4b2540f))
* keep the database and state files under the shared llm-prompts root ([#237](https://github.com/alexfayers/mcp-memory/issues/237)) ([e19caaf](https://github.com/alexfayers/mcp-memory/commit/e19caaf67ce665a8f6b62f112b2593d560295eb5))
* **rules:** add a recall trigger when recording a do-not-run gotcha ([#244](https://github.com/alexfayers/mcp-memory/issues/244)) ([d864d87](https://github.com/alexfayers/mcp-memory/commit/d864d8759f6f86e21e5fe387c6c390fa12280a9f))
* **rules:** keep dates and provenance prefixes out of observation text ([#246](https://github.com/alexfayers/mcp-memory/issues/246)) ([ea879ef](https://github.com/alexfayers/mcp-memory/commit/ea879ef981923b11ffbbb9c33bc178ade6d85c47))
* **server:** warn when an entity's location belongs to another scope ([#255](https://github.com/alexfayers/mcp-memory/issues/255)) ([55725d7](https://github.com/alexfayers/mcp-memory/commit/55725d7931f1c2ca4f523151f60dc08bcd586a32))
* **visualise:** rebuild the visualiser around a searchable entity list ([#247](https://github.com/alexfayers/mcp-memory/issues/247)) ([43f7aae](https://github.com/alexfayers/mcp-memory/commit/43f7aaeda2ebf3bdee5cf052704309592eabcc55))
* **visualise:** vote from -3 to 3 on the dashboard ([#250](https://github.com/alexfayers/mcp-memory/issues/250)) ([21f5d09](https://github.com/alexfayers/mcp-memory/commit/21f5d09ee7365de58cf31b17d388dee3b9be73ac))


### Bug Fixes

* **ci:** pass the JS test files to node --test by glob ([#253](https://github.com/alexfayers/mcp-memory/issues/253)) ([324d3bf](https://github.com/alexfayers/mcp-memory/commit/324d3bfb1af029da1a20245fb0fe00566d74fd22))

## [0.5.0](https://github.com/alexfayers/mcp-memory/compare/v0.4.0...v0.5.0) (2026-11-26)


### Features

* **observations:** record where each observation came from ([#208](https://github.com/alexfayers/mcp-memory/issues/208)) ([3785a6f](https://github.com/alexfayers/mcp-memory/commit/3785a6f2d554a87ded9e7c7219f966bafce3d449))


### Miscellaneous Chores

* sync shared files from llm-prompts-ci ([#233](https://github.com/alexfayers/mcp-memory/issues/233)) ([0d43838](https://github.com/alexfayers/mcp-memory/commit/0d43838b972ac73c3997d9348902a4c88f3891fd))

## [0.4.0](https://github.com/alexfayers/mcp-memory/compare/v0.3.2...v0.4.0) (2026-11-24)


### Features

* add opencode install target ([#199](https://github.com/alexfayers/mcp-memory/issues/199)) ([6d9e691](https://github.com/alexfayers/mcp-memory/commit/6d9e691a0b347aae4666ad8f0403025b91566e48))
* **dream:** show the dream's progress live while waiting ([#204](https://github.com/alexfayers/mcp-memory/issues/204)) ([e28f2f9](https://github.com/alexfayers/mcp-memory/commit/e28f2f98387e908fcc7261de987a5770e309a9b6))


### Bug Fixes

* **dream:** match model ids by exact family and version (+1 more) ([#207](https://github.com/alexfayers/mcp-memory/issues/207)) ([5f36acc](https://github.com/alexfayers/mcp-memory/commit/5f36accfa8b6de4c1d0a45149ba39065b584a7a0))


### Miscellaneous Chores

* **deps:** update fayers-configs lock source ([#230](https://github.com/alexfayers/mcp-memory/issues/230)) ([b7fef37](https://github.com/alexfayers/mcp-memory/commit/b7fef3737f8730f7adebc5f5c95a70221e1c36ef))
* **deps:** update github actions ([#227](https://github.com/alexfayers/mcp-memory/issues/227)) ([e38ea13](https://github.com/alexfayers/mcp-memory/commit/e38ea133cfdcf86771bbd9309791024b704d8681))

## [0.3.2](https://github.com/alexfayers/mcp-memory/compare/v0.3.1...v0.3.2) (2026-11-23)


### Miscellaneous Chores

* **deps:** lock file maintenance ([#224](https://github.com/alexfayers/mcp-memory/issues/224)) ([35f05b5](https://github.com/alexfayers/mcp-memory/commit/35f05b513ca7ecff95123edbda44161a1a1b48d5))

## [0.3.1](https://github.com/alexfayers/mcp-memory/compare/v0.3.0...v0.3.1) (2026-11-22)


### Miscellaneous Chores

* **deps:** update github actions ([#221](https://github.com/alexfayers/mcp-memory/issues/221)) ([7f2b1aa](https://github.com/alexfayers/mcp-memory/commit/7f2b1aab2958b7b93efbac211f1ebc770c61b277))

## [0.3.0](https://github.com/alexfayers/mcp-memory/compare/v0.2.0...v0.3.0) (2026-11-21)


### Features

* **hooks:** skip the memory-review nudge when the server runs heavy dreaming ([#205](https://github.com/alexfayers/mcp-memory/issues/205)) ([18f61cd](https://github.com/alexfayers/mcp-memory/commit/18f61cd31e1233052f9c3c3736c69ece9858a75a))


### Bug Fixes

* **deps:** lock llm-prompts with the release tag in versions ([#217](https://github.com/alexfayers/mcp-memory/issues/217)) ([3965e0e](https://github.com/alexfayers/mcp-memory/commit/3965e0e76259541b0d506356c9d735ad0260b63a))


### Miscellaneous Chores

* sync shared files from llm-prompts-ci ([#219](https://github.com/alexfayers/mcp-memory/issues/219)) ([9202f89](https://github.com/alexfayers/mcp-memory/commit/9202f89ef376d56cfa37de33d2e8e7a8ef4d3b86))

## [0.2.0](https://github.com/alexfayers/mcp-memory/compare/v0.1.3...v0.2.0) (2026-11-20)


### Features

* **hooks:** require a task at stop only after wide work ([#213](https://github.com/alexfayers/mcp-memory/issues/213)) ([b1e4e91](https://github.com/alexfayers/mcp-memory/commit/b1e4e91915f692de418841889de5e08ca48dc048))

## [0.1.3](https://github.com/alexfayers/mcp-memory/compare/v0.1.2...v0.1.3) (2026-11-19)


### Bug Fixes

* **dream:** fall back to claude when pi cannot find the model (+1 more) ([#203](https://github.com/alexfayers/mcp-memory/issues/203)) ([f1a6cd8](https://github.com/alexfayers/mcp-memory/commit/f1a6cd8c1df7665eaaca6797e6b103980929dd43))


### Miscellaneous Chores

* sync shared files from llm-prompts-ci ([#201](https://github.com/alexfayers/mcp-memory/issues/201)) ([abaf602](https://github.com/alexfayers/mcp-memory/commit/abaf60258f81eb5d5ff7447da1ec5bc14877789a))

## [0.1.2](https://github.com/alexfayers/mcp-memory/compare/v0.1.1...v0.1.2) (2026-11-17)


### Miscellaneous Chores

* sync shared files from llm-prompts-ci ([#196](https://github.com/alexfayers/mcp-memory/issues/196)) ([b87e61c](https://github.com/alexfayers/mcp-memory/commit/b87e61c7382bb2a575f26c6d0901e634928cab46))

## [0.1.1](https://github.com/alexfayers/mcp-memory/compare/v0.1.0...v0.1.1) (2026-11-16)


### Miscellaneous Chores

* **deps:** lock file maintenance ([#194](https://github.com/alexfayers/mcp-memory/issues/194)) ([114db67](https://github.com/alexfayers/mcp-memory/commit/114db6729f695e688b1b514a3748a8137e59c89d))
* sync Copilot review rules from llm-prompts-ci ([#190](https://github.com/alexfayers/mcp-memory/issues/190)) ([b1deb37](https://github.com/alexfayers/mcp-memory/commit/b1deb377fb2b07179b85ad80f472389b6252d92b))
* sync shared files from llm-prompts-ci ([#191](https://github.com/alexfayers/mcp-memory/issues/191)) ([85bfea9](https://github.com/alexfayers/mcp-memory/commit/85bfea9a9caea87904324d5ed99b3ae4edc070cb))
* sync shared files from llm-prompts-ci ([#193](https://github.com/alexfayers/mcp-memory/issues/193)) ([c09119e](https://github.com/alexfayers/mcp-memory/commit/c09119e0211a65cd1fc1e6317875abb237c8a31c))
