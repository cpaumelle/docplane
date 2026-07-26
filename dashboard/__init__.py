"""DocPlane operator dashboard package and route assembly."""
from . import app as app
from .authoring import router as authoring_router

app.app.include_router(authoring_router)

__all__ = ["app"]
