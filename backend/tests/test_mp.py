"""杏临汇小程序（/mp）：首页聚合、药品检索过滤、预约校验、咨询自动回复、档案与商城骨架。

conftest 以 SQLite 内存库建表并调用 app.seed.seed_mp_data（幂等种子），
不依赖外部 PG / Redis；C 端用户 token 直接按种子用户（users 表自增 id=1）签发。
"""

from datetime import date, timedelta

import pytest

from app.core.security import SCOPE_USER, TOKEN_TYPE_ACCESS, create_token

API = "/api/v1/mp"
PERIODS = {"上午", "下午", "晚间"}


@pytest.fixture
def auth_headers() -> dict:
    token = create_token(1, SCOPE_USER, TOKEN_TYPE_ACCESS)
    return {"Authorization": f"Bearer {token}"}


async def _first_service(client, keyword: str) -> dict:
    resp = await client.get(f"{API}/services", params={"q": keyword})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items, f"未找到服务：{keyword}"
    return items[0]


async def _first_provider(client, service_id: int) -> dict:
    resp = await client.get(f"{API}/providers", params={"service_id": service_id})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items, "该服务下无可匹配人员"
    return items[0]


@pytest.mark.asyncio
async def test_mp_home(client):
    resp = await client.get(f"{API}/home")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"scenarios", "notices", "stats"}

    scenarios = body["scenarios"]
    assert len(scenarios) == 9
    assert [s["sort"] for s in scenarios] == sorted(s["sort"] for s in scenarios)
    assert scenarios[0]["key"] == "checkup"
    consult = next(s for s in scenarios if s["key"] == "consult")
    assert consult["name"] == "看病问诊"
    assert consult["tagline"] == "服务流程演示，真人接诊尚未开放"

    assert len(body["notices"]) == 3
    assert set(body["notices"][0]) == {"id", "content"}
    assert "义诊" in body["notices"][0]["content"]

    assert body["stats"] == {"providers": 8, "services": 12, "users": 1}


@pytest.mark.asyncio
async def test_mp_services_filters(client):
    resp = await client.get(f"{API}/scenarios")
    assert resp.status_code == 200
    assert len(resp.json()) == 9

    resp = await client.get(f"{API}/services", params={"scenario": "medication"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(item["scenario"] == "medication" for item in body["items"])
    first = body["items"][0]
    assert set(first) == {
        "id", "scenario", "scenario_name", "service_type", "name", "desc",
        "price", "unit", "duration_min", "sales", "provider_count",
    }
    assert first["scenario_name"] == "合理用药"

    resp = await client.get(f"{API}/services", params={"service_type": "third"})
    assert resp.status_code == 200
    third = resp.json()
    assert third["total"] == 2
    assert all(item["service_type"] == "third" for item in third["items"])

    resp = await client.get(f"{API}/services", params={"q": "陪诊"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp = await client.get(f"{API}/services", params={"service_type": "vip"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "服务类型仅支持 online、offline 或 third"


@pytest.mark.asyncio
async def test_mp_drugs_filters(client):
    resp = await client.get(f"{API}/drugs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 14
    names = [item["common_name"] for item in body["items"]]
    assert names == sorted(names)  # 按 common_name 排序
    assert all(item["favorited"] is False for item in body["items"])

    resp = await client.get(f"{API}/drugs", params={"tag": "rx"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert all("rx" in item["tags"] for item in body["items"])
    aspirin = next(d for d in body["items"] if d["common_name"] == "阿司匹林肠溶片")
    assert aspirin["tags"] == ["rx", "essential", "insurance"]  # 计划书示例标签

    resp = await client.get(f"{API}/drugs", params={"letter": "A"})
    assert resp.status_code == 200
    body = resp.json()
    # 阿司匹林 / 阿莫西林 / 氨氯地平 / 奥美拉唑 / 阿托伐他汀 共 5 个
    assert body["total"] == 5
    assert all(item["manufacturer"] for item in body["items"])

    resp = await client.get(f"{API}/drugs", params={"q": "阿司匹林"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["brand_name"] == "拜阿司匹灵"
    assert len(body["items"][0]["science"]) > 50  # 科普正文为成段文字

    resp = await client.get(f"{API}/drugs", params={"tag": "unknown"})
    assert resp.status_code == 400
    resp = await client.get(f"{API}/drugs", params={"letter": "a"})
    assert resp.status_code == 400

    # 详情：留言板结构存在（未登录 favorited=false）
    drug_id = aspirin["id"]
    resp = await client.get(f"{API}/drugs/{drug_id}")
    assert resp.status_code == 200
    detail = resp.json()
    assert set(detail) - set(aspirin) == {"board", "feedback_mode"}
    assert detail["feedback_mode"] == "demo"
    assert detail["board"] == []


@pytest.mark.asyncio
async def test_mp_appointments_create_and_validation(client, auth_headers):
    online = await _first_service(client, "全科")
    assert online["service_type"] == "online"
    provider = await _first_provider(client, online["id"])

    # 未登录 401
    resp = await client.post(
        f"{API}/appointments",
        json={"service_id": online["id"], "provider_id": provider["id"]},
    )
    assert resp.status_code == 401

    payload = {
        "service_id": online["id"],
        "provider_id": provider["id"],
        "date": (date.today() + timedelta(days=1)).isoformat(),
        "period": "上午",
        "address": None,  # 线上服务地址可空
        "contact_name": "南屿",
        "contact_phone": "13800000001",
        "remark": "血压复查",
    }
    resp = await client.post(f"{API}/appointments", json=payload, headers=auth_headers)
    assert resp.status_code == 201
    created = resp.json()
    assert created["status"] == "pending"
    appointment_id = created["id"]

    # 列表可见（种子已有 2 个演示预约）
    resp = await client.get(f"{API}/appointments", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    mine = next(a for a in body["items"] if a["id"] == appointment_id)
    assert mine["service"]["name"] == online["name"]
    assert mine["provider"]["name"] == provider["name"]

    # 日期校验：过去与超出 7 天均 400
    for bad_date in (date.today() - timedelta(days=1), date.today() + timedelta(days=8)):
        resp = await client.post(
            f"{API}/appointments",
            json={**payload, "date": bad_date.isoformat()},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "7 天" in resp.json()["detail"]

    # 时段校验 400
    resp = await client.post(
        f"{API}/appointments", json={**payload, "period": "中午"}, headers=auth_headers
    )
    assert resp.status_code == 400

    # 线下上门服务必须填写地址
    offline = await _first_service(client, "康复治疗师")
    offline_provider = await _first_provider(client, offline["id"])
    resp = await client.post(
        f"{API}/appointments",
        json={**payload, "service_id": offline["id"], "provider_id": offline_provider["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "地址" in resp.json()["detail"]

    # 人员不提供该服务 → 400
    escort_service = await _first_service(client, "陪诊")
    escort_provider = await _first_provider(client, escort_service["id"])
    resp = await client.post(
        f"{API}/appointments",
        json={**payload, "provider_id": escort_provider["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 400

    # 取消：pending 可取消，再取消 409
    resp = await client.post(
        f"{API}/appointments/{appointment_id}/cancel", headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    resp = await client.post(
        f"{API}/appointments/{appointment_id}/cancel", headers=auth_headers
    )
    assert resp.status_code == 409

    # 评价：评分范围 400；非 completed 409；已评价的种子预约 409
    confirmed = next(
        a for a in body["items"] if a["status"] == "confirmed"
    )
    resp = await client.post(
        f"{API}/appointments/{confirmed['id']}/review",
        json={"rating": 0},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    resp = await client.post(
        f"{API}/appointments/{confirmed['id']}/review",
        json={"rating": 5, "content": "很好"},
        headers=auth_headers,
    )
    assert resp.status_code == 409  # 尚未完成
    completed = next(a for a in body["items"] if a["status"] == "completed")
    resp = await client.post(
        f"{API}/appointments/{completed['id']}/review",
        json={"rating": 4, "content": "回复很快"},
        headers=auth_headers,
    )
    assert resp.status_code == 409  # 种子预约已评价过


@pytest.mark.asyncio
async def test_mp_consult_autoreply(client, auth_headers):
    # token 指向种子演示用户（已建档）
    resp = await client.get(f"{API}/profile", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "南屿"

    ai_service = await _first_service(client, "健康问答演示")
    provider = await _first_provider(client, ai_service["id"])

    # 种子已为该用户建立同 service+provider 的 open 会话：复用返回同一 id，并追加消息
    resp = await client.post(
        f"{API}/consults",
        json={
            "service_id": ai_service["id"],
            "provider_id": provider["id"],
            "content": "谢谢，那膝盖疼的时候止痛药应该怎么选？",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    consult_id = body["id"]
    assert body["reused"] is True

    # 旧种子消息仍可读取；新消息必须作为 system 自动回执落库
    resp = await client.get(
        f"{API}/consults/{consult_id}/messages", headers=auth_headers
    )
    assert resp.status_code == 200
    messages = resp.json()
    assert [m["sender"] for m in messages] == [
        "user", "provider", "user", "provider", "user", "system",
    ]
    assert "布洛芬" in messages[0]["content"]  # 种子首条 user 消息
    assert "系统自动回执" in messages[5]["content"]
    assert "尚未接入真人专业人员" in messages[5]["content"]
    assert "个体用药建议" in messages[5]["content"]
    assert "阿司匹林" not in messages[5]["content"]
    assert "布洛芬" not in messages[5]["content"]

    # 增量轮询：after_id 只取其后消息
    resp = await client.get(
        f"{API}/consults/{consult_id}/messages",
        params={"after_id": messages[4]["id"]},
        headers=auth_headers,
    )
    assert [m["id"] for m in resp.json()] == [messages[5]["id"]]

    # 追加消息：仍自动生成回复
    resp = await client.post(
        f"{API}/consults/{consult_id}/messages",
        json={"content": "那平时还需要注意什么？"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    sent = resp.json()
    assert sent["user_message"]["sender"] == "user"
    assert sent["system_message"]["sender"] == "system"
    assert "系统自动回执" in sent["system_message"]["content"]
    assert "provider_message" not in sent

    resp = await client.get(
        f"{API}/consults/{consult_id}/messages", headers=auth_headers
    )
    assert len(resp.json()) == 8

    # 非 AI 服务同样只发系统回执，不冒用具体人员身份
    general = await _first_service(client, "全科")
    general_provider = await _first_provider(client, general["id"])
    resp = await client.post(
        f"{API}/consults",
        json={
            "service_id": general["id"],
            "provider_id": general_provider["id"],
            "content": "最近血压有点波动，想咨询一下。",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["reused"] is False
    human_id = resp.json()["id"]
    resp = await client.get(f"{API}/consults/{human_id}/messages", headers=auth_headers)
    human_messages = resp.json()
    assert [m["sender"] for m in human_messages] == ["user", "system"]
    reply = human_messages[1]
    assert general_provider["name"] not in reply["content"]
    assert general_provider["title"] not in reply["content"]
    assert "尚未接入真人专业人员" in reply["content"]
    assert "诊断、治疗或个体用药建议" in reply["content"]
    assert "120" in reply["content"]

    # 会话列表含预览与人员摘要（种子 AI 会话 1 个 + 全科新建 1 个）
    resp = await client.get(f"{API}/consults", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    mine = next(c for c in body["items"] if c["id"] == consult_id)
    assert mine["provider"]["name"] == provider["name"]
    assert mine["last_message"]
    human = next(c for c in body["items"] if c["id"] == human_id)
    assert human["provider"]["name"] == general_provider["name"]


@pytest.mark.asyncio
async def test_mp_provider_detail_and_slots(client):
    resp = await client.get(f"{API}/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 8
    items = body["items"]
    # online 优先、rating 倒序
    assert items[0]["online"] is True

    resp = await client.get(f"{API}/providers/{items[0]['id']}")
    assert resp.status_code == 200
    detail = resp.json()
    assert set(detail) - set(items[0]) == {"slots", "reviews"}
    slots = detail["slots"]
    assert len(slots) == 7
    assert slots[0]["date"] == date.today().isoformat()
    for slot in slots:
        assert slot["periods"]
        assert set(slot["periods"]) <= PERIODS
    # 时段稳定：两次请求结果一致（按 date+provider_id 哈希）
    resp2 = await client.get(f"{API}/providers/{items[0]['id']}")
    assert resp2.json()["slots"] == slots
    # 最近 5 条评价（种子演示用户已留 1 条）
    assert len(detail["reviews"]) == 1
    assert detail["reviews"][0]["user_name"] == "测试用户"  # conftest 种子用户昵称
    assert detail["reviews"][0]["rating"] == 5

    resp = await client.get(f"{API}/providers/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_mp_profile_roundtrip(client, auth_headers):
    payload = {
        "name": "南屿",
        "gender": "male",
        "birth": "1988-05",
        "height_cm": 176,
        "weight_kg": 73.5,
        "blood": "A",
        "chronic_tags": ["高血压", "高血脂"],
        "allergy": "青霉素类抗生素",
        "surgery_history": "2023 年阑尾切除术",
        "smoke": False,
        "drink": True,
        "sport_freq": "每周 2-3 次",
    }
    resp = await client.put(f"{API}/profile", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    saved = resp.json()
    assert saved["chronic_tags"] == ["高血压", "高血脂"]
    assert saved["weight_kg"] == 73.5

    resp = await client.get(f"{API}/profile", headers=auth_headers)
    assert resp.json() == saved

    resp = await client.put(
        f"{API}/profile", json={**payload, "gender": "x"}, headers=auth_headers
    )
    assert resp.status_code == 400
    resp = await client.put(
        f"{API}/profile", json={**payload, "birth": "1988-13"}, headers=auth_headers
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_mp_products_and_favorites(client, auth_headers):
    resp = await client.get(f"{API}/products")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 8
    assert all(item["status"] == "coming" for item in body["items"])

    resp = await client.get(f"{API}/products", params={"category": "fsmp"})
    assert resp.json()["total"] == 2
    resp = await client.get(f"{API}/products", params={"category": "vip"})
    assert resp.status_code == 400

    # 药品收藏切换与收藏列表
    drug_id = 1
    resp = await client.post(f"{API}/drugs/{drug_id}/favorite", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"favorited": True}
    resp = await client.get(f"{API}/drugs/{drug_id}", headers=auth_headers)
    assert resp.json()["favorited"] is True
    resp = await client.get(f"{API}/favorites", headers=auth_headers)
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["id"] == drug_id

    # 留言：长度 20-500 之外 400，之内 201
    resp = await client.post(
        f"{API}/drugs/{drug_id}/feedback", json={"content": "太短"}, headers=auth_headers
    )
    assert resp.status_code == 400
    resp = await client.post(
        f"{API}/drugs/{drug_id}/feedback",
        json={"content": "请问这款药需要随餐服用吗？家里老人记性不好，希望说明更清楚一些。"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    resp = await client.get(f"{API}/drugs/{drug_id}")
    assert len(resp.json()["board"]) == 1
    assert resp.json()["board"][0]["user_name"] == "测试用户"
