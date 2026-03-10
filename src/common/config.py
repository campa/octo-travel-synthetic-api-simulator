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
    log_level: str = "INFO"

    # Per-module log levels (like log4j logger categories).
    # Set via env: OTAS_LOG_LEVEL_SEEDER_GENERATOR=DEBUG etc.
    log_level_seeder_generator: str = ""
    log_level_seeder_prompt_builder: str = ""
    log_level_seeder_ollama_client: str = ""
    log_level_server_app: str = ""
    log_level_server_middleware: str = ""
    log_level_state_manager: str = ""
    log_level_telemetry_setup: str = ""

    def build_logging_config(self) -> dict:
        """Build a logging.config.dictConfig-compatible dict.

        Per-module levels override the root level when set (non-empty).
        """
        module_map = {
            "seeder.generator": self.log_level_seeder_generator,
            "seeder.prompt_builder": self.log_level_seeder_prompt_builder,
            "seeder.ollama_client": self.log_level_seeder_ollama_client,
            "server.app": self.log_level_server_app,
            "server.middleware": self.log_level_server_middleware,
            "state.manager": self.log_level_state_manager,
            "telemetry.setup": self.log_level_telemetry_setup,
        }

        loggers = {}
        for name, level in module_map.items():
            if level:
                loggers[name] = {"level": level.upper()}

        return {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "standard",
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {
                "level": self.log_level.upper(),
                "handlers": ["console"],
            },
            "loggers": loggers,
        }

    # Seeder
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:14b"
    product_count: int = 3
    max_retries: int = 3
    avg_slots_per_day: int = 3

    # Telemetry
    otlp_endpoint: str = "localhost:5081"
    otlp_user: str = "admin@otas.local"
    otlp_password: str = "admin"
    service_name: str = "otas"

    # Seed persistence
    seed_file: str = "seed_data.json"


settings = Settings()
