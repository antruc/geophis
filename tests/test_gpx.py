"""Comprueba la conversion GPX -> tabla de atributos limpia (waypoints y tracks).

Los GPX se generan al vuelo con el mismo esquema que exporta el Garmin,
para no tener que mantener archivos GPS reales en el repo.
"""

import pytest

import geophis as geo

WPT = """\
<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <wpt lat="20.5" lon="-104.5">
    <ele>1581.020386</ele>
    <time>2026-02-21T16:09:47Z</time>
    <name>013</name>
    <sym>Flag, Blue</sym>
  </wpt>
</gpx>
"""

# Un track con 2 tramos, una linea por tramo.
# ponytail: sin tramo de 1 punto; GDAL no puede ni leer la capa tracks con eso
# (ver test_cargar_tracks_tramo_de_un_punto_falla_claro).
TRK = """\
<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test"
     xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxx="http://www.garmin.com/xmlschemas/GpxExtensions/v3">
  <trk>
    <name>2026-02-18</name>
    <extensions>
      <gpxx:TrackExtension>
        <gpxx:DisplayColor>Cyan</gpxx:DisplayColor>
      </gpxx:TrackExtension>
    </extensions>
    <trkseg>
      <trkpt lat="20.50" lon="-104.50"><time>2026-02-18T15:59:27Z</time></trkpt>
      <trkpt lat="20.51" lon="-104.51"><time>2026-02-18T22:23:27Z</time></trkpt>
    </trkseg>
    <trkseg>
      <trkpt lat="20.52" lon="-104.52"><time>2026-02-19T16:00:00Z</time></trkpt>
      <trkpt lat="20.53" lon="-104.53"><time>2026-02-19T17:00:00Z</time></trkpt>
    </trkseg>
  </trk>
</gpx>
"""


@pytest.fixture
def gpx(tmp_path):
    """Fabrica de archivos GPX temporales: gpx(contenido) -> ruta."""

    def _hacer(contenido):
        ruta = tmp_path / "prueba.gpx"
        ruta.write_text(contenido, encoding="utf-8")
        return ruta

    return _hacer


def test_cargar_waypoints(gpx):
    g = geo.cargar_waypoints(gpx(WPT))
    assert list(g.columns) == ["NAME", "LAYER", "ELEVATION", "time", "sym", "geometry"]
    r = g.iloc[0]
    assert r["NAME"] == "013"
    assert r["LAYER"] == "Waypoint"
    assert r["ELEVATION"] == 1581.020386
    assert r["time"] == "2026-02-21T16:09:47Z"
    assert r["sym"] == "Flag, Blue"


def test_cargar_tracks(gpx):
    g = geo.cargar_tracks(gpx(TRK))
    assert list(g.columns) == [
        "NAME",
        "LAYER",
        "gpxx_DisplayColor",
        "START_TIME",
        "END_TIME",
        "geometry",
    ]
    assert len(g) == 2  # una linea por tramo
    r = g.iloc[0]
    assert r["NAME"] == "2026-02-18"
    assert r["LAYER"] == "Tracklog"
    assert r["gpxx_DisplayColor"] == "Cyan"
    # 15:59:27Z -> UTC-6 local, formato espanol
    assert r["START_TIME"] == "18/02/2026 09:59:27 a. m."
    assert r["END_TIME"] == "18/02/2026 04:23:27 p. m."


def test_cargar_tracks_sin_time_no_revienta(gpx):
    # GPX de otra fuente sin <time>: tiempos vacios en vez de KeyError
    import re

    sin_time = re.sub(r"<time>[^<]*</time>", "", TRK)
    g = geo.cargar_tracks(gpx(sin_time))
    assert len(g) == 2
    assert (g["START_TIME"] == "").all() and (g["END_TIME"] == "").all()


def test_cargar_tracks_tramo_de_un_punto_falla_claro(gpx):
    roto = TRK.replace(
        "  </trk>",
        "    <trkseg>\n"
        '      <trkpt lat="20.54" lon="-104.54"><time>2026-02-20T16:00:00Z</time></trkpt>\n'
        "    </trkseg>\n"
        "  </trk>",
    )
    with pytest.raises(ValueError, match="tramo de un solo punto"):
        geo.cargar_tracks(gpx(roto))


def test_convertir_gpx_con_tramo_roto_falla_claro(gpx, tmp_path):
    """El sondeo de capas tragaba el error y convertía la capa vacía de waypoints."""
    roto = TRK.replace(
        "  </trk>",
        "    <trkseg>\n"
        '      <trkpt lat="20.54" lon="-104.54"><time>2026-02-20T16:00:00Z</time></trkpt>\n'
        "    </trkseg>\n"
        "  </trk>",
    )
    with pytest.raises(ValueError, match="tramo de un solo punto"):
        geo.convertir(gpx(roto), tmp_path / "salida.shp")
