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
