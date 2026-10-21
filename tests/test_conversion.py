"""Tests de conversión de formato (SHP <-> GPX, SHP -> KMZ).

La lógica propia que probamos es la reproyección automática a EPSG:4326;
el ruteo de capas y el driver KMZ los pone GDAL. El test de KMZ falla si
tu build de GDAL no trae el driver LIBKML: sirve de verificación.
"""

import os

import geopandas as gpd
from shapely.geometry import LineString, MultiPoint, Point

import geophis as geo

# Coords en UTM 13N (metros) dentro de la zona de operación.
_LINEA_UTM = LineString([(300000, 2600000), (300100, 2600100)])
_PUNTO_UTM = Point(300000, 2600000)


def test_shp_a_gpx_track_reproyecta_a_4326(tmp_path, gdf):
    g = gdf(_LINEA_UTM)  # crs=32613 por defecto
    ruta = str(tmp_path / "ruta.gpx")
    geo.guardar(g, ruta)  # gpx_como="track" por defecto

    r = gpd.read_file(ruta, layer="tracks")  # el GPS aquí trabaja con tracks
    assert len(r) >= 1
    # tras reproyectar, las coords son lon/lat: México ~ (-106, 23).
    minx, miny, maxx, maxy = r.total_bounds
    assert minx < -90 and 10 < maxy < 40


def test_shp_a_gpx_route(tmp_path, gdf):
    g = gdf(_LINEA_UTM)
    ruta = str(tmp_path / "ruta.gpx")
    geo.guardar(g, ruta, gpx_como="route")
    assert len(gpd.read_file(ruta, layer="routes")) >= 1


def test_gpx_de_vuelta_a_shp(tmp_path, gdf):
    g = gdf(_LINEA_UTM)
    gpx = str(tmp_path / "ruta.gpx")
    geo.guardar(g, gpx)

    r = geo.cargar(gpx, capa="tracks")
    shp = str(tmp_path / "vuelta.shp")
    geo.guardar(r, shp)
    assert len(geo.cargar(shp)) >= 1


def test_shp_a_kmz(tmp_path, gdf, cuadrado):
    # Falla si GDAL no tiene el driver LIBKML: eso es lo que queremos saber.
    g = gdf(cuadrado(x=300000, y=2600000))
    ruta = str(tmp_path / "predio.kmz")
    geo.guardar(g, ruta)
    assert os.path.exists(ruta) and os.path.getsize(ruta) > 0


def test_convertir_gpx_solo_tracks_autodetecta_capa(tmp_path, gdf):
    # Un GPX de solo tracks leído a secas sale vacío; convertir debe encontrarlo.
    g = gdf(_LINEA_UTM)
    gpx = str(tmp_path / "solo_track.gpx")
    geo.guardar(g, gpx)  # escribe track
    # capa por defecto (waypoints) vacía; la pedimos explícita para no
    # disparar el aviso de multicapa de pyogrio.
    assert len(gpd.read_file(gpx, layer="waypoints")) == 0

    shp = str(tmp_path / "de_track.shp")
    geo.convertir(gpx, shp)
    assert len(geo.cargar(shp)) >= 1


def test_puntos_con_atributos_ida_y_vuelta(tmp_path, capsys):
    # Antes revento con "Error adding field 'NAME'": GPX_USE_EXTENSIONS no
    # llegaba a GDAL. Es justo el SHP que sale de convertir un GPX de waypoints.
    g = gpd.GeoDataFrame(
        {"NAME": ["P1", "P2"], "ELEVATION": [5.0, 6.0], "CLAVE": ["a", "b"]},
        geometry=[_PUNTO_UTM, MultiPoint([(300050, 2600050), (300060, 2600060)])],
        crs=32613,
    )
    gpx = str(tmp_path / "pts.gpx")
    geo.guardar(g, gpx, gpx_como="track")  # SHP no mezcla Point y MultiPoint
    assert "gpx_como no aplica" in capsys.readouterr().out

    w = gpd.read_file(gpx, layer="waypoints")
    assert list(w["name"]) == ["P1", "P2", "P2"]  # el multipunto se separa
    assert list(w["ogr_CLAVE"]) == ["a", "b", "b"]
    # GPX -> SHP (NAME, LAYER, ELEVATION...) -> GPX, sin tocar campos
    geo.convertir(gpx, str(tmp_path / "vuelta.shp"))
    geo.convertir(str(tmp_path / "vuelta.shp"), str(tmp_path / "vuelta.gpx"))
    assert len(gpd.read_file(str(tmp_path / "vuelta.gpx"), layer="waypoints")) == 3


def test_convertir_gpx_solo_punto(tmp_path, gdf):
    g = gdf(_PUNTO_UTM)
    gpx = str(tmp_path / "solo_punto.gpx")
    geo.guardar(g, gpx)  # punto -> waypoints
    shp = str(tmp_path / "de_punto.shp")
    geo.convertir(gpx, shp)
    r = geo.cargar(shp)
    assert len(r) == 1 and r.geom_type.iloc[0] == "Point"
