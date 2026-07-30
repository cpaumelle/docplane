"""Single supported ASGI assembly for DocPlane."""
from fastapi import APIRouter, FastAPI

from app.agent_access_api import router as agent_access_router
from app.agent_api import router as monolithic_agent_router
from app.agent_contract_api import REPLACED_AGENT_PATHS, install_agent_contract, router as agent_contract_router
from app.agent_shortcuts_api import router as agent_shortcuts_router
from app.enhanced_discovery_api import DISCOVERY_PATH, router as enhanced_discovery_router
from app.event_api import router as event_router
from app.http_semantics import HeadAsGetMiddleware
from app.model_api import router as model_router
from app.observe_api import router as observe_router
from app.operation_contract_api import install_operation_contract, router as operation_contract_router
from app.operation_request_openapi import install_discriminated_operation_requests
from app.reorganisation_api import router as reorganisation_router
from app.system_api import router as system_router
from app.trust_api import router as trust_router
from app.work_api import router as work_router

agent_router = APIRouter()
agent_router.routes.extend(route for route in monolithic_agent_router.routes if getattr(route, "path", None) not in REPLACED_AGENT_PATHS)

contract_router = APIRouter()
contract_router.routes.extend(
    route for route in agent_contract_router.routes if getattr(route, "path", None) != DISCOVERY_PATH
)

app = FastAPI(
    title="DocPlane",
    version="1.0.0",
    description=("Documentation control plane with switchable managed/private-fabric admission, uniform contributor access, revision-bound direct publication, durable audit history and certified releases."),
)
app.add_middleware(HeadAsGetMiddleware)
app.include_router(system_router)
app.include_router(enhanced_discovery_router)
app.include_router(contract_router)
app.include_router(operation_contract_router)
app.include_router(agent_access_router)
app.include_router(agent_router)
app.include_router(agent_shortcuts_router)
app.include_router(event_router)
app.include_router(work_router)
app.include_router(model_router)
app.include_router(observe_router)
app.include_router(trust_router)
app.include_router(reorganisation_router)
install_agent_contract(app)
install_operation_contract(app)
install_discriminated_operation_requests(app)

__all__ = ["app"]
