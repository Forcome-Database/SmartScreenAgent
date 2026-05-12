from fastapi import FastAPI
from backend.app.routers import auth as auth_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="SmartScreenAgent",
        version="0.1.0",
        description="AI 简历筛选服务",
    )
    app.include_router(auth_router.router)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "smartscreen-agent", "status": "ok"}

    return app


app = create_app()
