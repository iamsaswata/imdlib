def pytest_configure(config):
    config.addinivalue_line(
        'markers', 'slow: uses real IMD files and region sources (see test_regions_real.py)')
