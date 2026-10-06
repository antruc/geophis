"""MDE y pendiente contra VERDAD ANALITICA, no contra sí mismos.

El resto de la suite comprueba que la cadena corre, que las guardas cierran y
que las cifras no se mueven. Esto comprueba que el número sea el CORRECTO. Aquí las superficies tienen pendiente conocida en forma cerrada, y
lo que se compara es contra ella:

- un plano inclinado: pendiente constante, y sus curvas de nivel son rectas
  paralelas que se pueden construir exactas.
- un cono: pendiente constante en todas partes, y sus curvas son círculos, o
  sea el caso curvo, que es donde el interpolador tiene algo que inventar.

Las dos partes van separadas a propósito. La A evalúa `calcular_pendiente`
sobre un MDE analítico, sin interpolar: si falla, el defecto está en Horn, en
las unidades o en el `transform`. La B recorre la cadena entera desde curvas con
los parámetros DERIVADOS: si la A pasa y la B no, el defecto está en el MDE.
"""

import numpy as np
import geopandas as gpd
import pytest
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, box

import geophis as geo

UTM = 32613
H = 5.0
RANGOS = [("0-5", 0, 5), ("5-30", 5, 30), ("30-70", 30, 70), (">100", 100, float("inf"))]


def _malla(n=120, h=H):
    """(X, Y, transform) de una malla regular con el origen arriba-izquierda."""
    X, Y = np.meshgrid(np.arange(n) * h, np.arange(n) * h)
    return X, Y, from_origin(0, (n - 1) * h, h, h)


def _interior(arr, borde=2):
    """Quita el borde: ahí la regla NoData 7 de 8 deja NaN a propósito."""
    return arr[borde:-borde, borde:-borde]


# ── A. calcular_pendiente sobre MDE analitico ───────────────────────────


@pytest.mark.parametrize(
    "nombre, coef_x, coef_y, real_pct",
    [
        ("50% en x", 0.50, 0.00, 50.0),
        ("50% en y", 0.00, 0.50, 50.0),
        ("30%x + 40%y (pitagoras)", 0.30, 0.40, 50.0),
        ("100%, el limite de clase", 0.00, 1.00, 100.0),
        ("141.42%, mas de 45 grados", 1.4142, 0.00, 141.42),
    ],
)
def test_pendiente_exacta_sobre_planos_analiticos(nombre, coef_x, coef_y, real_pct):
    """Horn sobre un plano tiene que dar la pendiente del plano, sin holgura.

    El caso `30%x + 40%y` no es decorativo: comprueba que las dos derivadas se
    componen por hipotenusa y no se suman ni se toma la mayor.
    """
    X, Y, tf = _malla()
    z = coef_x * X + coef_y * Y
    pct = _interior(geo.calcular_pendiente(z, tf))
    grados = _interior(geo.calcular_pendiente(z, tf, unidad="grados"))
    assert np.nanmedian(pct) == pytest.approx(real_pct, abs=1e-6)
    assert np.nanmedian(grados) == pytest.approx(
        np.degrees(np.arctan(real_pct / 100)), abs=1e-6
    )


def test_z_factor_corrige_z_en_pies():
    """Sin `z_factor`, z en pies infla la pendiente 3.28 veces EN SILENCIO."""
    X, _, tf = _malla()
    z_pies = (0.50 * X) / 0.3048
    sin_factor = np.nanmedian(_interior(geo.calcular_pendiente(z_pies, tf)))
    con_factor = np.nanmedian(
        _interior(geo.calcular_pendiente(z_pies, tf, z_factor=0.3048))
    )
    assert con_factor == pytest.approx(50.0, abs=1e-6)
    assert sin_factor == pytest.approx(50.0 / 0.3048, rel=1e-6)


def test_regla_nodata_7_de_8():
    """Centro NoData -> NoData; menos de 7 de 8 vecinos validos -> NoData.

    Y el borde del raster entero sale NaN, que es lo que hace falta recortar
    antes de citar superficies.
    """
    X, _, tf = _malla(n=60)
    z = 0.5 * X
    z[30, 30] = np.nan
    p = geo.calcular_pendiente(z, tf)
    assert np.isnan(p[30, 30])
    assert np.isnan(p[0, 0]) and np.isnan(p[-1, -1])
    # el vecino SI se computa, con el NaN sustituido por el centro (regla 7 de 8),
    # asi que da un valor MENOR que el real sin quedar marcado. Documentado.
    assert np.isfinite(p[30, 31]) and p[30, 31] < 50.0


@pytest.mark.parametrize(
    "nombre, eje, signo, azimut, clase",
    [
        ("sube al este, mira al oeste", "x", +1, 270.0, 4),
        ("sube al oeste, mira al este", "x", -1, 90.0, 2),
        ("sube al sur, mira al norte", "y", +1, 0.0, 1),
        ("sube al norte, mira al sur", "y", -1, 180.0, 3),
    ],
)
def test_aspecto_exacto_en_las_cuatro_orientaciones(nombre, eje, signo, azimut, clase):
    """`calcular_orientacion` comparte la derivada con `calcular_pendiente`.

    Convencion documentada: azimut 0=N, 90=E, 180=S, 270=O, y clase 1=N, 2=E,
    3=S, 4=O. El aspecto mira CUESTA ABAJO, o sea al contrario de donde sube.
    """
    X, Y, tf = _malla(n=60)
    base = X if eje == "x" else Y
    z = 0.5 * (base if signo > 0 else base.max() - base)
    g = np.nanmedian(_interior(geo.calcular_orientacion(z, tf, unidad="grados")))
    c = np.median(_interior(geo.calcular_orientacion(z, tf)))
    assert g == pytest.approx(azimut, abs=1e-6)
    assert int(c) == clase


# ── B. la cadena entera desde curvas, con parametros derivados ──────────


def _pendiente_de_curvas(curvas, zona, campo="COTA"):
    """Deriva los parametros, interpola y devuelve la pendiente dentro de `zona`."""
    p = geo.derivar_parametros(curvas, campo, zona, RANGOS, 25_000, vecinos=48)
    h = float(p["resolucion"])
    cuadro_recorte, cuadro_salida = geo.cuadros_mde(
        zona, resolucion=h, margen_borde_celdas=int(p["margen_borde_celdas"])
    )
    mde, tf = geo.interpolar_mde(
        geo.recortar(curvas, cuadro_recorte),
        campo,
        resolucion=h,
        equidistancia=float(p["equidistancia"]),
        intervalo_muestreo=float(p["intervalo_muestreo"]),
        vecinos=48,
        cuadro=cuadro_salida,
    )
    pend = geo.calcular_pendiente(mde, tf)
    dentro = geo.rasterizar(zona, pend.shape, tf) == 1
    return pend[dentro & np.isfinite(pend)]


def test_cadena_desde_curvas_rectas_da_la_pendiente_real():
    """Plano al 50 %: curvas rectas paralelas cada 40 m con 20 m de cota.

    Es el caso facil y por eso la tolerancia es estrecha: si esto se desvia, el
    problema no es el relieve, es la cadena.
    """
    ys = np.arange(0, 1201, 40.0)
    curvas = gpd.GeoDataFrame(
        {"COTA": ys * 0.5},
        geometry=[LineString([(0, y), (1200, y)]) for y in ys],
        crs=UTM,
    )
    zona = gpd.GeoDataFrame(geometry=[box(200, 200, 1000, 1000)], crs=UTM)
    v = _pendiente_de_curvas(curvas, zona)
    assert np.median(v) == pytest.approx(50.0, rel=0.005)
    assert np.percentile(v, 90) == pytest.approx(50.0, rel=0.02)


def test_cadena_desde_curvas_circulares_da_la_pendiente_real():
    """Cono al 50 %: las curvas son circulos, o sea el caso CURVO.

    Un cono tiene la misma pendiente en todas partes, asi que cualquier
    ondulacion del interpolador entre curvas se ve directamente como desviacion.
    Es la version chica del defecto que infla la clase mas alta en produccion.
    """
    radios = np.arange(40, 1401, 40.0)
    curvas = gpd.GeoDataFrame(
        {"COTA": radios * 0.5},
        geometry=[Point(1500, 1500).buffer(r, quad_segs=64).exterior for r in radios],
        crs=UTM,
    )
    zona = gpd.GeoDataFrame(geometry=[box(1100, 1100, 1900, 1900)], crs=UTM)
    v = _pendiente_de_curvas(curvas, zona)
    assert np.median(v) == pytest.approx(50.0, rel=0.01)
    assert np.percentile(v, 90) == pytest.approx(50.0, rel=0.02)
