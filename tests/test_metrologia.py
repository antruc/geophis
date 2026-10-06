"""Tests de geophis.metrologia: las REGLAS, sin tocar un raster.

Los techos (`resolucion_regla`), el `h` que sale de ellos, la
UMM en pixeles (`min_pixeles_umm`) y el margen de borde
(`margen_borde_celdas`). Aritmetica pura: ningun test de aqui interpola,
rasteriza ni escribe a disco, y por eso no necesita montajes geometricos.

Lo que barre y mide esta en `test_barridos.py`.
"""

import pytest

import geophis as geo


# ---- las dos reglas que los scripts ya no copian ----------------------------


def test_min_pixeles_umm_es_ceil_no_round():
    """`round(1.1)=1` dejaria pasar manchas de 361 m2 con UMM 400.

    La UMM es un MINIMO: lo que no la alcanza no se entrega, y redondear hacia
    abajo lo entrega.
    """
    assert geo.min_pixeles_umm(400, 19) == 2  # 400/361 = 1.108 -> 2, no 1
    assert geo.min_pixeles_umm(400, 25.3) == 1
    assert geo.min_pixeles_umm(2500, 5) == 100  # la corrida entregable
    with pytest.raises(ValueError, match="deben ser > 0"):
        geo.min_pixeles_umm(0, 5)


def test_margen_borde_celdas_reproduce_la_corrida_entregable():
    """n=90 (rodilla), i=20 (terreno), s=400 (el VACIO), h=5 -> 98 celdas."""
    assert geo.margen_borde_celdas(90, 20, 400, 5) == 98
    # la `s` del vacio es 2*q90; meter `s_equivalente_zona` (2*q50 = s/2) da un
    # margen la raiz de dos mas chico, en silencio
    assert geo.margen_borde_celdas(90, 20, 200, 5) < 98
    with pytest.raises(ValueError, match="vecinos"):
        geo.margen_borde_celdas(0, 20, 400, 5)


# ---- A3: resolucion_regla ---------------------------------------------------


def test_resolucion_regla_manda_el_menor():
    rangos = [("0-100", 0, 100)]
    h_techos, f_techos, techos = geo.resolucion_regla(
        equidistancia=20, umm_m2=2500, rangos=rangos, ancho_medido=15.5
    )
    assert techos["umm"] == pytest.approx(25.0)  # sqrt(2500/4)
    assert techos["deteccion"] == pytest.approx(10.0)  # 20/(2*1)
    assert techos["trazado"] == pytest.approx(15.5 / 3)
    assert min(techos.values()) == techos["trazado"]


def test_resolucion_regla_sin_ancho_medido_usa_geometrico():
    h_techos, f_techos, techos = geo.resolucion_regla(20, 2500, [("0-100", 0, 100)])
    assert techos["trazado"] == pytest.approx((20 / 1) / 3)


def test_resolucion_regla_sin_lambda_no_trae_piso():
    """Llamador viejo ve exactamente los tres techos: su min() no cambia.

    `resolucion_regla` sigue dando los techos CRUDOS; el redondeo lo pone
    la reducción a `h`, que es quien sabe de qué lado se puede fallar.
    """
    h_techos, f_techos, techos = geo.resolucion_regla(20, 2500, [("0-100", 0, 100)])
    assert "antialias" not in techos
    assert techos["trazado"] == pytest.approx(20 / 3)  # crudo, sin redondear
    # y el `h` lo baja al valor limpio: un TECHO se redondea HACIA ABAJO
    assert (h_techos, f_techos) == (5.0, True)


def test_resolucion_h_piso_gana_cuando_es_infactible(capsys):
    """El caso que rompia los flujos: min() de los cuatro daba el peor valor.

    e=20, p_max=1 -> trazado 6.67 m; lambda 37.8 -> piso 18.9 m. No hay h que
    cumpla las dos, y el que manda es el PISO (el alias inventa area; la UMM
    gruesa solo deja de trazar).
    """
    h, factible, techos = geo.resolucion_regla(
        20, 400, [("0-100", 0, 100)], lambda_m=37.8
    )
    assert techos["antialias"] == pytest.approx(18.9)
    assert min(techos.values()) == pytest.approx(20 / 3)  # el min() ingenuo
    # 20, no 18.9: un PISO se redondea HACIA ARRIBA. Al mas cercano daria 20
    # aqui por suerte, pero con un piso de 22 daria 20 y lo violaria.
    assert (h, factible) == (20.0, False)
    assert h >= techos["antialias"]
    assert "INFACTIBLE" in capsys.readouterr().out


def test_resolucion_h_redondea_del_lado_que_respeta_la_condicion():
    """La direccion del redondeo, que es lo unico que no es cosmetico.

    Al valor limpio MAS CERCANO, un piso de 22 cae en 20 y queda por debajo de
    si mismo: la regla produciria un `h` que su propia guarda antialias
    rechaza. Por eso el piso va hacia arriba y el techo hacia abajo.
    """
    # piso 22 -> 25, nunca 20
    h, factible, techos = geo.resolucion_regla(
        20, 2500, [("0-100", 0, 100)], lambda_m=44.0
    )
    assert techos["antialias"] == pytest.approx(22.0)
    assert (h, factible) == (25.0, False)


def test_resolucion_regla_infactible_avisa_siempre(capsys):
    geo.resolucion_regla(20, 400, [("0-100", 0, 100)], lambda_m=37.8)
    assert "INFACTIBLE" in capsys.readouterr().out

    # techo 5.1667 -> 5, nunca 10. Es el `h` de la corrida entregable de
    # referencia.
    h_techos, f_techos, techos = geo.resolucion_regla(
        20, 2500, [("0-100", 0, 100)], ancho_medido=15.5
    )
    assert techos["trazado"] == pytest.approx(15.5 / 3)
    assert (h_techos, f_techos) == (5.0, True)


def test_resolucion_h_piso_bajo_no_estorba():
    """Con las curvas juntas el piso cae bajo el techo y manda el techo."""
    h_techos, f_techos, techos = geo.resolucion_regla(
        20, 2500, [("0-100", 0, 100)], lambda_m=6.0
    )
    assert (h_techos, f_techos) == (5.0, True)


# los RANGOS de produccion de los dos predios. La clase mas estrecha vale 5
# puntos, y es el juez de la espuria. Con `[("0-100", 0, 100)]` valdria 100 y
# el criterio no discriminaria: por eso el test usa los de verdad.
RANGOS_9 = [
    ("0-5", 0, 5),
    ("5-10", 5, 10),
    ("10-15", 10, 15),
    ("15-25", 15, 25),
    ("25-35", 25, 35),
    ("35-45", 35, 45),
    ("45-60", 45, 60),
    ("60-100", 60, 100),
    (">100", 100, float("inf")),
]


def test_con_A_medida_el_piso_antialias_deja_de_ser_el_juez():
    """`A` medida tiene que tocar `factible`, o el piso no discrimina.

    Las dos mitades, con las cifras medidas de los dos predios. Una guarda que
    grita en los dos regimenes no es guarda, y por eso el test fija el
    caso que PASA y el que NO por separado.
    """
    # Predio real: A = 0.00 m medida. lambda 74.90 del extent, techo de
    # trazado 15.464/3 = 5.15. La espuria en ese techo es 0.000 %.
    h_sin_a, f_sin_a, sin_a = geo.resolucion_regla(
        20, 2500, RANGOS_9, ancho_medido=15.464, lambda_m=74.896
    )
    # el defecto: sin `A` el piso manda y el predio sale INFACTIBLE
    assert sin_a["antialias"] == pytest.approx(37.448)
    assert (h_sin_a, f_sin_a)[1] is False

    h_con_a, f_con_a, con_a = geo.resolucion_regla(
        20, 2500, RANGOS_9, ancho_medido=15.464, lambda_m=74.896, amplitud_rizo=0.0
    )
    assert "antialias" not in con_a
    assert (h_con_a, f_con_a) == (5.0, True)

    # Otro predio real: A = 16.669 m medida, lambda 37.91, techo 5.00. La
    # espuria ahi vale 245.7 %, o sea 49 veces la clase mas estrecha: el piso
    # SIGUE mandando y el predio sigue infactible.
    h_infactible, f_infactible, infactible = geo.resolucion_regla(
        20, 2500, RANGOS_9, ancho_medido=15.0, lambda_m=37.908, amplitud_rizo=16.669
    )
    assert infactible["antialias"] == pytest.approx(18.954)
    assert (h_infactible, f_infactible)[1] is False


def test_el_juez_de_la_espuria_sale_de_rangos_y_no_esta_tecleado():
    """El umbral sale de `rangos`, no de un numero escrito.

    Con las mismas cifras de un predio real pero clases de 100 puntos de ancho, una
    espuria que con clases estrechas no pasa, pasa: el criterio se mueve con `rangos`, que es
    lo que la REGLA DE LOS NUMEROS pide.
    """
    kw = dict(ancho_medido=15.464, lambda_m=74.896, amplitud_rizo=1.0)
    # A = 1 m sobre lambda 74.9 en el techo 5.15 da ~8.1 %: pasa de 2.5, entra
    assert "antialias" in geo.resolucion_regla(20, 2500, RANGOS_9, **kw)[2]
    # con una sola clase de 0 a 100 el juez vale 50 y la misma espuria pasa
    assert "antialias" not in geo.resolucion_regla(20, 2500, [("0-100", 0, 100)], **kw)[2]


def test_el_juez_es_MEDIA_clase_porque_esa_es_la_distancia_maxima():
    """El ancho ENTERO de la clase seria optimista por un factor de 2.

    Lo que mueve un pixel de clase no es el ancho, es la distancia de su
    pendiente al limite mas cercano, y dentro de una clase de ancho `w` esa
    distancia no puede pasar de `w/2` (el centro). Medido en el sintetico con
    `A` CONOCIDA (rampa + rizo sinusoidal): con la misma espuria de 4.07 %
    que el ancho entero dejaba pasar, los pixeles que cambian de clase van de
    0.00 % a 52.81 % segun esa distancia.
    """
    from geophis.metrologia import _distancia_max_al_limite

    assert _distancia_max_al_limite(RANGOS_9) == pytest.approx(2.5)  # 5/2, no 5

    # A = 0.5 m sobre lambda 74.9 en el techo 5.15 da 4.07 %: por debajo del
    # ancho entero (5) y por ENCIMA de la distancia maxima real (2.5). En el
    # sintetico movia el 33.71 % de los pixeles, asi que tiene que RECHAZARSE.
    assert (
        "antialias"
        in geo.resolucion_regla(
            20, 2500, RANGOS_9, ancho_medido=15.464, lambda_m=74.896, amplitud_rizo=0.5
        )[2]
    )
    # y un predio real, con A = 0.00 m medida, sigue pasando
    assert (
        "antialias"
        not in geo.resolucion_regla(
            20, 2500, RANGOS_9, ancho_medido=15.464, lambda_m=74.896, amplitud_rizo=0.0
        )[2]
    )


def test_resolucion_regla_lambda_nan_se_ignora():
    """Zona sin curvas dentro: NaN no es un piso, es una medicion que no hubo."""
    h_techos, f_techos, techos = geo.resolucion_regla(
        20, 2500, [("0-100", 0, 100)], lambda_m=float("nan")
    )
    assert "antialias" not in techos
