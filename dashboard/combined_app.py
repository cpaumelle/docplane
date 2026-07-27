"""Assembled DocPlane dashboard application."""
from pathlib import Path

from fastapi.responses import FileResponse

from .app import app
from .authoring import router as authoring_router

_STATIC = Path(__file__).resolve().parent / "static"
app.include_router(authoring_router)


@app.get("/authoring", include_in_schema=False)
def authoring_workspace():
    return FileResponse(_STATIC / "authoring.html")


__all__ = ["app"]
