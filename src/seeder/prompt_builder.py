"""Prompt construction for OCTO Product generation via Ollama.

Builds a system prompt that includes the OCTO Product JSON schema
(loaded from the ``octo-std/`` spec files) and explicit instructions
to generate entirely fictional data.
"""

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


# Default location of the split spec files
_OCTO_STD_DIR = Path(__file__).resolve().parent.parent.parent / "octo-std"


def _load_spec_schema(spec_file: str = "get-products.yaml") -> str:
    """Load an OCTO endpoint spec from ``octo-std/`` and format it for the LLM.

    Returns a YAML-formatted schema description string that replaces the
    previously hardcoded ``_SCHEMA_DESCRIPTION``.
    """
    path = _OCTO_STD_DIR / spec_file
    if not path.exists():
        logger.warning("Spec file not found: %s — falling back to empty schema", path)
        return "(schema file not found)"

    with open(path, "r", encoding="utf-8") as f:
        spec = yaml.safe_load(f)

    # Build a concise text representation the LLM can reason about
    endpoint = spec.get("endpoint", "unknown")
    summary = spec.get("summary", "")
    schema_yaml = yaml.dump(
        spec.get("response", {}), default_flow_style=False, sort_keys=False
    )

    return (
        f"The following is the OpenAPI response schema for {endpoint} ({summary}).\n"
        f"Use it as the authoritative reference for field names, types, and enums.\n\n"
        f"```yaml\n{schema_yaml}```"
    )


class PromptBuilder:
    """Builds LLM prompts for generating synthetic OCTO Product JSON."""

    def __init__(
        self,
        avg_slots_per_day: int = 3,
    ) -> None:
        self._avg_slots_per_day = avg_slots_per_day

    def build_prompt(self, error_hints: list[str] | None = None) -> str:
        """Build the full prompt for generating one OCTO Product.

        Args:
            error_hints: Optional list of validation error messages from
                previous failed attempts. These are injected as explicit
                constraints so the LLM avoids repeating the same mistakes.
        """
        parts: list[str] = []

        # System instruction
        parts.append(
            "You are a data generator for the OCTO Travel API standard. "
            "Your task is to generate exactly ONE valid OCTO Product JSON object."
        )

        # Schema — loaded from octo-std/ spec files
        parts.append("\n## OCTO Product JSON Schema\n")
        parts.append(_load_spec_schema("get-products.yaml"))

        # Generation instructions
        parts.append("\n## Generation Instructions\n")
        parts.append(
            "Generate exactly ONE fictional OCTO Product JSON object following "
            "these rules:\n\n"
            "1. Generate fresh UUID v4 values for the product id, each option "
            "id, and each unit id.\n"
            "2. The product MUST have at least one Option, and each Option "
            "MUST have at least one Unit.\n"
            "3. Use realistic but fictional product names (e.g. invented tour "
            "names, fictional attractions, made-up cities).\n"
            "4. Choose a valid availabilityType: either 'START_TIME' or "
            "'OPENING_HOURS'.\n"
            f"5. For START_TIME products, include approximately "
            f"{self._avg_slots_per_day} entries in availabilityLocalStartTimes "
            f"(vary between {max(1, self._avg_slots_per_day - 1)} and "
            f"{self._avg_slots_per_day + 1}). "
            "For OPENING_HOURS products, leave availabilityLocalStartTimes "
            "as an empty array.\n"
            "6. Use realistic age ranges in unit restrictions (e.g. Adult "
            "18-64, Child 5-12, Infant 0-4).\n"
            "7. Set exactly one option as default (default: true).\n"
            "8. Include a mix of unit types from: ADULT, YOUTH, CHILD, "
            "INFANT, FAMILY, SENIOR, STUDENT, MILITARY, OTHER.\n"
            "9. Return ONLY the raw JSON object. No markdown, no "
            "explanation, no wrapping.\n"
        )

        # Error hints from previous failed attempts
        if error_hints:
            parts.append("\n## Previous Attempt Errors — MUST FIX\n")
            parts.append(
                "Your previous attempts were rejected due to the following "
                "validation errors. You MUST avoid these mistakes:\n"
            )
            for i, hint in enumerate(error_hints, 1):
                parts.append(f"{i}. {hint}")

        return "\n".join(parts)
