"""Los errores comunes salen en español y dicen qué hacer, no el texto de GDAL."""

import os

import geopandas as gpd
import numpy as np
import pytest
from rasterio.transform import from_origin
from shapely.geometry import Point, box

import geophis as geo

UTM = 32613
TF = from_origin(0, 5, 1, 1)


@pytest.fixture
def pol():
    return gpd.GeoDataFrame({"id": [1]}, geometry=[box(0, 0, 100, 100)], crs=UTM)


@pytest.fixture
def tif(tmp_path):
    return geo.guardar_raster(np.ones((5, 5)), tmp_path / "m.tif", TF, UTM)


def test_cargar_archivo_que_no_existe_propone_parecidos(tmp_path, pol):
    geo.guardar(pol, tmp_path / "predio.shp")
    with pytest.raises(FileNotFoundError, match=r"no existe el archivo.*predio\.shp"):
        geo.cargar(tmp_path / "predo.shp")
    with pytest.raises(FileNotFoundError, match="no existe la carpeta"):
        geo.cargar(tmp_path / "nada" / "predio.shp")


@pytest.mark.parametrize(
    "leer",
    [
        geo.cargar_raster,
        lambda r: geo.cargar_bandas(r, (1,)),
        lambda r: geo.mosaico([r], gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)], crs=UTM)),
        geo.cargar_waypoints,
        geo.cargar_tracks,
    ],
)
def test_lectores_dicen_no_existe_en_espanol(tmp_path, leer):
    with pytest.raises(FileNotFoundError, match="no existe el archivo"):
        leer(tmp_path / "no.tif")


def test_cargar_carpeta_vacia_o_capa_inexistente(tmp_path, pol):
    with pytest.raises(ValueError, match="ruta vacía"):
        geo.cargar("")
    with pytest.raises(IsADirectoryError, match="es una carpeta"):
        geo.cargar_raster(tmp_path)
    geo.guardar(pol, tmp_path / "a.gpkg")
    with pytest.raises(ValueError, match=r"no tiene la capa 'zzz'; capas: \['a'\]"):
        geo.cargar(tmp_path / "a.gpkg", capa="zzz")


def test_cargar_formato_desconocido(tmp_path):
    (tmp_path / "nota.txt").write_text("hola")
    with pytest.raises(ValueError, match="no es un formato que se pueda leer"):
        geo.cargar(tmp_path / "nota.txt")


@pytest.mark.parametrize("nombre", ["", "carpeta/", "x.abc", "sin_ext"])
def test_guardar_ruta_mala_no_escribe_nada(tmp_path, pol, nombre):
    ruta = str(tmp_path / nombre) if nombre else ""
    with pytest.raises((ValueError, IsADirectoryError)):
        geo.guardar(pol, ruta)
    assert not [n for n in os.listdir(tmp_path) if n != "carpeta"]


@pytest.mark.parametrize("nombre", ["", "carpeta/", "mde", "mde.png"])
def test_guardar_raster_ruta_mala(tmp_path, nombre):
    ruta = str(tmp_path / nombre) if nombre else ""
    with pytest.raises((ValueError, IsADirectoryError)):
        geo.guardar_raster(np.ones((5, 5)), ruta, TF, UTM)
    with pytest.raises(IsADirectoryError, match="añade el nombre"):
        geo.guardar_raster(np.ones((5, 5)), tmp_path, TF, UTM)


def test_argumentos_cambiados_de_lugar(pol, tif):
    with pytest.raises(TypeError, match="orden de los argumentos"):
        geo.guardar_raster(np.ones((5, 5)), TF, UTM, "m.tif")
    with pytest.raises(TypeError, match="el primero es la capa"):
        geo.guardar("x.shp", pol)
    with pytest.raises(TypeError, match=r"¿falta geo\.cargar\('a\.shp'\)\?"):
        geo.recortar("a.shp", pol)
    with pytest.raises(TypeError, match="capa ya cargada"):
        geo.cargar(pol)


def test_guardar_geometria_mixta_en_shp(tmp_path):
    mixta = gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1), Point(0, 0)], crs=UTM)
    with pytest.raises(ValueError, match="mezcla"):
        geo.guardar(mixta, tmp_path / "mix.shp")


@pytest.mark.parametrize("destino", [999999, "utm13"])
def test_reproyectar_crs_invalido(pol, destino):
    with pytest.raises(ValueError, match="no es un CRS válido"):
        geo.reproyectar(pol, destino)


def test_puntos_a_linea(pol):
    pts = gpd.GeoDataFrame(geometry=[Point(0, 0), Point(1, 1)], crs=UTM)
    with pytest.raises(ValueError, match="no tiene el campo 'zzz'"):
        geo.puntos_a_linea(pts, "zzz")
    with pytest.raises(ValueError, match="Polygon"):
        geo.puntos_a_linea(pol)


def test_casos_sueltos(pol, capsys):
    with pytest.raises(TypeError, match="un número"):
        geo.zona_de_influencia(pol, "10")
    geo.zona_de_influencia(pol.set_crs(None, allow_override=True), 1)
    assert "no tiene CRS" in capsys.readouterr().out
    with pytest.raises(ValueError, match="tolerancia"):
        geo.simplificar(pol, -5)
    with pytest.raises(ValueError, match="2D"):
        geo.calcular_pendiente(np.ones(5), 1)
    with pytest.raises(TypeError, match="entero de 0 a 255"):
        geo.rasterizar(pol, (5, 5), TF, valor="id")
