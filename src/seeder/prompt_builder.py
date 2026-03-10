"""Prompt construction for OCTO Product generation via Ollama.

Reads real production sample files as few-shot examples and builds a system
prompt that includes the OCTO Product JSON schema, trimmed examples, and
explicit instructions to generate entirely fictional data.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Essential Product fields to keep in few-shot examples (trim verbose extras)
_PRODUCT_KEEP_KEYS = {
    "id", "internalName", "reference", "locale", "timeZone",
    "allowFreesale", "instantConfirmation", "instantDelivery",
    "availabilityRequired", "availabilityType", "deliveryFormats",
    "deliveryMethods", "redemptionMethod", "options",
}

_OPTION_KEEP_KEYS = {
    "id", "default", "internalName", "reference",
    "availabilityLocalStartTimes", "cancellationCutoff",
    "cancellationCutoffAmount", "cancellationCutoffUnit",
    "requiredContactFields", "restrictions", "units",
}

_UNIT_KEEP_KEYS = {
    "id", "internalName", "reference", "type",
    "requiredContactFields", "restrictions",
}

_SAMPLES_SUBDIR = "prodcuts-and-availability-calendar-responses"


def _trim_unit(unit: dict) -> dict:
    """Keep only essential Unit fields."""
    return {k: v for k, v in unit.items() if k in _UNIT_KEEP_KEYS}


def _trim_option(option: dict) -> dict:
    """Keep only essential Option fields, trimming nested units."""
    trimmed = {k: v for k, v in option.items() if k in _OPTION_KEEP_KEYS}
    if "units" in trimmed and isinstance(trimmed["units"], list):
        trimmed["units"] = [_trim_unit(u) for u in trimmed["units"]]
    return trimmed


def _trim_product(product: dict) -> dict:
    """Keep only essential Product fields, trimming nested options."""
    trimmed = {k: v for k, v in product.items() if k in _PRODUCT_KEEP_KEYS}
    if "options" in trimmed and isinstance(trimmed["options"], list):
        trimmed["options"] = [_trim_option(o) for o in trimmed["options"]]
    return trimmed


_SCHEMA_DESCRIPTION = """\
The OCTO Product JSON schema has the following structure:

{
  "id": "<UUID v4 string>",
  "internalName": "<string - descriptive product name>",
  "reference": "<string or null - short reference code>",
  "locale": "<string - e.g. 'en', 'de', 'fr'>",
  "timeZone": "<string - IANA timezone e.g. 'Europe/London', 'America/New_York'>",
  "allowFreesale": <boolean>,
  "instantConfirmation": <boolean>,
  "instantDelivery": <boolean>,
  "availabilityRequired": <boolean>,
  "availabilityType": "<'START_TIME' or 'OPENING_HOURS'>",
  "deliveryFormats": ["<one or more of: 'PDF_URL', 'QRCODE'>"],
  "deliveryMethods": ["<one or more of: 'VOUCHER', 'TICKET'>"],
  "redemptionMethod": "<'DIGITAL', 'PRINT', or 'MANIFEST'>",
  "options": [
    {
      "id": "<UUID v4 string>",
      "default": <boolean>,
      "internalName": "<string - option name>",
      "reference": "<string or null>",
      "availabilityLocalStartTimes": ["<HH:MM strings, e.g. '09:00', '14:30'>"],
      "cancellationCutoff": "<string, e.g. '24 hours'>",
      "cancellationCutoffAmount": <integer>,
      "cancellationCutoffUnit": "<'hour', 'minute', or 'day'>",
      "requiredContactFields": ["<zero or more of: 'firstName', 'lastName', 'emailAddress', 'phoneNumber', 'country', 'notes', 'locales'>"],
      "restrictions": {
        "minUnits": <integer or null>,
        "maxUnits": <integer or null>
      },
      "units": [
        {
          "id": "<UUID v4 string>",
          "internalName": "<string - unit name, e.g. 'Adult', 'Child'>",
          "reference": "<string or null>",
          "type": "<one of: 'ADULT', 'YOUTH', 'CHILD', 'INFANT', 'FAMILY', 'SENIOR', 'STUDENT', 'MILITARY', 'OTHER'>",
          "requiredContactFields": [],
          "restrictions": {
            "minAge": <integer>,
            "maxAge": <integer>,
            "idRequired": <boolean>,
            "minQuantity": <integer or null>,
            "maxQuantity": <integer or null>,
            "paxCount": <integer>,
            "accompaniedBy": []
          }
        }
      ]
    }
  ]
}
"""


class PromptBuilder:
    """Builds LLM prompts for generating synthetic OCTO Product JSON."""

    def __init__(
        self,
        samples_dir: str = "real-samples",
        avg_slots_per_day: int = 3,
    ) -> None:
        self._samples_dir = samples_dir
        self._avg_slots_per_day = avg_slots_per_day
        self._examples: list[str] = []
        self._load_examples()

    def _load_examples(self) -> None:
        """Find and load 2-3 trimmed product examples from sample files."""
        samples_path = Path(self._samples_dir) / _SAMPLES_SUBDIR
        if not samples_path.exists():
            # Fall back to samples_dir itself if subdir doesn't exist
            samples_path = Path(self._samples_dir)

        product_files = sorted(
            p for p in samples_path.iterdir()
            if p.is_file() and p.name.endswith("-products.json")
        ) if samples_path.exists() else []

        loaded = 0
        for filepath in product_files:
            if loaded >= 3:
                break
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            # Product files contain arrays; take the first product from each
            if isinstance(data, list) and len(data) > 0:
                trimmed = _trim_product(data[0])
                self._examples.append(
                    json.dumps(trimmed, indent=2, ensure_ascii=False)
                )
                loaded += 1
                logger.debug("Loaded sample from %s", filepath.name)

        if not self._examples:
            logger.warning("No product sample files found in %s", samples_path)

    def build_prompt(self) -> str:
        """Build the full prompt for generating one OCTO Product."""
        parts: list[str] = []

        # System instruction
        parts.append(
            "You are a data generator for the OCTO Travel API standard. "
            "Your task is to generate exactly ONE valid OCTO Product JSON object."
        )

        # Schema
        parts.append("\n## OCTO Product JSON Schema\n")
        parts.append(_SCHEMA_DESCRIPTION)

        # Few-shot examples
        if self._examples:
            parts.append("\n## Examples (for structure reference only)\n")
            parts.append(
                "Below are real product examples showing the expected JSON "
                "structure and field patterns. Use them ONLY to understand the "
                "format. Do NOT copy any values from these examples.\n"
            )
            for i, example in enumerate(self._examples, 1):
                parts.append(f"### Example {i}\n```json\n{example}\n```\n")

        # Generation instructions
        parts.append("\n## Generation Instructions\n")
        parts.append(
            "Generate exactly ONE fictional OCTO Product JSON object following "
            "these rules:\n"
            "\n"
            "1. NEVER reproduce any names, descriptions, IDs, references, "
            "locations, or any other values from the examples above. All "
            "values must be entirely fictional and original.\n"
            "2. Generate fresh UUID v4 values for the product id, each option "
            "id, and each unit id.\n"
            "3. The product MUST have at least one Option, and each Option "
            "MUST have at least one Unit.\n"
            "4. Use realistic but fictional product names (e.g. invented tour "
            "names, fictional attractions, made-up cities).\n"
            "5. Choose a valid availabilityType: either 'START_TIME' or "
            "'OPENING_HOURS'.\n"
            f"6. For START_TIME products, include approximately "
            f"{self._avg_slots_per_day} entries in availabilityLocalStartTimes "
            f"(vary between {max(1, self._avg_slots_per_day - 1)} and "
            f"{self._avg_slots_per_day + 1}). "
            "For OPENING_HOURS products, leave availabilityLocalStartTimes "
            "as an empty array.\n"
            "7. Use realistic age ranges in unit restrictions (e.g. Adult "
            "18-64, Child 5-12, Infant 0-4).\n"
            "8. Set exactly one option as default (default: true).\n"
            "9. Include a mix of unit types from: ADULT, YOUTH, CHILD, "
            "INFANT, FAMILY, SENIOR, STUDENT, MILITARY, OTHER.\n"
            "10. Return ONLY the raw JSON object. No markdown, no "
            "explanation, no wrapping.\n"
        )

        return "\n".join(parts)
