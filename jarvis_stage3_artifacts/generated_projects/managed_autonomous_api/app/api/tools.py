from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter
from app.services.tool_gateway import gateway

router = APIRouter(prefix="/api/tools", tags=["tools"])


class ShellRequest(BaseModel):
    command: str


class HttpGetRequest(BaseModel):
    url: str


class FileReadRequest(BaseModel):
    path: str


class FileWriteRequest(BaseModel):
    path: str
    content: str


@router.get("/health")
def tools_health():
    return gateway.health()


@router.get("/config")
def tools_config():
    return gateway.config_snapshot()


@router.post("/shell")
def tools_shell(req: ShellRequest):
    return gateway.execute_shell(req.command)


@router.post("/http/get")
def tools_http_get(req: HttpGetRequest):
    return gateway.http_get(req.url)


@router.post("/filesystem/read")
def tools_file_read(req: FileReadRequest):
    return gateway.file_read(req.path)


@router.post("/filesystem/write")
def tools_file_write(req: FileWriteRequest):
    return gateway.file_write(req.path, req.content)
