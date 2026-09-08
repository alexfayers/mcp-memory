@_default: lint type-check naming-check test

lint:
    uv run ruff check --fix src/ tests/
    uv run ruff format src/ tests/

type-check:
    uv run mypy src/

naming-check:
    uv run python tests/naming_check.py src/mcp_memory/storage

naming-check-final:
    uv run python tests/naming_check.py src/mcp_memory/storage --final

test *args:
    uv run pytest {{args}}

baseline *args:
    uv run python -m tests.eval.regen_eval_baseline {{args}}
