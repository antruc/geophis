"""Tests de geophis.barridos: lo que MIDE sobre el terreno y los barridos.

`raster_distancia`, `separaciones`, `barrer_vecinos`, `barrer_resolucion`,
`histograma_fase` y `amplitud_rizo`. Las reglas puras (techos, piso, UMM, margen) estan en
`test_metrologia.py`.

Los montajes compartidos con `test_raster.py` (`_gdf_box`, `_curvas_rampa`)
viven en `conftest.py`, que es lo unico que los dos archivos comparten.
"""

import geopandas as gpd
import numpy as np
import pytest
from rasterio.transform import Affine
from shapely.geometry import LineString, Point, box

import geophis as geo
from geophis.barridos import _avisos_rodilla
from conftest import _curvas_rampa, _gdf_box

UTM = 32613


# ---- barrer_vecinos: la rodilla ---------------------------------------------


def _zona_rampa():
    return _gdf_box(0, 0, 30, 30)


def test_barrer_vecinos_devuelve_una_fila_por_n_y_la_rodilla():
    filas, n = geo.barrer_vecinos(
        _curvas_rampa(),
        "cota",
        _zona_rampa(),
        resolucion=5,
        intervalo_terreno=5,
        s_vacio=20,
        enes=(8, 16),
        zigzag=1.0,
    )
    assert [f["vecinos"] for f in filas] == [8, 16]
    assert n in (8, 16)
    # el margen crece con `n`: mas vecinos piden mas curvas de fuera
    assert filas[1]["margen_borde_celdas"] > filas[0]["margen_borde_celdas"]


def test_barrer_vecinos_el_extent_no_se_mueve_entre_corridas():
    """`n` tiene que ser la UNICA variable.

    Solo crece `cuadro_recorte` (cuantas curvas de FUERA sostienen el borde);
    `cuadro_salida` es el bbox de `zona` y es el mismo siempre. Si el extent se
    moviera, el conteo de celdas cambiaria y la clase alta no compararia.
    """
    filas, _ = geo.barrer_vecinos(
        _curvas_rampa(),
        "cota",
        _zona_rampa(),
        resolucion=5,
        intervalo_terreno=5,
        s_vacio=20,
        enes=(8, 16),
        zigzag=1.0,
    )
    assert len({f["n_celdas"] for f in filas}) == 1


def test_barrer_vecinos_la_rodilla_no_depende_de_donde_acabe_enes():
    """El barrido para al converger: extender la lista no mueve la rodilla,
    porque no llega ahi, y los `n` de mas no se pagan. Un criterio anclado al
    minimo de la rejilla la moveria (en un predio real: hasta 90 daria 64, hasta 122
    daria 122).

    La rampa es de 100 %: con `p_max` 0.5 toda la zona es clase alta y la
    cifra no se mueve con `n`, asi que converge en el primer par.
    """
    kw = dict(resolucion=5, intervalo_terreno=5, s_vacio=20, zigzag=1.0, p_max=0.5)
    filas_c, n_c = geo.barrer_vecinos(_curvas_rampa(), "cota", _zona_rampa(), enes=(8, 16), **kw)
    filas_l, n_l = geo.barrer_vecinos(
        _curvas_rampa(), "cota", _zona_rampa(), enes=(8, 16, 32, 64), **kw
    )
    assert filas_c[0]["alta_ha"] > 0
    assert n_c == n_l == 8 and not n_l.avisos
    assert [f["vecinos"] for f in filas_l] == [8, 16], "paro al converger"


def test_barrer_vecinos_avisa_si_la_rodilla_cae_en_el_borde(capsys):
    """Rodilla == el `n` mas grande de la lista puede no ser rodilla, sino borde."""
    geo.barrer_vecinos(
        _curvas_rampa(),
        "cota",
        _zona_rampa(),
        resolucion=5,
        intervalo_terreno=5,
        s_vacio=20,
        enes=(8,),
        zigzag=1.0,
        p_max=0.5,  # la rampa es de 100 %: con 1.0 no hay clase alta
    )
    assert "borde de la lista" in capsys.readouterr().out


def test_barrer_vecinos_avisa_aunque_MOSTRAR_sea_False(capsys, monkeypatch):
    """`geo.MOSTRAR` decide si se imprime la TABLA, no si la cifra vale.

    Con la guarda del borde DENTRO del `if mostrar`, una corrida de
    produccion con `geo.MOSTRAR = False` sacaria la fila de procedencia `122 medido rodilla de barrer_vecinos` con la misma cara
    que tendria una rodilla real. Un aviso que no sale siempre no es un aviso.
    """
    monkeypatch.setattr(geo, "MOSTRAR", False)
    _, rodilla = geo.barrer_vecinos(
        _curvas_rampa(),
        "cota",
        _zona_rampa(),
        resolucion=5,
        intervalo_terreno=5,
        s_vacio=20,
        enes=(8,),
        zigzag=1.0,
        p_max=0.5,  # la rampa es de 100 %: con 1.0 no hay clase alta
    )
    salida = capsys.readouterr().out
    assert "borde de la lista" in salida
    # la TABLA si se calla: es lo unico que `geo.MOSTRAR` puede decidir
    assert "rodilla: n=" not in salida
    # y el aviso viaja ademas en el resultado, para quien no lee la consola
    assert rodilla.avisos and rodilla == 8


def test_el_aviso_mira_si_la_clase_alta_convergio():
    """Sin convergencia no hay rodilla, aunque el barrido devuelva un numero.

    Las filas son las del `>100` del spline en un predio real (160.57 / 157.28 /
    155.96 ha con n = 48 / 90 / 122). Lo que se fija aqui es el CRITERIO, que
    es aritmetica pura; un sintetico calibra, no descubre.
    """
    filas = [
        {"vecinos": 48, "alta_ha": 160.57, "delta_pct": float("nan")},
        {"vecinos": 90, "alta_ha": 157.28, "delta_pct": -2.05},
    ]
    avisos = _avisos_rodilla(filas, 0.01)
    assert len(avisos) == 1 and "NO convergio" in avisos[0]
    # un solo `n` no tiene con que compararse
    assert _avisos_rodilla(filas[:1], 0.01)
    filas.append({"vecinos": 122, "alta_ha": 155.96, "delta_pct": -0.84})
    assert _avisos_rodilla(filas, 0.01) == []


def test_sin_clase_alta_no_hay_rodilla():
    """Un predio real a h=50 m: 0 ha de `>100` en n=20 y n=32 "convergia" y salia
    `vecinos 20 medido`, con la meseta cayendo de 310 a 236 ha."""
    filas = [
        {"vecinos": 20, "alta_ha": 0.0, "delta_pct": float("nan")},
        {"vecinos": 32, "alta_ha": 0.0, "delta_pct": 0.0},
    ]
    avisos = _avisos_rodilla(filas, 0.01)
    assert len(avisos) == 1 and "0 ha" in avisos[0]


def test_barrer_vecinos_valida_las_entradas():
    zona = _zona_rampa()
    with pytest.raises(ValueError, match="deben ser > 0"):
        geo.barrer_vecinos(
            _curvas_rampa(),
            "cota",
            zona,
            resolucion=5,
            intervalo_terreno=0,
            s_vacio=20,
        )
    with pytest.raises(ValueError, match="tolerancia"):
        geo.barrer_vecinos(
            _curvas_rampa(),
            "cota",
            zona,
            resolucion=5,
            intervalo_terreno=5,
            s_vacio=20,
            tolerancia=1.0,
        )


def test_barrer_resolucion_tabula_una_fila_por_h(gdf, cuadrado):
    """La rampa no tiene rizo, asi que la clase alta sale vacia a los dos h.

    Lo que fija el test es el contrato: una fila por h, de fino a grueso,
    min_pixeles derivado de h, y delta_pct NaN en la primera.

    h=9.5 esta ahi para pinchar el redondeo: 100/90.25 = 1.108, que con `round`
    da 1 (una mancha de 90 m2 pasa una UMM de 100) y con `ceil` da 2. Es el
    unico h de los tres donde las dos reglas difieren.
    """
    predio = gdf(cuadrado(0, 0, 30))
    filas = geo.barrer_resolucion(
        _curvas_rampa(),
        "cota",
        predio,
        rangos=[("0-100", 0, 100), (">100", 100, float("inf"))],
        umm_m2=100,
        resoluciones=[5, 9.5, 10],
    )
    assert [f["h"] for f in filas] == [5.0, 9.5, 10.0]
    assert [f["min_pixeles"] for f in filas] == [4, 2, 1]  # ceil de 4, 1.108, 1
    assert filas[0]["delta_pct"] != filas[0]["delta_pct"]  # NaN, no hay previo
    assert all(f["area_alta_ha"] == 0 for f in filas)  # rampa lineal, sin >100


def test_barrer_resolucion_reporta_banda_y_no_dictamina(gdf, cuadrado, capsys):
    """Imprime la BANDA de la cifra, no un veredicto de resolubilidad.

    Dictaminar "no hay h que la sostenga" a partir de `delta_pct` contra un
    umbral se equivoca: esa columna arrastra el escalon entero de
    `min_pixeles` y se compararia contra el piso GLOBAL. El veredicto vive en `sostenimiento_por_clase`. Si alguien
    lo devuelve aqui, este test truena.
    """
    predio = gdf(cuadrado(0, 0, 30))
    geo.barrer_resolucion(
        _curvas_rampa(),
        "cota",
        predio,
        rangos=[("0-100", 0, 100), (">100", 100, float("inf"))],
        umm_m2=100,
        resoluciones=[5, 10],
    )
    salida = capsys.readouterr().out
    assert "SIN MESETA" not in salida
    assert "no hay `h` que la sostenga" not in salida
    assert "min_pixeles" in salida  # avisa del escalon que ensucia delta_pct


def test_barrer_resolucion_min_pixeles_fijo_no_lo_deriva(gdf, cuadrado):
    """`min_pixeles=<int>` lo deja igual en todas las filas.

    Sin el, `ceil(100/h^2)` daria 4 y 1: el escalon cae dentro de `delta_pct` y
    lo vuelve ilegible. Con el fijo, `h` es la unica variable.
    """
    predio = gdf(cuadrado(0, 0, 30))
    filas = geo.barrer_resolucion(
        _curvas_rampa(),
        "cota",
        predio,
        rangos=[("0-100", 0, 100), (">100", 100, float("inf"))],
        umm_m2=100,
        resoluciones=[5, 10],
        min_pixeles=3,
    )
    assert [f["min_pixeles"] for f in filas] == [3, 3]


def test_barrer_resolucion_hs_por_defecto_sale_de_lambda(gdf, cuadrado):
    """Sin `resoluciones`, el barrido bracketea el piso: lambda/4 a lambda."""
    predio = gdf(cuadrado(0, 0, 30))
    filas = geo.barrer_resolucion(
        _curvas_rampa(),
        "cota",
        predio,
        rangos=[("0-100", 0, 100), (">100", 100, float("inf"))],
        umm_m2=100,
        lambda_m=20.0,
    )
    assert [f["h"] for f in filas] == [5.0, 6.7, 10.0, 13.3, 20.0]


def _curvas_escalon():
    """Ladera de 50 % con un escalon de 300 % entre y=40 y y=50: la clase alta
    es una franja de ~10 x 100 m (~160 px a 2.5 m) contra una UMM de 2000 m2
    (320 px). Las dos laderas, ~640 px cada una, pasan la UMM: si no, el sieve
    no tiene vecino grande donde absorber y la franja sobrevive igual."""
    lineas = [LineString([(0, y), (100, y)]) for y in (0, 40, 50, 90)]
    return gpd.GeoDataFrame({"cota": [0, 20, 50, 70]}, geometry=lineas, crs=32613)


def test_barrer_resolucion_exentos_protege_la_clase_del_sieve(gdf, cuadrado):
    """Con la clase alta exenta, la franja sobrevive al sieve; sin ella, la
    absorbe el vecino. Es la diferencia entre barrer la receta TIN de
    produccion (`limpiar_moteado(..., exentos=[alta])`) y barrer otra."""
    kw = dict(
        rangos=[("0-100", 0, 100), (">100", 100, float("inf"))],
        umm_m2=2000,
        resoluciones=[2.5],
        metodo="tin",
    )
    predio = gdf(box(0, 0, 100, 90))
    sin = geo.barrer_resolucion(_curvas_escalon(), "cota", predio, **kw)
    con = geo.barrer_resolucion(_curvas_escalon(), "cota", predio, exentos=[">100"], **kw)
    assert sin[0]["area_alta_ha"] == 0
    assert con[0]["area_alta_ha"] > 0


def test_barrer_resolucion_tin_valida_entradas(gdf, cuadrado):
    predio = gdf(cuadrado(0, 0, 30))
    kw = dict(rangos=[("0-100", 0, 100), (">100", 100, float("inf"))], umm_m2=100)
    with pytest.raises(ValueError, match="pasa `resoluciones`"):
        geo.barrer_resolucion(_curvas_rampa(), "cota", predio, metodo="tin", **kw)
    with pytest.raises(ValueError, match="no estan en `rangos`"):
        geo.barrer_resolucion(
            _curvas_rampa(), "cota", predio, resoluciones=[5], exentos=["nope"], **kw
        )


def test_barrer_resolucion_pasa_los_quiebres(gdf, cuadrado):
    """Los quiebres llegan a `interpolar_mde`: con spline, ella los rechaza."""
    predio = gdf(cuadrado(0, 0, 30))
    cauce = gdf(LineString([(15, -5), (15, 35)]))
    kw = dict(
        rangos=[("0-100", 0, 100), (">100", 100, float("inf"))],
        umm_m2=100,
        resoluciones=[5],
        quiebres=cauce,
    )
    with pytest.raises(ValueError):
        geo.barrer_resolucion(_curvas_rampa(), "cota", predio, **kw)
    filas = geo.barrer_resolucion(_curvas_rampa(), "cota", predio, metodo="tin", **kw)
    assert len(filas) == 1


# ---- A1: raster_distancia / separaciones ------------------------------------


def test_raster_distancia_cero_sobre_la_curva(gdf, cuadrado):
    zona = gdf(cuadrado(0, 0, 100))
    curvas = gdf(LineString([(0, 50), (100, 50)]))
    distancia, mascara, transform = geo.raster_distancia(curvas, zona, resolucion=5)
    assert distancia.shape == mascara.shape
    assert mascara.sum() == 400  # 100x100 a 5 m/px
    d_zona = distancia[mascara]
    assert d_zona.min() < 5  # celda pegada a la curva
    assert d_zona.max() == pytest.approx(50, abs=5)  # borde de la zona, lejos de la curva


def test_raster_distancia_sin_curvas_en_la_malla_falla(gdf, cuadrado):
    zona = gdf(cuadrado(0, 0, 100))
    curvas = gdf(LineString([(1000, 1000), (1000, 1100)]))  # fuera de la malla
    with pytest.raises(ValueError, match="ninguna curva cae en la malla"):
        geo.raster_distancia(curvas, zona, resolucion=5)


def test_separaciones_estructura_basica(gdf, cuadrado):
    zona = gdf(cuadrado(0, 0, 100))
    curvas = gdf(*[LineString([(0, y), (100, y)]) for y in (0, 20, 40, 60, 80, 100)])
    lam, _, zigzag = geo.separacion_media(curvas, zona, paso=2.5)
    distancia, mascara, _ = geo.raster_distancia(curvas, zona, resolucion=5)
    tabla, resumen = geo.separaciones(distancia, mascara, 5, lam, zigzag)
    assert tabla  # hay curvas: la tabla no sale vacia
    assert resumen["q50"] < resumen["q90"]
    # con cortes derivados la cifra seria 0.5% por construccion: sale NaN
    assert resumen["area_sin_curvas_ha"] != resumen["area_sin_curvas_ha"]
    # con un corte explicito si significa algo: todo esta a menos de 100 m
    _, con_corte = geo.separaciones(distancia, mascara, 5, lam, zigzag, cortes=[100.0])
    assert con_corte["area_sin_curvas_ha"] == pytest.approx(0.0)


def test_separaciones_sin_curvas_da_nan():
    forma = (20, 20)
    # `separaciones` lee del raster de distancia, no de una malla: no hay
    # transform que pasarle
    distancia = np.full(forma, 1e9)
    mascara = np.ones(forma, dtype=bool)
    tabla, resumen = geo.separaciones(distancia, mascara, 5, float("nan"), float("nan"))
    assert tabla == []
    assert resumen["lambda_armonica"] != resumen["lambda_armonica"]


# ---- A8: histograma_fase ------------------------------------------------------


def _curvas_dos_cotas(cotas=(0.0, 10.0)):
    """GeoDataFrame minimo solo para que histograma_fase detecte campo/e."""
    import geopandas as gpd

    return gpd.GeoDataFrame(
        {"cota": list(cotas)}, geometry=[box(0, 0, 1, 1)] * len(cotas), crs=UTM
    )


def test_histograma_fase_fase_uniforme_da_evacuacion_1():
    # z construido a mano cubriendo 100 periodos completos de e=10 de forma
    # densa y pareja: por definicion la fase sale uniforme, evacuacion ~ 1.0
    curvas = _curvas_dos_cotas((0.0, 10.0))
    z = np.linspace(0.0, 1000.0, 200_000, endpoint=False)  # 100 periodos de e=10
    mde = z.reshape(1, -1)
    densidad, exceso_ha, evacuacion, atomo_ha, n_completos = geo.histograma_fase(
        mde, curvas, "cota", resolucion=1.0
    )
    assert evacuacion == pytest.approx(1.0, abs=0.02)
    assert exceso_ha == pytest.approx(0.0, abs=0.5)
    assert len(densidad) > 0


def test_histograma_fase_ancho_bin_no_divide_falla():
    curvas = _curvas_dos_cotas((0.0, 10.0))
    with pytest.raises(ValueError, match="no divide"):
        geo.histograma_fase(
            np.zeros((2, 2)), curvas, "cota", resolucion=1.0, ancho_bin=3.0
        )


def test_histograma_fase_menos_de_dos_cotas_falla():
    curvas = _curvas_dos_cotas((0.0,))
    with pytest.raises(ValueError, match="menos de dos cotas"):
        geo.histograma_fase(np.zeros((2, 2)), curvas, "cota", resolucion=1.0)


# ---- A9: amplitud_rizo --------------------------------------------------------
#
# Rampa al este con curvas verticales cada 20 m de cota y un rizo COSENO de
# periodo igual a la separacion entre curvas y fase puesta para que su maximo
# caiga SOBRE las curvas. Asi la excursion fuera de [z_lo, z_hi] vale casi `A`
# justo al lado de cada curva, que es donde la funcion la busca. Con el seno
# (maximo a mitad de banda) la excursion seria 0 y el montaje no probaria nada.

_H_RIZO, _E_RIZO, _X0_RIZO = 0.5, 20.0, 0.25  # celda, equidistancia, x de la 1a curva


def _malla_rampa_con_rizo(amplitud):
    """(mde, transform, curvas) de una rampa z = x con rizo de amplitud conocida."""
    ncol, nrow = 200, 20  # 100 m x 10 m a 0.5 m/px
    tf = Affine(_H_RIZO, 0, 0, 0, -_H_RIZO, nrow * _H_RIZO)
    x = (np.arange(ncol) + 0.5) * _H_RIZO
    z = x + amplitud * np.cos(2 * np.pi * (x - _X0_RIZO) / _E_RIZO)
    mde = np.tile(z, (nrow, 1))
    xs = [_X0_RIZO + i * _E_RIZO for i in range(5)]  # 0.25, 20.25, ... 80.25
    curvas = gpd.GeoDataFrame(
        {"COTA": xs},
        geometry=[LineString([(cx, -10), (cx, 20)]) for cx in xs],
        crs=UTM,
    )
    return mde, tf, curvas


def _malla_rampa_con_rizo_seno(amplitud):
    """Igual, pero con el rizo en SENO: cero sobre las curvas, maximo a mitad.

    Es la geometria REAL de un interpolador, que pasa por el dato: el rizo se
    anula donde hay curva y ondula entre curva y curva. La de arriba usa coseno
    con la fase puesta para que el maximo caiga SOBRE la curva, que es donde la
    funcion sabe mirar.
    """
    ncol, nrow = 200, 20
    tf = Affine(_H_RIZO, 0, 0, 0, -_H_RIZO, nrow * _H_RIZO)
    x = (np.arange(ncol) + 0.5) * _H_RIZO
    z = x + amplitud * np.sin(2 * np.pi * (x - _X0_RIZO) / _E_RIZO)
    xs = [_X0_RIZO + i * _E_RIZO for i in range(5)]
    curvas = gpd.GeoDataFrame(
        {"COTA": xs},
        geometry=[LineString([(cx, -10), (cx, 20)]) for cx in xs],
        crs=UTM,
    )
    return np.tile(z, (nrow, 1)), tf, curvas


def test_amplitud_rizo_es_CIEGA_al_rizo_que_no_se_sale_de_la_banda():
    """El techo del metodo: con el rizo en su fase REAL no ve nada.

    El montaje de coseno de aqui arriba pone el maximo del rizo SOBRE la curva
    para que la excursion asome; su propio comentario ya decia que "con el seno
    la excursion seria 0 y el montaje no probaria nada". Lo que faltaba era
    sacar la consecuencia: **el seno es el caso real.** Un interpolador pasa por
    el dato, asi que el rizo vale cero en la curva y es maximo a mitad de banda,
    que es justo donde `[z_lo, z_hi]` lo tapa.

    Medido en un sintetico a escala de predio (rampa + rizo sinusoidal, e=20):
    `A` de 2, 4, 8, 9, 10, 11 y 12 m dan TODAS 0.00 m, y el primer punto que se
    ve, `A`=16.669, recupera el 11.8 %. Identico en los dos regimenes, o sea
    que depende de `A/e` y no del predio.

    Consecuencia, y es lo que cierra B: la ceguera NO es de la discriminante
    (esta rampa no tiene ni un lomo sin curva), asi que el test de rayo por
    pixel no puede arreglarla.
    """
    a_grande = 4.0  # el 20 % de la equidistancia, y aun asi invisible
    mde, tf, curvas = _malla_rampa_con_rizo_seno(a_grande)
    a, detalle = geo.amplitud_rizo(mde, tf, curvas, "COTA")
    assert a == pytest.approx(0.0, abs=1e-9)
    assert detalle["frac_excursion"] == pytest.approx(0.0, abs=1e-9)
    # y no es que no haya pixeles medidos: la discriminante funciona
    assert detalle["frac_usada"] > 0.7

    # con la MISMA amplitud y la fase que la funcion sabe mirar, si la ve
    mde_cos, tf_cos, curvas_cos = _malla_rampa_con_rizo(a_grande)
    a_cos, _ = geo.amplitud_rizo(mde_cos, tf_cos, curvas_cos, "COTA")
    assert a_cos > 0.5 * a_grande


def test_amplitud_rizo_recupera_A_cuando_el_rizo_asoma_fuera_de_la_banda():
    """El piso tiene que quedar por DEBAJO de `A` y cerca de ella.

    OJO AL ALCANCE, que el nombre viejo se comia (era
    `test_amplitud_rizo_recupera_una_amplitud_conocida`, y prometia mas de lo
    que cubre): esto vale con el rizo en la fase que ASOMA fuera de la banda.
    Con la fase real no recupera nada, ver el test de arriba.

    Por debajo siempre: es un piso, y si alguna vez sale por encima de la
    amplitud real la funcion esta midiendo otra cosa. Cerca porque el rizo de
    este montaje asoma entero fuera de la banda; lo que falta hasta `A` es el
    medio pixel entre el centro de celda y la curva.
    """
    amplitud = 5.0
    mde, tf, curvas = _malla_rampa_con_rizo(amplitud)
    a, detalle = geo.amplitud_rizo(mde, tf, curvas, "COTA")
    assert a <= amplitud  # PISO: nunca por encima de la amplitud real
    assert a == pytest.approx(amplitud, rel=0.2)
    assert detalle["excursion_max"] <= amplitud
    # 0.78 medido: la rampa no tiene cimas, pero la franja al este de la ultima
    # curva (20 m de 100) no tiene cota por encima y queda fuera, con razon.
    assert detalle["frac_usada"] > 0.7


def test_amplitud_rizo_sin_rizo_da_cero():
    """Sin rizo el MDE es el plano y no se sale de la banda en ningun pixel."""
    mde, tf, curvas = _malla_rampa_con_rizo(0.0)
    a, detalle = geo.amplitud_rizo(mde, tf, curvas, "COTA")
    assert a == pytest.approx(0.0, abs=1e-9)
    assert detalle["excursion_max"] == pytest.approx(0.0, abs=1e-9)


def test_amplitud_rizo_no_cuenta_la_cima_como_excursion(capsys):
    """LA DISCRIMINANTE. Un cono exacto, sin rizo ninguno: la cifra tiene que
    salir 0.

    Dentro de la curva de cumbre no hay cota por encima, y las dos curvas mas
    cercanas caen las dos hacia afuera. Si se tomaran como banda, esos pixeles
    darian hasta 20 m de excursion inventada sobre una superficie que no ondula
    en absoluto, y la funcion mediria la hipsometria en vez del interpolador.
    """
    n, h = 200, 1.0
    tf = Affine(h, 0, 0, 0, -h, n * h)
    x = (np.arange(n) + 0.5) * h
    xx, yy = np.meshgrid(x, x[::-1])
    r = np.hypot(xx - 100, yy - 100)
    mde = 100 - r  # cono: cima 100 en el centro, 20 en r = 80
    radios = [80, 60, 40, 20]
    curvas = gpd.GeoDataFrame(
        {"COTA": [100 - rad for rad in radios]},
        geometry=[Point(100, 100).buffer(rad).exterior for rad in radios],
        crs=UTM,
    )
    a, detalle = geo.amplitud_rizo(mde, tf, curvas, "COTA")
    assert a == pytest.approx(0.0, abs=0.5)
    # y descarto de verdad: la cima y el exterior no tienen envolvente
    assert detalle["frac_usada"] < 0.9
    # frac_usada 0.45 < 0.5: el aviso sale aunque `geo.MOSTRAR = False`
    assert "Aviso: amplitud_rizo" in capsys.readouterr().out


def test_juzgar_clasificaciones_veredicto_por_metodo_y_recomienda_por_largo_visto():
    """El juez: criterio de SU metodo, y gana el que ve MAS escarpe.

    Escarpe sintetico: curvas cada 12 m con 20 m de equidistancia (167 %), asi
    que toda la zona es `>100` y `s_sostenida` = 12 m. Cuatro candidatas:

        TODO    tin, h=3, toda la zona      SI, ve el 100 %
        MITAD   tin, h=3, media zona        SI, ve el 50 %: pierde contra TODO
        GRUESO  tin, h=20                   NO: 20 > 12, el objeto no cabe
        ALIAS   spline, h=3                 CONDICIONAL: piso 6 > 3. El MISMO
                                            h que en TODO pasa: el piso es del
                                            metodo, no del predio
    """
    crs = "EPSG:32613"
    curvas = gpd.GeoDataFrame(
        {"COTA": [i * 20.0 for i in range(11)]},
        geometry=[LineString([(0, i * 12), (400, i * 12)]) for i in range(11)],
        crs=crs,
    )
    zona = gpd.GeoDataFrame(geometry=[box(20, 20, 380, 100)], crs=crs)
    rangos = [("0-100", 0, 100), (">100", 100, float("inf"))]

    def clases(x_corte):
        return gpd.GeoDataFrame(
            {"PENDIENTES": [">100", "0-100"]},
            geometry=[box(20, 20, x_corte, 100), box(x_corte, 20, 380, 100)],
            crs=crs,
        )

    filas, recomendada = geo.juzgar_clasificaciones(
        {
            "TODO": (clases(380), "tin", 3.0),
            "MITAD": (clases(200), "tin", 3.0),
            "GRUESO": (clases(380), "tin", 20.0),
            "ALIAS": (clases(380), "spline", 3.0),
        },
        curvas,
        "COTA",
        zona,
        rangos,
        20.0,
        1.0,
    )
    f = {x["nombre"]: x for x in filas}
    assert f["TODO"]["s_sostenida"] == pytest.approx(12.0, rel=0.05)
    assert [f[n]["veredicto"] for n in ("TODO", "MITAD", "GRUESO", "ALIAS")] == [
        "SI",
        "SI",
        "NO",
        "CONDICIONAL",
    ]
    assert f["TODO"]["pct_visto"] == pytest.approx(100, abs=2)
    assert f["MITAD"]["pct_visto"] == pytest.approx(50, abs=2)
    assert f["GRUESO"]["corte"] is None  # un solo corte, y no pasa
    assert recomendada == "TODO"
    with pytest.raises(ValueError, match="metodo"):
        geo.juzgar_clasificaciones(
            {"X": (clases(380), "idw", 3.0)}, curvas, "COTA", zona, rangos, 20.0, 1.0
        )
