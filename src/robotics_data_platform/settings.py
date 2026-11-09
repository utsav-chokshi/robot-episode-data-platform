"""Environment configuration; secrets must not appear in health responses."""
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RDP_", extra="ignore")

    database_url: SecretStr
    alembic_config: Path = Path("alembic.ini")
    raw_root: Path = Path("/raw")
    dataset_root: Path = Path("/datasets")
    evaluation_root: Path = Path('/evaluations')
