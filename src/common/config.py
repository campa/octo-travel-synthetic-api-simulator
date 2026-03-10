"""Configuration management using Pydantic Settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_prefix="OTAS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8080

    # Seeder
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:14b"
    product_count: int = 10
    max_retries: int = 3
    avg_slots_per_day: int = 3

    # State
    availability_days: int = 90

    # Telemetry
    otlp_endpoint: str = "localhost:5081"
    otlp_user: str = "admin@otas.local"
    otlp_password: str = "admin"
    service_name: str = "otas"

    # Seed persistence
    seed_file: str = "seed_data.json"


settings = Settings()
