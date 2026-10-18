from bot import texts
from bot.catalog import IMAGE_MODELS, MODEL_CATEGORIES, TARIFFS, TEXT_MODELS, VIDEO_MODELS


def test_model_counts_match_docs():
    assert len(TEXT_MODELS) == 32
    assert len(IMAGE_MODELS) == 8
    assert len(VIDEO_MODELS) == 9


def test_model_ids_unique():
    ids = [m.id for m in (*TEXT_MODELS, *IMAGE_MODELS, *VIDEO_MODELS)]
    assert len(ids) == len(set(ids))


def test_tariff_prices():
    assert {c: t.price_usd for c, t in TARIFFS.items()} == {
        "start": 18,
        "creator": 25,
        "pro": 33,
        "unlimited": 80,
    }
    assert TARIFFS["pro"].stars(77) == 2541


def test_hidden_tariffs_cannot_be_paid():
    from bot.handlers.payments import tariff_from_payload

    assert tariff_from_payload("tariff:pro") is TARIFFS["pro"]
    assert tariff_from_payload("tariff:start") is None
    assert tariff_from_payload("tariff:creator") is None


def test_model_lists_fit_in_one_message():
    for category in MODEL_CATEGORIES:
        assert len(texts.models_list(category)) < 4096
