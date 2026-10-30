"""Read-only catalog contracts for the four approved nutrition categories."""
import pytest
from app.core.security import SCOPE_USER, TOKEN_TYPE_ACCESS, create_token
API = '/api/v1/mp'
def headers():
    return {'Authorization': 'Bearer ' + create_token(1, SCOPE_USER, TOKEN_TYPE_ACCESS)}
@pytest.mark.asyncio
@pytest.mark.parametrize('category', ['sport', 'fsmp', 'food', 'functional'])
async def test_nutrition_category_is_supported(client, category):
    response = await client.get(f'{API}/products', params={'category': category}, headers=headers())
    assert response.status_code == 200
    assert all(p['category'] == category and p['status'] == 'coming' for p in response.json()['items'])
@pytest.mark.asyncio
async def test_product_detail_matches_public_catalog_and_absent_is_404(client):
    catalog = (await client.get(f'{API}/products')).json()
    first = catalog['items'][0]
    detail = await client.get(f"{API}/products/{first['id']}", headers=headers())
    assert detail.status_code == 200
    assert detail.json() == first
    assert (await client.get(f"{API}/products/{first['id']}")).status_code == 401
    assert (await client.get(f'{API}/products', params={'category': 'sport'})).status_code == 401
    assert (await client.get(f'{API}/products/99999999', headers=headers())).status_code == 404
    assert (await client.get(f'{API}/products', params={'category': 'unknown'}, headers=headers())).status_code == 400
