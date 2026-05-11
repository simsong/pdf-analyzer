# Copyright (C) 2026 Sabinok Corporation
# SPDX-License-Identifier: GPL-3.0-or-later

.PHONY: check check-all check-system-deps

check: check-system-deps
	uv sync --group dev
	uv run pylint src
	uv run pyright src
	uv run pytest

check-system-deps:
	@command -v gs >/dev/null || { echo "ERROR: Ghostscript (gs) is required but was not found in PATH." >&2; exit 1; }
	@command -v verapdf >/dev/null || { echo "ERROR: veraPDF (verapdf) is required but was not found in PATH." >&2; exit 1; }
	@printf 'Ghostscript '
	@gs --version
	@verapdf --version | head -1
