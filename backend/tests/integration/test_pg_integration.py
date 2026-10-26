"""PostgreSQL 真库集成：迁移循环、并发扣分、跨批次唯一约束、回调入账。

运行前提见 tests/integration/conftest.py 头部说明（JIANSHU_PG_TEST_URL）。
"""

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from tests.commerce_helpers import API, admin_headers, make_package, user_headers, wxpay_env  # noqa: F401

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("JIANSHU_PG_TEST_URL"),
    reason="需要注入本机隔离 PG（JIANSHU_PG_TEST_URL）",
)


def test_migration_cycle_on_pg(pg_engine):
    """（pg_engine 已升级到 head）全量回退 base → 再次升级 → current 指向 head（真实 PG 方言）。"""
    from tests.integration.conftest import PG_URL, alembic_run

    out = alembic_run("downgrade", "base", url=PG_URL)
    assert "Running downgrade" in out
    out = alembic_run("upgrade", "head", url=PG_URL)
    assert "Running upgrade" in out
    out = alembic_run("current", url=PG_URL)
    assert "a7d0e4f6b8c3" in out


@pytest.mark.asyncio
async def test_concurrent_points_redeem_on_pg(pg_client, pg_engine):
    """6 路并发同 client_order_id 积分兑换：PG 行锁/唯一约束下恰一单成功。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import SCOPE_ADMIN, SCOPE_USER, create_token, hash_password
    from app.models import (
        AdminUser, MpPackage, MpPackageVersion, MpPointsAccount, MpSaleRelease, User,
    )

    factory = async_sessionmaker(bind=pg_engine, expire_on_commit=False)
    async with factory() as session:
        admin = AdminUser(username=f"conc-admin-{uuid.uuid4().int % 1000000:06d}", name="并发管理员",
                          password_hash=hash_password("Concurrent-PG-2026!"), role="admin")
        session.add(admin)
        conc_phone = f"13899{uuid.uuid4().int % 1000000:06d}"
        session.add(User(nickname="PG并发用户", phone=conc_phone, status="active"))
        pkg = MpPackage(name="PG并发服务包", status="on_shelf")
        session.add(pkg)
        await session.flush()
        version = MpPackageVersion(
            package_id=pkg.id, version_no=1, price_cents=9900, points_cost=300,
            validity_days=45, benefits=[{"name": "健康管家"}],
            service_desc="PG 并发说明", terms="PG 并发条款",
        )
        session.add(version)
        await session.flush()
        pkg.current_version_id = version.id
        session.add(MpSaleRelease(package_id=pkg.id, version_id=version.id, env="all",
                                  basis="PG 集成放行", released_by_admin_id=admin.id))
        # 用户 id 由序列分配；单独取回
        await session.flush()
        u = (await session.execute(select(User).where(User.phone == conc_phone))).scalar_one()
        session.add(MpPointsAccount(user_id=u.id, balance=300))
        await session.commit()
        uid, pid, vid, aid = u.id, pkg.id, version.id, admin.id

    headers = {"Authorization": f"Bearer {create_token(uid, SCOPE_USER, 'access')}"}
    admin_headers = {"Authorization": f"Bearer {create_token(aid, SCOPE_ADMIN, 'access')}"}
    key = str(uuid.uuid4())
    payload = {
        "client_order_id": key, "package_id": pid, "version_id": vid,
        "pay_type": "points", "agree_terms": True,
    }

    async def submit(_):
        transport = httpx.ASGITransport(app=__import__("app.main", fromlist=["app"]).app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t", headers=headers) as c:
            return await c.post(f"{API}/mp/orders", json=payload)

    results = await asyncio.gather(*(submit(i) for i in range(6)))
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 1, f"应恰一单创建，实际 {codes}"

    from app.models import MpEntitlement, MpOrder, MpPointsEntry

    async with factory() as session:
        orders = (await session.execute(select(MpOrder).where(MpOrder.client_order_id == key))).scalars().all()
        assert len(orders) == 1 and orders[0].status == "paid"
        ents = (await session.execute(select(func.count()).select_from(MpEntitlement)
                                      .where(MpEntitlement.order_id == orders[0].id))).scalar()
        assert int(ents) == 1
        redeems = (await session.execute(select(func.count()).select_from(MpPointsEntry)
                                         .where(MpPointsEntry.biz_type == "redeem"))).scalar()
        assert int(redeems) == 1
        account = await session.get(MpPointsAccount, uid)
        assert account.balance == 0


@pytest.mark.asyncio
async def test_r6_cross_batch_unique_index_on_pg(pg_client, pg_engine):
    """R6 部分唯一索引在 PG 生效：跨批次同号确认 409；直插重复行被数据库拒绝。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models import MpEntitlement
    from tests.integration.conftest import alembic_run  # noqa: F401

    admin = await admin_headers(pg_client)
    org_name = f"PG-R6-单位-{uuid.uuid4().int % 1000000:06d}"
    resp = await pg_client.post(f"{API}/admin/mp/orgs", json={"name": org_name}, headers=admin)
    assert resp.status_code == 201, f"建单位失败: {resp.status_code} {resp.text}"
    org_id = resp.json()["id"]
    made = await make_package(pg_client, admin)

    async def preview(phone, name):
        resp = await pg_client.post(
            f"{API}/admin/mp/batches/preview",
            json={"org_id": org_id, "package_id": made["package_id"], "version_id": made["version_id"],
                  "quantity": 5, "file_name": name, "rows": [phone]},
            headers=admin,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["batch"]["id"], resp.json()["rows"][0]["status"]

    r6_phone = f"13898{uuid.uuid4().int % 1000000:06d}"
    # 两批都在任一方发放前完成预览（此时均判 valid），先后确认时第二批必须撞防重
    batch_b, status_b = await preview(r6_phone, "pg-r6-b.csv")
    batch_c, status_c = await preview(r6_phone, "pg-r6-c.csv")
    assert status_b == "valid" and status_c == "valid"

    first = await pg_client.post(f"{API}/admin/mp/batches/{batch_b}/confirm", headers=admin)
    assert first.status_code == 200
    second = await pg_client.post(f"{API}/admin/mp/batches/{batch_c}/confirm", headers=admin)
    assert second.status_code == 409
    assert "重复手机号" in second.json()["detail"]

    # 数据库层直证：向 mp_entitlements 插入同 org×版本×phone 非 void 行 → IntegrityError
    from sqlalchemy.exc import IntegrityError

    from app.models import MpPackageVersion

    factory = async_sessionmaker(bind=pg_engine, expire_on_commit=False)
    async with factory() as session:
        version = await session.get(MpPackageVersion, made["version_id"])
        ent = MpEntitlement(
            source_type="batch", batch_id=None, batch_row_id=None,
            org_id=org_id, phone=r6_phone,
            package_id=version.package_id, package_version_id=version.id,
            package_name="x", version_no=1, benefits=[], validity_days=45, status="pending",
        )
        session.add(ent)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_wxpay_callback_accept_on_pg(pg_client, wxpay_env):
    """真实密码学回调在 PG 上完成入账：paid + 恰一份权益 + accepted 事件。"""
    from tests.commerce_helpers import build_notify

    admin = await admin_headers(pg_client)
    made = await make_package(pg_client, admin, price_cents=6400)
    resp = await pg_client.post(
        f"{API}/mp/orders",
        json={
            "client_order_id": str(uuid.uuid4()),
            "package_id": made["package_id"],
            "version_id": made["version_id"],
            "pay_type": "wxpay",
            "agree_terms": True,
        },
        headers=user_headers(),
    )
    assert resp.status_code == 201, resp.text
    order = resp.json()

    payload = {
        "out_trade_no": order["order_no"],
        "transaction_id": "wx_pg_tx_001",
        "trade_state": "SUCCESS",
        "amount": {"total": 6400, "currency": "CNY"},
    }
    body, headers = build_notify(wxpay_env, payload)
    resp = await pg_client.post(f"{API}/pay/wxpay/notify", content=body, headers=headers)
    assert resp.status_code == 200 and resp.json()["code"] == "SUCCESS"

    detail = (await pg_client.get(f"{API}/mp/orders/{order['id']}", headers=user_headers())).json()
    assert detail["status"] == "paid" and detail["entitlement_id"]
    admin_detail = (await pg_client.get(f"{API}/admin/mp/orders/{order['id']}", headers=admin)).json()
    assert any(e["result"] == "paid" for e in admin_detail["events"])
