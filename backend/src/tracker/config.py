from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://tracker:tracker@localhost:5432/tracker"
    redis_url: str = "redis://localhost:6379/0"
    mlb_base_url: str = "https://statsapi.mlb.com"
    artifacts_dir: Path = Path(__file__).resolve().parents[2] / "artifacts"
    static_dir: Path | None = None
    stream_maxlen: int = 20_000
    stream_block_ms: int = 15_000
    finished_stream_ttl_seconds: int = 6 * 3600
    run_ingester: bool = False
    poll_floor_seconds: float = 2.0
    schedule_interval_seconds: float = 60.0
    full_frame_every: int = 100
    cors_origins: list[str] = ["http://localhost:5173"]

    @field_validator("database_url")
    @classmethod
    def _asyncpg_scheme(cls, v: str) -> str:
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+asyncpg://" + v[len(prefix) :]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
