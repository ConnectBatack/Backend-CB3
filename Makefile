SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn
ALEMBIC := $(VENV)/bin/alembic
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff

API_HOST ?= 0.0.0.0
API_PORT ?= 8001
BASE_URL ?= http://localhost:$(API_PORT)

.PHONY: help venv install db-up db-down migrate seed run dev start lint test test-endpoints docker-up docker-down clean

help: ## Afficher l'aide des commandes disponibles
	@awk 'BEGIN {FS = ":.*##"; printf "\n\033[1mConnectBatack 3 - Backend Makefile\033[0m\n\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)

venv: ## Créer l'environnement virtuel Python (.venv)
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

install: venv ## Installer les dépendances Python dans .venv
	$(PIP) install -r requirements.txt

db-up: ## Démarrer PostgreSQL et Redis via Docker Compose
	docker compose up -d db redis --wait

db-down: ## Arrêter PostgreSQL et Redis
	docker compose stop db redis

migrate: ## Appliquer les migrations Alembic
	$(ALEMBIC) upgrade head

seed: ## Initialiser les données (utilisateurs, photos locales, projets, tâches, registre)
	$(PYTHON) -m scripts.seed_db

run: ## Lancer le serveur FastAPI (uvicorn --reload)
	$(UVICORN) app.main:app --reload --host $(API_HOST) --port $(API_PORT)

dev: run ## Alias de run

start: db-up migrate seed run ## Démarrer DB/Redis, migrer, seeder et lancer l'API

lint: ## Vérifier le code avec Ruff
	$(RUFF) check app tests alembic scripts

test: ## Exécuter la suite de tests pytest
	$(PYTEST) -v

test-endpoints: ## Tester tous les endpoints sur le serveur actif ($(BASE_URL))
	BASE_URL=$(BASE_URL) $(PYTHON) -m scripts.verify_endpoints

docker-up: ## Construire et démarrer toute la stack Docker (mode classique)
	DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose up --build -d

docker-down: ## Arrêter les conteneurs du projet ConnectBatack 3
	docker compose down

clean: ## Nettoyer les caches Python/pytest/ruff
	rm -rf .pytest_cache .ruff_cache __pycache__
	find app tests alembic scripts -type d -name "__pycache__" -exec rm -rf {} +

