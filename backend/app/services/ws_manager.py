"""WebSocket 连接管理：user_id → 多连接（多端同时在线）。

进程内注册表（单实例部署够用）；未来多实例时在此层替换为 Redis pub/sub 广播即可，
端点协议不变。
"""

import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("app.ws")


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = {}

    def register(self, user_id: int, websocket: WebSocket) -> None:
        self._connections.setdefault(user_id, set()).add(websocket)
        logger.info("ws connected user=%s (%d online)", user_id, self.online_count())

    def unregister(self, user_id: int, websocket: WebSocket) -> None:
        conns = self._connections.get(user_id)
        if conns is None:
            return
        conns.discard(websocket)
        if not conns:
            self._connections.pop(user_id, None)
        logger.info("ws disconnected user=%s (%d online)", user_id, self.online_count())

    async def disconnect_user(self, user_id: int, code: int = 4401) -> None:
        """封禁后关闭该进程内现有连接；多进程时需改用共享通知。"""
        conns = list(self._connections.pop(user_id, ()))
        for ws in conns:
            try:
                await ws.close(code=code)
            except Exception:
                pass

    async def send_to_user(self, user_id: int, payload: dict[str, Any]) -> None:
        """向该用户全部在线连接推送；单连接失败仅剔除，不影响其余。"""
        conns = list(self._connections.get(user_id, ()))
        for ws in conns:
            try:
                await ws.send_json(payload)
            except Exception:
                self.unregister(user_id, ws)

    def online_count(self) -> int:
        return sum(len(conns) for conns in self._connections.values())


manager = ConnectionManager()
