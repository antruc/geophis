"""Fixtures y montajes compartidos por los tests de geophis.

Los dos constructores de abajo (`_gdf_box`, `_curvas_rampa`) no son fixtures:
son funciones normales que `test_raster.py` y `test_barridos.py` importan
(`from conftest import ...`). Estan aqui y no duplicadas en cada archivo porque
al partir los tests de barridos y metrologia fuera de `test_raster.py` eran los
unicos montajes que los dos lados seguian necesitando.
"""

import geopandas as gpd
import pytest
from shapely.geometry import LineString, box

UTM = 32613  # UTM 13N, metros


def _gdf_box(minx, miny, maxx, maxy):
    return gpd.GeoDataFrame(geometry=[box(minx, miny, maxx, maxy)], crs=UTM)


def _curvas_rampa():
    """Curvas horizontales E-W en y=0,10,20,30 con cota = y (rampa hacia el N)."""
    lineas = [LineString([(0, y), (30, y)]) for y in (0, 10, 20, 30)]
    return gpd.GeoDataFrame({"cota": [0, 10, 20, 30]}, geometry=lineas, crs=UTM)


@pytest.fixture
def cuadrado():
    """Fábrica de cuadrados: cuadrado(x=0, y=0, lado=100)."""

    def _hacer(x=0, y=0, lado=100):
        return box(x, y, x + lado, y + lado)

    return _hacer


@pytest.fixture
def gdf():
    """Fábrica de GeoDataFrames: gdf(*geoms, crs=UTM, **columnas)."""

    def _hacer(*geoms, crs=UTM, **cols):
        datos = {"geometry": list(geoms)}
        datos.update(cols)
        return gpd.GeoDataFrame(datos, crs=crs)

    return _hacer
