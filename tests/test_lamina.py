"""lamina: contra verdad analitica (percentiles, el cero, la escala), no contra si misma."""

import importlib

import numpy as np
import pandas as pd
import pytest
from PIL import Image
from rasterio import Affine

import geophis as geo

# el modulo, no la funcion: `geo.lamina` es la funcion del mismo nombre
lam = importlib.import_module("geophis.lamina")

UTM = 32613
TF = Affine(10, 0, 500_000, 0, -10, 2_000_000)


def _rampa(alto=50, ancho=100, lo=0.0, hi=1.0):
    return np.tile(np.linspace(lo, hi, ancho), (alto, 1))


def test_estirado_es_p2_p98_y_los_extremos_toman_el_color_de_los_extremos():
    z = _rampa()
    p = geo.panel_continuo(z, TF, UTM, "rampa", paleta="gris", unidad="u")
    r = lam._rango(p["datos"], "gris")
    assert r == pytest.approx(tuple(np.percentile(z, [2, 98])))
    img = lam._colorear(p, p["datos"], r)
    assert tuple(img[0, 0]) == (0, 0, 0)
    assert tuple(img[0, -1]) == (255, 255, 255)


def test_divergente_simetrico_el_cero_cae_en_el_color_central():
    z = _rampa(lo=-0.1, hi=0.5)  # asimetrico a proposito
    p = geo.panel_continuo(z, TF, UTM, "dNDVI", paleta="divergente", unidad="dNDVI")
    vmin, vmax = lam._rango(p["datos"], "divergente")
    assert vmin == pytest.approx(-vmax)
    cero = np.zeros((1, 1))
    assert (
        tuple(lam._colorear(p, cero, (vmin, vmax))[0, 0]) == lam.PALETAS["divergente"][2]
    )


def test_sin_dato_no_se_confunde_con_el_minimo():
    z = _rampa()
    z[0, 0] = np.nan
    p = geo.panel_continuo(z, TF, UTM, "x", paleta="gris", unidad="u")
    img = lam._colorear(p, p["datos"], (0.0, 1.0))
    assert tuple(img[0, 0]) == lam.SIN_DATO
    assert tuple(img[1, 0]) != lam.SIN_DATO  # el minimo


def test_clases_reducidas_no_inventan_colores():
    # 3000 columnas -> se reduce: el vecino mas cercano no mezcla clases
    cod = np.repeat([1, 2, 3, -1], 750)[None].repeat(10, 0)
    cols = {1: "#1a9850", 2: "#fee08b", 3: "#d73027"}
    p = geo.panel_clases(cod, TF, UTM, "c", {1: "a", 2: "b", 3: "c"}, cols)
    img = lam._colorear(p, lam._reducir(p, 37.0), None)
    vistos = {tuple(c) for c in img.reshape(-1, 3)}
    assert vistos <= {lam._color(c) for c in cols.values()} | {lam.SIN_DATO}


def test_estirado_compartido_por_paleta_y_unidad(tmp_path):
    antes = geo.panel_continuo(
        _rampa(lo=0, hi=0.5), TF, UTM, "antes", "vegetacion", "NDVI"
    )
    despues = geo.panel_continuo(
        _rampa(lo=0.3, hi=0.9), TF, UTM, "despues", "vegetacion", "NDVI"
    )
    pend = geo.panel_continuo(_rampa(lo=0, hi=80), TF, UTM, "pendiente", "gris", "%")
    info = geo.lamina([antes, despues, pend], tmp_path / "l.png", cita=None, ancho_px=600)
    r_a, r_d, r_p = (f["estirado"] for f in info["paneles"])
    juntos = np.concatenate([antes["datos"].ravel(), despues["datos"].ravel()])
    assert r_a == r_d == pytest.approx(tuple(np.percentile(juntos, [2, 98])))
    assert r_p == pytest.approx(tuple(np.percentile(pend["datos"], [2, 98])))
    assert Image.open(tmp_path / "l.png").size == (info["ancho"], info["alto"])


def test_barra_de_escala_limpia_y_a_su_longitud(tmp_path):
    # 1000 columnas de 10 m caben enteras en 2000 px: 30 % de 10 km -> 2 km
    p = geo.panel_continuo(_rampa(ancho=1000), TF, UTM, "x", "gris", "u")
    info = geo.lamina([p], tmp_path / "e.png", cita=None, ancho_px=2000)
    assert info["escala_m"] == 2000
    assert info["escala_px"] * 10 == info["escala_m"]


def test_misma_malla_crs_metrico_y_cita_obligatorios(tmp_path):
    a = geo.panel_continuo(_rampa(), TF, UTM, "a", "gris", "u")
    b = geo.panel_continuo(_rampa(), TF @ Affine.translation(1, 0), UTM, "b", "gris", "u")
    with pytest.raises(ValueError, match="malla"):
        geo.lamina([a, b], tmp_path / "x.png", cita=None)
    grados = geo.panel_continuo(
        _rampa(), Affine(0.001, 0, -100, 0, -0.001, 20), 4326, "g", "gris", "u"
    )
    with pytest.raises(ValueError, match="metros"):
        geo.lamina([grados], tmp_path / "x.png", cita=None)
    with pytest.raises(TypeError):
        geo.lamina([a], tmp_path / "x.png")


def test_la_fuente_empaquetada_tiene_acentos():
    f = lam._fuente(20)

    def glifo(c):
        m = f.getmask(c)
        return m.size, bytes(Image.Image()._new(m).tobytes())

    faltante = glifo("一")  # sin glifo en DejaVu Sans: la caja vacia
    for c in "Ñéú²·":
        assert glifo(c) != faltante, c


def test_rotulo_y_cita_salen_del_informe():
    inf = pd.DataFrame(
        {
            "fecha": ["2026-10-05", "2025-12-30", "2026-10-03"],
            "aporta_pct": [2.4, 0.0, 64.9],
            "usada": [True, False, True],
        }
    )
    assert geo.rotulo_escenas(inf) == "03-oct-2026 (64.9 %) y 05-oct-2026 (2.4 %)"
    assert geo.cita_sentinel2(inf) == "Contains modified Copernicus Sentinel data 2026"
    viejo = inf.assign(fecha=["2025-12-30"] * 3)
    assert geo.cita_sentinel2(viejo, inf).endswith("data 2025, 2026")


def test_la_receta_antes_despues_documentada_corre(tmp_path):
    """docs/GEOPHIS.md §"La lámina del antes y el después", pasos 2 a 4, sin red.

    El despues llega en otra malla (desplazada una celda), como pasa con dos
    `imagen_sentinel2`: sin la linea de `alinear_rasters`, `lamina` falla.
    """
    import geopandas as gpd
    from shapely.geometry import box

    rng = np.random.default_rng(0)
    nombres = ["azul", "verde", "rojo", "nir"]
    b_a = {k: rng.uniform(0.02, 0.4, (50, 60)) for k in nombres}
    tf_d = TF @ Affine.translation(-1, -2)
    b_d = {k: rng.uniform(0.02, 0.4, (53, 62)) for k in nombres}
    tf, crs = TF, UTM
    predio = gpd.GeoDataFrame(
        geometry=[box(500_050, 1_999_600, 500_500, 1_999_900)], crs=UTM
    )
    inf = pd.DataFrame({"fecha": ["2026-10-03"], "aporta_pct": [100.0], "usada": [True]})

    b_d = dict(
        zip(
            b_d,
            geo.alinear_rasters(
                (b_a["rojo"], tf, crs), *[(v, tf_d, crs) for v in b_d.values()]
            ),
        )
    )
    dndvi = geo.indice_vegetacion("ndvi", **b_d) - geo.indice_vegetacion("ndvi", **b_a)
    contorno = geo.reproyectar(predio, crs)
    p_antes = geo.panel_rgb(
        b_a, tf, crs, "ANTES · " + geo.rotulo_escenas(inf), contorno=contorno
    )
    p_desp = geo.panel_rgb(
        b_d, tf, crs, "DESPUÉS · " + geo.rotulo_escenas(inf), contorno=contorno
    )
    p_cambio = geo.panel_continuo(
        dndvi,
        tf,
        crs,
        "Cambio de NDVI",
        paleta="divergente",
        unidad="dNDVI",
        contorno=contorno,
    )
    info = geo.lamina(
        [p_antes, p_desp, p_cambio],
        tmp_path / "LAMINA.png",
        titulo="Predio · antes y después del evento",
        cita=geo.cita_sentinel2(inf, inf),
    )
    assert info["paneles"][0]["estirado"] == info["paneles"][1]["estirado"]
    vmin, vmax = info["paneles"][2]["estirado"]
    assert vmin == pytest.approx(-vmax)
