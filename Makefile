# Atalhos de desenvolvimento.
# No Windows sem `make`, rode os comandos da coluna da direita direto no terminal.

.PHONY: help install lint format types test cov check run serve scheduler docker clean

help:  ## Mostra esta ajuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Cria o ambiente e instala tudo
	python -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"
	.venv/bin/pre-commit install

lint:  ## Roda o linter
	ruff check src tests

format:  ## Formata o código
	ruff format src tests
	ruff check src tests --fix

types:  ## Checagem estática de tipos
	mypy

test:  ## Roda os testes
	pytest

cov:  ## Testes com relatório de cobertura
	pytest --cov --cov-report=term-missing --cov-report=html

check: lint types test  ## Tudo que o CI roda

run:  ## Executa o workflow de exemplo
	fluxor run examples/hello-mundo.yaml

serve:  ## Sobe a API + dashboard em modo desenvolvimento
	fluxor serve --reload

scheduler:  ## Roda apenas o agendador
	fluxor scheduler

docker:  ## Constrói e sobe via docker compose
	docker compose up --build -d

clean:  ## Remove artefatos gerados
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
