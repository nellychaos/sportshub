"""Demo showcase page -- visual display of multi-source sports data."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from sportshub.demo.service import DemoService

router = APIRouter(tags=["demo"])


@router.get("/demo", response_class=HTMLResponse)
async def demo_page(request: Request):
    templates = request.app.state.templates
    svc = DemoService()
    data = svc.get_demo_data()

    return templates.TemplateResponse(
        request,
        "demo/index.html",
        {"data": data},
    )
