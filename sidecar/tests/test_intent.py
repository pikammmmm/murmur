import pytest

from murmur_sidecar.intent import detect_profile, listify


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Dear John, thanks for your help. Regards, Tom", "email"),
        ("write an email to my boss saying I'll be late", "email"),
        ("send this email to the team", "email"),
        ("subject: meeting tomorrow at noon", "email"),
        ("to whom it may concern, I am writing about", "email"),
        ("shopping list milk eggs bread", "list"),
        ("here's my grocery list, milk, eggs, and bread", "list"),
        ("I need to buy milk, eggs, and bread", "list"),
        ("add this to my list", "list"),
        ("can you fix the bug in the parser", "generic"),
        ("hey what's up, are we still on for later", "generic"),  # greeting alone != email
        ("the dear price of gas these days", "generic"),          # 'dear' not followed by a name
    ],
)
def test_detect_profile(text, expected):
    assert detect_profile(text) == expected


def test_detect_profile_keeps_app_fallback():
    assert detect_profile("just some notes here", "notes") == "notes"
    assert detect_profile("", "code") == "code"


def test_listify_splits_enumerated_items():
    assert listify("shopping list: milk, eggs, bread, and butter") == "- milk\n- eggs\n- bread\n- butter"


def test_listify_strips_buy_prefix():
    assert listify("I need to buy milk, eggs, and bread") == "- milk\n- eggs\n- bread"


def test_listify_single_item_left_for_llm():
    # no delimiters -> can't confidently split, leave as-is
    assert listify("shopping list milk eggs bread") == "shopping list milk eggs bread"
