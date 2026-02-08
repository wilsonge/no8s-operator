.PHONY: help install dev-setup start stop restart logs clean test lint format db-init db-migrate db-reset s3-setup

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install Python dependencies
	pip install .

dev-setup: ## Set up development environment
	@echo "Setting up development environment..."
	pip install -e ".[dev]"
	chmod +x src/tfctl.py
	@echo "Development environment ready!"

lint: ## Run flake8 linting
	flake8

format: ## Format code with black
	black .

format-check: ## Check code formatting without making changes
	black --check .

start: ## Start all services with docker-compose
	docker-compose up -d
	@echo "Services started. API available at http://localhost:8000"
	@echo "View logs with: make logs"

stop: ## Stop all services
	docker-compose down

restart: ## Restart all services
	docker-compose restart

logs: ## Follow logs from all services
	docker-compose logs -f

logs-api: ## Follow API logs only
	docker-compose logs -f controller-api

logs-db: ## Follow database logs only
	docker-compose logs -f postgres

clean: ## Clean up containers and volumes
	docker-compose down -v
	rm -rf /tmp/terraform-workspaces/*

test: ## Run tests (TODO: implement tests)
	@echo "Tests not yet implemented"
	# pytest tests/

db-init: ## Initialize database schema
	@echo "Initializing database schema..."
	python -c "import asyncio; from db import DatabaseManager; \
		async def init(): \
			db = DatabaseManager('localhost', 5432, 'terraform_controller', 'terraform', 'terraform'); \
			await db.connect(); \
			await db.initialize_schema(); \
			await db.close(); \
		asyncio.run(init())"
	@echo "Database initialized!"

db-migrate: ## Run pending database migrations
	@echo "Running database migrations..."
	python -c "import asyncio; from db import DatabaseManager; \
		async def migrate(): \
			db = DatabaseManager('localhost', 5432, 'terraform_controller', 'terraform', 'terraform'); \
			await db.connect(); \
			await db.initialize_schema(); \
			await db.close(); \
		asyncio.run(migrate())"
	@echo "Migrations complete!"

db-reset: ## Reset database (WARNING: deletes all data)
	@echo "WARNING: This will delete all data!"
	@read -p "Are you sure? [y/N] " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		docker-compose exec postgres psql -U terraform -d terraform_controller -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"; \
		make db-init; \
	fi

s3-setup: ## Set up S3 bucket for state storage (LocalStack)
	@echo "Creating S3 bucket in LocalStack..."
	aws --endpoint-url=http://localhost:4566 s3 mb s3://terraform-state-bucket || true
	@echo "S3 bucket created!"

# Development shortcuts
run-api: ## Run API server locally (not in Docker)
	python src/main.py

run-controller: ## Run controller locally (not in Docker)
	python src/controller.py

# CLI shortcuts
cli-apply: ## Apply a resource (usage: make cli-apply FILE=resource.yaml)
	./src/tfctl.py apply $(FILE)

cli-list: ## List all resources
	./src/tfctl.py get

cli-status: ## Show status of a resource (usage: make cli-status ID=1)
	./src/tfctl.py status $(ID)

cli-history: ## Show reconciliation history (usage: make cli-history ID=1)
	./src/tfctl.py history $(ID)

cli-outputs: ## Show outputs (usage: make cli-outputs ID=1)
	./src/tfctl.py outputs $(ID)

cli-delete: ## Delete a resource (usage: make cli-delete ID=1)
	./src/tfctl.py delete $(ID)

# Build
build: ## Build Docker image
	docker-compose build

# Docker management
ps: ## Show running containers
	docker-compose ps

shell-api: ## Open shell in API container
	docker-compose exec controller-api /bin/bash

shell-db: ## Open psql shell in database
	docker-compose exec postgres psql -U terraform -d terraform_controller
