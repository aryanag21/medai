from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo-root .env (shared with docker-compose.yml's own variable substitution — see
# docs/DATABASE.md). Resolved relative to this file, not the process's cwd: pydantic-settings'
# default `env_file=".env"` is cwd-relative, which silently found nothing when the app was run
# from backend/ (the documented way to run it) while .env lived one level up at the repo root.
_REPO_ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

# Known-insecure dev-only placeholder values (also in .env.example) — named here, not just
# inlined as field defaults, so the startup guard below checks the exact same literals rather
# than risking drift between "the default" and "the value we refuse to run with".
_DEV_DEFAULT_JWT_SECRET_KEY = "change-me-in-real-environments-min-32-bytes-long"
_DEV_DEFAULT_FIELD_ENCRYPTION_KEY = "kgcgyGwg_TOkwTOdpCClu340NKpRIVU15x2EeNDDtqg="


class Settings(BaseSettings):
    """Application settings, read only from environment variables (.env in dev).

    Per medai_spec.yaml security.minimum_requirements: secrets_outside_source_code,
    environment_variables_for_credentials. Never hard-code credentials here.
    """

    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT_ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    environment: str = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://medai:medai@localhost:5432/medai"

    jwt_secret_key: str = _DEV_DEFAULT_JWT_SECRET_KEY
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 525600
    jwt_refresh_token_expire_days: int = 7

    # Application-level field encryption key for sensitive columns (Fernet key).
    # DEV-ONLY DEFAULT — never use this value outside local development.
    # Generate a real one with: from cryptography.fernet import Fernet; Fernet.generate_key()
    field_encryption_key: str = _DEV_DEFAULT_FIELD_ENCRYPTION_KEY

    # LLMProvider (architecture.provider_interfaces) — active concrete choice: Gemini. Empty
    # key means "not configured"; app/providers/llm/gemini_provider.py fails closed
    # (LLMNotConfiguredError) rather than fabricating a response. See docs/KNOWN_LIMITATIONS.md.
    gemini_api_key: str = ""
    # Free-tier quota (20 requests/day, hit during Phase 8 testing) is tracked per model, not
    # per key — switching model id buys a fresh quota, it isn't a more generous one. Change
    # any time via .env; a paid tier removes this ceiling entirely.
    gemini_model: str = "gemini-2.0-flash"

    # OpenAIProvider (app/providers/llm/openai_provider.py) is kept as a second working
    # LLMProvider implementation but is not selected by get_llm_provider() — these are unused
    # unless that's changed back.
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    @model_validator(mode="after")
    def _refuse_insecure_defaults_outside_development(self) -> "Settings":
        """Phase 13 (security_hardening, secrets_management): the JWT signing key and the
        field-encryption key both ship with well-known, publicly-visible dev-only placeholder
        values (also in .env.example) so local setup works out of the box. Outside
        `environment=="development"`, silently running with either default would be a real
        vulnerability — anyone who has read this (public) source could forge valid JWTs for any
        user, or decrypt every "encrypted at rest" column in the database. Fail loudly at
        startup instead of silently serving traffic with a known-broken secret.
        """

        if self.environment == "development":
            return self

        insecure = []
        if self.jwt_secret_key == _DEV_DEFAULT_JWT_SECRET_KEY:
            insecure.append("JWT_SECRET_KEY")
        if self.field_encryption_key == _DEV_DEFAULT_FIELD_ENCRYPTION_KEY:
            insecure.append("FIELD_ENCRYPTION_KEY")

        if insecure:
            raise ValueError(
                f"Refusing to start with environment={self.environment!r} while "
                f"{', '.join(insecure)} still {'has' if len(insecure) == 1 else 'have'} the "
                "dev-only placeholder value from .env.example. Set a real, unique value "
                "(security.minimum_requirements: secrets_outside_source_code)."
            )
        return self


settings = Settings()
