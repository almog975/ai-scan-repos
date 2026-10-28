.PHONY: test
# Passes with no tests (pytest exit 5) or when pytest is not installed.
test:
	@if python3 -c "import pytest" 2>/dev/null; then python3 -m pytest -q || [ $$? -eq 5 ]; else echo "[gateway] pytest not installed - skipping"; fi
