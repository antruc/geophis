"""Tests de geophis.geometria."""

import pytest
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    Point,
    Polygon,
    box,
)

import geophis as geo


def test_zona_de_influencia_no_muta_y_crece(gdf, cuadrado):
    g = gdf(cuadrado(lado=10))
    area0 = g.geometry.area.iloc[0]
    r = geo.zona_de_influencia(g, 5)
    assert g.geometry.area.iloc[0] == area0  # original intacto
    assert r.geometry.area.iloc[0] > area0


def test_disolver_une_en_una_con_etiqueta(gdf, cuadrado):
    g = gdf(cuadrado(0, 0), cuadrado(200, 0))
    r = geo.disolver(g, campo="cond", valor="X")
    assert len(r) == 1
    assert r["cond"].iloc[0] == "X"


def test_disolver_vacio_avisa(gdf, cuadrado, capsys):
    # la fila con geometría vacía parece válida y viaja aguas abajo sin reventar
    r = geo.disolver(gdf(cuadrado()).iloc[:0])
    assert len(r) == 1 and r.geometry.iloc[0].is_empty
    assert "vacía" in capsys.readouterr().out


def test_disolver_por_grupo_uno_por_valor(gdf, cuadrado):
    # 4 cuadrados, 2 códigos: dissolve por grupo => 2 entidades
    g = gdf(
        cuadrado(0, 0),
        cuadrado(100, 0),
        cuadrado(0, 100),
        cuadrado(100, 100),
        codigo=[1, 1, 2, 2],
    )
    r = geo.disolver_por_grupo(g, "codigo")
    assert len(r) == 2
    assert sorted(r["codigo"]) == [1, 2]
    assert r.crs == g.crs


def test_disolver_por_grupo_campo_inexistente_falla(gdf, cuadrado):
    with pytest.raises(ValueError, match="no tiene el campo"):
        geo.disolver_por_grupo(gdf(cuadrado()), "nope")


def test_explotar_deshace_disolver_por_grupo(gdf, cuadrado):
    """El caso real: dos clases, cuatro manchas separadas. Disolver deja 2 filas
    multiparte; separar_multipartes devuelve las 4 piezas con su clase copiada."""
    g = gdf(
        cuadrado(0, 0),
        cuadrado(1000, 0),
        cuadrado(0, 1000),
        cuadrado(1000, 1000),
        codigo=[1, 1, 2, 2],
    )
    agrupado = geo.disolver_por_grupo(g, "codigo")
    assert len(agrupado) == 2  # multiparte: las manchas ya no se cuentan

    piezas = geo.separar_multipartes(agrupado)
    assert len(piezas) == 4
    assert sorted(piezas["codigo"]) == [1, 1, 2, 2]
    assert set(piezas.geom_type) == {"Polygon"}
    assert piezas.crs == g.crs
    # la superficie total no cambia: separar_multipartes reparte, no crea ni pierde
    assert geo.superficie_total_ha(piezas) == pytest.approx(
        geo.superficie_total_ha(agrupado)
    )


def test_explotar_deja_indice_plano_e_indexable(gdf, cuadrado):
    """La trampa que envuelve: sin `index_parts=False` + `reset_index(drop=True)`
    queda un MultiIndex, y el patrón que sigue es `iloc[[k]]` / elegir la mayor."""
    agrupado = geo.disolver_por_grupo(
        gdf(cuadrado(0, 0), cuadrado(1000, 0), codigo=[1, 1]), "codigo"
    )
    piezas = geo.separar_multipartes(agrupado)
    assert list(piezas.index) == [0, 1]
    assert piezas.index.nlevels == 1
    assert len(piezas.iloc[[0]]) == 1  # indexable pieza a pieza


def test_explotar_no_toca_una_capa_ya_simple(gdf, cuadrado):
    g = gdf(cuadrado(0, 0), cuadrado(1000, 0), codigo=[1, 2])
    piezas = geo.separar_multipartes(g)
    assert len(piezas) == 2 and sorted(piezas["codigo"]) == [1, 2]


def test_explotar_conserva_la_columna_de_geometria_renombrada(gdf, cuadrado):
    """Mismo criterio que el resto del módulo: por nombre real, no por literal."""
    g = gdf(cuadrado(0, 0), cuadrado(1000, 0), codigo=[1, 1]).rename_geometry("geom")
    piezas = geo.separar_multipartes(geo.disolver_por_grupo(g, "codigo"))
    assert piezas.geometry.name == "geom" and len(piezas) == 2


def test_explotar_area_por_pieza_y_no_por_grupo(gdf, cuadrado):
    """Para qué existe: `calcular_superficie` mide la FILA. Sobre el disuelto da
    el área del grupo; sobre lo explotado, la de cada mancha."""
    agrupado = geo.disolver_por_grupo(
        gdf(cuadrado(0, 0), cuadrado(1000, 0), codigo=[1, 1]), "codigo"
    )
    grupo = geo.calcular_superficie(agrupado, campo="AREA_HA")
    piezas = geo.calcular_superficie(geo.separar_multipartes(agrupado), campo="AREA_HA")
    assert len(grupo) == 1 and grupo["AREA_HA"].iloc[0] == pytest.approx(2.0)
    assert list(piezas["AREA_HA"]) == pytest.approx([1.0, 1.0])


def test_fusionar_concatena_y_toma_crs(gdf, cuadrado):
    a, b = gdf(cuadrado(0, 0)), gdf(cuadrado(200, 0))
    r = geo.fusionar([a, b])
    assert len(r) == 2 and r.crs.to_epsg() == 32613


def test_fusionar_vacio_falla():
    with pytest.raises(ValueError):
        geo.fusionar([])


def test_fusionar_crs_distintos_falla(gdf, cuadrado):
    a = gdf(cuadrado())
    b = gdf(cuadrado()).to_crs(4326)
    with pytest.raises(ValueError):
        geo.fusionar([a, b])


def test_intersecar_corta_y_conserva_atributos(gdf, cuadrado):
    a = gdf(cuadrado(0, 0, 100), uso=["bosque"])
    b = gdf(cuadrado(50, 0, 100))
    r = geo.intersecar(a, b)
    assert r.geometry.area.sum() == pytest.approx(50 * 100)
    assert r["uso"].iloc[0] == "bosque"


def test_rellenar_huecos_quita_el_anillo_interior(gdf):
    from shapely.geometry import Polygon

    donut = Polygon(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        [[(40, 40), (60, 40), (60, 60), (40, 60)]],  # hueco de 400 m2
    )
    r = geo.rellenar_huecos(gdf(donut))
    assert not r.geometry.iloc[0].interiors
    assert r.area.iloc[0] == 10_000  # recupero los 400 m2 del hueco


def test_rellenar_huecos_respeta_el_maximo(gdf, capsys):
    from shapely.geometry import Polygon

    # dos huecos: uno de 400 m2 (0.04 ha) y otro de 2500 m2 (0.25 ha)
    donut = Polygon(
        [(0, 0), (200, 0), (200, 200), (0, 200)],
        [
            [(10, 10), (30, 10), (30, 30), (10, 30)],
            [(100, 100), (150, 100), (150, 150), (100, 150)],
        ],
    )
    r = geo.rellenar_huecos(gdf(donut), area_max_ha=0.1)
    quedan = r.geometry.iloc[0].interiors
    assert len(quedan) == 1  # solo se fue el chico
    assert Polygon(quedan[0]).area == 2500
    assert "1 de 2 huecos rellenados" in capsys.readouterr().out


def test_rellenar_huecos_no_muta_la_entrada(gdf):
    from shapely.geometry import Polygon

    donut = Polygon(
        [(0, 0), (10, 0), (10, 10), (0, 10)], [[(4, 4), (6, 4), (6, 6), (4, 6)]]
    )
    entrada = gdf(donut)
    geo.rellenar_huecos(entrada)
    assert entrada.geometry.iloc[0].interiors  # sigue con su hueco


def test_rellenar_huecos_multipoligono_y_geometria_sin_huecos(gdf, cuadrado):
    from shapely.geometry import MultiPolygon, Polygon

    con_hueco = Polygon(
        [(0, 0), (10, 0), (10, 10), (0, 10)], [[(4, 4), (6, 4), (6, 6), (4, 6)]]
    )
    mp = MultiPolygon([con_hueco, Polygon([(20, 0), (30, 0), (30, 10), (20, 10)])])
    r = geo.rellenar_huecos(gdf(mp, cuadrado()))
    assert not any(p.interiors for p in r.geometry.iloc[0].geoms)
    assert r.geometry.iloc[1].equals(cuadrado())  # el que no tenia huecos, intacto


def test_rellenar_huecos_area_max_invalida(gdf, cuadrado):
    import pytest as _pt

    with _pt.raises(ValueError, match="debe ser > 0"):
        geo.rellenar_huecos(gdf(cuadrado()), area_max_ha=0)


def test_cerrar_microhuecos_no_toca_el_hueco_de_un_solo_poligono(gdf):
    # la especificacion es el doc: el hueco interior es de `rellenar_huecos`
    con_hueco = Polygon(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        [[(50, 50), (50, 51), (51, 51), (51, 50)]],  # hueco de 1x1 m
    )
    r = geo.cerrar_microhuecos(gdf(con_hueco), distancia=1)
    assert len(r.geometry.iloc[0].interiors) == 1
    assert r.geometry.iloc[0].area == pytest.approx(100 * 100 - 1)


def test_cerrar_microhuecos_cierra_la_rendija_entre_vecinos(gdf):
    capa = gdf(box(0, 0, 50, 100), box(50.5, 0, 100, 100))
    r = geo.cerrar_microhuecos(capa, distancia=1)
    geo.comprobar_particion([r], gdf(box(0, 0, 100, 100)), tolerancia_ha=1e-9)


def test_linea_a_poligono_multilinea_usa_primer_tramo(gdf):
    ml = MultiLineString([[(0, 0), (100, 0), (100, 100), (0, 100)]])
    r = geo.linea_a_poligono(gdf(ml))
    assert r.geometry.iloc[0].area == pytest.approx(100 * 100)


def test_linea_a_poligono_pocos_vertices_falla(gdf):
    with pytest.raises(ValueError):
        geo.linea_a_poligono(gdf(LineString([(0, 0), (1, 1)])))


def test_crear_cuadro_con_margen(gdf, cuadrado):
    g = gdf(cuadrado(0, 0, 100))
    r = geo.crear_cuadro(g, margen=10)
    assert tuple(r.total_bounds) == (-10, -10, 110, 110)


def test_cuadros_mde_borde_escala_con_resolucion(gdf, cuadrado):
    g = gdf(cuadrado(0, 0, 100))
    # resolucion 20, 10 celdas de borde => 200 m; salida = predio
    recorte, salida = geo.cuadros_mde(g, resolucion=20, margen_borde_celdas=10)
    assert tuple(salida.total_bounds) == (0, 0, 100, 100)
    assert tuple(recorte.total_bounds) == (-200, -200, 300, 300)


def test_cuadros_mde_con_margen_salida(gdf, cuadrado):
    g = gdf(cuadrado(0, 0, 100))
    # salida ampliada 50 m; borde 10 celdas * 10 = 100 m sobre la salida
    recorte, salida = geo.cuadros_mde(
        g, resolucion=10, margen_borde_celdas=10, margen_salida=50
    )
    assert tuple(salida.total_bounds) == (-50, -50, 150, 150)
    assert tuple(recorte.total_bounds) == (-150, -150, 250, 250)


def test_cuadros_mde_resolucion_invalida(gdf, cuadrado):
    with pytest.raises(ValueError):
        geo.cuadros_mde(gdf(cuadrado(0, 0, 100)), resolucion=0)


def test_limpiar_vacias_descarta_vacias(gdf, cuadrado):
    g = gdf(cuadrado(), Polygon())
    assert len(geo.limpiar_vacias(g)) == 1


def test_reparar_geometrias_arregla_bowtie(gdf):
    bowtie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2)])  # inválido (auto-cruce)
    assert not bowtie.is_valid
    r = geo.reparar_geometrias(gdf(bowtie))
    assert r.geometry.is_valid.all()


def test_calcular_superficie_hectareas(gdf, cuadrado):
    g = gdf(cuadrado(lado=100))  # 10000 m2 = 1 ha
    r = geo.calcular_superficie(g)
    assert r["superficie_ha"].iloc[0] == pytest.approx(1.0)


def test_borrar_resta(gdf, cuadrado):
    base = gdf(cuadrado(0, 0, 100))
    otro = gdf(cuadrado(0, 0, 50))
    r = geo.borrar(base, otro)
    assert r.geometry.area.iloc[0] == pytest.approx(100 * 100 - 50 * 50)


def test_borrar_alinea_crs(gdf, cuadrado):
    # `otro` en 4326 se reproyecta solo, igual que recortar
    base = gdf(cuadrado(300000, 2600000, 100))
    otro = gdf(cuadrado(300000, 2600000, 50)).to_crs(epsg=4326)
    r = geo.borrar(base, otro)
    assert r.geometry.area.iloc[0] == pytest.approx(100 * 100 - 50 * 50, rel=1e-5)


def test_borrar_descarta_esquirla_de_linea(gdf, cuadrado, tmp_path):
    # el difference deja área + un tramo de línea donde los bordes se rozan.
    # Esa mezcla no se puede guardar a shapefile: debe quedarse solo el área.
    mezcla = GeometryCollection(
        [Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), LineString([(20, 0), (30, 0)])]
    )
    r = geo.borrar(gdf(mezcla), gdf(cuadrado(100, 100, 10)))
    assert set(r.geom_type) == {"Polygon"}
    assert r.geometry.area.iloc[0] == pytest.approx(100)
    geo.guardar(r, tmp_path / "ok.shp")  # con la esquirla de línea revienta aquí


def test_resolver_por_prioridad_recorta_contra_todas_las_anteriores(gdf):
    # C pisa a A y a B, pero A y B no se tocan entre sí: recortando C solo contra
    # su vecina B se quedaría el solape con A
    a = gdf(Polygon([(0, 0), (100, 0), (100, 100), (0, 100)]), nivel="A")
    b = gdf(Polygon([(200, 0), (300, 0), (300, 100), (200, 100)]), nivel="B")
    c = gdf(Polygon([(50, 0), (250, 0), (250, 100), (50, 100)]), nivel="C")
    r = geo.resolver_por_prioridad([a, b, c])
    area_c = r[r["nivel"] == "C"].geometry.area.sum()
    assert area_c == pytest.approx(100 * 100)  # 200x100 menos 50x100 de cada lado


def test_comprobar_particion_ve_el_hueco_que_el_solape_no_delata(gdf, cuadrado):
    # El modo de fallo de la receta de dos pasadas: falta superficie y el solape
    # es CERO, o sea que mirar solo el solape (lo unico que ve
    # `resolver_por_prioridad`) da la capa por buena. 1 ha = 10000 m2.
    zona = gdf(box(0, 0, 200, 100))  # 2 ha
    izq = gdf(cuadrado(0, 0, 100), clase="A")
    trozo = gdf(box(100, 0, 150, 100), clase="B")  # deja 50x100 sin cubrir

    falta, solape = geo.comprobar_particion([izq, trozo], zona, 1.0)
    assert falta == pytest.approx(0.5)  # 5000 m2 de hueco
    assert solape == pytest.approx(0.0)
    with pytest.raises(ValueError, match=r"no cierra"):
        geo.comprobar_particion([izq, trozo], zona, 0.1)

    # y el modo simetrico: cubre entero, pero contando una franja dos veces
    der = gdf(box(90, 0, 200, 100), clase="B")
    falta, solape = geo.comprobar_particion([izq, der], zona, 1.0)
    assert falta == pytest.approx(0.0)
    assert solape == pytest.approx(0.1)  # 10x100 m2 contados dos veces


def test_comprobar_particion_no_deja_que_hueco_y_exceso_se_compensen(gdf):
    # 0.5 ha sin cubrir dentro y 0.5 ha fuera: la cifra neta da 0.0
    zona = gdf(box(0, 0, 200, 100))
    capa = gdf(box(0, 0, 150, 100), box(200, 0, 250, 100))
    falta, _ = geo.comprobar_particion([capa], zona, 1.0)
    assert falta == pytest.approx(0.0)
    with pytest.raises(ValueError, match=r"hueco.*exceso"):
        geo.comprobar_particion([capa], zona, 0.1)


def test_comprobar_particion_ubica_el_hueco_pegado_al_borde(gdf):
    # una muesca de 10x10 m en el contorno no es anillo interior
    zona = gdf(box(0, 0, 200, 100))
    capa = gdf(box(0, 0, 200, 100).difference(box(190, 90, 200, 100)))
    with pytest.raises(ValueError, match=r"1 piezas.*\(195, 95\)"):
        geo.comprobar_particion([capa], zona, 0.001)


def test_comprobar_particion_no_confunde_un_enclave_con_hueco(gdf):
    predio = Polygon(
        [(0, 0), (200, 0), (200, 100), (0, 100)],
        [[(50, 25), (100, 25), (100, 75), (50, 75)]],
    )
    geo.comprobar_particion([gdf(predio)], gdf(predio), 1e-9)


def test_comprobar_particion_zona_invalida_da_error_legible(gdf, cuadrado):
    corbata = Polygon([(0, 0), (100, 100), (100, 0), (0, 100)])
    with pytest.raises(ValueError, match="reparar_geometrias"):
        geo.comprobar_particion([gdf(cuadrado())], gdf(corbata), 1e9)
    # con dos filas revienta antes, al unir la zona
    zona = gdf(corbata, cuadrado(50, 0))
    with pytest.raises(ValueError, match="reparar_geometrias"):
        geo.comprobar_particion([gdf(cuadrado())], zona, 1e9)
    # y lo mismo con la geometría inválida en las capas
    with pytest.raises(ValueError, match="reparar_geometrias"):
        geo.comprobar_particion([zona], gdf(cuadrado()), 1e9)


def test_comprobar_particion_no_ubica_una_astilla_si_fallo_el_solape(gdf):
    zona = gdf(box(0, 0, 200, 100))
    capa = gdf(box(0, 0, 110, 100), box(100, 0, 199.99, 100))  # 1 m2 de hueco
    with pytest.raises(ValueError, match="doble conteo") as e:
        geo.comprobar_particion([capa], zona, 0.01)
    assert "centrada" not in str(e.value)


def test_repartir_por_cercania_parte_el_conflicto_a_la_mitad(gdf):
    # solape de 20 m (x 80-100): el borde justo debe caer en x=90
    a = gdf(Polygon([(0, 0), (100, 0), (100, 100), (0, 100)]))
    b = gdf(Polygon([(80, 0), (180, 0), (180, 100), (80, 100)]))
    na, nb, conflicto = geo.repartir_por_cercania(a, b, nombres=("A", "B"))
    assert na.geometry.area.iloc[0] == pytest.approx(90 * 100, rel=0.02)
    assert nb.geometry.area.iloc[0] == pytest.approx(90 * 100, rel=0.02)
    assert conflicto.geometry.area.sum() == pytest.approx(20 * 100, rel=0.02)
    assert set(conflicto["gana"]) == {"A", "B"}


def test_repartir_por_cercania_sin_solape_no_toca_nada(gdf, cuadrado):
    a = gdf(cuadrado(0, 0, 100))
    b = gdf(cuadrado(500, 0, 100))
    na, nb, conflicto = geo.repartir_por_cercania(a, b)
    assert len(conflicto) == 0
    assert na.geometry.area.iloc[0] == pytest.approx(100 * 100)
    assert nb.geometry.area.iloc[0] == pytest.approx(100 * 100)


def test_repartir_por_cercania_exige_una_entidad(gdf, cuadrado):
    a = gdf(cuadrado(0, 0, 100), cuadrado(300, 0, 100))
    b = gdf(cuadrado(50, 0, 100))
    with pytest.raises(ValueError, match="una entidad por capa"):
        geo.repartir_por_cercania(a, b)


def test_superficie_ha_no_redondea(gdf, cuadrado):
    # 100.5 m2 = 0.01005 ha: calcular_superficie lo dejaría en 0.01 y la
    # comparación contra una tolerancia de 0.01 ha daría un falso "cuadra"
    g = gdf(Polygon([(0, 0), (100.5, 0), (100.5, 1), (0, 1)]))
    assert geo.superficie_total_ha(g) == pytest.approx(0.01005)


def test_pendiente_sostenida_curvas_rectas(gdf, cuadrado):
    # 3 curvas de 100 m en un cuadro de 100x100 => separacion 10000/300 = 33.3 m,
    # y con equidistancia 20 la cartografía sostiene 60%. Sin zigzag los dos
    # estimadores coinciden, asi que este caso no distingue cual se usa.
    zona = gdf(cuadrado(0, 0, 100))
    curvas = gdf(*[LineString([(0, y), (100, y)]) for y in (25, 50, 75)])
    sep, pct = geo.pendiente_sostenida(curvas, zona, equidistancia=20)
    assert sep == pytest.approx(100 / 3, rel=1e-6)
    assert pct == pytest.approx(60, rel=1e-6)


def _diente_sierra(y, largo=100, q=0.3, amp=0.15):  # amp=q/2 -> tortuosidad sqrt(2)
    """Curva en diente de sierra: misma extension, largo real sqrt(2) veces mayor."""
    n = int(largo / q) + 1
    return LineString([(i * q, y + (amp if i % 2 else -amp)) for i in range(n)])


def test_pendiente_sostenida_no_la_hunde_el_zigzag(gdf, cuadrado):
    """La separacion es la PERPENDICULAR entre curvas, no area/largo_real.

    Tres curvas separadas 25 m, cada una con un diente de sierra de amplitud
    0.3 m (mucho mas fino que el `paso` de 2.5 m de la banda). La separacion
    real no cambia: el diente no aleja las curvas. Pero el largo REAL se
    multiplica por sqrt(2), asi que `area/largo` la hunde por ese mismo factor.

    Con `area/largo` aqui y G'(0) en `separacion_media`, el barrido compararia
    dos estimadores distintos.
    Con zigzag 1.002 daba igual; medido en un predio con zigzag 1.41, no.
    """
    zona = gdf(cuadrado(0, 0, 100))
    rectas = gdf(*[LineString([(0, y), (100, y)]) for y in (25, 50, 75)])
    sierra = gdf(*[_diente_sierra(y) for y in (25, 50, 75)])

    sep_recta, _ = geo.pendiente_sostenida(rectas, zona, equidistancia=20)
    sep_sierra, _ = geo.pendiente_sostenida(sierra, zona, equidistancia=20)

    # el largo real si se multiplica por ~sqrt(2): es lo que hundia al estimador viejo
    tortuosidad = geo.longitud_total_m(sierra) / geo.longitud_total_m(rectas)
    assert tortuosidad == pytest.approx(2**0.5, rel=0.02)

    # ...y aun asi la separacion casi no se mueve: 31.5 contra 33.3, -5 %.
    # El estimador VIEJO habria dado 33.3/1.41 = 23.6, o sea -29 %.
    assert sep_sierra == pytest.approx(sep_recta, rel=0.10)
    assert sep_sierra > 30

    # El -5 % que queda NO es ruido, es la propiedad del estimador: la banda de
    # `paso` alrededor de una curva ondulada sale mas ancha que 2*paso por la
    # amplitud del diente, asi que `longitud_efectiva` se pasa por ~amp/paso
    # (aqui 0.15/2.5 = 6 %) y λ se queda corta por lo mismo. G'(0) limpia el
    # zigzag MAS FINO que `paso`, no todo el zigzag. Con ondulacion del orden de
    # `paso` o mayor, los dos estimadores convergen y ninguno la quita.
    residual = 1 - sep_sierra / sep_recta
    assert 0.02 < residual < 0.09


def _rangos_prueba():
    return [("0-45", 0, 45), ("45-60", 45, 60), (">60", 60, float("inf"))]


def test_sostenimiento_por_clase_acumula_de_arriba_abajo(gdf, cuadrado):
    """Cada corte mide la union de SU clase y todas las superiores.

    Dos franjas contiguas, una etiquetada '>60' y otra '45-60'. El corte 60
    mide solo la primera; el corte 45 mide las dos juntas. Es exactamente
    reagrupar `rangos` con la abierta empezando en cada `p`, sin una corrida
    del barrido por cada corte.
    """
    alta = gdf(cuadrado(0, 0, 100), **{"clase": [">60"]})
    media = gdf(cuadrado(0, 100, 100), **{"clase": ["45-60"]})
    zona = geo.fusionar([alta, media])
    curvas = gdf(*[LineString([(0, y), (100, y)]) for y in range(10, 200, 20)])

    filas = geo.sostenimiento_por_clase(
        zona, curvas, "clase", _rangos_prueba(), equidistancia=20
    )
    assert [f["corte"] for f in filas] == [60, 45]  # de mayor a menor
    assert filas[0]["etiquetas"] == [">60"]
    assert filas[1]["etiquetas"] == ["45-60", ">60"]  # acumulada
    assert filas[0]["area_ha"] == pytest.approx(1.0)
    assert filas[1]["area_ha"] == pytest.approx(2.0)
    # el corte 0 no entra: '>= 0' es el predio entero, no una clase
    assert all(f["corte"] > 0 for f in filas)


def test_sostenimiento_por_clase_piso_local_es_la_mitad(gdf, cuadrado):
    """`piso_local` = `s_sostenida`/2, el mismo `antialias` con la λ LOCAL.

    Es el numero que en un predio real dio vuelta al veredicto: el piso GLOBAL decia 37 m
    y el de la clase era 15.5. Si estos dos dejan de cuadrar, la comparacion
    `piso_local <= h <= s_sost` deja de significar lo que dice.
    """
    zona = gdf(cuadrado(0, 0, 100), **{"clase": [">60"]})
    curvas = gdf(*[LineString([(0, y), (100, y)]) for y in (25, 50, 75)])
    f = geo.sostenimiento_por_clase(
        zona, curvas, "clase", _rangos_prueba(), equidistancia=20
    )[0]
    assert f["piso_local"] == pytest.approx(f["s_sostenida"] / 2)
    # misma formula que resolucion_regla, con la λ de ESTA clase
    *_, techos = geo.resolucion_regla(
        20, 400, _rangos_prueba(), lambda_m=f["s_sostenida"]
    )
    assert techos["antialias"] == pytest.approx(f["piso_local"])


def test_sostenimiento_por_clase_sin_piezas_da_nan(gdf, cuadrado):
    """Un corte sin poligonos no se cita, y NaN != NaN lo delata."""
    zona = gdf(cuadrado(0, 0, 100), **{"clase": ["45-60"]})
    curvas = gdf(*[LineString([(0, y), (100, y)]) for y in (25, 50, 75)])
    filas = geo.sostenimiento_por_clase(
        zona, curvas, "clase", _rangos_prueba(), equidistancia=20
    )
    alto = filas[0]  # corte 60: no hay ningun poligono '>60'
    assert alto["n_piezas"] == 0
    assert alto["s_sostenida"] != alto["s_sostenida"]
    assert alto["piso_local"] != alto["piso_local"]


def test_pendiente_sostenida_sin_curvas_da_nan(gdf, cuadrado):
    # zona sin ninguna curva dentro: nada que la sostenga, y NaN != NaN lo delata
    zona = gdf(cuadrado(0, 0, 100))
    curvas = gdf(LineString([(500, 0), (500, 100)]))
    sep, pct = geo.pendiente_sostenida(curvas, zona, equidistancia=20)
    assert sep != sep and pct != pct


def test_separacion_media_tres_curvas_paralelas(gdf, cuadrado):
    # mismo montaje que test_pendiente_sostenida_area_sobre_largo: separacion 100/3
    zona = gdf(cuadrado(0, 0, 100))
    curvas = gdf(*[LineString([(0, y), (100, y)]) for y in (25, 50, 75)])
    lam, largo_ef, zigzag = geo.separacion_media(curvas, zona, paso=2.5)
    assert lam == pytest.approx(100 / 3, rel=1e-2)
    assert zigzag == pytest.approx(1.0, rel=1e-2)


def test_separacion_media_h_deriva_paso_a_la_mitad(gdf, cuadrado):
    zona = gdf(cuadrado(0, 0, 100))
    curvas = gdf(*[LineString([(0, y), (100, y)]) for y in (25, 50, 75)])
    con_h = geo.separacion_media(curvas, zona, resolucion=5.0)  # paso = h/2 = 2.5
    con_paso = geo.separacion_media(curvas, zona, paso=2.5)
    assert con_h == con_paso


def test_separacion_media_sin_curvas_da_nan(gdf, cuadrado):
    zona = gdf(cuadrado(0, 0, 100))
    curvas = gdf(LineString([(500, 0), (500, 100)]))
    lam, largo_ef, zigzag = geo.separacion_media(curvas, zona, paso=2.5)
    assert lam != lam and largo_ef != largo_ef and zigzag != zigzag


def test_auditar_rangos_estructura_basica(gdf, cuadrado):
    zona = gdf(cuadrado(0, 0, 100))
    curvas = gdf(*[LineString([(0, y), (100, y)]) for y in (0, 20, 40, 60, 80, 100)])
    clase = gdf(cuadrado(0, 0, 100), PENDIENTES=[">100"])
    distancia, mascara, _ = geo.raster_distancia(curvas, zona, resolucion=5)
    filas = geo.auditar_rangos(
        clase,
        curvas,
        "PENDIENTES",
        [(">100", 100, float("inf"))],
        20,
        distancia,
        mascara,
        5,
    )
    assert len(filas) == 1
    fila = filas[0]
    assert set(fila) == {
        "etiqueta",
        "area_mde_ha",
        "acumulado_ha",
        "techo_ha",
        "s_sostenida",
        "p_sostenida",
        "bandera",
    }
    assert fila["area_mde_ha"] == pytest.approx(1.0)
    assert fila["p_sostenida"] == fila["p_sostenida"]  # curvas SI caen dentro: no NaN


def test_auditar_rangos_salta_rango_sin_limite_inferior(gdf, cuadrado):
    zona = gdf(cuadrado(0, 0, 100))
    curvas = gdf(LineString([(0, 50), (100, 50)]))
    clase = gdf(cuadrado(0, 0, 100), PENDIENTES=["0-5"])
    distancia, mascara, _ = geo.raster_distancia(curvas, zona, resolucion=5)
    filas = geo.auditar_rangos(
        clase,
        curvas,
        "PENDIENTES",
        [("0-5", 0, 5)],
        20,
        distancia,
        mascara,
        5,
    )
    assert filas == []


def test_auditar_rangos_callado_no_imprime(gdf, cuadrado, capsys, monkeypatch):
    monkeypatch.setattr(geo, "MOSTRAR", False)
    zona = gdf(cuadrado(0, 0, 100))
    curvas = gdf(*[LineString([(0, y), (100, y)]) for y in (0, 50, 100)])
    clase = gdf(cuadrado(0, 0, 100), PENDIENTES=[">100"])
    distancia, mascara, _ = geo.raster_distancia(curvas, zona, resolucion=5)
    geo.auditar_rangos(
        clase,
        curvas,
        "PENDIENTES",
        [(">100", 100, float("inf"))],
        20,
        distancia,
        mascara,
        5,
    )
    assert capsys.readouterr().out == ""


def test_recortar_a_mascara(gdf, cuadrado):
    g = gdf(cuadrado(0, 0, 100))
    mascara = gdf(cuadrado(0, 0, 50))
    r = geo.recortar(g, mascara)
    assert r.geometry.area.sum() == pytest.approx(50 * 50)


def test_recortar_descarta_esquirla_no_poligono(gdf, cuadrado, tmp_path):
    # un polígono solapa de verdad; otro solo toca la máscara en la esquina
    # (100,100), que el clip devuelve como POINT. keep_geom_type debe tirarlo,
    # si no, guardar a shapefile (tipo único) truena con FeatureError.
    mascara = gdf(cuadrado(0, 0, 100))
    capa = gdf(cuadrado(50, 50, 100), cuadrado(100, 100, 100))
    r = geo.recortar(capa, mascara)
    assert set(r.geometry.geom_type) <= {"Polygon", "MultiPolygon"}
    assert len(r) == 1
    geo.guardar(r, tmp_path / "out.shp")  # no debe lanzar


def test_subdividir_parte_poligono_grande(gdf, cuadrado):
    # 4 ha en un cuadrado de 200 m; max 1 ha => varias partes, todas <= 1 ha
    g = geo.calcular_superficie(gdf(cuadrado(lado=200)))
    r = geo.subdividir_por_area(g, area_max_ha=1)
    assert len(r) > 1
    assert (r["superficie_ha"] <= 1 + 1e-6).all()


def test_eliminar_menores_sin_campo_falla(gdf, cuadrado):
    g = gdf(cuadrado())
    with pytest.raises(ValueError):
        geo.eliminar_menores(g, area_min_ha=1, area_max_ha=10)


def test_eliminar_menores_absorbe_vecino(gdf, cuadrado):
    # dos cuadrados que se tocan; uno pequeño se fusiona con el grande
    grande = cuadrado(0, 0, 100)  # 1 ha
    chico = cuadrado(100, 0, 20)  # 0.04 ha, toca al grande
    g = geo.calcular_superficie(gdf(grande, chico))
    r = geo.eliminar_menores(g, area_min_ha=0.5, area_max_ha=10)
    assert len(r) == 1


def test_union_espacial_pega_columna_sin_cortar(gdf, cuadrado):
    # punto dentro del cuadrado hereda su atributo; geometría del punto intacta
    punto = gdf(Point(50, 50))
    zonas = gdf(cuadrado(0, 0, 100), zona=["A"])
    r = geo.union_espacial(punto, zonas)
    assert r["zona"].iloc[0] == "A"
    assert r.geometry.iloc[0].equals(Point(50, 50))
    assert "index_right" not in r.columns


def test_union_espacial_reproyecta_otro(gdf, cuadrado):
    punto = gdf(Point(50, 50))
    zonas = gdf(cuadrado(0, 0, 100), zona=["A"]).to_crs(4326)  # otro CRS
    r = geo.union_espacial(punto, zonas)
    assert r["zona"].iloc[0] == "A"


def test_union_espacial_avisa_cuando_duplica(gdf, cuadrado, capsys):
    # un cuadrado que pisan dos zonas sale dos veces; el indice original vuelve
    rodal = gdf(cuadrado(0, 0, 10)).set_axis([7])
    zonas = gdf(cuadrado(0, 0, 5), cuadrado(5, 0, 5), zona=["A", "B"])
    r = geo.union_espacial(rodal, zonas)
    assert len(r) == 2 and list(r.index) == [7, 7]
    assert "1 entidad(es) cumplen con varias" in capsys.readouterr().out


def test_calcular_longitud_metros_y_km(gdf):
    linea = gdf(LineString([(0, 0), (0, 1000)]))  # 1000 m
    r = geo.calcular_longitud(linea)
    assert r["longitud_m"].iloc[0] == pytest.approx(1000)
    rk = geo.calcular_longitud(linea, unidad="km")
    assert rk["longitud_km"].iloc[0] == pytest.approx(1.0)


def test_calcular_coordenadas_decimal_utm_y_gms(gdf):
    """Un punto en UTM 13N en las tres salidas, y el acarreo de 59.996" a minuto."""
    p = gdf(Point(-104.58936, 20.54151), Point(0.5, -(10 + 59.996 / 3600)), crs=4326)
    r = geo.calcular_coordenadas(p)
    assert r["LAT"].iloc[0] == 20.54151 and r["LON"].iloc[0] == -104.58936
    u = geo.calcular_coordenadas(p.iloc[:1], crs=32613)
    assert u["X"].iloc[0] == pytest.approx(542806.57) and u.crs == p.crs
    g = geo.calcular_coordenadas(p, formato="gms")
    assert g["LAT_GMS"].tolist() == ["20°32'29.44\" N", "10°01'00.00\" S"]
    assert g["LON_GMS"].tolist() == ["104°35'21.70\" O", "0°30'00.00\" E"]
    with pytest.raises(ValueError, match="proyectado"):
        geo.calcular_coordenadas(p, crs=32613, formato="gms")
    with pytest.raises(ValueError, match="entidad_a_punto"):
        geo.calcular_coordenadas(gdf(LineString([(0, 0), (1, 1)])))


def test_medidas_en_cualquier_unidad_y_crs(gdf, cuadrado, capsys):
    """Un cuadrado de 1 ha mide lo mismo en UTM, en grados y en un CRS en pies.

    En grados mide sobre el elipsoide (antes: un número en grados² con nombre de
    hectáreas); en pies convierte (antes: ft² con nombre de m²).
    """
    utm = gdf(cuadrado(500000, 2000000, 100))  # 1 ha, 400 m de perímetro
    en_pies = utm.to_crs("+proj=utm +zone=13 +datum=WGS84 +units=ft")
    for capa in (utm, utm.to_crs(4326), en_pies):
        assert geo.superficie_total(capa) == pytest.approx(1.0, rel=2e-3)
        assert geo.superficie_total(capa, "m2") == pytest.approx(10_000, rel=2e-3)
        assert geo.longitud_total(capa, "km") == pytest.approx(0.4, rel=2e-3)
        r = geo.calcular_superficie(geo.calcular_longitud(capa, unidad="km"), unidad="km2")
        assert r["superficie_km2"].iloc[0] == pytest.approx(0.01, abs=0.005)
        assert r["longitud_km"].iloc[0] == pytest.approx(0.4, abs=0.005)
    # la guarda de `guardar` reconoce la columna en km2 y no da aviso falso
    geo.archivo._avisar_medida_obsoleta(geo.calcular_superficie(utm, unidad="km2", decimales=6))
    assert "no cuadra" not in capsys.readouterr().out
    with pytest.raises(ValueError, match="unidad de área 'acre'"):
        geo.superficie_total(utm, "acre")


def test_simplificar_reduce_vertices_sin_muta(gdf):
    # línea con vértice intermedio redundante colineal
    linea = gdf(LineString([(0, 0), (5, 0.0001), (10, 0)]))
    n0 = len(linea.geometry.iloc[0].coords)
    r = geo.simplificar(linea, tolerancia=1)
    assert len(r.geometry.iloc[0].coords) < n0
    assert len(linea.geometry.iloc[0].coords) == n0  # original intacto


def test_campo_seguro_no_choca(gdf, cuadrado):
    g = gdf(cuadrado(), tipo=["a"])
    assert geo.nombre_campo_libre(g, "nuevo") == "nuevo"


# ---- ancho_banda: la `w` medida --------------------------------------------


def _curvas_escalon(crs, separacion=10.0, largo=100.0):
    """Dos cotas paralelas separadas `separacion` m. La banda medida ES ese numero."""
    import geopandas as gpd

    return gpd.GeoDataFrame(
        {"cota": [0.0, 20.0]},
        geometry=[
            LineString([(0, 0), (largo, 0)]),
            LineString([(0, separacion), (largo, separacion)]),
        ],
        crs=crs,
    )


def _zona(crs, largo=100.0):
    import geopandas as gpd
    from shapely.geometry import box

    return gpd.GeoDataFrame(geometry=[box(0, -5, largo, 15)], crs=crs)


def test_ancho_banda_recupera_la_separacion_real(gdf, cuadrado):
    """Curvas paralelas a 10 m: `w` tiene que salir 10, no el geometrico 20.

    Es la prueba de que la escalera mide la banda y no la repite de la
    equidistancia. Con e=20 y p=100 % el ancho GEOMETRICO es 20 m; el real, 10.
    """
    crs = gdf(cuadrado()).crs
    curvas = _curvas_escalon(crs)
    _, resumen = geo.ancho_banda(curvas, "cota", _zona(crs), equidistancia=20, zigzag=1.0)
    assert resumen["ancho_m"] == pytest.approx(10.0, abs=0.2)
    assert resumen["ancho_geometrico"] == pytest.approx(20.0)
    assert resumen["largo_efectivo_m"] == pytest.approx(100.0, abs=0.5)


def test_ancho_banda_el_zigzag_se_cancela_en_w(gdf, cuadrado):
    """`w` no depende del zigzag (divide area y largo por igual); el blanco SI.

    Contraintuitivo y por eso fijado: quien pase `zigzag=1.0` por descuido no
    mueve `w` ni un decimal, pero infla `area_ha`, que es la cifra que se cita
    como blanco cartografico.
    """
    crs = gdf(cuadrado()).crs
    curvas, zona = _curvas_escalon(crs), _zona(crs)
    _, uno = geo.ancho_banda(curvas, "cota", zona, 20, zigzag=1.0)
    _, dos = geo.ancho_banda(curvas, "cota", zona, 20, zigzag=1.4)
    assert uno["ancho_m"] == pytest.approx(dos["ancho_m"])
    assert uno["area_ha"] == pytest.approx(dos["area_ha"] * 1.4)


def test_ancho_banda_siempre_cierra_la_banda(gdf, cuadrado):
    """`s_max` entra en la escalera aunque `cortes` no lo traiga.

    Si el ultimo corte no llega a `s_max`, el largo efectivo sale corto y `w`
    inflada. Se añade en vez de fallar: es un descuido barato de corregir.
    """
    crs = gdf(cuadrado()).crs
    tabla, resumen = geo.ancho_banda(
        _curvas_escalon(crs), "cota", _zona(crs), 20, 1.0, cortes=[8]
    )
    assert [f["distancia"] for f in tabla] == [8.0, 20.0]
    assert resumen["separacion_max"] == pytest.approx(20.0)


def test_ancho_banda_sin_banda_falla(gdf, cuadrado):
    """Curvas mas separadas que `s_max`: no hay clase. Es resultado, no cero."""
    crs = gdf(cuadrado()).crs
    curvas = _curvas_escalon(crs, separacion=200.0)
    zona = _zona(crs)
    with pytest.raises(ValueError, match="ninguna banda medible"):
        geo.ancho_banda(curvas, "cota", zona, 20, 1.0)


def test_ancho_banda_devuelve_la_banda_con_la_que_conto(gdf, cuadrado):
    """La geometria devuelta ES la que produjo `largo_efectivo_m`, no otra.

    Es la cifra que el resumen numerico no puede dar: el % del escarpe
    cartografico que el MDE ve sale de intersecar ESTA banda con la clase del
    MDE. Si la geometria no reprodujera el largo que la funcion conto, las dos
    mitades de esa division vendrian de mediciones distintas.

    Se fija tambien la trampa del zigzag: `longitud_total_m(banda)` es largo de TRAZO
    y `largo_efectivo_m` ya viene dividido entre el zigzag.
    """
    crs = gdf(cuadrado()).crs
    curvas, zona = _curvas_escalon(crs), _zona(crs)
    tabla, resumen, banda = geo.ancho_banda(
        curvas, "cota", zona, 20, zigzag=1.4, devolver_banda=True
    )
    assert not banda.empty
    assert banda.crs == curvas.crs
    assert geo.longitud_total_m(banda) / 1.4 == pytest.approx(
        resumen["largo_efectivo_m"], rel=1e-6
    )
    # y sin la bandera la firma vieja sigue devolviendo dos cosas
    assert len(geo.ancho_banda(curvas, "cota", zona, 20, 1.4)) == 2
    assert [f["distancia"] for f in tabla] == [8.0, 12.0, 16.0, 20.0]


def test_ancho_banda_una_cota_falla(gdf, cuadrado):
    import geopandas as gpd

    crs = gdf(cuadrado()).crs
    una = gpd.GeoDataFrame(
        {"cota": [0.0]}, geometry=[LineString([(0, 0), (100, 0)])], crs=crs
    )
    with pytest.raises(ValueError, match="menos de dos cotas"):
        geo.ancho_banda(una, "cota", _zona(crs), 20, 1.0)


def test_campo_seguro_renombra_si_existe(gdf, cuadrado):
    g = gdf(cuadrado(), tipo=["a"])
    r = geo.nombre_campo_libre(g, "tipo")
    assert r == "tipo_1"


def _linea(gdf, *pts):
    return gdf(LineString(pts))


def test_concordancia_escalera_y_las_dos_direcciones(gdf):
    # red corrida 20 m y de la MITAD del largo: la precision la gana entera y
    # la cobertura no pasa del 50 %. Por eso se miden las dos.
    ref = _linea(gdf, (0, 0), (1000, 0))
    red = _linea(gdf, (0, 20), (500, 20))
    t = geo.concordancia_lineas(red, ref, [25, 10])
    assert list(t["tol_m"]) == [10, 25]  # sale ordenada
    assert t["precision_pct"].tolist() == pytest.approx([0, 100])
    # 51.5 y no 50: la punta redonda del buffer alcanza sqrt(25**2 - 20**2) = 15 m
    # mas de `ref`. Es geometria, no holgura.
    assert t["cobertura_pct"].iloc[1] == pytest.approx(51.5, abs=0.01)
    assert t["km_referencia"].iloc[0] == pytest.approx(1.0)


def test_concordancia_zona_mide_dentro_pero_casa_fuera(gdf):
    # la pareja de los ultimos 10 m de `ref` esta FUERA de la zona. Si la zona
    # recortara tambien lo que casa, la cobertura daria 0 en vez de 10.
    zona = gdf(box(0, -50, 100, 50))
    ref = _linea(gdf, (0, 0), (100, 0))
    red = gdf(LineString([(100, 0), (200, 0)]), LineString([(0, 40), (10, 40)]))
    t = geo.concordancia_lineas(red, ref, [10], zona=zona)
    assert t["cobertura_pct"].iloc[0] == pytest.approx(10, abs=0.1)
    assert t["km_lineas"].iloc[0] == pytest.approx(0.010)


def test_concordancia_falla_sin_largo_o_con_tol_cero(gdf):
    ref = _linea(gdf, (0, 0), (100, 0))
    with pytest.raises(ValueError, match="> 0"):
        geo.concordancia_lineas(ref, ref, [0])
    with pytest.raises(ValueError, match="sin largo"):
        geo.concordancia_lineas(ref, ref, [10], zona=gdf(box(500, 500, 600, 600)))


def test_bloques_ajedrez_alterna_y_parte_la_zona(gdf):
    zona = gdf(box(500, 0, 3500, 2000))  # no alineada: la rejilla se ancla en 0
    b = geo.bloques_ajedrez(zona, 1000)
    assert len(b) == 8  # 4 columnas (dos medias) x 2 filas
    assert geo.superficie_total_ha(b) == pytest.approx(geo.superficie_total_ha(zona))
    for _, c in b.iterrows():
        vecino = b[(b["fila"] == c["fila"]) & (b["col"] == c["col"] + 1)]
        if len(vecino):
            assert vecino["pliegue"].iloc[0] != c["pliegue"]
    # los del mismo pliegue solo se tocan en esquinas: la union no pierde area
    for p in (0, 1):
        s = b[b["pliegue"] == p]
        assert geo.superficie_total_ha(geo.disolver(s)) == pytest.approx(
            geo.superficie_total_ha(s)
        )
    with pytest.raises(ValueError, match="> 0"):
        geo.bloques_ajedrez(zona, 0)


# --- entidad a punto, cercano, thiessen y overlays ---


def test_entidad_a_punto_de_una_u_cae_dentro_solo_con_dentro(gdf):
    u = Polygon(
        [(0, 0), (30, 0), (30, 30), (20, 30), (20, 10), (10, 10), (10, 30), (0, 30)]
    )
    capa = gdf(u, nombre=["U"])
    dentro = geo.entidad_a_punto(capa)
    assert dentro.geometry.iloc[0].within(u)
    assert dentro["nombre"].iloc[0] == "U"
    # el centroide geometrico cae en el hueco de la U: esa es la trampa
    assert not geo.entidad_a_punto(capa, dentro=False).geometry.iloc[0].within(u)


def test_cercano_empate_deja_una_fila_y_avisa(gdf, capsys):
    capa = gdf(Point(0, 0), Point(10, 0), n=[1, 2])
    otro = gdf(Point(-1, 0), Point(1, 0), clave=["x", "y"])
    salida = geo.cercano(capa, otro, campo_id="clave")
    assert len(salida) == 2  # sjoin_nearest a pelo daria 3
    assert list(salida["DIST_M"]) == [1.0, 9.0]
    assert list(salida["ID_CERCA"]) == ["x", "y"]
    assert "1 entidades con empate" in capsys.readouterr().out


def test_cercano_max_distancia_deja_nan_y_crs_geografico_falla(gdf):
    capa = gdf(Point(0, 0), Point(100, 0))
    otro = gdf(Point(1, 0))
    salida = geo.cercano(capa, otro, max_distancia=5)
    assert salida["DIST_M"].iloc[0] == 1.0
    assert salida["DIST_M"].isna().iloc[1]
    with pytest.raises(ValueError, match="geográficas"):
        geo.cercano(gdf(Point(0, 0), crs=4326), gdf(Point(1, 0), crs=4326))


def test_puntos_sobre_linea_incluye_el_final(gdf):
    capa = gdf(LineString([(0, 0), (50, 0)]), camino=["C1"])
    salida = geo.puntos_sobre_linea(capa, 20)
    assert list(salida["CADENA_M"]) == [0, 20, 40, 50]
    assert [p.x for p in salida.geometry] == [0, 20, 40, 50]
    assert set(salida["camino"]) == {"C1"} and set(salida["ID_LINEA"]) == {0}
    assert list(geo.puntos_sobre_linea(capa, 20, incluir_final=False)["CADENA_M"]) == [
        0,
        20,
        40,
    ]
    # un multiple exacto no duplica el final
    assert list(geo.puntos_sobre_linea(capa, 25)["CADENA_M"]) == [0, 25, 50]


def test_puntos_sobre_linea_explota_multilineas(gdf):
    multi = MultiLineString([[(0, 0), (10, 0)], [(0, 5), (10, 5)]])
    salida = geo.puntos_sobre_linea(gdf(multi), 10)
    assert list(salida["ID_LINEA"]) == [0, 0, 1, 1]
    assert list(salida["CADENA_M"]) == [0, 10, 0, 10]


def test_identidad_y_union_areas(gdf, cuadrado):
    a = gdf(cuadrado(0, 0, 100), A=[1])
    b = gdf(cuadrado(50, 0, 100), B=[2])
    ident = geo.identidad(a, b)
    assert geo.superficie_total_ha(ident) == pytest.approx(1.0)  # el area de `a`
    assert len(ident) == 2 and set(ident.columns) >= {"A", "B"}
    union = geo.union(a, b)
    assert geo.superficie_total_ha(union) == pytest.approx(1.5)  # el area de la union
    assert len(union) == 3


def test_thiessen_asigna_por_contencion_y_parte_la_zona(gdf, cuadrado):
    # voronoi_polygons devuelve la celda de (5, 8) en segundo lugar, no tercero
    puntos = gdf(Point(0, 0), Point(10, 0), Point(5, 8), id=["a", "b", "c"])
    zona = gdf(cuadrado(-5, -5, 20))
    celdas = geo.thiessen(puntos, zona)
    for _, fila in celdas.iterrows():
        punto = puntos.loc[puntos["id"] == fila["id"]].geometry.iloc[0]
        assert fila.geometry.contains(punto)
    geo.comprobar_particion([celdas], zona, tolerancia_ha=1e-9)


def test_thiessen_puntos_duplicados_falla(gdf, cuadrado):
    puntos = gdf(Point(0, 0), Point(0, 0), Point(5, 5))
    with pytest.raises(ValueError, match="duplicados"):
        geo.thiessen(puntos, gdf(cuadrado(-5, -5, 20)))


def test_partir_por_linea_diagonal_y_linea_que_no_sale(gdf, cuadrado, capsys):
    rodal = gdf(cuadrado(0, 0, 10), rodal=["R1"])
    diagonal = gdf(LineString([(-1, -1), (11, 11)]))
    partes = geo.partir_por_linea(rodal, diagonal)
    assert len(partes) == 2 and set(partes["rodal"]) == {"R1"}
    assert geo.superficie_total_ha(partes) == pytest.approx(
        geo.superficie_total_ha(rodal)
    )
    assert capsys.readouterr().out == ""
    colgante = gdf(LineString([(5, -1), (5, 5)]))
    assert len(geo.partir_por_linea(rodal, colgante)) == 1
    assert "1 poligonos tocados" in capsys.readouterr().out


def test_partir_por_linea_camino_por_el_lindero_no_avisa(gdf, cuadrado, capsys):
    rodal = gdf(cuadrado(0, 0, 10))
    lindero = gdf(LineString([(0, -5), (0, 15)]))  # corre por el borde oeste
    assert len(geo.partir_por_linea(rodal, lindero)) == 1
    assert capsys.readouterr().out == ""


def test_medir_geodesico_area_positiva_en_los_dos_sentidos(gdf, cuadrado):
    # 1 km x 1 km en el meridiano central de UTM 13N: factor de escala 0.9996,
    # así que el elipsoide da ~0.08 % MÁS que el plano, en los dos sentidos
    c = cuadrado(x=500000, y=2270000, lado=1000)
    r = geo.medir_geodesico(gdf(c, c.reverse()))
    assert (r["SUP_GEO"] > 100).all() and (r["SUP_GEO"] < 100.2).all()
    assert r["SUP_GEO"].iloc[0] == r["SUP_GEO"].iloc[1]
    assert r["LONG_GEO"].iloc[0] == pytest.approx(4000 / 0.9996, rel=1e-3)


def test_medir_geodesico_linea_no_duplica_largo(gdf):
    r = geo.medir_geodesico(gdf(LineString([(500000, 2270000), (500000, 2271000)])))
    assert r["SUP_GEO"].iloc[0] == 0
    assert r["LONG_GEO"].iloc[0] == pytest.approx(1000 / 0.9996, rel=1e-3)


ESCALERA_IZQ = Polygon(
    [(-50, 0), (-50, 100), (0, 100), (10, 100), (10, 90), (20, 90), (20, 80),
     (30, 80), (30, 70), (40, 70), (40, 60), (50, 60), (50, 0)]
)  # fmt: skip
ESCALERA_DER = Polygon(
    [(50, 0), (50, 60), (40, 60), (40, 70), (30, 70), (30, 80), (20, 80),
     (20, 90), (10, 90), (10, 100), (100, 100), (100, 0)]
)  # fmt: skip


def test_simplificar_conserva_la_particion_y_el_borde_exterior(gdf):
    capa = gdf(ESCALERA_IZQ, ESCALERA_DER)
    r = geo.simplificar(capa, 10)
    geo.comprobar_particion([r], gdf(box(-50, 0, 100, 100)), tolerancia_ha=1e-9)
    antes = capa.geometry.count_coordinates()
    assert (r.geometry.count_coordinates() < antes).all()
    assert r.geometry.union_all().area == pytest.approx(
        capa.geometry.union_all().area, abs=1e-6
    )


def test_simplificar_encimados_falla_y_manda_a_comprobar_particion(gdf):
    with pytest.raises(ValueError, match="comprobar_particion"):
        geo.simplificar(gdf(box(0, 0, 60, 100), box(50, 0, 100, 100)), 10)


def _red_rodeo(gdf):
    """Brecha lenta recta de (0,0) a (200,0), o rodeo por una avenida a y=100.

    La calle de la derecha esta dibujada hacia abajo y es de un sentido, con la
    etiqueta en `other_tags` como la deja el driver OSM de GDAL.
    """
    return gdf(
        LineString([(0, 0), (100, 0), (200, 0)]),
        LineString([(0, 100), (100, 100), (200, 100)]),
        LineString([(0, 0), (0, 100)]),
        LineString([(200, 100), (200, 0)]),
        tipo=["brecha", "avenida", "calle", "calle"],
        other_tags=[None, None, None, '"oneway"=>"yes","surface"=>"asphalt"'],
    )


def test_ruta_red_rodea_por_lo_rapido_y_respeta_el_sentido(gdf):
    red = _red_rodeo(gdf)
    vel = {"brecha": 5, "avenida": 60, "calle": 20}
    ida = gdf(Point(0, 0), Point(200, 0))
    vuelta = gdf(Point(200, 0), Point(0, 0))

    # rodeo: 100 m a 20 + 200 m a 60 + 100 m a 20 = 48 s, contra 144 s recto
    r = geo.ruta_red(red, ida, vel, campo="tipo", campo_sentido="oneway")
    assert r["METROS"].iloc[0] == pytest.approx(400)
    assert r["MINUTOS"].iloc[0] == pytest.approx(48 / 60)
    assert r.geometry.iloc[0].length == pytest.approx(400)

    # de vuelta la calle de un sentido no se sube: queda la brecha recta
    r = geo.ruta_red(red, vuelta, vel, campo="tipo", campo_sentido="oneway")
    assert r["METROS"].iloc[0] == pytest.approx(200)
    assert r["MINUTOS"].iloc[0] == pytest.approx(144 / 60)

    # sin sentidos, la vuelta tambien rodea
    r = geo.ruta_red(red, vuelta, vel, campo="tipo")
    assert r["METROS"].iloc[0] == pytest.approx(400)


def test_ruta_red_dice_que_tipo_no_tiene_velocidad(gdf):
    red = _red_rodeo(gdf)
    with pytest.raises(ValueError, match=r"'calle' \(2 vías\)"):
        geo.ruta_red(red, gdf(Point(0, 0), Point(200, 0)), {"brecha": 5, "avenida": 60}, campo="tipo")


def test_ruta_red_sentido_que_bloquea_lo_dice(gdf):
    red = gdf(LineString([(0, 0), (100, 0)]), sentido=["yes"])
    with pytest.raises(ValueError, match="sentidos únicos de 'sentido'"):
        geo.ruta_red(red, gdf(Point(100, 0), Point(0, 0)), 30, campo_sentido="sentido")


def test_ruta_red_sentido_numerico_y_extremos_a_milimetros(gdf):
    # una columna numerica llega como 1.0, no como "1": comparando texto saldria doble sentido
    red = gdf(LineString([(0, 0), (100, 0)]), sentido=[1.0])
    with pytest.raises(ValueError, match="sentidos únicos"):
        geo.ruta_red(red, gdf(Point(100, 0), Point(0, 0)), 30, campo_sentido="sentido")
    # extremos a 2 mm a ambos lados de una linea de la reja de 1 cm: antes, red partida
    red = gdf(LineString([(0, 0), (100, 0.004)]), LineString([(100, 0.006), (200, 0)]))
    r = geo.ruta_red(red, gdf(Point(0, 0), Point(200, 0)), 30)
    assert r["METROS"].iloc[0] == pytest.approx(200, abs=0.01)


def test_ruta_red_cruce_sin_vertice_es_red_partida_salvo_con_nodar(gdf):
    # cruce en (100, 50) que ninguna de las dos lineas tiene como vertice: CAD tipico
    red = gdf(LineString([(0, 50), (200, 50)]), LineString([(100, 0), (100, 100)]))
    puntos = gdf(Point(0, 50), Point(100, 0))
    with pytest.raises(ValueError, match="partida en 2 piezas.*nodar=True"):
        geo.ruta_red(red, puntos, 30)
    r = geo.ruta_red(red, puntos, 30, nodar=True)
    assert r["METROS"].iloc[0] == pytest.approx(150)


def test_isocronas_corta_a_media_calle_y_junta_dos_origenes(gdf):
    # 1 km sin vertices intermedios a 60 km/h = 1 km/min: los cortes caen a media calle
    calle = gdf(LineString([(0, 0), (1000, 0)]))
    r = geo.isocronas(calle, gdf(Point(0, 0)), 60, cortes=[0.25, 0.5])
    assert list(r["KM"]) == pytest.approx([0.25, 0.25])
    assert r.geometry.iloc[1].length == pytest.approx(250)

    # dos origenes, uno en cada punta: a 0.6 min se juntan y cubren el tramo
    r = geo.isocronas(calle, gdf(Point(0, 0), Point(1000, 0)), 60, cortes=[0.25, 0.6])
    assert list(r["KM"]) == pytest.approx([0.5, 0.5])


def test_isocronas_hacia_respeta_el_sentido(gdf):
    # de un sentido, alejandose del origen: se sale por ella pero no se llega
    calle = gdf(LineString([(0, 0), (1000, 0)]), sentido=["yes"])
    salir = geo.isocronas(calle, gdf(Point(0, 0)), 60, [0.5], campo_sentido="sentido")
    llegar = geo.isocronas(calle, gdf(Point(0, 0)), 60, [0.5], campo_sentido="sentido", hacia=True)
    assert salir["KM"].iloc[0] == pytest.approx(0.5)
    assert llegar["KM"].iloc[0] == 0 and llegar.geometry.iloc[0].is_empty


def test_isocronas_con_ancho_da_poligonos_que_no_se_pisan(gdf):
    calle = gdf(LineString([(0, 0), (1000, 0)]))
    r = geo.isocronas(calle, gdf(Point(0, 0)), 60, [0.25, 0.5], ancho=10)
    a, b = r.geometry
    assert a.intersection(b).area == pytest.approx(0, abs=1e-6)
    assert a.area + b.area == pytest.approx(LineString([(0, 0), (500, 0)]).buffer(10).area)


def test_isocronas_cortes_no_crecientes_falla(gdf):
    with pytest.raises(ValueError, match="crecientes"):
        geo.isocronas(gdf(LineString([(0, 0), (10, 0)])), gdf(Point(0, 0)), 30, [10, 5])
