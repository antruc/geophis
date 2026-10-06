"""`zonificar`: raster continuo -> poligonos por rangos.

Un raster sintetico con la respuesta contada a mano: columnas 0-4 en la clase
baja, 5-9 en la alta, celdas de 10 m (0.01 ha), y una cinta de una columna que
el sieve borra si no esta exenta.
"""

import geopandas as gpd
import numpy as np
import pytest
from rasterio.transform import Affine
from shapely.geometry import box

import geophis as geo

UTM = 32613
TF = Affine(10, 0, 0, 0, -10, 100)
RANGOS = [("baja", -1, 4), ("alta", 4, 10), ("cinta", 10, 20)]


def _raster():
    a = np.tile(np.arange(10.0), (10, 1))  # 0..9 por columna
    a[:, 2] = 15.0  # cinta de 10 celdas en medio de la baja
    return a


def test_zonificar_particion_del_recorte_y_sieve():
    predio = gpd.GeoDataFrame(geometry=[box(0, 0, 95, 100)], crs=UTM)  # media columna fuera
    z = geo.zonificar(_raster(), TF, UTM, RANGOS, 11, recorte=predio)
    # la cinta (10 celdas < 11) se la come la baja; la capa cubre el predio
    assert set(z["CLASE"]) == {"baja", "alta"}
    assert z["SUP"].sum() == pytest.approx(0.95)
    assert z.loc[z["CLASE"] == "alta", "SUP"].sum() == pytest.approx(0.45)
    # exenta, la cinta sobrevive con sus 0.10 ha
    z = geo.zonificar(_raster(), TF, UTM, RANGOS, 11, recorte=predio, exentos=["cinta"])
    assert z.loc[z["CLASE"] == "cinta", "SUP"].sum() == pytest.approx(0.10)
    # por pieza la baja sale en dos (la cinta la parte); por clase, una fila
    assert (z["CLASE"] == "baja").sum() == 2
    z = geo.zonificar(_raster(), TF, UTM, RANGOS, 11, exentos=["cinta"], por_clase=True)
    assert len(z) == 3


def test_zonificar_invalidar_deja_fuera_y_no_absorbe():
    # la alta invalidada desaparece y la baja no crece hacia ella
    inval = _raster() > 4  # rangos (min, max]: el 4 es baja
    inval[:, 2] = False
    z = geo.zonificar(_raster(), TF, UTM, RANGOS, 11, invalidar=inval)
    assert set(z["CLASE"]) == {"baja"}
    assert z["SUP"].sum() == pytest.approx(0.50)
    with pytest.raises(ValueError, match="no estan en rangos"):
        geo.zonificar(_raster(), TF, UTM, RANGOS, 11, exentos=["media"])
