# Contributing

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

mcp-memory's dev dependency group references [`cline-hooks`](https://github.com/alexfayers/cline-hooks)
as an editable path dependency at `../cline-hooks`, so clone it as a sibling of this repo before
syncing:

```bash
git clone https://github.com/alexfayers/cline-hooks.git ../cline-hooks
uv sync
```

## Checks

```bash
just          # lint + type-check + naming-check + test (everything)
just lint         # ruff check --fix + ruff format
just type-check   # mypy (strict)
just naming-check # storage submodule naming convention check
just test         # pytest
```

Run `just` before opening a PR - all four must pass.

CI measures tool output size and ranking on the base and on the PR, posts the change as a PR comment, and fails on size growth unless the PR carries the `size-increase-accepted` label, or on a recall, success or MRR drop unless it carries the `ranking-drop-accepted` label. `just bench-diff [ref]` runs the same comparison locally (default `origin/main`); `just baseline` writes the current measurement to `tests/eval/baseline.json` (untracked). Queries labelled unreachable are reported apart and never gate. When a PR changes the benchmark, the comment compares the base and PR code on the old benchmark, which gates, and the old and new benchmarks under the PR code.

## Commit messages

Single-line, conventional-commit style: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`. No body.

## Pull requests

`main` is protected - pushes must go through a PR (squash or rebase merge, no merge commits).

**PR titles and descriptions:**
- Title in conventional-commit format (`type: subject`).
- Description as bullet points, not paragraphs.
- State WHAT changed and WHY, not HOW.
- No restating the diff, no process commentary, no filler.
- List a PR that must merge first as a `Depends on <PR URL>` line; the `check-dependencies` check fails until it merges and re-checks when that PR closes.
