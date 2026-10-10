"""Application configuration module using Pydantic Settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """AegisOps system configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application Metadata
    app_name: str = Field(default="AegisOps", description="Application name")
    app_version: str = Field(default="0.1.0", description="Application semantic version")
    environment: Literal["development", "staging", "production", "test"] = Field(
        default="development", description="Deployment environment"
    )
    debug: bool = Field(default=False, description="Debug mode flag")
    api_v1_prefix: str = Field(default="/api/v1", description="API v1 routing prefix")
    host: str = Field(default="0.0.0.0", description="Server host bind address")
    port: int = Field(default=8000, description="Server port bind address")

    # Logging Configuration
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", description="Global logging level"
    )
    log_format: Literal["json", "console"] = Field(
        default="json", description="Structured JSON or human-readable console logging"
    )

    # Security & CORS
    secret_key: SecretStr = Field(
        default=SecretStr("change-me-to-a-secure-random-secret-key-at-least-32-chars"),
        description="Cryptographic secret key for token signing and security operations",
    )
    allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"],
        description="Allowed CORS origins",
    )

    # Database Configuration
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/aegisops",
        description="SQLAlchemy database connection URL",
    )
    db_pool_size: int = Field(default=10, ge=1, le=100, description="DB connection pool size")
    db_max_overflow: int = Field(
        default=20, ge=0, le=100, description="Max overflow connections beyond pool size"
    )
    db_pool_timeout: int = Field(
        default=30, ge=1, description="Database connection checkout timeout in seconds"
    )
    db_pool_recycle: int = Field(
        default=1800, ge=60, description="Recycle database connections after seconds"
    )
    db_echo: bool = Field(default=False, description="Echo raw SQL statements to logger")

    # Feature Flags
    enable_metrics: bool = Field(default=True, description="Enable Prometheus metrics scraping")
    enable_background_tasks: bool = Field(
        default=True, description="Enable background worker schedules"
    )

    # Phase 5 Agent & Investigation Configuration
    agent_max_tool_calls: int = Field(
        default=15, ge=1, le=50, description="Max tool calls per agent investigation run"
    )
    agent_max_iterations: int = Field(
        default=10, ge=1, le=30, description="Max state graph iteration steps per investigation"
    )
    agent_llm_provider: Literal["fake", "openai", "anthropic", "gemini"] = Field(
        default="fake", description="LLM provider for investigation reasoning"
    )
    agent_llm_model: str = Field(
        default="mock-reasoner-v1", description="Model identifier for configured LLM provider"
    )
    agent_llm_api_key: SecretStr = Field(
        default=SecretStr(""), description="API key for real LLM provider (empty for fake/offline)"
    )
    remediation_simulation_enabled: bool = Field(
        default=True, description="Allow in-process simulated remediation execution"
    )
    remediation_require_approval_by_default: bool = Field(
        default=True, description="Require operator approval for remediation actions by default"
    )

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_allowed_origins(cls, value: object) -> list[str]:
        """Support comma-separated strings as well as lists for ALLOWED_ORIGINS."""
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            return [origin.strip() for origin in stripped.split(",") if origin.strip()]
        if isinstance(value, list):
            return [str(v) for v in value]
        return []

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        """Validate database URL scheme."""
        allowed_schemes = ("postgresql", "postgresql+psycopg", "sqlite", "sqlite+aiosqlite")
        if not any(value.startswith(scheme) for scheme in allowed_schemes):
            raise ValueError(
                f"Unsupported database scheme in URL: '{value}'. Must be PostgreSQL or SQLite."
            )
        return value

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, value: SecretStr, info: object) -> SecretStr:
        """Enforce secret key minimum length."""
        secret_str = value.get_secret_value()
        if len(secret_str) < 16:
            raise ValueError("SECRET_KEY must be at least 16 characters long.")
        return value

    @property
    def is_production(self) -> bool:
        """Return True if running in production."""
        return self.environment == "production"

    @property
    def is_testing(self) -> bool:
        """Return True if running in test environment."""
        return self.environment == "test"

    @property
    def is_development(self) -> bool:
        """Return True if running in development."""
        return self.environment == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()


def clear_settings_cache() -> None:
    """Clear settings cache to allow dynamic environment reloads in tests."""
    get_settings.cache_clear()
