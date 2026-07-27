"""Assembled DocPlane dashboard application."""
from fastapi.responses import RedirectResponse

from .app import app
from .authoring import router as authoring_router

app.include_router(authoring_router)


@app.get("/authoring", include_in_schema=False)
def authoring_workspace() -> RedirectResponse:
    """Preserve the original URL while using the unified dashboard shell."""
    return RedirectResponse(url="/?view=authoring", status_code=307)


__all__ = ["app"]
