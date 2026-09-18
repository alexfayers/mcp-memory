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

## Commit messages

Single-line, conventional-commit style: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`. No body.

## Pull requests

`main` is protected - pushes must go through a PR (squash or rebase merge, no merge commits).

**PR titles and descriptions:**
- Title in conventional-commit format (`type: subject`).
- Description as bullet points, not paragraphs.
- State WHAT changed and WHY, not HOW.
- No restating the diff, no process commentary, no filler.
