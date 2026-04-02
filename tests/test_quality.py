"""Tests for the deterministic quality scorer."""

import pytest

from models.product import (
    AvailabilityType,
    DeliveryFormat,
    DeliveryMethod,
    Feature,
    FeatureType,
    FAQ,
    Location,
    LocationType,
    Media,
    MediaRel,
    Option,
    Place,
    Product,
    RedemptionMethod,
    Unit,
    UnitType,
)
from seeder.quality import (
    BatchScore,
    QualityScorer,
    _check_coherence,
    _check_completeness,
    _check_realism,
    _check_diversity,
    _coord_digit_entropy,
    _is_city_centroid,
    _is_sequential_digits,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_unit(**overrides) -> dict:
    defaults = dict(
        id="unit-1",
        internalName="Adult",
        reference=None,
        type="ADULT",
        requiredContactFields=[],
        restrictions={
            "minAge": 18, "maxAge": 99, "idRequired": False,
            "minQuantity": None, "maxQuantity": None,
            "paxCount": 1, "accompaniedBy": [],
        },
        pricingFrom=[{
            "original": 3500, "retail": 3500, "net": None,
            "currency": "USD", "currencyPrecision": 2, "includedTaxes": [],
        }],
        title="Adult",
        titlePlural="Adults",
        subtitle="18+ Years",
    )
    defaults.update(overrides)
    return defaults


def _make_child_unit(**overrides) -> dict:
    defaults = dict(
        id="unit-child-1",
        internalName="Child",
        reference=None,
        type="CHILD",
        requiredContactFields=[],
        restrictions={
            "minAge": 5, "maxAge": 12, "idRequired": False,
            "minQuantity": None, "maxQuantity": None,
            "paxCount": 1, "accompaniedBy": [],
        },
        pricingFrom=[{
            "original": 2000, "retail": 2000, "net": None,
            "currency": "USD", "currencyPrecision": 2, "includedTaxes": [],
        }],
        title="Child",
        titlePlural="Children",
        subtitle="5-12 Years",
    )
    defaults.update(overrides)
    return defaults


def _make_option(**overrides) -> dict:
    defaults = dict(
        id="opt-1",
        default=False,
        internalName="Standard",
        reference=None,
        availabilityLocalStartTimes=["09:00"],
        cancellationCutoff="24 hours",
        cancellationCutoffAmount=24,
        cancellationCutoffUnit="hour",
        requiredContactFields=[],
        restrictions={"minUnits": 1, "maxUnits": None},
        units=[_make_unit()],
        title="Standard Tour",
        shortDescription="A standard tour experience.",
    )
    defaults.update(overrides)
    return defaults


def _make_product(**overrides) -> Product:
    defaults = dict(
        id="prod-1",
        internalName="City Tour",
        reference=None,
        locale="en",
        timeZone="America/New_York",
        allowFreesale=False,
        instantConfirmation=True,
        instantDelivery=True,
        availabilityRequired=True,
        availabilityType=AvailabilityType.START_TIME,
        deliveryFormats=[DeliveryFormat.QRCODE],
        deliveryMethods=[DeliveryMethod.TICKET],
        redemptionMethod=RedemptionMethod.DIGITAL,
        options=[_make_option()],
        defaultCurrency="USD",
        availableCurrencies=["USD"],
        pricingPer="UNIT",
        includeTax=True,
        title="City Tour",
        shortDescription="A guided city tour.",
        description="<p>Explore the city with an expert guide.</p>",
        features=[{"shortDescription": "Guided tour", "type": "HIGHLIGHT"}],
        faqs=[{"question": "Is it fun?", "answer": "Yes, very much."}],
        media=[{"src": "https://example.com/cover.jpg", "type": "image/jpeg", "rel": "COVER"}],
        locations=[{
            "title": "Meeting Point",
            "types": ["START"],
            "place": {
                "latitude": 40.748817,
                "longitude": -73.985428,
                "postalAddress": "350 5th Ave, New York, NY 10118",
                "identifiers": {"googlePlaceId": "ChIJtcaxrqlZwokRk0lDuqMGXxs"},
            },
        }],
        categoryLabels=["WALKING_TOUR", "CULTURAL"],
        durationMinutesFrom=60,
        durationMinutesTo=90,
        commentary=[{"format": "audio", "language": "en"}],
        country="US",
    )
    defaults.update(overrides)
    return Product(**defaults)


# ---------------------------------------------------------------------------
# Utility function tests
# ---------------------------------------------------------------------------

class TestCoordDigitEntropy:
    def test_high_entropy_real_coord(self):
        # Real-looking coordinate with varied digits
        assert _coord_digit_entropy(40.748817) > 1.5

    def test_low_entropy_placeholder(self):
        # Sequential placeholder — entropy is moderate but caught by sequential check
        assert _coord_digit_entropy(48.123456) < 2.6

    def test_repeated_digits(self):
        # All same digit
        assert _coord_digit_entropy(48.111111) < 0.01


class TestIsSequentialDigits:
    def test_sequential(self):
        assert _is_sequential_digits(48.123456) is True

    def test_not_sequential(self):
        assert _is_sequential_digits(40.748817) is False

    def test_all_same(self):
        # All same digit: diffs are all 0 → sequential by definition
        assert _is_sequential_digits(48.111111) is True


class TestIsCityCentroid:
    def test_london_centroid(self):
        assert _is_city_centroid(51.5074, -0.1278) is True

    def test_new_york_centroid(self):
        assert _is_city_centroid(40.7128, -74.0060) is True

    def test_specific_venue(self):
        # Empire State Building — not a centroid
        assert _is_city_centroid(40.748817, -73.985428) is False


# ---------------------------------------------------------------------------
# Realism tests
# ---------------------------------------------------------------------------

class TestRealism:
    def test_good_product_scores_high(self):
        p = _make_product()
        score, issues = _check_realism(p, {})
        assert score >= 0.8
        assert len(issues) == 0

    def test_city_centroid_flagged(self):
        p = _make_product(locations=[{
            "title": "Central London",
            "types": ["START"],
            "place": {
                "latitude": 51.5074,
                "longitude": -0.1278,
                "identifiers": {"googlePlaceId": "ChIJdd4hrwug2EcRmSrV3Vo6llI_unique"},
            },
        }])
        score, issues = _check_realism(p, {})
        centroid_issues = [i for i in issues if i.check == "city_centroid"]
        assert len(centroid_issues) == 1

    def test_sequential_coords_flagged(self):
        p = _make_product(locations=[{
            "title": "Cave Park",
            "types": ["START"],
            "place": {
                "latitude": 48.123456,
                "longitude": 11.234567,
                "identifiers": {"googlePlaceId": "ChIJunique123"},
            },
        }])
        score, issues = _check_realism(p, {})
        seq_issues = [i for i in issues if i.check == "coordinate_sequential"]
        assert len(seq_issues) >= 1

    def test_known_dummy_place_id_flagged(self):
        p = _make_product(locations=[{
            "title": "Some Place",
            "types": ["START"],
            "place": {
                "latitude": 40.748817,
                "longitude": -73.985428,
                "identifiers": {"googlePlaceId": "ChIJN1t_tDeuEmsRUsoyG83frY4"},
            },
        }])
        score, issues = _check_realism(p, {})
        dummy_issues = [i for i in issues if i.check == "known_dummy_place_id"]
        assert len(dummy_issues) == 1

    def test_duplicate_place_id_flagged(self):
        gid = "ChIJsomeSharedId12345678901"
        place_index = {gid: ["Product A", "Product B"]}
        p = _make_product(locations=[{
            "title": "Shared Place",
            "types": ["START"],
            "place": {
                "latitude": 40.748817,
                "longitude": -73.985428,
                "identifiers": {"googlePlaceId": gid},
            },
        }])
        score, issues = _check_realism(p, place_index)
        dup_issues = [i for i in issues if i.check == "duplicate_place_id"]
        assert len(dup_issues) == 1

    def test_currency_country_mismatch(self):
        p = _make_product(country="GB", defaultCurrency="USD")
        score, issues = _check_realism(p, {})
        mismatch = [i for i in issues if i.check == "currency_country_mismatch"]
        assert len(mismatch) == 1

    def test_timezone_country_mismatch(self):
        p = _make_product(country="US", timeZone="Europe/London")
        score, issues = _check_realism(p, {})
        mismatch = [i for i in issues if i.check == "timezone_country_mismatch"]
        assert len(mismatch) == 1

    def test_adult_age_range_with_child_sibling(self):
        """ADULT 0/0 flagged when CHILD has real ages."""
        p = _make_product(options=[_make_option(units=[
            _make_unit(restrictions={
                "minAge": 0, "maxAge": 0, "idRequired": False,
                "minQuantity": None, "maxQuantity": None,
                "paxCount": 1, "accompaniedBy": [],
            }),
            _make_child_unit(),
        ])])
        score, issues = _check_realism(p, {})
        age_issues = [i for i in issues if i.check == "adult_age_range"]
        assert len(age_issues) == 1

    def test_child_price_exceeds_adult(self):
        p = _make_product(options=[_make_option(units=[
            _make_unit(pricingFrom=[{
                "original": 2000, "retail": 2000, "net": None,
                "currency": "USD", "currencyPrecision": 2, "includedTaxes": [],
            }]),
            _make_child_unit(pricingFrom=[{
                "original": 3000, "retail": 3000, "net": None,
                "currency": "USD", "currencyPrecision": 2, "includedTaxes": [],
            }]),
        ])])
        score, issues = _check_realism(p, {})
        price_issues = [i for i in issues if i.check == "child_price_exceeds_adult"]
        assert len(price_issues) == 1


# ---------------------------------------------------------------------------
# Coherence tests
# ---------------------------------------------------------------------------

class TestCoherence:
    def test_good_product_scores_high(self):
        p = _make_product(
            durationMinutesFrom=60,
            durationMinutesTo=90,
            options=[_make_option(
                durationAmount="75",
                durationUnit="minute",
            )],
        )
        score, issues = _check_coherence(p)
        assert score >= 0.8

    def test_duration_mismatch_flagged(self):
        p = _make_product(
            durationMinutesFrom=150,
            durationMinutesTo=180,
            options=[_make_option(
                durationAmount="90",
                durationUnit="minute",
            )],
        )
        score, issues = _check_coherence(p)
        dur_issues = [i for i in issues if i.check == "duration_mismatch"]
        assert len(dur_issues) == 1

    def test_duration_hours_converted(self):
        """3 hours = 180 min, should fit in [150, 210]."""
        p = _make_product(
            durationMinutesFrom=150,
            durationMinutesTo=210,
            options=[_make_option(
                durationAmount="3",
                durationUnit="hour",
            )],
        )
        score, issues = _check_coherence(p)
        dur_issues = [i for i in issues if i.check == "duration_mismatch"]
        assert len(dur_issues) == 0

    def test_email_delivery_no_contact_flagged(self):
        p = _make_product(
            description="<p>Your ticket will be delivered via email.</p>",
            options=[_make_option(requiredContactFields=[])],
        )
        score, issues = _check_coherence(p)
        email_issues = [i for i in issues if i.check == "email_delivery_no_contact"]
        assert len(email_issues) == 1

    def test_email_delivery_with_contact_ok(self):
        p = _make_product(
            description="<p>Your ticket will be delivered via email.</p>",
            options=[_make_option(requiredContactFields=["emailAddress"])],
        )
        score, issues = _check_coherence(p)
        email_issues = [i for i in issues if i.check == "email_delivery_no_contact"]
        assert len(email_issues) == 0

    def test_faq_pricing_contradiction(self):
        p = _make_product(
            faqs=[{
                "question": "Is it free for kids?",
                "answer": "Yes, children under 12 can join for free.",
            }],
            options=[_make_option(units=[
                _make_unit(),
                _make_child_unit(),  # priced at 2000, ages 5-12
            ])],
        )
        score, issues = _check_coherence(p)
        faq_issues = [i for i in issues if i.check == "faq_pricing_contradiction"]
        assert len(faq_issues) == 1

    def test_cutoff_amount_mismatch(self):
        p = _make_product(options=[_make_option(
            cancellationCutoff="48 hours",
            cancellationCutoffAmount=24,
        )])
        score, issues = _check_coherence(p)
        cutoff_issues = [i for i in issues if i.check == "cutoff_amount_mismatch"]
        assert len(cutoff_issues) == 1

    def test_multiple_defaults_flagged(self):
        p = _make_product(options=[
            _make_option(id="opt-1", default=True),
            _make_option(id="opt-2", default=True, internalName="Premium"),
        ])
        score, issues = _check_coherence(p)
        default_issues = [i for i in issues if i.check == "multiple_defaults"]
        assert len(default_issues) == 1


# ---------------------------------------------------------------------------
# Completeness tests
# ---------------------------------------------------------------------------

class TestCompleteness:
    def test_full_product_scores_high(self):
        p = _make_product()
        score, issues = _check_completeness(p)
        assert score >= 0.9

    def test_missing_description_penalized(self):
        p = _make_product(description=None)
        score, issues = _check_completeness(p)
        desc_issues = [i for i in issues if i.check == "missing_description"]
        assert len(desc_issues) == 1

    def test_missing_media_cover_penalized(self):
        p = _make_product(media=[
            {"src": "https://example.com/gallery.jpg", "type": "image/jpeg", "rel": "GALLERY"}
        ])
        score, issues = _check_completeness(p)
        media_issues = [i for i in issues if i.check == "missing_media_cover"]
        assert len(media_issues) == 1

    def test_empty_product_scores_low(self):
        p = _make_product(
            description=None,
            features=None,
            faqs=None,
            media=None,
            locations=None,
            categoryLabels=None,
            commentary=None,
            options=[_make_option(shortDescription=None)],
        )
        score, issues = _check_completeness(p)
        assert score < 0.1


# ---------------------------------------------------------------------------
# Diversity tests
# ---------------------------------------------------------------------------

class TestDiversity:
    def test_diverse_batch(self):
        products = [
            _make_product(id="p1", title="River Cruise", country="US",
                          availabilityType=AvailabilityType.START_TIME,
                          defaultCurrency="USD",
                          categoryLabels=["CRUISE"]),
            _make_product(id="p2", title="Castle Tour", country="GB",
                          availabilityType=AvailabilityType.OPENING_HOURS,
                          defaultCurrency="GBP", timeZone="Europe/London",
                          categoryLabels=["HISTORICAL"]),
            _make_product(id="p3", title="Cave Adventure", country="DE",
                          availabilityType=AvailabilityType.START_TIME,
                          defaultCurrency="EUR", timeZone="Europe/Berlin",
                          categoryLabels=["ADVENTURE"]),
        ]
        score, issues = _check_diversity(products)
        assert score >= 0.7

    def test_duplicate_titles_flagged(self):
        products = [
            _make_product(id="p1", title="Same Tour"),
            _make_product(id="p2", title="Same Tour"),
        ]
        score, issues = _check_diversity(products)
        dup_issues = [i for i in issues if i.check == "duplicate_titles"]
        assert len(dup_issues) == 1

    def test_single_product_batch(self):
        products = [_make_product()]
        score, issues = _check_diversity(products)
        assert score >= 0.5  # single product can't be diverse but shouldn't crash

    def test_all_same_availability_type(self):
        products = [
            _make_product(id="p1", title="Tour A",
                          availabilityType=AvailabilityType.START_TIME),
            _make_product(id="p2", title="Tour B",
                          availabilityType=AvailabilityType.START_TIME),
            _make_product(id="p3", title="Tour C",
                          availabilityType=AvailabilityType.START_TIME),
        ]
        score, issues = _check_diversity(products)
        avail_issues = [i for i in issues if i.check == "availability_type_imbalance"]
        assert len(avail_issues) == 1


# ---------------------------------------------------------------------------
# Integration: QualityScorer.score_batch
# ---------------------------------------------------------------------------

class TestQualityScorer:
    def test_score_batch_returns_all_dimensions(self):
        products = [
            _make_product(id="p1", title="Tour A"),
            _make_product(id="p2", title="Tour B", country="GB",
                          defaultCurrency="GBP", timeZone="Europe/London",
                          availabilityType=AvailabilityType.OPENING_HOURS,
                          options=[_make_option(availabilityLocalStartTimes=[])],
                          categoryLabels=["HISTORICAL"]),
        ]
        scorer = QualityScorer()
        batch = scorer.score_batch(products)

        assert isinstance(batch, BatchScore)
        assert len(batch.product_scores) == 2
        assert 0.0 <= batch.avg_realism <= 1.0
        assert 0.0 <= batch.avg_coherence <= 1.0
        assert 0.0 <= batch.avg_completeness <= 1.0
        assert 0.0 <= batch.diversity <= 1.0
        assert 0.0 <= batch.composite <= 1.0

    def test_score_batch_empty(self):
        scorer = QualityScorer()
        batch = scorer.score_batch([])
        # Empty batch: avg scores are 0.0 but diversity defaults to 1.0
        assert batch.avg_realism == 0.0
        assert batch.avg_coherence == 0.0
        assert batch.diversity == 1.0

    def test_problematic_batch_scores_lower(self):
        """A batch with known issues should score lower than a clean batch."""
        clean = [
            _make_product(id="p1", title="River Cruise", country="US",
                          defaultCurrency="USD",
                          availabilityType=AvailabilityType.START_TIME,
                          categoryLabels=["CRUISE"]),
            _make_product(id="p2", title="Castle Tour", country="GB",
                          defaultCurrency="GBP", timeZone="Europe/London",
                          availabilityType=AvailabilityType.OPENING_HOURS,
                          options=[_make_option(availabilityLocalStartTimes=[])],
                          categoryLabels=["HISTORICAL"]),
        ]

        problematic = [
            _make_product(
                id="p3", title="Bad Tour", country="US",
                defaultCurrency="EUR",  # wrong currency
                locations=[{
                    "title": "Fake Place",
                    "types": ["START"],
                    "place": {
                        "latitude": 48.123456,  # sequential
                        "longitude": 11.234567,
                        "identifiers": {"googlePlaceId": "ChIJN1t_tDeuEmsRUsoyG83frY4"},
                    },
                }],
                durationMinutesFrom=150,
                durationMinutesTo=180,
                options=[_make_option(durationAmount="90", durationUnit="minute")],
            ),
            _make_product(
                id="p4", title="Bad Tour",  # duplicate title
                country="US", defaultCurrency="EUR",
            ),
        ]

        scorer = QualityScorer()
        clean_score = scorer.score_batch(clean)
        bad_score = scorer.score_batch(problematic)

        assert clean_score.composite > bad_score.composite
