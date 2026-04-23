"""Deterministic quality scoring for generated OCTO products.

Evaluates products across four dimensions:
- Realism: coordinates, place IDs, currency/country/timezone consistency
- Coherence: duration alignment, contact-delivery consistency, pricing logic
- Completeness: presence of optional-but-valuable fields
- Diversity: batch-level variety in countries, categories, availability types

Each dimension produces a 0.0–1.0 score. A composite score is computed as
a weighted average. Individual issues are tracked for observability.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Optional

from models.product import Product, UnitType

logger = logging.getLogger("seeder.quality")


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

# Country → expected currencies (covers major tourism markets)
_COUNTRY_CURRENCIES: dict[str, set[str]] = {
    "US": {"USD"},
    "GB": {"GBP"},
    "DE": {"EUR"}, "FR": {"EUR"}, "ES": {"EUR"}, "IT": {"EUR"},
    "NL": {"EUR"}, "AT": {"EUR"}, "BE": {"EUR"}, "PT": {"EUR"},
    "GR": {"EUR"}, "IE": {"EUR"}, "FI": {"EUR"},
    "JP": {"JPY"},
    "AU": {"AUD"},
    "CA": {"CAD"},
    "CH": {"CHF"},
    "SE": {"SEK"},
    "NO": {"NOK"},
    "DK": {"DKK"},
    "NZ": {"NZD"},
    "MX": {"MXN"},
    "TH": {"THB"},
    "AE": {"AED"},
    "ZA": {"ZAR"},
    "BR": {"BRL"},
    "TR": {"TRY"},
    "IS": {"ISK"},
    "HR": {"EUR"},
    "CZ": {"CZK"},
    "HU": {"HUF"},
    "PL": {"PLN"},
}

# Country → expected timezone prefix
_COUNTRY_TZ_PREFIXES: dict[str, list[str]] = {
    "US": ["America/"],
    "GB": ["Europe/London"],
    "DE": ["Europe/Berlin"],
    "FR": ["Europe/Paris"],
    "ES": ["Europe/Madrid"],
    "IT": ["Europe/Rome"],
    "NL": ["Europe/Amsterdam"],
    "AT": ["Europe/Vienna"],
    "JP": ["Asia/Tokyo"],
    "AU": ["Australia/"],
    "CA": ["America/"],
    "CH": ["Europe/Zurich"],
    "SE": ["Europe/Stockholm"],
    "NO": ["Europe/Oslo"],
    "DK": ["Europe/Copenhagen"],
    "NZ": ["Pacific/Auckland"],
    "MX": ["America/"],
    "TH": ["Asia/Bangkok"],
    "AE": ["Asia/Dubai"],
    "ZA": ["Africa/Johannesburg"],
    "BR": ["America/"],
    "TR": ["Europe/Istanbul"],
    "IS": ["Atlantic/Reykjavik"],
    "HR": ["Europe/Zagreb"],
    "CZ": ["Europe/Prague"],
    "HU": ["Europe/Budapest"],
    "PL": ["Europe/Warsaw"],
    "PT": ["Europe/Lisbon"],
    "GR": ["Europe/Athens"],
    "IE": ["Europe/Dublin"],
    "FI": ["Europe/Helsinki"],
    "BE": ["Europe/Brussels"],
}

# Well-known city centroids that LLMs love to use (lat, lon, tolerance)
_CITY_CENTROIDS: list[tuple[float, float, float]] = [
    (51.5074, -0.1278, 0.001),   # London
    (48.8566, 2.3522, 0.001),    # Paris
    (40.7128, -74.0060, 0.001),  # New York
    (52.5200, 13.4050, 0.001),   # Berlin
    (41.9028, 12.4964, 0.001),   # Rome
    (40.4168, -3.7038, 0.001),   # Madrid
    (35.6762, 139.6503, 0.001),  # Tokyo
    (-33.8688, 151.2093, 0.001), # Sydney
    (48.2082, 16.3738, 0.001),   # Vienna
    (59.3293, 18.0686, 0.001),   # Stockholm
]

# Known LLM-favorite dummy Google Place IDs
_KNOWN_DUMMY_PLACE_IDS: set[str] = {
    "ChIJN1t_tDeuEmsRUsoyG83frY4",  # Sydney Opera House
    "ChIJD7fiBh9u5kcRYJSMaMOCCwQ",  # Paris
    "ChIJdd4hrwug2EcRmSrV3Vo6llI",  # London
}

_PLACE_ID_RE = re.compile(r"^ChIJ[A-Za-z0-9_-]{20,}$")


# ---------------------------------------------------------------------------
# Issue tracking
# ---------------------------------------------------------------------------

@dataclass
class QualityIssue:
    """A single quality issue found in a product or batch."""
    dimension: str          # realism | coherence | completeness | diversity
    check: str              # e.g. "duplicate_place_id"
    message: str            # human-readable description
    product_id: str = ""    # empty for batch-level issues


@dataclass
class ProductScore:
    """Quality scores for a single product."""
    product_id: str
    title: str
    realism: float = 0.0
    coherence: float = 0.0
    completeness: float = 0.0
    issues: list[QualityIssue] = field(default_factory=list)


@dataclass
class BatchScore:
    """Quality scores for an entire batch of products."""
    product_scores: list[ProductScore] = field(default_factory=list)
    diversity: float = 0.0
    composite: float = 0.0
    issues: list[QualityIssue] = field(default_factory=list)

    @property
    def avg_realism(self) -> float:
        if not self.product_scores:
            return 0.0
        return sum(p.realism for p in self.product_scores) / len(self.product_scores)

    @property
    def avg_coherence(self) -> float:
        if not self.product_scores:
            return 0.0
        return sum(p.coherence for p in self.product_scores) / len(self.product_scores)

    @property
    def avg_completeness(self) -> float:
        if not self.product_scores:
            return 0.0
        return sum(p.completeness for p in self.product_scores) / len(self.product_scores)

    @property
    def all_issues(self) -> list[QualityIssue]:
        result = list(self.issues)
        for ps in self.product_scores:
            result.extend(ps.issues)
        return result


# ---------------------------------------------------------------------------
# Realism checks
# ---------------------------------------------------------------------------

def _coord_digit_entropy(value: float) -> float:
    """Shannon entropy of the decimal digits of a coordinate value."""
    decimal_str = f"{abs(value):.6f}".split(".")[1]
    if not decimal_str:
        return 0.0
    freq: dict[str, int] = {}
    for ch in decimal_str:
        freq[ch] = freq.get(ch, 0) + 1
    total = len(decimal_str)
    entropy = 0.0
    for count in freq.values():
        p = count / total
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


def _is_sequential_digits(value: float) -> bool:
    """Check if decimal digits form a sequential pattern like 123456 or 234567."""
    decimal_str = f"{abs(value):.6f}".split(".")[1]
    if len(decimal_str) < 4:
        return False
    diffs = [int(decimal_str[i + 1]) - int(decimal_str[i]) for i in range(len(decimal_str) - 1)]
    # All diffs the same (e.g., all +1 or all 0)
    return len(set(diffs)) == 1


def _is_city_centroid(lat: float, lon: float) -> bool:
    """Check if coordinates match a known city centroid."""
    for clat, clon, tol in _CITY_CENTROIDS:
        if abs(lat - clat) < tol and abs(lon - clon) < tol:
            return True
    return False


def _check_realism(product: Product, all_place_ids: dict[str, list[str]]) -> tuple[float, list[QualityIssue]]:
    """Score realism for a single product. Returns (score, issues)."""
    checks_passed = 0
    checks_total = 0
    issues: list[QualityIssue] = []
    pid = product.id

    # --- Coordinate plausibility ---
    if product.locations:
        for loc in product.locations:
            if loc.place and loc.place.latitude is not None and loc.place.longitude is not None:
                lat, lon = loc.place.latitude, loc.place.longitude

                # Check 1: entropy
                checks_total += 1
                lat_ent = _coord_digit_entropy(lat)
                lon_ent = _coord_digit_entropy(lon)
                if lat_ent >= 1.5 and lon_ent >= 1.5:
                    checks_passed += 1
                else:
                    issues.append(QualityIssue(
                        dimension="realism",
                        check="coordinate_entropy",
                        message=f"Low coordinate entropy for '{loc.title}': "
                                f"lat={lat} (ent={lat_ent:.2f}), lon={lon} (ent={lon_ent:.2f})",
                        product_id=pid,
                    ))

                # Check 2: sequential digits
                checks_total += 1
                if not _is_sequential_digits(lat) and not _is_sequential_digits(lon):
                    checks_passed += 1
                else:
                    issues.append(QualityIssue(
                        dimension="realism",
                        check="coordinate_sequential",
                        message=f"Sequential digit pattern in coordinates for '{loc.title}': "
                                f"lat={lat}, lon={lon}",
                        product_id=pid,
                    ))

                # Check 3: city centroid
                checks_total += 1
                if not _is_city_centroid(lat, lon):
                    checks_passed += 1
                else:
                    issues.append(QualityIssue(
                        dimension="realism",
                        check="city_centroid",
                        message=f"Coordinates for '{loc.title}' match a known city centroid: "
                                f"lat={lat}, lon={lon}",
                        product_id=pid,
                    ))

                # Check 4: Google Place ID uniqueness + validity
                if loc.place.identifiers:
                    gid = loc.place.identifiers.get("googlePlaceId")
                    if gid:
                        checks_total += 1
                        if gid in _KNOWN_DUMMY_PLACE_IDS:
                            issues.append(QualityIssue(
                                dimension="realism",
                                check="known_dummy_place_id",
                                message=f"Known dummy Google Place ID '{gid}' used for '{loc.title}'",
                                product_id=pid,
                            ))
                        elif gid in all_place_ids and len(all_place_ids[gid]) > 1:
                            issues.append(QualityIssue(
                                dimension="realism",
                                check="duplicate_place_id",
                                message=f"Google Place ID '{gid}' reused across products: "
                                        f"{all_place_ids[gid]}",
                                product_id=pid,
                            ))
                        else:
                            checks_passed += 1

    # --- Currency ↔ Country ---
    if product.country and product.default_currency:
        checks_total += 1
        expected = _COUNTRY_CURRENCIES.get(product.country)
        if expected is None or product.default_currency in expected:
            checks_passed += 1
        else:
            issues.append(QualityIssue(
                dimension="realism",
                check="currency_country_mismatch",
                message=f"Currency '{product.default_currency}' unexpected for "
                        f"country '{product.country}' (expected {expected})",
                product_id=pid,
            ))

    # --- Timezone ↔ Country ---
    if product.country and product.time_zone:
        checks_total += 1
        prefixes = _COUNTRY_TZ_PREFIXES.get(product.country)
        if prefixes is None or any(product.time_zone.startswith(p) for p in prefixes):
            checks_passed += 1
        else:
            issues.append(QualityIssue(
                dimension="realism",
                check="timezone_country_mismatch",
                message=f"Timezone '{product.time_zone}' unexpected for "
                        f"country '{product.country}' (expected prefix {prefixes})",
                product_id=pid,
            ))

    # --- ADULT age range consistency ---
    for option in product.options:
        has_child_with_ages = any(
            u.type == UnitType.CHILD
            and (u.restrictions.min_age > 0 or u.restrictions.max_age > 0)
            for u in option.units
        )
        for unit in option.units:
            if unit.type == UnitType.ADULT:
                checks_total += 1
                r = unit.restrictions
                if has_child_with_ages and r.min_age == 0 and r.max_age == 0:
                    issues.append(QualityIssue(
                        dimension="realism",
                        check="adult_age_range",
                        message=f"ADULT unit '{unit.id}' in option '{option.internal_name}' "
                                f"has minAge=0/maxAge=0 while sibling CHILD has real age ranges",
                        product_id=pid,
                    ))
                else:
                    checks_passed += 1

    # --- Child price > Adult price ---
    for option in product.options:
        adult_prices = []
        child_prices = []
        for unit in option.units:
            if unit.pricing_from:
                price = unit.pricing_from[0].original
                if unit.type == UnitType.ADULT:
                    adult_prices.append(price)
                elif unit.type == UnitType.CHILD:
                    child_prices.append(price)
        if adult_prices and child_prices:
            checks_total += 1
            if max(child_prices) <= min(adult_prices):
                checks_passed += 1
            else:
                issues.append(QualityIssue(
                    dimension="realism",
                    check="child_price_exceeds_adult",
                    message=f"CHILD price ({max(child_prices)}) > ADULT price "
                            f"({min(adult_prices)}) in option '{option.internal_name}'",
                    product_id=pid,
                ))

    score = checks_passed / checks_total if checks_total > 0 else 1.0
    return score, issues


# ---------------------------------------------------------------------------
# Coherence checks
# ---------------------------------------------------------------------------

def _option_duration_minutes(option) -> Optional[float]:
    """Convert option duration to minutes, or None if not set."""
    if option.duration_amount is None or option.duration_unit is None:
        return None
    try:
        amount = float(option.duration_amount)
    except (ValueError, TypeError):
        return None
    unit = option.duration_unit.value  # "hour", "minute", "day"
    if unit == "hour":
        return amount * 60
    elif unit == "minute":
        return amount
    elif unit == "day":
        return amount * 1440
    return None


def _check_coherence(product: Product) -> tuple[float, list[QualityIssue]]:
    """Score coherence for a single product. Returns (score, issues)."""
    checks_passed = 0
    checks_total = 0
    issues: list[QualityIssue] = []
    pid = product.id

    # --- Duration alignment ---
    if product.duration_minutes_from is not None and product.duration_minutes_to is not None:
        for option in product.options:
            dur = _option_duration_minutes(option)
            if dur is not None:
                checks_total += 1
                if product.duration_minutes_from <= dur <= product.duration_minutes_to:
                    checks_passed += 1
                else:
                    issues.append(QualityIssue(
                        dimension="coherence",
                        check="duration_mismatch",
                        message=f"Option '{option.internal_name}' duration {dur}min "
                                f"outside product range [{product.duration_minutes_from}, "
                                f"{product.duration_minutes_to}]",
                        product_id=pid,
                    ))

    # --- Contact-delivery consistency ---
    # If description mentions email delivery, at least one option should require emailAddress
    desc_text = (product.description or "").lower()
    if "email" in desc_text and ("deliver" in desc_text or "boarding pass" in desc_text or "ticket" in desc_text):
        checks_total += 1
        any_requires_email = any(
            "emailAddress" in [f.value if hasattr(f, "value") else f
                               for f in opt.required_contact_fields]
            for opt in product.options
        )
        if any_requires_email:
            checks_passed += 1
        else:
            issues.append(QualityIssue(
                dimension="coherence",
                check="email_delivery_no_contact",
                message="Description mentions email delivery but no option requires emailAddress",
                product_id=pid,
            ))

    # --- FAQ-pricing consistency ---
    if product.faqs:
        for faq in product.faqs:
            answer_lower = faq.answer.lower()
            # Look for "free" + age pattern
            free_match = re.search(
                r"(?:children|kids?)\s+(?:under|below|aged?)\s+(\d+)\s+.*?free",
                answer_lower,
            )
            if not free_match:
                free_match = re.search(
                    r"free\s+.*?(?:children|kids?)\s+(?:under|below|aged?)\s+(\d+)",
                    answer_lower,
                )
            if free_match:
                free_age = int(free_match.group(1))
                checks_total += 1
                # Check if any CHILD unit covers that age range and has a price
                has_priced_child = False
                for option in product.options:
                    for unit in option.units:
                        if unit.type == UnitType.CHILD and unit.pricing_from:
                            price = unit.pricing_from[0].original
                            if price > 0 and unit.restrictions.min_age < free_age:
                                has_priced_child = True
                if has_priced_child:
                    issues.append(QualityIssue(
                        dimension="coherence",
                        check="faq_pricing_contradiction",
                        message=f"FAQ claims free for children under {free_age} "
                                f"but CHILD unit is priced",
                        product_id=pid,
                    ))
                else:
                    checks_passed += 1

    # --- Option default logic: at most one default ---
    defaults_count = sum(1 for opt in product.options if opt.default)
    checks_total += 1
    if defaults_count <= 1:
        checks_passed += 1
    else:
        issues.append(QualityIssue(
            dimension="coherence",
            check="multiple_defaults",
            message=f"{defaults_count} options have default=true (expected at most 1)",
            product_id=pid,
        ))

    # --- Cancellation cutoff amount matches string ---
    for option in product.options:
        checks_total += 1
        cutoff_match = re.match(r"(\d+)\s+\w+", option.cancellation_cutoff)
        if cutoff_match:
            parsed_amount = int(cutoff_match.group(1))
            if parsed_amount == option.cancellation_cutoff_amount:
                checks_passed += 1
            else:
                issues.append(QualityIssue(
                    dimension="coherence",
                    check="cutoff_amount_mismatch",
                    message=f"Option '{option.internal_name}' cutoff string "
                            f"'{option.cancellation_cutoff}' implies amount {parsed_amount} "
                            f"but cancellationCutoffAmount={option.cancellation_cutoff_amount}",
                    product_id=pid,
                ))
        else:
            checks_passed += 1  # can't parse, don't penalize

    score = checks_passed / checks_total if checks_total > 0 else 1.0
    return score, issues


# ---------------------------------------------------------------------------
# Completeness checks
# ---------------------------------------------------------------------------

# (field_accessor, weight)
_COMPLETENESS_FIELDS: list[tuple[str, int]] = [
    ("description", 2),
    ("features", 1),
    ("faqs", 1),
    ("media_cover", 2),
    ("locations", 2),
    ("category_labels", 1),
    ("commentary", 1),
    ("option_descriptions", 1),
]


def _check_completeness(product: Product) -> tuple[float, list[QualityIssue]]:
    """Score completeness for a single product. Returns (score, issues)."""
    issues: list[QualityIssue] = []
    pid = product.id
    weighted_sum = 0.0
    weight_total = 0

    for field_name, weight in _COMPLETENESS_FIELDS:
        weight_total += weight
        present = False

        if field_name == "description":
            present = bool(product.description and len(product.description.strip()) > 10)
        elif field_name == "features":
            present = bool(product.features and len(product.features) > 0)
        elif field_name == "faqs":
            present = bool(product.faqs and len(product.faqs) > 0)
        elif field_name == "media_cover":
            if product.media:
                present = any(m.rel.value == "COVER" for m in product.media if m.rel)
        elif field_name == "locations":
            present = bool(product.locations and len(product.locations) > 0)
        elif field_name == "category_labels":
            present = bool(product.category_labels and len(product.category_labels) > 0)
        elif field_name == "commentary":
            present = bool(product.commentary and len(product.commentary) > 0)
        elif field_name == "option_descriptions":
            if product.options:
                opts_with_desc = sum(
                    1 for o in product.options if o.short_description
                )
                present = opts_with_desc == len(product.options)

        if present:
            weighted_sum += weight
        else:
            issues.append(QualityIssue(
                dimension="completeness",
                check=f"missing_{field_name}",
                message=f"Missing or empty: {field_name}",
                product_id=pid,
            ))

    score = weighted_sum / weight_total if weight_total > 0 else 1.0
    return score, issues


# ---------------------------------------------------------------------------
# Diversity checks (batch-level)
# ---------------------------------------------------------------------------

def _check_diversity(products: list[Product]) -> tuple[float, list[QualityIssue]]:
    """Score diversity across a batch. Returns (score, issues)."""
    if not products:
        return 1.0, []

    issues: list[QualityIssue] = []
    n = len(products)
    sub_scores: list[float] = []

    # --- Unique countries ---
    countries = [p.country for p in products if p.country]
    unique_countries = len(set(countries))
    country_score = unique_countries / n if n > 0 else 1.0
    sub_scores.append(country_score)
    if unique_countries < n and n > 1:
        issues.append(QualityIssue(
            dimension="diversity",
            check="low_country_diversity",
            message=f"Only {unique_countries}/{n} unique countries",
        ))

    # --- Unique titles (case-insensitive) ---
    titles = [p.title or p.internal_name for p in products]
    titles_lower = [t.strip().lower() for t in titles]
    unique_titles = len(set(titles_lower))
    title_score = unique_titles / n if n > 0 else 1.0
    sub_scores.append(title_score)
    if unique_titles < n:
        dupes = [t for t, tl in zip(titles, titles_lower) if titles_lower.count(tl) > 1]
        issues.append(QualityIssue(
            dimension="diversity",
            check="duplicate_titles",
            message=f"Duplicate product titles: {set(dupes)}",
        ))

    # --- Unique descriptions ---
    descriptions = [
        (p.description or "").strip().lower() for p in products
        if p.description and p.description.strip()
    ]
    if len(descriptions) > 1:
        unique_descs = len(set(descriptions))
        desc_score = unique_descs / len(descriptions)
        sub_scores.append(desc_score)
        if unique_descs < len(descriptions):
            issues.append(QualityIssue(
                dimension="diversity",
                check="duplicate_descriptions",
                message=f"Only {unique_descs}/{len(descriptions)} unique descriptions in batch",
            ))

    # --- Availability type spread ---
    avail_types = [p.availability_type.value for p in products]
    type_counts: dict[str, int] = {}
    for t in avail_types:
        type_counts[t] = type_counts.get(t, 0) + 1
    if len(type_counts) > 1:
        min_count = min(type_counts.values())
        max_count = max(type_counts.values())
        avail_score = min_count / max_count  # 1.0 = perfect balance
    else:
        avail_score = 0.0 if n > 1 else 1.0
    sub_scores.append(avail_score)
    if avail_score < 0.5 and n > 2:
        issues.append(QualityIssue(
            dimension="diversity",
            check="availability_type_imbalance",
            message=f"Availability type distribution: {type_counts}",
        ))

    # --- Category spread ---
    all_categories: list[str] = []
    for p in products:
        if p.category_labels:
            all_categories.extend(p.category_labels)
    unique_cats = len(set(all_categories))
    cat_score = unique_cats / len(all_categories) if all_categories else 1.0
    sub_scores.append(cat_score)

    # --- Currency spread ---
    currencies = [p.default_currency for p in products if p.default_currency]
    unique_currencies = len(set(currencies))
    currency_score = unique_currencies / n if n > 0 else 1.0
    sub_scores.append(currency_score)

    diversity = sum(sub_scores) / len(sub_scores) if sub_scores else 1.0
    return diversity, issues


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Composite weights
_W_REALISM = 0.30
_W_COHERENCE = 0.30
_W_COMPLETENESS = 0.20
_W_DIVERSITY = 0.20


class QualityScorer:
    """Deterministic quality scorer for generated OCTO products."""

    def score_batch(self, products: list[Product]) -> BatchScore:
        """Score a batch of products across all quality dimensions."""
        # Build place-ID index for cross-product duplicate detection
        place_id_index: dict[str, list[str]] = {}
        for p in products:
            if p.locations:
                for loc in p.locations:
                    if loc.place and loc.place.identifiers:
                        gid = loc.place.identifiers.get("googlePlaceId")
                        if gid:
                            label = p.title or p.internal_name
                            place_id_index.setdefault(gid, []).append(label)

        product_scores: list[ProductScore] = []
        for product in products:
            ps = ProductScore(
                product_id=product.id,
                title=product.title or product.internal_name,
            )

            realism, r_issues = _check_realism(product, place_id_index)
            ps.realism = realism
            ps.issues.extend(r_issues)

            coherence, c_issues = _check_coherence(product)
            ps.coherence = coherence
            ps.issues.extend(c_issues)

            completeness, comp_issues = _check_completeness(product)
            ps.completeness = completeness
            ps.issues.extend(comp_issues)

            product_scores.append(ps)

        diversity, d_issues = _check_diversity(products)

        batch = BatchScore(
            product_scores=product_scores,
            diversity=diversity,
            issues=d_issues,
        )

        # Composite
        batch.composite = (
            _W_REALISM * batch.avg_realism
            + _W_COHERENCE * batch.avg_coherence
            + _W_COMPLETENESS * batch.avg_completeness
            + _W_DIVERSITY * batch.diversity
        )

        logger.info(
            "Quality scores — realism=%.2f coherence=%.2f completeness=%.2f "
            "diversity=%.2f composite=%.2f issues=%d",
            batch.avg_realism,
            batch.avg_coherence,
            batch.avg_completeness,
            batch.diversity,
            batch.composite,
            len(batch.all_issues),
        )

        return batch
