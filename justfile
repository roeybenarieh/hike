default:
    @just --list

# Install all dependencies (including optional extras)
sync:
    uv sync --all-extras

# Type-check the source
typecheck:
    uv run pyright src/

# Type-check a specific file
typecheck-file file:
    uv run pyright {{ file }}

# Run all tests
test:
    uv run pytest tests/

# Run only fast (in-memory, no containers) tests
test-unit:
    uv run pytest tests/hike/ddd/providers/in_memory/ tests/hike/ddd/test_entity.py tests/hike/ddd/test_value_object.py tests/hike/ddd/test_aggregate.py tests/hike/ddd/specifications/

# Run integration tests for a specific provider (pymongo | redis | sqlalchemy)
test-provider provider:
    uv run pytest tests/hike/ddd/providers/{{ provider }}/

# Run all integration tests (requires Docker)
test-integration:
    uv run pytest tests/hike/ddd/providers/pymongo/ tests/hike/ddd/providers/redis/ tests/hike/ddd/providers/sqlalchemy/

# Build the package
build:
    uv build

# Run lint + typecheck (no tests)
check: typecheck
    @echo "All checks passed."

# Run all checks and tests
ci: check test

# run mkdocs
docs:
  uv run mkdocs serve


# get a nix shell with all dependencies
dependencies:
  nix develop

# Remove build artifacts
clean:
    rm -rf dist/ .pytest_cache/ __pycache__
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
