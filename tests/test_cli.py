"""Tests del CLI (geophis.cli.main): ayuda, validaciones, autonombrado de la
salida y aviso de sobrescritura. La conversion en si la cubre test_conversion;
aqui se prueba solo la capa del CLI.
"""

import geopandas as gpd

import geophis as geo
from geophis.cli import main
from shapely.geometry import LineString, Point

_PUNTO_UTM = Point(300000, 2600000)
_LINEA_UTM = LineString([(300000, 2600000), (300100, 2600100)])


def test_sin_args_muestra_ayuda(capsys):
    assert main([]) == 0
    assert "uso: geophis" in capsys.readouterr().out


def test_ayuda_por_palabra(capsys):
    assert main(["ayuda"]) == 0
    assert "ejemplos:" in capsys.readouterr().out


def test_tipo_invalido(capsys):
    assert main(["cualquiera.shp", "foo"]) == 2
    assert "no valido" in capsys.readouterr().out


def test_archivo_inexistente(capsys):
    assert main(["nope.shp", "kmz"]) == 1  # un archivo fallido -> codigo 1
    assert "no existe" in capsys.readouterr().out


def test_mismo_tipo_se_salta(tmp_path, capsys):
    f = tmp_path / "x.shp"
    f.write_text("")  # solo debe existir; el CLI no lo lee en este caso
    assert main([str(f), "shp"]) == 0  # se salta, no es error
    assert "ya es" in capsys.readouterr().out


def test_convierte_y_autonombra(tmp_path, gdf):
    gpx = tmp_path / "punto.gpx"
    geo.guardar(gdf(_PUNTO_UTM), str(gpx))
    assert main([str(gpx), "shp"]) == 0
    assert (tmp_path / "punto.shp").exists()  # misma carpeta y nombre, .shp


def test_sobrescribir_responde_no_y_salta(tmp_path, gdf, monkeypatch, capsys):
    gpx = tmp_path / "p.gpx"
    geo.guardar(gdf(_PUNTO_UTM), str(gpx))
    salida = tmp_path / "p.shp"
    salida.write_text("previo")
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert main([str(gpx), "shp"]) == 0
    assert "Saltado" in capsys.readouterr().out
    assert salida.read_text() == "previo"  # intacto


def test_sobrescribir_si_procede(tmp_path, gdf, monkeypatch):
    gpx = tmp_path / "q.gpx"
    geo.guardar(gdf(_PUNTO_UTM), str(gpx))
    salida = tmp_path / "q.shp"
    salida.write_text("previo")
    monkeypatch.setattr("builtins.input", lambda _: "s")
    assert main([str(gpx), "shp"]) == 0
    assert len(geo.cargar(str(salida))) == 1  # ya es shapefile real


def test_flag_si_sobrescribe_sin_preguntar(tmp_path, gdf, monkeypatch):
    gpx = tmp_path / "r.gpx"
    geo.guardar(gdf(_PUNTO_UTM), str(gpx))
    salida = tmp_path / "r.shp"
    salida.write_text("previo")

    def _no_preguntar(_):
        raise AssertionError("con --si no debe preguntar")

    monkeypatch.setattr("builtins.input", _no_preguntar)
    assert main([str(gpx), "shp", "--si"]) == 0
    assert len(geo.cargar(str(salida))) == 1  # sobrescrito


def test_tipo_con_punto_tambien_vale(tmp_path, gdf):
    gpx = tmp_path / "p.gpx"
    geo.guardar(gdf(_PUNTO_UTM), str(gpx))
    assert main([str(gpx), ".shp"]) == 0
    assert (tmp_path / "p.shp").exists()


def test_gpx_como_route(tmp_path, gdf):
    shp = tmp_path / "linea.shp"
    geo.guardar(gdf(_LINEA_UTM), str(shp))
    assert main([str(shp), "gpx", "--gpx-como", "route"]) == 0
    assert len(gpd.read_file(tmp_path / "linea.gpx", layer="routes")) >= 1


def test_capa_explicita(tmp_path, gdf):
    gpx = tmp_path / "t.gpx"
    geo.guardar(gdf(_LINEA_UTM), str(gpx))  # escribe tracks
    assert main([str(gpx), "geojson", "--capa", "tracks"]) == 0
    assert (tmp_path / "t.geojson").exists()


def test_lote_varios_archivos(tmp_path, gdf, capsys):
    for n in ("a", "b", "c"):
        geo.guardar(gdf(_PUNTO_UTM), str(tmp_path / f"{n}.gpx"))
    rutas = [str(tmp_path / f"{n}.gpx") for n in ("a", "b", "c")]
    assert main(rutas + ["shp"]) == 0
    for n in ("a", "b", "c"):
        assert (tmp_path / f"{n}.shp").exists()
    assert "3 convertidos, 0 con error" in capsys.readouterr().out


def test_lote_sigue_tras_error(tmp_path, gdf, capsys):
    geo.guardar(gdf(_PUNTO_UTM), str(tmp_path / "ok.gpx"))
    rutas = [str(tmp_path / "nope.gpx"), str(tmp_path / "ok.gpx")]
    assert main(rutas + ["shp"]) == 1  # hubo un fallo
    assert (tmp_path / "ok.shp").exists()  # el bueno se convirtio igual
    assert "1 convertidos, 1 con error" in capsys.readouterr().out
