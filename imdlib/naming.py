# Variable metadata for IMD gridded data and computed indices.
#
# RAW_METADATA: attributes for raw input variables (rain, tmin, tmax, rain_gpm).
# VAR_METADATA: attributes for computed indices, keyed by method string.

RAW_METADATA = {
    'rain':     {'var_name': 'rain',     'units': 'mm/day', 'long_name': 'Rainfall'},
    'rain_gpm': {'var_name': 'rain_gpm', 'units': 'mm/day', 'long_name': 'GPM Merged Rainfall'},
    'tmin':     {'var_name': 'tmin',     'units': 'C',      'long_name': 'Minimum Temperature'},
    'tmax':     {'var_name': 'tmax',     'units': 'C',      'long_name': 'Maximum Temperature'},
}

VAR_METADATA = {
    # Annual compute indices (scale='A')
    'cdd':    {'var_name': 'cdd',    'units': 'Days',    'long_name': 'Consecutive dry days'},
    'cwd':    {'var_name': 'cwd',    'units': 'Days',    'long_name': 'Consecutive wet days'},
    'dr':     {'var_name': 'dr',     'units': 'Days',    'long_name': 'Rainy days'},
    'dtr':    {'var_name': 'dtr',    'units': 'C',       'long_name': 'Diurnal Temperature Range'},
    'd64':    {'var_name': 'd64',    'units': 'Days',    'long_name': 'Heavy precipitation days'},
    'mmk_hr': {'var_name': 'mmk_hr', 'units': 'Z',       'long_name': 'Modified Mann-Kendall statistics (Z)'},
    'mnadt':  {'var_name': 'mnadt',  'units': 'C',       'long_name': 'Min. Annual Daily Tmin (Coolest Night)'},
    'mxadt':  {'var_name': 'mxadt',  'units': 'C',       'long_name': 'Max. Annual Daily Tmax (Hottest Day)'},
    'pci':    {'var_name': 'pci',    'units': '',        'long_name': 'Precipitation concentration index'},
    'rtwd':   {'var_name': 'rtwd',   'units': 'mm/year', 'long_name': 'Total precipitation in wet days'},
    'rx5d':   {'var_name': 'rx5d',   'units': 'mm/year', 'long_name': 'Maximum 5 days rainfall'},
    'rxa':    {'var_name': 'rxa',    'units': 'mm',      'long_name': 'Maximum daily annual rainfall'},
    'sdii':   {'var_name': 'sdii',   'units': 'mm/day',   'long_name': 'Simple daily intensity index'},
    'spr':    {'var_name': 'spr',    'units': 'Zsr',     'long_name': "Spearman's Rho statistics (Zsr)"},
    'sse':    {'var_name': 'sse',    'units': 'Slope',   'long_name': "Sen's slope estimates"},
    'sstr':   {'var_name': 'sstr',   'units': '%',       'long_name': 'Magnitude of trend'},
    # Drought indices (scale='M')
    'spi':    {'var_name': 'spi',    'units': '',         'long_name': 'Standardized Precipitation Index'},
    'spei':   {'var_name': 'spei',   'units': '',         'long_name': 'Standardized Precipitation Evapotranspiration Index'},
}
