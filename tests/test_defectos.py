"""Un test por defecto corregido, el mínimo que falla si el arreglo se deshace.

Los tres primeros son de falla SILENCIOSA: sin estos, deshacer el arreglo no
rompe nada visible y la cifra sale mal. Los demás solo comprueban que el error
llega con su nombre en vez de reventar tres frames abajo.
"""

import warnings

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio import Affine
from shapely.geometry import box, LineString, Polygon

import geophis as geo

UTM = 32613


def _renombrada(*geoms, crs=UTM, **cols):
    """Capa cuya columna de geometría activa NO se llama 'geometry'.

    Es lo que entrega un GeoPackage con la columna `geom`, o `rename_geometry`.
    `cargar()` siempre devuelve 'geometry', así que nada más en la suite lo crea.
    """
    datos = {"geometry": list(geoms)}
    datos.update(cols)
    return gpd.GeoDataFrame(datos, crs=crs).rename_geometry("geom")


# ---- 1. la columna de geometría, por nombre real ---------------------------


@pytest.mark.parametrize(
    "op",
    [
        lambda g: geo.zona_de_influencia(g, 10),
        lambda g: geo.simplificar(g, 1),
        lambda g: geo.reparar_geometrias(g),
        lambda g: geo.cerrar_microhuecos(g, 1),
        lambda g: geo.borrar(g, box(-50, -50, 0, 150)),
    ],
)
def test_ops_no_crean_una_columna_geometry_paralela(op):
    """El defecto: `salida["geometry"] = ...` sobre una capa con la geometría en
    `geom` añadía una columna NUEVA y dejaba la activa intacta. La capa parecía
    transformada y `superficie_total_ha` devolvía el área de antes, sin avisar."""
    capa = _renombrada(box(0, 0, 100, 100))
    salida = op(capa)
    assert salida.geometry.name == "geom"
    assert "geometry" not in salida.columns


def test_zona_de_influencia_sobre_columna_renombrada_si_cambia_el_area():
    """La mitad que de verdad importa: que el resultado no sea el de la entrada."""
    capa = _renombrada(box(0, 0, 100, 100))
    assert geo.superficie_total_ha(geo.zona_de_influencia(capa, 10)) > geo.superficie_total_ha(capa) * 1.4


def test_intersecar_acepta_columna_renombrada_y_geometria_suelta():
    capa = _renombrada(box(0, 0, 100, 100))
    otra = _renombrada(box(50, 0, 150, 100))
    assert geo.superficie_total_ha(geo.intersecar(capa, otra)) == pytest.approx(0.5)
    # geometría shapely suelta, como ya aceptaban `recortar` y `borrar`
    assert geo.superficie_total_ha(
        geo.intersecar(capa, box(50, 0, 150, 100))
    ) == pytest.approx(0.5)


def test_subdividir_y_fusionar_respetan_la_columna_renombrada():
    capa = _renombrada(box(0, 0, 1000, 1000), superficie_ha=[100.0])
    partes = geo.subdividir_por_area(capa, area_max_ha=30)
    assert partes.geometry.name == "geom" and len(partes) > 1
    assert geo.superficie_total_ha(partes) == pytest.approx(100.0)
    fusion = geo.eliminar_menores(partes, area_min_ha=30, area_max_ha=200)
    assert fusion.geometry.name == "geom"


def test_guardar_gpx_de_poligono_renombrado_exporta_el_contorno(tmp_path):
    """`_poligono_a_borde` con la columna renombrada exportaba el POLÍGONO."""
    capa = _renombrada(box(0, 0, 100, 100))
    ruta = str(tmp_path / "p.gpx")
    geo.guardar(capa, ruta)
    leido = gpd.read_file(ruta, layer="tracks")
    assert set(leido.geom_type) <= {"LineString", "MultiLineString"}


def test_fusionar_rechaza_columnas_de_geometria_distintas():
    """`concat` no falla con nombres distintos: alinea por nombre y saca DOS
    columnas de geometría medio vacías. La mitad de las entidades queda con la
    geometría fuera de la columna activa y `superficie_total_ha` mide media capa."""
    a = _renombrada(box(0, 0, 100, 100))
    b = gpd.GeoDataFrame({"geometry": [box(200, 0, 300, 100)]}, crs=UTM)
    with pytest.raises(ValueError, match="columna de geometría"):
        geo.fusionar([a, b])


def test_fusionar_conserva_la_columna_renombrada():
    a = _renombrada(box(0, 0, 100, 100))
    b = _renombrada(box(200, 0, 300, 100))
    unido = geo.fusionar([a, b])
    assert unido.geometry.name == "geom" and len(unido) == 2
    assert geo.superficie_total_ha(unido) == pytest.approx(2.0)


# ---- 3. los avisos no se pierden cuando el cuerpo lanza --------------------


def test_traducir_avisos_reemite_aunque_falle(tmp_path):
    """El `for` iba fuera del `with` y sin `finally`: una excepción se llevaba
    por delante los avisos de GDAL, que son justo los que explican el fallo."""
    from geophis.archivo import _traducir_avisos

    with warnings.catch_warnings(record=True) as visto:
        warnings.simplefilter("always")
        with pytest.raises(RuntimeError):
            with _traducir_avisos():
                warnings.warn("aviso desconocido que hay que ver", UserWarning)
                raise RuntimeError("el cuerpo revienta")
    assert any("desconocido" in str(a.message) for a in visto)


def test_traducir_avisos_no_entra_en_bucle(capsys):
    """Reemitir DENTRO del catch_warnings volvería a capturar en la misma lista
    que se recorre. Si esto no termina, el arreglo está mal hecho."""
    from geophis.archivo import _traducir_avisos

    with _traducir_avisos():
        warnings.warn("Column names longer than 10 characters", UserWarning)
    assert "10 caracteres" in capsys.readouterr().out


# ---- 6. el .tif declara el nodata que de verdad trae -----------------------


def test_guardar_raster_declara_nan(tmp_path):
    """Un .tif que decía -9999 y traía NaN: otros SIG leen esos NaN como dato."""
    ruta = str(tmp_path / "r.tif")
    tf = Affine(1, 0, 500000, 0, -1, 2000000)
    geo.guardar_raster(np.array([[1.0, np.nan]]), ruta, tf, UTM)
    with rasterio.open(ruta) as f:
        assert np.isnan(f.nodata)


# ---- 2, 5, 7, 9: el error llega con su nombre ------------------------------


def test_ancho_banda_acepta_un_generador_de_cortes():
    """`cortes` es `Iterable` y se recorría dos veces; un generador quedaba
    agotado y el segundo `max()` reventaba con 'empty sequence'."""
    curvas = gpd.GeoDataFrame(
        {"COTA": [i * 20.0 for i in range(6)]},
        geometry=[LineString([(0, i * 12), (400, i * 12)]) for i in range(6)],
        crs=UTM,
    )
    zona = gpd.GeoDataFrame(geometry=[box(20, 20, 380, 60)], crs=UTM)
    _, resumen = geo.ancho_banda(
        curvas,
        "COTA",
        zona,
        20.0,
        1.0,
        cortes=(x for x in (8, 12, 16, 20)),
    )
    assert resumen["ancho_m"] > 0


@pytest.mark.parametrize(
    "kw, mensaje",
    [
        ({"resolucion": 0}, "resolucion"),
        ({"intervalo_muestreo": 0}, "intervalo_muestreo"),
        ({"equidistancia": -1}, "equidistancia"),
    ],
)
def test_interpolar_mde_rechaza_parametros_no_positivos(kw, mensaje):
    """Con `intervalo_muestreo=0` el muestreo hacía `largos // 0` -> inf ->
    entero basura, y reservaba memoria hasta que el sistema mataba el proceso."""
    curvas = gpd.GeoDataFrame(
        {"COTA": [0.0, 20.0]},
        geometry=[LineString([(0, 0), (100, 0)]), LineString([(0, 50), (100, 50)])],
        crs=UTM,
    )
    with pytest.raises(ValueError, match=mensaje):
        geo.interpolar_mde(curvas, "COTA", **kw)


def test_remuestrear_rechaza_resolucion_no_positiva():
    with pytest.raises(ValueError, match="res_destino"):
        geo.remuestrear(np.zeros((4, 4)), Affine(1, 0, 0, 0, -1, 0), UTM, res_destino=0)


def test_detectar_utm_sin_crs_falla_diciendo_que_falta_el_crs():
    capa = gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)], crs=None)
    with pytest.raises(ValueError, match="CRS"):
        geo.detectar_utm(capa)


# ---- 8. el CLI sin terminal ------------------------------------------------


def test_cli_sin_terminal_salta_en_vez_de_reventar(tmp_path, monkeypatch, capsys):
    """`input()` sin tty lanza EOFError: salía el traceback crudo que
    `_Parser.error` existe para evitar."""
    from geophis import cli

    origen = tmp_path / "a.geojson"
    gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)], crs=UTM).to_file(origen)
    (tmp_path / "a.gpkg").write_bytes(b"ocupado")

    def _sin_terminal(_):
        raise EOFError

    monkeypatch.setattr("builtins.input", _sin_terminal)
    assert cli.main([str(origen), "gpkg"]) == 0
    assert "no hay terminal" in capsys.readouterr().out


# ---- 10. la rama hidrologica ---------------------------------------------


def test_guardar_avisa_de_longitud_obsoleta_igual_que_de_area(tmp_path, capsys):
    """El defecto: `guardar` vigilaba la columna de AREA y no la de LONGITUD.

    Mismo modo de fallo exacto, y en la rama hidrologica la longitud es la
    medida que SE CITA (densidad de drenaje = km de cauce / km2). Medido sobre
    la red de un predio real: `calcular_longitud` y luego `recortar` dejaba la
    columna diciendo 71.0 km sobre una geometria de 60.7, un 14 % de mas, y se
    escribia al shapefile sin una palabra.
    """
    red = gpd.GeoDataFrame({"geometry": [LineString([(0, 0), (0, 100)])]}, crs=UTM)
    red = geo.calcular_longitud(red)
    assert red["longitud_m"].iloc[0] == 100.0

    # el recorte mueve la geometria DESPUES de medirla
    red = geo.recortar(red, box(0, 0, 10, 40))
    geo.guardar(red, tmp_path / "red.shp")

    salida = capsys.readouterr().out
    assert "longitud_m" in salida and "no cuadra" in salida


def test_guardar_no_avisa_si_la_longitud_se_midio_al_final(tmp_path, capsys):
    """La otra mitad: una guarda que siempre grita no es una guarda."""
    red = gpd.GeoDataFrame({"geometry": [LineString([(0, 0), (0, 100)])]}, crs=UTM)
    red = geo.recortar(red, box(0, 0, 10, 40))
    red = geo.calcular_longitud(red)  # ULTIMO paso, como manda la regla
    geo.guardar(red, tmp_path / "red.shp")
    assert "no cuadra" not in capsys.readouterr().out


def test_el_aviso_de_gdal_no_nombra_un_formato_ajeno_y_trae_los_nombres(tmp_path, capsys):
    """El defecto: guardar un .SHP imprimia 'GPX renombro un campo'.

    Dos entradas de `_AVISOS_ES` tecleaban 'GPX' mientras que los avisos de GDAL
    de los que salen son genericos, asi que el mensaje mandaba a revisar el
    esquema de un formato que no se estaba usando: senalar al paso inocente.

    Y la traduccion tiraba el DETALLE. GDAL dice cual campo y con que nombre
    quedo (`'superficie_ha' to 'superfic_1'`), que es el nombre que hay que
    buscar en la tabla guardada; sin el, el aviso no es accionable.
    """
    capa = gpd.GeoDataFrame(
        {"geometry": [box(0, 0, 10, 10)], "SUPERFICIE": [1.0], "superficie_ha": [0.01]},
        crs=UTM,
    )
    geo.guardar(capa, tmp_path / "capa.shp")
    salida = capsys.readouterr().out

    assert "GPX" not in salida, "nombra un formato que no se esta usando"
    assert "renombro" in salida or "renombró" in salida
    assert "superficie_ha" in salida, "el aviso tiene que decir QUE campo"
    assert "superfic" in salida, "y con que nombre quedo"


def test_umbral_celdas_es_la_regla_y_no_un_entero_tecleado():
    """El `umbral` de la rama hidrologica va en CELDAS y no es portable.

    A 5 m/px, 1.25 ha son 500 celdas; a 20 m/px son 32. El script no puede
    escribir el entero ni la formula: es `derivado`, igual que `min_pixeles`.
    """
    assert geo.umbral_celdas(1.25, 5) == 500
    assert geo.umbral_celdas(1.25, 20) == 32  # 31.25 -> ceil

    # `ceil` y no `round`, por el mismo argumento que la UMM: es un MINIMO.
    assert geo.umbral_celdas(1.0, 30) == 12  # 11.11, round daria 11

    with pytest.raises(ValueError):
        geo.umbral_celdas(0, 5)
    with pytest.raises(ValueError):
        geo.umbral_celdas(1.25, 0)


def test_comprobar_nombres_shp_separa_el_corte_de_la_COLISION(gdf, cuadrado):
    """Las dos causas se arreglan distinto: una pide acortar, otra desambiguar.

    Los nombres son los medidos sobre GDAL: 'superficie_ha' sale 'superfic_1'
    porque 'SUPERFICIE' ya ocupa el corto (colision INSENSIBLE a mayusculas), y
    'aaaaaaaaaaaa' sale 'aaaaaaaaaa' por simple corte, sin colision.
    """
    capa = gdf(
        cuadrado(),
        SUPERFICIE=[1],
        superficie_ha=[2],
        DESAGUE_NORTE=[3],
        DESAGUE_NOMBRE=[4],
        ID_MICRO=[5],
        aaaaaaaaaaaa=[6],
    )
    p = geo.comprobar_nombres_shp(capa)

    # ID_MICRO cabe: `microcuencas` eligio sus columnas <=10 justo para esto.
    assert "ID_MICRO" not in p
    # corte a secas: no choca con nadie
    assert "caracteres" in p["aaaaaaaaaaaa"]
    # colision, y las DOS partes se nombran: quien lee el aviso tiene que saber
    # con quien choca para poder elegir el nombre nuevo.
    assert "choca" in p["superficie_ha"] and "SUPERFICIE" in p["superficie_ha"]
    assert "choca" in p["SUPERFICIE"] and "superficie_ha" in p["SUPERFICIE"]
    assert "choca" in p["DESAGUE_NORTE"] and "DESAGUE_NOMBRE" in p["DESAGUE_NORTE"]

    # una capa que ya eligio nombres cortos no da falso positivo: una guarda que
    # siempre grita no es guarda.
    assert geo.comprobar_nombres_shp(gdf(cuadrado(), ID_MICRO=[1], SUP=[2])) == {}


def test_tolerancia_rasterizacion_depende_del_CONTORNO_y_no_del_area():
    """El cierre raster->vector se tolera por PERIMETRO, no por superficie.

    El error vive en el borde: cada tramo del contorno cae hasta media celda
    dentro o fuera, asi que la banda es P*h/2. Dos zonas de la misma superficie
    toleran distinto si una es mas dentada, y la misma zona tolera el doble al
    doblar el pixel. Un numero tecleado no hace ninguna de las dos cosas.
    """
    # Predio real: P = 20966 m a 5 m/px. El deficit medido fue 1.30 ha.
    assert geo.tolerancia_rasterizacion_ha(20966, 5) == pytest.approx(5.2415)
    assert geo.tolerancia_rasterizacion_ha(20966, 10) == pytest.approx(10.483)

    # misma superficie, contorno mas largo -> mas tolerancia. Un cuadrado de
    # 1 ha mide 400 m de contorno; partido en 4 cuadrados sueltos, 800 m.
    assert geo.tolerancia_rasterizacion_ha(800, 5) == 2 * geo.tolerancia_rasterizacion_ha(
        400, 5
    )

    with pytest.raises(ValueError):
        geo.tolerancia_rasterizacion_ha(0, 5)
    with pytest.raises(ValueError):
        geo.tolerancia_rasterizacion_ha(20966, 0)


def test_cargar_bbox_lee_solo_la_ventana(tmp_path):
    """Sin `bbox` hay que cargar el estado entero para recortar a un predio.

    Medido sobre la hidrografia estatal (384 554 tramos, 133 MB) contra un
    cuadro de 1765 ha: 33.1 s / 763 MB sin bbox contra 7.1 s / 164 MB con el.
    No es solo lento: `ancho_banda` ya lleva su propio recorte por dentro
    porque sin el "agotaba la memoria y el sistema mataba el proceso sin
    traza", y esa defensa vivia dentro de UNA funcion.
    """
    ruta = tmp_path / "lineas.shp"
    gpd.GeoDataFrame(
        {"geometry": [LineString([(x, 0), (x, 10)]) for x in range(0, 100, 10)]},
        crs=UTM,
    ).to_file(ruta)

    assert len(geo.cargar(ruta)) == 10
    ventana = geo.cargar(ruta, bbox=box(-1, -1, 21, 11))
    assert len(ventana) == 3, "lee las que TOCAN la ventana, enteras"

    # y no es un recorte: la entidad vuelve completa, por eso `recortar` sigue
    # haciendo falta despues.
    assert ventana.geometry.length.max() == 10.0


def test_quemar_cauces_dice_por_que_no_quemo_nada(capsys):
    """El defecto: devolvia un `0` mudo y quien llama solo podia adivinar.

    Las dos causas se arreglan distinto (cauces en otro CRS, o de otro predio)
    y el 0 no las distingue. Paso de verdad en la primera corrida con la
    hidrografia estatal: `recortar` mueve el SEGUNDO argumento, asi que los
    cauces seguian en Conica Conforme de Lambert (x~2.4e6) sobre un raster en
    UTM 13N (x~5.3e5), y salieron 0 pixeles sin una palabra de por que.

    La guarda lleva la cifra.
    """
    mde = np.zeros((10, 10), dtype=float)
    tf = Affine(1.0, 0.0, 0.0, 0.0, -1.0, 10.0)  # raster en x[0, 10]
    lejos = gpd.GeoDataFrame(
        {"geometry": [LineString([(2_400_000, 5), (2_400_010, 5)])]}, crs=UTM
    )

    salida, n = geo.quemar_cauces(mde, lejos, tf, 5.0)
    assert n == 0 and np.array_equal(salida, mde), "el MDE vuelve intacto"

    aviso = capsys.readouterr().out
    assert "0 pixeles quemados" in aviso
    assert "2,400,000" in aviso and "CRS" in aviso, "tiene que ensenar los dos extents"


def test_quemar_cauces_no_avisa_cuando_si_quema(capsys):
    """La otra mitad: una guarda que siempre grita no es una guarda."""
    mde = np.zeros((10, 10), dtype=float)
    tf = Affine(1.0, 0.0, 0.0, 0.0, -1.0, 10.0)
    dentro = gpd.GeoDataFrame({"geometry": [LineString([(0, 5), (10, 5)])]}, crs=UTM)

    _, n = geo.quemar_cauces(mde, dentro, tf, 5.0)
    assert n > 0
    assert "0 pixeles quemados" not in capsys.readouterr().out


def test_detectar_cota_no_deja_ganar_a_una_columna_de_codigos(capsys):
    """El defecto: UNA fila mala mandaba la equidistancia de 20 m a 1 m.

    `regularidad_min` era un criterio de ORDEN y no una puerta, asi que la
    regularidad mandaba y el paso solo desempataba empates exactos. Con paso 1
    sobre enteros la regularidad es 1.00 SIEMPRE (todo entero es multiplo de
    1), asi que cualquier columna de codigos ganaba a una cota con el minimo
    defecto. La guarda de "enteros consecutivos" no la atrapa: una columna de
    IDs con huecos no es consecutiva.

    Medido sobre las curvas estatales, ventana predio+6000: una
    fila con 3302 m entre curvas de 1180 bajaba `ELEVACION` a 0.98 y ganaba
    `IDENTIFICA` con e=1 m. De la equidistancia cuelgan `interpolar_mde`,
    `resolucion_regla` y `margen_borde_celdas`.
    """
    cotas = list(range(340, 1200, 20)) + [3302]  # la fila mala del estatal
    ids = [135_829_510 + k for k in (0, 1, 5, 14, 51, 88, 130, 189, 260, 344)]
    ids += [ids[-1] + 400 * k for k in range(1, len(cotas) - len(ids) + 1)]

    capa = gpd.GeoDataFrame(
        {
            "geometry": [LineString([(0, y), (10, y)]) for y in range(len(cotas))],
            "IDENTIFICA": ids[: len(cotas)],
            "ELEVACION": cotas,
        },
        crs=UTM,
    )

    campo, e = geo.detectar_cota(capa)
    assert campo == "ELEVACION", "una columna de codigos gano a la cota"
    assert e == 20, f"equidistancia inventada: {e}"

    # y el reparto tiene que ensenar la fila mala, no taparla
    assert "2122" in capsys.readouterr().out


def test_detectar_cota_sigue_exigiendo_el_umbral():
    """La puerta sigue siendo puerta: sin ninguna escalera, falla con puntajes."""
    capa = gpd.GeoDataFrame(
        {
            "geometry": [LineString([(0, y), (1, y)]) for y in range(6)],
            "ruido": [0.0, 1.7, 3.1, 9.4, 11.03, 40.9],
        },
        crs=UTM,
    )
    with pytest.raises(ValueError, match="escalera"):
        geo.detectar_cota(capa)


_IZQ = [(-50, 0), (-50, 100), (0, 100), (10, 100), (10, 90), (20, 90), (20, 80),
        (30, 80), (30, 70), (40, 70), (40, 60), (50, 60), (50, 0)]  # fmt: skip
_DER = [(50, 0), (50, 60), (40, 60), (40, 70), (30, 70), (30, 80), (20, 80),
        (20, 90), (10, 90), (10, 100), (100, 100), (100, 0)]  # fmt: skip


def _escalera():
    """Dos vecinos con borde en escalera de pixel de 10 m, como sale de `poligonizar`."""
    return gpd.GeoDataFrame(geometry=[Polygon(_IZQ), Polygon(_DER)], crs=UTM)


def test_simplificar_por_poligono_rompe_la_particion():
    """Por que existe el camino por cobertura: simplificando cada vecino por su
    cuenta, cada lado de la arista compartida se mueve distinto (40 ha de hueco
    en un predio real). Tolerancia 5 y no
    10: con 10 la escalera simetrica cae en la misma diagonal por los dos lados;
    con 5 deja 50 m2 de hueco y 50 de solape."""
    zona = gpd.GeoDataFrame(geometry=[box(-50, 0, 100, 100)], crs=UTM)
    r = geo.simplificar(_escalera(), 5, conservar_topologia=False)
    with pytest.raises(ValueError):
        geo.comprobar_particion([r], zona, tolerancia_ha=1e-9)


def test_cerrar_microhuecos_no_invade_al_vecino():
    """El defecto: closing por poligono rellenaba cada entrante de la escalera,
    que es del vecino (261 ha de solape en un predio real)."""
    capa = _escalera()
    r = geo.cerrar_microhuecos(capa, 10)
    a, b = r.geometry.iloc[0], r.geometry.iloc[1]
    assert a.intersection(b).area == pytest.approx(0)
    assert list(r.geometry.area) == pytest.approx(list(capa.geometry.area))


# ---- bugs confirmados ----------------------------------------------------


def test_remuestrear_no_escribe_cero_en_el_borde():
    """Con ceil la malla sobresale de la fuente; esa franja salía en 0.0."""
    from rasterio.crs import CRS
    from rasterio.transform import from_origin

    a = np.full((10, 10), 50.0)
    out, _ = geo.remuestrear(a, from_origin(0, 100, 10, 10), CRS.from_epsg(UTM), 3)
    assert out.shape == (34, 34)
    borde = np.concatenate([out[:, -1], out[-1, :]])
    assert not (borde == 0).any()


def test_reparar_geometrias_conserva_area_del_mono():
    """buffer(0) se quedaba con la mitad del moño: 1.0 en vez de 2.0."""
    mono = Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])
    r = geo.reparar_geometrias(gpd.GeoDataFrame(geometry=[mono], crs=UTM))
    assert r.geometry.area.sum() == pytest.approx(2.0)


def test_combinar_categorias_no_se_desborda_con_uint8():
    a, b = np.array([[30]], np.uint8), np.array([[4]], np.uint8)
    assert int(geo.combinar_categorias(a, b, 10)[0, 0]) == 304


def test_medida_con_nulos_no_avisa_en_falso(capsys):
    capa = gpd.GeoDataFrame(
        {"SUP": [0.0004, None]}, geometry=[box(0, 0, 2, 2), box(5, 5, 7, 7)], crs=UTM
    )
    from geophis.archivo import _avisar_medida_obsoleta

    _avisar_medida_obsoleta(capa)
    assert "no cuadra" not in capsys.readouterr().out


def test_detectar_campo_vocabulario_en_minusculas():
    capa = gpd.GeoDataFrame(
        {"tipo": ["PERENNE", "INTERMITENTE"]},
        geometry=[LineString([(0, 0), (1, 1)])] * 2,
        crs=UTM,
    )
    assert geo.detectar_campo(capa, {"perenne", "intermitente"}) == "tipo"


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_zona_de_influencia_en_geografico_avisa(capsys):
    geo.zona_de_influencia(gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)], crs=4326), 1)
    assert "geográfico" in capsys.readouterr().out


def test_rellenar_huecos_en_geografico_falla():
    capa = gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)], crs=4326)
    with pytest.raises(ValueError, match="geográficas"):
        geo.rellenar_huecos(capa, area_max_ha=1)


def test_totales_en_geografico_fallan_en_vez_de_dar_grados():
    capa = gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)], crs=4326)
    with pytest.raises(ValueError, match="geográficas"):
        geo.superficie_total_ha(capa)
    with pytest.raises(ValueError, match="geográficas"):
        geo.longitud_total_m(capa)


def test_raster_distancia_exige_mismo_crs():
    from geophis.barridos import raster_distancia

    curvas = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (10, 10)])], crs=UTM)
    zona = gpd.GeoDataFrame(geometry=[box(0, 0, 10, 10)], crs=6372)
    with pytest.raises(ValueError, match="reproyecta"):
        raster_distancia(curvas, zona, 1)


def test_subdividir_por_area_conserva_superficie():
    # El corte en x=100 deja una tira de 100 m2 a la derecha, menor que la
    # esquirla: tirarla le quitaría a la capa el 1 % de su área.
    forma = box(0, 0, 100, 100).union(box(100, 0, 200, 1))
    g = gpd.GeoDataFrame(geometry=[forma], crs=UTM)
    r = geo.subdividir_por_area(g, area_max_ha=0.5, min_esquirla=150)
    assert r.area.sum() == pytest.approx(forma.area, rel=1e-6)


def test_campo_seguro_es_determinista():
    g = gpd.GeoDataFrame({"tipo": ["a"], "tipo_1": ["b"]}, geometry=[box(0, 0, 1, 1)], crs=UTM)
    assert geo.nombre_campo_libre(g, "tipo") == geo.nombre_campo_libre(g, "tipo") == "tipo_2"


# ---- pies, equidistancia con huecos, áreas en grados ----------------------


def test_crs_en_pies_falla_en_vez_de_dar_ft2_como_ha():
    """Un State Plane en pies pasaba como proyectado y daba 100 ha por 9.29."""
    g = gpd.GeoDataFrame(geometry=[box(0, 0, 1000, 1000)], crs=2229)
    with pytest.raises(ValueError, match="METROS"):
        geo.superficie_total_ha(g)


def test_equidistancia_con_curvas_faltantes_es_el_paso_no_la_moda():
    """Con niveles faltantes el salto doble era la moda: 40 en vez de 20."""
    from geophis.metrologia import _detectar_equidistancia

    assert _detectar_equidistancia([0, 20, 40, 80, 120, 160, 200]) == 20


@pytest.mark.parametrize(
    "op",
    [
        lambda g: geo.subdividir_por_area(g, 1.0),
        lambda g: geo.eliminar_menores(g, 1.0, 10.0),
    ],
)
def test_areas_por_fila_en_geografico_fallan(op):
    """Solo avisaban y seguían con grados2/10000 como si fueran ha."""
    g = gpd.GeoDataFrame({"superficie_ha": [0.5]}, geometry=[box(0, 0, 0.01, 0.01)], crs=4326)
    with pytest.raises(ValueError, match="geográficas"):
        op(g)


def test_guardar_en_pies_no_da_aviso_falso_de_sup(tmp_path, capsys):
    """La medida viva salía en ft2 y nunca cuadraba con la SUP en ha."""
    g = gpd.GeoDataFrame(geometry=[box(0, 0, 1000, 1000)], crs=2229)
    geo.guardar(geo.calcular_superficie(g, campo="SUP"), tmp_path / "pies.gpkg")
    assert "no cuadra" not in capsys.readouterr().out
