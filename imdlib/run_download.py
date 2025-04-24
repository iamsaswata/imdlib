from core import get_data

data = get_data("tmin", 2000, 2003, fn_format='yearwise', file_dir="data")
print("Download done.")