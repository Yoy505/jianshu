"""微信回执使用桩；不访问真实微信账号。"""
from unittest.mock import AsyncMock
import httpx
import pytest
from sqlalchemy import select
from app.api import wx as wx_api
from app.core.config import settings
from app.core.deps import get_db
from app.main import app
from app.models import User
from app.services import wx_service
from conftest import FakeRedis

async def account(**fields):
    async for db in app.dependency_overrides[get_db]():
        user = User(nickname="手机号测试", **fields)
        db.add(user)
        await db.commit()
        return user.id

@pytest.fixture
def wx_reply(monkeypatch):
    session = AsyncMock(return_value={"openid": "phone-test-openid", "unionid": "union-test"})
    phone = AsyncMock(return_value="13900000009")
    monkeypatch.setattr(wx_api, "code2session", session)
    monkeypatch.setattr(wx_api, "phone_number", phone)
    return session, phone

@pytest.mark.asyncio
async def test_phone_required_and_new_account(client, wx_reply):
    assert (await client.post('/api/v1/wx/login', json={'code': 'real-code'})).status_code == 400
    response = await client.post('/api/v1/wx/login', json={'code': 'login-code', 'phone_code': 'phone-code', 'phone': '13800000001'})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['token'] and body['is_new'] and body['user']['phone'] == '139****0009'
    wx_reply[0].assert_awaited_once_with('login-code')
    assert wx_reply[1].await_args.args[0] == 'phone-code'
    again = await client.post('/api/v1/wx/login', json={'code': 'next', 'phone_code': 'next-phone'})
    assert again.status_code == 200 and not again.json()['is_new']
    assert again.json()['user']['id'] == body['user']['id']

@pytest.mark.asyncio
async def test_link_existing_phone_account(client, wx_reply):
    wx_reply[1].return_value = '13800000001'
    response = await client.post('/api/v1/wx/login', json={'code': 'login', 'phone_code': 'phone'})
    assert response.status_code == 200 and not response.json()['is_new']
    async for db in app.dependency_overrides[get_db]():
        user = (await db.execute(select(User).where(User.phone == '13800000001'))).scalar_one()
        assert user.wx_openid == 'phone-test-openid'

@pytest.mark.asyncio
@pytest.mark.parametrize('conflict', ['different_openid', 'two_accounts', 'changed_phone', 'banned'])
async def test_account_conflicts_never_merge(client, wx_reply, conflict):
    if conflict == 'different_openid':
        await account(phone='13900000009', wx_openid='another-openid')
    elif conflict == 'two_accounts':
        await account(wx_openid='phone-test-openid')
        await account(phone='13900000009')
    elif conflict == 'changed_phone':
        await account(wx_openid='phone-test-openid', phone='13900000008')
    else:
        await account(phone='13900000009', status='banned')
    response = await client.post('/api/v1/wx/login', json={'code': 'login', 'phone_code': 'phone'})
    assert response.status_code == (403 if conflict == 'banned' else 409)
    assert 'token' not in response.json()

@pytest.mark.asyncio
async def test_existing_openid_binds_phone(client, wx_reply):
    user_id = await account(wx_openid='phone-test-openid')
    response = await client.post('/api/v1/wx/login', json={'code': 'login', 'phone_code': 'phone'})
    assert response.status_code == 200
    assert response.json()['user']['id'] == user_id
    assert response.json()['user']['phone'] == '139****0009'

@pytest.mark.asyncio
async def test_guest_landing_and_protected_routes(client):
    for path in ['home', 'scenarios', 'services', 'services?scenario=rehab', 'drugs', 'products']:
        response = await client.get('/api/v1/mp/' + path)
        assert response.status_code == 200, (path, response.text)
    for path in ['services/1', 'providers', 'providers/1', 'drugs/1', 'services?q=康复', 'drugs?tag=otc', 'products?category=food', 'drugs?page=2', 'profile', 'appointments', 'consults', 'favorites']:
        response = await client.get('/api/v1/mp/' + path)
        assert response.status_code == 401, (path, response.text)

@pytest.mark.asyncio
@pytest.mark.parametrize('variant', ['ok', 'expired', 'wrong_app', 'bad_json', 'http_error', 'missing_info'])
async def test_wechat_phone_verification(monkeypatch, variant):
    monkeypatch.setattr(settings, 'WX_APPID', 'test-app')
    monkeypatch.setattr(settings, 'WX_SECRET', 'test-secret')
    posts = []
    def handler(request):
        posts.append(request)
        if request.url.path.endswith('stable_token'):
            return httpx.Response(200, json={'access_token': 'server-only-token', 'expires_in': 7200})
        if variant == 'expired': return httpx.Response(200, json={'errcode': 40029})
        if variant == 'bad_json': return httpx.Response(200, text='bad')
        if variant == 'http_error': return httpx.Response(503)
        if variant == 'missing_info': return httpx.Response(200, json={'errcode': 0})
        return httpx.Response(200, json={'errcode': 0, 'phone_info': {'purePhoneNumber': '13900000009', 'countryCode': '86', 'watermark': {'appid': 'wrong' if variant == 'wrong_app' else 'test-app'}}})
    original = httpx.AsyncClient
    monkeypatch.setattr(wx_service.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    redis = FakeRedis()
    if variant == 'ok':
        assert await wx_service.phone_number('phone-code', redis) == '13900000009'
        assert await wx_service.phone_number('another-code', redis) == '13900000009'
        assert sum(r.url.path.endswith('stable_token') for r in posts) == 1
        assert b'phone-code' in posts[1].content
    else:
        from fastapi import HTTPException
        with pytest.raises(HTTPException): await wx_service.phone_number('phone-code', redis)
