from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import get_settings
from backend.app.logging_config import configure_logging
from backend.app.middleware import AccessLogMiddleware
from backend.app.routers import auth as auth_router
from backend.app.routers import candidates as candidates_router
from backend.app.routers import candidates_read as candidates_read_router
from backend.app.routers import health as health_router


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    app = FastAPI(title="SmartScreenAgent", version="0.1.0")

    # CORS — P3 Next.js 前端跨域调用
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # access log + trace id
    app.add_middleware(AccessLogMiddleware)

    app.include_router(health_router.router)
    app.include_router(auth_router.router)
    app.include_router(candidates_router.router)
    app.include_router(candidates_read_router.router)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "smartscreen-agent", "status": "ok"}

    return app


app = create_app()
