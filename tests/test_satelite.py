"""Tests de `satelite.py`: busqueda STAC y armado de la imagen Sentinel-2.

Todo sin red: la busqueda se prueba con un JSON escrito a mano y con
`urlopen` sustituido; la imagen, con GeoTIFF sinteticos cuyas rutas van en
`escenas` hechas a mano (misma forma que devuelve `buscar_sentinel2`).

El test de red va aparte, con `GEOPHIS_RED=1` (`correr_red.cmd`).
"""

import io
import json
import os
import urllib.error

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine
from shapely.geometry import box, mapping

import geophis as geo
from geophis import satelite

UTM = 32613


# ---- _agrupar_escenas y la busqueda ---------------------------------------


def _item(tesela, fecha, x0, y0, nubes=5.0, updated="2026-01-01", href="a"):
    return {
        "id": f"S2A_T{tesela}_{fecha.replace('-', '')}T170000_L2A",
        "geometry": mapping(box(x0, y0, x0 + 0.1, y0 + 0.1)),
        "properties": {
            "datetime": f"{fecha}T17:00:00Z",
            "grid:code": f"MGRS-{tesela}",
            "eo:cloud_cover": nubes,
            "updated": updated,
        },
        "assets": {"red": {"href": href}, "scl": {"href": href + "_scl"}},
    }


# el predio cae en la esquina de 4 teselas, como en un predio real
PREDIO_GEO = gpd.GeoDataFrame(geometry=[box(-104.03, 21.97, -103.97, 22.03)], crs=4326)
ESQUINAS = {"13QDC": (-104.1, 21.9), "13QEC": (-104.0, 21.9), "13QDD": (-104.1, 22.0)}
ESQUINAS["13QED"] = (-104.0, 22.0)


def test_agrupar_deduplica_y_mide_cobertura():
    f = [_item(t, "2026-04-24", *xy, nubes=3.0) for t, xy in ESQUINAS.items()]
    f.append(
        _item("13QDC", "2026-04-24", *ESQUINAS["13QDC"], updated="2026-02", href="b")
    )
    # otra pasada del mismo dia: mas reciente pero media franja, no gana
    franja = _item("13QEC", "2026-04-24", -104.0, 21.9, updated="2027", href="c")
    franja["geometry"] = mapping(box(-104.0, 21.9, -103.98, 22.0))
    f.append(franja)
    # otra fecha con 3 teselas: le falta 13QED
    f += [_item(t, "2026-04-29", *xy, nubes=1.0) for t, xy in list(ESQUINAS.items())[:3]]
    escenas = satelite._agrupar_escenas(f, PREDIO_GEO.to_crs(UTM))
    assert [e["fecha"] for e in escenas] == ["2026-04-29", "2026-04-24"]  # menos nubes
    completa = escenas[1]
    assert completa["teselas"] == sorted(ESQUINAS)
    assert completa["cubre"] == pytest.approx(100)
    assert len(completa["urls"]["red"]) == 4 and "b" in completa["urls"]["red"]
    assert "c" not in completa["urls"]["red"]
    assert 70 < escenas[0]["cubre"] < 80  # la de 3 teselas no cubre todo


class _Resp(io.BytesIO):
    status = 200


def _fake_urlopen(paginas, pedidas):
    def urlopen(req, timeout=None):
        pedidas.append(json.loads(req.data))
        return _Resp(json.dumps(paginas.pop(0)).encode())

    return urlopen


def test_buscar_sigue_la_paginacion(monkeypatch):
    p1 = {
        "features": [_item("13QDC", "2026-02-01", *ESQUINAS["13QDC"])],
        "links": [
            {
                "rel": "next",
                "href": satelite.URL_STAC,
                "method": "POST",
                "body": {"next": "x"},
            }
        ],
    }
    p2 = {"features": [_item("13QDC", "2026-05-01", *ESQUINAS["13QDC"])], "links": []}
    pedidas = []
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen([p1, p2], pedidas))
    escenas = geo.buscar_sentinel2(PREDIO_GEO, ("2026-02-01", "2026-05-31"))
    assert {e["fecha"] for e in escenas} == {"2026-02-01", "2026-05-01"}
    assert pedidas[1] == {"next": "x"}  # el body del link, tal cual


def test_pedir_reintenta_un_503(monkeypatch):
    llamadas = []

    def urlopen(req, timeout=None):
        llamadas.append(1)
        if len(llamadas) == 1:
            raise urllib.error.HTTPError(req.full_url, 503, "caido", {}, None)
        return _Resp(b'{"features": []}')

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr(satelite.time, "sleep", lambda s: None)
    assert satelite._pedir(satelite.URL_STAC, {}, "POST") == {"features": []}
    assert len(llamadas) == 2


def test_buscar_sin_resultados_dice_el_bbox(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen([{"features": []}], []))
    with pytest.raises(ValueError, match=r"bbox \[-104\.03"):
        geo.buscar_sentinel2(PREDIO_GEO, ("2026-02-01", "2026-05-31"))


# ---- imagen_sentinel2 con GeoTIFF sinteticos -------------------------------

# bandas de 10 m y scl de 20 m sobre 200 x 200 m; el predio queda adentro
PREDIO = gpd.GeoDataFrame(geometry=[box(500020, 1999820, 500180, 1999980)], crs=UTM)


def _tif(ruta, arr, res, escala=True):
    arr = np.asarray(arr)
    with rasterio.open(
        ruta,
        "w",
        driver="GTiff",
        crs=UTM,
        transform=Affine(res, 0, 500000, 0, -res, 2000000),
        width=arr.shape[1],
        height=arr.shape[0],
        count=1,
        dtype="uint16",
        nodata=0,
    ) as dst:
        dst.write(arr.astype("uint16"), 1)
        if escala:
            dst.scales, dst.offsets = (0.0001,), (-0.1,)
    return str(ruta)


def _escena(tmp_path, fecha, scl, crudo, nubes=1.0):
    """Una escena de una tesela: cada banda de 10 m vale `crudo`, scl de 20 m."""
    d = tmp_path / fecha
    d.mkdir()
    urls = {"scl": [_tif(d / "scl.tif", scl, 20, escala=False)]}
    for k in satelite.BANDAS_S2:
        res, n = (20, 10) if k.startswith("swir") else (10, 20)
        urls[k] = [_tif(d / f"{k}.tif", np.full((n, n), crudo), res)]
    return {
        "fecha": fecha,
        "teselas": ["13QDC"],
        "cubre": 100.0,
        "nubes": nubes,
        "urls": urls,
    }


def test_imagen_una_escena_limpia(tmp_path):
    e = _escena(tmp_path, "2026-04-24", np.full((10, 10), 4), 2000)
    bandas, tf, crs, res, informe, origen = geo.imagen_sentinel2(PREDIO, escenas=[e])
    assert set(bandas) == {"azul", "verde", "rojo", "nir", "swir1", "swir2"}
    assert len(informe) == 1 and informe["usada"].all()
    # 20 m de scl y swir contra 10 m de las visibles: sale en la malla de 10 m
    assert res == 10 and bandas["rojo"].shape == bandas["swir1"].shape == (16, 16)
    assert np.nanmax(bandas["rojo"]) == pytest.approx(0.1)  # reflectancia, no crudo
    assert not np.isnan(bandas["swir2"]).any()
    assert crs.to_epsg() == UTM
    assert (origen == 0).all()  # todo sale de la fila 0 del informe


def test_imagen_rellena_la_nube_con_la_segunda(tmp_path):
    scl1 = np.full((10, 10), 4)
    scl1[2:5, 2:5] = 9  # nube alta
    scl2 = np.full((10, 10), 5)
    scl2[6:8, 6:8] = 8  # otra nube, en otro sitio
    e1 = _escena(tmp_path, "2026-04-24", scl1, 2000, nubes=1.0)
    e2 = _escena(tmp_path, "2026-04-29", scl2, 3000, nubes=2.0)
    bandas, *_, informe, origen = geo.imagen_sentinel2(
        PREDIO, escenas=[e1, e2], margen_nube=0
    )
    assert informe["usada"].all()
    assert informe["aporta_pct"].sum() == pytest.approx(100)
    rojo = bandas["rojo"]
    assert not np.isnan(rojo).any()
    assert rojo[4, 4] == pytest.approx(0.2)  # hueco de e1 relleno con e2
    assert rojo[10, 10] == pytest.approx(0.1)
    # e2 es la principal (menos nube en el predio); origen = fila del informe
    assert origen[4, 4] == 1 and origen[10, 10] == 0


def test_imagen_cita_la_licencia_aunque_mostrar_false(tmp_path, capsys):
    e = _escena(tmp_path, "2026-04-24", np.full((10, 10), 4), 2000)
    geo.imagen_sentinel2(PREDIO, escenas=[e])
    assert "Copernicus Sentinel data 2026" in capsys.readouterr().out


def test_imagen_max_revisar_corta_la_lectura_y_avisa(tmp_path, capsys):
    nublada = np.full((10, 10), 9)  # toda nube alta: ninguna llega a limpio_min
    escenas = [_escena(tmp_path, f"2026-04-2{i}", nublada, 2000) for i in range(3)]
    *_, informe, _ = geo.imagen_sentinel2(PREDIO, escenas=escenas, max_revisar=2)
    assert len(informe) == 2  # la tercera no se leyo
    assert "quedan 1 sin leer" in capsys.readouterr().out


def test_imagen_scl_sucio_sale_nan(tmp_path):
    scl = np.full((10, 10), 4)
    scl[1, 1:9] = satelite.SCL_SUCIO + (5,)
    e = _escena(tmp_path, "2026-04-24", scl, 2000)
    bandas, *_ = geo.imagen_sentinel2(PREDIO, escenas=[e], limpio_min=0, margen_nube=0)
    fila = bandas["nir"][0]  # fila 1 de la scl de 20 m, ya dentro del predio
    # columnas 1..7 de scl a 20 m: sucias -> NaN; la 8 (clase 5) con dato
    assert np.isnan(fila[0:14]).all()
    assert not np.isnan(fila[14:]).any()


def test_imagen_margen_nube_ensancha_la_nube(tmp_path):
    scl = np.full((10, 10), 4)
    scl[5, 5] = 9  # un pixel de nube de 20 m = celdas 8 y 9 de 10 m
    e = _escena(tmp_path, "2026-04-24", scl, 2000)
    rojo = geo.imagen_sentinel2(PREDIO, escenas=[e], limpio_min=0)[0]["rojo"]
    # margen 2 (default): pixeles 3..7 de 20 m -> celdas 4..13 de 10 m
    assert np.isnan(rojo[4:14, 4:14]).all()
    assert not np.isnan(rojo[3, :]).any() and not np.isnan(rojo[:, 14]).any()
    rojo0 = geo.imagen_sentinel2(PREDIO, escenas=[e], limpio_min=0, margen_nube=0)[0]["rojo"]
    assert np.isnan(rojo0).sum() == 4  # solo el pixel de 20 m


def test_imagen_rellena_con_la_fecha_cercana_no_con_la_limpia(tmp_path):
    principal = np.full((10, 10), 4)
    principal[2:5, 2:5] = 9  # 86 % limpio
    cerca = np.full((10, 10), 4)
    cerca[6:9, 1:9] = 9  # 62 % limpio, pero limpia sobre el hueco
    lejos = np.full((10, 10), 4)
    lejos[6:9, 1:5] = 9  # 81 % limpio, tambien limpia sobre el hueco
    escenas = [
        _escena(tmp_path, "2026-04-10", principal, 2000),
        _escena(tmp_path, "2026-01-10", lejos, 2000),
        _escena(tmp_path, "2026-04-15", cerca, 2000),
    ]
    *_, informe, _ = geo.imagen_sentinel2(
        PREDIO, escenas=escenas, margen_nube=0, max_dias=None
    )
    assert informe["usada"].tolist() == [True, False, True]
    assert informe["dias"].tolist() == [0, 90, 5]
    # con max_dias=3 ninguna rellena: queda el hueco y se avisa
    *_, informe, _ = geo.imagen_sentinel2(PREDIO, escenas=escenas, margen_nube=0, max_dias=3)
    assert informe["usada"].tolist() == [True, False, False]


def test_la_receta_del_predio_a_la_imagen_corre_entera(tmp_path, monkeypatch):
    """`docs/GEOPHIS.md` §"Del predio a la imagen", linea por linea, sin red.

    Solo se sustituye la busqueda: `imagen_sentinel2(fechas=)` recibe dos
    escenas sinteticas, la primera con una nube que rellena la segunda, para
    que el `informe` tenga dos filas usadas como en lluvias. Comprueba que la
    receta encadena y que lo que promete leer del informe es cierto. No
    comprueba cifras de campo.
    """
    scl1 = np.full((10, 10), 4)
    scl1[1:3, 1:3] = 9
    scl2 = np.full((10, 10), 5)
    scl2[8:10, 8:10] = 8  # lejos de la de scl1: ni con margen_nube se tocan
    escenas = [
        _escena(tmp_path, "2026-04-24", scl1, 2000),
        _escena(tmp_path, "2026-04-29", scl2, 3000, nubes=2.0),
    ]
    monkeypatch.setattr(satelite, "buscar_sentinel2", lambda *a, **k: escenas)
    ruta = tmp_path / "PREDIO.shp"
    geo.guardar(PREDIO.to_crs(4326), ruta)  # otro CRS, como en un predio real
    monkeypatch.chdir(tmp_path)

    # -- la receta --
    RUTA_PREDIO = ruta
    FECHAS = ("2026-02-01", "2026-05-31")
    NOMBRES = ["azul", "verde", "rojo", "nir", "swir1", "swir2"]

    predio = geo.cargar(RUTA_PREDIO)
    bandas, tf, crs, res, informe, origen = geo.imagen_sentinel2(predio, fechas=FECHAS)
    print(informe.to_string(index=False))
    print(f"predio sin dato limpio: {100 - informe['aporta_pct'].sum():.2f} %")

    geo.guardar_raster(
        [bandas[n] for n in NOMBRES], "S2_BANDAS.tif", tf, crs, nombres=NOMBRES
    )
    geo.guardar_raster(origen, "S2_ORIGEN.tif", tf, crs)  # fila del informe por pixel
    anios = sorted({f[:4] for f in informe.loc[informe["usada"], "fecha"]})
    CITA = f"Contains modified Copernicus Sentinel data {', '.join(anios)}"

    ndvi = geo.indice_vegetacion("ndvi", **bandas)
    geo.guardar_raster(ndvi, "NDVI.tif", tf, crs)
    resumen = geo.resumen_raster(ndvi, tf)
    geo.vista_previa((ndvi, geo.reproyectar(predio, crs)), "NDVI.png", transform=tf)

    # -- lo que la receta promete --
    assert crs.to_epsg() == UTM  # UTM del predio, no el 4326 de origen
    assert informe["usada"].sum() == 2  # la nube obliga a mezclar fechas
    assert 100 - informe["aporta_pct"].sum() == pytest.approx(0)
    assert set(np.unique(origen)) == {0.0, 1.0}  # cada pixel dice su fecha
    assert CITA == "Contains modified Copernicus Sentinel data 2026"
    with rasterio.open(tmp_path / "S2_BANDAS.tif") as src:
        assert src.count == 6 and list(src.descriptions) == NOMBRES
    assert resumen["p50"] == pytest.approx(0)  # bandas sinteticas iguales
    assert (tmp_path / "NDVI.png").exists()


def test_imagen_fechas_y_escenas_a_la_vez_falla():
    with pytest.raises(ValueError, match="exactamente uno"):
        geo.imagen_sentinel2(PREDIO, fechas=("2026-01-01", "2026-02-01"), escenas=[{}])


# ---- con red (fuera de la suite normal) ------------------------------------


# Predios sinteticos en UTM 13N, no archivos: las teselas MGRS van cada 100 km
# y miden 109.8 km, asi que el traslape es de 9.8 km al E y al S de cada linea.
# El primero cruza la esquina de 600000/2400000 mas alla de ese traslape en los
# dos ejes (necesita 4 teselas); el segundo cae en el centro de una.
@pytest.mark.skipif(not os.environ.get("GEOPHIS_RED"), reason="sin GEOPHIS_RED=1")
@pytest.mark.parametrize(
    "caja, teselas",
    [
        ((599_000, 2_389_000, 611_000, 2_401_000), 4),
        ((650_000, 2_350_000, 652_000, 2_352_000), 1),
    ],
)
def test_red_predios_sinteticos(caja, teselas):
    predio = gpd.GeoDataFrame(geometry=[box(*caja)], crs=UTM)
    escenas = geo.buscar_sentinel2(predio, ("2026-02-01", "2026-05-31"))
    mejor = next(e for e in escenas if e["cubre"] >= 100)
    assert len(mejor["teselas"]) == teselas
    rojo, tf, _, _ = geo.mosaico(mejor["urls"]["red"], predio)
    dentro = geo.rasterizar(predio, rojo.shape, tf) == 1
    assert np.isnan(rojo[dentro]).mean() == 0  # ningun hueco por falta de tesela


def test_imagen_sin_anillo_vacio_en_el_borde(tmp_path):
    # lindero que no cae en la malla de 20 m: las celdas de 10 m del borde cuyo
    # pixel de 20 m tiene el centro fuera del predio no deben quedar vacias
    predio = gpd.GeoDataFrame(geometry=[box(500030, 1999830, 500170, 1999970)], crs=UTM)
    e = _escena(tmp_path, "2026-04-24", np.full((10, 10), 4), 2000)
    bandas, tf, *_, informe, _ = geo.imagen_sentinel2(predio, escenas=[e])
    dentro = geo.rasterizar(predio, bandas["swir1"].shape, tf) == 1
    assert not np.isnan(bandas["swir1"][dentro]).any()
    assert np.isnan(bandas["swir1"][~dentro]).all()
    assert informe["aporta_pct"].iloc[0] == pytest.approx(100)
