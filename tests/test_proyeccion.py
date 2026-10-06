"""Tests de geophis.proyeccion."""

import pytest

import geophis as geo
from geophis.proyeccion import _exigir_crs

UTM = 32613


def test_exigir_crs_falla_sin_crs(gdf, cuadrado):
    g = gdf(cuadrado(), crs=None)
    with pytest.raises(ValueError):
        _exigir_crs(g)


def test_reproyectar_no_hace_nada_si_ya_esta(gdf, cuadrado):
    g = gdf(cuadrado())
    assert geo.reproyectar(g, UTM) is g


def test_reproyectar_equivalente_sin_codigo_no_reproyecta(gdf, cuadrado):
    from pyproj import CRS

    # mismo CRS pero definido por proj4: to_epsg() da None; equals() lo reconoce
    proj4 = CRS.from_proj4("+proj=utm +zone=13 +datum=WGS84 +units=m +no_defs")
    g = gdf(cuadrado()).set_crs(proj4, allow_override=True)
    assert geo.reproyectar(g, UTM) is g


def test_reproyectar_cambia_crs(gdf, cuadrado):
    g = gdf(cuadrado())
    r = geo.reproyectar(g, 4326)
    assert r is not g and r.crs.to_epsg() == 4326


def test_reproyectar_acepta_crs_de_otra_capa(gdf, cuadrado):
    # el caso INEGI: el CRS destino no tiene código EPSG, to_epsg() da None
    from pyproj import CRS

    lcc = CRS.from_proj4(
        "+proj=lcc +lat_1=17.5 +lat_2=29.5 +lat_0=12 +lon_0=-102 "
        "+x_0=2500000 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )
    otra = gdf(cuadrado()).to_crs(lcc)
    assert otra.crs.to_epsg() is None
    assert geo.reproyectar(gdf(cuadrado()), otra.crs).crs.equals(lcc)


def test_reproyectar_falla_con_destino_none(gdf, cuadrado):
    with pytest.raises(ValueError):
        geo.reproyectar(gdf(cuadrado()), None)


def _punto_utm13(crs):
    import geopandas as gpd
    from shapely.geometry import Point

    return gpd.GeoDataFrame(geometry=[Point(-103.5, 20.5)], crs=4326).to_crs(crs)


def test_reproyectar_wgs84_a_utm_no_avisa_datum(capsys):
    geo.reproyectar(_punto_utm13(4326), UTM)
    assert "datum" not in capsys.readouterr().out


def test_reproyectar_itrf2008_a_utm_no_avisa_datum(capsys):
    # INEGI LCC ITRF2008 -> WGS84: operación de 1 m, dentro de la tolerancia
    geo.reproyectar(_punto_utm13(6372), UTM)
    assert "datum" not in capsys.readouterr().out


def test_reproyectar_nad27_a_utm_avisa_datum(capsys):
    # medido en esta máquina: 'NAD27 to WGS 84 (18)', 12 m en la zona de trabajo
    geo.reproyectar(_punto_utm13(4267), UTM)
    out = capsys.readouterr().out
    assert "cambio de datum" in out and "12 m" in out
