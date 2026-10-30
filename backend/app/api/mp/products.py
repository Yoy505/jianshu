"""小程序 · 健康商城（浏览骨架，status=coming）。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, pagination_params
from app.models import MpProduct
from app.schemas.common import Page
from app.schemas.mp import ProductOut

router = APIRouter()

CATEGORIES = ("sport", "fsmp", "food", "functional", "package", "device")


@router.get("/products", response_model=Page[ProductOut])
async def list_products(
    category: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
) -> Page[ProductOut]:
    page, page_size = pagination
    if category is not None and category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="商品分类仅支持 sport、fsmp、food、functional、package 或 device")

    filters = [MpProduct.status == "coming"]
    if category is not None:
        filters.append(MpProduct.category == category)

    total = await db.scalar(select(func.count()).select_from(MpProduct).where(*filters))
    rows = (
        await db.execute(
            select(MpProduct)
            .where(*filters)
            .order_by(MpProduct.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    return Page(
        items=[
            ProductOut(
                id=p.id,
                category=p.category,
                name=p.name,
                desc=p.desc,
                price=p.price,
                status=p.status,
            )
            for p in rows
        ],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


@router.get("/products/{product_id}", response_model=ProductOut)
async def get_product(
    product_id: int, db: AsyncSession = Depends(get_db),
) -> ProductOut:
    product = await db.get(MpProduct, product_id)
    if product is None or product.status != "coming":
        raise HTTPException(status_code=404, detail="商品不存在或已下架")
    return ProductOut(
        id=product.id, category=product.category, name=product.name,
        desc=product.desc, price=product.price, status=product.status,
    )
