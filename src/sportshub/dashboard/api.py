"""Dashboard JSON API endpoints."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.api.dependencies import get_db
from sportshub.dashboard.service import DashboardService

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _get_service(
    request: Request, session: AsyncSession = Depends(get_db)
) -> DashboardService:
    start_time = getattr(request.app.state, "start_time", None)
    return DashboardService(session, start_time=start_time)


def _get_circuit_breaker(request: Request):
    return getattr(request.app.state, "circuit_breaker", None)


@router.get("/overview")
async def get_overview(request: Request, session: AsyncSession = Depends(get_db)):
    svc = _get_service(request, session)
    return await svc.get_system_overview()


@router.get("/sources")
async def get_sources(request: Request, session: AsyncSession = Depends(get_db)):
    svc = _get_service(request, session)
    cb = _get_circuit_breaker(request)
    return await svc.get_source_statuses(circuit_breaker=cb)


@router.get("/ingestion")
async def get_ingestion(
    request: Request, session: AsyncSession = Depends(get_db), hours: int = 24
):
    svc = _get_service(request, session)
    return await svc.get_ingestion_timeline(hours=hours)


@router.get("/quality")
async def get_quality(request: Request, session: AsyncSession = Depends(get_db)):
    svc = _get_service(request, session)
    return await svc.get_data_quality()


@router.get("/alerts")
async def get_alerts(request: Request, session: AsyncSession = Depends(get_db)):
    svc = _get_service(request, session)
    cb = _get_circuit_breaker(request)
    return await svc.get_alerts(circuit_breaker=cb)


@router.get("/violations")
async def get_violations(request: Request, session: AsyncSession = Depends(get_db)):
    svc = _get_service(request, session)
    return await svc.get_constraint_violations()


@router.get("/reconciliation")
async def get_reconciliation(
    request: Request, session: AsyncSession = Depends(get_db), days: int = 30
):
    svc = _get_service(request, session)
    return await svc.get_reconciliation_stats(days=days)


@router.get("/llm")
async def get_llm_effectiveness(
    request: Request, session: AsyncSession = Depends(get_db), hours: int = 168
):
    svc = _get_service(request, session)
    return await svc.get_llm_effectiveness(hours=hours)


@router.get("/reliability")
async def get_reliability(
    request: Request, session: AsyncSession = Depends(get_db)
):
    svc = _get_service(request, session)
    return await svc.get_source_reliability()


@router.get("/reference-data")
async def get_reference_data(request: Request, session: AsyncSession = Depends(get_db)):
    svc = _get_service(request, session)
    return svc.get_reference_data_inventory()


@router.get("/script-activity")
async def get_script_activity(request: Request, session: AsyncSession = Depends(get_db)):
    svc = _get_service(request, session)
    return svc.get_script_activity()


@router.get("/completeness")
async def get_completeness(request: Request, session: AsyncSession = Depends(get_db)):
    svc = _get_service(request, session)
    return await svc.get_data_completeness()
