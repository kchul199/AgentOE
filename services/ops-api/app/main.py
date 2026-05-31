"""AgentOE 통합 운영 포탈 API — ops-api

포트: 8001 (ops-portal 에서 /ops-api 프록시 또는 직접 호출)
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import monitoring, config, sessions, kill_switch, scenarios

app = FastAPI(
    title="AgentOE Ops API",
    description="통합 운영 포탈 백엔드 — 모니터링 / 환경설정 / 상담이력 / 킬스위치 / 시나리오 관리",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://localhost:5173", "http://127.0.0.1:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PREFIX = "/api/v1"
app.include_router(monitoring.router, prefix=PREFIX)
app.include_router(config.router,     prefix=PREFIX)
app.include_router(sessions.router,   prefix=PREFIX)
app.include_router(kill_switch.router, prefix=PREFIX)
app.include_router(scenarios.router,  prefix=PREFIX)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "ops-api"}


@app.get(f"{PREFIX}/livez")
async def livez() -> dict:
    return {"status": "ok"}
