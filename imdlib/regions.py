"""
Named regions of India for ``IMD.region()``: states, districts, cities,
CWC basins and sub-basins, and the functions ``imdlib.regions.search``,
``imdlib.regions.list`` and ``imdlib.regions.info``.

The region tables, the name index and the grid weights are built by
``scripts/build_regions.py`` and shipped in ``imdlib/data/regions``. They are
read on first use, not when imdlib is imported. The grid definitions and the
weighting (``_equal_area``, ``_cell_fractions``) are shared with the build
script, so that shipped weights and ``region(shapefile=...)`` agree.

Weights: a cell's weight is the fraction ``f`` of the cell covered by the
region times ``cos(lat)`` of the cell centre. ``f`` is computed in the
spherical Lambert cylindrical equal-area projection (x = lon, y = sin(lat)),
where grid cells are rectangles and areas are true areas.
"""

import bisect
import builtins
import difflib
import json
import os
import re
import unicodedata
from collections import namedtuple

import numpy as np
import pandas as pd

# list() is left out, so that 'from imdlib.regions import *' keeps the builtin list
__all__ = ['search', 'info', 'RegionError', 'RegionNotFoundError', 'AmbiguousRegionError']

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'regions')

# Region types of the shipped tables, in the order of their type codes
_TYPES = ('state', 'district', 'basin', 'subbasin')
_STATE, _DISTRICT, _BASIN, _SUBBASIN = range(4)
# Cells covering less of a region than this are numerical slivers
_MIN_FRACTION = 1e-4


class _Grid(namedtuple('_Grid', 'lon0 lat0 step nlon nlat')):
    """A regular IMD grid: centre of the first cell, spacing (degrees), size."""
    __slots__ = ()

    @property
    def lon(self):
        return self.lon0 + self.step * np.arange(self.nlon)

    @property
    def lat(self):
        return self.lat0 + self.step * np.arange(self.nlat)

    def edges(self):
        """Cell edges as (longitudes, sin(latitudes))."""
        lon = self.lon0 - self.step / 2 + self.step * np.arange(self.nlon + 1)
        lat = self.lat0 - self.step / 2 + self.step * np.arange(self.nlat + 1)
        return lon, np.sin(np.deg2rad(lat))


# Grids of the shipped weights. Cell index in the shipped data: ilon * nlat + ilat
_GRIDS = {
    'r025': _Grid(66.5, 6.5, 0.25, 135, 129),    # rain, archive and real-time
    't100': _Grid(67.5, 7.5, 1.0, 31, 31),       # temperature, archive
    't050': _Grid(67.5, 7.5, 0.5, 61, 61),       # temperature, real-time
}
# GPM rain is cell-aligned with r025 and uses its weights: GPM index = r025 index + offset
_GPM = _Grid(50.0, -30.0, 0.25, 241, 281)
_GPM_OFFSET = (66, 146)
# Cell index of a city outside a grid
_NO_CELL = 65535
# Missing values of IMD files (GPM rain has none)
_RAIN_MISSING = -999.0
_TEMP_MISSING = 99.9

_ONE_TYPE = "Give exactly one of state=, district=, city=, basin=, subbasin=, shapefile=."
_NOT_IMD_GRID = ("region() needs data on an IMD grid (0.25°, 0.5°, 1.0°, GPM 0.25°). "
                 "For other grids use region(shapefile=...).")


###############################################################################
# Errors
###############################################################################

class RegionError(ValueError):
    """A region could not be used (base class of the region errors)."""


class RegionNotFoundError(RegionError):
    """No region of the requested type has this name."""


class AmbiguousRegionError(RegionError):
    """The name fits more than one region; add ``state=`` or ``district=``."""


def _user_error(exc):
    """
    ``exc`` without its traceback if it reports a wrong input, else None.

    Wrong inputs are the region errors and the TypeError, ValueError and
    ImportError raised by the checks in this module. The public functions
    raise them again without the internal frames, so that the traceback ends
    at the user's call. Other errors keep their full traceback.
    """
    if not isinstance(exc, (TypeError, ValueError, ImportError)):
        return None
    tb = exc.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    if isinstance(exc, RegionError) or (tb is not None and
                                        tb.tb_frame.f_globals.get('__name__') == __name__):
        return exc.with_traceback(None)
    return None


###############################################################################
# Names
###############################################################################

_PUNCTUATION = re.compile(r"[.,\-'()]")
_SPACES = re.compile(r"\s+")


def _normalise(text):
    """
    Key used to match names: accents removed, lower case, ``&`` as ``and``,
    the characters ``. , - ' ( )`` as spaces, and whitespace collapsed.
    """
    text = unicodedata.normalize('NFKD', str(text))
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace('&', ' and ')
    return _SPACES.sub(' ', _PUNCTUATION.sub(' ', text)).strip()


def _join(items):
    """'A', 'A and B', 'A, B and C'."""
    items = [str(i) for i in items]
    return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' and ' + items[-1]


def _unique(items):
    """Items without repeats, in their first order."""
    out = []
    for i in items:
        if i not in out:
            out.append(i)
    return out


def _list(iterable):
    """The builtin ``list`` (this module defines its own ``list()``)."""
    return builtins.list(iterable)


###############################################################################
# Geometry (also used by scripts/build_regions.py)
###############################################################################

def _equal_area(geom):
    """Shapely geometry in lon/lat -> x = lon, y = sin(lat)."""
    import shapely
    return shapely.transform(geom, lambda c: np.column_stack(
        [c[:, 0], np.sin(np.deg2rad(c[:, 1]))]))


def _cell_fractions(geom, lon_edges, y_edges):
    """
    Fraction of each grid cell covered by ``geom`` (from :func:`_equal_area`).

    The cell range is bisected recursively and the geometry clipped to each
    half, so each vertex is processed about log2(number of cells) times.

    Parameters
    ----------
    geom : shapely geometry
        Polygons in x = lon, y = sin(lat).
    lon_edges, y_edges : 1D arrays
        Cell edges (increasing), as returned by :meth:`_Grid.edges`.

    Returns
    -------
    (ilon, ilat, fraction) : arrays
        Cells that overlap ``geom`` and the fraction (0-1] of each covered.
    """
    import shapely
    nlon, nlat = len(lon_edges) - 1, len(y_edges) - 1
    minx, miny, maxx, maxy = geom.bounds
    i0 = max(0, int(np.searchsorted(lon_edges, minx, 'right')) - 1)
    i1 = min(nlon, int(np.searchsorted(lon_edges, maxx, 'left')))
    j0 = max(0, int(np.searchsorted(y_edges, miny, 'right')) - 1)
    j1 = min(nlat, int(np.searchsorted(y_edges, maxy, 'left')))
    ilon, ilat, frac = [], [], []

    def part(g, i0, i1, j0, j1):
        """The geometry clipped to a block of cells, and the block."""
        return (shapely.clip_by_rect(g, lon_edges[i0], y_edges[j0], lon_edges[i1], y_edges[j1]),
                i0, i1, j0, j1)
    todo = [part(geom, i0, i1, j0, j1)] if i0 < i1 and j0 < j1 else []
    while todo:
        g, i0, i1, j0, j1 = todo.pop()
        area = g.area
        if area <= 0:
            continue
        block = (lon_edges[i1] - lon_edges[i0]) * (y_edges[j1] - y_edges[j0])
        if area >= block * (1 - 1e-12):                 # all cells fully covered
            ii, jj = np.meshgrid(np.arange(i0, i1), np.arange(j0, j1), indexing='ij')
            ilon.extend(ii.ravel())
            ilat.extend(jj.ravel())
            frac.extend([1.0] * ii.size)
        elif i1 - i0 == 1 and j1 - j0 == 1:             # one cell
            ilon.append(i0)
            ilat.append(j0)
            frac.append(min(area / block, 1.0))
        elif i1 - i0 >= j1 - j0:                        # split the longer side
            m = (i0 + i1) // 2
            todo += [part(g, i0, m, j0, j1), part(g, m, i1, j0, j1)]
        else:
            m = (j0 + j1) // 2
            todo += [part(g, i0, i1, j0, m), part(g, i0, i1, m, j1)]
    return np.array(ilon, dtype=int), np.array(ilat, dtype=int), np.array(frac, dtype=float)


def _edges_of(centres):
    """Cell edges of increasing cell centres (midpoints; outer cells symmetric)."""
    c = np.asarray(centres, dtype=float)
    if c.ndim != 1 or len(c) < 2 or not np.all(np.diff(c) > 0):
        raise ValueError("region(shapefile=...) needs increasing lon_array and lat_array "
                         "with at least two values each.")
    mid = (c[1:] + c[:-1]) / 2
    return np.concatenate([[2 * c[0] - mid[0]], mid, [2 * c[-1] - mid[-1]]])




###############################################################################
# Shipped data (read on first use)
###############################################################################

class _Strings:
    """
    Strings stored as one UTF-8 blob, separated by newlines, decoded when
    used (much less memory than a list of str).
    """

    def __init__(self, blob):
        blob = np.asarray(blob, dtype=np.uint8)
        self.blob = blob.tobytes()
        if blob.size:
            # start of each string, and one past the end of the last (for the end of each)
            self.start = np.concatenate([[0], np.flatnonzero(blob == 10) + 1,
                                         [blob.size + 1]]).astype(np.int32)
        else:
            self.start = np.zeros(1, dtype=np.int32)

    def __len__(self):
        return len(self.start) - 1

    def __getitem__(self, i):
        if isinstance(i, slice):
            return [self[k] for k in range(*i.indices(len(self)))]
        if i < 0:
            i += len(self)
        if not 0 <= i < len(self):
            raise IndexError(i)
        return self.blob[int(self.start[i]):int(self.start[i + 1]) - 1].decode('utf-8')

    def __iter__(self):
        return iter(self.range(0, len(self)))

    def range(self, i0, i1):
        """Strings i0 to i1 - 1 (decoded together)."""
        if i1 <= i0:
            return []
        return self.blob[int(self.start[i0]):int(self.start[i1]) - 1].decode('utf-8').split('\n')

    def find(self, text):
        """Indices of the strings that contain ``text`` (in order, with repeats)."""
        for m in re.finditer(re.escape(text.encode('utf-8')), self.blob):
            yield int(np.searchsorted(self.start, m.start(), 'right')) - 1


class _Keys:
    """
    Normalised city names: the name in lower case, or the key stored for the
    few names where that is not the key.
    """

    def __init__(self, names, other_idx, other_keys):
        self.names, self.other_idx, self.other = names, other_idx, other_keys

    def __len__(self):
        return len(self.names)

    def _other(self, i):
        k = int(np.searchsorted(self.other_idx, i))
        return k if k < len(self.other_idx) and self.other_idx[k] == i else None

    def __getitem__(self, i):
        if i < 0:
            i += len(self)
        k = self._other(i)
        return self.names[i].lower() if k is None else self.other[k]

    def range(self, i0, i1):
        """Keys i0 to i1 - 1."""
        out = [n.lower() for n in self.names.range(i0, i1)]
        k0, k1 = np.searchsorted(self.other_idx, [i0, i1])
        for k in range(k0, k1):
            out[int(self.other_idx[k]) - i0] = self.other[k]
        return out

    def find(self, text):
        """Indices of the keys that contain ``text``."""
        lower = _Strings.__new__(_Strings)
        lower.blob, lower.start = self.names.blob.lower(), self.names.start
        for i in lower.find(text):
            if self._other(i) is None:
                yield i
        for k in self.other.find(text):
            yield int(self.other_idx[k])


class _Regions:
    """States, districts, basins and sub-basins: names, name index and weights."""

    def __init__(self, path):
        with np.load(path, allow_pickle=False) as z:
            a = {k: z[k] for k in z.files}
        self.type = a['reg_type'].astype(int)
        self.name = [str(s) for s in a['reg_name']]
        self.parent = a['reg_parent'].astype(int)     # state of a district, basin of a sub-basin
        self.code = [str(s) for s in a['reg_code']]
        # Per type: normalised name or alias -> [(region, alias as shown or '')]
        self.index = [{} for _ in _TYPES]
        for t, key, r, alias in zip(a['key_type'], a['key_text'], a['key_region'], a['key_alias']):
            self.index[t].setdefault(str(key), []).append((int(r), str(alias)))
        # Names of districts before a split: key -> [(name as shown, state, [districts])]
        self.split = {}
        ptr = a['split_ptr']
        for n, (key, text, st) in enumerate(zip(a['split_key'], a['split_text'],
                                                a['split_state'])):
            self.split.setdefault(str(key), []).append(
                (str(text), int(st), [int(r) for r in a['split_region'][ptr[n]:ptr[n + 1]]]))
        # LGD districts without an SoI shape: key -> [(name, state, parent district)]
        self.newer = {}
        for key, name, st, par in zip(a['orphan_key'], a['orphan_name'], a['orphan_state'],
                                      a['orphan_parent']):
            self.newer.setdefault(str(key), []).append((str(name), int(st), int(par)))
        # Places taken for districts that are not: key -> [(name, state, part of)]
        self.not_district = {}
        for key, name, st, part in zip(a['notdist_key'], a['notdist_name'], a['notdist_state'],
                                       a['notdist_part']):
            self.not_district.setdefault(str(key), []).append((str(name), int(st), str(part)))
        # Weights per grid (CSR): cells and fractions of region k are [ptr[k]:ptr[k + 1]]
        self.weights = {g: (a[g + '_ptr'], a[g + '_cell'], a[g + '_frac']) for g in _GRIDS}
        self.children = {}                             # parent -> children, by name
        for k in sorted(range(len(self.name)), key=lambda k: (self.name[k].lower(), k)):
            if self.parent[k] >= 0:
                self.children.setdefault(int(self.parent[k]), []).append(k)

    def label(self, k):
        """Column name: 'Name (State)' for districts, the name for others."""
        if self.type[k] == _DISTRICT:
            return '{} ({})'.format(self.name[k], self.name[self.parent[k]])
        return self.name[k]

    def of_type(self, t):
        return [k for k in range(len(self.name)) if self.type[k] == t]

    def cells(self, grid, k):
        """Cell indices (ilon * nlat + ilat) and fractions of region k on a grid."""
        ptr, cell, frac = self.weights[grid]
        return cell[ptr[k]:ptr[k + 1]].astype(np.int64), frac[ptr[k]:ptr[k + 1]].astype(float)


class _CityCells(dict):
    """
    Cell of each city on a grid ('r025', 't100', 't050', 'gpm'), read from
    cities.npz when a grid is first used. _NO_CELL: outside the grid. 'gpm'
    is the r025 cell that contains the place; the r025 cell differs from it
    for a few places.
    """

    def __init__(self, path):
        super().__init__()
        self.path = path

    def __missing__(self, grid):
        with np.load(self.path, allow_pickle=False) as z:
            if grid == 'r025':
                cells = z['cell_gpm'].copy()
                cells[z['r025_idx']] = z['r025_cell']
            else:
                cells = z['cell_' + grid]
        self[grid] = cells
        return cells


# Sorts after any character of a name: [key, key + _AFTER) holds the names starting with key
_AFTER = '\uffff'
# Rank of a match: the name of the place (or another name of it) or an alternate name
_NAME, _ALTERNATE = 1, 0


class _Cities:
    """Populated places (sorted by normalised name) and their other names."""

    def __init__(self, path):
        with np.load(path, allow_pickle=False) as z:
            a = {k: z[k] for k in z.files if not k.startswith(('cell_', 'r025_'))}
        self.name = _Strings(a['name'])
        self.key = _Keys(self.name, a['xkey_idx'], _Strings(a['xkey']))     # sorted
        self.state = a['state']                        # region id of the state
        self.district = a['district']                  # region id of the district
        self.hq = a['hq']                              # 1 district HQ, 2 state capital
        self.town = a['town']                          # 1 town (used to rank places)
        self.cells = _CityCells(path)
        self.alt_key = _Strings(a['alt_key'])          # sorted
        self.alt_text = _Strings(a['alt_text'])
        self.alt_city = a['alt_city']
        self.alt_rank = a['alt_rank']

    @staticmethod
    def _span(keys, low, high):
        return range(bisect.bisect_left(keys, low), bisect.bisect_left(keys, high))

    def _alias(self, k):
        return int(self.alt_city[k]), self.alt_text[k], int(self.alt_rank[k])

    def _between(self, low, high):
        """[(city, alias or '', rank)] with a name or alias in [low, high)."""
        out = [(c, '', _NAME) for c in self._span(self.key, low, high)]
        return out + [self._alias(k) for k in self._span(self.alt_key, low, high)]

    def lookup(self, key):
        """[(city, alias as shown or '', rank)] whose name or an alias is ``key``."""
        found = {}
        for c, alias, rank in self._between(key, key + '\0'):
            if c not in found or rank > found[c][1]:
                found[c] = (alias, rank)
        return [(c, alias, rank) for c, (alias, rank) in found.items()]

    def starting(self, key):
        """Like :meth:`lookup`, for names and aliases that start with ``key``."""
        return self._between(key, key + _AFTER)

    def containing(self, key, limit):
        """Like :meth:`lookup`, for names and aliases that contain ``key`` (at most ``limit``)."""
        out = []
        for i in self.key.find(key):
            out.append((i, '', _NAME))
            if len(out) >= limit:
                return out
        for k in self.alt_key.find(key):
            out.append(self._alias(k))
            if len(out) >= limit:
                break
        return out

    def close(self, key, n=3):
        """
        Names and aliases close to ``key``, among those with its first two
        letters; names of district HQs and state capitals first.
        """
        start = key[:2]
        span = self._span(self.key, start, start + _AFTER)
        names = set(self.key.range(span.start, span.stop))
        span = self._span(self.alt_key, start, start + _AFTER)
        names.update(self.alt_key.range(span.start, span.stop))
        close = difflib.get_close_matches(key, sorted(names), n=max(n, 10), cutoff=0.75)
        close.sort(key=lambda k: not any(self.hq[c] for c, _, _ in self.lookup(k)))
        return close[:n]


_cache = {}


def _regions():
    if 'regions' not in _cache:
        _cache['regions'] = _Regions(os.path.join(_DATA_DIR, 'regions.npz'))
    return _cache['regions']


def _cities():
    if 'cities' not in _cache:
        _cache['cities'] = _Cities(os.path.join(_DATA_DIR, 'cities.npz'))
    return _cache['cities']


###############################################################################
# Resolving names
###############################################################################

# A column of the result: its label, 'region' or 'city', and the region or city id
_Column = namedtuple('_Column', 'label kind id')


def _as_names(value, keyword):
    if isinstance(value, str):
        return [value]
    if isinstance(value, (builtins.list, tuple)) and value and \
            all(isinstance(v, str) for v in value):
        return _list(value)
    raise TypeError("{}= must be a name or a list of names, not {!r}.".format(keyword, value))


def _resolve_region(t, name, states=None):
    """
    Id of the state, district, basin or sub-basin (type ``t``) called ``name``.
    For districts, ``states`` (a set of state ids) narrows the match. An
    official name is used before an old name or another spelling.
    """
    R = _regions()
    key = _normalise(name)
    hits = everywhere = R.index[t].get(key, [])
    splits = R.split.get(key, []) if t == _DISTRICT else []
    if t == _DISTRICT and states is not None:
        hits = [h for h in hits if R.parent[h[0]] in states]
        splits = [s for s in splits if s[1] in states]
    official = sorted({r for r, alias in hits if not alias})
    if official:
        if len(official) == 1:
            return official[0]
        raise _region_ambiguous(t, name, official)
    ids = sorted({r for r, _ in hits})
    if splits:
        if not ids and len(splits) == 1:
            text, state, districts = splits[0]
            raise AmbiguousRegionError(
                "{!r} matches several districts: {} ({}). Use one of these names.".format(
                    name, ', '.join(R.name[r] for r in districts), R.name[state]))
        ids = sorted(set(ids).union(*(s[2] for s in splits)))
    if len(ids) == 1:
        return ids[0]
    if ids:
        raise _region_ambiguous(t, name, ids)
    if t == _DISTRICT and states is not None and everywhere:
        raise RegionNotFoundError("No district named {!r} in {}. Found: {}.".format(
            name, _join(sorted(R.name[s] for s in states)),
            _join(sorted({R.label(r) for r, _ in everywhere}))))
    raise _region_not_found(t, name, key, states)


def _region_not_found(t, name, key, states):
    R = _regions()
    in_states = t == _DISTRICT and states is not None
    if t == _DISTRICT:
        for place, state, part in R.not_district.get(key, []):
            if not in_states or state in states:
                return RegionNotFoundError(
                    "{} is not a district in the official boundaries; it is part of {}. For "
                    "{} itself use city={!r}.".format(place, part, place, place))
        for new, state, parent in R.newer.get(key, []):
            if not in_states or state in states:
                return RegionNotFoundError(
                    "{} ({}) is newer than the Survey of India boundaries used here. It lies "
                    "within {}: use district={!r}.".format(new, R.name[state], R.name[parent],
                                                          R.name[parent]))

    def allowed(r):
        return not in_states or R.parent[r] in states
    keys = [k for k, hits in R.index[t].items() if any(allowed(r) for r, _ in hits)]
    labels = []
    for k in difflib.get_close_matches(key, keys, n=3, cutoff=0.75):
        labels += [R.label(r) for r, _ in R.index[t][k] if allowed(r) and R.label(r) not in labels]
    where = " in {}".format(_join(sorted(R.name[s] for s in states))) if in_states else ''
    message = "No {} named {!r}{}.".format(_TYPES[t], name, where)
    if labels:
        message += " Did you mean: {}?".format(', '.join(labels))
    return RegionNotFoundError(message)


def _region_ambiguous(t, name, ids):
    R = _regions()
    states = [R.name[R.parent[r]] for r in ids]
    if t == _DISTRICT and len(set(states)) == len(ids):
        if len({R.name[r] for r in ids}) == 1:
            message = "{!r} is a district in {}.".format(name, _join(states))
        else:
            message = "{!r} matches the districts {}.".format(
                name, _join([R.label(r) for r in ids]))
        return AmbiguousRegionError(message + " Add state=, e.g. region(district={!r}, "
                                              "state={!r}).".format(name, states[-1]))
    return AmbiguousRegionError("{!r} matches the {}s {}. Use one of these names.".format(
        name, _TYPES[t], _join([R.label(r) for r in ids])))


def _state_ids(state):
    """Ids of the states named in ``state`` (None if not given)."""
    if state is None:
        return None
    return {_resolve_region(_STATE, n) for n in _as_names(state, 'state')}


def _city_label(c, with_district=False):
    R, C = _regions(), _cities()
    if with_district:
        return '{} ({}, {})'.format(C.name[c], R.name[C.district[c]], R.name[C.state[c]])
    return '{} ({})'.format(C.name[c], R.name[C.state[c]])


# Order of preference of the places found by a city name (the lowest level wins)
_HQ_NAME = 1            # a district HQ or state capital with this name
_TOWN_NAME = 2          # a town with this name
_HQ_OLD_NAME = 3        # a district HQ or state capital with it as an old or other name
_TOWN_OLD_NAME = 4      # a town with it as an old or other name
_OTHER_PLACE = 5        # any other place (e.g. a village), by its name or another name
_HQ_LEVELS = (_HQ_NAME, _HQ_OLD_NAME)


def _city_level(hq, town, rank):
    """
    Preference of a place found by a name (lower first). A town has 15,000
    people or more or is an administrative centre (marked at build time).
    """
    if hq:
        return _HQ_NAME if rank == _NAME else _HQ_OLD_NAME
    if town:
        return _TOWN_NAME if rank == _NAME else _TOWN_OLD_NAME
    return _OTHER_PLACE


def _resolve_city(name, states=None, districts=None):
    """
    Id of the city called ``name``, narrowed by sets of state and district
    ids. If several places fit, the most preferred is used (see
    :func:`_city_level`); if more than one is left, AmbiguousRegionError.
    """
    R, C = _regions(), _cities()
    key = _normalise(name)
    hits = C.lookup(key)
    if not hits:
        raise _city_not_found(name, key)
    cands = [(c, rank) for c, _, rank in hits
             if (states is None or C.state[c] in states)
             and (districts is None or C.district[c] in districts)]
    if not cands:
        where = sorted(R.label(d) for d in districts) if districts is not None else \
            sorted(R.name[s] for s in states)
        raise RegionNotFoundError("No city named {!r} in {}. See imd.regions.search({!r}).".format(
            name, _join(where), name))
    levels = [_city_level(C.hq[c], C.town[c], rank) for c, rank in cands]
    best = min(levels)
    tied = [c for (c, _), level in zip(cands, levels) if level == best]
    capitals = best in _HQ_LEVELS
    if len(tied) == 1:
        return tied[0]
    tied.sort(key=lambda c: (-int(C.hq[c]), R.name[C.state[c]], R.name[C.district[c]], c))
    hint = "see imd.regions.search({!r}).".format(name)
    if len({int(C.district[c]) for c in tied}) == 1:
        # Places in one district: nothing more can tell them apart
        d = int(C.district[tied[0]])
        raise AmbiguousRegionError("{:,} places named {!r} in {} cannot be told apart. Use "
                                   "region(district={!r}) for the district, or {}".format(
                                       len(tied), name, R.label(d), R.name[d], hint))
    if states is None and len({int(C.state[c]) for c in tied}) > 1:
        hint = "Add state=, or " + hint
    elif districts is None:
        hint = "Add district=, or " + hint
    else:
        hint = hint[0].upper() + hint[1:]
    if capitals:
        labels = [_city_label(c) for c in tied]
        labels = [_city_label(c, labels.count(l) > 1) for c, l in zip(tied, labels)]
        raise AmbiguousRegionError("{!r} fits {} district HQs or state capitals: {}. {}".format(
            name, len(tied), _join(labels), hint))
    # Places in the same district have the same label: list each label once
    counts = {}
    for c in tied:
        label = _city_label(c, with_district=True)
        counts[label] = counts.get(label, 0) + 1
    shown = ['{}{}'.format(label, '' if n == 1 else ' ({} places)'.format(n))
             for label, n in _list(counts.items())[:10]]
    first = 'first {}: '.format(len(shown)) if len(counts) > len(shown) else ''
    raise AmbiguousRegionError("{:,} places named {!r} ({}{}). {}".format(
        len(tied), name, first, ', '.join(shown), hint))


def _city_not_found(name, key):
    C = _cities()
    labels = []
    for k in C.close(key):
        near = sorted((c for c, _, _ in C.lookup(k)), key=lambda c: -int(C.hq[c]))
        c = near[0]
        label = _city_label(c) if len(near) == 1 or C.hq[c] else C.name[c]
        if label not in labels:
            labels.append(label)
    hint = " Did you mean: {}?".format(', '.join(labels)) if labels else ''
    return RegionNotFoundError("No city named {!r}.{} See imd.regions.search({!r}).".format(
        name, hint, name))


def _target(state, district, city, basin, subbasin):
    """(type, value) of a region() call: the most specific keyword given."""
    given = [v is not None for v in (state, district, city, basin, subbasin)]
    if (basin is not None or subbasin is not None) and sum(given) > 1:
        raise TypeError(_ONE_TYPE)
    for kind, value in (('city', city), ('district', district), ('state', state),
                        ('basin', basin), ('subbasin', subbasin)):
        if value is not None:
            return kind, value
    raise TypeError(_ONE_TYPE)


def _select(state=None, district=None, city=None, basin=None, subbasin=None, by=None):
    """The columns (list of _Column) for the keywords of region()."""
    kind, value = _target(state, district, city, basin, subbasin)
    names = _as_names(value, kind)
    if by is not None and {'state': 'district', 'basin': 'subbasin'}.get(kind) != by:
        raise ValueError(
            "by={!r} cannot be used with {}=. Use by='district' with state=, by='subbasin' "
            "with basin=, or by='<field>' with shapefile=.".format(by, kind))
    if kind == 'city':
        states = _state_ids(state)
        districts = None if district is None else \
            {_resolve_region(_DISTRICT, n, states) for n in _as_names(district, 'district')}
        ids = _unique(_resolve_city(n, states, districts) for n in names)
        labels = [_city_label(c) for c in ids]
        # Two places with the same 'Name (State)': add their districts
        return [_Column(_city_label(c, with_district=labels.count(label) > 1), 'city', c)
                for c, label in zip(ids, labels)]
    R = _regions()
    t = _TYPES.index(kind)
    states = _state_ids(state) if t == _DISTRICT else None
    ids = []
    for n in names:
        r = _resolve_region(t, n, states)
        ids += R.children.get(r, []) if by is not None else [r]
    return [_Column(R.label(r), 'region', r) for r in _unique(ids)]


###############################################################################
# Cells and weights on the object's grid
###############################################################################

# Cells of a column: indices into the object's lon_array and lat_array, and weights
_Cells = namedtuple('_Cells', 'lon lat weight')


def _offset(values, start, step, n):
    """Offset of ``values`` as a contiguous window of a regular axis, or None."""
    values = np.asarray(values, dtype=float)
    if values.ndim != 1 or not 0 < len(values) <= n:
        return None
    k = (values[0] - start) / step
    o = int(round(k))
    if abs(k - o) > 1e-6 or o < 0 or o + len(values) > n:
        return None
    if not np.allclose(values, start + step * np.arange(o, o + len(values)), rtol=0, atol=1e-6):
        return None
    return o


def _identify_grid(obj):
    """
    (grid key, lon offset, lat offset) of the object: an IMD grid or a window
    of it (e.g. after clip()). The key is one of _GRIDS or 'gpm'.
    """
    grids = dict(_GRIDS, gpm=_GPM)
    order = ['gpm'] + _list(_GRIDS) if obj.cat == 'rain_gpm' else _list(_GRIDS) + ['gpm']
    for key in order:
        g = grids[key]
        i = _offset(obj.lon_array, g.lon0, g.step, g.nlon)
        j = _offset(obj.lat_array, g.lat0, g.step, g.nlat)
        if i is not None and j is not None:
            return key, i, j
    raise ValueError(_NOT_IMD_GRID)


def _named_cells(obj, columns):
    """Cells of named regions and cities on the object's grid."""
    key, i_off, j_off = _identify_grid(obj)
    weights = 'r025' if key == 'gpm' else key
    grid = _GRIDS[weights]
    if key == 'gpm':
        i_off, j_off = i_off - _GPM_OFFSET[0], j_off - _GPM_OFFSET[1]
    out = []
    for col in columns:
        if col.kind == 'city':
            cell = int(_cities().cells[key][col.id])
            # A place outside the grid has no cell: its column is NaN
            flat = np.array([] if cell == _NO_CELL else [cell], dtype=np.int64)
            frac = np.ones(len(flat))
        else:
            flat, frac = _regions().cells(weights, col.id)
        ilon, ilat = flat // grid.nlat, flat % grid.nlat
        weight = frac * np.cos(np.deg2rad(grid.lat0 + grid.step * ilat))
        ilon, ilat = ilon - i_off, ilat - j_off
        if ((ilon < 0) | (ilon >= len(obj.lon_array)) | (ilat < 0) |
                (ilat >= len(obj.lat_array))).any():
            what = 'is outside' if col.kind == 'city' else 'extends beyond'
            raise ValueError("{} {} this data's extent (was it clipped?).".format(col.label, what))
        out.append(_Cells(ilon, ilat, weight))
    return out


def _shapefile_cells(obj, path, by):
    """[(label, _Cells)] of the polygons of a shapefile, on the object's own grid."""
    try:
        import shapefile as pyshp
        import shapely
        from shapely.geometry import shape
    except ImportError:
        raise ImportError("region(shapefile=...) needs pyshp and shapely: "
                          "pip install pyshp shapely") from None
    path = os.fspath(path)
    stem = os.path.splitext(path)[0]
    name = os.path.basename(path)
    if os.path.isfile(stem + '.prj'):
        with open(stem + '.prj', encoding='utf-8', errors='replace') as f:
            if not f.read().strip().upper().startswith(('GEOGCS', 'GEOGCRS', 'GEODCRS')):
                raise ValueError("{} is not in longitude/latitude (see its .prj file). Reproject "
                                 "it to EPSG:4326 first.".format(name))
    groups = {}
    with pyshp.Reader(path) as sf:
        if sf.shapeType not in (pyshp.POLYGON, pyshp.POLYGONZ, pyshp.POLYGONM):
            raise ValueError("{} has no polygons; region(shapefile=...) needs polygons."
                             .format(name))
        fields = [f[0] for f in sf.fields[1:]]
        if by is not None and by not in fields:
            raise ValueError("{} has no field {!r}. Fields: {}.".format(
                name, by, ', '.join(fields)))
        for item in sf.iterShapeRecords():
            if item.shape.shapeType == pyshp.NULL or not item.shape.points:
                continue
            label = os.path.basename(stem) if by is None else str(item.record[by])
            groups.setdefault(label, []).append(shape(item.shape.__geo_interface__))
    if not groups:
        raise ValueError("{} has no polygons.".format(name))
    lon_edges = _edges_of(obj.lon_array)
    y_edges = np.sin(np.deg2rad(np.clip(_edges_of(obj.lat_array), -90, 90)))
    lat = np.asarray(obj.lat_array, dtype=float)
    out = []
    for label, geoms in groups.items():
        geom = shapely.make_valid(shapely.union_all(
            [shapely.make_valid(shapely.force_2d(g)) for g in geoms]))
        x0, y0, x1, y1 = geom.bounds
        if x0 < -180 or x1 > 360 or y0 < -90 or y1 > 90:
            raise ValueError("{} is not in longitude/latitude. Reproject it to EPSG:4326 "
                             "first.".format(name))
        ilon, ilat, frac = _cell_fractions(_equal_area(geom), lon_edges, y_edges)
        keep = frac > _MIN_FRACTION
        if not keep.any():
            raise ValueError("{} does not overlap this data's grid.".format(label))
        ilon, ilat = ilon[keep], ilat[keep]
        out.append((label, _Cells(ilon, ilat, frac[keep] * np.cos(np.deg2rad(lat[ilat])))))
    return out


def _missing(values, cat):
    """True where ``values`` are the missing value of IMD files of this variable."""
    if cat == 'rain':
        return values == _RAIN_MISSING
    if cat in ('tmin', 'tmax'):
        # 99.9 as stored (float32) and as typed
        return (values == _TEMP_MISSING) | (values == float(np.float32(_TEMP_MISSING)))
    return np.zeros(values.shape, dtype=bool)          # GPM rain has none


def _weighted_means(obj, cells):
    """
    Weighted mean of each column's cells for every time step: array
    (time, columns). Masked, missing (NaN) and missing-value cells are left
    out and the weights of the other cells rescaled; no valid cell gives NaN.
    """
    nlat = len(obj.lat_array)
    flat = np.unique(np.concatenate([c.lon * nlat + c.lat for c in cells])).astype(np.int64)
    if len(flat):
        lon_idx, lat_idx = flat // nlat, flat % nlat
        values = np.array(obj._read_cells(lon_idx, lat_idx),
                          dtype=np.float64).reshape(-1, len(flat))
        # Before the missing values become NaN: a rain mask not known yet is
        # built from these values (one read of the files)
        mask = obj._land_mask_cells(lon_idx, lat_idx, values)
        if not obj.computed:
            values[_missing(values, obj.cat)] = np.nan
        if mask is not None:
            values[:, ~np.asarray(mask, dtype=bool)] = np.nan
    else:                                              # only places without a cell
        values = np.zeros((obj.data.shape[0] if obj.computed else obj.no_days, 0))
    days = values.shape[0]
    valid = np.isfinite(values)
    values[~valid] = 0.0
    out = np.empty((days, len(cells)))
    for k, c in enumerate(cells):
        # Sums in a fixed order (not BLAS), so that the result does not depend
        # on how the values were read (lazy or in memory)
        num = np.zeros(days)
        den = np.zeros(days)
        for col, w in zip(np.searchsorted(flat, c.lon * nlat + c.lat), c.weight):
            num += values[:, col] * w
            den += valid[:, col] * w
        with np.errstate(invalid='ignore', divide='ignore'):
            out[:, k] = np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)
    return out


def _region(obj, state=None, district=None, city=None, basin=None, subbasin=None,
            shapefile=None, by=None):
    """Implementation of ``IMD.region()`` (see its documentation)."""
    if shapefile is not None:
        if any(v is not None for v in (state, district, city, basin, subbasin)):
            raise TypeError(_ONE_TYPE)
        labels, cells = zip(*_shapefile_cells(obj, shapefile, by))
    else:
        columns = _select(state, district, city, basin, subbasin, by)
        labels, cells = [c.label for c in columns], _named_cells(obj, columns)
    values = _weighted_means(obj, cells)
    return pd.DataFrame(values, index=obj._time_index(values.shape[0]), columns=_list(labels))


###############################################################################
# search, list, info
###############################################################################

_KINDS = ('state', 'district', 'city', 'basin', 'subbasin')
_SEARCH_KINDS = ('state', 'district', 'basin', 'subbasin', 'city')     # order of search results


def _match_rank(key, text):
    """0 equal, 1 starts with, 2 a word starts with, 3 contains; None if no match."""
    if key == text:
        return 0
    if key.startswith(text):
        return 1
    if (' ' + text) in (' ' + key):
        return 2
    return 3 if text in key else None


def _keep_best(found, item, rank, alias):
    """Keep the best match of each result: lowest rank, the name before an alias."""
    if rank is None:
        return
    if item not in found or (rank, alias != '') < (found[item][0], found[item][1] != ''):
        found[item] = (rank, alias)


def _search_regions(text, kinds, states, found):
    R = _regions()
    for t, kind in enumerate(_TYPES):
        if kind not in kinds or (states is not None and t not in (_STATE, _DISTRICT)):
            continue
        for key, hits in R.index[t].items():
            rank = _match_rank(key, text)
            for r, alias in hits:
                if states is None or (r if t == _STATE else R.parent[r]) in states:
                    _keep_best(found, ('region', r), rank, alias)


def _search_cities(text, states, limit, found):
    C = _cities()
    hits = C.lookup(text) + C.starting(text)
    if len({c for c, _, _ in hits}) < limit:
        hits += C.containing(text, 50 * limit)
    for c, alias, _ in hits:
        if states is None or C.state[c] in states:
            key = _normalise(alias) if alias else C.key[c]
            _keep_best(found, ('city', c), _match_rank(key, text), alias)


def _search_row(item):
    """(name, type, state, district) of a search result."""
    kind, k = item
    R = _regions()
    if kind == 'city':
        C = _cities()
        return C.name[k], 'city', R.name[C.state[k]], R.name[C.district[k]]
    t = R.type[k]
    state = R.name[k] if t == _STATE else R.name[R.parent[k]] if t == _DISTRICT else ''
    return R.name[k], _TYPES[t], state, ''


def search(text, type=None, state=None, limit=20):
    """
    Find regions by part of their name.

    Parameters
    ----------
    text : str
        Part of a name. Case, accents and punctuation are ignored. Old names
        and other spellings are found too.
    type : {'state', 'district', 'city', 'basin', 'subbasin'}, optional
        Only regions of this type.
    state : str, optional
        Only this state and its districts and cities.
    limit : int, default 20
        Maximum number of rows. Names equal to the text come first, then
        names that start with it, then names that contain it. Within each,
        states, districts, basins and sub-basins come before cities (their
        official names before their old names), and cities follow the order
        of preference of ``region(city=...)``.

    Returns
    -------
    pandas.DataFrame
        Columns ``name``, ``type``, ``state``, ``district`` (of a city) and
        ``matched_alias`` (the old name or other spelling that matched, if
        the name itself did not).

    Examples
    --------
    >>> import imdlib as imd
    >>> imd.regions.search('pun')
    >>> imd.regions.search('Rampur', type='city', state='Uttar Pradesh')
    """
    try:
        return _search(text, type, state, limit)
    except Exception as e:
        error = _user_error(e)
        if error is None:
            raise
    raise error from None


def _search(text, type, state, limit):
    if type is not None and type not in _KINDS:
        raise ValueError("type must be one of {}.".format(', '.join(map(repr, _KINDS))))
    text = _normalise(text)
    if not text:
        raise ValueError("search() needs some text.")
    states = _state_ids(state)
    kinds = _KINDS if type is None else (type,)
    found = {}                                          # (kind, id) -> (rank, alias)
    _search_regions(text, kinds, states, found)
    if 'city' in kinds:
        _search_cities(text, states, limit, found)

    def order(entry):
        item, (rank, alias) = entry
        name, kind, state_name, district = _search_row(item)
        level = 0
        if kind == 'city':          # the order of preference of region(city=...)
            C = _cities()
            level = _city_level(C.hq[item[1]], C.town[item[1]], _ALTERNATE if alias else _NAME)
        # Areas before the many cities. Areas: official names before old
        # names. Cities: the order of preference of region(city=...).
        is_city = kind == 'city'
        return (rank, is_city, not is_city and alias != '', _SEARCH_KINDS.index(kind),
                level, name.lower(), state_name, district)
    rows, seen = [], set()
    for item, (rank, alias) in sorted(found.items(), key=order):
        if len(rows) >= int(limit):
            break
        row = _search_row(item)
        if row not in seen:                 # places that would show as the same row
            seen.add(row)
            rows.append(row + (alias,))
    return pd.DataFrame(rows, columns=['name', 'type', 'state', 'district', 'matched_alias'])


def list(type, state=None, basin=None):
    """
    Official names of all regions of a type.

    Parameters
    ----------
    type : {'state', 'district', 'basin', 'subbasin'}
        Region type. Cities are found with :func:`search`.
    state : str, optional
        Only the districts of this state.
    basin : str, optional
        Only the sub-basins of this basin.

    Returns
    -------
    list of str
        Names in alphabetical order. A district name appears more than once
        if districts of that name exist in several states.

    Examples
    --------
    >>> import imdlib as imd
    >>> imd.regions.list('district', state='Kerala')
    >>> imd.regions.list('subbasin', basin='Godavari')
    """
    try:
        return _list_names(type, state, basin)
    except Exception as e:
        error = _user_error(e)
        if error is None:
            raise
    raise error from None


def _list_names(type, state, basin):
    if type == 'city':
        raise ValueError("Cities are found with imd.regions.search(), e.g. "
                         "imd.regions.search('Pune', type='city').")
    if type not in _TYPES:
        raise ValueError("type must be one of 'state', 'district', 'basin', 'subbasin'.")
    if state is not None and type != 'district':
        raise ValueError("state= can only be used with type='district'.")
    if basin is not None and type != 'subbasin':
        raise ValueError("basin= can only be used with type='subbasin'.")
    R = _regions()
    ids = R.of_type(_TYPES.index(type))
    if state is not None:
        ids = R.children.get(_resolve_region(_STATE, state), [])
    if basin is not None:
        ids = R.children.get(_resolve_region(_BASIN, basin), [])
    return sorted((R.name[k] for k in ids), key=lambda n: (n.lower(), n))


def info():
    """
    Sources and counts of the region data shipped with IMDLIB.

    Returns
    -------
    dict
        ``sources`` (a description and date for each source) and
        ``counts`` (states, districts, cities, basins, sub-basins, aliases).

    Examples
    --------
    >>> import imdlib as imd
    >>> imd.regions.info()['sources']
    """
    with open(os.path.join(_DATA_DIR, 'meta.json'), encoding='utf-8') as f:
        meta = json.load(f)
    return {'sources': {k: dict(v) for k, v in meta['sources'].items()},
            'counts': dict(meta['counts'])}
