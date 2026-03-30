"""V1 API router — mounts all endpoint sub-routers."""

from fastapi import APIRouter

from sportshub.api.v1 import competitions, events, health, players, teams

v1_router = APIRouter()

# Health endpoint has no auth dependency (set at individual router level)
v1_router.include_router(health.router)

# All other endpoints require API key auth (set at individual router level)
v1_router.include_router(events.router)
v1_router.include_router(teams.router)
v1_router.include_router(players.router)
v1_router.include_router(competitions.router)
