from typing import Annotated

from fastapi import APIRouter, Depends

from commerce_operations.api.approvals import router as approvals_router
from commerce_operations.api.approved_products import router as approved_products_router
from commerce_operations.api.inventory import router as inventory_router
from commerce_operations.api.orders import router as orders_router
from commerce_operations.api.pricing import router as pricing_router
from commerce_operations.api.procurement import router as procurement_router
from commerce_operations.api.schemas import ApiInfoResponse
from commerce_operations.config import Settings, get_settings

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(approvals_router)
api_v1_router.include_router(approved_products_router)
api_v1_router.include_router(procurement_router)
api_v1_router.include_router(inventory_router)
api_v1_router.include_router(orders_router)
api_v1_router.include_router(pricing_router)
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@api_v1_router.get("", response_model=ApiInfoResponse, include_in_schema=False)
@api_v1_router.get("/", response_model=ApiInfoResponse, summary="API version information")
def api_info(settings: SettingsDependency) -> ApiInfoResponse:
    return ApiInfoResponse(name=settings.app_name, version=settings.app_version)
