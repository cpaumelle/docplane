"""Single supported ASGI assembly for DocPlane."""
from fastapi import FastAPI

from app.agent_api import router as agent_router
from app.event_api import router as event_router
from app.reorganisation_api import router as reorganisation_router
from app.system_api import router as system_router
from app.trust_api import router as trust_router
from app.work_api import router as work_router

app = FastAPI(
    title="DocPlane",
    version="1.0.0",
    description=(
        "Private-fabric documentation control plane with uniform contributor access, "
        "revision-bound direct publication, durable audit history and certified releases."
    ),
)

app.include_router(system_router)
app.include_router(agent_router)
app.include_router(event_router)
app.include_router(work_router)
app.include_router(trust_router)
app.include_router(reorganisation_router)

__all__ = ["app"]
