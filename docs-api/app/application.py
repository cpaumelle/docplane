"""DocPlane ASGI application assembly.

The historical control-plane implementation remains in ``app.main`` while product surfaces are being
extracted into versioned routers. New containers and tests import this module so every supported endpoint
is assembled without forcing unrelated refactors into one change.
"""
from app.main import app
from app.agent_api import router as agent_router
from app.authoring_api import router as authoring_router
from app.event_api import router as event_router
from app.reorganisation_api import router as reorganisation_router
from app.trust_api import router as trust_router
from app.work_api import router as work_router

app.include_router(agent_router)
app.include_router(authoring_router)
app.include_router(event_router)
app.include_router(work_router)
app.include_router(trust_router)
app.include_router(reorganisation_router)

__all__ = ["app"]
