@_default: lint type-check test

lint:
    uv run ruff check --fix src/ tests/
    uv run ruff format src/ tests/

type-check:
    uv run mypy src/

test *args:
    uv run pytest {{args}}

baseline *args:
    uv run python -m tests.regen_eval_baseline {{args}}
