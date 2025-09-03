# Professional Client Management Bot - Makefile
# Automation for development, testing, and deployment tasks

.PHONY: help install install-dev test test-unit test-integration test-e2e lint format type-check quality-check clean run dev docker-build docker-run logs deploy health-check backup

# Default target
help: ## Show this help message
        @echo "Client Management Bot - Development Commands"
        @echo "============================================="
        @awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# Installation
install: ## Install production dependencies
        @echo "📦 Installing production dependencies..."
        pip install -e .

install-dev: ## Install development dependencies
        @echo "🛠️ Installing development dependencies..."
        pip install -e ".[dev,test,monitoring]"
        pre-commit install

# Testing
test: ## Run all tests
        @echo "🧪 Running all tests..."
        pytest -v --cov=. --cov-report=html --cov-report=term-missing

test-unit: ## Run unit tests only
        @echo "🔬 Running unit tests..."
        pytest tests/test_core/ tests/test_utils/ -v -m "not integration and not e2e"

test-integration: ## Run integration tests
        @echo "🔗 Running integration tests..."
        pytest -v -m integration

test-e2e: ## Run end-to-end tests
        @echo "🎯 Running end-to-end tests..."
        pytest -v -m e2e

test-watch: ## Run tests in watch mode
        @echo "👀 Running tests in watch mode..."
        pytest-watch --poll

# Code Quality
lint: ## Run linting
        @echo "🔍 Running linting..."
        flake8 . --config=pyproject.toml
        mypy . --config-file=pyproject.toml

format: ## Format code
        @echo "✨ Formatting code..."
        black .
        isort .

type-check: ## Run type checking
        @echo "🔎 Running type checking..."
        mypy . --config-file=pyproject.toml

quality-check: format lint type-check test-unit ## Run all quality checks
        @echo "✅ All quality checks passed!"

# Development
run: ## Run the bot in production mode
        @echo "🚀 Starting bot in production mode..."
        python3 main.py

dev: ## Run the bot in development mode
        @echo "🔧 Starting bot in development mode..."
        ENVIRONMENT=development DEBUG=true python3 main.py

debug: ## Run with debug logging
        @echo "🐛 Starting bot with debug logging..."
        LOG_LEVEL=DEBUG python3 main.py

# Database
db-init: ## Initialize database
        @echo "🗄️ Initializing database..."
        python3 -c "from services.database_service import db_service; db_service.init_db()"

db-migrate: ## Run database migrations
        @echo "📊 Running database migrations..."
        python3 -c "from services.database_service import db_service; db_service.migrate()"

db-reset: ## Reset database (WARNING: This will delete all data!)
        @echo "⚠️ Resetting database..."
        @read -p "Are you sure? This will delete ALL data! Type 'yes' to continue: " confirm && [ "$$confirm" = "yes" ]
        python3 -c "from services.database_service import db_service; db_service.reset_db()"

# WhatsApp Service
whatsapp-start: ## Start WhatsApp service
        @echo "📱 Starting WhatsApp service..."
        node whatsapp_baileys_multi.js &

whatsapp-stop: ## Stop WhatsApp service
        @echo "📱 Stopping WhatsApp service..."
        pkill -f "whatsapp_baileys_multi.js" || true

whatsapp-restart: whatsapp-stop whatsapp-start ## Restart WhatsApp service

# Monitoring
health-check: ## Check system health
        @echo "🏥 Checking system health..."
        @python -c "
import requests
import sys
try:
    response = requests.get('http://localhost:3001/health', timeout=5)
    print(f'WhatsApp Service: {\"✅ Healthy\" if response.status_code == 200 else \"❌ Unhealthy\"}')
except Exception as e:
    print(f'WhatsApp Service: ❌ Unavailable - {e}')
    sys.exit(1)
"

logs: ## Show application logs
        @echo "📋 Showing logs..."
        tail -f app.log bot.log

logs-whatsapp: ## Show WhatsApp service logs
        @echo "📱 Showing WhatsApp logs..."
        pm2 logs whatsapp-service || echo "WhatsApp service not running with PM2"

metrics: ## Show metrics summary
        @echo "📊 System metrics..."
        @python -c "
from core.monitoring import monitoring
import json
status = monitoring.get_system_status()
print(json.dumps(status, indent=2, default=str))
"

# Docker
docker-build: ## Build Docker image
        @echo "🐳 Building Docker image..."
        docker build -t client-management-bot:latest .

docker-run: ## Run Docker container
        @echo "🐳 Running Docker container..."
        docker run -d --name client-bot \
                --env-file .env \
                -p 3001:3001 \
                client-management-bot:latest

docker-stop: ## Stop Docker container
        @echo "🐳 Stopping Docker container..."
        docker stop client-bot || true
        docker rm client-bot || true

docker-logs: ## Show Docker logs
        @echo "🐳 Docker logs..."
        docker logs -f client-bot

# Deployment
deploy-staging: ## Deploy to staging
        @echo "🚧 Deploying to staging..."
        # Add staging deployment commands here

deploy-production: ## Deploy to production
        @echo "🚀 Deploying to production..."
        @read -p "Deploy to PRODUCTION? Type 'yes' to continue: " confirm && [ "$$confirm" = "yes" ]
        # Add production deployment commands here

# Backup & Restore
backup: ## Create system backup
        @echo "💾 Creating backup..."
        @timestamp=$$(date +%Y%m%d_%H%M%S) && \
        mkdir -p backups/$$timestamp && \
        pg_dump $$DATABASE_URL > backups/$$timestamp/database.sql && \
        cp -r sessions/ backups/$$timestamp/ && \
        cp -r templates/ backups/$$timestamp/ && \
        echo "✅ Backup created: backups/$$timestamp"

restore: ## Restore from backup
        @echo "📂 Available backups:"
        @ls -la backups/ 2>/dev/null || echo "No backups found"
        @read -p "Enter backup timestamp to restore: " timestamp && \
        if [ -d "backups/$$timestamp" ]; then \
                echo "🔄 Restoring from backups/$$timestamp..."; \
                psql $$DATABASE_URL < backups/$$timestamp/database.sql; \
                cp -r backups/$$timestamp/sessions/ ./; \
                cp -r backups/$$timestamp/templates/ ./; \
                echo "✅ Restore completed"; \
        else \
                echo "❌ Backup not found"; \
        fi

# Maintenance
clean: ## Clean up temporary files
        @echo "🧹 Cleaning up..."
        find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
        find . -type f -name "*.pyc" -delete 2>/dev/null || true
        find . -type f -name "*.pyo" -delete 2>/dev/null || true
        find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
        rm -rf .pytest_cache/ .coverage htmlcov/ .mypy_cache/ dist/ build/ 2>/dev/null || true

clean-cache: ## Clear application caches
        @echo "🗑️ Clearing caches..."
        @python -c "
from core.cache import cache_manager
cache_manager.clear_all()
print('✅ All caches cleared')
"

update-deps: ## Update dependencies
        @echo "📦 Updating dependencies..."
        pip list --outdated --format=freeze | grep -v '^\-e' | cut -d = -f 1 | xargs -n1 pip install -U

# Security
security-check: ## Run security checks
        @echo "🔒 Running security checks..."
        pip-audit || echo "Install pip-audit: pip install pip-audit"
        bandit -r . -f json -o security-report.json || echo "Install bandit: pip install bandit"

# Performance
profile: ## Profile application performance
        @echo "⚡ Profiling application..."
        python -m cProfile -o profile.stats main.py

load-test: ## Run load tests
        @echo "📈 Running load tests..."
        # Add load testing commands here

# Documentation
docs-build: ## Build documentation
        @echo "📚 Building documentation..."
        @mkdir -p docs
        @echo "# Client Management Bot Documentation" > docs/README.md
        @echo "Documentation would be built here with Sphinx or similar"

# Environment
env-check: ## Check environment variables
        @echo "🔧 Checking environment..."
        @python -c "
from config.settings import settings, validate_settings
if validate_settings():
    print('✅ Configuration is valid')
else:
    print('❌ Configuration has errors')
    exit(1)
"

env-template: ## Create environment template
        @echo "📝 Creating .env template..."
        @cat > .env.template << 'EOF'
# Database Configuration
DATABASE_URL=postgresql://user:password@localhost:5432/dbname

# Telegram Bot
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here

# WhatsApp Service
WHATSAPP_SERVICE_URL=http://localhost:3001

# Payment Processing
MERCADO_PAGO_TOKEN=your_mercado_pago_token_here

# Application Settings
ENVIRONMENT=development
DEBUG=true
LOG_LEVEL=INFO

# Security
RATE_LIMIT_REQUESTS=100
RATE_LIMIT_WINDOW=3600

# Monitoring
METRICS_ENABLED=true
HEALTH_CHECK_INTERVAL=30
EOF
        @echo "✅ Environment template created: .env.template"

# Quick commands
start: whatsapp-start run ## Start all services
stop: whatsapp-stop ## Stop all services
restart: stop start ## Restart all services
status: health-check ## Check status
full-test: quality-check test ## Run complete test suite