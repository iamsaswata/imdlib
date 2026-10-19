"""
``imdlib.history()``: how a day, month, season or year at a place compares
with the same period in past years.
"""

import numbers
import re
import warnings
from collections import namedtuple
from datetime import date

import numpy as np
import pandas as pd

from imdlib import cache, loader, regions
from imdlib.util import DataNotAvailableError

# Per variable: how the days of a period combine ('sum' or 'mean'), whether
# the departure is also given in percent, whether a value of 0 (no rain) has
# no rank and no last higher or lower year, and the default normal period
Rule = namedtuple('Rule', 'combine percent zero_is_none normal')
RULES = {
    'rain': Rule('sum', True, True, (1971, 2020)),
    'tmax': Rule('mean', False, False, (1991, 2020)),
    'tmin': Rule('mean', False, False, (1991, 2020)),
}

# IMD seasons and their months
SEASONS = {
    'winter': (1, 2),
    'summer': (3, 4, 5),
    'monsoon': (6, 7, 8, 9),
    'postmonsoon': (10, 11, 12),
}
# Codes made of the first letters of the months: JF, MAM, JJAS, OND
SEASON_CODES = {''.join('JFMAMJJASOND'[m - 1] for m in months): name
                for name, months in SEASONS.items()}

UNITS = ('day', 'month', 'season', 'year')

_MONTH = re.compile(r'^\d{4}-\d{2}$')
_YEAR = re.compile(r'^\d{4}$')
_DAY = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def history(var, start, end=None, *, state=None, district=None, city=None, basin=None,
            subbasin=None, shapefile=None, season=None, by=None, normal=None, since=None,
            offline=False, cache_dir=None, proxies=None, progress=True, parallel=4):
    """
    Compare a day, month, season or year at a place with the same period in
    past years.

    Parameters
    ----------
    var : str
        'rain', 'tmax' or 'tmin'.

    start, end : int or str
        The period, as in :func:`imdlib.load`: a year (2024), a month
        ('2024-07') or a day ('2024-07-14'). ``end`` (written the same way)
        gives a range; if None, ``end = start``.

    state, district, city, basin, subbasin, shapefile :
        One place, as in :meth:`IMD.region`. ``state`` or ``district`` may
        narrow a district or city name. Without a place: all of India (the
        area-weighted mean of the grid, as :meth:`IMD.spatial_mean`).

    season : str, optional
        With years only: 'winter' (Jan-Feb), 'summer' (Mar-May), 'monsoon'
        (Jun-Sep) or 'postmonsoon' (Oct-Dec), or the codes 'JF', 'MAM',
        'JJAS' and 'OND'.

    by : str, optional
        One row per 'day', 'month', 'season' or 'year'. Default: as the
        period is written (a season if ``season`` is given).

    normal : (int, int), optional
        First and last year of the normal. Default: 1971-2020 for rain,
        1991-2020 for temperature (``RULES``).

    since : int or 'all', optional
        First year of the past years for ``rank``, ``last_higher`` and
        ``last_lower``; 'all' is
        the first year of the IMD archive (1901 for rain, 1951 for
        temperature). Default: the first year of the normal.

    offline, cache_dir, proxies, progress, parallel :
        As in :func:`imdlib.load`. ``progress`` also shows the notes on the
        download and on real-time data.

    Returns
    -------
    pandas.DataFrame
        One row per period (dates, months or years as pandas dates and
        periods; seasons as text such as '2024 monsoon') with the columns:

        - ``days``: number of days in a month, season or year row; fewer than
          in the whole period if only a part of it is compared (e.g. the
          current month so far). Not given for days.

        - ``value``: rain total, or mean temperature, of the period.
        - ``normal``: the average of the same period in the normal years.
        - ``departure``: ``value`` minus ``normal``.
        - ``departure_pct``: the departure in percent of normal (rain only;
          empty when the normal is 0).
        - ``rank``, ``of``, ``years``: position among the past years (1 =
          highest; equal values share a rank), the number of years, and
          the first and last year, e.g. '1991-2025'.
        - ``last_higher``, ``last_lower``: the last earlier year with a value
          as high or higher, and as low or lower; empty if there is none
          (a record).

        Values are rounded to 0.1 (mm, °C or %); ``rank``, ``last_higher``
        and ``last_lower`` use the values before rounding. For rain, a value
        of 0 has no ``rank``, ``last_higher`` and ``last_lower``.

    Notes
    -----
    The same period in a past year has the same days of the year. A month,
    season or year that is not complete (e.g. the current month) is compared
    with the same days in past years. 29 February is compared with 28
    February of the years without it, as a day; in a month or longer period
    it is only counted in leap years.

    The past years come from the IMD archive; the first use downloads them
    (one file per year). Days after the last published archive year come
    from the provisional real-time data. With a month, season or year that
    reaches the present, days not published yet are left out.

    Examples
    --------
    >>> import imdlib as imd
    >>> imd.history('rain', '2024-07-14', district='Pune')
    >>> imd.history('tmax', '2024-05', district='Pune')
    >>> imd.history('rain', 2024, season='monsoon', state='Kerala')
    >>> imd.history('rain', 2015, 2024, season='monsoon', district='Pune', since='all')
    """
    with regions._user_facing():
        if var not in RULES:
            raise ValueError("var must be 'rain', 'tmax' or 'tmin', got {!r}.".format(var))
        place = _place(state, district, city, basin, subbasin, shapefile)
        today = date.today()
        days, unit, months, open_end = _period(start, end, season, by, today)
        normal, since = _years(var, normal, since, days[0].year)
    root = cache.get_dir(cache_dir)
    options = dict(cache_dir=cache_dir, offline=offline, proxies=proxies,
                   progress=progress, parallel=parallel)

    first = min(since, normal[0])
    data, last = _past(var, first, today, root, normal, options)
    past = _past_series(var, data, first, last, place, options)
    values = _values(var, days, last, past, open_end, place, options)
    return _table(var, _rows(values, unit, months), unit, past, last, normal, since)


###############################################################################
# Arguments
###############################################################################

def _place(state, district, city, basin, subbasin, shapefile):
    """The keywords of the place for region(); checks them without data."""
    place = dict(state=state, district=district, city=city, basin=basin,
                 subbasin=subbasin, shapefile=shapefile)
    place = {k: v for k, v in place.items() if v is not None}
    if shapefile is not None:
        regions._check_shapefile_exists(shapefile)
        if len(place) > 1:
            raise TypeError(regions._ONE_TYPE)
        return place
    if not place:
        return place            # all of India
    columns = regions._select(state, district, city, basin, subbasin)
    if len(columns) > 1:
        raise ValueError("history() compares one place at a time, got {}."
                         .format(', '.join(c.label for c in columns)))
    return place


def _form(value, name):
    """'year', 'month' or 'day', and the first and last day of ``value``."""
    if isinstance(value, numbers.Integral) and not isinstance(value, bool):
        value = str(int(value))
    if isinstance(value, str):
        if _YEAR.match(value):
            first = pd.Timestamp(int(value), 1, 1)
            return 'year', first, pd.Timestamp(int(value), 12, 31)
        if _MONTH.match(value):
            try:
                first = pd.Timestamp(value + '-01')
            except ValueError:
                pass
            else:
                return 'month', first, first + pd.offsets.MonthEnd(0)
        if _DAY.match(value):
            try:
                day = pd.Timestamp(value)
            except ValueError:
                pass
            else:
                return 'day', day, day
    raise ValueError("{} must be a year (2024), a month ('2024-07') or a day "
                     "('2024-07-14'), got {!r}.".format(name, value))


def _period(start, end, season, by, today):
    """
    The days to compare, the row unit, the months of a row of a season or
    year, and whether the period may reach days not published yet.
    """
    form, first, _ = _form(start, 'start')
    end_form, _, last = _form(start if end is None else end, 'end')
    if end_form != form:
        raise ValueError("start and end must both be years, months or days, got {!r} "
                         "and {!r}.".format(start, end))
    if last < first:
        raise ValueError("end {!r} is before start {!r}.".format(end, start))
    if by is not None and by not in UNITS:
        raise ValueError("by must be 'day', 'month', 'season' or 'year', got {!r}."
                         .format(by))
    days = pd.date_range(first, last, freq='D')
    months = tuple(range(1, 13))
    if season is not None:
        if form != 'year':
            raise ValueError("season= can only be used with years, e.g. "
                             "history('rain', 2024, season='monsoon', ...).")
        months = SEASONS[_season_name(season)]
        days = days[days.month.isin(months)]
    unit = by or ('season' if season is not None else form)
    # A month, season or year can reach the present: keep the days until today
    open_end = form != 'day'
    if open_end:
        days = days[days <= pd.Timestamp(today)]
        if len(days) == 0:
            raise DataNotAvailableError("{} is in the future.".format(start))
    return days, unit, months, open_end


def _season_name(season):
    if isinstance(season, str):
        if season.lower() in SEASONS:
            return season.lower()
        if season.upper() in SEASON_CODES:
            return SEASON_CODES[season.upper()]
    raise ValueError("Unknown season {!r}. Use JF (winter), MAM (summer), JJAS (monsoon) "
                     "or OND (postmonsoon).".format(season))


def _years(var, normal, since, first_year):
    """Check ``normal`` and ``since``; returns them as (int, int) and int."""
    first = loader.ARCHIVE_START[var]
    normal = RULES[var].normal if normal is None else normal
    try:
        n0, n1 = (int(y) for y in normal)
    except (TypeError, ValueError):
        raise ValueError("normal must be two years, e.g. (1991, 2020), got {!r}."
                         .format(normal)) from None
    if not first <= n0 <= n1:
        raise ValueError("normal must be two years from {} on, the first not after the "
                         "second, got {!r}.".format(first, normal))
    if since is None:
        since = min(n0, first_year)
    elif since == 'all':
        since = first
    elif isinstance(since, numbers.Integral) and not isinstance(since, bool):
        since = int(since)
        if since < first:
            raise ValueError("The IMD {} archive starts in {}; since={}. Use since='all' "
                             "for all years.".format(var, first, since))
        if since > first_year:
            raise ValueError("since={} is after the period ({}).".format(since, first_year))
    else:
        raise ValueError("since must be a year or 'all', got {!r}.".format(since))
    return (n0, n1), since


###############################################################################
# Data
###############################################################################

def _past(var, first, today, root, normal, options):
    """
    Daily values of the place in the archive, from year ``first`` to the
    last published year, as a lazy IMD object; and that last year.
    """
    last = today.year - 1
    _announce(var, first, last, root, today, options)
    try:
        data = loader.load(var, first, last, **options)
    except DataNotAvailableError:
        # Last year is not published yet
        last -= 1
        data = loader.load(var, first, last, **options)
    if normal[1] > last:
        raise DataNotAvailableError(
            "The normal {}-{} reaches beyond the IMD {} archive, which ends in {}."
            .format(normal[0], normal[1], var, last))
    return data, last


def _announce(var, first, last, root, today, options):
    """One line on what the past years need to download."""
    if options['offline'] or not options['progress'] or first > last:
        return
    files = loader._files(var, 'archive', '{}-01-01'.format(first),
                          '{}-12-31'.format(last), root, today)
    missing = [f for f in files if not f.is_cached()]
    if missing:
        print("{} {}-{} for comparison: {} of {} years cached, downloading {} ({})."
              .format(var, first, last, len(files) - len(missing), len(files), len(missing),
                      cache._format_size(sum(f.expected for f in missing))), flush=True)


def _values(var, days, last, past, open_end, place, options):
    """Daily values of the place for ``days``: archive, then real-time."""
    recent = days[days.year > last]
    if len(recent) == 0:
        return past.reindex(days)
    # Each year separately: a season of two years is not one range of days
    parts = [past.reindex(days[days.year <= last])]
    for year in sorted(set(recent.year)):
        wanted = recent[recent.year == year]
        while True:
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter('always')
                    data = loader.load(var, wanted[0].strftime('%Y-%m-%d'),
                                       wanted[-1].strftime('%Y-%m-%d'), source='realtime',
                                       **options)
                break
            except DataNotAvailableError as e:
                # Days not published yet at the end of a month, season or year
                missing = getattr(e, 'days', None)
                if not open_end or not missing or missing[0] <= wanted[0]:
                    raise
                wanted = wanted[wanted < missing[0]]
        # Warnings (days missing at IMD) point to the call of history()
        for w in caught:
            warnings.warn(w.message, w.category, stacklevel=3)
        if options['progress']:
            print("{} {} to {}: provisional real-time data.".format(
                var, wanted[0].strftime('%Y-%m-%d'), wanted[-1].strftime('%Y-%m-%d')),
                flush=True)
        parts.append(_series(data, place).reindex(wanted))
        if len(wanted) < (recent.year == year).sum():
            break
    return pd.concat(parts)


def _series(data, place):
    """The values of the place as a Series named after it (no place: India)."""
    with regions._user_facing():
        frame = data.region(**place) if place else data.spatial_mean()
    return frame.iloc[:, 0].rename(frame.columns[0] if place else 'India')


def _past_series(var, data, first, last, place, options):
    """The values of the place in the archive years ``first`` to ``last``."""
    if place:
        return _series(data, place)
    # All of India: the whole grid, one year at a time to keep memory low.
    # The files are in the cache (downloaded with ``data``).
    options = dict(options, offline=True, progress=False)
    return pd.concat([_series(loader.load(var, year, **options), place)
                      for year in range(first, last + 1)])


###############################################################################
# Rows and the table
###############################################################################

# One row: its label, its days, and the months of a whole period
_Row = namedtuple('_Row', 'label days months')


def _rows(values, unit, months):
    """Split the daily values into rows of ``unit``. In a month or longer
    period, days without a value (missing at IMD) are left out, also from
    the past years."""
    if unit != 'day':
        values = values.dropna()
    days = values.index
    if unit == 'day':
        keys = days
    elif unit == 'month':
        keys = days.to_period('M')
    elif unit == 'year':
        keys = days.to_period('Y')
    else:
        season = {m: name for name, ms in SEASONS.items() for m in ms}
        keys = pd.Index(['{} {}'.format(d.year, season[d.month]) for d in days])
    rows = []
    for key in pd.unique(keys):
        sel = days[keys == key]
        if unit == 'month':
            row_months = (sel[0].month,)
        elif unit == 'season':
            row_months = SEASONS[key.split()[1]]
        else:
            row_months = months
        rows.append(_Row(key, values[sel], row_months))
    return rows


def _same_days(row, unit, year):
    """The days of ``row`` in another ``year``."""
    days = row.days.index
    if unit != 'day':
        whole = _whole(days[0].year, row.months)
        if len(days) == len(whole) and (days == whole).all():
            return _whole(year, row.months)
    leap = pd.Timestamp(year, 1, 1).is_leap_year
    out = []
    for d in days:
        if d.month == 2 and d.day == 29 and not leap:
            if unit != 'day':
                continue
            out.append(pd.Timestamp(year, 2, 28))
        else:
            out.append(pd.Timestamp(year, d.month, d.day))
    return pd.DatetimeIndex(out)


def _whole(year, months):
    days = pd.date_range(pd.Timestamp(year, 1, 1), pd.Timestamp(year, 12, 31), freq='D')
    return days[days.month.isin(months)]


def _combine(values, how):
    """Rain total or mean temperature; NaN if a day has no value."""
    if len(values) == 0 or np.isnan(values).any():
        return np.nan
    return float(values.sum() if how == 'sum' else values.mean())


def _table(var, rows, unit, past, last, normal, since):
    rule = RULES[var]
    first = min(since, normal[0])
    by_day = _by_day(past) if unit == 'day' else None
    records = []
    for row in rows:
        year = row.days.index[0].year
        value = _combine(row.days.values, rule.combine)
        # The same period in each year of the archive; this year: its value
        if by_day is not None:
            d = row.days.index[0]
            col = by_day.get((d.month, d.day))
            by_year = {} if col is None else col.to_dict()
        else:
            by_year = {y: _combine(past.reindex(_same_days(row, unit, y)).values,
                                   rule.combine)
                       for y in range(first, last + 1)}
        by_year[year] = value
        records.append(dict(days=len(row.days), value=value,
                            **_compare(value, by_year, year, normal, since, rule)))
    table = pd.DataFrame.from_records(records, index=_index([r.label for r in rows], unit))
    # Days of a month, season or year: shows a part of one (e.g. so far)
    columns = (['days'] if unit != 'day' else []) + ['value', 'normal', 'departure'] + (['departure_pct'] if rule.percent else []) \
        + ['rank', 'of', 'years', 'last_higher', 'last_lower']
    table = table[columns]
    table.columns.name = past.name      # the place
    # 0.1 mm, 0.1 °C and 0.1 %, as IMD gives them; the departure is that of
    # the rounded values. Ranks are of the values before rounding.
    table['value'] = table['value'].round(1)
    table['normal'] = table['normal'].round(1)
    table['departure'] = (table['value'] - table['normal']).round(1)
    if rule.percent:
        table['departure_pct'] = table['departure_pct'].round(1)
    for c in ('days', 'rank', 'of', 'last_higher', 'last_lower'):
        if c not in table:
            continue
        table[c] = table[c].astype('Int64')
    return table


def _by_day(past):
    """{(month, day): Series of the value by year}; 29 February is 28 February
    in years without it."""
    frame = pd.DataFrame({'year': past.index.year, 'month': past.index.month,
                          'day': past.index.day, 'value': past.values})
    table = frame.pivot_table(index='year', columns=['month', 'day'], values='value',
                              aggfunc='first', dropna=False)
    if (2, 29) in table.columns and (2, 28) in table.columns:
        table[(2, 29)] = table[(2, 29)].fillna(table[(2, 28)])
    return {key: table[key].dropna() for key in table.columns}


def _compare(value, values, year, normal, since, rule):
    """normal, departure, rank, of, years, last_higher and last_lower of
    ``value`` (of ``year``) among ``values`` ({year: value})."""
    norm = [v for y, v in values.items() if normal[0] <= y <= normal[1] and not np.isnan(v)]
    normal_value = float(np.mean(norm)) if norm else np.nan
    departure = value - normal_value
    out = dict(normal=normal_value, departure=departure)
    if rule.percent:
        out['departure_pct'] = (100 * departure / normal_value
                                if normal_value and not np.isnan(normal_value) else np.nan)
    ranked = {y: v for y, v in values.items() if y >= since and not np.isnan(v)}
    out['of'] = len(ranked)
    out['years'] = '{}-{}'.format(min(ranked), max(ranked)) if ranked else None
    out['rank'] = out['last_higher'] = out['last_lower'] = None
    if np.isnan(value) or (rule.zero_is_none and value == 0):
        return out
    out['rank'] = 1 + sum(v > value for v in ranked.values())
    earlier = [y for y in sorted(ranked, reverse=True) if y < year]
    out['last_higher'] = next((y for y in earlier if ranked[y] >= value), None)
    out['last_lower'] = next((y for y in earlier if ranked[y] <= value), None)
    return out


def _index(labels, unit):
    if unit == 'day':
        return pd.DatetimeIndex(labels, name='date')
    if unit in ('month', 'year'):
        return pd.PeriodIndex(labels, name=unit)
    return pd.Index(labels, name='season')
