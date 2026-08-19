from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from commerce_operations.api.errors import register_exception_handlers
from commerce_operations.api.health import router as health_router
from commerce_operations.api.middleware import RequestContextMiddleware
from commerce_operations.api.router import api_v1_router
from commerce_operations.config import Settings, get_settings
from commerce_operations.observability.logging import configure_logging
from commerce_operations.security import SecurityMiddleware


def create_app(settings: Settings | None = None, *, error_reporter=None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
    )
    application.state.settings = settings
    application.state.error_reporter = error_reporter
    application.add_middleware(SecurityMiddleware, settings=settings)
    application.add_middleware(RequestContextMiddleware)
    if settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    application.include_router(health_router)
    application.include_router(api_v1_router)
    register_exception_handlers(application)
    return application


app = create_app()
