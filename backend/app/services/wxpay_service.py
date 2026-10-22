"""微信支付 v3（服务商外直连商户）服务：JSAPI 下单、回调验签解密、查单、退款。

真实外部契约按微信支付 v3 文档实现：
- 请求签名： Authorization: WECHATPAY2-SHA256-RSA2048 mchid="",nonce_str="",signature="",
  timestamp="",serial_no=""；签名串 `METHOD\nURL_PATH\nTIMESTAMP\nNONCE\nBODY\n`，RSA-SHA256。
- 调起支付参数签名串 `appid\ntimeStamp\nnonceStr\nprepay_id\n`。
- 回调验签： `TIMESTAMP\nNONCE\nBODY\n` + 平台证书/公钥 RSA-SHA256；时间戳 ±5 分钟。
- 回调资源解密： AES-256-GCM（APIv3 密钥，nonce 12 字节，associated_data）。

任何配置缺失时 wxpay_enabled() 为 False，调用方按契约返回 503；
本模块不读取业务数据、不写库，密钥与证书只从配置文件路径加载且不落入日志。
测试通过 set_http_transport_for_tests 注入 httpx MockTransport 与 generate_* 辅助。
"""

import base64
import json
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509 import load_pem_x509_certificate

from app.core.config import settings

logger = logging.getLogger("app.wxpay")

WXPAY_BASE = "https://api.mch.weixin.qq.com"
NOTIFY_TIMESTAMP_TOLERANCE = 300  # 秒


class WxPayError(Exception):
    """微信支付接口返回非成功（errcode/HTTP 非 2xx），message 面向日志不含密钥。"""


@dataclass(frozen=True)
class WxPayConfig:
    appid: str
    mchid: str
    api_v3_key: bytes
    cert_serial_no: str
    private_key: Any  # RSAPrivateKey
    platform_public_key: Any  # RSAPublicKey（平台证书或公钥）
    notify_url: str


_config_cache: WxPayConfig | None = None
_config_loaded = False
# 测试注入点：非 None 时所有出站请求走该 transport（不触网）
_http_transport: httpx.AsyncBaseTransport | None = None


def reset_config_cache() -> None:
    """配置变更（测试）后清缓存，下次调用重新加载。"""
    global _config_cache, _config_loaded
    _config_cache = None
    _config_loaded = False


def set_http_transport_for_tests(transport: httpx.AsyncBaseTransport | None) -> None:
    global _http_transport
    _http_transport = transport


def wxpay_enabled() -> bool:
    return load_config() is not None


def load_config() -> WxPayConfig | None:
    """加载并缓存配置；任一项缺失返回 None（不抛错、不落日志敏感值）。"""
    global _config_cache, _config_loaded
    if _config_loaded:
        return _config_cache
    _config_loaded = True
    try:
        if not (
            settings.WX_APPID
            and settings.WXPAY_MCHID
            and settings.WXPAY_API_V3_KEY
            and settings.WXPAY_CERT_SERIAL_NO
            and settings.WXPAY_PRIVATE_KEY_PATH
            and settings.WXPAY_PLATFORM_CERT_PATH
            and settings.WXPAY_NOTIFY_URL
        ):
            return None
        if len(settings.WXPAY_API_V3_KEY.encode()) != 32:
            logger.warning("WXPAY_API_V3_KEY 长度不是 32 字节，按未配置处理")
            return None
        key_pem = Path(settings.WXPAY_PRIVATE_KEY_PATH).read_bytes()
        private_key = serialization.load_pem_private_key(key_pem, password=None)
        cert_pem = Path(settings.WXPAY_PLATFORM_CERT_PATH).read_bytes()
        try:
            platform_public_key = load_pem_x509_certificate(cert_pem).public_key()
        except ValueError:
            platform_public_key = serialization.load_pem_public_key(cert_pem)
        _config_cache = WxPayConfig(
            appid=settings.WX_APPID,
            mchid=settings.WXPAY_MCHID,
            api_v3_key=settings.WXPAY_API_V3_KEY.encode(),
            cert_serial_no=settings.WXPAY_CERT_SERIAL_NO,
            private_key=private_key,
            platform_public_key=platform_public_key,
            notify_url=settings.WXPAY_NOTIFY_URL,
        )
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("微信支付配置加载失败（%s），支付通道按未开通处理", type(exc).__name__)
        _config_cache = None
    return _config_cache


def _rsa_sign(private_key: Any, message: str) -> str:
    signature = private_key.sign(
        message.encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    return base64.b64encode(signature).decode()


def _rsa_verify(public_key: Any, message: str, signature_b64: str) -> bool:
    try:
        public_key.verify(
            base64.b64decode(signature_b64), message.encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        return True
    except (InvalidSignature, ValueError):
        return False


def _authorization(cfg: WxPayConfig, method: str, url_path: str, body: str) -> str:
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    message = f"{method}\n{url_path}\n{timestamp}\n{nonce}\n{body}\n"
    signature = _rsa_sign(cfg.private_key, message)
    return (
        f'WECHATPAY2-SHA256-RSA2048 mchid="{cfg.mchid}",nonce_str="{nonce}",'
        f'signature="{signature}",timestamp="{timestamp}",serial_no="{cfg.cert_serial_no}"'
    )


async def _request(
    cfg: WxPayConfig, method: str, url_path: str, payload: dict | None = None
) -> dict[str, Any]:
    body = (
        ""
        if payload is None
        else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": _authorization(cfg, method, url_path, body or ""),
        "User-Agent": "jianshu-commerce/1.0",
    }
    async with httpx.AsyncClient(
        base_url=WXPAY_BASE, timeout=15.0, transport=_http_transport
    ) as client:
        resp = await client.request(method, url_path, content=body or None, headers=headers)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 300 or (isinstance(data, dict) and data.get("code") == "FAIL"):
        code = data.get("code", f"HTTP_{resp.status_code}") if isinstance(data, dict) else f"HTTP_{resp.status_code}"
        raise WxPayError(f"{code}:{data.get('message', '') if isinstance(data, dict) else ''}")
    return data


async def jsapi_prepay(
    cfg: WxPayConfig, *, out_trade_no: str, description: str, total_cents: int, openid: str
) -> str:
    """JSAPI 下单，返回 prepay_id。"""
    payload = {
        "appid": cfg.appid,
        "mchid": cfg.mchid,
        "description": description,
        "out_trade_no": out_trade_no,
        "notify_url": cfg.notify_url,
        "amount": {"total": total_cents, "currency": "CNY"},
        "payer": {"openid": openid},
    }
    data = await _request(cfg, "POST", "/v3/pay/transactions/jsapi", payload)
    prepay_id = data.get("prepay_id")
    if not prepay_id:
        raise WxPayError("PREPAY_MISSING:微信未返回 prepay_id")
    return str(prepay_id)


def request_payment_params(cfg: WxPayConfig, prepay_id: str) -> dict[str, str]:
    """构造 wx.requestPayment 参数（RSA 签名）。"""
    time_stamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    message = f"{cfg.appid}\n{time_stamp}\n{nonce}\nprepay_id={prepay_id}\n"
    return {
        "timeStamp": time_stamp,
        "nonceStr": nonce,
        "package": f"prepay_id={prepay_id}",
        "signType": "RSA",
        "paySign": _rsa_sign(cfg.private_key, message),
    }


async def query_order(cfg: WxPayConfig, out_trade_no: str) -> dict[str, Any]:
    """查单（回调丢失时对账）。"""
    return await _request(
        cfg, "GET", f"/v3/pay/transactions/out-trade-no/{out_trade_no}?mchid={cfg.mchid}"
    )


async def close_order(cfg: WxPayConfig, out_trade_no: str) -> None:
    await _request(
        cfg,
        "POST",
        f"/v3/pay/transactions/out-trade-no/{out_trade_no}/close",
        {"mchid": cfg.mchid},
    )


async def create_refund(
    cfg: WxPayConfig,
    *,
    out_trade_no: str,
    out_refund_no: str,
    refund_cents: int,
    total_cents: int,
    reason: str,
) -> dict[str, Any]:
    payload = {
        "out_trade_no": out_trade_no,
        "out_refund_no": out_refund_no,
        "reason": reason,
        "notify_url": cfg.notify_url,
        "amount": {"refund": refund_cents, "total": total_cents, "currency": "CNY"},
    }
    return await _request(cfg, "POST", "/v3/refund/domestic/refunds", payload)


def verify_notify_signature(
    cfg: WxPayConfig, *, timestamp: str, nonce: str, body: bytes, signature: str
) -> bool:
    """回调验签：时间戳新鲜度 + 平台公钥 RSA-SHA256。"""
    try:
        if abs(int(timestamp) - int(time.time())) > NOTIFY_TIMESTAMP_TOLERANCE:
            return False
    except (TypeError, ValueError):
        return False
    message = f"{timestamp}\n{nonce}\n".encode() + body + b"\n"
    return _rsa_verify(cfg.platform_public_key, message.decode(errors="replace"), signature)


def decrypt_resource(cfg: WxPayConfig, resource: dict[str, Any]) -> dict[str, Any]:
    """AES-256-GCM 解密回调 resource，返回业务 JSON（含 out_trade_no 等）。"""
    ciphertext = base64.b64decode(resource["ciphertext"])
    nonce = resource["nonce"].encode()
    associated = (resource.get("associated_data") or "").encode()
    plaintext = AESGCM(cfg.api_v3_key).decrypt(nonce, ciphertext, associated)
    data = json.loads(plaintext)
    if not isinstance(data, dict):
        raise ValueError("回调资源不是 JSON 对象")
    return data
