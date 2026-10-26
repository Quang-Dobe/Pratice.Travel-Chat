from q_agent.conversations.title import make_title


def test_short_text_is_unchanged() -> None:
    assert make_title("Trip to Hue") == "Trip to Hue"


def test_whitespace_is_collapsed_and_trimmed() -> None:
    assert make_title("  Trip \n\t to   Hue  ") == "Trip to Hue"


def test_text_of_exactly_fifty_characters_is_unchanged() -> None:
    text = "a" * 49 + "b"

    assert make_title(text) == text


def test_long_text_is_cut_at_a_word_boundary() -> None:
    text = "plan a relaxing week long trip through the mountains of northern Vietnam"

    title = make_title(text)

    assert title == "plan a relaxing week long trip through the…"
    assert len(title) <= 51


def test_word_ending_exactly_at_fifty_is_kept() -> None:
    first = "a" * 30 + " " + "b" * 19
    text = first + " ccccc"

    assert make_title(text) == first + "…"


def test_unfinished_word_at_the_cut_is_dropped() -> None:
    text = "a" * 49 + " " + "b" * 10

    assert make_title(text) == "a" * 49 + "…"


def test_single_long_word_is_hard_cut() -> None:
    assert make_title("x" * 80) == "x" * 50 + "…"
