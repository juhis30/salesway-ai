from fastapi import APIRouter

from app.api.v1 import analytics, auth, catalog, imports, insights, organization

api_router = APIRouter()
api_router.include_router(insights.router)
api_router.include_router(auth.router)
api_router.include_router(organization.router)
api_router.include_router(catalog.router)
api_router.include_router(imports.router)
api_router.include_router(analytics.router)
