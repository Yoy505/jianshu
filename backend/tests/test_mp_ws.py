"""WebSocket 实时通道：鉴权、回显 + 自动应答推送、业务错误帧（契约五之二）。

TestClient 为同步驱动（自带事件循环），因此用临时文件库而非内存库，
避免 aiosqlite 连接跨事件循环复用的问题。
"""

import asyncio

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.deps import get_db, get_redis
from app.core.config import settings
from app.core.security import SCOPE_USER, create_token
from app.db.base import Base
from app.main import app
from tests.conftest import FakeRedis, _seed_test_data


@pytest.fixture()
def ws_client(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/ws-test.db"

    async def prepare():
        engine = create_async_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with session_factory() as session:
            await _seed_test_data(session)
        await engine.dispose()
        return session_factory

    # 预建库与种子（独立事件循环；文件库无跨循环复用问题）
    session_factory = asyncio.new_event_loop().run_until_complete(prepare())

    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    redis_stub = FakeRedis()

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = lambda: redis_stub

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()
    asyncio.new_event_loop().run_until_complete(engine.dispose())


def test_ws_auth_and_message_flow(ws_client):
    token = create_token(1, SCOPE_USER, "access")

    # ---- 无效 token：连接被关闭 ----
    with pytest.raises(Exception):
        with ws_client.websocket_connect("/api/v1/mp/ws?token=bad-token") as bad:
            bad.receive_json()

    # ---- 合法连接：connected 帧 ----
    with ws_client.websocket_connect(f"/api/v1/mp/ws?token={token}") as ws:
        assert ws.receive_json() == {"type": "connected", "user_id": 1}

        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

        # seed 的 open 会话（user 1）
        resp = ws_client.get("/api/v1/mp/consults", headers={"Authorization": f"Bearer {token}"})
        consult_id = resp.json()["items"][0]["id"]

        # 发消息：先回显 user 帧，再推 system 自动回执帧
        ws.send_json({"type": "message", "consult_id": consult_id, "content": "WS 测试消息"})
        echo = ws.receive_json()
        assert echo["type"] == "message"
        assert echo["consult_id"] == consult_id
        assert echo["message"]["sender"] == "user"
        assert echo["message"]["content"] == "WS 测试消息"

        reply = ws.receive_json()
        assert reply["type"] == "message"
        assert reply["message"]["sender"] == "system"
        assert "尚未接入真人专业人员" in reply["message"]["content"]
        assert "个体用药建议" in reply["message"]["content"]

        # 空内容：error 帧，连接保持
        ws.send_json({"type": "message", "consult_id": consult_id, "content": "  "})
        assert ws.receive_json() == {"type": "error", "detail": "消息内容不能为空"}

        # error 之后仍可正常收发
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

        # 未知类型
        ws.send_json({"type": "whatever"})
        assert ws.receive_json() == {"type": "error", "detail": "未知的消息类型"}

        ws.send_json([])
        assert ws.receive_json() == {"type": "error", "detail": "消息帧格式不正确"}

        # ---- 落库核验：REST 拉增量能看到 WS 发的消息（两通道同库）----
        resp = ws_client.get(
            f"/api/v1/mp/consults/{consult_id}/messages?after_id=0",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert any(m["content"] == "WS 测试消息" for m in resp.json())


def test_ws_missing_consult_error(ws_client):
    token = create_token(1, SCOPE_USER, "access")
    with ws_client.websocket_connect(f"/api/v1/mp/ws?token={token}") as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"type": "message", "consult_id": 99999, "content": "你好"})
        assert ws.receive_json() == {"type": "error", "detail": "会话不存在"}


def test_ws_staging_rejects_message_without_writing(ws_client, monkeypatch):
    token = create_token(1, SCOPE_USER, "access")
    headers = {"Authorization": f"Bearer {token}"}
    consult_id = ws_client.get("/api/v1/mp/consults", headers=headers).json()["items"][0]["id"]
    url = f"/api/v1/mp/consults/{consult_id}/messages"
    before = ws_client.get(url, headers=headers).json()

    monkeypatch.setattr(settings, "APP_ENV", "staging")
    with ws_client.websocket_connect(f"/api/v1/mp/ws?token={token}") as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"type": "message", "consult_id": consult_id, "content": "不得保存的健康信息"})
        error = ws.receive_json()
        assert error["type"] == "error"
        assert "暂未开放" in error["detail"]
    assert ws_client.get(url, headers=headers).json() == before


def test_ws_ban_closes_existing_connection(ws_client):
    token = create_token(1, SCOPE_USER, "access")
    admin_login = ws_client.post(
        "/api/v1/admin/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert admin_login.status_code == 200
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}

    with ws_client.websocket_connect(f"/api/v1/mp/ws?token={token}") as ws:
        assert ws.receive_json()["type"] == "connected"
        banned = ws_client.post("/api/v1/admin/users/1/ban", headers=admin_headers)
        assert banned.status_code == 200
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4401
