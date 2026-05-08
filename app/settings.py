from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_ENV: str = "dev"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8015

    DATABASE_PATH: str = "state/supervisor.db"
    DEFAULT_PROJECT_ROOT: str = str(Path.cwd())

    EXECUTION_MODE: str = "safe"  # safe | dry_run | real
    MAX_CONCURRENT_TASKS: int = 3
    TASK_DEFAULT_TIMEOUT_SECONDS: int = 60
    TASK_MAX_RETRIES: int = 2
    TASK_RETRY_BACKOFF_SECONDS: float = 1.5

    ALLOWED_SHELL_COMMANDS: str = (
        "python,py,pip,uvicorn,git,dir,echo,type,Get-ChildItem,Get-Content,"
        "Test-NetConnection,Invoke-RestMethod,curl,powershell,pwsh"
    )
    BLOCKED_SHELL_PATTERNS: str = (
        "del ,rm ,shutdown,format ,diskpart,reg delete,rmdir /s,"
        "Remove-Item -Recurse,Remove-Item -Force"
    )

    HTTP_DEFAULT_TIMEOUT_SECONDS: int = 45
    HTTP_VERIFY_SSL: bool = True

    N8N_BASE_URL: str = "http://127.0.0.1:5678"
    N8N_DEFAULT_WEBHOOK_PATH: str = "/webhook/jarvis-execution"

    LLM_MODE: str = "ollama"  # ollama | openai | disabled
    LLM_BASE_URL: str = "http://127.0.0.1:11434"
    LLM_MODEL: str = "llama3.2:latest"
    LLM_TIMEOUT_SECONDS: int = 120
    LLM_API_KEY: str = ""

    LOG_LEVEL: str = "INFO"

    @property
    def database_path(self) -> Path:
        path = Path(self.DATABASE_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def default_project_root(self) -> Path:
        path = Path(self.DEFAULT_PROJECT_ROOT).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def allowed_shell_commands(self) -> list[str]:
        return [x.strip().lower() for x in self.ALLOWED_SHELL_COMMANDS.split(",") if x.strip()]

    @property
    def blocked_shell_patterns(self) -> list[str]:
        return [x.strip().lower() for x in self.BLOCKED_SHELL_PATTERNS.split(",") if x.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()