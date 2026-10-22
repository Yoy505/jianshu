"""服务包交易域测试共用夹具与工具。"""

import base64
import json
import secrets
import time
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings
from app.core.security import SCOPE_USER, create_token
from app.services import wxpay_service

API = "/api/v1"


def user_headers(user_id: int = 1) -> dict:
    return {"Authorization": f"Bearer {create_token(user_id, SCOPE_USER, 'access')}"}


async def admin_headers(client: httpx.AsyncClient, role: str = "admin") -> dict:
    resp = await client.post(
        f"{API}/admin/auth/login",
        json={"username": role, "password": "admin123" if role == "admin" else "editor123"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def make_package(
    client: httpx.AsyncClient,
    headers: dict,
    *,
    name: str = "全域健康管家 · 45 天服务包",
    price_cents: int = 9900,
    points_cost: int | None = None,
    validity_days: int = 45,
    on_shelf: bool = True,
) -> dict:
    """经后台 API 建服务包 + 版本（+上架），返回 {package_id, version_id}。"""
    resp = await client.post(
        f"{API}/admin/mp/packages", json={"name": name, "subtitle": "测试服务包"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    package_id = resp.json()["id"]
    body = {
        "price_cents": price_cents,
        "validity_days": validity_days,
        "benefits": [{"name": "健康管家服务", "desc": "45 天全域健康管家"}, {"name": "用药咨询"}],
        "service_desc": "服务内容说明：以服务准备页与客服为准。",
        "terms": "售后规则：未激活可全额退款；激活后按实际服务进度协商。",
    }
    if points_cost is not None:
        body["points_cost"] = points_cost
    resp = await client.post(
        f"{API}/admin/mp/packages/{package_id}/versions", json=body, headers=headers
    )
    assert resp.status_code == 201, resp.text
    version_id = resp.json()["id"]
    if on_shelf:
        resp = await client.post(
            f"{API}/admin/mp/packages/{package_id}/shelf",
            json={"on_shelf": True, "version_id": version_id},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
    return {"package_id": package_id, "version_id": version_id}


@pytest.fixture
def wxpay_env(tmp_path):
    """生成真实 RSA 密钥对与 APIv3 密钥，写入配置并加载。

    平台「证书」以公钥 PEM 提供（加载器两种格式都支持）；
    platform_key 供测试对回调签名，merchant_key 供服务端请求签名。
    """
    merchant_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    platform_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = merchant_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub_pem = platform_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    (tmp_path / "mch.pem").write_bytes(priv_pem)
    (tmp_path / "platform.pem").write_bytes(pub_pem)
    api_key = "0123456789abcdef0123456789abcdef"

    saved = dict(
        WXPAY_MCHID=settings.WXPAY_MCHID,
        WXPAY_API_V3_KEY=settings.WXPAY_API_V3_KEY,
        WXPAY_CERT_SERIAL_NO=settings.WXPAY_CERT_SERIAL_NO,
        WXPAY_PRIVATE_KEY_PATH=settings.WXPAY_PRIVATE_KEY_PATH,
        WXPAY_PLATFORM_CERT_PATH=settings.WXPAY_PLATFORM_CERT_PATH,
        WXPAY_NOTIFY_URL=settings.WXPAY_NOTIFY_URL,
        WX_APPID=settings.WX_APPID,
    )
    settings.WXPAY_MCHID = "1900000001"
    settings.WXPAY_API_V3_KEY = api_key
    settings.WXPAY_CERT_SERIAL_NO = "TEST-SERIAL-0001"
    settings.WXPAY_PRIVATE_KEY_PATH = str(tmp_path / "mch.pem")
    settings.WXPAY_PLATFORM_CERT_PATH = str(tmp_path / "platform.pem")
    settings.WXPAY_NOTIFY_URL = "https://mp.singlin.cn/api/v1/pay/wxpay/notify"
    settings.WX_APPID = settings.WX_APPID or "wx-test-appid"
    wxpay_service.reset_config_cache()
    try:
        yield SimpleNamespace(platform_key=platform_key, api_key=api_key)
    finally:
        for key, value in saved.items():
            setattr(settings, key, value)
        wxpay_service.reset_config_cache()
        wxpay_service.set_http_transport_for_tests(None)


def build_notify(env, payload: dict, *, sign: bool = True, timestamp: str | None = None):
    """构造微信支付回调：真实 AES-256-GCM 加密 + RSA-SHA256 签名。"""
    nonce = secrets.token_bytes(12).hex()  # 加密与信封使用同一 nonce 字符串
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    ciphertext = AESGCM(env.api_key.encode()).encrypt(nonce.encode(), plaintext, b"transaction")
    body = json.dumps(
        {
            "resource": {
                "ciphertext": base64.b64encode(ciphertext).decode(),
                "nonce": nonce,
                "associated_data": "transaction",
            }
        },
        separators=(",", ":"),
    ).encode()
    ts = timestamp or str(int(time.time()))
    header_nonce = secrets.token_hex(8)
    headers = {}
    if sign:
        message = f"{ts}\n{header_nonce}\n".encode() + body + b"\n"
        signature = base64.b64encode(
            env.platform_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        ).decode()
        headers = {
            "Wechatpay-Timestamp": ts,
            "Wechatpay-Nonce": header_nonce,
            "Wechatpay-Signature": signature,
        }
    return body, headers
