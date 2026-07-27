"""Assembled DocPlane dashboard application."""
from .app import app
from .authoring import router as authoring_router

app.include_router(authoring_router)

__all__ = ["app"]
