from typing import List
from pydantic import BaseSettings, PostgresDsn, validator


class Settings(BaseSettings):
    # Application
    PROJECT_NAME: str = "Enterprise SaaS Boilerplate"
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # Security
    SECRET_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    ALGORITHM: str = "HS256"

    # Database
    DATABASE_URL: PostgresDsn
    TEST_DATABASE_URL: PostgresDsn | None = None

    # CORS
    ALLOWED_HOSTS: List[str] = ["*"]
    FRONTEND_URL: str = "http://localhost:3000"

    @validator("ALLOWED_HOSTS", pre=True)
    def assemble_cors_origins(cls, v):
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, (list, str)):
            return v
        raise ValueError("ALLOWED_HOSTS must be a string or list")

    # Stripe
    STRIPE_PUBLISHABLE_KEY: str | None = None
    STRIPE_SECRET_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: str | None = None

    # Email (for future use)
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()