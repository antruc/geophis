"""Procedencia de parámetros y arnés de cifras firmes.

El test que importa es `test_reproduce_los_derivados_de_referencia`: son las cifras
de un predio real, escritas a mano en la memoria técnica, y aquí salen de la
cadena. Si alguna se mueve, o la aritmética cambió o alguien reordenó los
argumentos de una regla, que es exactamente el defecto que costó 14.86 -> 4.35 ha.
"""

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, Polygon, box

import geophis as geo
from geophis.parametros import Valor, ValorEntero

# un predio real, todo MEDIDO aparte (memoria tecnica). Pasarlos a
# mano deja la funcion en aritmetica pura: sin shapefiles y sin interpolar.
REFERENCIA = dict(
    escala=25_000,
    equidistancia=20.0,
    lambda_m=74.93,
    zigzag=1.408,
    ancho_medido=15.5,
    s_vacio=400.0,
    vecinos=90,
    resolucion=5.0,
)
RANGOS = [
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


def _referencia(**cambios):
    datos = dict(REFERENCIA, **cambios)
    escala = datos.pop("escala")
    return geo.derivar_parametros(
        None, "COTA", None, RANGOS, escala, **datos
    )


def test_reproduce_los_derivados_de_referencia():
    """Las cifras de la corrida entregable, derivadas en vez de escritas."""
    p = _referencia()
    assert float(p["umm_m2"]) == pytest.approx(2500.0)  # (2 mm x 25000)^2
    assert float(p["p_max"]) == pytest.approx(1.0)  # de RANGOS, no a mano
    assert float(p["intervalo_muestreo_terreno"]) == pytest.approx(20.0)
    assert float(p["intervalo_muestreo"]) == pytest.approx(28.16)
    assert int(p["margen_borde_celdas"]) == 98  # n=90, i=20, s=400, h=5
    assert int(p["min_pixeles"]) == 100  # ceil(2500/25)


def test_toda_la_referencia_tiene_procedencia_y_nada_es_default():
    """Con las mediciones dadas no queda un solo valor que nadie eligiera."""
    p = _referencia()
    assert geo.sin_medir(p) == []
    assert p["escala"].fuente == "declarado"
    assert p["equidistancia"].fuente == "declarado"
    assert p["umm_m2"].fuente == "derivado"
    # `declarado`, no `medido`: el 90 de un predio real SI se midio, pero se le pasa aqui
    # como int pelado y la libreria no tiene forma de saberlo, asi que no lo
    # afirma. Para que la tabla lo diga hay que pasar el `Valor` del barrido,
    # no el entero.
    assert p["vecinos"].fuente == "declarado"
    assert p["margen_borde_celdas"].fuente == "derivado"


def test_las_convenciones_se_ven_pero_no_ensucian_sin_medir():
    """`LADO_MM`, `N_MIN`, `N_CRUCE` y `CUANTIL_VACIO` mandan sobre la cifra.

    Salen en la tabla, no escondidas en la firma. Fuera de `sin_medir` porque ninguna
    medicion las sustituye: una lista que siempre trae algo se deja de mirar.
    """
    p = _referencia()
    for clave in ("lado_mm", "n_min", "n_cruce", "cuantil_vacio"):
        assert p[clave].fuente == "convencion"
        assert clave in geo.tabla_procedencia(p)
    assert geo.sin_medir(p) == []
    # y mandan de verdad: `LADO_MM` = 2 mm entra al cuadrado en la UMM
    assert float(p["umm_m2"]) == pytest.approx((2e-3 * float(p["escala"])) ** 2)


def test_vecinos_sin_medir_sale_marcado_y_no_revienta():
    """El defecto: `vecinos` = 48 que nadie escribio, y nada avisaba.

    No se levanta excepcion a proposito: una derivacion reporta, no dictamina.
    Lo que cambia es que el default ahora es VISIBLE.
    """
    p = _referencia(vecinos=None)
    assert "vecinos" in geo.sin_medir(p)
    assert int(p["vecinos"]) == 48
    assert "SIN MEDIR" in geo.tabla_procedencia(p)


def test_sin_ancho_medido_el_techo_de_trazado_cae_al_geometrico():
    """`w` = 15.5 da un techo de 5.17; el geometrico (20) da 6.67.

    Se comprueba sobre `techos['trazado']` y no sobre `resolucion`: en ESTE
    predio el piso antialias (74.93/2 = 37.5) supera a todos los techos, asi
    que `resolucion_h` devuelve el PISO en los dos casos (SIGUIENTE §3.1, donde
    el aviso se equivoca). Es el techo el que se mueve con `w`.
    """
    con_w = _referencia(resolucion=None)
    sin_w = _referencia(resolucion=None, ancho_medido=None, medir_ancho=False)
    assert con_w["techos"]["trazado"] == pytest.approx(15.5 / 3, abs=0.01)
    assert sin_w["techos"]["trazado"] == pytest.approx(20.0 / 3, abs=0.01)
    assert "ancho_w" in geo.sin_medir(sin_w)
    assert "ancho_w" not in geo.sin_medir(con_w)
    # los dos infactibles, y el origen tiene que DECIRLO en vez de citar el techo
    assert con_w["factible"] is False
    assert "INFACTIBLE" in con_w["resolucion_regla"].origen
    assert "INFACTIBLE" in geo.tabla_procedencia(con_w)


def test_valor_pasa_por_float_pero_la_aritmetica_pierde_la_procedencia():
    """Se pasa tal cual a la API; operar con el devuelve un float pelado."""
    p = _referencia()
    assert isinstance(p["resolucion"], Valor) and isinstance(p["resolucion"], float)
    assert isinstance(p["min_pixeles"], ValorEntero)
    assert isinstance(p["min_pixeles"], int)
    doble = p["resolucion"] * 2
    assert doble == pytest.approx(10.0)
    assert not hasattr(doble, "fuente")


def test_faltan_capas_dice_que_falta_y_no_inventa():
    with pytest.raises(ValueError, match="curvas.*zona"):
        geo.derivar_parametros(
            None,
            "COTA",
            None,
            RANGOS,
            25_000,
            equidistancia=20.0,
        )


def test_comprobar_cifras_firmes_pasa_cerca_y_revienta_lejos():
    firmes = {"alta_sin_sieve_ha": 14.86, "atomo_ha": 57.80}
    geo.comprobar_cifras_firmes({"alta_sin_sieve_ha": 14.9, "atomo_ha": 57.7}, firmes)
    with pytest.raises(ValueError, match="alta_sin_sieve_ha"):
        geo.comprobar_cifras_firmes({"alta_sin_sieve_ha": 4.35, "atomo_ha": 57.80}, firmes)
    with pytest.raises(ValueError, match="no trae"):
        geo.comprobar_cifras_firmes({"atomo_ha": 57.80}, firmes)


def test_lambda_sale_del_extent_y_el_zigzag_de_la_zona():
    """Dominios distintos a proposito, y no se pueden sacar de una sola pasada.

    Medido sobre un predio real, misma capa de curvas:

        lambda: poligono 76.15 | bbox 74.90 | bbox+1500 68.34   (memoria 74.93)
        zigzag: poligono 1.413 | bbox 1.26                       (memoria 1.408)

    Con los dos del mismo dominio, uno de ellos sale mal y nada avisa: tomando
    el bbox para ambos, `intervalo_muestreo` se va 10 % y el blanco cartografico
    12 % (`ancho_banda` divide area y largo por el zigzag). Predio NO rectangular
    a proposito: con un rectangulo el bbox y el poligono coinciden y este test
    no probaria nada.
    """
    # DOS REGIMENES, como el predio real (banda empinada contra llano): curvas
    # cada 10 m abajo y cada 70 m arriba. Con densidad uniforme el bbox y el
    # poligono dan casi la misma lambda y el test no probaria nada (medido: 0.5 %).
    xs = np.arange(0, 401, 5.0)
    ys = [k * 10.0 for k in range(11)] + [140.0 + k * 70.0 for k in range(6)]
    curvas = gpd.GeoDataFrame(
        {"COTA": [k * 20.0 for k in range(len(ys))]},
        geometry=[
            LineString(np.column_stack([xs, y + 4.0 * np.sin(xs / 21.0)])) for y in ys
        ],
        crs=32613,
    )
    # el poligono vive en la banda densa y sube por un brazo delgado; su BBOX se
    # traga todo el llano de arriba, que el poligono apenas toca
    zona = gpd.GeoDataFrame(
        geometry=[
            Polygon(
                [
                    (40, 10),
                    (360, 10),
                    (360, 100),
                    (120, 100),
                    (120, 460),
                    (60, 460),
                    (60, 100),
                    (40, 100),
                ]
            )
        ],
        crs=32613,
    )
    p = geo.derivar_parametros(
        curvas, "COTA", zona, RANGOS, 25_000, medir_ancho=False
    )
    lam_extent = geo.separacion_media(curvas, geo.crear_cuadro(zona))[0]
    lam_zona = geo.separacion_media(curvas, zona)[0]
    zz_zona = geo.separacion_media(curvas, zona)[2]

    assert float(p["lambda_m"]) == pytest.approx(lam_extent, rel=1e-9)
    assert float(p["zigzag"]) == pytest.approx(zz_zona, rel=1e-9)
    # y los dominios de verdad difieren, o el test no estaria probando nada
    assert abs(lam_extent - lam_zona) / lam_zona > 0.01


def _curvas_rectas(crs, ys, cotas, x0=0.0, x1=4000.0):
    return gpd.GeoDataFrame(
        {"COTA": list(cotas)},
        geometry=[LineString([(x0, y), (x1, y)]) for y in ys],
        crs=crs,
    )


def test_ancho_banda_recorta_sola_y_da_lo_MISMO_que_recortada_a_mano():
    """El recorte previo es exacto, no una aproximacion.

    Sin el, `zona_de_influencia` se aplicaba a la multilinea COMPLETA de cada nivel; con
    cartografia estatal eso agoto los 16 GB y el sistema mato el proceso sin
    traza. Lo que este test fija es que abaratarlo no movio la cifra.
    """
    zona = gpd.GeoDataFrame(geometry=[box(1000, 100, 2000, 260)], crs=32613)
    ys = np.arange(0, 401, 12.0)
    enormes = _curvas_rectas(32613, ys, ys * 20.0 / 12.0, x0=-40_000, x1=40_000)
    apretadas = geo.recortar(enormes, geo.zona_de_influencia(zona, 25))

    _, con_todo = geo.ancho_banda(enormes, "COTA", zona, 20.0, 1.0)
    _, con_poco = geo.ancho_banda(apretadas, "COTA", zona, 20.0, 1.0)
    assert con_todo["ancho_m"] == pytest.approx(con_poco["ancho_m"], rel=1e-9)
    assert con_todo["area_ha"] == pytest.approx(con_poco["area_ha"], rel=1e-9)


def test_ancho_banda_no_hace_contiguos_dos_niveles_que_no_lo_son():
    """La escalera de cotas sale de las curvas COMPLETAS, no de las recortadas.

    Cota 0 en y=100 y cota 40 en y=112 estan a 12 m, pero NO son contiguas: entre
    ellas va la 20, que aqui vive lejisimos y el recorte se lleva. Si la escalera
    se recalculara sobre lo recortado, el par (0, 40) pasaria por vecino y
    apareceria una banda de 12 m que no existe.
    """
    zona = gpd.GeoDataFrame(geometry=[box(0, 90, 1000, 130)], crs=32613)
    curvas = _curvas_rectas(32613, [100.0, 90_000.0, 112.0], [0.0, 20.0, 40.0])
    with pytest.raises(ValueError, match="ninguna banda medible"):
        geo.ancho_banda(curvas, "COTA", zona, 20.0, 1.0)


def test_medir_vecinos_convierte_el_unico_default_en_medido():
    """`vecinos` no es DERIVABLE, pero si es MEDIBLE, y no es lo mismo.

    Con `vecinos="medir"` se barre la rodilla aqui y deja de estar en
    `sin_medir`. Apagado por defecto porque cuesta una interpolacion por `n`.
    """
    ys = np.arange(0, 301, 12.0)
    curvas = _curvas_rectas(32613, ys, ys * 20.0 / 12.0, x0=0, x1=600)
    zona = gpd.GeoDataFrame(geometry=[box(100, 100, 500, 200)], crs=32613)

    flojo = geo.derivar_parametros(
        curvas, "COTA", zona, RANGOS, 25_000, medir_ancho=False
    )
    assert "vecinos" in geo.sin_medir(flojo)
    assert flojo["vecinos"].fuente == "default"

    medido = geo.derivar_parametros(
        curvas,
        "COTA",
        zona,
        RANGOS,
        25_000,
        medir_ancho=False,
        vecinos="medir",
    )
    assert "vecinos" not in geo.sin_medir(medido)
    assert medido["vecinos"].fuente == "medido"
    assert int(medido["vecinos"]) > 0
    # y el margen se recalcula CON la rodilla, no con el default que ya no aplica
    assert int(medido["margen_borde_celdas"]) == geo.margen_borde_celdas(
        int(medido["vecinos"]),
        float(medido["intervalo_muestreo_terreno"]),
        float(medido["s_vacio"]),
        float(medido["resolucion"]),
    )


def _rampa_para_vecinos():
    ys = np.arange(0, 301, 12.0)
    return (
        _curvas_rectas(32613, ys, ys * 20.0 / 12.0, x0=0, x1=600),
        gpd.GeoDataFrame(geometry=[box(100, 100, 500, 200)], crs=32613),
    )


def test_derivar_parametros_deja_pasar_enes():
    """Extender la rejilla no puede costar la tabla de procedencia.

    Si `derivar_parametros` no dejara elegir `enes`, barrer otra rejilla
    obligaria a saltarse la funcion que lleva la procedencia.

    La rejilla default empieza en 20, asi que un `vecinos` menor solo puede
    venir de la que se paso.
    """
    curvas, zona = _rampa_para_vecinos()
    p = geo.derivar_parametros(
        curvas,
        "COTA",
        zona,
        RANGOS,
        25_000,
        medir_ancho=False,
        vecinos=(8, 16),
    )
    assert int(p["vecinos"]) in (8, 16)


def test_una_rodilla_SIN_convergencia_cae_en_sin_medir():
    """Una rodilla que no es rodilla es "nadie lo eligio" con otro nombre.

    El barrido siempre devuelve un numero, asi que estampar `medido` sobre el
    resultado pelado dejaria pasar `vecinos 122 medido` (un predio real). Con una rejilla de un solo `n`
    la rodilla cae por fuerza en el borde, el barrido lo dice, y la procedencia
    tiene que recogerlo.
    """
    curvas, zona = _rampa_para_vecinos()
    p = geo.derivar_parametros(
        curvas,
        "COTA",
        zona,
        RANGOS,
        25_000,
        medir_ancho=False,
        vecinos=(8,),
    )
    assert p["vecinos"].fuente == "default"
    assert "vecinos" in geo.sin_medir(p)
    # y la razon va pegada al valor, no solo en la consola que ya se ignora
    assert "borde de la lista" in p["vecinos"].origen


def test_un_int_pelado_entra_DECLARADO_y_no_medido():
    """La libreria no puede afirmar `medido` sobre lo que no midio.

    Un `vecinos` = 90 DECLARADO por costo, con su razon en el comentario del
    script, no puede salir como "rodilla de un barrido".
    """
    curvas, zona = _rampa_para_vecinos()
    p = geo.derivar_parametros(
        curvas, "COTA", zona, RANGOS, 25_000, vecinos=90, medir_ancho=False
    )
    assert int(p["vecinos"]) == 90
    assert p["vecinos"].fuente == "declarado"
    assert "rodilla" not in p["vecinos"].origen
    # `declarado` no es `default`: alguien SI lo eligio, solo que no fue la libreria
    assert "vecinos" not in geo.sin_medir(p)


def test_una_segunda_llamada_NO_lava_el_default_de_la_primera():
    """El lavado: una segunda derivacion no puede convertir un default en medido.

    Una etapa que vuelve a derivar pasandole el MISMO `vecinos` = 48 de la
    primera tiene que seguir diciendo `default`, y la tabla que va al
    entregable es la SEGUNDA. El `Valor` pasa tal cual; el cast a int lo
    rompe, y por eso se prueban los dos caminos.
    """
    curvas, zona = _rampa_para_vecinos()
    primera = geo.derivar_parametros(
        curvas, "COTA", zona, RANGOS, 25_000, medir_ancho=False
    )
    assert primera["vecinos"].fuente == "default"

    segunda = geo.derivar_parametros(
        curvas,
        "COTA",
        zona,
        RANGOS,
        25_000,
        vecinos=primera["vecinos"],  # el Valor, NO int(...)
        medir_ancho=False,
    )
    assert segunda["vecinos"].fuente == "default"
    assert "vecinos" in geo.sin_medir(segunda), "el default se lavo al pasar de llamada"
    assert segunda["vecinos"].origen == primera["vecinos"].origen

    # y el cast sigue siendo la frontera: un int pelado ya no sabe de donde vino
    casteada = geo.derivar_parametros(
        curvas,
        "COTA",
        zona,
        RANGOS,
        25_000,
        vecinos=int(primera["vecinos"]),
        medir_ancho=False,
    )
    assert casteada["vecinos"].fuente == "declarado"


def test_la_procedencia_pasa_por_los_siete_parametros_de_mano():
    """No es solo `vecinos`: los siete valores que se pueden dar a mano."""
    curvas, zona = _rampa_para_vecinos()
    primera = geo.derivar_parametros(
        curvas, "COTA", zona, RANGOS, 25_000, medir_ancho=False
    )
    segunda = geo.derivar_parametros(
        curvas,
        "COTA",
        zona,
        RANGOS,
        25_000,
        equidistancia=primera["equidistancia"],
        lambda_m=primera["lambda_m"],
        zigzag=primera["zigzag"],
        ancho_medido=primera["ancho_w"],
        s_vacio=primera["s_vacio"],
        resolucion=primera["resolucion"],
        vecinos=primera["vecinos"],
    )
    for clave, origen in [
        ("equidistancia", "equidistancia"),
        ("lambda_m", "lambda_m"),
        ("zigzag", "zigzag"),
        ("ancho_w", "ancho_w"),
        ("s_vacio", "s_vacio"),
        ("resolucion", "resolucion"),
        ("vecinos", "vecinos"),
    ]:
        assert segunda[clave].fuente == primera[origen].fuente, clave
        assert segunda[clave].origen == primera[origen].origen, clave


def test_la_espuria_no_puede_ser_mas_firme_que_la_A_de_la_que_sale():
    """`pendiente_espuria_pct` es funcion de `A`, asi que hereda su fuente.

    Antes decia `medido ... con A=16.4751 m MEDIDA` sobre cualquier float, y en
    En un predio real ese float lo habia marcado `amplitud_rizo` con DOS avisos de que
    no era citable.
    """
    curvas, zona = _rampa_para_vecinos()
    p = geo.derivar_parametros(
        curvas,
        "COTA",
        zona,
        RANGOS,
        25_000,
        amplitud_rizo=1.4,
        medir_ancho=False,
    )
    assert p["pendiente_espuria_pct"].fuente == "declarado"
    assert "MEDIDA" not in p["pendiente_espuria_pct"].origen


def test_registra_con_que_capa_se_midio_y_no_solo_con_que_fuente(tmp_path):
    """Dos capas del mismo predio salen las dos como `medido` y dan cifras
    distintas (blanco cartografico 10.28 contra 16.14 ha en un predio real). La huella
    geometrica las distingue aunque nadie declare rutas; la ruta la pone
    `cargar` en `attrs` y es best effort.
    """
    curvas = gpd.GeoDataFrame(
        {"COTA": [i * 20.0 for i in range(9)]},
        geometry=[LineString([(0, i * 12), (400, i * 12)]) for i in range(9)],
        crs=32613,
    )
    predio = gpd.GeoDataFrame(geometry=[box(20, 20, 380, 80)], crs=32613)
    ruta = tmp_path / "curvas.gpkg"
    curvas.to_file(ruta, driver="GPKG")
    cargada = geo.cargar(ruta)
    assert cargada.attrs["ruta"] == str(ruta)

    p = geo.derivar_parametros(cargada, "COTA", predio, RANGOS, 25_000)
    ins = p["insumos"]
    assert ins["curvas"]["ruta"] == str(ruta)
    assert ins["curvas"]["n"] == 9
    assert "km de trazo" in ins["curvas"]["medida"]
    assert ins["zona"]["ruta"].startswith("no declarada")  # construida en memoria
    assert "2.16 ha" in ins["zona"]["medida"]
    assert str(ruta) in geo.tabla_procedencia(p)


def test_crs_geografico_revienta_en_vez_de_dar_grados_con_nombre_de_metros():
    """El fallo silencioso: en 4326 nada revienta y todo sale en grados.

    Saber el CRS ya se sabia; lo que faltaba era comprobarlo.
    """
    curvas = gpd.GeoDataFrame(
        {"COTA": [0.0, 20.0]},
        geometry=[
            LineString([(-103.0, 20.0), (-102.9, 20.0)]),
            LineString([(-103.0, 20.001), (-102.9, 20.001)]),
        ],
        crs=4326,
    )
    predio = gpd.GeoDataFrame(geometry=[box(-103.0, 20.0, -102.9, 20.001)], crs=4326)
    with pytest.raises(ValueError, match="geográficas"):
        geo.derivar_parametros(curvas, "COTA", predio, RANGOS, 25_000)


def test_cadena_completa_mide_sobre_cartografia_de_verdad():
    """El wiring, que es donde viven los errores de orden de argumentos.

    Ladera uniforme, curvas E-W cada 12 m con 20 m de cota (167 %), la misma de
    `test_docs`. Aqui no se comprueban cifras de campo: se comprueba que cada
    medicion entra en la siguiente y que la procedencia sale coherente.
    """
    curvas = gpd.GeoDataFrame(
        {"COTA": [i * 20.0 for i in range(9)]},
        geometry=[LineString([(0, i * 12), (400, i * 12)]) for i in range(9)],
        crs=32613,
    )
    predio = gpd.GeoDataFrame(geometry=[box(20, 20, 380, 80)], crs=32613)

    p = geo.derivar_parametros(curvas, "COTA", predio, RANGOS, 25_000)

    assert float(p["equidistancia"]) == pytest.approx(20.0)
    assert p["equidistancia"].fuente == "medido"
    # las curvas son rectas: el zigzag tiene que salir practicamente 1
    assert float(p["zigzag"]) == pytest.approx(1.0, abs=0.05)
    # `w` medida contra el nivel contiguo, siempre por debajo del geometrico e/p_max
    assert p["ancho_w"].fuente == "medido"
    assert float(p["ancho_w"]) < 20.0
    assert float(p["blanco_carta_ha"]) > 0
    # s_vacio = 2*q90 sobre curvas separadas 12 m: del orden de la separacion,
    # nunca de la mediana (que valdria la mitad)
    assert 5.0 < float(p["s_vacio"]) < 30.0
    assert p["s_vacio"].fuente == "medido"
    # con lambda ~12 m el piso (6) supera al techo de trazado (w/3): infactible,
    # y eso es un RESULTADO que se declara, no un error
    assert p["factible"] is False
    # `resolucion` sale del piso, REDONDEADA HACIA ARRIBA a un valor limpio: el
    # piso crudo (6.0) se reporta aparte, pero el `h` que se entrega tiene que
    # ser un numero que alguien pueda escribir. Nunca por debajo del piso.
    assert float(p["resolucion"]) >= float(p["resolucion_piso"])
    assert float(p["resolucion"]) == 10.0
    assert float(p["resolucion_piso"]) == pytest.approx(6.0)
    assert geo.sin_medir(p) == ["vecinos"]


def test_metodo_tin_sin_piso_sin_vecinos_y_margen_del_vacio():
    """Con `metodo="tin"` la tabla dice lo que de verdad se usa.

    Medido en un predio real con la tabla del spline: `resolucion` 20 m INFACTIBLE
    y `vecinos` SIN MEDIR, para una superficie hecha a 5 m y sin vecindario. Con
    lambda 20 m el piso del spline (10) pisa el techo de trazado (5) y lo vuelve
    infactible; con TIN el piso no existe.
    """
    spline = _referencia(lambda_m=20.0, vecinos=None, resolucion=None)
    tin = _referencia(lambda_m=20.0, vecinos=None, resolucion=None, metodo="tin")
    assert spline["factible"] is False and "vecinos" in geo.sin_medir(spline)
    assert tin["factible"] is True
    assert float(tin["resolucion"]) == pytest.approx(5.0)  # techo w/3 = 15.5/3
    assert np.isnan(float(tin["resolucion_piso"]))
    assert "vecinos" not in tin and geo.sin_medir(tin) == []
    assert int(tin["margen_borde_celdas"]) == 400 // 5 + 2  # ceil(s_vacio/h)+2
    with pytest.raises(ValueError, match="RBF"):
        _referencia(metodo="tin")  # un predio real trae vecinos=90


def test_parametros_rellena_solo_lo_que_queda_en_none():
    """`parametros=p` toma del dict lo que quedo en None; lo dado a mano manda."""
    from geophis.metrologia import _de_parametros

    p = {"resolucion": 5.0, "margen_borde_celdas": 12}
    assert list(_de_parametros(p, resolucion=None, margen_borde_celdas=3).values()) == [5.0, 3]
    assert list(_de_parametros(None, resolucion=None).values()) == [None]


def test_callado_devuelve_MOSTRAR_como_estaba_aunque_falle():
    """Las llamadas internas se callan con `_callado`; si revientan, no dejan
    la libreria muda para el resto de la sesion."""
    from geophis._salida import _callado

    def falla():
        assert geo.MOSTRAR is False
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        _callado(falla)()
    assert geo.MOSTRAR is True


def test_s_vacio_que_mide_cero_falla_claro():
    """Con la malla más gruesa que la separación, cada celda toca una curva y
    `s_vacio` medía 0: reventaba después en `barrer_vecinos` sin decir por qué."""
    ys = np.arange(0, 1000, 10.0)
    curvas = _curvas_rectas(32613, ys, ys * 2, x1=1000.0)
    zona = gpd.GeoDataFrame(geometry=[box(100, 100, 900, 900)], crs=32613)
    with pytest.raises(ValueError, match="s_vacio` mide 0"):
        geo.derivar_parametros(
            curvas, "COTA", zona, [("0-100", 0, 100)], 25_000,
            resolucion=50.0, medir_ancho=False, vecinos=48,
        )
