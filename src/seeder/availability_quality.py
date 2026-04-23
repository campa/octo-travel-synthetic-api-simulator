"""Deterministic quality scoring for generated OCTO availability data.

Evaluates availability across three dimensions:
- Realism: varied vacancies, sensible capacity for product type, status mix
- Coherence: start times match product definition, no overlaps, correct
  openingHours vs START_TIME usage
- Completeness: enough days generated relative to the requested window

Each dimension produces a 0.0–1.0 score. Issues are tracked for reporting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("seeder.availability_quality")

_OPEN_STATUSES = {"AVAILABLE", "FREESALE", "LIMITED"}


@dataclass
class AvailabilityIssue:
    """A single quality issue found in availability data."""

    dimension: str  # realism | coherence | completeness
    check: str
    message: str
    product_id: str = ""
    option_id: str = ""


@dataclass
class OptionAvailabilityScore:
    """Quality scores for one option's availability."""

    product_id: str
    option_id: str
    product_title: str
    option_title: str
    realism: float = 0.0
    coherence: float = 0.0
    completeness: float = 0.0
    day_count: int = 0
    issues: list[AvailabilityIssue] = field(default_factory=list)


@dataclass
class AvailabilityBatchScore:
    """Quality scores for all availability data."""

    option_scores: list[OptionAvailabilityScore] = field(default_factory=list)
    composite: float = 0.0
    issues: list[AvailabilityIssue] = field(default_factory=list)

    @property
    def avg_realism(self) -> float:
        if not self.option_scores:
            return 0.0
        return sum(s.realism for s in self.option_scores) / len(self.option_scores)

    @property
    def avg_coherence(self) -> float:
        if not self.option_scores:
            return 0.0
        return sum(s.coherence for s in self.option_scores) / len(self.option_scores)

    @property
    def avg_completeness(self) -> float:
        if not self.option_scores:
            return 0.0
        return sum(s.completeness for s in self.option_scores) / len(self.option_scores)

    @property
    def all_issues(self) -> list[AvailabilityIssue]:
        result = list(self.issues)
        for s in self.option_scores:
            result.extend(s.issues)
        return result


# Weights for composite score
_W_REALISM = 0.40
_W_COHERENCE = 0.40
_W_COMPLETENESS = 0.20


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


def _score_realism(
    days: list[dict],
    product_id: str,
    option_id: str,
) -> tuple[float, list[AvailabilityIssue]]:
    """Score realism of availability data.

    Checks:
    - Status variety (not all days identical status)
    - Vacancy variety (not all open days same vacancies)
    - Vacancies <= capacity
    - Sensible capacity values (> 0)
    """
    issues: list[AvailabilityIssue] = []
    score = 1.0

    if not days:
        return 0.0, issues

    statuses = [d.get("status") for d in days]
    open_days = [d for d in days if d.get("status") in _OPEN_STATUSES]

    # Status variety
    unique_statuses = set(statuses)
    if len(days) > 2 and len(unique_statuses) == 1:
        score -= 0.15
        issues.append(AvailabilityIssue(
            dimension="realism",
            check="monotonous_status",
            message=f"All {len(days)} days have identical status '{statuses[0]}'",
            product_id=product_id,
            option_id=option_id,
        ))

    # Vacancy variety
    vacancies = [d.get("vacancies") for d in open_days if d.get("vacancies") is not None]
    if len(vacancies) > 2 and len(set(vacancies)) == 1:
        score -= 0.20
        issues.append(AvailabilityIssue(
            dimension="realism",
            check="monotonous_vacancies",
            message=f"All {len(vacancies)} open days have identical vacancies={vacancies[0]}",
            product_id=product_id,
            option_id=option_id,
        ))

    # Vacancies > capacity
    for d in days:
        vac = d.get("vacancies")
        cap = d.get("capacity")
        if vac is not None and cap is not None and vac > cap:
            score -= 0.15
            issues.append(AvailabilityIssue(
                dimension="realism",
                check="vacancies_exceeds_capacity",
                message=f"Day {d.get('localDate', '?')}: vacancies ({vac}) > capacity ({cap})",
                product_id=product_id,
                option_id=option_id,
            ))
            break  # one penalty is enough

    # Zero or negative capacity on open days
    for d in open_days:
        cap = d.get("capacity")
        if cap is not None and cap <= 0:
            score -= 0.10
            issues.append(AvailabilityIssue(
                dimension="realism",
                check="invalid_capacity",
                message=f"Day {d.get('localDate', '?')}: capacity is {cap} on an open day",
                product_id=product_id,
                option_id=option_id,
            ))
            break

    return max(0.0, score), issues


def _score_coherence(
    days: list[dict],
    product_data: dict,
    option_data: dict | None,
    product_id: str,
    option_id: str,
) -> tuple[float, list[AvailabilityIssue]]:
    """Score coherence of availability with product definition.

    Checks:
    - START_TIME: start times are subset of option's defined times
    - START_TIME: no overlapping sessions given duration
    - OPENING_HOURS: no start times present
    - FREESALE only when allowFreesale=true
    """
    issues: list[AvailabilityIssue] = []
    score = 1.0

    if not days:
        return 0.0, issues

    avail_type = product_data.get("availabilityType", "OPENING_HOURS")
    allow_freesale = product_data.get("allowFreesale", False)

    allowed_times: set[str] | None = None
    duration_min: int | None = None
    if option_data and avail_type == "START_TIME":
        raw = option_data.get("availabilityLocalStartTimes", [])
        if raw:
            allowed_times = set(raw)
        duration_min = _get_option_duration_minutes(option_data)

    invalid_time_count = 0
    overlap_count = 0

    for d in days:
        status = d.get("status", "")
        local_date = d.get("localDate", "?")

        # FREESALE check
        if status == "FREESALE" and not allow_freesale:
            score -= 0.15
            issues.append(AvailabilityIssue(
                dimension="coherence",
                check="invalid_freesale",
                message=f"Day {local_date}: FREESALE but allowFreesale=false",
                product_id=product_id,
                option_id=option_id,
            ))

        # OPENING_HOURS should not have start times
        if avail_type == "OPENING_HOURS":
            day_times = d.get("availabilityLocalStartTimes") or []
            if day_times:
                score -= 0.10
                issues.append(AvailabilityIssue(
                    dimension="coherence",
                    check="opening_hours_has_start_times",
                    message=f"Day {local_date}: OPENING_HOURS product has start times",
                    product_id=product_id,
                    option_id=option_id,
                ))

        # START_TIME checks
        if status in _OPEN_STATUSES and avail_type == "START_TIME":
            day_times = d.get("availabilityLocalStartTimes") or []

            if allowed_times and day_times:
                invalid = [t for t in day_times if t not in allowed_times]
                if invalid:
                    invalid_time_count += len(invalid)

            if duration_min and len(day_times) > 1:
                sorted_times = sorted(day_times, key=_time_to_minutes)
                for i in range(len(sorted_times) - 1):
                    end_min = _time_to_minutes(sorted_times[i]) + duration_min
                    next_start = _time_to_minutes(sorted_times[i + 1])
                    if next_start < end_min:
                        overlap_count += 1

    if invalid_time_count > 0:
        score -= min(0.30, invalid_time_count * 0.10)
        issues.append(AvailabilityIssue(
            dimension="coherence",
            check="invalid_start_times",
            message=f"{invalid_time_count} start time(s) not in option's defined times",
            product_id=product_id,
            option_id=option_id,
        ))

    if overlap_count > 0:
        score -= min(0.30, overlap_count * 0.10)
        issues.append(AvailabilityIssue(
            dimension="coherence",
            check="overlapping_start_times",
            message=f"{overlap_count} overlapping start time pair(s) given option duration",
            product_id=product_id,
            option_id=option_id,
        ))

    return max(0.0, score), issues


def _score_completeness(
    days: list[dict],
    product_data: dict,
    product_id: str,
    option_id: str,
) -> tuple[float, list[AvailabilityIssue]]:
    """Score completeness of availability data.

    Checks:
    - At least 1 day present
    - Open days have capacity and vacancies
    - OPENING_HOURS products have openingHours entries on open days
    """
    issues: list[AvailabilityIssue] = []
    score = 1.0

    if not days:
        issues.append(AvailabilityIssue(
            dimension="completeness",
            check="no_days",
            message="No availability days generated",
            product_id=product_id,
            option_id=option_id,
        ))
        return 0.0, issues

    avail_type = product_data.get("availabilityType", "OPENING_HOURS")
    open_days = [d for d in days if d.get("status") in _OPEN_STATUSES]

    # Open days missing capacity/vacancies
    missing_capacity = sum(1 for d in open_days if d.get("capacity") is None)
    missing_vacancies = sum(
        1 for d in open_days
        if d.get("status") != "FREESALE" and d.get("vacancies") is None
    )

    if open_days and missing_capacity > len(open_days) * 0.5:
        score -= 0.15
        issues.append(AvailabilityIssue(
            dimension="completeness",
            check="missing_capacity",
            message=f"{missing_capacity}/{len(open_days)} open days missing capacity",
            product_id=product_id,
            option_id=option_id,
        ))

    if open_days and missing_vacancies > len(open_days) * 0.5:
        score -= 0.15
        issues.append(AvailabilityIssue(
            dimension="completeness",
            check="missing_vacancies",
            message=f"{missing_vacancies}/{len(open_days)} open days missing vacancies",
            product_id=product_id,
            option_id=option_id,
        ))

    # OPENING_HOURS should have openingHours on open days
    if avail_type == "OPENING_HOURS":
        missing_hours = sum(1 for d in open_days if not d.get("openingHours"))
        if open_days and missing_hours > len(open_days) * 0.5:
            score -= 0.15
            issues.append(AvailabilityIssue(
                dimension="completeness",
                check="missing_opening_hours",
                message=f"{missing_hours}/{len(open_days)} open days missing openingHours",
                product_id=product_id,
                option_id=option_id,
            ))

    return max(0.0, score), issues


class AvailabilityQualityScorer:
    """Deterministic quality scorer for generated OCTO availability data."""

    def score_batch(
        self,
        availability: dict[str, dict[str, list[dict]]],
        products: list[dict],
    ) -> AvailabilityBatchScore:
        """Score all availability data against product definitions.

        Args:
            availability: {product_id: {option_id: [day_dicts]}}
            products: list of product dicts (from seed dump)
        """
        product_index = {p.get("id", ""): p for p in products}
        option_scores: list[OptionAvailabilityScore] = []

        for product_id, options in availability.items():
            product_data = product_index.get(product_id, {})
            product_title = (
                product_data.get("title")
                or product_data.get("internalName", "Unknown")
            )

            # Build option index for this product
            option_index = {
                o.get("id", ""): o for o in product_data.get("options", [])
            }

            for option_id, days in options.items():
                option_data = option_index.get(option_id)
                option_title = ""
                if option_data:
                    option_title = (
                        option_data.get("title")
                        or option_data.get("internalName", "Unknown")
                    )

                os = OptionAvailabilityScore(
                    product_id=product_id,
                    option_id=option_id,
                    product_title=product_title,
                    option_title=option_title,
                    day_count=len(days),
                )

                realism, r_issues = _score_realism(days, product_id, option_id)
                os.realism = realism
                os.issues.extend(r_issues)

                coherence, c_issues = _score_coherence(
                    days, product_data, option_data, product_id, option_id,
                )
                os.coherence = coherence
                os.issues.extend(c_issues)

                completeness, comp_issues = _score_completeness(
                    days, product_data, product_id, option_id,
                )
                os.completeness = completeness
                os.issues.extend(comp_issues)

                option_scores.append(os)

        batch = AvailabilityBatchScore(option_scores=option_scores)
        batch.composite = (
            _W_REALISM * batch.avg_realism
            + _W_COHERENCE * batch.avg_coherence
            + _W_COMPLETENESS * batch.avg_completeness
        )

        logger.info(
            "Availability quality — realism=%.2f coherence=%.2f "
            "completeness=%.2f composite=%.2f issues=%d",
            batch.avg_realism,
            batch.avg_coherence,
            batch.avg_completeness,
            batch.composite,
            len(batch.all_issues),
        )

        return batch
