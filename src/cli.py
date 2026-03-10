"""CLI entrypoint for the OCTO Travel Mock API Server.

Parses command-line arguments, builds settings, seeds data (or loads from file),
populates the state manager, and starts the FastAPI server.
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import uvicorn

from common.config import Settings
from models.product import Product
from seeder.generator import ProductGenerator
from seeder.ollama_client import OllamaClient, SeedingFailedError
from seeder.prompt_builder import PromptBuilder
from seeder.sample_index import RealSamplesIndex
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
        "--availability-days", type=int, default=None,
        help="Days of availability to generate (default: 90)",
    )
    parser.add_argument(
        "--avg-slots-per-day", type=int, default=None,
        help="Avg time slots per day for START_TIME products (default: 3)",
    )
    parser.add_argument(
        "--seed-file", default=None,
        help="Path to seed data JSON file (default: seed_data.json)",
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
        "availability_days": "availability_days",
        "avg_slots_per_day": "avg_slots_per_day",
        "seed_file": "seed_file",
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
    )
    prompt_builder = PromptBuilder(
        samples_dir="real-samples",
        avg_slots_per_day=settings.avg_slots_per_day,
    )
    sample_index = RealSamplesIndex(samples_dir="real-samples")
    generator = ProductGenerator(
        ollama_client=ollama_client,
        prompt_builder=prompt_builder,
        sample_index=sample_index,
        max_retries=settings.max_retries,
        telemetry=telemetry,
    )
    return await generator.generate_products(settings.product_count)


def _load_seed_file(seed_file: str) -> list[Product]:
    """Load products from a JSON seed file."""
    path = Path(seed_file)
    if not path.exists():
        logger.error("Seed file not found: %s", seed_file)
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return [Product.model_validate(item) for item in data]


def _dump_seed_file(products: list[Product], seed_file: str) -> None:
    """Write products to a JSON seed file."""
    data = [p.model_dump(by_alias=True) for p in products]
    with open(seed_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("Seed data written to %s", seed_file)


def _count_entities(products: list[Product]) -> tuple[int, int, int]:
    """Return (product_count, option_count, unit_count)."""
    options = sum(len(p.options) for p in products)
    units = sum(len(u.units) for p in products for u in p.options)
    return len(products), options, units


def main() -> None:
    """Main entry point: parse args → seed → serve."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    args = parse_args()
    settings = _apply_cli_overrides(Settings(), args)

    # Init telemetry
    telemetry = init_telemetry(settings)

    # Seed or load products
    if args.skip_seed:
        logger.info("Loading seed data from %s", settings.seed_file)
        products = _load_seed_file(settings.seed_file)
    else:
        logger.info("Starting Ollama seeder (product_count=%d)", settings.product_count)
        try:
            products = asyncio.run(_run_seeder(settings, telemetry))
        except SeedingFailedError as exc:
            logger.error("Seeding failed: %s", exc)
            sys.exit(1)

        if args.dump_seed:
            _dump_seed_file(products, settings.seed_file)

    # Load into state manager (triggers availability generation)
    state = StateManager(
        availability_days=settings.availability_days,
        telemetry=telemetry,
    )
    state.load_products(products)

    p_count, o_count, u_count = _count_entities(products)
    logger.info(
        "State loaded: %d Products, %d Options, %d Units",
        p_count, o_count, u_count,
    )

    # Create and start FastAPI app
    app = create_app(state=state, settings=settings, telemetry=telemetry)
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
