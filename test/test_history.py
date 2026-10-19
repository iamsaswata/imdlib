"""
Tests for imdlib.history().

No network access: requests.post is replaced by a fake server (see
test_load.py) that sends files with the same value in every cell, so the
value of any place is known exactly.
"""
import sys

import numpy as np
import pandas as pd
import pytest

import imdlib as imd
import imdlib.history  # noqa: F401
from imdlib.regions import RegionNotFoundError
from imdlib.util import DataNotAvailableError

# The module (imdlib.history is the function)
hist = sys.modules['imdlib.history']

from test_load import FakeDate, FakeResponse, isolated, server  # noqa: F401 (fixtures)

ARCHIVE = {'https://imdpune.gov.in/cmpg/Griddata/rainfall.php': 'rain',
           'https://imdpune.gov.in/cmpg/Griddata/maxtemp.php': 'tmax',
           'https://imdpune.gov.in/cmpg/Griddata/mintemp.php': 'tmin'}
REALTIME = {'https://imdpune.gov.in/cmpg/Realtimedata/Rainfall/rain.php': 'rain',
            'https://imdpune.gov.in/cmpg/Realtimedata/max/max.php': 'tmax',
            'https://imdpune.gov.in/cmpg/Realtimedata/min/min.php': 'tmin'}
SHAPE = {('archive', 'rain'): (129, 135), ('archive', 'tmax'): (31, 31),
         ('realtime', 'rain'): (129, 135), ('realtime', 'tmax'): (61, 61)}


def temp(day):
    """Temperature of a day: differs by year, and by day within the year
    (29 February differs from 28 February). Exact in float32."""
    return 30 + ((day.year * 7) % 13) * 0.5 + (day.day % 4) * 0.25


def field(var, source, values):
    nlat, nlon = SHAPE[(source, var)]
    return np.repeat(np.asarray(values, '<f4'), nlat * nlon).tobytes()


def uniform_server(value=temp, published=2025, empty_days=()):
    """Archive years up to ``published``, and real-time days except
    ``empty_days`` ('DDMMYYYY'), with ``value(day)`` in every cell."""
    def handler(url, data):
        key = list(data.values())[0]
        if url in ARCHIVE:
            year = int(key)
            if year > published:
                return FakeResponse(b'')
            days = pd.date_range('{}-01-01'.format(year), '{}-12-31'.format(year))
            return FakeResponse(field(ARCHIVE[url], 'archive', [value(d) for d in days]))
        if key in empty_days:
            return FakeResponse(b'')
        day = pd.Timestamp(key[4:] + '-' + key[2:4] + '-' + key[:2])
        return FakeResponse(field(REALTIME[url], 'realtime', [value(day)]))
    return handler


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr(hist, 'date', FakeDate)     # 2026-10-06


def expected(values, value, year, normal=(1991, 2020), since=1991):
    """normal and (rank, of, years, last_higher, last_lower), written out
    plainly. ``values``: {year: value}."""
    values = {**values, year: value}
    norm = np.mean([v for y, v in values.items() if normal[0] <= y <= normal[1]])
    ranked = {y: v for y, v in values.items() if y >= since}
    rank = 1 + sum(v > value for v in ranked.values())
    years = '{}-{}'.format(min(ranked), max(ranked))
    earlier = [y for y in sorted(ranked, reverse=True) if y < year]
    higher = [y for y in earlier if ranked[y] >= value]
    lower = [y for y in earlier if ranked[y] <= value]
    return norm, (rank, len(ranked), years, higher[0] if higher else None,
                  lower[0] if lower else None)


def tenth(x):
    """Equal after rounding to 0.1."""
    return pytest.approx(x, abs=0.051)


def ranks(row):
    """(rank, of, years, last_higher, last_lower) of a row; None if empty."""
    return tuple(None if pd.isna(row[c]) else row[c]
                 for c in ('rank', 'of', 'years', 'last_higher', 'last_lower'))


###############################################################################
# Days, months, seasons and years
###############################################################################

def test_day(server):
    server(uniform_server())
    t = imd.history('tmax', '2024-07-14', district='Pune', progress=False)
    assert list(t.columns) == ['value', 'normal', 'departure', 'rank', 'of', 'years',
                               'last_higher', 'last_lower']
    assert list(t.index) == [pd.Timestamp('2024-07-14')] and t.index.name == 'date'
    assert t.columns.name == 'Pune (Maharashtra)'
    value = temp(pd.Timestamp('2024-07-14'))
    norm, want = expected(
        {y: temp(pd.Timestamp(y, 7, 14)) for y in range(1991, 2026)}, value, 2024)
    row = t.iloc[0]
    assert row['value'] == tenth(value)
    assert row['normal'] == tenth(norm)
    # Rounded to 0.1; the departure is that of the rounded values
    assert row['departure'] == pytest.approx(row['value'] - row['normal'], abs=1e-9)
    assert row['departure'] == pytest.approx(value - norm, abs=0.11)
    assert ranks(row) == want and want[1:3] == (35, '1991-2025')
    assert want[3] is not None and want[4] is not None


def test_month_and_season_and_years(server):
    server(uniform_server())

    def mean(year, months):
        days = pd.date_range('{}-01-01'.format(year), '{}-12-31'.format(year))
        return np.mean([temp(d) for d in days if d.month in months])

    t = imd.history('tmax', '2024-05', district='Pune', progress=False)
    assert list(t.index) == [pd.Period('2024-05', 'M')] and t.index.name == 'month'
    assert list(t.columns[:2]) == ['days', 'value'] and t['days'].iloc[0] == 31
    norm, want = expected({y: mean(y, (5,)) for y in range(1991, 2026)},
                          mean(2024, (5,)), 2024)
    assert t['value'].iloc[0] == tenth(mean(2024, (5,)))
    assert t['normal'].iloc[0] == tenth(norm)
    assert ranks(t.iloc[0]) == want

    t = imd.history('tmax', 2015, 2024, season='monsoon', district='Pune', progress=False)
    assert list(t.index) == ['{} monsoon'.format(y) for y in range(2015, 2025)]
    assert (t['days'] == 122).all()
    assert t.index.name == 'season'
    jjas = {y: mean(y, (6, 7, 8, 9)) for y in range(1991, 2026)}
    for year in (2015, 2020, 2024):
        norm, want = expected(jjas, jjas[year], year)
        row = t.loc['{} monsoon'.format(year)]
        assert row['value'] == tenth(jjas[year])
        assert ranks(row) == want

    # Season codes, any case
    assert t.equals(imd.history('tmax', 2015, 2024, season='jjas', district='Pune',
                                progress=False))

    t = imd.history('tmax', 2024, district='Pune', progress=False)
    assert list(t.index) == [pd.Period('2024', 'Y')] and t.index.name == 'year'
    assert t['value'].iloc[0] == tenth(mean(2024, range(1, 13)))


def test_by(server):
    server(uniform_server())
    t = imd.history('tmax', '2024-07', district='Pune', by='day', progress=False)
    assert len(t) == 31 and t.index[0] == pd.Timestamp('2024-07-01')
    t = imd.history('tmax', 2024, district='Pune', by='season', progress=False)
    assert list(t.index) == ['2024 winter', '2024 summer', '2024 monsoon', '2024 postmonsoon']
    t = imd.history('tmax', 2024, district='Pune', by='month', progress=False)
    assert len(t) == 12 and t['days'].sum() == 366
    # A part of a month: the number of days shows it
    t = imd.history('tmax', '2024-07-14', district='Pune', by='month', progress=False)
    assert list(t.index) == [pd.Period('2024-07', 'M')] and t['days'].iloc[0] == 1
    t = imd.history('tmax', 2023, 2024, season='summer', district='Pune', by='year',
                    progress=False)
    assert list(t.index) == [pd.Period('2023', 'Y'), pd.Period('2024', 'Y')]


def test_29_february(server):
    server(uniform_server())
    # As a day: compared with 28 February in years without 29 February
    t = imd.history('tmax', '2024-02-29', district='Pune', progress=False)
    past = {y: temp(pd.Timestamp(y, 2, 29 if pd.Timestamp(y, 1, 1).is_leap_year else 28))
            for y in range(1991, 2026)}
    norm, want = expected(past, temp(pd.Timestamp('2024-02-29')), 2024)
    assert t['normal'].iloc[0] == tenth(norm)
    assert ranks(t.iloc[0]) == want
    # In a month: each year's whole February
    t = imd.history('tmax', '2024-02', district='Pune', progress=False)
    feb = {y: np.mean([temp(d) for d in pd.date_range('{}-02-01'.format(y),
                                                      periods=29 if y % 4 == 0 else 28)])
           for y in range(1991, 2021)}
    assert t['normal'].iloc[0] == tenth(np.mean(list(feb.values())))


###############################################################################
# Rain
###############################################################################

def rain(day):
    """No rain in January; otherwise rain that differs by year."""
    return 0.0 if day.month == 1 else float((day.year * 3) % 11)


def test_rain(server):
    server(uniform_server(rain))
    t = imd.history('rain', '2024-07-14', district='Pune', normal=(2018, 2020),
                    progress=False)
    assert list(t.columns) == ['value', 'normal', 'departure', 'departure_pct', 'rank',
                               'of', 'years', 'last_higher', 'last_lower']
    value = rain(pd.Timestamp('2024-07-14'))
    norm = np.mean([rain(pd.Timestamp(y, 7, 14)) for y in (2018, 2019, 2020)])
    assert t['departure_pct'].iloc[0] == tenth(100 * (value - norm) / norm)
    assert t['of'].iloc[0] == 8                       # 2018-2025

    # A month is the total of its days
    t = imd.history('rain', '2024-07', district='Pune', normal=(2018, 2020), progress=False)
    assert t['value'].iloc[0] == tenth(31 * value)

    # No rain: no rank and no last years; a normal of 0: no percent
    t = imd.history('rain', '2024-01-10', district='Pune', normal=(2018, 2020),
                    progress=False)
    row = t.iloc[0]
    assert row['value'] == 0 and row['normal'] == 0
    assert pd.isna(row['departure_pct'])
    assert ranks(row) == (None, 8, '2018-2025', None, None)


###############################################################################
# Past years
###############################################################################

def test_since(server):
    srv = server(uniform_server())
    value = temp(pd.Timestamp('2024-07-14'))
    past = {y: temp(pd.Timestamp(y, 7, 14)) for y in range(1951, 2026)}
    t = imd.history('tmax', '2024-07-14', district='Pune', since='all', progress=False)
    norm, want = expected(past, value, 2024, since=1951)
    assert ranks(t.iloc[0]) == want and want[1:3] == (75, '1951-2025')
    assert len(srv.calls) == 75                       # 1951-2025, downloaded once

    t = imd.history('tmax', '2024-07-14', district='Pune', since=2010, progress=False)
    assert ranks(t.iloc[0]) == expected(past, value, 2024, since=2010)[1]
    assert t['of'].iloc[0] == 16 and t['years'].iloc[0] == '2010-2025'
    # The normal is still 1991-2020
    assert t['normal'].iloc[0] == tenth(expected(past, value, 2024)[0])
    assert len(srv.calls) == 75                       # all cached

    t = imd.history('tmax', '2024-07-14', district='Pune', normal=(1961, 1990),
                    progress=False)
    assert t['normal'].iloc[0] == tenth(
        np.mean([past[y] for y in range(1961, 1991)]))


def test_record_has_no_last_higher(server):
    def hot_2024(day):
        return 45.0 if day.year == 2024 else temp(day)
    server(uniform_server(hot_2024))
    t = imd.history('tmax', '2024-07-14', district='Pune', progress=False)
    row = t.iloc[0]
    assert row['rank'] == 1 and pd.isna(row['last_higher']) and row['last_lower'] == 2023


def test_equal_values_share_a_rank(server):
    server(uniform_server(lambda day: 30.0 + (day.year in (2010, 2024)) * 2))
    t = imd.history('tmax', '2024-07-14', district='Pune', progress=False)
    # An equal value counts as higher (and as lower)
    assert ranks(t.iloc[0]) == (1, 35, '1991-2025', 2010, 2023)
    t = imd.history('tmax', '2010-07-14', district='Pune', progress=False)
    assert ranks(t.iloc[0]) == (1, 35, '1991-2025', None, 2009)


def test_last_higher_and_lower(server):
    server(uniform_server(lambda day: {2024: 20.0, 2015: 19.0, 2020: 20.0}.get(day.year, 30.0)))
    t = imd.history('tmax', '2024-07-14', district='Pune', progress=False)
    # Rank shared with 2020; 2023 was higher, 2020 as low
    assert ranks(t.iloc[0]) == (33, 35, '1991-2025', 2023, 2020)


def test_download_note(server, capsys):
    srv = server(uniform_server())
    imd.history('tmax', '2024-07-14', district='Pune')
    out = capsys.readouterr().out
    assert "tmax 1991-2025 for comparison: 0 of 35 years cached, downloading 35 (49.1 MB)." \
        in out
    assert len(srv.calls) == 35
    imd.history('tmax', '2024-07-15', district='Pune')
    assert capsys.readouterr().out == ''
    assert len(srv.calls) == 35


def test_offline(server):
    srv = server(uniform_server())
    with pytest.raises(FileNotFoundError, match="tmax 1991-2025"):
        imd.history('tmax', '2024-07-14', district='Pune', offline=True)
    assert srv.calls == []


###############################################################################
# Recent days: real-time data
###############################################################################

def test_unpublished_last_year_uses_realtime(server, capsys):
    server(uniform_server(published=2024))
    t = imd.history('tmax', '2025-07-14', '2025-07-15', district='Pune')
    out = capsys.readouterr().out
    assert "tmax 2025-07-14 to 2025-07-15: provisional real-time data." in out
    past = {y: temp(pd.Timestamp(y, 7, 14)) for y in range(1991, 2025)}
    value = temp(pd.Timestamp('2025-07-14'))
    norm, want = expected(past, value, 2025)
    row = t.iloc[0]
    assert row['value'] == tenth(value)
    assert ranks(row) == want and want[1:3] == (35, '1991-2025')


def test_current_month_so_far(server, capsys):
    # Today is 2026-10-06; 2026-10-06 is not published yet, 2026-10-03 is missing
    server(uniform_server(empty_days=('06102026', '03102026')))
    with pytest.warns(UserWarning, match="2026-10-03"):
        t = imd.history('tmax', '2026-10', district='Pune')
    assert "tmax 2026-10-01 to 2026-10-05: provisional real-time data." in \
        capsys.readouterr().out
    # The month so far without the missing day, also in the past years
    assert t['days'].iloc[0] == 4
    days = [pd.Timestamp(2026, 10, d) for d in (1, 2, 4, 5)]
    assert t['value'].iloc[0] == tenth(np.mean([temp(d) for d in days]))
    assert t['normal'].iloc[0] == tenth(np.mean(
        [np.mean([temp(d.replace(year=y)) for d in days]) for y in range(1991, 2021)]))
    assert t['of'].iloc[0] == 36

    # As days: the missing day is a row without a value
    with pytest.warns(UserWarning):
        t = imd.history('tmax', '2026-10', district='Pune', by='day', progress=False)
    assert len(t) == 5 and pd.isna(t.loc['2026-10-03', 'value'])
    assert pd.isna(t.loc['2026-10-03', 'rank'])


def test_unpublished_day_given_as_day_raises(server):
    server(uniform_server(empty_days=('06102026',)))
    with pytest.raises(DataNotAvailableError, match="2026-10-06"):
        imd.history('tmax', '2026-10-06', district='Pune', progress=False)


###############################################################################
# Wrong input: checked before any download
###############################################################################

@pytest.mark.parametrize('kwargs, error, match', [
    (dict(var='rain_gpm'), ValueError, "var must be"),
    (dict(district=['Pune', 'Nashik']), ValueError, "one place at a time"),
    (dict(district='Puen'), RegionNotFoundError, "Pune"),
    (dict(start='2024-7'), ValueError, "start must be"),
    (dict(start='2024-07', end='2024-08-01'), ValueError, "both be years, months or days"),
    (dict(start='2024-08', end='2024-07'), ValueError, "before start"),
    (dict(season='monsoon'), ValueError, "only be used with years"),
    (dict(start=2024, season='rainy'), ValueError, "JJAS"),
    (dict(by='week'), ValueError, "by must be"),
    (dict(normal=(2020, 1991)), ValueError, "normal must be"),
    (dict(normal=1991), ValueError, "normal must be two years"),
    (dict(since=1900), ValueError, "starts in 1951"),
    (dict(since=2030), ValueError, "after the period"),
    (dict(since='some'), ValueError, "since must be"),
    (dict(start='2027-01'), DataNotAvailableError, "future"),
])
def test_wrong_input(server, kwargs, error, match):
    srv = server(uniform_server())
    args = dict(var='tmax', start='2024-07-14', district='Pune')
    args.update(kwargs)
    var, start = args.pop('var'), args.pop('start')
    with pytest.raises(error, match=match):
        imd.history(var, start, **args)
    assert srv.calls == []


def test_constants():
    assert {var: rule.normal for var, rule in hist.RULES.items()} == \
        {'rain': (1971, 2020), 'tmax': (1991, 2020), 'tmin': (1991, 2020)}
    assert hist.SEASON_CODES == {'JF': 'winter', 'MAM': 'summer', 'JJAS': 'monsoon',
                                 'OND': 'postmonsoon'}


###############################################################################
# All of India
###############################################################################

def test_all_india(server):
    srv = server(uniform_server())
    t = imd.history('tmax', '2024-07-14', progress=False)
    assert t.columns.name == 'India'
    norm, want = expected({y: temp(pd.Timestamp(y, 7, 14)) for y in range(1991, 2026)},
                          temp(pd.Timestamp('2024-07-14')), 2024)
    assert t['value'].iloc[0] == tenth(temp(pd.Timestamp('2024-07-14')))
    assert t['normal'].iloc[0] == tenth(norm)
    assert ranks(t.iloc[0]) == want
    assert len(srv.calls) == 35
    # Recent days from real-time data
    t = imd.history('tmax', '2026-10-01', '2026-10-02', progress=False)
    assert t.columns.name == 'India'
    assert t['value'].tolist() == [tenth(temp(pd.Timestamp('2026-10-01'))),
                                   tenth(temp(pd.Timestamp('2026-10-02')))]


def test_default_normal_of_rain(server):
    server(uniform_server(rain))
    t = imd.history('rain', '2024-07-14', district='Pune', progress=False)
    assert t['years'].iloc[0] == '1971-2025'
    assert t['normal'].iloc[0] == tenth(np.mean([rain(pd.Timestamp(y, 7, 14))
                                                 for y in range(1971, 2021)]))
