.PHONY: monarch-login test contract up down logs

monarch-login:
	uv run monarch-login

test:
	uv run pytest

contract:
	./scripts/contract.sh

up:
	mkdir -p data/signal data/gateway data/monarch data/logs data/agents/pi data/agents/deep
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100
