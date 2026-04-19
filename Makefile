.PHONY: check check-all

check:
	uv sync --group dev
	uv run pylint src
	uv run pyright src
	uv run pytest
