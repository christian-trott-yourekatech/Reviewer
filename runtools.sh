

echo "Running formatter..."
uv run ruff format src tests

echo "Running linter..."
uv run ruff check --output-format full src tests

echo "Running type checker..."
uv run pyrefly check --output-format full-text src tests

echo "Running unit tests..."
uv run pytest --tb=short