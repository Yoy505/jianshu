"""设备域（切片 C）测试辅助：合成适配器与设备事实造数。

合成适配器仅测试/开发使用（`register_adapter`），生产注册表为空——同步如实 blocked。
"""

import uuid
from datetime import datetime, timedelta, timezone

import httpx

from tests.commerce_helpers import API

SYNTH_VENDOR = {"code": "synth", "name": "合成测试厂商（未定型号占位）"}
DEVICE_MODEL = "WRIST-TEST"

# 测试用户 1 的厂商侧账号标识 = 微信身份（bind_test_wx_identity 绑定的 openid）
USER1_KEY = "steward-test-openid"


def make_record(
    record_id: str,
    *,
    user_key: str = USER1_KEY,
    day_offset: int = 0,
    tz_offset_minutes: int = 480,
    updated_at: datetime | None = None,
    metrics: list[tuple[str, float]] | None = None,
) -> dict:
    """构造一条厂商记录（业务日期 = 今天 + day_offset）。"""
    day = datetime.now(timezone.utc).date() + timedelta(days=day_offset)
    return {
        "vendor_user_key": user_key,
        "vendor_record_id": record_id,
        "vendor_updated_at": (updated_at or datetime.now(timezone.utc)).isoformat(),
        "metrics": [
            {
                "metric_type": metric_type,
                "value": value,
                "business_date": day.isoformat(),
                "tz_offset_minutes": tz_offset_minutes,
            }
            for metric_type, value in (metrics or [("sleep_duration", 420)])
        ],
    }


class ListAdapter:
    """按调用次序依次返回预置批次的合成适配器；批次耗尽返回空。"""

    def __init__(self, batches: list[list[dict]]):
        self.batches = list(batches)

    async def pull(self, binding, cursor: str, user_key: str):
        if not self.batches:
            return [], cursor
        batch = self.batches.pop(0)
        next_cursor = f"rec-{batch[-1]['vendor_record_id']}" if batch else cursor
        return batch, next_cursor


class FailingAdapter:
    async def pull(self, binding, cursor: str, user_key: str):
        raise RuntimeError("合成厂商接口故障")


async def ensure_device_facts(client: httpx.AsyncClient, a: dict) -> dict:
    """get-or-create 合成厂商 + 本人 active 绑定，并注入一条有效数据（幂等）。"""
    from app.services import device_service as ds

    resp = await client.post(f"{API}/admin/devices/vendors", json=SYNTH_VENDOR, headers=a)
    assert resp.status_code in (201, 409), resp.text

    resp = await client.get(f"{API}/admin/devices/bindings", params={"user_id": 1, "status": "active"}, headers=a)
    active = [b for b in resp.json()["items"]]
    if active:
        binding = active[0]
    else:
        resp = await client.post(
            f"{API}/admin/devices/bindings",
            json={
                "user_id": 1,
                "vendor_id": await _synth_vendor_id(client, a),
                "device_model": DEVICE_MODEL,
                "device_sn": f"SN-{uuid.uuid4().hex[:8]}",
            },
            headers=a,
        )
        assert resp.status_code == 201, resp.text
        binding = resp.json()
    binding_id = binding["id"]

    # 已有有效数据则不重复注入
    resp = await client.get(
        f"{API}/admin/devices/metrics", params={"binding_id": binding_id, "quality": "valid"}, headers=a
    )
    if resp.json()["total"] == 0:
        async with client.session_factory() as session:  # type: ignore[attr-defined]
            ds.register_adapter(SYNTH_VENDOR["code"], ListAdapter([[make_record("setup-1")]]))
            result = await ds.sync_binding(session, binding_id)
        assert result["status"] in ("success", "partial"), result
    return binding


async def _synth_vendor_id(client: httpx.AsyncClient, a: dict) -> int:
    resp = await client.get(f"{API}/admin/devices/vendors", headers=a)
    for vendor in resp.json():
        if vendor["code"] == SYNTH_VENDOR["code"]:
            return vendor["id"]
    raise AssertionError("合成厂商应已注册")
