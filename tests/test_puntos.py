"""Tests de carga de puntos desde CSV/Excel y conversiones punto/línea/polígono."""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

import geophis as geo

UTM = 32613

# Cuatro esquinas de un cuadrado de 100 m en UTM 13N, en orden de contorno.
_ESQUINAS = [
    {"seq": 1, "x": 300000, "y": 2600000},
    {"seq": 2, "x": 300100, "y": 2600000},
    {"seq": 3, "x": 300100, "y": 2600100},
    {"seq": 4, "x": 300000, "y": 2600100},
]


def _pts(**cols):
    """Capa de puntos con las 4 esquinas y columnas extra opcionales."""
    return gpd.GeoDataFrame(
        cols,
        geometry=gpd.points_from_xy(
            [e["x"] for e in _ESQUINAS], [e["y"] for e in _ESQUINAS]
        ),
        crs=UTM,
    )


def test_cargar_puntos_csv_asigna_crs(tmp_path):
    ruta = tmp_path / "pts.csv"
    pd.DataFrame(_ESQUINAS).to_csv(ruta, index=False)
    gdf = geo.cargar_puntos(ruta, epsg=UTM)
    assert len(gdf) == 4
    assert gdf.crs.to_epsg() == UTM
    assert (gdf.geom_type == "Point").all()


def test_cargar_puntos_orden(tmp_path):
    ruta = tmp_path / "pts.csv"
    pd.DataFrame(reversed(_ESQUINAS)).to_csv(ruta, index=False)
    gdf = geo.cargar_puntos(ruta, epsg=UTM, orden="seq")
    assert gdf.iloc[0]["seq"] == 1  # reordenado por la columna de secuencia


def test_cargar_puntos_excel(tmp_path):
    ruta = tmp_path / "pts.xlsx"
    pd.DataFrame(_ESQUINAS).to_excel(ruta, index=False)
    gdf = geo.cargar_puntos(ruta, epsg=UTM)
    assert len(gdf) == 4


def test_puntos_a_linea():
    pts = gpd.GeoDataFrame(
        pd.DataFrame(_ESQUINAS),
        geometry=gpd.points_from_xy(
            [e["x"] for e in _ESQUINAS], [e["y"] for e in _ESQUINAS]
        ),
        crs=UTM,
    )
    linea = geo.puntos_a_linea(pts)
    assert linea.geom_type.iloc[0] == "LineString"
    assert len(linea.geometry.iloc[0].coords) == 4


def test_puntos_a_poligono_cierra_area():
    pts = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(
            [e["x"] for e in _ESQUINAS], [e["y"] for e in _ESQUINAS]
        ),
        crs=UTM,
    )
    poly = geo.puntos_a_poligono(pts)
    assert poly.geom_type.iloc[0] == "Polygon"
    assert round(poly.geometry.iloc[0].area) == 10000  # 100 x 100 m


def test_puntos_a_linea_agrupa_por_campo():
    pts = _pts(grupo=["a", "a", "b", "b"])
    r = geo.puntos_a_linea(pts, campo="grupo")
    assert len(r) == 2 and list(r["grupo"]) == ["a", "b"]


def test_puntos_a_linea_un_punto_falla():
    with pytest.raises(ValueError):
        geo.puntos_a_linea(_pts().iloc[:1])


def test_puntos_a_poligono_pocos_puntos_falla():
    with pytest.raises(ValueError):
        geo.puntos_a_poligono(_pts().iloc[:2])


def test_puntos_a_poligono_envolvente_ignora_orden():
    pts = _pts().iloc[[0, 2, 1, 3]]  # orden cruzado (bowtie)
    poly = geo.puntos_a_poligono(pts, envolvente=True)
    assert round(poly.geometry.iloc[0].area) == 10000


def test_cargar_puntos_columna_faltante_falla(tmp_path):
    ruta = tmp_path / "pts.csv"
    pd.DataFrame({"este": [1], "norte": [2]}).to_csv(ruta, index=False)
    with pytest.raises(ValueError, match="no tiene la columna"):
        geo.cargar_puntos(ruta, epsg=UTM)


def test_cargar_puntos_orden_faltante_falla(tmp_path):
    ruta = tmp_path / "pts.csv"
    pd.DataFrame(_ESQUINAS).to_csv(ruta, index=False)
    with pytest.raises(ValueError, match="orden"):
        geo.cargar_puntos(ruta, epsg=UTM, orden="nope")


def test_linea_a_poligono(gdf):
    coords = [(e["x"], e["y"]) for e in _ESQUINAS]
    capa = gdf(LineString(coords))
    poly = geo.linea_a_poligono(capa)
    assert poly.geom_type.iloc[0] == "Polygon"
    assert round(poly.geometry.iloc[0].area) == 10000
