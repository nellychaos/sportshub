"""Dashboard HTML views with HTMX support."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.api.dependencies import get_db
from sportshub.dashboard.service import DashboardService

router = APIRouter(tags=["dashboard-views"])


def _get_service(request: Request, session: AsyncSession) -> DashboardService:
    start_time = getattr(request.app.state, "start_time", None)
    return DashboardService(session, start_time=start_time)


def _get_circuit_breaker(request: Request):
    return getattr(request.app.state, "circuit_breaker", None)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_index(
    request: Request, session: AsyncSession = Depends(get_db)
):
    svc = _get_service(request, session)
    cb = _get_circuit_breaker(request)
    templates = request.app.state.templates

    overview = await svc.get_system_overview()
    sources = await svc.get_source_statuses(circuit_breaker=cb)
    ingestion = await svc.get_ingestion_timeline(hours=24)
    quality = await svc.get_data_quality()
    alerts = await svc.get_alerts(circuit_breaker=cb)
    violations = await svc.get_constraint_violations()
    llm = await svc.get_llm_effectiveness(hours=168)
    reconciliation = await svc.get_reconciliation_stats(days=30)
    reliability = await svc.get_source_reliability()
    reference_data = svc.get_reference_data_inventory()
    script_activity = svc.get_script_activity()
    completeness = await svc.get_data_completeness()
    scorecard = svc.get_thesis_scorecard()
    betting = await svc.get_betting_coverage()

    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {
            "overview": overview,
            "sources": sources,
            "ingestion": ingestion,
            "quality": quality,
            "alerts": alerts,
            "violations": violations,
            "llm": llm,
            "reconciliation": reconciliation,
            "reliability": reliability,
            "reference_data": reference_data,
            "script_activity": script_activity,
            "completeness": completeness,
            "scorecard": scorecard,
            "betting": betting,
        },
    )


@router.get("/dashboard/partials/status", response_class=HTMLResponse)
async def partial_status(
    request: Request, session: AsyncSession = Depends(get_db)
):
    svc = _get_service(request, session)
    templates = request.app.state.templates
    overview = await svc.get_system_overview()
    return templates.TemplateResponse(
        request,
        "dashboard/partials/status_banner.html",
        {"overview": overview},
    )


@router.get("/dashboard/partials/sources", response_class=HTMLResponse)
async def partial_sources(
    request: Request, session: AsyncSession = Depends(get_db)
):
    svc = _get_service(request, session)
    cb = _get_circuit_breaker(request)
    templates = request.app.state.templates
    sources = await svc.get_source_statuses(circuit_breaker=cb)
    return templates.TemplateResponse(
        request,
        "dashboard/partials/source_cards.html",
        {"sources": sources},
    )


@router.get("/dashboard/partials/ingestion", response_class=HTMLResponse)
async def partial_ingestion(
    request: Request, session: AsyncSession = Depends(get_db)
):
    svc = _get_service(request, session)
    templates = request.app.state.templates
    ingestion = await svc.get_ingestion_timeline(hours=24)
    return templates.TemplateResponse(
        request,
        "dashboard/partials/ingestion_table.html",
        {"ingestion": ingestion},
    )


@router.get("/dashboard/partials/alerts", response_class=HTMLResponse)
async def partial_alerts(
    request: Request, session: AsyncSession = Depends(get_db)
):
    svc = _get_service(request, session)
    cb = _get_circuit_breaker(request)
    templates = request.app.state.templates
    alerts = await svc.get_alerts(circuit_breaker=cb)
    return templates.TemplateResponse(
        request,
        "dashboard/partials/alerts.html",
        {"alerts": alerts},
    )


@router.get("/dashboard/partials/violations", response_class=HTMLResponse)
async def partial_violations(
    request: Request, session: AsyncSession = Depends(get_db)
):
    svc = _get_service(request, session)
    templates = request.app.state.templates
    violations = await svc.get_constraint_violations()
    return templates.TemplateResponse(
        request,
        "dashboard/partials/violations.html",
        {"violations": violations},
    )


@router.get("/dashboard/partials/reconciliation", response_class=HTMLResponse)
async def partial_reconciliation(
    request: Request, session: AsyncSession = Depends(get_db)
):
    svc = _get_service(request, session)
    templates = request.app.state.templates
    reconciliation = await svc.get_reconciliation_stats(days=30)
    return templates.TemplateResponse(
        request,
        "dashboard/partials/reconciliation.html",
        {"reconciliation": reconciliation},
    )


@router.get("/dashboard/partials/llm", response_class=HTMLResponse)
async def partial_llm(
    request: Request, session: AsyncSession = Depends(get_db)
):
    svc = _get_service(request, session)
    templates = request.app.state.templates
    llm = await svc.get_llm_effectiveness(hours=168)
    return templates.TemplateResponse(
        request,
        "dashboard/partials/llm_effectiveness.html",
        {"llm": llm},
    )


@router.get("/dashboard/partials/reliability", response_class=HTMLResponse)
async def partial_reliability(
    request: Request, session: AsyncSession = Depends(get_db)
):
    svc = _get_service(request, session)
    templates = request.app.state.templates
    reliability = await svc.get_source_reliability()
    return templates.TemplateResponse(
        request,
        "dashboard/partials/reliability.html",
        {"reliability": reliability},
    )


@router.get("/dashboard/partials/reference-data", response_class=HTMLResponse)
async def partial_reference_data(
    request: Request, session: AsyncSession = Depends(get_db)
):
    svc = _get_service(request, session)
    templates = request.app.state.templates
    reference_data = svc.get_reference_data_inventory()
    return templates.TemplateResponse(
        request,
        "dashboard/partials/reference_data.html",
        {"reference_data": reference_data},
    )


@router.get("/dashboard/partials/script-activity", response_class=HTMLResponse)
async def partial_script_activity(
    request: Request, session: AsyncSession = Depends(get_db)
):
    svc = _get_service(request, session)
    templates = request.app.state.templates
    script_activity = svc.get_script_activity()
    return templates.TemplateResponse(
        request,
        "dashboard/partials/script_activity.html",
        {"script_activity": script_activity},
    )


@router.get("/dashboard/partials/completeness", response_class=HTMLResponse)
async def partial_completeness(
    request: Request, session: AsyncSession = Depends(get_db)
):
    svc = _get_service(request, session)
    templates = request.app.state.templates
    completeness = await svc.get_data_completeness()
    return templates.TemplateResponse(
        request,
        "dashboard/partials/data_completeness.html",
        {"completeness": completeness},
    )


@router.get("/dashboard/partials/betting", response_class=HTMLResponse)
async def partial_betting(
    request: Request, session: AsyncSession = Depends(get_db)
):
    svc = _get_service(request, session)
    templates = request.app.state.templates
    betting = await svc.get_betting_coverage()
    return templates.TemplateResponse(
        request,
        "dashboard/partials/betting_coverage.html",
        {"betting": betting},
    )
