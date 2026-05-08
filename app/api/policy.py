from fastapi import APIRouter

from app.services.policy import get_policy
from app.services.tool_registry import get_tool_registry

router = APIRouter()

@router.get("/policy")
def policy():
    return {
        "policy": get_policy(),
        "tools": get_tool_registry(),
    }