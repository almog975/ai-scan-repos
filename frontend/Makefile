.PHONY: test
test:
	@if [ -d node_modules ]; then npx vitest run --passWithNoTests; else echo "[frontend] node_modules missing (run npm ci) - skipping"; fi
