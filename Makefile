.PHONY: monarch-login test

monarch-login:
	uv run monarch-login

test:
	uv run pytest
