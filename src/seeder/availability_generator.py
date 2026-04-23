"""Orchestrates Availability Calendar generation via Ollama with retry and validation.

Generates availability in weekly chunks (7 days per LLM call) to keep
context short and allow deterministic slot-frequency capping.  After each
week is validated, a post-processing step enforces
``max_slots_per_week`` by flipping excess available days to CLOSED.
"""

import asyncio
import json
import logging
import math
import re
import time
from datetime import date, timedelta
from pathlib import Path

from models.availability import AvailabilityCalendarDay, AvailabilityStatus
from seeder.availability_prompt_builder import AvailabilityPromptBuilder
from seeder.ollama_client import (
    OllamaClient,
    OllamaInvalidResponseError,
    OllamaUnreachableError,
    SeedingFailedError,
)
from telemetry.setup import TelemetryInstruments

logger = logging.getLogger(__name__)

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)

_WEEK_DAYS = 3

# Statuses that count as "available slots"
_OPEN_STATUSES = {
    AvailabilityStatus.AVAILABLE.value,
    AvailabilityStatus.FREESALE.value,
    AvailabilityStatus.LIMITED.value,
}


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    m = _CODE_FENCE_RE.match(text)
    return m.group(1).strip() if m else text


def _validation_error_to_hint(exc: Exception) -> str:
    from pydantic import ValidationError

    if not isinstance(exc, ValidationError):
        return f"Schema validation failed: {exc}"

    hints: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(p) for p in error["loc"])
        msg = error["msg"]
        hints.append(f"Field '{loc}': {msg}")
    return "; ".join(hints) if hints else f"Schema validation failed: {exc}"


def _normalize_calendar_day(data: dict) -> dict:
    """Fix common LLM output quirks before Pydantic validation."""
    status = data.get("status", "")

    if status in ("CLOSED", "SOLD_OUT"):
        data["available"] = False
    elif status in ("AVAILABLE", "FREESALE", "LIMITED"):
        data["available"] = True

    if status == "FREESALE":
        data["vacancies"] = None
        data["capacity"] = None
    if status == "CLOSED":
        data["vacancies"] = None
        data["capacity"] = None

    data.setdefault("openingHours", [])
    return data


def _count_day_slots(day: dict) -> int:
    """Count bookable time slots for a single day.

    For START_TIME products each entry in availabilityLocalStartTimes is
    one slot.  For OPENING_HOURS products (no start times) an open day
    counts as 1 slot.
    """
    if day.get("status") not in _OPEN_STATUSES:
        return 0
    start_times = day.get("availabilityLocalStartTimes") or []
    return max(len(start_times), 1)


def _close_day(day: dict) -> dict:
    """Flip a day to CLOSED in-place."""
    day["status"] = AvailabilityStatus.CLOSED.value
    day["available"] = False
    day["vacancies"] = None
    day["capacity"] = None
    day["statusMessage"] = "Closed"
    day.pop("availabilityLocalStartTimes", None)
    return day


def _cap_slots(days: list[dict], max_slots: int) -> list[dict]:
    """Enforce max bookable time-slots per week.

    A "slot" is one entry in ``availabilityLocalStartTimes`` for
    START_TIME products, or 1 per open day for OPENING_HOURS products.

    Strategy (preserves earlier days):
    1. Walk days in order, accumulating slot count.
    2. When adding a day would exceed the budget:
       - If the day has multiple start times, trim the list to fit.
       - If even 1 slot doesn't fit, close the day entirely.
    """
    budget = max_slots
    for day in days:
        if day.get("status") not in _OPEN_STATUSES:
            continue

        start_times = day.get("availabilityLocalStartTimes") or []
        day_slots = max(len(start_times), 1)

        if budget <= 0:
            _close_day(day)
            continue

        if day_slots <= budget:
            budget -= day_slots
        elif len(start_times) > 1 and budget >= 1:
            # Trim start times to fit remaining budget
            day["availabilityLocalStartTimes"] = start_times[:budget]
            budget = 0
        else:
            _close_day(day)

    return days


def _time_to_minutes(t: str) -> int:
    """Convert HH:MM string to minutes since midnight."""
    parts = t.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def _get_option_duration_minutes(option_data: dict) -> int | None:
    """Extract option duration in minutes, or None if not set."""
    amount = option_data.get("durationAmount")
    unit = option_data.get("durationUnit")
    if amount is None or unit is None:
        return None
    try:
        amount_num = float(amount)
    except (ValueError, TypeError):
        return None
    if unit == "hour":
        return int(amount_num * 60)
    if unit == "minute":
        return int(amount_num)
    if unit == "day":
        return int(amount_num * 1440)
    return None


def _check_coherence(
    days: list[dict],
    product_data: dict,
    option_data: dict | None,
) -> list[str]:
    """Check availability coherence with product data.

    Only flags hard structural constraint violations that the LLM must
    fix.  Realism concerns (varied vacancies, appropriate extended fields)
    are handled via prompt guidance — we trust the LLM to be spontaneous.

    Returns a list of human-readable issue descriptions (empty = all good).
    """
    issues: list[str] = []
    avail_type = product_data.get("availabilityType", "OPENING_HOURS")
    allow_freesale = product_data.get("allowFreesale", False)

    # Allowed start times from the option definition
    allowed_times: set[str] | None = None
    duration_min: int | None = None
    if option_data and avail_type == "START_TIME":
        raw = option_data.get("availabilityLocalStartTimes", [])
        if raw:
            allowed_times = set(raw)
        duration_min = _get_option_duration_minutes(option_data)

    for day in days:
        local_date = day.get("localDate", "?")
        status = day.get("status", "")

        # FREESALE not allowed if product doesn't allow it
        if status == "FREESALE" and not allow_freesale:
            issues.append(
                f"Day {local_date}: FREESALE status used but product "
                "allowFreesale=false. Use AVAILABLE instead."
            )

        # Vacancies must be <= capacity
        vac = day.get("vacancies")
        cap = day.get("capacity")
        if vac is not None and cap is not None and vac > cap:
            issues.append(
                f"Day {local_date}: vacancies ({vac}) exceeds "
                f"capacity ({cap}). vacancies must be <= capacity."
            )

        # START_TIME checks
        if status in _OPEN_STATUSES and avail_type == "START_TIME":
            day_times = day.get("availabilityLocalStartTimes") or []

            # Subset check
            if allowed_times and day_times:
                invalid = [t for t in day_times if t not in allowed_times]
                if invalid:
                    issues.append(
                        f"Day {local_date}: start times {invalid} are not in "
                        f"the option's defined times {sorted(allowed_times)}. "
                        "Only use a subset of the option's start times."
                    )

            # Overlap check using duration
            if duration_min and len(day_times) > 1:
                sorted_times = sorted(day_times, key=_time_to_minutes)
                for i in range(len(sorted_times) - 1):
                    end_min = _time_to_minutes(sorted_times[i]) + duration_min
                    next_start = _time_to_minutes(sorted_times[i + 1])
                    if next_start < end_min:
                        issues.append(
                            f"Day {local_date}: start times {sorted_times[i]} "
                            f"and {sorted_times[i+1]} overlap — the first "
                            f"session ends at minute {end_min} but the next "
                            f"starts at minute {next_start} "
                            f"(duration={duration_min}min). "
                            "Remove one of the overlapping times."
                        )

        # OPENING_HOURS should not have start times
        if avail_type == "OPENING_HOURS":
            day_times = day.get("availabilityLocalStartTimes") or []
            if day_times:
                issues.append(
                    f"Day {local_date}: OPENING_HOURS product should not "
                    "have availabilityLocalStartTimes. Remove them."
                )

    return issues


def _fix_coherence(
    days: list[dict],
    product_data: dict,
    option_data: dict | None,
) -> list[dict]:
    """Deterministically fix hard constraint violations.

    Only fixes structural issues (vacancies > capacity, invalid start
    times, overlaps).  Realism is left to the LLM via prompt guidance.
    """
    avail_type = product_data.get("availabilityType", "OPENING_HOURS")
    allow_freesale = product_data.get("allowFreesale", False)

    allowed_times: set[str] | None = None
    duration_min: int | None = None
    if option_data and avail_type == "START_TIME":
        raw = option_data.get("availabilityLocalStartTimes", [])
        if raw:
            allowed_times = set(raw)
        duration_min = _get_option_duration_minutes(option_data)

    for day in days:
        status = day.get("status", "")

        # Fix FREESALE → AVAILABLE
        if status == "FREESALE" and not allow_freesale:
            day["status"] = AvailabilityStatus.AVAILABLE.value

        # Fix vacancies > capacity
        vac = day.get("vacancies")
        cap = day.get("capacity")
        if vac is not None and cap is not None and vac > cap:
            day["vacancies"] = cap

        if status not in _OPEN_STATUSES:
            # Closed/sold-out days should not have start times
            day.pop("availabilityLocalStartTimes", None)
            continue

        if avail_type == "OPENING_HOURS":
            day.pop("availabilityLocalStartTimes", None)
            continue

        # Filter to allowed subset
        day_times = day.get("availabilityLocalStartTimes") or []
        if allowed_times and day_times:
            day_times = [t for t in day_times if t in allowed_times]

        # Remove overlapping times
        if duration_min and len(day_times) > 1:
            sorted_times = sorted(day_times, key=_time_to_minutes)
            kept = [sorted_times[0]]
            for t in sorted_times[1:]:
                prev_end = _time_to_minutes(kept[-1]) + duration_min
                if _time_to_minutes(t) >= prev_end:
                    kept.append(t)
            day_times = kept

        if day_times:
            day["availabilityLocalStartTimes"] = day_times
        else:
            day.pop("availabilityLocalStartTimes", None)

    return days


def load_products_from_seed(seed_product_file: str) -> list[dict]:
    """Load product dicts from the seed product dump file."""
    path = Path(seed_product_file)
    if not path.exists():
        raise FileNotFoundError(f"Seed product file not found: {seed_product_file}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class AvailabilityGenerator:
    """Generates availability calendar data in weekly chunks."""

    def __init__(
        self,
        ollama_client: OllamaClient,
        prompt_builder: AvailabilityPromptBuilder,
        max_retries: int = 3,
        availability_window_days: int = 5,
        availability_start_date: str = "",
        max_slots_per_week: int = 5,
        telemetry: TelemetryInstruments | None = None,
    ) -> None:
        self._client = ollama_client
        self._prompt_builder = prompt_builder
        self._max_retries = max_retries
        self._window_days = availability_window_days
        self._start_date = availability_start_date
        self._max_slots_per_week = max_slots_per_week
        self._tel = telemetry

    async def generate_availability(
        self,
        products: list[dict],
    ) -> dict[str, dict[str, list[dict]]]:
        """Generate availability for all products and their options.

        The total window (``availability_window_days``) is split into
        7-day chunks.  Each chunk is one LLM call → validate → cap cycle.

        Returns:
            Nested dict: {product_id: {option_id: [calendar_day_dicts]}}
        """
        gen_start = time.monotonic()
        result: dict[str, dict[str, list[dict]]] = {}

        start_date_str = self._start_date or date.today().isoformat()
        start_dt = date.fromisoformat(start_date_str)

        total_options = sum(len(p.get("options", [])) for p in products)
        current = 0

        for product in products:
            product_id = product.get("id", "unknown")
            result[product_id] = {}

            for option in product.get("options", []):
                current += 1
                option_id = option.get("id", "unknown")
                option_name = option.get("title") or option.get("internalName", "?")
                product_title = product.get("title") or product.get("internalName", "?")

                logger.info(
                    "Generating availability %d/%d: product '%s' → option '%s'",
                    current, total_options, product_title, option_name,
                )

                all_days = await self._generate_weekly_chunks(
                    product_data=product,
                    option_id=option_id,
                    start_dt=start_dt,
                )
                result[product_id][option_id] = all_days

        gen_duration = time.monotonic() - gen_start
        logger.info(
            "Availability generation complete: %d products, %d options, %.1fs",
            len(products), total_options, gen_duration,
        )
        if self._tel:
            self._tel.availability_generation_duration_seconds.record(gen_duration)
        return result

    async def _generate_weekly_chunks(
        self,
        product_data: dict,
        option_id: str,
        start_dt: date,
    ) -> list[dict]:
        """Split the total window into 7-day weeks and generate each."""
        option_start = time.monotonic()
        remaining = self._window_days
        cursor = start_dt
        all_days: list[dict] = []

        # Resolve option data once for coherence checks
        option_data = None
        for opt in product_data.get("options", []):
            if opt.get("id") == option_id:
                option_data = opt
                break

        num_weeks = math.ceil(self._window_days / _WEEK_DAYS)

        for week_num in range(1, num_weeks + 1):
            chunk_size = min(_WEEK_DAYS, remaining)
            chunk_start = cursor.isoformat()

            logger.info(
                "  Week %d/%d: %s → %d days",
                week_num, num_weeks, chunk_start, chunk_size,
            )

            week_days = await self._generate_single_chunk(
                product_data=product_data,
                option_data=option_data,
                option_id=option_id,
                num_days=chunk_size,
                start_date=chunk_start,
            )

            # Enforce slot cap on this week
            week_days = _cap_slots(week_days, self._max_slots_per_week)
            all_days.extend(week_days)

            cursor += timedelta(days=chunk_size)
            remaining -= chunk_size

        if self._tel:
            option_duration = time.monotonic() - option_start
            attrs = {
                "product_id": product_data.get("id", "unknown"),
                "option_id": option_id,
            }
            self._tel.availability_option_duration_seconds.record(
                option_duration, attrs,
            )

        return all_days

    async def _generate_single_chunk(
        self,
        product_data: dict,
        option_data: dict | None,
        option_id: str,
        num_days: int,
        start_date: str,
    ) -> list[dict]:
        """Generate one chunk (up to 7 days) with retry logic."""
        error_hints: list[str] = []

        for attempt in range(1, self._max_retries + 1):
            prompt = self._prompt_builder.build_prompt(
                product_data=product_data,
                option_id=option_id,
                num_days=num_days,
                start_date=start_date,
                max_slots=self._max_slots_per_week,
                error_hints=error_hints if error_hints else None,
            )

            try:
                logger.info(
                    "    Option %s: attempt %d/%d",
                    option_id[:8], attempt, self._max_retries,
                )

                req_start = time.monotonic()
                ollama_resp = await self._client.generate(prompt)
                req_duration = time.monotonic() - req_start

                if self._tel:
                    self._tel.seeder_ollama_requests_total.add(1)
                    self._tel.seeder_ollama_request_duration_seconds.record(req_duration)
                    self._tel.llm_prompt_tokens.add(ollama_resp.prompt_eval_count)
                    self._tel.llm_completion_tokens.add(ollama_resp.eval_count)

                raw_text = _strip_code_fences(ollama_resp.response)

                # Parse JSON
                try:
                    data = json.loads(raw_text)
                except json.JSONDecodeError as exc:
                    error_hints.append(
                        f"Output was not valid JSON (parse error at position {exc.pos}). "
                        "Return ONLY a raw JSON array, no markdown fences or extra text."
                    )
                    raise OllamaInvalidResponseError(
                        f"Failed to parse LLM output as JSON: {exc}"
                    ) from exc

                if isinstance(data, dict):
                    data = [data]
                if not isinstance(data, list):
                    error_hints.append(
                        "Output must be a JSON array of calendar day objects."
                    )
                    raise OllamaInvalidResponseError("LLM output is not a JSON array")

                # Normalize and validate each day
                validated_days: list[dict] = []
                all_hints: list[str] = []

                for i, day_data in enumerate(data):
                    day_data = _normalize_calendar_day(day_data)
                    try:
                        day = AvailabilityCalendarDay.model_validate(day_data)
                        validated_days.append(day.model_dump(by_alias=True))
                    except Exception as exc:
                        all_hints.append(
                            f"Day {i} (localDate={day_data.get('localDate', '?')}): "
                            + _validation_error_to_hint(exc)
                        )

                if len(all_hints) > len(data) // 2:
                    error_hints.extend(all_hints[:5])
                    raise OllamaInvalidResponseError(
                        f"{len(all_hints)}/{len(data)} days failed validation"
                    )

                if validated_days:
                    # Coherence: check against product constraints
                    coherence_issues = _check_coherence(
                        validated_days, product_data, option_data,
                    )
                    if coherence_issues:
                        logger.info(
                            "    Option %s: %d coherence issues, auto-fixing",
                            option_id[:8], len(coherence_issues),
                        )
                        # Auto-fix what we can deterministically
                        validated_days = _fix_coherence(
                            validated_days, product_data, option_data,
                        )
                        # Feed remaining issues as hints for next attempt
                        # (only matters if this chunk gets retried)
                        error_hints.extend(coherence_issues[:3])

                    # Trim excess days to save memory, but accept whatever count
                    max_days = int(num_days * 1.25)
                    if len(validated_days) > max_days:
                        logger.info(
                            "    Option %s: trimming %d days to %d",
                            option_id[:8], len(validated_days), max_days,
                        )
                        validated_days = validated_days[:max_days]

                    logger.info(
                        "    Option %s: %d/%d days validated",
                        option_id[:8], len(validated_days), len(data),
                    )
                    if self._tel:
                        self._tel.availability_option_attempts.record(
                            attempt,
                            {"product_id": product_data.get("id", "unknown"),
                             "option_id": option_id},
                        )
                    return validated_days

                error_hints.extend(all_hints[:5])
                raise OllamaInvalidResponseError("No days passed validation")

            except OllamaUnreachableError:
                logger.error(
                    "    Option %s: Ollama unreachable on attempt %d/%d",
                    option_id[:8], attempt, self._max_retries,
                )
                if attempt < self._max_retries:
                    backoff = 2 ** attempt
                    logger.info("    Backing off %d seconds", backoff)
                    await asyncio.sleep(backoff)

            except OllamaInvalidResponseError as exc:
                logger.error(
                    "    Option %s: invalid response on attempt %d/%d — %s",
                    option_id[:8], attempt, self._max_retries, exc,
                )

        logger.error(
            "Option %s: all %d attempts exhausted", option_id[:8], self._max_retries
        )
        raise SeedingFailedError(
            f"Failed to generate availability for option {option_id} "
            f"after {self._max_retries} attempts"
        )
