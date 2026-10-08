"""
build_regions.py
================
Builds the region data used by ``IMD.region()`` and ``imdlib.regions``:

    imdlib/data/regions/meta.json     sources, dates, SHA-256 of inputs, counts
    imdlib/data/regions/regions.npz   states, districts, basins, sub-basins:
                                      tables, name index, grid weights
    imdlib/data/regions/cities.npz    populated places and their grid cells

Sources (read only; zips are read in place, not extracted):
    - Survey of India (SoI) state/district boundaries and HQ points, one zip
      per state (DISTRICTS/*.zip)
    - Local Government Directory (LGD) state and district lists (xlsx)
    - Central Water Commission (CWC) basins and sub-basins (shapefiles)
    - GeoNames India dump (IN.zip or IN.txt)
    - scripts/region_aliases.csv   hand-kept aliases of regions and cities
    - scripts/region_overrides.csv hand fixes (SoI -> LGD pairs below the
      name threshold, HQ places of LGD districts without a shape, wrong aliases,
      places that are not districts, SoI capital and HQ labels)
    - one IMD file per grid with a data mask: archive rain (one year),
      archive tmax (one year) and real-time tmax (one day)

SoI decides the shapes, LGD the official names and codes. The weights are
computed with ``imdlib.regions._cell_fractions``, the same code that
``region(shapefile=...)`` uses.

Steps (each prints its part of the report):
    1. read SoI, fix geometries, decode legacy symbols
    2. join SoI districts to LGD (Census 2011 code -> LGD code -> name -> overrides)
    3. place LGD districts without a shape in their parent SoI district
    4. read CWC basins and sub-basins
    5. build the name index (official names and aliases, collisions dropped)
    6. compute the weights of every region on every grid
    7. cities: GeoNames populated places merged with SoI HQs and capitals
    8. write the outputs

Maintainer-only dependencies (not needed by imdlib users):
    pip install geopandas shapely pyproj pyogrio openpyxl scipy

Usage (from the repository root):
    python scripts/build_regions.py --gis <folder with the sources> \\
        --rain-sample <archive rain file> --tmax-sample <archive tmax file> \\
        --rt-tmax-sample <real-time tmax file>

The script exits non-zero if a region has no cell on a grid. To update a
source, replace its files and rerun the script; no code change is needed.
Step by step (sources, sample files, checks, tests, what to commit):
scripts/REGIONS_DATA_UPDATE.md.
"""

import argparse
import bisect
import csv
import datetime
import difflib
import glob
import hashlib
import html
import io
import json
import multiprocessing
import os
import re
import sys
import unicodedata
import zipfile
from collections import Counter, defaultdict, namedtuple

import numpy as np
import pandas as pd
import geopandas as gpd
import pyogrio
import shapely
from scipy.spatial import cKDTree

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
# Shared with the runtime, so that names and weights match exactly
from imdlib.regions import (_BASIN as BASIN, _DISTRICT as DISTRICT, _GPM as GPM,  # noqa: E402
                            _GPM_OFFSET as GPM_OFFSET, _GRIDS as GRIDS,
                            _MIN_FRACTION as MIN_FRACTION, _NO_CELL as NO_CELL, _STATE as STATE,
                            _SUBBASIN as SUBBASIN, _TYPES as TYPES,
                            _cell_fractions as cell_fractions, _equal_area as equal_area,
                            _normalise as normalise)
from imdlib.util import land_mask_of  # noqa: E402

SCRIPT_VERSION = 4
EARTH_RADIUS = 6371.0072        # km, authalic radius of WGS84 (areas in the region table)
JOIN_SIMILARITY = 0.75          # SoI/LGD name similarity accepted in the name step
SAME_PLACE_KM = 10              # an SoI HQ point and a GeoNames place of the same name
SAME_SEAT_KM = 25               # ... if the GeoNames place is a seat of government, and
                                # two SoI points of the same name in one state
RENAMED_SEAT_KM = 5             # an SoI HQ point and a GeoNames seat of another name
SMALL_WORDS = {'and', 'of', 'the'}
LEGACY = str.maketrans({'>': 'ā', '<': 'ā', '|': 'ī', '\\': 'ī', '@': 'ū', '#': 'ū'})
GEONAMES_COLUMNS = ['geonameid', 'name', 'asciiname', 'alternatenames', 'latitude', 'longitude',
                    'fclass', 'fcode', 'cc', 'cc2', 'a1', 'a2', 'a3', 'a4', 'population',
                    'elevation', 'dem', 'timezone', 'modified']
CITY_EXCLUDED_CODES = {'PPLQ', 'PPLH', 'PPLW'}  # abandoned, historical, destroyed
HQ, CAPITAL = 1, 2                              # city flags
SEAT_CODES = ('PPLC', 'PPLA', 'PPLA2')          # GeoNames seats of government
CAPITAL_CODES = ('PPLC', 'PPLA')                # GeoNames national and state capitals
TOWN_CODES = ('PPLC', 'PPLA', 'PPLA2', 'PPLA3', 'PPLA4')  # GeoNames administrative centres
TOWN_POPULATION = 15000                         # a town: this many people, or a TOWN_CODES place
STATUS_OWN, STATUS_NEIGHBOUR, STATUS_NO_DATA = 0, 1, 2   # cells of a region on a grid


###############################################################################
# Report
###############################################################################

class Report:
    """Prints the build report and keeps it."""

    def __init__(self):
        self.lines = []

    def __call__(self, *parts):
        line = ' '.join(str(p) for p in parts)
        print(line, flush=True)
        self.lines.append(line)

    def section(self, title):
        self('\n' + title)

    def items(self, title, items):
        """'title (n):' and then one item per line ('title (0): none' if empty)."""
        items = [str(i) for i in items]
        self('{} ({}):{}'.format(title, len(items), '' if items else ' none'))
        indent = ' ' * (len(title) - len(title.lstrip()) + 4)
        for item in items:
            self(indent + item)


report = Report()


###############################################################################
# Names
###############################################################################

def decode_soi(name):
    """SoI legacy symbols -> letters (K>NGRA -> KāNGRA); trailing \\r\\n removed."""
    return str(name).translate(LEGACY).strip()


def ascii_name(name):
    """Name without accents (ā -> a)."""
    return ''.join(c for c in unicodedata.normalize('NFKD', name) if not unicodedata.combining(c))


def title_case(name):
    """'JOR BAGH' -> 'Jor Bagh', 'MAUGANJ' -> 'Mauganj', '1ST PHASE' -> '1st Phase'."""
    return re.sub(r"(^|[\s\-(/.])([a-z])", lambda m: m.group(1) + m.group(2).upper(),
                  name.lower())


def display(name):
    """
    Name as shown: without accents, written in capitals (or all in lower
    case) -> title case, and 'and', 'of', 'the' in lower case (not as first
    word).
    """
    name = ascii_name(str(name)).strip()
    letters = [c for c in name if c.isalpha()]
    upper = sum(c.isupper() for c in letters)
    if letters and ((upper > len(letters) / 2 and len(letters) > 3) or upper == 0):
        name = title_case(name)
    return ' '.join(w.lower() if i and w.lower() in SMALL_WORDS else w
                    for i, w in enumerate(name.split(' ')))


def without_brackets(name):
    """'Khandwa (East Nimar)' -> 'Khandwa' (district names in result columns)."""
    return ' '.join(re.sub(r'\([^)]*\)', ' ', name).split())


def bracketed(name):
    """'Khandwa (East Nimar)' -> ['East Nimar']."""
    return [b.strip() for b in re.findall(r'\(([^)]*)\)', name) if b.strip()]


def similarity(a, b):
    """Similarity of two district names for the SoI/LGD join (0-1)."""
    def compact(s):
        s = re.sub(r'\(.*?\)', ' ', normalise(s))
        s = s.replace(' and ', ' ').replace('twenty four', '24')
        return re.sub(r'[^a-z0-9]', '', s)
    return difflib.SequenceMatcher(None, compact(a), compact(b)).ratio()


def ascii_alternates(text, drop_district=False):
    """GeoNames alternate names: ASCII, at least 4 characters (' District' removed)."""
    out = []
    for a in str(text).split(','):
        a = a.strip()
        if drop_district:
            a = re.sub(r'\s+district$', '', a, flags=re.I).strip()
        if len(a) >= 4 and a.isascii():
            out.append(a)
    return out


###############################################################################
# Files
###############################################################################

def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def file_info(path, root):
    st = os.stat(path)
    # No modification time: a rebuild from a fresh checkout gives the same meta.json
    return {'path': os.path.relpath(path, root).replace(os.sep, '/'),
            'bytes': st.st_size, 'sha256': sha256(path)}


def dbf_date(path):
    """Date in the header of a .dbf file, or the latest one in a zip of shapefiles."""
    def parse(head):
        return '{:04d}-{:02d}-{:02d}'.format(1900 + head[1], head[2], head[3])
    if path.lower().endswith('.zip'):
        with zipfile.ZipFile(path) as z:
            return max(parse(z.read(m)[:4]) for m in z.namelist() if m.lower().endswith('.dbf'))
    with open(path, 'rb') as f:
        return parse(f.read(4))


def lgd_date(files):
    """Export date in the LGD file names (e.g. All_Stateof_India_2026-10-07_11-26-37.xlsx)."""
    dates = re.findall(r'(\d{4}-\d{2}-\d{2})_\d', ' '.join(os.path.basename(f) for f in files))
    return max(dates) if dates else ''


def read_csv_rows(path):
    """Rows of a CSV file as dicts; lines starting with '#' are comments."""
    with open(path, newline='', encoding='utf-8') as f:
        return [{k: (v or '').strip() for k, v in row.items()}
                for row in csv.DictReader(line for line in f if not line.startswith('#'))]


def read_zip_layer(zpath, member):
    """A shapefile inside a zip, read in place (no extraction)."""
    if member.startswith('/') or '..' in member.split('/'):
        raise SystemExit('unsafe member name in {}: {}'.format(zpath, member))
    return pyogrio.read_dataframe('/vsizip/' + os.path.abspath(zpath) + '/' + member)


###############################################################################
# Geometry
###############################################################################

def polygonal(geom):
    """Valid polygonal part of a geometry (make_valid may add lines or points)."""
    geom = shapely.make_valid(geom)
    if geom.geom_type in ('Polygon', 'MultiPolygon'):
        return geom
    return shapely.union_all([g for g in shapely.get_parts(geom)
                              if g.geom_type in ('Polygon', 'MultiPolygon')])


def fix_geometries(gdf, label):
    """Z values dropped and invalid geometries fixed; returns (gdf, labels of fixed rows)."""
    gdf = gdf.copy()
    geoms = shapely.force_2d(gdf.geometry.values)
    fixed = []
    for k in np.flatnonzero(~shapely.is_valid(geoms)):
        fixed.append(label(gdf.iloc[k]))
        geoms[k] = polygonal(geoms[k])
    gdf['geometry'] = geoms
    return gdf, fixed


def locate(points, polygons):
    """Index of the polygon containing each point (the nearest one if none)."""
    owner = np.full(len(points), -1)
    poly, point = shapely.STRtree(points).query(polygons, predicate='contains')
    owner[point] = poly
    outside = np.flatnonzero(owner < 0)
    if len(outside):
        # Nearest in a metric projection (India LCC)
        pts = gpd.GeoSeries(points[outside], crs=4326).to_crs('EPSG:7755').values
        polys = gpd.GeoSeries(polygons, crs=4326).to_crs('EPSG:7755').values
        near = shapely.STRtree(polys).query_nearest(pts, return_distance=False, all_matches=False)
        owner[outside[near[0]]] = near[1]
    return owner, len(outside)


def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2 +
         np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


###############################################################################
# Sources
###############################################################################

Soi = namedtuple('Soi', 'zips states districts district_hq state_hq')


def read_soi(folder):
    """SoI layers of all state zips, in lon/lat, with Z values dropped and fixed geometries."""
    report.section('[1] Survey of India')
    zips = sorted(glob.glob(os.path.join(folder, '*.zip')))
    layers = defaultdict(list)
    for zp in zips:
        with zipfile.ZipFile(zp) as z:
            members = [m for m in z.namelist() if m.lower().endswith('.shp')]
        for m in members:
            code = m.split('/')[0]                     # folder = LGD state code
            if not code.isdigit():
                raise SystemExit('unexpected folder in {}: {}'.format(zp, m))
            base = os.path.basename(m).upper()
            if base.endswith('STATE_BDY.SHP'):
                kind = 'states'
            elif base.endswith('DISTRICT_BDY.SHP') or base == 'UTTAR_PRADESH_DISTRICT.SHP':
                kind = 'districts'
            elif base.endswith('DISTRICT_HQ.SHP'):
                kind = 'district_hq'
            elif base.endswith('STATE_HQ.SHP'):
                kind = 'state_hq'
            else:
                report('  ignored layer', zp, m)
                continue
            gdf = read_zip_layer(zp, m).to_crs(4326)   # custom LCC -> lon/lat
            gdf['folder'] = int(code)
            layers[kind].append(gdf)
    layers = {k: gpd.GeoDataFrame(pd.concat(v, ignore_index=True), crs=4326)
              for k, v in layers.items()}
    report('  zips:', len(zips), '| layers:', {k: len(v) for k, v in layers.items()})
    states, fixed_s = fix_geometries(layers['states'], lambda r: 'state ' + decode_soi(r.STATE))
    districts, fixed_d = fix_geometries(layers['districts'],
                                        lambda r: 'district ' + decode_soi(r.DISTRICT))
    report('  geometries fixed (make_valid):', ', '.join(fixed_s + fixed_d) or 'none')
    if states.folder.duplicated().any():
        raise SystemExit('two state polygons for one state code')
    # DNH&DD districts carry the old state code 26; the merged UT is 38
    old_code = districts.STATE_LGD.astype(str).str.strip() == '26'
    code = districts.STATE_LGD.astype(str).str.strip().astype(int)
    districts['state_code'] = code.where(~old_code, 38)
    if (districts.state_code != districts.folder).any():
        raise SystemExit('district state codes differ from folder codes')
    report('  DNH&DD state code 26 -> 38:', int(old_code.sum()), 'districts')
    districts['soi_name'] = [decode_soi(n) for n in districts.DISTRICT]
    return Soi(zips, states.sort_values('folder').reset_index(drop=True), districts,
               layers['district_hq'], layers['state_hq'])


Lgd = namedtuple('Lgd', 'files states districts')


def read_xlsx_table(path, required):
    """First sheet of an xlsx file, from the row with the ``required`` headers."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    for k, row in enumerate(rows):
        cells = {str(c).strip() for c in row if c is not None}
        if all(name in cells for name in required):
            header = [str(c).strip() if c is not None else '' for c in row]
            body = [r for r in rows[k + 1:] if any(c is not None for c in r)]
            return pd.DataFrame(body, columns=header)
    raise SystemExit('no header with {} in {}'.format(required, path))


def read_lgd(folder):
    state_file = sorted(glob.glob(os.path.join(folder, 'All_Stateof_India_*.xlsx')))[-1]
    district_file = sorted(glob.glob(os.path.join(folder, 'All_Districtof_India_*.xlsx')))[-1]
    S = read_xlsx_table(state_file, ['State Code', 'State Name (In English)'])
    D = read_xlsx_table(district_file, ['District Code', 'District Name(In English)'])
    states = pd.DataFrame({'code': S['State Code'].astype(float).astype(int),
                           'name': S['State Name (In English)'].astype(str).str.strip(),
                           'local': S['State Name (In Local)'].astype(str).str.strip()})
    districts = pd.DataFrame({'state': D['State Code'].astype(float).astype(int),
                              'code': D['District Code'].astype(float).astype(int),
                              'name': D['District Name(In English)'].astype(str).str.strip(),
                              'c2011': D['Census 2011 Code'].astype(float).fillna(0).astype(int)})
    return Lgd([state_file, district_file], states, districts)


Cwc = namedtuple('Cwc', 'basins subbasins')


def read_cwc(basin_shp, subbasin_shp):
    """CWC basins and sub-basins in lon/lat, names unescaped, unnamed basins named."""
    report.section('[4] CWC basins and sub-basins')
    basins = gpd.read_file(basin_shp).to_crs(4326)
    subs = gpd.read_file(subbasin_shp).to_crs(4326)
    basins, fixed_b = fix_geometries(basins, lambda r: 'basin ' + str(r.Basin_Code))
    subs, fixed_s = fix_geometries(subs, lambda r: 'sub-basin ' + html.unescape(str(r.Sub_Basin)))
    basins['name'] = [html.unescape(str(n)).strip() if n is not None else None
                      for n in basins.Basin_Name]
    subs['name'] = [html.unescape(str(n)).strip() for n in subs.Sub_Basin]
    subs['basin_name'] = [html.unescape(str(n)).strip() for n in subs.BA_NAME]
    named = []
    for k in np.flatnonzero(basins.name.isna()):
        code = basins.Basin_Code.iloc[k]
        names = subs.basin_name[subs.Basin_Code == code].unique()
        if len(names) != 1:
            raise SystemExit('cannot name basin {} from the sub-basins: {}'.format(code, names))
        basins.loc[basins.index[k], 'name'] = re.sub(r'\s+Basin$', '', names[0])
        named.append('{} -> {}'.format(code, basins.name.iloc[k]))
    if subs.name.duplicated().any():
        raise SystemExit('duplicate sub-basin names')
    report('  basins:', len(basins), '| sub-basins:', len(subs))
    report.items('  basins named from the sub-basin layer', named)
    report('  geometries fixed (make_valid):', ', '.join(fixed_b + fixed_s) or 'none')
    return Cwc(basins.sort_values('Basin_Code').reset_index(drop=True),
               subs.sort_values(['Basin_Code', 'name']).reset_index(drop=True))


def read_geonames(path, states):
    """GeoNames rows with coordinates and the SoI state code of their point."""
    if path.lower().endswith('.zip'):
        with zipfile.ZipFile(path) as z:
            source = io.BytesIO(z.read('IN.txt'))
    else:
        source = path
    gn = pd.read_csv(source, sep='\t', header=None, names=GEONAMES_COLUMNS, dtype=str,
                     quoting=csv.QUOTE_NONE, keep_default_na=False, encoding='utf-8')
    gn['lat'] = gn.latitude.astype(float)
    gn['lon'] = gn.longitude.astype(float)
    # The state by point-in-polygon (GeoNames uses FIPS codes, some '00' or blank)
    owner, _ = locate(shapely.points(gn.lon.values, gn.lat.values), states.geometry.values)
    gn['state_code'] = states.folder.values[owner]
    return gn


###############################################################################
# 2-3. Districts: LGD join and LGD districts without a shape
###############################################################################

def join_lgd(districts, lgd, state_name, overrides):
    """
    LGD code of each SoI district: Census 2011 code -> LGD code -> state and
    name similarity -> overrides (kind=match). Returns the districts with the
    columns lgd, how, state_name and name (official display name).
    """
    report.section('[2] LGD join')
    report('  LGD states:', len(lgd.states), '| districts:', len(lgd.districts))
    L = lgd.districts
    districts = districts.copy()
    districts['state_name'] = [state_name[c] for c in districts.state_code]
    codes = [int(c) if str(c).strip().isdigit() else None for c in districts.DIST_LGD]
    lgd_code, how, used = [None] * len(districts), [None] * len(districts), set()

    def take(i, code, step):
        lgd_code[i], how[i] = int(code), step
        used.add(int(code))
    rows = list(zip(districts.state_code, codes, districts.soi_name))
    for i, (st, c, _) in enumerate(rows):               # SoI code = Census 2011 code
        if c is not None:
            m = L[(L.state == st) & (L.c2011 == c) & (L.c2011 > 0)]
            if len(m) == 1:
                take(i, m.code.iloc[0], 'c2011')
    for i, (st, c, name) in enumerate(rows):            # SoI code = LGD code (newest)
        if lgd_code[i] is None and c is not None:
            m = L[(L.state == st) & (L.code == c) & ~L.code.isin(used)]
            if len(m) == 1 and similarity(name, m.name.iloc[0]) > 0.8:
                take(i, c, 'lgd_code')
    for i, (st, _, name) in enumerate(rows):            # same state, similar name
        if lgd_code[i] is None:
            m = L[(L.state == st) & ~L.code.isin(used)]
            score = m.name.map(lambda n: similarity(name, n))
            if len(m) and score.max() >= JOIN_SIMILARITY:
                take(i, m.code[score.idxmax()], 'name')
    for row in (r for r in overrides if r['kind'] == 'match'):
        hit = [i for i in range(len(districts)) if districts.state_name.iloc[i] == row['state']
               and normalise(districts.soi_name.iloc[i]) == normalise(row['name'])]
        if len(hit) != 1:
            raise SystemExit('override match not found: {}'.format(row))
        if lgd_code[hit[0]] is None:
            take(hit[0], int(row['value']), 'override')
    districts['lgd'] = lgd_code
    districts['how'] = how
    report('  join outcome:', dict(Counter(h or 'none' for h in how)))
    if districts.lgd.dropna().duplicated().any():
        raise SystemExit('an LGD district was joined twice')
    lgd_name = dict(zip(L.code, L.name))
    # Official name from LGD; the SoI name for shapes without an LGD code. Columns
    # show it without a part in brackets ('Khandwa (East Nimar)' -> 'Khandwa')
    districts['full_name'] = [display(lgd_name[int(c)]) if c == c and c is not None else
                              display(n) for c, n in zip(districts.lgd, districts.soi_name)]
    districts['name'] = [without_brackets(n) for n in districts.full_name]
    report.items('  names shown without their part in brackets', [
        '{} -> {}'.format(f, n) for f, n in zip(districts.full_name, districts.name) if f != n])
    no_lgd = districts[districts.lgd.isna()]
    report.items('  SoI without LGD', [
        '{} ({})'.format(n, s) for n, s in zip(no_lgd.name, no_lgd.state_name)])
    report.items('  name-step matches', ['{} -> {}'.format(a, b) for a, b, h in
                                         zip(districts.soi_name, districts.name, districts.how)
                                         if h == 'name'])
    if districts.duplicated(subset=['state_code', 'name']).any():
        raise SystemExit('two districts of one state have the same name')
    return districts


def newer_districts(districts, lgd, state_name, geonames, soi, overrides):
    """
    LGD districts without an SoI shape and the SoI district (row index) that
    their HQ lies in. The HQ is a place named like the district, or the
    place given in the overrides (kind=hq); kind=parent gives the district.
    """
    report.section('[3] LGD districts without a shape')
    joined = set(districts.lgd.dropna().astype(int))
    by_name = {(r['state'], normalise(r['name'])): r for r in overrides
               if r['kind'] in ('hq', 'parent')}
    out = []
    for _, o in lgd.districts[~lgd.districts.code.isin(joined)].iterrows():
        sname = state_name[o.state]
        override = by_name.get((sname, normalise(o['name'])))
        in_state = districts[districts.state_code == o.state]
        if override is not None and override['kind'] == 'parent':
            parent = in_state.index[[normalise(n) == normalise(override['value'])
                                     for n in in_state.name]][0]
            via = 'override'
        else:
            hq = override['value'] if override is not None else o['name']
            point, via = hq_point(hq, o.state, geonames, soi)
            if point is None:
                raise SystemExit('no HQ point for LGD district {} ({})'.format(o['name'], sname))
            inside = in_state.index[shapely.contains(in_state.geometry.values, point)]
            if len(inside) != 1:
                raise SystemExit('HQ of {} is not in one district of {}'.format(o['name'], sname))
            parent = inside[0]
        out.append(dict(name=display(o['name']), state=int(o.state), code=int(o.code),
                        parent=parent))
        report('  {} ({}) -> parent {} [{}]'.format(display(o['name']), sname,
                                                    districts.name[parent], via))
    return out


def hq_point(name, state, geonames, soi):
    """(point, description) of a place in a state: GeoNames (admin units first), else SoI HQ."""
    key = normalise(name)
    cand = geonames[(geonames.state_code == state) &
                    ((geonames.name.map(normalise) == key) |
                     (geonames.asciiname.map(normalise) == key))]
    if len(cand):
        cand = cand.assign(rank=cand.fclass.map({'A': 0, 'P': 1}).fillna(2)).sort_values('rank')
        r = cand.iloc[0]
        return shapely.points(r.lon, r.lat), 'GeoNames {} {} ({:.3f}, {:.3f})'.format(
            r.fcode, name, r.lat, r.lon)
    hqs = soi.district_hq[soi.district_hq.folder == state]
    hit = hqs[[normalise(decode_soi(n)) == key for n in hqs.DIST_HQ]]
    if len(hit):
        return hit.geometry.iloc[0], 'SoI HQ point ' + name
    return None, None


###############################################################################
# Region table
###############################################################################

class RegionTable:
    """
    All regions in id order: states (by name), districts (by state and name),
    basins (by CWC code), sub-basins (by basin code and name).
    """

    def __init__(self, soi_states, districts, state_name, cwc):
        states = soi_states.assign(name=[state_name[c] for c in soi_states.folder])
        self.states = states.sort_values('name').reset_index(drop=True)
        districts = districts.assign(row=districts.index)
        self.districts = districts.sort_values(['state_name', 'name']).reset_index(drop=True)
        self.type, self.name, self.parent, self.code, self.geometry = [], [], [], [], []
        self.full = []              # official name in full ('Khandwa (East Nimar)')
        self.state_id = {}          # LGD state code -> id
        self.district_id = {}       # row of the SoI district table -> id
        self.basin_id = {}          # CWC code -> id
        for _, s in self.states.iterrows():
            self.state_id[s.folder] = self._add(STATE, s['name'], -1, str(s.folder), s.geometry)
        for _, d in self.districts.iterrows():
            code = '' if d.lgd is None or d.lgd != d.lgd else str(int(d.lgd))
            self.district_id[d.row] = self._add(DISTRICT, d['name'], self.state_id[d.state_code],
                                                code, d.geometry, d.full_name)
        for _, b in cwc.basins.iterrows():
            self.basin_id[b.Basin_Code] = self._add(BASIN, b['name'], -1, b.Basin_Code, b.geometry)
        for _, s in cwc.subbasins.iterrows():
            self._add(SUBBASIN, s['name'], self.basin_id[s.Basin_Code], '', s.geometry)

    def _add(self, t, name, parent, code, geometry, full=None):
        self.type.append(t)
        self.name.append(name)
        self.full.append(full or name)
        self.parent.append(parent)
        self.code.append(code)
        self.geometry.append(geometry)
        return len(self.name) - 1

    def __len__(self):
        return len(self.name)

    def ids(self, t):
        return [k for k in range(len(self)) if self.type[k] == t]

    def scope(self, k):
        """Names are unique within a scope: the state for districts, else the type."""
        return self.parent[k] if self.type[k] == DISTRICT else -1

    def find(self, t, name, state=''):
        """Id of a region by official name (and state name, for districts)."""
        hits = [k for k in self.ids(t) if normalise(self.name[k]) == normalise(name) and
                (t != DISTRICT or self.name[self.parent[k]] == state)]
        if len(hits) != 1:
            raise SystemExit('region not found: {} {!r} {!r}'.format(TYPES[t], name, state))
        return hits[0]


###############################################################################
# 5. Name index
###############################################################################

def collect_aliases(table, lgd, cwc, geonames, hand_aliases):
    """[(region, alias as written, source)] from SoI, LGD, CWC, GeoNames and the hand list."""
    aliases = []
    for k in table.ids(STATE):
        row = table.states.iloc[k]
        aliases.append((k, decode_soi(row.STATE), 'SoI'))
        local = lgd.states.local[lgd.states.code == row.folder]
        if len(local) and local.iloc[0].isascii():
            aliases.append((k, local.iloc[0], 'LGD local name'))
    for _, d in table.districts.iterrows():
        k = table.district_id[d.row]
        aliases.append((k, d.soi_name, 'SoI'))
        if d.full_name != d['name']:            # 'Khandwa (East Nimar)', 'East Nimar'
            aliases.append((k, d.full_name, 'LGD'))
            aliases += [(k, b, 'LGD') for b in bracketed(d.full_name)
                        if sum(c.isalpha() for c in b) >= 4]
    for code, b in table.basin_id.items():
        for name in cwc.subbasins.basin_name[cwc.subbasins.Basin_Code == code].unique():
            aliases.append((b, name, 'CWC BA_NAME'))
    aliases += district_aliases_from_geonames(table, geonames)
    for r in hand_aliases:
        if r['type'] != 'city':
            k = table.find(TYPES.index(r['type']), r['name'], r['state'])
            aliases.append((k, r['alias'], 'region_aliases.csv'))
    return aliases


def check_hand_aliases(rows):
    """Rows of region_aliases.csv: known type, an alias, a state for districts and cities."""
    for r in rows:
        where = 'region_aliases.csv row {}'.format(','.join(r.get(c, '') for c in
                                                             ('type', 'name', 'state', 'district',
                                                              'alias')))
        if r.get('type') not in TYPES + ('city',):
            raise SystemExit('{}: type must be one of {}'.format(where, ', '.join(TYPES + ('city',))))
        if not r.get('name') or not r.get('alias'):
            raise SystemExit('{}: name and alias are needed'.format(where))
        if r['type'] in ('district', 'city') and not r.get('state'):
            raise SystemExit('{}: state is needed for a {}'.format(where, r['type']))
        if r.get('district') and r['type'] != 'city':
            raise SystemExit('{}: district is only used for cities'.format(where))


def district_aliases_from_geonames(table, geonames):
    """
    Names of GeoNames ADM2 units for the SoI district they belong to: the one
    whose name the unit also has (in the state of the unit's point, or in any
    state if only one district fits), else the district containing the point
    if no other unit is in it.
    """
    adm2 = geonames[geonames.fcode == 'ADM2'].reset_index(drop=True)
    rows = table.districts
    owner, _ = locate(shapely.points(adm2.lon.values, adm2.lat.values), rows.geometry.values)
    names = [{normalise(a) for a in [n, an] + ascii_alternates(alts, drop_district=True)}
             for n, an, alts in zip(adm2.name, adm2.asciiname, adm2.alternatenames)]
    keys = [{normalise(n), normalise(s)} for n, s in zip(rows.name, rows.soi_name)]
    by_name = {}
    for a in range(len(adm2)):
        cands = [k for k in range(len(rows)) if names[a] & keys[k]]
        same_state = [k for k in cands if rows.state_code[k] == rows.state_code[owner[a]]]
        if len(same_state) == 1:
            by_name[a] = same_state[0]
        elif not same_state and len(cands) == 1:   # enclaves
            by_name[a] = cands[0]
    taken = set(by_name.values())
    others = Counter(owner[a] for a in range(len(adm2)) if a not in by_name)
    by_point = {a: owner[a] for a in range(len(adm2))
                if a not in by_name and others[owner[a]] == 1 and owner[a] not in taken}
    unused = sorted(adm2.name[a] for a in range(len(adm2))
                    if a not in by_name and a not in by_point)
    report('  GeoNames ADM2 rows:', len(adm2), '| matched by name:', len(by_name),
           '| by point only:', len(by_point), '| not used:', len(unused))
    report.items('    by point only', ['{} -> {}'.format(adm2.name[a], rows.name[k])
                                         for a, k in sorted(by_point.items())])
    report.items('    not used', unused)
    out = []
    for a, k in sorted({**by_name, **by_point}.items()):
        names = [adm2.name[a], adm2.asciiname[a]]
        for text in names + ascii_alternates(adm2.alternatenames[a], drop_district=True):
            if text.isascii() and len(text) >= 4:
                out.append((table.district_id[rows.row[k]], text, 'GeoNames ADM2'))
    return out


def contains_words(name, words):
    """True if the words (a list) occur one after the other in name (a list of words)."""
    n = len(words)
    return any(name[i:i + n] == words for i in range(len(name) - n + 1))


def build_name_index(table, aliases, overrides):
    """
    Official names and the aliases that survive the collision rules:
    ([(type, key, region, alias as shown or '')], [(state, key, alias as
    shown, [districts])]). Official names beat aliases: an alias equal to the
    official name of another region in the same scope is dropped, and so is
    an alias shared by two regions of one scope. A district alias whose words
    occur in the official names of two or more districts of its state (the
    name of a district before it was split, e.g. Bengaluru) points to none of
    them; it is kept apart so that it can raise an error listing them.
    """
    keys = [(table.type[k], normalise(table.name[k]), k, '') for k in range(len(table))]
    wrong = set()
    for r in (r for r in overrides if r['kind'] == 'drop_alias'):
        t = DISTRICT if r['state'] else next(t for t in (STATE, BASIN, SUBBASIN) if any(
            normalise(table.name[k]) == normalise(r['name']) for k in table.ids(t)))
        wrong.add((table.find(t, r['name'], r['state']), normalise(r['value'])))
    report('  aliases removed by region_overrides.csv:',
           sum((k, normalise(text)) in wrong for k, text, _ in aliases))
    official = defaultdict(set)
    for t, key, k, _ in keys:
        official[(t, key)].add(k)
    candidates, seen, dropped = [], set(), []
    for k, text, source in aliases:
        t, key = table.type[k], normalise(text)
        if not key or key == normalise(table.name[k]) or (k, key) in seen or (k, key) in wrong:
            continue
        clash = [o for o in official.get((t, key), ())
                 if o != k and table.scope(o) == table.scope(k)]
        if clash:
            dropped.append('{} {!r} for {} (official name of {})'.format(
                TYPES[t], text, table.name[k], table.name[clash[0]]))
            continue
        seen.add((k, key))
        candidates.append((t, key, k, display(text), source))
    # Names of districts before a split
    words = {k: [normalise(table.name[k]).split(), normalise(table.full[k]).split()]
             for k in table.ids(DISTRICT)}
    in_state = defaultdict(list)
    for k in table.ids(DISTRICT):
        in_state[table.parent[k]].append(k)
    split = {}
    for t, key, k, text, _ in candidates:
        if t != DISTRICT or (table.parent[k], key) in split:
            continue
        inside = [o for o in in_state[table.parent[k]]
                  if any(contains_words(w, key.split()) for w in words[o])]
        if len(inside) >= 2:
            inside.sort(key=lambda o: (int(table.code[o]) if table.code[o] else 1 << 30, o))
            split[(table.parent[k], key)] = (text, inside)
    candidates = [c for c in candidates if c[0] != DISTRICT or
                  (table.parent[c[2]], c[1]) not in split]
    report('  names of districts before a split ({}; they raise an error listing the '
           'districts):'.format(len(split)))
    for (st, key), (text, inside) in sorted(split.items(), key=lambda x: x[1][0]):
        report('    {} ({}) -> {}'.format(text, table.name[st],
                                          ', '.join(table.name[o] for o in inside)))
    sharing = defaultdict(set)
    for t, key, k, _, _ in candidates:
        sharing[(t, key, table.scope(k))].add(k)
    kept = Counter()
    for t, key, k, text, source in candidates:
        others = sharing[(t, key, table.scope(k))] - {k}
        if others:
            dropped.append('{} {!r} for {} (also an alias of {})'.format(
                TYPES[t], text, table.name[k], ', '.join(table.name[o] for o in sorted(others))))
        else:
            keys.append((t, key, k, text))
            kept[source] += 1
    report('  aliases kept:', sum(kept.values()), '| by source:', dict(kept))
    report.items('  aliases dropped', sorted(set(dropped)))
    regions_of = defaultdict(set)
    for t, key, k, _ in keys:
        regions_of[(t, key)].add(k)
    report.items('  names that fit more than one region (an official name is used first, else '
                 'state= is needed)', sorted(
                     key for (t, key), ks in regions_of.items() if len(ks) > 1))
    splits = [(st, key, text, inside) for (st, key), (text, inside) in sorted(split.items())]
    return sorted(keys), splits


def not_districts(table, overrides):
    """
    Places that are often taken for districts but are not districts in the
    official boundaries (kind=not_district): [(name, state id, part of)].
    """
    out = []
    for r in (r for r in overrides if r['kind'] == 'not_district'):
        state = table.find(STATE, r['state'])
        if any(normalise(table.name[k]) == normalise(r['name']) and table.parent[k] == state
               for k in table.ids(DISTRICT)):
            raise SystemExit('{} is a district of {}'.format(r['name'], r['state']))
        out.append((display(r['name']), state, r['value']))
    report.items('  not districts (region_overrides.csv)',
                 ['{} (part of {})'.format(n, p) for n, _, p in out])
    return out


###############################################################################
# 6. Weights
###############################################################################

def data_mask(path, grid, kind):
    """Cells with data (lon, lat) in an IMD file of ``kind`` 'rain' or 'tmax'."""
    cells = grid.nlat * grid.nlon
    size = os.path.getsize(path)
    days = size // (cells * 4)
    if days == 0 or days * cells * 4 != size:
        raise SystemExit('{} is not a {}x{} IMD file'.format(path, grid.nlat, grid.nlon))
    a = np.fromfile(path, '<f4').reshape(days, grid.nlat, grid.nlon).transpose(0, 2, 1)
    return land_mask_of(kind, [a], days)


def data_masks(args):
    """Cells with data on each grid, from the sample files."""
    samples = {'r025': (args.rain_sample, 'rain'), 't100': (args.tmax_sample, 'tmax'),
               't050': (args.rt_tmax_sample, 'tmax')}
    masks = {}
    for key, (path, kind) in samples.items():
        masks[key] = data_mask(path, GRIDS[key], kind)
        report('  data mask {}: {} cells with data ({})'.format(
            key, int(masks[key].sum()), os.path.basename(path)))
    return masks


# The 8 cells around a cell
AROUND = [(di, dj) for di in (-1, 0, 1) for dj in (-1, 0, 1) if di or dj]


class DataCells:
    """
    Cells of a grid that have data. A place without data in its own cells
    uses the nearest cell with data among the 8 cells around them; if none
    of those has data either, it keeps its own cells (no data: NaN).
    """

    def __init__(self, grid, mask):
        self.grid, self.mask = grid, mask

    def containing(self, lon, lat):
        """(ilon, ilat) of the cell containing each point (may be outside the grid)."""
        g = self.grid
        return (np.rint((np.asarray(lon) - g.lon0) / g.step).astype(int),
                np.rint((np.asarray(lat) - g.lat0) / g.step).astype(int))

    def inside(self, i, j):
        return (i >= 0) & (i < self.grid.nlon) & (j >= 0) & (j < self.grid.nlat)

    def has_data(self, i, j):
        ok = self.inside(i, j)
        ok[ok] = self.mask[i[ok], j[ok]]
        return ok

    def city_cells(self, lon, lat):
        """
        (cell, status) of each place: the containing cell if it has data
        (status 0), else the nearest cell with data around it (1), else the
        containing cell, or NO_CELL outside the grid (2: no data).
        """
        g = self.grid
        lon, lat = np.asarray(lon, float), np.asarray(lat, float)
        i, j = self.containing(lon, lat)
        inside = self.inside(i, j)
        flat = np.where(inside, i * g.nlat + j, NO_CELL)
        status = np.where(self.has_data(i, j), STATUS_OWN, STATUS_NO_DATA)
        todo = np.flatnonzero(status != STATUS_OWN)
        best = np.full(len(todo), np.inf)
        for di, dj in AROUND:
            ii, jj = i[todo] + di, j[todo] + dj
            ok = self.has_data(ii, jj)
            km = np.where(ok, haversine(g.lat0 + g.step * jj, g.lon0 + g.step * ii,
                                        lat[todo], lon[todo]), np.inf)
            better = km < best
            best[better] = km[better]
            flat[todo[better]] = ii[better] * g.nlat + jj[better]
            status[todo[better]] = STATUS_NEIGHBOUR
        return flat, status

    def region_neighbour(self, ilon, ilat, geom):
        """
        (cell, km) of the cell with data around a region's own cells that is
        nearest to the region, or None if none of them has data.
        """
        g = self.grid
        own = set(zip(ilon.tolist(), ilat.tolist()))
        cand = sorted({(i + di, j + dj) for i, j in own for di, dj in AROUND} - own)
        cand = [(i, j) for i, j in cand if 0 <= i < g.nlon and 0 <= j < g.nlat and self.mask[i, j]]
        if not cand:
            return None
        i, j = np.array(cand).T
        centres = shapely.points(g.lon0 + g.step * i, g.lat0 + g.step * j)
        near = shapely.get_coordinates(shapely.shortest_line(geom, centres))[0::2]
        km = haversine(near[:, 1], near[:, 0], g.lat0 + g.step * j, g.lon0 + g.step * i)
        k = int(np.argmin(km))
        return int(i[k] * g.nlat + j[k]), float(km[k])


_projected = []       # geometries for the worker processes (shared by fork)


def _fractions_job(k):
    """Fractions of region k on every grid, and its area in projected units."""
    geom = _projected[k]
    out = {'area': geom.area}
    for key, grid in GRIDS.items():
        out[key] = cell_fractions(geom, *grid.edges())
    return out


def check_gpm_alignment():
    """GPM cells are the r025 cells shifted by GPM_OFFSET (GPM reuses the r025 weights)."""
    r025 = GRIDS['r025']
    i, j = GPM_OFFSET
    if not (np.allclose(GPM.lon[i:i + r025.nlon], r025.lon, rtol=0, atol=1e-9) and
            np.allclose(GPM.lat[j:j + r025.nlat], r025.lat, rtol=0, atol=1e-9) and
            GPM.step == r025.step):
        raise SystemExit('the GPM grid is not aligned with r025 at offset {}'.format(GPM_OFFSET))
    report('  GPM grid = r025 grid shifted by {} cells (lon, lat): checked'.format(GPM_OFFSET))


def check_fractions(geom):
    """Largest difference between cell_fractions and exact intersections (all grids)."""
    worst = 0.0
    for grid in GRIDS.values():
        lon_e, y_e = grid.edges()
        i, j, f = cell_fractions(geom, lon_e, y_e)
        boxes = shapely.box(lon_e[i], y_e[j], lon_e[i + 1], y_e[j + 1])
        exact = shapely.area(shapely.intersection(geom, boxes)) / shapely.area(boxes)
        worst = max(worst, float(np.max(np.abs(exact - f))) if len(f) else 0.0)
    return worst


def build_weights(table, masks, workers):
    """
    Weights of every region on every grid (CSR arrays <g>_ptr, <g>_cell,
    <g>_frac), <g>_status (0 own cells, 1 a cell with data next to them
    because none of them has data, 2 no data: own cells kept, NaN on IMD
    data) and reg_area_km2.
    """
    check_gpm_alignment()
    _projected[:] = [equal_area(g) for g in table.geometry]
    samples = (table.state_id[27], table.ids(DISTRICT)[0], table.ids(SUBBASIN)[0])
    worst = max(check_fractions(_projected[k]) for k in samples)
    report('  rectangle clipping vs exact intersection, max |difference| of fractions: '
           '{:.2e}'.format(worst))
    if worst > 1e-9:
        raise SystemExit('rectangle clipping is not exact enough')
    with multiprocessing.get_context('fork').Pool(workers) as pool:
        results = pool.map(_fractions_job, range(len(table)), chunksize=1)
    area = np.array([r['area'] for r in results])           # in degrees x sin(lat)
    arrays = {'reg_area_km2': area * np.pi / 180 * EARTH_RADIUS ** 2}
    failed = []
    for key, grid in GRIDS.items():
        lon_e, y_e = grid.edges()
        cell_area = (lon_e[1] - lon_e[0]) * np.diff(y_e)        # per latitude row
        data = DataCells(grid, masks[key])
        ptr, cells, fracs = [0], [], []
        status = np.full(len(table), STATUS_OWN, np.uint8)
        low, moved, nodata = [], [], []
        for k, r in enumerate(results):
            ilon, ilat, frac = r[key]
            coverage = np.sum(frac * cell_area[ilat]) / r['area']
            keep = frac > MIN_FRACTION
            ilon, ilat, frac = ilon[keep], ilat[keep], frac[keep]
            flat = ilon * grid.nlat + ilat
            if len(flat) == 0:
                failed.append('{} {}'.format(key, table.name[k]))
            elif not masks[key][ilon, ilat].any():
                near = data.region_neighbour(ilon, ilat, table.geometry[k])
                if near is None:
                    status[k] = STATUS_NO_DATA
                    nodata.append(table.name[k])
                else:
                    flat, frac = np.array([near[0]]), np.ones(1)
                    status[k] = STATUS_NEIGHBOUR
                    moved.append('{} ({:.0f} km)'.format(table.name[k], near[1]))
            if coverage < 0.99:
                low.append('{} {:.1%}'.format(table.name[k], coverage))
            cells.extend(flat)
            fracs.extend(frac)
            ptr.append(len(cells))
        arrays.update({key + '_ptr': np.array(ptr, np.int32),
                       key + '_cell': np.array(cells, np.uint16),
                       key + '_frac': np.array(fracs, np.float32),
                       key + '_status': status})
        n = np.diff(ptr)
        report('  {}: {} entries; cells per region min {} median {} max {}'.format(
            key, len(cells), n.min(), int(np.median(n)), n.max()))
        report.items('    coverage < 99%', low)
        report.items('    no own cell with data, a cell next to them used', moved)
        report.items('    no data, NaN', nodata)
    if failed:
        report('  REGIONS WITHOUT CELLS:', ', '.join(failed))
        sys.exit(1)
    return arrays


###############################################################################
# 7. Cities
###############################################################################

CITY_OVERRIDES = ('city_name', 'city_relabel', 'city_same', 'city_keep', 'city_skip')
FCODE_RANK = {'PPLC': 0, 'PPLA': 1, 'PPLA2': 2, 'PPLA3': 3, 'PPLA4': 4}     # others 5


class Town:
    """SoI state capital and district HQ points of one town (same name, same state)."""

    def __init__(self, name, state, flag):
        self.name, self.state, self.flag = name, state, flag
        self.key = normalise(name)
        self.points, self.flags = [], []           # SoI points and their flags
        self.labels = []                           # SoI spellings kept as aliases
        self.keep = False                          # not merged with a place of another name
        self.same = None                           # GeoNames name of the same place
        self.district = ''                         # district of the town (official name)

    @property
    def home(self):
        """Point of the town: its first HQ point, else its capital point."""
        return next((p for p, f in zip(self.points, self.flags) if f == HQ), self.points[0])

    @property
    def lat(self):
        return np.array([p.y for p in self.points])

    @property
    def lon(self):
        return np.array([p.x for p in self.points])


def soi_towns(soi, table, overrides, state_code):
    """
    SoI state capitals and district HQs as towns. The state of an HQ is
    that of its district (its zip), the state of a capital that of its point.
    region_overrides.csv: city_name corrects a label (the label is kept as
    an alias), city_relabel replaces a wrong label, city_same names the
    GeoNames place of the town, city_keep keeps the town apart from places
    of another name, city_skip drops a point.
    Points of the same name in one state within SAME_SEAT_KM (state capitals:
    any distance) are one town.
    """
    rows = {}
    for r in overrides:
        if r['kind'] in CITY_OVERRIDES:
            rows[(state_code[r['state']], normalise(r['name']))] = r
    caps, hqs = soi.state_hq, soi.district_hq
    owner, _ = locate(np.array(list(caps.geometry)), soi.states.geometry.values)
    points = [(decode_soi(n), g, CAPITAL, int(soi.states.folder[o]))
              for n, g, o in zip(caps.CAPITAL_NA, caps.geometry, owner)]
    points += [(decode_soi(n), g, HQ, int(f))
               for n, g, f in zip(hqs.DIST_HQ, hqs.geometry, hqs.folder) if n is not None]
    used, skipped, towns = set(), [], []
    for label, point, flag, state in points:
        row = rows.get((state, normalise(label)))
        name, keep, same = display(label), False, None
        if row is not None:
            used.add((state, normalise(label)))
            if row['kind'] == 'city_skip':
                skipped.append('{} ({})'.format(name, row['state']))
                continue
            if row['kind'] in ('city_name', 'city_relabel'):
                name = row['value']
            keep = row['kind'] == 'city_keep'
            same = row['value'] if row['kind'] == 'city_same' else None
        key = normalise(name)
        town = next((t for t in towns if t.state == state and t.key == key and (
            flag == CAPITAL or t.flag == CAPITAL or
            haversine(t.lat, t.lon, point.y, point.x).min() <= SAME_SEAT_KM)), None)
        if town is None:
            town = Town(name, state, flag)
            towns.append(town)
        town.flag = max(town.flag, flag)
        town.points.append(point)
        town.flags.append(flag)
        town.keep |= keep
        town.same = town.same or same
        if normalise(label) != key and (row is None or row['kind'] != 'city_relabel'):
            town.labels.append(display(label))
    unused = [k for k in rows if k not in used]
    if unused:
        raise SystemExit('city overrides that match no SoI point: {}'.format(unused))
    # The district of a town: that of its first HQ point (else of its capital point)
    owner, _ = locate(np.array([t.home for t in towns]), table.districts.geometry.values)
    for t, o in zip(towns, owner):
        t.district = table.districts.name[o]
    report('  SoI points (state capitals + district HQs):', len(points), '| towns:', len(towns))
    report.items('    skipped (region_overrides.csv)', skipped)
    corrected = ['{} -> {}'.format(r['name'], r['value']) for r in rows.values()
                 if r['kind'] in ('city_name', 'city_relabel')]
    report.items('    labels corrected (region_overrides.csv)', sorted(corrected))
    return towns


def match_towns(P, towns):
    """
    Index of the GeoNames place of each town (-1: none) and how it was found:
    1. the place named in region_overrides.csv (city_same), within SAME_PLACE_KM;
    2. same name within SAME_PLACE_KM (SAME_SEAT_KM if the place is a seat of
       government; also across a state border); the most important place
       first, then the nearest;
    3. an alternate name of the place within SAME_PLACE_KM;
    4. a seat of government of another name (e.g. a renamed town) within
       RENAMED_SEAT_KM, the nearest.
    """
    by_name, by_alt, seats = defaultdict(list), defaultdict(list), defaultdict(list)
    anywhere = defaultdict(list)
    for k, (key, st) in enumerate(zip(P.norm, P.state_code)):
        by_name[(st, key)].append(k)
        anywhere[key].append(k)
    for k, (alts, st) in enumerate(zip(P.alternatenames, P.state_code)):
        for a in ascii_alternates(alts):
            by_alt[(st, normalise(a))].append(k)
    for k in np.flatnonzero(P.fcode.isin(SEAT_CODES).values):
        seats[P.state_code[k]].append(k)
    lat, lon, fcode = P.lat.values, P.lon.values, P.fcode.values

    def best(town, cands, limit, by_rank=True):
        cands = sorted(set(cands))
        if not cands:
            return None
        km = np.array([haversine(town.lat, town.lon, lat[k], lon[k]).min() for k in cands])
        ok = [(FCODE_RANK.get(fcode[k], 5) if by_rank else 0, d, k)
              for k, d in zip(cands, km) if d <= limit(k)]
        return min(ok)[2:0:-1] if ok else None        # (place, km)

    found = []
    for t in towns:
        hit = None
        if t.same:
            hit = best(t, by_name[(t.state, normalise(t.same))], lambda k: SAME_SEAT_KM)
            how = 'city_same'
            if hit is None:
                raise SystemExit('city_same: no place {} near {}'.format(t.same, t.name))
        if hit is None:
            hit = best(t, by_name[(t.state, t.key)],
                       lambda k: SAME_SEAT_KM if fcode[k] in SEAT_CODES else SAME_PLACE_KM)
            if hit is None:                         # a place just across a state border
                hit = best(t, anywhere[t.key], lambda k: SAME_PLACE_KM)
            how = 'name'
        if hit is None and not t.keep:
            hit = best(t, by_alt[(t.state, t.key)], lambda k: SAME_PLACE_KM)
            how = 'alternate name'
        found.append((hit, how))
    for n, t in enumerate(towns):                   # renamed seats of government
        if found[n][0] is None and not t.keep:
            hit = best(t, seats[t.state], lambda k: RENAMED_SEAT_KM, by_rank=False)
            found[n] = (hit, 'seat of another name')
    return found


def merge_soi_hq(P, soi, table, overrides, state_code):
    """
    Add the SoI state capitals and district HQs to the GeoNames places
    (see :func:`soi_towns` and :func:`match_towns`); a town without a place
    is added. GeoNames national and state capitals (PPLC, PPLA) are capitals
    too, region_overrides.csv (city_hq) flags places such as Mahe as HQs and
    adds other spellings (city_alias).
    Columns added: hq, names (names of the place besides its own, e.g. its
    GeoNames name if the SoI name is shown) and labels (SoI spellings).
    """
    towns = soi_towns(soi, table, overrides, state_code)
    found = match_towns(P, towns)
    P['hq'] = np.where(P.fcode.isin(CAPITAL_CODES), CAPITAL, 0)
    P['names'] = [[] for _ in range(len(P))]
    P['labels'] = [[] for _ in range(len(P))]
    merged = defaultdict(list)
    added, lines = [], defaultdict(list)
    for t, (hit, how) in zip(towns, found):
        if hit is None:
            added.append(t)
            continue
        k, km = hit
        merged[k].append((t, how))
        if how != 'name':
            lines[how].append('{} -> {} {} {:.1f} km'.format(t.name, P.name[k], P.fcode[k], km))
    for k, group in merged.items():
        group.sort(key=lambda x: -x[0].flag)                # the capital first
        towns_k = [t for t, _ in group]
        gname = P.name[k]
        # The name shown: the GeoNames name if an SoI point has it; else the name
        # that is also the district's name; else the SoI name
        districts = {normalise(t.district) for t in towns_k}
        # (GeoNames alternate names are not shown: they are often other places)
        if any(how == 'name' for _, how in group):
            shown = gname
        else:
            shown = next((n for n in [gname] + [t.name for t in towns_k]
                          if normalise(n) in districts), towns_k[0].name)
        shown = display(shown)
        P.at[k, 'name'] = shown
        if P.state_code[k] != towns_k[0].state:
            lines['state'].append('{} {} -> {}'.format(shown, P.state_code[k], towns_k[0].state))
            P.at[k, 'state_code'] = towns_k[0].state
        P.at[k, 'hq'] = max([P.hq[k]] + [t.flag for t in towns_k])
        P.at[k, 'names'] = [n for n in [gname] + [t.name for t in towns_k]
                            if normalise(n) != normalise(shown)]
        P.at[k, 'labels'] = [l for t in towns_k for l in t.labels]
    rows = [dict(geonameid='', name=display(t.name), asciiname='', alternatenames='',
                 lat=t.home.y, lon=t.home.x, state_code=t.state, fcode='', town=False,
                 hq=t.flag, names=[], labels=list(t.labels)) for t in added]
    P = pd.concat([P, pd.DataFrame(rows)], ignore_index=True)
    # Places flagged in region_overrides.csv (city_hq)
    flagged = []
    for r in (r for r in overrides if r['kind'] == 'city_hq'):
        st = state_code[r['state']]
        cand = [k for k in np.flatnonzero((P.state_code == st).values)
                if normalise(P.name[k]) == normalise(r['name'])]
        if not cand:
            raise SystemExit('city_hq: no place {} in {}'.format(r['name'], r['state']))
        k = min(cand, key=lambda k: (FCODE_RANK.get(P.fcode[k], 5), k))
        P.at[k, 'hq'] = max(P.hq[k], HQ)
        flagged.append('{} ({})'.format(P.name[k], r['state']))
    # Other spellings given in region_overrides.csv (city_alias)
    for r in (r for r in overrides if r['kind'] == 'city_alias'):
        st = state_code[r['state']]
        cand = [k for k in np.flatnonzero((P.state_code == st).values & (P.hq > 0).values)
                if normalise(P.name[k]) == normalise(r['name'])]
        if len(cand) != 1:
            raise SystemExit('city_alias: no single HQ {} in {}'.format(r['name'], r['state']))
        P.at[cand[0], 'labels'] = P.labels[cand[0]] + [r['value']]
    P['norm'] = P.name.map(normalise)
    report('  towns merged with a GeoNames place:', len(towns) - len(added),
           '| by: name', sum(how == 'name' for h, how in found if h is not None),
           '| added as new places:', len(added))
    for how in ('city_same', 'alternate name', 'seat of another name'):
        report.items('    by ' + how, lines[how])
    report.items('    places across a state border, given the state of the HQ', lines['state'])
    report.items('    added', sorted(display(t.name) for t in added))
    report('    GeoNames capitals (PPLC, PPLA) flagged as capitals:',
           int(P.fcode.isin(CAPITAL_CODES).sum()))
    report.items('    flagged as HQ by region_overrides.csv', flagged)
    return P


def build_cities(geonames, soi, table, masks, overrides, state_code, hand_aliases):
    """
    Populated places sorted by normalised name: names, state and district
    (region ids), HQ flag, the cell on each grid, and aliases.
    """
    report.section('[7] Cities')
    P = geonames[(geonames.fclass == 'P') & ~geonames.fcode.isin(CITY_EXCLUDED_CODES)]
    report('  GeoNames populated places (class P without PPLQ/PPLH/PPLW):', len(P))
    P = P[['geonameid', 'asciiname', 'name', 'alternatenames', 'lat', 'lon', 'state_code',
           'fcode', 'population']].copy()
    # Towns, used only to rank places of the same name: TOWN_POPULATION people or
    # more, or an administrative centre (PPLA3 or higher) whatever its population
    population = pd.to_numeric(P.pop('population'), errors='coerce').fillna(0)
    P['town'] = (population >= TOWN_POPULATION).values | P.fcode.isin(TOWN_CODES).values
    report('  places ranked as towns ({:,}+ people, or PPLA3 and higher):'.format(TOWN_POPULATION),
           int(P.town.sum()))
    # Plain names: ASCII, title case for names in capitals
    P['name'] = [display(a if a else n) for a, n in zip(P.asciiname, P.name)]
    P['norm'] = P.name.map(normalise)
    P = merge_soi_hq(P.reset_index(drop=True), soi, table, overrides, state_code)
    # Exact duplicates: same name, state and position to 0.01 degree (HQ, else town kept)
    P = P.sort_values(['hq', 'town', 'geonameid'], ascending=[False, False, True],
                      kind='stable')
    duplicate = P.assign(rlat=P.lat.round(2), rlon=P.lon.round(2)).duplicated(
        subset=['norm', 'state_code', 'rlat', 'rlon'])
    report('  exact duplicates merged:', int(duplicate.sum()))
    P = P[~duplicate].reset_index(drop=True)
    # District: point-in-polygon (nearest if outside), within the state of the place
    points = shapely.points(P.lon.values, P.lat.values)
    rows = table.districts
    owner, outside = locate(points, rows.geometry.values)
    other_state = np.flatnonzero(rows.state_code.values[owner] != P.state_code.values)
    for k in other_state:
        same = np.flatnonzero(rows.state_code.values == P.state_code[k])
        owner[k] = same[int(np.argmin(shapely.distance(rows.geometry.values[same], points[k])))]
    report('  cities:', len(P), '| outside all districts (nearest used):', outside,
           '| district moved to the state of the place:', len(other_state))
    city = {'state': np.array([table.state_id[c] for c in P.state_code], np.uint8),
            'district': np.array([table.district_id[rows.row[o]] for o in owner], np.uint16),
            'hq': P.hq.values.astype(np.uint8), 'town': P.town.values.astype(np.uint8)}
    for key, grid in GRIDS.items():
        cells, status = DataCells(grid, masks[key]).city_cells(P.lon.values, P.lat.values)
        city['cell_' + key] = cells.astype(np.uint16)
        nodata = defaultdict(list)
        for k in np.flatnonzero(status == STATUS_NO_DATA):
            nodata[table.name[city['state'][k]]].append(P.name[k])
        report('  cities on {}: own cell {} | a cell next to it {} | no data, NaN {}'.format(
            key, int((status == STATUS_OWN).sum()), int((status == STATUS_NEIGHBOUR).sum()),
            int((status == STATUS_NO_DATA).sum())))
        for st, names in sorted(nodata.items()):
            report.items('    no data, ' + st, sorted(names))
    # GPM has no data mask: the r025 cell that contains the place
    i, j = DataCells(GRIDS['r025'], masks['r025']).containing(P.lon.values, P.lat.values)
    if not DataCells(GRIDS['r025'], masks['r025']).inside(i, j).all():
        raise SystemExit('cities outside the 0.25 degree grid')
    city['cell_gpm'] = (i * GRIDS['r025'].nlat + j).astype(np.uint16)
    order = np.lexsort((-P.hq.values, P.norm.values))
    P = P.iloc[order].reset_index(drop=True)
    city = {k: v[order] for k, v in city.items()}
    city['names'], city['keys'] = list(P.name), list(P.norm)
    city['aliases'] = city_aliases(P)
    hand = hand_city_aliases([r for r in hand_aliases if r['type'] == 'city'], city, table.name)
    city['aliases'] = sorted(city['aliases'] + hand)
    report('  city aliases from region_aliases.csv:', len(hand))
    report('  check: places that have these old names (HQs/capitals, then towns):')
    for name in ('Bombay', 'Gurgaon', 'Calcutta', 'Madras', 'Bangalore', 'Poona', 'Trivandrum',
                 'Allahabad', 'Faizabad', 'Aurangabad', 'Rajahmundry', 'Berhampur', 'Hubli'):
        key = normalise(name)
        hits = [k for alias_key, k, _, _ in city['aliases'] if alias_key == key]
        report('    {}: HQ/capital {} | town {}'.format(name, *(
            ', '.join('{} ({})'.format(P.name[k], table.name[city['state'][k]])
                      for k in hits if want(k)) or 'none'
            for want in (lambda k: P.hq[k], lambda k: not P.hq[k] and P.town[k]))))
    return city


def city_aliases(P):
    """
    [(key, city, alias as shown, rank)], sorted: rank 1 for other names of
    the place (e.g. its GeoNames name when the SoI name is shown), rank 0
    for GeoNames alternate names and SoI spellings. All places keep all
    their aliases; when a name fits several places, the order of preference
    of imdlib.regions (_city_level) decides at run time.
    """
    out = []
    for k, (alts, own, names, labels) in enumerate(zip(
            P.alternatenames, P.norm, P.names, P.labels)):
        seen = {own}
        for text, rank in [(n, 1) for n in names] + [(a, 0) for a in ascii_alternates(alts)] + \
                [(lab, 0) for lab in labels]:
            key = normalise(text)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append((key, k, display(text), rank))
    out.sort()
    report('  city aliases:', len(out))
    return out


def hand_city_aliases(rows, city, region_name):
    """
    [(key, city, alias as shown, rank)] of the city rows of region_aliases.csv.

    A row names an existing place by its name and state, and by its district
    if the state is not enough; it must fit exactly one place. The alias ranks
    as an old or other name of the place (like a GeoNames alternate name).
    ``city`` holds the sorted ``keys`` and ``names`` of the places, their
    ``state`` and ``district`` region ids and their ``aliases``.
    """
    known = {(key, k) for key, k, _, _ in city['aliases']}
    out = []
    for r in rows:
        key = normalise(r['name'])
        span = range(bisect.bisect_left(city['keys'], key),
                     bisect.bisect_right(city['keys'], key))
        found = [k for k in span
                 if normalise(region_name[city['state'][k]]) == normalise(r['state'])
                 and (not r.get('district') or
                      normalise(region_name[city['district'][k]]) == normalise(r['district']))]
        where = 'region_aliases.csv: city {!r} in {}'.format(
            r['name'], ', '.join(v for v in (r.get('district'), r['state']) if v))
        if not found:
            raise SystemExit('{} fits no place. Check the name, state and district (see '
                             'imd.regions.search({!r})).'.format(where, r['name']))
        if len(found) > 1:
            raise SystemExit('{} fits {} places: {}. Add or correct the district.'.format(
                where, len(found), '; '.join('{} ({}, {})'.format(
                    city['names'][k], region_name[city['district'][k]],
                    region_name[city['state'][k]]) for k in found)))
        k, alias = found[0], normalise(r['alias'])
        if alias == key or (alias, k) in known:
            report('  region_aliases.csv: {!r} is already a name of {}'.format(r['alias'],
                                                                                r['name']))
            continue
        known.add((alias, k))
        out.append((alias, k, display(r['alias']), 0))
    return out


###############################################################################
# 8. Outputs
###############################################################################

def blob(strings):
    """Strings as one UTF-8 byte array, separated by newlines."""
    strings = list(strings)
    if any('\n' in s for s in strings):
        raise SystemExit('a name contains a newline')
    return np.frombuffer('\n'.join(strings).encode('utf-8'), np.uint8)


def write_regions(path, table, keys, splits, newer, not_district, weights):
    arrays = dict(
        reg_type=np.array(table.type, np.uint8),
        reg_name=np.array(table.name),
        reg_parent=np.array(table.parent, np.int16),
        reg_code=np.array(table.code),
        key_type=np.array([x[0] for x in keys], np.uint8),
        key_text=np.array([x[1] for x in keys]),
        key_region=np.array([x[2] for x in keys], np.int32),
        key_alias=np.array([x[3] for x in keys]),
        split_state=np.array([x[0] for x in splits], np.int16),
        split_key=np.array([x[1] for x in splits]),
        split_text=np.array([x[2] for x in splits]),
        split_ptr=np.cumsum([0] + [len(x[3]) for x in splits]).astype(np.int32),
        split_region=np.array([k for x in splits for k in x[3]], np.int16),
        orphan_name=np.array([o['name'] for o in newer]),
        orphan_key=np.array([normalise(o['name']) for o in newer]),
        orphan_state=np.array([table.state_id[o['state']] for o in newer], np.int16),
        orphan_parent=np.array([table.district_id[o['parent']] for o in newer], np.int16),
        orphan_code=np.array([str(o['code']) for o in newer]),
        notdist_name=np.array([x[0] for x in not_district]),
        notdist_key=np.array([normalise(x[0]) for x in not_district]),
        notdist_state=np.array([x[1] for x in not_district], np.int16),
        notdist_part=np.array([x[2] for x in not_district]),
        **weights)
    if any(v.dtype.kind == 'O' for v in arrays.values()):
        raise SystemExit('object arrays cannot be loaded without pickle')
    np.savez_compressed(path, **arrays)


def write_cities(path, city):
    """
    cities.npz: names as one UTF-8 blob (newline-separated) in key order;
    the normalised key of a name is stored only where it is not the name in
    lower case (xkey_idx, xkey); the r025 cell only where it is not the cell
    that contains the place (cell_gpm).
    """
    names, keys = city['names'], city['keys']
    if keys != sorted(keys):
        raise SystemExit('city keys are not sorted')
    other = [k for k, (n, key) in enumerate(zip(names, keys)) if n.lower() != key]
    moved = np.flatnonzero(city['cell_r025'] != city['cell_gpm'])
    aliases = city['aliases']
    np.savez_compressed(
        path, name=blob(names),
        xkey_idx=np.array(other, np.int32), xkey=blob(keys[k] for k in other),
        state=city['state'], district=city['district'], hq=city['hq'], town=city['town'],
        cell_gpm=city['cell_gpm'], cell_t100=city['cell_t100'], cell_t050=city['cell_t050'],
        r025_idx=moved.astype(np.int32), r025_cell=city['cell_r025'][moved],
        alt_key=blob(a[0] for a in aliases), alt_text=blob(a[2] for a in aliases),
        alt_city=np.array([a[1] for a in aliases], np.int32),
        alt_rank=np.array([a[3] for a in aliases], np.uint8))
    report('  city names stored with a separate key:', len(other),
           '| r025 cells other than the containing cell:', len(moved))


def write_meta(path, args, soi, lgd, geonames, table, keys, newer, city):
    gis = os.path.abspath(args.gis)
    inputs = [file_info(z, gis) for z in soi.zips] + [file_info(f, gis) for f in lgd.files]
    for shp in (args.basins, args.subbasins):
        inputs += [file_info(os.path.splitext(shp)[0] + ext, gis)
                   for ext in ('.shp', '.dbf', '.shx', '.prj')]
    inputs.append(file_info(args.geonames, gis))
    inputs += [file_info(f, REPO) for f in (args.aliases, args.overrides)]
    samples = {'r025': args.rain_sample, 't100': args.tmax_sample, 't050': args.rt_tmax_sample}
    # No build date and no sample file names: the same inputs give the same file on any day
    meta = {
        'script_version': SCRIPT_VERSION,
        'sources': {
            'boundaries': {'description': 'Survey of India state and district boundaries',
                           'date': max(dbf_date(z) for z in soi.zips)},
            'names': {'description': 'Local Government Directory (lgdirectory.gov.in), '
                                     'state and district lists',
                      'date': lgd_date(lgd.files)},
            'basins': {'description': 'Central Water Commission (CWC) basins and sub-basins',
                       'date': max(dbf_date(os.path.splitext(f)[0] + '.dbf')
                                   for f in (args.basins, args.subbasins))},
            'places': {'description': 'GeoNames (geonames.org, CC BY 4.0), populated places '
                                      'and alternate names',
                       'date': max(geonames.modified)},
        },
        'counts': {'states': len(table.ids(STATE)), 'districts': len(table.ids(DISTRICT)),
                   'basins': len(table.ids(BASIN)), 'subbasins': len(table.ids(SUBBASIN)),
                   'districts_without_shape': len(newer), 'cities': len(city['names']),
                   'aliases': sum(1 for k in keys if k[3]), 'city_aliases': len(city['aliases'])},
        'grids': {k: g._asdict() for k, g in GRIDS.items()},
        'gpm_offset': list(GPM_OFFSET),
        'inputs': inputs,
        'data_masks': {grid: {'sha256': sha256(path)} for grid, path in samples.items() if path},
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=1, ensure_ascii=False)
        f.write('\n')


###############################################################################
# Main
###############################################################################

def parse_args():
    p = argparse.ArgumentParser(description='Build the region data of imdlib.')
    p.add_argument('--gis', required=True, help='folder with the sources (see REGIONS_DATA_UPDATE.md)')
    p.add_argument('--districts', help='SoI zips (default GIS/DISTRICTS)')
    p.add_argument('--lgd', help='LGD xlsx folder (default GIS/lgdirectory_gov_in)')
    p.add_argument('--basins', help='default GIS/CWC_Basin/BASIN_CWC.shp')
    p.add_argument('--subbasins', help='default GIS/CWC_Sub_Basin/CWCSUBBASIN.shp')
    p.add_argument('--geonames', help='IN.zip or IN.txt (default GIS/GeoNames/IN.zip)')
    p.add_argument('--aliases', default=os.path.join(HERE, 'region_aliases.csv'))
    p.add_argument('--overrides', default=os.path.join(HERE, 'region_overrides.csv'))
    p.add_argument('--rain-sample', required=True, help='archive rain file of one year')
    p.add_argument('--tmax-sample', required=True, help='archive tmax file of one year')
    p.add_argument('--rt-tmax-sample', required=True, help='real-time tmax file of one day')
    p.add_argument('--out', default=os.path.join(REPO, 'imdlib', 'data', 'regions'))
    p.add_argument('--workers', type=int, default=max(1, min(16, os.cpu_count() or 1)))
    args = p.parse_args()
    args.districts = args.districts or os.path.join(args.gis, 'DISTRICTS')
    args.lgd = args.lgd or os.path.join(args.gis, 'lgdirectory_gov_in')
    args.basins = args.basins or os.path.join(args.gis, 'CWC_Basin', 'BASIN_CWC.shp')
    args.subbasins = args.subbasins or os.path.join(args.gis, 'CWC_Sub_Basin', 'CWCSUBBASIN.shp')
    args.geonames = args.geonames or os.path.join(args.gis, 'GeoNames', 'IN.zip')
    return args


def main():
    args = parse_args()
    started = datetime.datetime.now(datetime.timezone.utc)
    # The build date is in the report only, not in the outputs
    report('IMDLIB region build, script version', SCRIPT_VERSION, '| built',
           started.strftime('%Y-%m-%d %H:%M UTC'))
    overrides = read_csv_rows(args.overrides)
    hand_aliases = read_csv_rows(args.aliases)
    check_hand_aliases(hand_aliases)

    soi = read_soi(args.districts)
    lgd = read_lgd(args.lgd)
    state_name = {int(c): display(n) for c, n in zip(lgd.states.code, lgd.states.name)}
    if set(state_name) != set(soi.states.folder):
        raise SystemExit('SoI and LGD state codes differ')
    districts = join_lgd(soi.districts, lgd, state_name, overrides)
    geonames = read_geonames(args.geonames, soi.states)
    newer = newer_districts(districts, lgd, state_name, geonames, soi, overrides)
    cwc = read_cwc(args.basins, args.subbasins)

    table = RegionTable(soi.states, districts, state_name, cwc)
    report('\n  regions: states', len(table.ids(STATE)), '| districts', len(table.ids(DISTRICT)),
           '| basins', len(table.ids(BASIN)), '| sub-basins', len(table.ids(SUBBASIN)),
           '| total', len(table))
    for t in (STATE, BASIN, SUBBASIN):
        names = [table.name[k] for k in table.ids(t)]
        if len(set(names)) != len(names):
            raise SystemExit('duplicate {} names'.format(TYPES[t]))
    repeated = Counter(table.name[k] for k in table.ids(DISTRICT))
    report.items('  district names in more than one state', [
        '{} ({})'.format(n, ', '.join(table.name[table.parent[k]] for k in table.ids(DISTRICT)
                                      if table.name[k] == n))
        for n, c in sorted(repeated.items()) if c > 1])

    report.section('[5] Names and aliases')
    aliases = collect_aliases(table, lgd, cwc, geonames, hand_aliases)
    keys, splits = build_name_index(table, aliases, overrides)
    not_district = not_districts(table, overrides)
    report.section('[6] Weights')
    masks = data_masks(args)
    weights = build_weights(table, masks, args.workers)
    state_code = {n: c for c, n in state_name.items()}
    city = build_cities(geonames, soi, table, masks, overrides, state_code, hand_aliases)

    report.section('[8] Output')
    os.makedirs(args.out, exist_ok=True)
    write_regions(os.path.join(args.out, 'regions.npz'), table, keys, splits, newer,
                  not_district, weights)
    write_cities(os.path.join(args.out, 'cities.npz'), city)
    write_meta(os.path.join(args.out, 'meta.json'), args, soi, lgd, geonames, table, keys, newer,
               city)
    for path in [os.path.join(args.out, f) for f in ('meta.json', 'regions.npz', 'cities.npz')]:
        report('  {}: {:,} bytes'.format(os.path.relpath(path, REPO), os.path.getsize(path)))
    report('Done in {:.0f} s'.format(
        (datetime.datetime.now(datetime.timezone.utc) - started).total_seconds()))


if __name__ == '__main__':
    main()
