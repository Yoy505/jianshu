.PHONY: up down backend-setup backend-run worker beat seed admin-dev h5-dev

up:            ## 启动本地 PostgreSQL + Redis
	docker compose up -d

down:          ## 停止并移除容器（数据保留）
	docker compose down

backend-setup: ## 创建 venv 并安装依赖
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

backend-run:   ## 启动 FastAPI（默认 8000）
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

worker:        ## 启动 Celery worker
	cd backend && .venv/bin/celery -A app.workers.celery_app worker -l info

beat:          ## 启动 Celery beat
	cd backend && .venv/bin/celery -A app.workers.celery_app beat -l info

seed:          ## 迁移到最新版并初始化管理员与演示数据
	cd backend && .venv/bin/alembic upgrade head && .venv/bin/python -m app.seed

admin-dev:     ## 管理后台开发服务（5173）
	cd admin-web && npm i && npm run dev

h5-dev:        ## 征文 H5 开发服务（5174）
	cd essay-h5 && npm i && npm run dev
