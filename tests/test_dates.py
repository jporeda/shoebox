import pytest

from shoebox.dates import DateParseError, export_calendar_date, format_partial, parse_partial_date, xmp_partial


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1987", (1987, None, None, 1987, 1987, "year")),
        ("1987-06", (1987, 6, None, 1987, 1987, "month")),
        ("1987-06-14", (1987, 6, 14, 1987, 1987, "day")),
        ("1980s", (1980, None, None, 1980, 1989, "decade")),
        ("1985-1988", (1986, None, None, 1985, 1988, "range")),
        ("1988 - 1985", (1986, None, None, 1985, 1988, "range")),
        ("~1987", (1987, None, None, 1985, 1989, "range")),
        ("c. 1987", (1987, None, None, 1985, 1989, "range")),
    ],
)
def test_parse(text, expected):
    pd = parse_partial_date(text)
    assert (pd.year, pd.month, pd.day, pd.min, pd.max, pd.precision) == expected


@pytest.mark.parametrize("text", ["", "87", "1987-13", "1987-02-30", "1700", "summer"])
def test_parse_rejects(text):
    with pytest.raises(DateParseError):
        parse_partial_date(text)


def test_round_trip_labels():
    for text in ["1987", "1987-06", "1987-06-14", "1980s", "1985-1988"]:
        pd = parse_partial_date(text)
        assert format_partial(pd.year, pd.month, pd.day, pd.precision, pd.min, pd.max) == text


def test_export_rules():
    assert export_calendar_date(1987, None, None, "year") == (1987, 7, 1)
    assert export_calendar_date(1980, None, None, "decade") == (1980, 1, 1)
    assert export_calendar_date(1987, 6, None, "month") == (1987, 6, 1)
    assert export_calendar_date(1987, 6, 14, "day") == (1987, 6, 14)
    assert export_calendar_date(1986, None, None, "range") == (1986, 7, 1)
    assert xmp_partial(1987, None, None, "year") == "1987"
    assert xmp_partial(1987, 6, None, "month") == "1987:06"


@pytest.mark.parametrize("text", ["1987", "1987-06", "1987-06-14", "1980s", "1985-1988", "~1987"])
def test_a_shown_date_reads_back_as_the_same_date(text):
    """The photo page starts its date box with the photo's date as shown: it must parse to itself."""
    pd = parse_partial_date(text)
    shown = format_partial(pd.year, pd.month, pd.day, pd.precision, pd.min, pd.max)
    again = parse_partial_date(shown)
    assert (again.year, again.month, again.day, again.min, again.max, again.precision) == (
        pd.year, pd.month, pd.day, pd.min, pd.max, pd.precision)


def test_months_parse_label_and_round_trip():
    from shoebox.dates import months_label, with_months

    assert months_label([12, 6, 7, 8]) == "Jun–Aug, Dec"
    pd = parse_partial_date("1985-1988 · Jun–Aug")
    assert (pd.precision, pd.min, pd.max, pd.months) == ("range", 1985, 1988, [6, 7, 8])
    label = format_partial(pd.year, pd.month, pd.day, pd.precision, pd.min, pd.max, pd.months)
    assert label == "1985-1988 · Jun–Aug" and parse_partial_date(label) == pd
    assert parse_partial_date("1987 · dec, jan").months == [1, 12]
    one = with_months(parse_partial_date("1987"), [6])  # one month of one year is just that month
    assert (one.precision, one.month, one.months) == ("month", 6, None)
    assert with_months(parse_partial_date("1980s"), list(range(1, 13))).months is None  # every month says nothing
    for bad in ("1987-06 · Jul", "1987 · Smarch", "1987 · Aug–Jun"):
        with pytest.raises(DateParseError):
            parse_partial_date(bad)
    assert export_calendar_date(1986, None, None, "range", [6, 7, 8]) == (1986, 6, 1)
