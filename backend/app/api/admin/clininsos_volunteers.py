"""医师志愿者（ClinInsOS 志愿者备案库）代理路由。

数据与审核动作实时转发 ClinInsOS 后台 API（医师资质报名：初审→终审）。
鉴权双保险：本后台管理员登录态 + 请求头 X-ClinInsOS-Token（前端页面
首次使用时用 ClinInsOS 管理员账号换取，本服务不落盘保存凭证）。
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request

from app.core.deps import get_current_admin

router = APIRouter(prefix="/admin/clininsos-volunteers", tags=["clininsos-volunteers"])

CLININSOS_BASE = "https://clininsos.smartstudio.cc/api/v1"


async def _forward(
    token: str,
    path: str,
    method: str = "GET",
    params: dict | None = None,
    json_body: dict | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.request(
            method,
            f"{CLININSOS_BASE}{path}",
            params=params,
            json=json_body,
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code == 401:
        raise HTTPException(401, "志愿者备案库登录已过期，请重新连接")
    return resp


def _require_token(token: str) -> str:
    if not token:
        raise HTTPException(401, "尚未连接志愿者备案库，请先输入 ClinInsOS 后台账号")
    return token


@router.post("/login")
async def clininsos_login(
    body: dict = Body(...),
    _admin=Depends(get_current_admin),
) -> dict:
    """用 ClinInsOS 管理员账号换取备案库 token（前端存本地，服务端不保存）。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{CLININSOS_BASE}/admin/login",
            json={"username": body.get("username", ""), "password": body.get("password", "")},
        )
    if resp.status_code != 200:
        raise HTTPException(401, "ClinInsOS 账号或密码不正确")
    return resp.json()


@router.get("")
async def list_volunteers(
    request: Request,
    _admin=Depends(get_current_admin),
    x_clininsos_token: str = Header(default=""),
) -> list:
    """报名列表（状态 / 审核层级 / 关键词筛选）。"""
    _require_token(x_clininsos_token)
    resp = await _forward(
        x_clininsos_token,
        "/admin/volunteers",
        params={k: v for k, v in request.query_params.items() if v},
    )
    if resp.status_code != 200:
        raise HTTPException(502, f"备案库查询失败：{resp.text[:160]}")
    return resp.json()


@router.post("/h5-password/reset")
async def reset_h5_password(
    body: dict = Body(...),
    _admin=Depends(get_current_admin),
    x_clininsos_token: str = Header(default=""),
) -> dict:
    """重置志愿者 H5 登录密码（按手机号，管理员兜底，无需短信）。"""
    _require_token(x_clininsos_token)
    resp = await _forward(
        x_clininsos_token,
        "/admin/volunteers/h5-password/reset",
        method="POST",
        json_body={
            "phone": body.get("phone", ""),
            "new_password": body.get("new_password", ""),
        },
    )
    if resp.status_code != 200:
        raise HTTPException(502, f"重置失败：{resp.text[:160]}")
    return resp.json()


@router.get("/{public_id}")
async def volunteer_detail(
    public_id: str,
    _admin=Depends(get_current_admin),
    x_clininsos_token: str = Header(default=""),
) -> dict:
    """报名详情（全部字段 + 审核时间线）。"""
    _require_token(x_clininsos_token)
    resp = await _forward(x_clininsos_token, f"/admin/volunteers/{public_id}")
    if resp.status_code != 200:
        raise HTTPException(502, f"备案库详情获取失败：{resp.text[:160]}")
    return resp.json()


@router.post("/{public_id}/review")
async def review_volunteer(
    public_id: str,
    body: dict = Body(...),
    _admin=Depends(get_current_admin),
    x_clininsos_token: str = Header(default=""),
) -> dict:
    """审核动作（approve level=1 初审 / level=2 终审；reject 需填意见）。"""
    _require_token(x_clininsos_token)
    resp = await _forward(
        x_clininsos_token,
        f"/admin/volunteers/{public_id}/review",
        method="POST",
        json_body={
            "action": body.get("action", ""),
            "level": body.get("level", 1),
            "comment": body.get("comment", ""),
        },
    )
    if resp.status_code != 200:
        raise HTTPException(502, f"审核提交失败：{resp.text[:160]}")
    return resp.json()
