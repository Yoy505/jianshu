.PHONY: up down backend-setup backend-run worker beat seed admin-dev h5-dev venv-check

# 后端解释器约定：默认使用官方最新稳定版 Python（backend/.python-version 声明确切版本，
# 当前 3.15.1，解释器安装于 ~/.local/python3.15.1）。旧 backend/.venv(3.12.8) 仅作回退对照。
# 覆盖方式：make backend-run PYTHON=$(which python3) VENV=backend/.venv
PYTHON       ?= $(HOME)/.local/python3.15.1/bin/python3.15
VENV         ?= backend/.venv-py315

up:            ## 启动本地 PostgreSQL + Redis（docker）
	docker compose up -d

down:          ## 停止并移除容器（数据保留）
	docker compose down

venv-check:    ## 校验 venv 解释器版本与 backend/.python-version 一致
	@V=$$($(VENV)/bin/python --version 2>&1 | cut -d' ' -f2); W=$$(cat backend/.python-version); \
	if [ "$$V" != "$$W" ]; then echo "venv($$V) 与 .python-version($$W) 不一致"; exit 1; fi; \
	echo "venv OK: $$V"; $(VENV)/bin/pip check

backend-setup: ## 用 PYTHON 指定解释器创建 venv 并按锁文件安装依赖
	$(PYTHON) --version && cd backend && $(PYTHON) -m venv $$(basename $(VENV)) && \
	$$(basename $(VENV))/bin/pip install -q -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -r requirements-test.lock

backend-run:   ## 启动 FastAPI（默认 8000）
	cd backend && $(VENV)/bin/uvicorn app.main:app --reload --port 8000

worker:        ## 启动 Celery worker
	cd backend && $(VENV)/bin/celery -A app.workers.celery_app worker -l info

beat:          ## 启动 Celery beat
	cd backend && $(VENV)/bin/celery -A app.workers.celery_app beat -l info

seed:          ## 迁移到最新版并初始化管理员与演示数据
	cd backend && $(VENV)/bin/alembic upgrade head && $(VENV)/bin/python -m app.seed

test-integration: ## 真实 PG/Redis/Celery 集成测试（需先注入隔离实例环境变量，见 docs/deploy.md）
	cd backend && JIANSHU_PG_TEST_URL=$${JIANSHU_PG_TEST_URL:?需设置本机隔离PG库(库名含test)} \
	  JIANSHU_REDIS_TEST_URL=$${JIANSHU_REDIS_TEST_URL:?需设置本机隔离Redis(db>=10)} \
	  $$(basename $(VENV))/bin/python -m pytest tests/integration -v

admin-dev:     ## 管理后台开发服务（5173）
	cd admin-web && npm i && npm run dev

h5-dev:        ## 征文 H5 开发服务（5174）
	cd essay-h5 && npm i && npm run dev
