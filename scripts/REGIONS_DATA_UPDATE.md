# Updating the region data

`scripts/build_regions.py` builds the data used by `region()` and `imdlib.regions`:

- `imdlib/data/regions/regions.npz`
- `imdlib/data/regions/cities.npz`
- `imdlib/data/regions/meta.json`

To update a source, replace its files and run the build again. No code change is needed.

The source files of the current data are in the private repository
`iamsaswata/imdlib-region-sources`, tag `regions-2026-10` (ask the maintainer for access).
Its README has the update steps with exact commands, including tagging each build.

## 1. Install the build dependencies

They are needed only for the build, not by imdlib users:

    pip install geopandas shapely pyproj pyogrio openpyxl scipy

## 2. Download the sources

Put them in one folder (here `GIS/`) with this layout:

    GIS/
      DISTRICTS/                 Survey of India (SoI) boundaries: one zip per state
        ANDHRA PRADESH.zip
        ...
      lgdirectory_gov_in/        Local Government Directory (LGD) lists
        All_Stateof_India_<date>.xlsx
        All_Districtof_India_<date>.xlsx
      CWC_Basin/                 Central Water Commission (CWC) basins
        BASIN_CWC.shp, .dbf, .shx, .prj
      CWC_Sub_Basin/             CWC sub-basins
        CWCSUBBASIN.shp, .dbf, .shx, .prj
      GeoNames/
        IN.zip

- **SoI district boundaries** (Survey of India Online Maps Portal,
  https://onlinemaps.surveyofindia.gov.in): the district boundary zip of every
  state and union territory (36 zips). Keep the zips as downloaded; the script
  reads them in place. Each zip has a folder named after the LGD state code,
  with the `*STATE_BDY`, `*DISTRICT_BDY`, `*DISTRICT_HQ` and `*STATE_HQ`
  shapefiles.
- **LGD lists** (https://lgdirectory.gov.in): the "All States of India" and
  "All Districts of India" downloads in Excel format. Keep the file names
  (`All_Stateof_India_<date>.xlsx`, `All_Districtof_India_<date>.xlsx`); the
  newest file of each kind is used and its date goes into `meta.json`.
- **CWC basins and sub-basins** (India-WRIS, https://indiawris.gov.in): the
  basin and sub-basin shapefiles, `BASIN_CWC.shp` and `CWCSUBBASIN.shp`, each
  with its `.dbf`, `.shx` and `.prj` files.
- **GeoNames** (CC BY 4.0): `IN.zip` from
  https://download.geonames.org/export/dump/.

The hand-kept files `scripts/region_aliases.csv` and
`scripts/region_overrides.csv` are part of the repository. Their headers
explain each kind of row. After a source update, the build may stop with a
message about a row that no longer matches; fix or remove that row.

To add a name that the sources miss (for example a local name of a town that
a friend told you), add a row to `scripts/region_aliases.csv` and run the
build. A row gives the type, the official name, the state (for districts and
cities) and the new name; for a city whose name exists more than once in its
state, also give the district:

    city,Prayagraj,Uttar Pradesh,,<new name>,<where the name comes from>

The build stops with a message if a city row fits no place or several.

## 3. Get the sample IMD files

The data masks (which grid cells have data) come from three IMD files. Take
them from an imdlib cache (`imd.cache.info()` shows where it is). The same
cache also serves the real-data tests of step 6, which need a few real-time
days of rain and of tmax:

    import imdlib as imd
    imd.load('rain', 2025)                                       # archive/rain/2025.grd
    imd.load('tmax', 2025)                                       # archive/tmax/2025.grd
    imd.load('rain', '2026-10-03', '2026-10-05', source='realtime')
    imd.load('tmax', '2026-10-03', '2026-10-04', source='realtime')

Use a whole year for the archive files; any one real-time tmax day works for
the build (here `realtime/tmax/2026-10-03.grd`). Use recent dates: real-time
data covers only the last few years. `meta.json` records the SHA-256 of each
sample file, not its name or path.

## 4. Run the build

From the repository root:

    python scripts/build_regions.py --gis GIS \
        --rain-sample <cache>/archive/rain/2025.grd \
        --tmax-sample <cache>/archive/tmax/2025.grd \
        --rt-tmax-sample <cache>/realtime/tmax/2026-10-03.grd \
        > build_report.txt

`--gis` is the folder of step 2; it has no default. The build takes a few
minutes. Run it twice: the second run must give byte-identical output files
(`git status` shows no further change). The outputs hold no build date, so a
rebuild from the same inputs gives the same files on any day; the date is
printed at the top of the report.

## 5. Check the report

- The exit code is 0. A region without any grid cell prints
  `REGIONS WITHOUT CELLS` and exits with 1.
- `[2] LGD join`: nearly all SoI districts join to LGD; read the
  `SoI without LGD` and `name-step matches` lists for wrong pairs.
- `[3] LGD districts without a shape`: each newer district has a sensible
  parent district.
- `[5] Names and aliases`: read `aliases dropped` and
  `names of districts before a split`.
- `[6] Weights` and `[7] Cities`: the `no data, NaN` lists should hold only
  island territories and places at the edge of a grid.
- `[7] Cities`, `check: places that have these old names`: Bombay, Madras,
  Calcutta, ... still point to their HQs, and Rajahmundry, Berhampur and
  Hubli to their towns.
- Compare the counts with the previous `meta.json` (`git diff`); large
  changes need a reason.

## 6. Run the tests

    pytest test

With the real IMD files and the sources, the slow tests also run (use a
temporary folder on a fast local disk):

    IMDLIB_TEST_CACHE=<cache> IMDLIB_TEST_GIS=GIS pytest test --basetemp /tmp/imdlib-tests

`IMDLIB_TEST_CACHE` is the cache of step 3, with `archive/rain/2025.grd`,
`archive/tmax/2025.grd` and a few real-time rain and tmax days; see
`test/test_regions_real.py`.

The tests name some places explicitly (for example in
`test/test_regions.py`, `test_shipped_*`). If a source update renames,
splits or adds such a place, a test fails and its message shows the new
situation. Check in the report that the change is real (a `diff` with the
report of the previous build shows what changed), then update the test
example to the new name.

## 7. Commit

Commit the three output files listed at the top, plus any change to
`scripts/region_aliases.csv` or `scripts/region_overrides.csv`. The sources and
the report are not committed.
