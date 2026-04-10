"""CLI entrypoint for the OCTO Travel Mock API Server.

Parses command-line arguments, builds settings, seeds data (or loads from file),
populates the state manager, and starts the FastAPI server.
"""

import argparse
import asyncio
import json
import logging
import logging.config
import sys
from pathlib import Path

import uvicorn

from common.config import Settings
from models.product import Product
from seeder.availability_generator import AvailabilityGenerator, load_products_from_seed
from seeder.availability_prompt_builder import AvailabilityPromptBuilder
from seeder.generator import ProductGenerator
from seeder.ollama_client import OllamaClient, SeedingFailedError
from seeder.prompt_builder import PromptBuilder
from server.app import create_app
from state.manager import StateManager
from telemetry.setup import init_telemetry

logger = logging.getLogger("otas.cli")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments. None values mean 'not provided' (use Settings default)."""
    parser = argparse.ArgumentParser(description="OCTO Travel Mock API Server")
    parser.add_argument("--host", default=None, help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default: 8080)")
    parser.add_argument(
        "--skip-seed", action="store_true",
        help="Load seed from file instead of invoking Ollama",
    )
    parser.add_argument(
        "--dump-seed", action="store_true",
        help="Write generated seed data to file after seeding",
    )
    parser.add_argument(
        "--product-count", type=int, default=None,
        help="Number of products to generate (default: 10)",
    )
    parser.add_argument(
        "--max-retries", type=int, default=None,
        help="Max Ollama retry attempts per product (default: 3)",
    )
    parser.add_argument(
        "--avg-slots-per-day", type=int, default=None,
        help="Avg time slots per day for START_TIME products (default: 3)",
    )
    parser.add_argument(
        "--seed-product-file", default=None,
        help="Path to product seed data JSON file (default: seed_product_data.json)",
    )
    parser.add_argument(
        "--availability-window-days", type=int, default=None,
        help="Number of days to generate availability for (default: 5)",
    )
    parser.add_argument(
        "--availability-start-date", default=None,
        help="Start date for availability generation, YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--availability-max-slots-per-week", type=int, default=None,
        help="Max available slots per 7-day week (default: 5)",
    )
    parser.add_argument(
        "--skip-availability", action="store_true",
        help="Skip availability generation (load from file if --skip-seed)",
    )
    parser.add_argument(
        "--seed-availability-file", default=None,
        help="Path to availability seed data JSON file (default: seed_availability_data.json)",
    )
    return parser.parse_args()


def _apply_cli_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    """Apply non-None CLI args over the Settings instance."""
    overrides: dict[str, object] = {}
    mapping = {
        "host": "host",
        "port": "port",
        "product_count": "product_count",
        "max_retries": "max_retries",
        "avg_slots_per_day": "avg_slots_per_day",
        "seed_product_file": "seed_product_file",
        "availability_window_days": "availability_window_days",
        "availability_start_date": "availability_start_date",
        "availability_max_slots_per_week": "availability_max_slots_per_week",
        "seed_availability_file": "seed_availability_file",
    }
    for arg_name, setting_name in mapping.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            overrides[setting_name] = value

    if overrides:
        return settings.model_copy(update=overrides)
    return settings


async def _run_seeder(settings: Settings, telemetry) -> list[Product]:
    """Run the Ollama-based seeder pipeline and return generated products."""
    ollama_client = OllamaClient(
        ollama_url=settings.ollama_url,
        model=settings.ollama_model,
        temperature=settings.ollama_temperature,
    )
    prompt_builder = PromptBuilder(
        avg_slots_per_day=settings.avg_slots_per_day,
    )
    generator = ProductGenerator(
        ollama_client=ollama_client,
        prompt_builder=prompt_builder,
        max_retries=settings.max_retries,
        telemetry=telemetry,
    )
    return await generator.generate_products(settings.product_count)


async def _run_availability_seeder(
    settings: Settings, products_data: list[dict], telemetry
) -> dict[str, dict[str, list[dict]]]:
    """Run the availability calendar seeder pipeline."""
    ollama_client = OllamaClient(
        ollama_url=settings.ollama_url,
        model=settings.ollama_model,
        temperature=settings.ollama_temperature,
    )
    prompt_builder = AvailabilityPromptBuilder()
    generator = AvailabilityGenerator(
        ollama_client=ollama_client,
        prompt_builder=prompt_builder,
        max_retries=settings.max_retries,
        availability_window_days=settings.availability_window_days,
        availability_start_date=settings.availability_start_date,
        max_slots_per_week=settings.availability_max_slots_per_week,
        telemetry=telemetry,
    )
    return await generator.generate_availability(products_data)


def _load_seed_product_file(seed_product_file: str) -> list[Product]:
    """Load products from a JSON seed file."""
    path = Path(seed_product_file)
    if not path.exists():
        logger.error("Seed product file not found: %s", seed_product_file)
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return [Product.model_validate(item) for item in data]


def _dump_seed_product_file(products: list[Product], seed_product_file: str) -> None:
    """Write products to a JSON seed file."""
    data = [p.model_dump(by_alias=True) for p in products]
    with open(seed_product_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("Seed product data written to %s", seed_product_file)


def _dump_seed_availability_file(
    availability: dict[str, dict[str, list[dict]]], filepath: str
) -> None:
    """Write availability calendar data to a JSON seed file."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(availability, f, indent=2, ensure_ascii=False)
    logger.info("Seed availability data written to %s", filepath)


def _load_seed_availability_file(filepath: str) -> dict[str, dict[str, list[dict]]]:
    """Load availability calendar data from a JSON seed file."""
    path = Path(filepath)
    if not path.exists():
        logger.error("Seed availability file not found: %s", filepath)
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _count_entities(products: list[Product]) -> tuple[int, int, int]:
    """Return (product_count, option_count, unit_count)."""
    options = sum(len(p.options) for p in products)
    units = sum(len(u.units) for p in products for u in p.options)
    return len(products), options, units


def main() -> None:
    """Main entry point: parse args → seed → serve."""
    args = parse_args()
    settings = _apply_cli_overrides(Settings(), args)

    logging.config.dictConfig(settings.build_logging_config())

    # Init telemetry
    telemetry = init_telemetry(settings)

    # Seed or load products
    if args.skip_seed:
        logger.info("Loading seed product data from %s", settings.seed_product_file)
        products = _load_seed_product_file(settings.seed_product_file)
    else:
        logger.info("Starting Ollama seeder (product_count=%d)", settings.product_count)
        try:
            products = asyncio.run(_run_seeder(settings, telemetry))
        except SeedingFailedError as exc:
            logger.error("Seeding failed: %s", exc)
            sys.exit(1)

        if args.dump_seed:
            _dump_seed_product_file(products, settings.seed_product_file)

    # Load into state manager
    state = StateManager(telemetry=telemetry)
    state.load_products(products)

    p_count, o_count, u_count = _count_entities(products)
    logger.info(
        "State loaded: %d Products, %d Options, %d Units",
        p_count, o_count, u_count,
    )

    # Generate or load availability
    if not args.skip_availability:
        # Build product dicts for the availability generator
        products_data = [p.model_dump(by_alias=True) for p in products]
        logger.info(
            "Starting availability seeder (window=%d days)",
            settings.availability_window_days,
        )
        try:
            availability = asyncio.run(
                _run_availability_seeder(settings, products_data, telemetry)
            )
        except SeedingFailedError as exc:
            logger.error("Availability seeding failed: %s", exc)
            sys.exit(1)

        if args.dump_seed:
            _dump_seed_availability_file(availability, settings.seed_availability_file)

        state.load_availability(availability)
    else:
        # Try to load availability from file
        avail_path = Path(settings.seed_availability_file)
        if avail_path.exists():
            logger.info("Loading seed availability data from %s", settings.seed_availability_file)
            availability = _load_seed_availability_file(settings.seed_availability_file)
            state.load_availability(availability)
        else:
            logger.warning("No seed availability file found at %s", settings.seed_availability_file)

    # Create and start FastAPI app
    app = create_app(state=state, settings=settings, telemetry=telemetry)
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
