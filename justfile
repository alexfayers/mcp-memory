sibling_llm_prompts := `d="$(dirname "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)")")/llm-prompts"; [ -d "$d" ] && printf %s "$d"; true`
uv_run := if sibling_llm_prompts == "" { "uv run" } else { "uv run --with-editable " + quote(sibling_llm_prompts) }

@_default: lint type-check naming-check test

lint:
    {{uv_run}} ruff check --fix src/ tests/
    {{uv_run}} ruff format src/ tests/

lint-check:
    {{uv_run}} ruff check src/ tests/

type-check:
    {{uv_run}} mypy src/

naming-check:
    {{uv_run}} python tests/naming_check.py src/mcp_memory/storage

naming-check-final:
    {{uv_run}} python tests/naming_check.py src/mcp_memory/storage --final

test *args:
    {{uv_run}} pytest {{args}}

baseline:
    {{uv_run}} python -m tests.eval.regen_baseline

baseline-compare base head *args:
    {{uv_run}} python -m tests.eval.compare_baseline --base {{base}} --head {{head}} {{args}}

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
