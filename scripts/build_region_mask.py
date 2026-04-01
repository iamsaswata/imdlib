"""
build_region_mask.py
====================
Builds IMD region classification masks (plains / hilly / coastal / ocean)
for use with imdlib heatwave and cold wave indices.

Data sources:
    - Elevation: ETOPO1 (NOAA), 1-arc-minute (~1.8 km) global relief
    - Coastline: Natural Earth 1:10m coastline shapefile

Classification rules (based on IMD operational practice):
    - Hilly:   mean grid-cell elevation >= 1000 m
    - Coastal:  land centroid of grid cell <= 50 km from nearest coastline
    - Plains:   everything else with >= 50% land coverage
    - Ocean:    cells with < 50% land pixels

Mask values:
    -1 = Ocean / no data
     0 = Plains
     1 = Hilly
     2 = Coastal

Outputs:
    region_mask_100.npy  — 1.0° grid (31x31), for tmin/tmax data
    region_mask_025.npy  — 0.25° grid (135x129), for rainfall data

Usage:
    pip install numpy xarray geopandas scipy
    python build_region_mask.py

References:
    - Elevation threshold 500m: Mandal et al. (2022), Sci. Reports, doi:10.1038/s41598-022-24065-0
    - Coastal distance 50km: same source
    - IMD cold/heat wave FAQ: https://mausam.imd.gov.in/pdfs/heatcolduser/faq.pdf

Author: Built for imdlib (https://github.com/iamsaswata/imdlib)
"""

import numpy as np
import xarray as xr
import geopandas as gpd
from scipy.spatial import cKDTree
import os
import time
import urllib.request
import zipfile


# ====================================================================
# Configuration
# ====================================================================
ELEV_THRESHOLD = 1000.0     # meters — grid cells above this are "hilly"
COAST_DISTANCE = 50.0       # km — grid cells within this are "coastal"

# IMD grid definitions (must match imdlib exactly)
LAT_TEMP = np.linspace(7.5, 37.5, 31)     # 1.0° temperature grid
LON_TEMP = np.linspace(67.5, 97.5, 31)
LAT_RAIN = np.linspace(6.5, 38.5, 129)    # 0.25° rainfall grid
LON_RAIN = np.linspace(66.5, 100.0, 135)

# Output filenames
OUTPUT_TEMP = "region_mask_100.npy"
OUTPUT_RAIN = "region_mask_025.npy"

# Data download URLs
ETOPO_URL = ("https://coastwatch.pfeg.noaa.gov/erddap/griddap/etopo180.nc"
             "?altitude[(5.0):1:(40.0)][(65.0):1:(101.0)]")
COASTLINE_URL = "https://naciscdn.org/naturalearth/10m/physical/ne_10m_coastline.zip"


# ====================================================================
# Data download helpers
# ====================================================================
def download_etopo(filepath="etopo_india.nc"):
    """Download ETOPO1 elevation data for India region from NOAA ERDDAP."""
    if os.path.exists(filepath):
        print(f"  ETOPO already exists: {filepath}")
        return filepath
    print(f"  Downloading ETOPO1 from NOAA ERDDAP...")
    urllib.request.urlretrieve(ETOPO_URL, filepath)
    print(f"  Saved: {filepath} ({os.path.getsize(filepath)} bytes)")
    return filepath


def download_coastline(dirpath="coastline"):
    """Download Natural Earth 1:10m coastline shapefile."""
    shp_path = os.path.join(dirpath, "ne_10m_coastline.shp")
    if os.path.exists(shp_path):
        print(f"  Coastline already exists: {shp_path}")
        return shp_path
    print(f"  Downloading Natural Earth 10m coastline...")
    zip_path = "ne_10m_coastline.zip"
    urllib.request.urlretrieve(COASTLINE_URL, zip_path)
    os.makedirs(dirpath, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(dirpath)
    os.remove(zip_path)
    print(f"  Saved: {shp_path}")
    return shp_path


# ====================================================================
# Core functions
# ====================================================================
def haversine_km(lat1, lon1, lat2, lon2):
    """Haversine distance in km between two points."""
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return R * 2 * np.arcsin(np.sqrt(a))


def build_coast_tree(shp_path, bbox=(63, 4, 103, 42)):
    """
    Build a KDTree of coastline vertices for fast nearest-neighbor queries.

    Parameters
    ----------
    shp_path : str — path to Natural Earth coastline shapefile
    bbox : tuple — (lon_min, lat_min, lon_max, lat_max) filter region

    Returns
    -------
    tree : cKDTree — scaled coordinate tree for approximate NN search
    points : ndarray — (N, 2) array of (lon, lat) coastline vertices
    scale_lon : float — longitude scaling factor used
    """
    coast_gdf = gpd.read_file(shp_path)
    points = []
    for geom in coast_gdf.geometry:
        if geom is None:
            continue
        b = geom.bounds  # (minx, miny, maxx, maxy)
        if b[2] < bbox[0] or b[0] > bbox[2] or b[3] < bbox[1] or b[1] > bbox[3]:
            continue
        if geom.geom_type == 'LineString':
            points.extend(list(geom.coords))
        elif geom.geom_type == 'MultiLineString':
            for line in geom.geoms:
                points.extend(list(line.coords))
    points = np.array(points)  # (N, 2) = (lon, lat)

    # Scale to approximate km for KDTree
    mean_lat = 22.0  # roughly center of India
    scale_lon = np.cos(np.radians(mean_lat))
    scaled = np.column_stack([
        points[:, 1] * 111.0,              # lat -> km
        points[:, 0] * 111.0 * scale_lon   # lon -> km
    ])
    tree = cKDTree(scaled)
    return tree, points, scale_lon


def classify_grid(lat_array, lon_array, resolution,
                  dem_lat, dem_lon, dem_elev,
                  coast_tree, coast_points, scale_lon,
                  elev_thresh=ELEV_THRESHOLD,
                  coast_dist_km=COAST_DISTANCE):
    """
    Classify each IMD grid cell as plains / hilly / coastal / ocean.

    Works for ANY lat/lon grid — the same function produces both
    the 1.0° and 0.25° masks, ensuring spatial consistency.

    For each cell:
      1. Extract all ETOPO1 pixels within cell bounds
      2. If no land pixels (elevation > -100m) exist → ocean
      3. Compute mean elevation of land pixels
      4. Compute Haversine distance from cell center to nearest coastline
      5. Classify: elevation ≥ threshold → hilly;
                   coast distance ≤ threshold → coastal; else → plains

    Parameters
    ----------
    lat_array : 1D array — cell-center latitudes
    lon_array : 1D array — cell-center longitudes
    resolution : float — cell size in degrees
    dem_* : ETOPO1 elevation data arrays
    coast_* : coastline KDTree and vertices from build_coast_tree()
    elev_thresh : float — elevation threshold for "hilly" (meters)
    coast_dist_km : float — distance threshold for "coastal" (km)

    Returns
    -------
    mask : int8 array, shape (len(lon_array), len(lat_array))
           Values: -1=ocean, 0=plains, 1=hilly, 2=coastal
           Axis order matches imdlib: (lon_index, lat_index)
    """
    half = resolution / 2.0
    n_lon = len(lon_array)
    n_lat = len(lat_array)

    mask = np.full((n_lon, n_lat), -1, dtype=np.int8)

    for i, lon_c in enumerate(lon_array):
        for j, lat_c in enumerate(lat_array):
            # Extract DEM pixels within cell bounds
            lat_sel = (dem_lat >= lat_c - half) & (dem_lat < lat_c + half)
            lon_sel = (dem_lon >= lon_c - half) & (dem_lon < lon_c + half)
            cell = dem_elev[np.ix_(lat_sel, lon_sel)]

            if cell.size == 0:
                continue

            # Only consider land pixels (elevation > -100m)
            land_pixels = cell[cell > -100]
            if land_pixels.size == 0:
                continue  # pure ocean cell

            mean_elev = np.mean(land_pixels)

            # Distance from cell center to nearest coastline
            query = np.array([[lat_c * 111.0, lon_c * 111.0 * scale_lon]])
            _, idx = coast_tree.query(query, k=1)
            dist_km = haversine_km(lat_c, lon_c,
                                   coast_points[idx[0], 1],
                                   coast_points[idx[0], 0])

            # Classify
            if mean_elev >= elev_thresh:
                mask[i, j] = 1   # hilly
            elif dist_km <= coast_dist_km:
                mask[i, j] = 2   # coastal
            else:
                mask[i, j] = 0   # plains

    return mask


# ====================================================================
# Main
# ====================================================================
def main():
    print("=" * 60)
    print("Building IMD Region Classification Masks")
    print(f"  Hilly threshold:   {ELEV_THRESHOLD}m")
    print(f"  Coastal distance:  {COAST_DISTANCE}km")
    print("=" * 60)

    # Download data
    print("\n[1] Downloading source data...")
    etopo_path = download_etopo()
    coast_path = download_coastline()

    # Load
    print("\n[2] Loading data...")
    ds = xr.open_dataset(etopo_path)
    dem_lat = ds['latitude'].values
    dem_lon = ds['longitude'].values
    dem_elev = ds['altitude'].values
    ds.close()
    print(f"  ETOPO1: {dem_elev.shape}, ~{dem_lat[1]-dem_lat[0]:.4f}° resolution")

    tree, points, scale_lon = build_coast_tree(coast_path)
    print(f"  Coastline: {len(points)} vertices in India region")

    # Build masks
    print(f"\n[3] Classifying 1.0° temperature grid ({len(LON_TEMP)}x{len(LAT_TEMP)})...")
    t0 = time.time()
    mask_temp = classify_grid(
        LAT_TEMP, LON_TEMP, 1.0,
        dem_lat, dem_lon, dem_elev, tree, points, scale_lon
    )
    print(f"  Done in {time.time()-t0:.1f}s")

    print(f"\n[4] Classifying 0.25° rain grid ({len(LON_RAIN)}x{len(LAT_RAIN)})...")
    t0 = time.time()
    mask_rain = classify_grid(
        LAT_RAIN, LON_RAIN, LAT_RAIN[1] - LAT_RAIN[0],
        dem_lat, dem_lon, dem_elev, tree, points, scale_lon
    )
    print(f"  Done in {time.time()-t0:.1f}s")

    # Save
    print(f"\n[5] Saving...")
    np.save(OUTPUT_TEMP, mask_temp)
    np.save(OUTPUT_RAIN, mask_rain)

    for name, m in [(OUTPUT_TEMP, mask_temp), (OUTPUT_RAIN, mask_rain)]:
        land = m >= 0
        print(f"\n  {name}: shape={m.shape}, {os.path.getsize(name)} bytes")
        print(f"    Ocean={np.sum(m==-1)}, Plains={np.sum(m==0)}, "
              f"Hilly={np.sum(m==1)}, Coastal={np.sum(m==2)}")

    print("\nDone!")


if __name__ == "__main__":
    main()
