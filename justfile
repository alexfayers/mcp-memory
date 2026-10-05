@_default: lint type-check naming-check test

lint:
    uv run ruff check --fix src/ tests/
    uv run ruff format src/ tests/

lint-check:
    uv run ruff check src/ tests/

type-check:
    uv run mypy src/

naming-check:
    uv run python tests/naming_check.py src/mcp_memory/storage

naming-check-final:
    uv run python tests/naming_check.py src/mcp_memory/storage --final

test *args:
    uv run pytest {{args}}

baseline:
    uv run python -m tests.eval.regen_baseline

baseline-compare base head *args:
    uv run python -m tests.eval.compare_baseline --base {{base}} --head {{head}} {{args}}

bench-diff ref="origin/main" *args:
    #!/usr/bin/env bash
    base_dir="../$(basename "$PWD")-bench-base"
    remove_artefacts() {
        for artefact in .bench-*.json; do
            if [ -e "$artefact" ]; then rm "$artefact"; fi
        done
    }
    remove_artefacts
    git worktree add --detach "$base_dir" {{ref}} >&2 || exit 1
    cleanup() {
        git worktree remove --force "$base_dir"
        remove_artefacts
    }
    trap cleanup EXIT
    if ! (cd "$base_dir" && uv sync --locked >&2 && just baseline >&2 && cp tests/eval/baseline.json "$OLDPWD/.bench-base.json"); then
        echo "No base measurement: {{ref}} could not be measured with its own code."
        exit 0
    fi
    cross_args=()
    if ! git diff --quiet {{ref}} -- tests/eval ':!tests/eval/test_*' ':!tests/eval/compare_baseline.py' ':!tests/eval/badge_endpoints.py'; then
        (cd "$base_dir" && uv run --project "$OLDPWD" python -m tests.eval.regen_baseline >&2 && cp tests/eval/baseline.json "$OLDPWD/.bench-head-on-base-data.json")
        cross_args=(--head-on-base-data .bench-head-on-base-data.json)
    fi
    just baseline >&2 || exit 2
    just baseline-compare .bench-base.json tests/eval/baseline.json "${cross_args[@]}" {{args}}
