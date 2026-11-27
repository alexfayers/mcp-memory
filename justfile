sibling_llm_prompts := `d="$(dirname "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)")")/llm-prompts"; [ -d "$d" ] && printf %s "$d"; true`
uv_run := if sibling_llm_prompts == "" { "uv run" } else { "uv run --with-editable " + quote(sibling_llm_prompts) }

@_default: lint type-check naming-check test js-test

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

js-test:
    node --test tests/js/*.test.mjs

baseline:
    {{uv_run}} python -m tests.eval.regen_baseline

baseline-compare base head *args:
    {{uv_run}} python -m tests.eval.compare_baseline --base {{base}} --head {{head}} {{args}}

upgrade_fixture := "tests/fixtures/upgrade-seed.json"
upgrade_project := "upgrade-smoke"

upgrade-seed:
    #!/usr/bin/env bash
    command -v mcp-memory > /dev/null || { echo "upgrade-seed: no mcp-memory on PATH" >&2; exit 1; }
    mcp-memory import {{upgrade_fixture}} --project {{upgrade_project}} || { echo "upgrade-seed: import into the server on port ${MCP_MEMORY_PORT:-default} failed" >&2; exit 1; }

upgrade-smoke:
    #!/usr/bin/env bash
    command -v mcp-memory > /dev/null || { echo "upgrade-smoke: no mcp-memory on PATH" >&2; exit 1; }
    export_file="$(mktemp)"
    trap 'rm "$export_file"' EXIT
    mcp-memory export "$export_file" > /dev/null || { echo "upgrade-smoke: mcp-memory export failed" >&2; exit 1; }
    facts='.projects["{{upgrade_project}}"] | [(.entities[]? | "entity \(.name) (\(.entity_type))", (.name as $name | .observations[] | "observation on \($name): \(.content)")), (.relations[]? | "relation \(.source) -\(.relation_type)-> \(.target)")]'
    missing="$(jq -nr --slurpfile seed {{upgrade_fixture}} --slurpfile export "$export_file" "(\$seed[0] | $facts) - (\$export[0] | $facts) | .[]")" || { echo "upgrade-smoke: could not read the export" >&2; exit 1; }
    if [ -n "$missing" ]; then
        printf 'upgrade-smoke: the export lost seeded data:\n%s\n' "$missing" >&2
        exit 1
    fi
    expected="$({{uv_run}} python -c 'from mcp_memory.migrations.schema import MIGRATIONS; print(max(m.version for m in MIGRATIONS))')" || { echo "upgrade-smoke: could not read the highest migration from the checkout" >&2; exit 1; }
    actual="$(jq -r .schema_version "$export_file")"
    if [ "$actual" != "$expected" ]; then
        echo "upgrade-smoke: schema_version is $actual, expected the highest migration $expected" >&2
        exit 1
    fi
    audit="$(mcp-memory utils audit --project {{upgrade_project}})" || { echo "upgrade-smoke: mcp-memory utils audit failed" >&2; exit 1; }
    findings="$(jq -r 'to_entries[] | select(.value | type == "array" and length > 0) | "\(.key): \(.value | tojson)"' <<< "$audit")" || { echo "upgrade-smoke: could not read the audit report" >&2; exit 1; }
    if [ -n "$findings" ]; then
        printf 'upgrade-smoke: the audit found issues in the seeded data:\n%s\n' "$findings" >&2
        exit 1
    fi
    echo "upgrade-smoke: seeded data, schema_version $actual and audit all pass"

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
