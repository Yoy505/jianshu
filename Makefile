.PHONY: up down backend-setup backend-run worker beat seed test test-integration admin-dev h5-dev venv-check

# 后端解释器约定：默认使用官方最新稳定版 Python（backend/.python-version 声明确切版本，
# 当前 3.15.1，解释器安装于 ~/.local/python3.15.1）。旧 backend/.venv(3.12.8) 仅作回退对照。
# 覆盖方式：make backend-run PYTHON=$(which python3) VENV=backend/.venv
# VENV 以仓库根目录为基准（相对或绝对均可）；各目标 cd backend 后统一使用绝对路径 VENV_BIN，
# 避免 "cd backend && backend/.venv-py315/bin/..." 这类路径重复（F07）。
PYTHON       ?= $(HOME)/.local/python3.15.1/bin/python3.15
VENV         ?= backend/.venv-py315
VENV_BIN     := $(abspath $(VENV))/bin

up:            ## 启动本地 PostgreSQL + Redis（docker）
	docker compose up -d

down:          ## 停止并移除容器（数据保留）
	docker compose down

venv-check:    ## 校验 venv 解释器版本与 backend/.python-version 一致
	@V=$$($(VENV_BIN)/python --version 2>&1 | cut -d' ' -f2); W=$$(cat backend/.python-version); \
	if [ "$$V" != "$$W" ]; then echo "venv($$V) 与 .python-version($$W) 不一致"; exit 1; fi; \
	echo "venv OK: $$V"; $(VENV_BIN)/pip check

backend-setup: ## 用 PYTHON 指定解释器创建 venv 并按锁文件安装依赖
	$(PYTHON) --version && $(PYTHON) -m venv $(abspath $(VENV)) && \
	cd backend && $(VENV_BIN)/pip install -q -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -r requirements-test.lock

backend-run:   ## 启动 FastAPI（默认 8000）
	cd backend && $(VENV_BIN)/uvicorn app.main:app --reload --port 8000

worker:        ## 启动 Celery worker
	cd backend && $(VENV_BIN)/celery -A app.workers.celery_app worker -l info

beat:          ## 启动 Celery beat
	cd backend && $(VENV_BIN)/celery -A app.workers.celery_app beat -l info

seed:          ## 迁移到最新版并初始化管理员与演示数据（仅开发库；生产不运行）
	cd backend && $(VENV_BIN)/alembic upgrade head && $(VENV_BIN)/python -m app.seed

test:          ## 后端单元/契约回归（SQLite 夹具）
	cd backend && APP_ENV=test $(VENV_BIN)/python -m pytest tests -q

test-integration: ## 真实 PG/Redis/Celery 集成测试（需先注入隔离实例环境变量，见 docs/deploy.md）
	cd backend && JIANSHU_PG_TEST_URL=$${JIANSHU_PG_TEST_URL:?需设置本机隔离PG库(库名含test)} \
	  JIANSHU_REDIS_TEST_URL=$${JIANSHU_REDIS_TEST_URL:?需设置本机隔离Redis(db>=10)} \
	  $(VENV_BIN)/python -m pytest tests/integration -v

admin-dev:     ## 管理后台开发服务（5173）
	cd admin-web && npm i && npm run dev

h5-dev:        ## 征文 H5 开发服务（5174）
	cd essay-h5 && npm i && npm run dev
