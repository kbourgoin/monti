.PHONY: monarch-login test up down logs

monarch-login:
	uv run monarch-login

test:
	uv run pytest

up:
	mkdir -p data/signal data/monarch data/logs data/agents/pi
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100
