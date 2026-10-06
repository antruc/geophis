"""Tests de geophis.raster (lógica de arrays sobre numpy/rasterio).

`acondicionar_mde` sí se prueba, sobre un MDE sintético escrito a disco: es el
único sitio de la librería que rellena el NoData, y ese relleno tiene que
quedarse dentro.

Lo que MIDE y barre (`raster_distancia`, `barrer_vecinos`, `amplitud_rizo`...)
vive en `test_barridos.py`, y las reglas puras en `test_metrologia.py`.

La rama hidrológica (dirección, acumulación, cuencas) vive en
`test_hidrologia.py`: sobre una rampa sintética las identidades salen exactas.
"""

import geopandas as gpd
import numpy as np
import pytest
from rasterio.transform import Affine
from rasterio.crs import CRS
from rasterio.warp import Resampling
from shapely.geometry import box, LineString, Point

import geophis as geo
from conftest import _curvas_rampa, _gdf_box

UTM = CRS.from_epsg(32613)
# transform 1 m/px, origen (0,0), y hacia abajo
TF = Affine(1, 0, 0, 0, -1, 0)


# ---- map algebra ----------------------------------------------------------


def test_combinar_categorias_empaqueta():
    a = np.array([[1, 2]])
    b = np.array([[3, 0]])
    r = geo.combinar_categorias(a, b, factor=10)
    assert r.tolist() == [[13, 20]] and r.dtype == np.int32


def test_combinar_categorias_factor_insuficiente_falla():
    a = np.array([[1]])
    b = np.array([[5]])
    with pytest.raises(ValueError):
        geo.combinar_categorias(a, b, factor=4)


def test_combinar_categorias_negativos_falla():
    # nodata -1 en cualquiera de los dos genera IDs que chocan
    with pytest.raises(ValueError, match=">= 0"):
        geo.combinar_categorias(np.array([[1]]), np.array([[-1]]), factor=10)


# ---- nodata ---------------------------------------------------------------


def test_rellenar_nodata_copia_vecino():
    arr = np.array([[1.0, np.nan], [3.0, 4.0]])
    r = geo.rellenar_nodata(arr)
    assert not np.isnan(r).any()
    assert r[0, 1] in (1.0, 4.0)  # tomó un vecino real, no inventó


def test_mascara_relleno_cubre_hueco_y_anillo():
    # el hueco queda plano tras rellenar; el anillo calculo su ventana 3x3 con
    # valores copiados. La mascara tiene que cubrir los dos, y nada mas.
    mde = np.tile(np.arange(9.0), (9, 1))
    mde[4, 4] = np.nan
    m = geo.mascara_relleno(mde)
    assert m[4, 4]  # el hueco
    assert m[3, 3] and m[5, 5]  # anillo en diagonal: tambien contaminado
    assert not m[2, 4] and m.sum() == 9  # 3x3 exacto, nada mas


def test_mascara_relleno_sin_nan_es_falsa():
    assert not geo.mascara_relleno(np.ones((4, 4))).any()


def test_rellenar_nodata_sin_nan_devuelve_igual():
    arr = np.array([[1.0, 2.0]])
    assert geo.rellenar_nodata(arr) is arr


# ---- vector <-> raster ----------------------------------------------------


def test_rasterizar_marca_geometria():
    g = _gdf_box(0, -4, 4, 0)  # cubre todo el grid 4x4
    mascara = geo.rasterizar(g, (4, 4), TF)
    assert mascara.sum() == 16 and mascara.max() == 1


def test_poligonizar_ignora_ceros():
    arr = np.array([[0, 0], [1, 1]], dtype=np.uint8)
    r = geo.poligonizar(arr, TF, UTM)
    assert len(r) == 1 and r["valor"].iloc[0] == 1


def test_poligonizar_vacio_conserva_columna_geometria():
    # sin regiones válidas la salida debe seguir siendo usable: quien llame
    # hace .geometry / recortar sin comprobar el largo primero
    r = geo.poligonizar(np.zeros((2, 2), dtype=np.uint8), TF, UTM)
    assert len(r) == 0 and r.crs == UTM
    assert r.geometry.empty and list(r.columns) == ["valor", "geometry"]


def test_limpiar_moteado_absorbe_mancha_chica():
    arr = np.full((6, 6), 1, dtype=np.int32)
    arr[2, 2] = 2  # píxel suelto: la clase 2 no llega al mínimo
    r = geo.limpiar_moteado(arr, min_pixeles=2)
    assert (r == 1).all()
    assert arr[2, 2] == 2  # no muta la entrada


def test_limpiar_moteado_min_1_no_quita_nada():
    arr = np.array([[1, 2], [1, 1]], dtype=np.int32)
    assert (geo.limpiar_moteado(arr, min_pixeles=1) == arr).all()


def test_limpiar_moteado_exento_ni_se_absorbe_ni_absorbe():
    """Las tres propiedades que compra `exentos`, en un solo montaje.

    Sin exención la receta anterior (sievear todo / sievear nada / resolver por
    prioridad) no era una partición: el sieve METÍA en la clase alta píxeles que
    en el crudo eran de otra clase, y ésos no estaban en ninguna de las dos
    capas. Medido en un predio real: 43.21 ha de hueco.
    """
    arr = np.ones((16, 16), dtype=np.int32)
    arr[2:10, 2:10] = 3  # bloque grande de la clase exenta
    arr[5:7, 5:7] = 2  # 4 px de otra clase, rodeados SOLO por la exenta
    arr[12:14, 3:5] = 3  # 4 px de la exenta, sueltos en el campo de 1
    original = arr.copy()

    # sin exentos el sieve se lleva las dos manchas chicas, cada una a su vecino
    sin = geo.limpiar_moteado(arr, min_pixeles=5)
    assert (sin == 2).sum() == 0  # el 2 se fue al 3
    assert (sin[12:14, 3:5] == 1).all()  # el 3 suelto se fue al 1

    con = geo.limpiar_moteado(arr, min_pixeles=5, exentos=[3])
    assert (con == 2).sum() == 4  # la exenta NO absorbe
    assert (con[12:14, 3:5] == 3).all()  # la exenta NO se absorbe
    # y la exenta sale IDÉNTICA a la del crudo, píxel a píxel: eso es lo que la
    # receta de dos pasadas intentaba comprar y no compraba
    assert ((con == 3) == (original == 3)).all()
    assert (arr == original).all()  # no muta la entrada


def test_limpiar_moteado_min_invalido_falla():
    with pytest.raises(ValueError, match="min_pixeles"):
        geo.limpiar_moteado(np.ones((2, 2), dtype=np.int32), min_pixeles=0)


def test_quemar_cauces_baja_elevacion():
    mde = np.full((4, 4), 100.0)
    cauces = _gdf_box(0, -4, 4, 0)  # cubre todo
    salida, n = geo.quemar_cauces(mde, cauces, TF, profundidad=5)
    assert n == 16
    # el interior, sin borde ni vecino mas bajo, recibe micro-pendiente hacia el borde
    assert np.allclose(salida, 95.0, atol=1e-3) and (salida >= 95.0).all()
    assert (mde == 100.0).all()  # no muta el original


def test_quemar_cauces_diagonal_queda_8_conectado():
    """El cauce quemado no puede cortarse: si se corta, la acumulación de flujo
    aguas abajo se rompe EN SILENCIO. Esto justifica el todo_tocado=False de
    quemar_cauces; si algún día falla, hay que voltearlo a True."""
    import geopandas as gpd
    from scipy.ndimage import label
    from shapely.geometry import LineString

    cauce = gpd.GeoDataFrame(geometry=[LineString([(0.5, -0.5), (9.5, -9.5)])], crs=UTM)
    salida, n = geo.quemar_cauces(np.full((10, 10), 100.0), cauce, TF, profundidad=5)
    quemado = salida < 100.0
    assert n == quemado.sum() > 0
    # 8-conectado: una sola región usando también las diagonales
    _, regiones = label(quemado, structure=np.ones((3, 3)))
    assert regiones == 1


# ---- remuestreo -----------------------------------------------------------


def test_remuestrear_baja_resolucion():
    arr = np.ones((4, 4), dtype=float)
    tf10 = Affine(10, 0, 0, 0, -10, 0)
    nuevo, tf_nuevo = geo.remuestrear(arr, tf10, UTM, res_destino=20)
    assert nuevo.shape == (2, 2)  # mitad de píxeles por lado
    assert tf_nuevo[0] == 20


def test_remuestrear_tamano_no_divisible_no_pierde_borde():
    arr = np.ones((5, 5), dtype=float)
    tf10 = Affine(10, 0, 0, 0, -10, 0)
    nuevo, _ = geo.remuestrear(arr, tf10, UTM, res_destino=20)
    assert nuevo.shape == (3, 3)  # ceil(2.5): el borde no se pierde


# ---- recorte con vector ---------------------------------------------------


def _tif(tmp_path, arr):
    ruta = str(tmp_path / "r.tif")
    geo.guardar_raster(arr, ruta, Affine(1, 0, 500000, 0, -1, 2000000), UTM)
    return ruta


def test_guardar_raster_escribe_nan_como_nodata(tmp_path):
    # el NaN sale marcado como nodata y vuelve NaN
    arr = np.array([[1.0, np.nan], [3.0, 4.0]])
    ruta = str(tmp_path / "p.tif")
    geo.guardar_raster(
        arr,
        ruta,
        Affine(1, 0, 500000, 0, -1, 2000000),
        UTM,
    )
    r, *_ = geo.cargar_raster(ruta)
    assert np.isnan(r[0, 1]) and r[1, 1] == 4.0


def test_cargar_raster_convierte_nodata_a_nan(tmp_path):
    # el nodata declarado (-9999) entra como NaN, no como elevacion real
    arr = np.array([[1.0, -9999.0], [3.0, 4.0]])
    ruta = _tif(tmp_path, arr)
    import rasterio

    with rasterio.open(ruta, "r+") as f:
        f.nodata = -9999.0
    r, *_ = geo.cargar_raster(ruta)
    assert np.isnan(r[0, 1]) and r[0, 0] == 1.0


def test_recortar_raster_recorta_a_mascara(tmp_path):
    arr = np.arange(16, dtype=float).reshape(4, 4)
    ruta = _tif(tmp_path, arr)
    mascara = _gdf_box(500000, 1999998, 500002, 2000000)  # esquina NW, 2x2
    rec, _, _, res = geo.recortar_raster(ruta, mascara)
    assert rec.shape == (2, 2) and res == 1
    assert np.allclose(rec, arr[:2, :2])


def test_recortar_raster_convierte_nodata_declarado(tmp_path):
    # un -9999 declarado DENTRO de la máscara sale como NaN, no como elevación
    arr = np.array([[1.0, -9999.0], [3.0, 4.0]])
    ruta = _tif(tmp_path, arr)
    import rasterio

    with rasterio.open(ruta, "r+") as f:
        f.nodata = -9999.0
    mascara = _gdf_box(500000, 1999998, 500002, 2000000)
    rec, *_ = geo.recortar_raster(ruta, mascara)
    assert np.isnan(rec[0, 1]) and rec[0, 0] == 1.0


def test_recortar_raster_desde_entero_devuelve_float_con_nan(tmp_path):
    """Con un raster int16, rellenar con NaN revienta dentro de numpy.ma.

    `TypeError: Cannot convert fill_value nan to dtype int16`. Cualquier
    producto INEGI entero choca con lo mismo. Fuera de la mascara y en el
    nodata declarado tiene que salir NaN; dentro, el valor tal cual.
    """
    import rasterio
    from shapely.geometry import Polygon

    arr = np.array([[1, 2, 3], [4, -32768, 6], [7, 8, 9]], dtype=np.int16)
    ruta = str(tmp_path / "e.tif")
    with rasterio.open(
        ruta,
        "w",
        driver="GTiff",
        crs=UTM,
        count=1,
        dtype="int16",
        width=3,
        height=3,
        nodata=-32768,
        transform=Affine(1, 0, 500000, 0, -1, 2000000),
    ) as f:
        f.write(arr, 1)
    # triangulo que deja fuera la esquina SE del recorte
    tri = Polygon([(500000, 2000000), (500003, 2000000), (500000, 1999997)])
    mascara = gpd.GeoDataFrame(geometry=[tri], crs=UTM)
    rec, *_ = geo.recortar_raster(ruta, mascara)
    assert rec.dtype == np.float64
    assert rec[0, 0] == 1.0 and np.isnan(rec[1, 1]) and np.isnan(rec[2, 2])


def test_recortar_raster_mascara_fuera_falla(tmp_path):
    ruta = _tif(tmp_path, np.zeros((2, 2)))
    with pytest.raises(ValueError, match="no intersecta"):
        geo.recortar_raster(ruta, _gdf_box(600000, 1999998, 600002, 2000000))


def test_recortar_raster_fuera_dice_LAS_DOS_CAJAS_y_no_solo_que_fallo(tmp_path):
    """Un "no intersecta" pelado manda a revisar la proyeccion.

    Y la proyeccion ya esta bien, porque esta funcion alinea el CRS ella misma.
    La unica causa posible es que la escena no cubra la zona, y para saberlo hay
    que ver las dos cajas. Con un Sentinel-2 real, la tesela y el predio
    pueden estar en el mismo EPSG:32613 y separados 21.7 km.
    """
    ruta = _tif(tmp_path, np.zeros((2, 2)))
    with pytest.raises(ValueError) as exc:
        geo.recortar_raster(ruta, _gdf_box(600000, 1999998, 600002, 2000000))
    msg = str(exc.value)
    assert "raster" in msg and "mascara" in msg  # las dos cajas, no una
    assert "km en x" in msg and "km en y" in msg  # cuanto, no solo que si
    assert "no es de proyección" in msg  # la causa que hay que descartar


# ---- cuencas / aspecto ----------------------------------------------------


def test_separacion_se_mide_por_gp0_sobre_el_extent():
    # DOS cosas en como se deriva la separacion:
    #   estimador : G'(0) (`separacion_media`) en vez de area/longitud, que el
    #               zigzag del trazo sesgaba a la baja (medido en campo: 74.93 m
    #               contra 59.36 m sobre la misma zona, 26 %).
    #   zona      : el extent de interpolacion, no el bbox de las curvas.
    from geophis.raster import (
        _resolver_parametros,
        _separacion_horizontal,
        _separacion_media,
    )

    curvas = _curvas_rampa()
    bounds = tuple(curvas.total_bounds)
    _, _, _, sep = _resolver_parametros(curvas, "cota", 1.0, None, None, bounds)
    assert sep == pytest.approx(_separacion_horizontal(curvas, bounds))

    # la zona manda: otro extent, otra separacion (no el bbox de las curvas).
    ancho = (bounds[0] - 100, bounds[1] - 100, bounds[2] + 100, bounds[3] + 100)
    _, _, _, sep_ancho = _resolver_parametros(curvas, "cota", 1.0, None, None, ancho)
    assert sep_ancho != pytest.approx(sep)
    assert _separacion_media(curvas) == pytest.approx(7.5)  # el respaldo, intacto


def test_muestreo_default_no_se_redondea():
    # `resolucion` sí se redondea a un valor limpio; `intervalo_muestreo` no.
    # Redondearlo lo hacía saltar de 50 a 100 con el corte en 75.0, así que una
    # separacion de 74.93 m decidia el muestreo (y con el la superficie del
    # rango mas alto) por centimetros.
    from geophis.raster import _redondear_limpio, _resolver_parametros

    curvas = _curvas_rampa()
    bounds = (-100.0, -100.0, 130.0, 130.0)
    _, _, muestreo, separacion = _resolver_parametros(
        curvas, "cota", 1.0, None, None, bounds
    )
    assert muestreo == separacion  # sin pasar por _redondear_limpio
    assert muestreo != _redondear_limpio(muestreo)  # y de hecho no es limpio


def test_interpolar_mde_avisa_sin_dato(capsys):
    # curvas rectas cuyo bbox de salida las desborda: las esquinas del cuadro
    # caen fuera de la envolvente convexa y salen NaN. El aviso tiene que decir
    # que hacer, no solo cuantas hectareas son.
    curvas = gpd.GeoDataFrame(
        {"COTA": [100.0, 120.0, 140.0]},
        geometry=[LineString([(0, i * 60), (300, i * 60)]) for i in range(3)],
        crs=UTM,
    )
    cuadro = gpd.GeoDataFrame(geometry=[box(-120, -120, 420, 240)], crs=UTM)
    mde, _ = geo.interpolar_mde(curvas, "COTA", resolucion=20, cuadro=cuadro)
    salida = capsys.readouterr().out
    assert np.isnan(mde).any()
    assert "sin dato:" in salida and "margen_borde_celdas" in salida

    # dentro de la envolvente no hay hueco, y entonces el aviso no aparece
    # dentro del ultimo punto muestreado: el muestreo cae en multiplos del paso
    # desde el origen de cada linea, no en su final
    ajustado = gpd.GeoDataFrame(geometry=[box(40, 20, 230, 100)], crs=UTM)
    mde2, _ = geo.interpolar_mde(curvas, "COTA", resolucion=20, cuadro=ajustado)
    salida2 = capsys.readouterr().out
    assert not np.isnan(mde2).any()
    assert "sin dato: 0.00 ha" in salida2 and "margen_borde_celdas" not in salida2


def test_acondicionar_mde_rellena_solo_adentro(tmp_path, capsys):
    # el relleno del NoData vive DENTRO de acondicionar_mde: pysheds lo exige y
    # nadie mas lo necesita. El MDE de disco conserva sus NaN.
    z = np.add.outer(np.arange(20.0), np.arange(20.0))
    z[10, 10] -= 5  # pit, para que fill_pits tenga trabajo
    z[:3, :3] = np.nan  # 9 px sin dato = 0.09 ha a 10 m/px
    ruta = tmp_path / "mde.tif"
    tf = Affine(10, 0, 0, 0, -10, 200)
    geo.guardar_raster(z, ruta, tf, UTM)

    grid, dem = geo.acondicionar_mde(ruta)
    assert not np.isnan(np.asarray(dem)).any()  # pysheds recibe un MDE sin NaN
    salida = capsys.readouterr().out
    assert "0.09 ha sin dato parcheadas" in salida
    assert "geometria inventada" in salida  # el aviso, no solo la cifra
    assert np.asarray(geo.acumulacion_flujo(grid, dem).fdir).shape == z.shape

    # el archivo no se toco: quien lea el MDE para pendiente sigue viendo NaN
    arr, *_ = geo.cargar_raster(ruta)
    assert np.isnan(arr[:3, :3]).all()


def test_acondicionar_mde_sin_nodata_reporta_cero(tmp_path, capsys):
    z = np.add.outer(np.arange(12.0), np.arange(12.0))
    ruta = tmp_path / "mde.tif"
    tf = Affine(10, 0, 500_000, 0, -10, 2_000_000)
    geo.guardar_raster(z, ruta, tf, UTM)
    geo.acondicionar_mde(ruta)
    assert "0.00 ha sin dato parcheadas" in capsys.readouterr().out


def test_etiquetar_laderas_una_region():
    acc = np.zeros((4, 4))  # nada supera el umbral => una sola ladera
    etiquetas, n = geo.etiquetar_laderas(acc, umbral=10)
    assert n == 1 and etiquetas.max() == 1


def test_quemar_cauces_sin_interseccion_devuelve_cero():
    # el 2o valor de retorno existe para esto: un cauce en otro CRS o de otro
    # predio no falla, quema 0 pixeles y devuelve el MDE intacto. Sin mirarlo,
    # el analisis sigue con la superficie interpolada creyendo que hay cauces.
    mde = np.full((10, 10), 100.0)
    lejos = gpd.GeoDataFrame(
        geometry=[LineString([(500, 500), (600, 600)])],
        crs=UTM,  # fuera del raster
    )
    salida, n = geo.quemar_cauces(mde, lejos, TF, profundidad=5)
    assert n == 0 and (salida == mde).all()


def test_quemar_sobre_nodata_avisa(capsys):
    # no es silencioso: el aviso va sin perilla que lo apague,
    # por lo mismo que el "sin dato" de interpolar_mde.
    mde = np.full((14, 14), 100.0)
    mde[5, 10] = np.nan
    cauce = gpd.GeoDataFrame(geometry=[LineString([(10.5, 0), (10.5, -14)])], crs=UTM)
    geo.quemar_cauces(mde, cauce, TF, profundidad=5)
    salida = capsys.readouterr().out
    # Lleva tambien el DENOMINADOR y la fraccion: "1657 celdas" no
    # dice si hay que parar, y medido sobre cartografia real que no cubria el
    # extent eran el 32 % del cauce, con el lecho subiendo los 5 m enteros.
    assert "1 de 14 celda(s) de cauce (7 %)" in salida
    assert "rellenar_nodata` ANTES de quemar" in salida


def test_quemar_sin_nodata_no_avisa(capsys):
    cauce = gpd.GeoDataFrame(geometry=[LineString([(10.5, 0), (10.5, -14)])], crs=UTM)
    geo.quemar_cauces(np.full((14, 14), 100.0), cauce, TF, profundidad=5)
    assert capsys.readouterr().out == ""


def test_quemar_sobre_nodata_rompe_el_cauce_en_silencio():
    """La trampa que documenta `acondicionar_mde`.

    Quemar sobre una celda sin dato da `NaN - profundidad`, que sigue siendo
    NaN. Despues el relleno copia el vecino valido mas cercano, que esta SIN
    quemar, asi que el lecho SUBE justo ahi: queda una presa dentro del cauce.
    No hay aviso; el MDE es valido y el cauce esta cortado.

    Por eso el orden correcto es rellenar ANTES de quemar, no despues.
    """
    # TF es de 1 m/px con origen en (0,0), asi que el centro de la columna 10
    # esta en x=10.5 y la fila i en y=-(i+0.5).
    mde = np.repeat((100.0 - np.arange(14.0))[:, None], 14, axis=1)
    # hueco ALTO y estrecho: asi el vecino valido mas cercano a su centro sale
    # por el lado (sin quemar) y no por arriba (quemado), sin empates.
    hueco = (slice(3, 9), slice(9, 12))
    cauce = gpd.GeoDataFrame(
        geometry=[LineString([(10.5, 0), (10.5, -14)])],
        crs=UTM,  # columna 10
    )

    # orden MALO: quemar y rellenar despues
    con_hueco = mde.copy()
    con_hueco[hueco] = np.nan
    quemado, n = geo.quemar_cauces(con_hueco, cauce, TF, profundidad=5)
    assert n == 14 and np.isnan(quemado[5, 10])  # NaN - 5 sigue NaN
    malo = geo.rellenar_nodata(quemado)
    # copio un vecino SIN quemar: 95 en vez de los 90 que tocaban
    assert malo[5, 10] == 100.0 - 5
    # y con eso el lecho SUBE aguas abajo: presa dentro del cauce
    assert malo[5, 10] > malo[2, 10]

    # orden BUENO: rellenar y quemar despues
    bueno, _ = geo.quemar_cauces(geo.rellenar_nodata(con_hueco), cauce, TF, profundidad=5)
    # el lecho NUNCA sube. No baja siempre, y eso es correcto: `rellenar_nodata`
    # COPIA el vecino en vez de interpolar, asi que el relleno deja mesetas en
    # los bordes del hueco. La distincion es la que importa: una meseta la
    # resuelve `resolve_flats` en el acondicionado; una SUBIDA es una presa, y
    # eso no lo arregla nadie.
    assert np.diff(bueno[:, 10]).max() <= 0
    assert np.diff(malo[:, 10]).max() > 0  # el orden malo si sube


# ---- extraer_valores ------------------------------------------------------
#
# Los dos primeros fallan con un `np.clip` sobre los indices.


def test_extraer_valores_muestrea_el_pixel_correcto():
    arr = np.arange(12.0).reshape(3, 4)  # TF: 1 m/px, origen (0,0), y hacia abajo
    # centro de (fila 1, col 2) = (2.5, -1.5) -> valor 1*4 + 2 = 6
    v = geo.extraer_valores(arr, TF, [(2.5, -1.5), (0.5, -0.5)])
    assert v.tolist() == [6.0, 0.0]


def test_extraer_valores_fuera_del_raster_da_nan_no_el_borde():
    # EL arreglo. El inline hacia np.clip sobre los indices, asi que un punto de
    # fuera se llevaba la cota del borde y entraba en las metricas de
    # `validar_mde` como si fuera un residual medido.
    arr = np.arange(12.0).reshape(3, 4)
    v = geo.extraer_valores(arr, TF, [(-5.0, -1.5), (99.0, -1.5), (2.5, 50.0)])
    assert np.isnan(v).all()


def test_extraer_valores_celda_no_cuadrada():
    # el otro defecto del inline: `(x - minx) / resolucion` con UNA resolucion
    # asume celda cuadrada. La inversa del Affine entero no.
    arr = np.arange(12.0).reshape(3, 4)
    tf = Affine(10, 0, 0, 0, -2, 0)  # 10 m en x, 2 m en y
    # (fila 1, col 2) -> centro (25, -3)
    assert geo.extraer_valores(arr, tf, [(25.0, -3.0)])[0] == 6.0


def test_extraer_valores_el_borde_lejano_cae_en_la_ultima_celda():
    # el extent cierra por arriba: maxx pertenece a la ultima columna, no a una
    # columna de mas. Sin esto, `validar_mde` perderia los puntos del borde.
    arr = np.arange(12.0).reshape(3, 4)
    assert geo.extraer_valores(arr, TF, [(4.0, -3.0)])[0] == 11.0  # esquina exacta


def test_extraer_valores_la_celda_de_mas_alla_del_borde_es_fuera():
    # el clamp del borde cerrado se aplicaba a TODA la celda siguiente: medio
    # pixel por debajo o a la derecha salia con el valor del borde, no NaN
    arr = np.arange(12.0).reshape(3, 4)
    v = geo.extraer_valores(arr, TF, [(2.5, -3.5), (4.5, -1.5)])
    assert np.isnan(v).all()


def test_extraer_valores_fuera_personalizable():
    arr = np.zeros((2, 2))
    assert geo.extraer_valores(arr, TF, [(-9.0, -9.0)], fuera=-1)[0] == -1.0


def test_extraer_valores_acepta_capa_y_reproyecta():
    arr = np.arange(12.0).reshape(3, 4)
    utm = gpd.GeoDataFrame(geometry=[Point(2.5, -1.5)], crs=UTM)
    directo = geo.extraer_valores(arr, TF, utm)
    reproyectado = geo.extraer_valores(arr, TF, utm.to_crs(4326), crs=UTM)
    assert directo[0] == 6.0 and reproyectado[0] == 6.0


def test_extraer_valores_rechaza_forma_mala():
    with pytest.raises(ValueError, match=r"\(N, 2\)"):
        geo.extraer_valores(np.zeros((3, 3)), TF, [1.0, 2.0, 3.0])
    with pytest.raises(ValueError, match="varias bandas"):
        geo.extraer_valores(np.zeros((2, 3, 3)), TF, [(0.5, -0.5)])


# ---- reproyeccion ---------------------------------------------------------

_TF_UTM = Affine(10, 0, 500_000, 0, -10, 2_000_000)


def test_reproyectar_raster_cambia_crs_y_recalcula_la_malla():
    z = np.add.outer(np.arange(20.0), np.arange(25.0))
    arr, tf = geo.reproyectar_raster(z, _TF_UTM, UTM, "EPSG:4326")
    # el paso pasa a grados: ~1e-4, no ~10. Si saliera en metros, la malla
    # tendria millones de pixeles o uno solo.
    assert abs(tf[0]) < 0.01
    assert arr.shape != z.shape or tf != _TF_UTM  # la malla NO se hereda
    assert np.isfinite(arr).any()


def test_reproyectar_raster_ida_y_vuelta_conserva_los_valores():
    # identidad: UTM -> geograficas -> UTM tiene que devolver el mismo rango.
    # Es la comprobacion que falla si el transform de salida esta mal armado.
    z = np.add.outer(np.arange(30.0), np.arange(30.0))
    a, tf_a = geo.reproyectar_raster(z, _TF_UTM, UTM, "EPSG:4326")
    b, _ = geo.reproyectar_raster(a, tf_a, CRS.from_epsg(4326), UTM, 10.0)
    dentro = b[np.isfinite(b)]
    assert dentro.min() == pytest.approx(z.min(), abs=1.0)
    assert dentro.max() == pytest.approx(z.max(), abs=1.0)


def test_reproyectar_raster_vecino_no_inventa_clases():
    # la trampa documentada: con bilineal, reproyectar codigos 1 y 3 fabrica
    # un 2 que no existe en la leyenda. Con nearest, no.
    codigos = np.where(np.add.outer(np.arange(20.0), np.arange(20.0)) < 20, 1.0, 3.0)
    cerca, _ = geo.reproyectar_raster(
        codigos, _TF_UTM, UTM, "EPSG:4326", metodo=Resampling.nearest
    )
    assert set(np.unique(cerca[np.isfinite(cerca)])) <= {1.0, 3.0}


def test_reproyectar_raster_sin_crs_dice_cual_falta():
    with pytest.raises(ValueError, match="crs_destino"):
        geo.reproyectar_raster(np.zeros((4, 4)), _TF_UTM, UTM, None)


def test_cargar_raster_dice_m_px_en_utm_y_u_px_en_geograficas(tmp_path, capsys):
    # el mismo defecto que tenia reproyectar_raster, una funcion mas atras:
    # 'm/px' con '.1f' es verdad en UTM y mentira en grados, en la unidad y en
    # la cifra (un paso de 1e-4 grados salia como '0.0 m/px').
    z = np.zeros((4, 4))
    utm = tmp_path / "utm.tif"
    geo.guardar_raster(z, utm, _TF_UTM, UTM)
    geo.cargar_raster(utm)
    assert "10 m/px" in capsys.readouterr().out

    ruta_geo = tmp_path / "geo.tif"
    tf_geo = Affine(0.0001, 0, -103.0, 0, -0.0001, 20.0)
    geo.guardar_raster(z, ruta_geo, tf_geo, CRS.from_epsg(4326))
    geo.cargar_raster(ruta_geo)
    salida = capsys.readouterr().out
    assert "0.0001 u/px" in salida and "m/px" not in salida


def test_reproyectar_raster_imprime_el_paso_en_unidades_del_crs(capsys):
    # a EPSG:4326 el paso vale ~1e-4: en '.2f' saldria '0.00 m/px', que miente
    # en la cifra y en la unidad.
    geo.reproyectar_raster(np.zeros((10, 10)), _TF_UTM, UTM, "EPSG:4326")
    salida = capsys.readouterr().out
    assert "u/px" in salida and "0.00 u/px" not in salida


def test_calcular_orientacion_plano_es_cero():
    mde = np.full((5, 5), 50.0)  # sin pendiente => clase 0 (sin orientación)
    clase = geo.calcular_orientacion(mde, resolucion=1)
    assert (clase == 0).all() and clase.dtype == np.uint8


def test_calcular_orientacion_rampa_una_orientacion():
    # rampa que sube hacia el este: pendiente constante => 1 clase no nula
    mde = np.tile(np.arange(5.0), (5, 1))
    interior = geo.calcular_orientacion(mde, resolucion=1)[1:-1, 1:-1]
    assert len(np.unique(interior)) == 1 and interior[0, 0] != 0


def test_calcular_orientacion_clase_cardinal_correcta():
    # el test que habría pillado el swap N-E / S-O: verifica la clase concreta
    # rampa que sube al este: la ladera mira al Oeste (clase 4)
    este = np.tile(np.arange(6.0), (6, 1))
    assert (geo.calcular_orientacion(este, resolucion=1)[1:-1, 1:-1] == 4).all()
    # rampa que sube al norte (z alto en la fila 0): mira al Sur (clase 3)
    norte = np.tile(np.arange(6.0)[::-1][:, None], (1, 6))
    assert (geo.calcular_orientacion(norte, resolucion=1)[1:-1, 1:-1] == 3).all()


def test_calcular_orientacion_nodata_sin_clase():
    # rampa al este con franja NoData: el NoData queda 0 y no contamina el borde
    mde = np.tile(np.arange(6.0), (6, 1))
    mde[:, :2] = -9999
    clase = geo.calcular_orientacion(mde, resolucion=1)
    assert (clase[:, :2] == 0).all()  # NoData sin orientacion
    interior = clase[1:-1, 3:-1]  # zona valida lejos del borde del raster
    assert len(np.unique(interior)) == 1 and interior[0, 0] != 0


def test_calcular_orientacion_orilla_de_hueco_sin_clase():
    # los píxeles válidos pegados a un hueco NoData quedan sin clase por la
    # regla 7-de-8, igual que la pendiente: no con una orientación inventada.
    mde = np.tile(np.arange(9.0), (9, 1))  # rampa al este => ladera al Oeste (4)
    mde[4:6, 4:6] = np.nan
    clase = geo.calcular_orientacion(mde, resolucion=1)
    assert (clase[4:6, 4:6] == 0).all()  # el hueco
    # orilla del hueco: 2 vecinos sin dato => 6 válidos => sin clase
    assert clase[3, 4] == 0 and clase[6, 5] == 0
    assert clase[2, 4] == 4  # lejos del hueco, orientación normal


def test_pendiente_y_aspecto_misma_mascara():
    # las dos leen la misma superficie con la misma derivada: donde una dice
    # "sin dato" la otra tiene que decir lo mismo.
    mde = np.tile(np.arange(8.0) * 2, (8, 1))
    mde[3:5, 3:5] = np.nan
    p = geo.calcular_pendiente(mde, resolucion=1)
    a = geo.calcular_orientacion(mde, resolucion=1)
    assert (a[np.isnan(p)] == 0).all()  # sin pendiente medible => sin orientación
    assert (a[~np.isnan(p)] != 0).all()  # con pendiente (200%) => siempre orientada


# ---- pendiente (Horn) -----------------------------------------------------


def test_calcular_pendiente_plano_exacta():
    # plano inclinado z = 3*x (res=1): pendiente constante 300% en el interior
    mde = np.tile(np.arange(6.0) * 3, (6, 1))
    p = geo.calcular_pendiente(mde, resolucion=1)
    interior = p[1:-1, 1:-1]
    assert np.allclose(interior, 300.0) and p.dtype == np.float32


def test_calcular_pendiente_grados():
    # misma rampa, pendiente 100% => 45 grados
    mde = np.tile(np.arange(6.0), (6, 1))
    p = geo.calcular_pendiente(mde, resolucion=1, unidad="grados")
    assert np.allclose(p[1:-1, 1:-1], 45.0)


def test_calcular_pendiente_z_factor():
    # z en pies, xy en metros: z_factor=0.3048 baja la pendiente en ese factor
    mde = np.tile(np.arange(6.0), (6, 1))  # 100% con z_factor=1
    p = geo.calcular_pendiente(mde, resolucion=1, z_factor=0.3048)
    assert np.allclose(p[1:-1, 1:-1], 30.48, atol=1e-3)
    with pytest.raises(ValueError):
        geo.calcular_pendiente(mde, resolucion=1, z_factor=0)


def test_calcular_orientacion_grados_azimut():
    # rampa que sube al este => la ladera baja al Oeste => azimut 270
    mde = np.tile(np.arange(6.0), (6, 1))
    az = geo.calcular_orientacion(mde, resolucion=1, unidad="grados")
    assert az.dtype == np.float32
    assert np.allclose(az[1:-1, 1:-1], 270.0)
    assert np.isnan(az[0, :]).all()  # borde: <7 vecinos => NaN, no -1 ni -9999
    # plano: sin orientacion, y la clase equivalente es 0
    plano = np.full((5, 5), 50.0)
    assert np.isnan(geo.calcular_orientacion(plano, resolucion=1, unidad="grados")).all()
    with pytest.raises(ValueError):
        geo.calcular_orientacion(mde, resolucion=1, unidad="azimut")


def test_calcular_pendiente_acepta_transform():
    # el transform es la fuente de verdad del tamano de celda: pasarlo tiene
    # que dar lo mismo que extraer el numero a mano, y resolver la celda no
    # cuadrada, que `abs(transform[0])` tiraba al quedarse solo con el eje x
    mde = np.tile(np.arange(6.0) * 3, (6, 1))
    tf = Affine(2.0, 0, 0, 0, -2.0, 0)
    assert np.allclose(
        geo.calcular_pendiente(mde, tf),
        geo.calcular_pendiente(mde, resolucion=2.0),
        equal_nan=True,
    )
    # celda de 2x5: la rampa va en x, asi que manda res_x
    rect = Affine(2.0, 0, 0, 0, -5.0, 0)
    assert np.allclose(
        geo.calcular_pendiente(mde, rect),
        geo.calcular_pendiente(mde, resolucion=(2.0, 5.0)),
        equal_nan=True,
    )


def test_calcular_pendiente_bordes_son_nan():
    # el borde tiene <7 vecinos válidos => NoData (NaN)
    mde = np.tile(np.arange(6.0) * 3, (6, 1))
    p = geo.calcular_pendiente(mde, resolucion=1)
    assert np.isnan(p[0, :]).all() and np.isnan(p[:, 0]).all()


# ---- reclasificación por rangos -------------------------------------------


def test_reclasificar_rangos_limites_compartidos():
    # convención (min, max], primero [min, max]: el 5 cae en el rango de abajo
    arr = np.array([[0.0, 5.0, 5.01, 10.0]])
    rangos = [("0-5", 0, 5), ("5-10", 5, 10)]
    cod, etq = geo.reclasificar_rangos(arr, rangos)
    assert cod.tolist() == [[1, 1, 2, 2]]
    assert etq == {1: "0-5", 2: "5-10"} and cod.dtype == np.int32


def test_reclasificar_rangos_nodata():
    arr = np.array([[np.nan, -3.0, 50.0]])
    cod, _ = geo.reclasificar_rangos(arr, [("0-100", 0, 100)])
    assert cod.tolist() == [[-1, -1, 1]]  # NaN y fuera de rango => nodata_valor


# ---- interpolación de MDE -------------------------------------------------


def test_muestrear_curvas_paso_fijo():
    # el muestreo va vectorizado: este test fija el conteo por tramo y las
    # distancias, que es donde se rompería la aritmética de índices
    import geopandas as gpd
    from shapely.geometry import LineString, MultiLineString

    from geophis.raster import _muestrear_curvas

    curvas = gpd.GeoDataFrame(
        {"cota": [100.0, 200.0]},
        geometry=[
            LineString([(0, 0), (25, 0)]),  # largo 25 -> 3 pts (0, 10, 20)
            MultiLineString(
                [
                    [(0, 5), (10, 5)],  # largo 10 -> 2 pts (0, 10)
                    [(0, 9), (5, 9)],  # largo 5  -> 2 pts (0, 5), corta el final
                ]
            ),
        ],
        crs=UTM,
    )
    xs, ys, zs, origen = _muestrear_curvas(curvas, "cota", paso=10)
    assert xs.tolist() == [0, 10, 20, 0, 10, 0, 5]
    assert ys.tolist() == [0, 0, 0, 5, 5, 9, 9]
    assert zs.tolist() == [100, 100, 100, 200, 200, 200, 200]
    # los dos tramos del MultiLineString comparten la curva de origen
    assert origen.tolist() == [0, 0, 0, 1, 1, 1, 1]


def test_muestrear_curvas_descarta_cota_nan():
    import geopandas as gpd
    from shapely.geometry import LineString

    from geophis.raster import _muestrear_curvas

    curvas = gpd.GeoDataFrame(
        {"cota": [np.nan, 50.0]},
        geometry=[LineString([(0, 0), (10, 0)]), LineString([(0, 1), (10, 1)])],
        crs=UTM,
    )
    _, _, zs, origen = _muestrear_curvas(curvas, "cota", paso=10)
    assert zs.tolist() == [50, 50]
    assert origen.tolist() == [1, 1]  # la curva descartada no deja hueco


def test_interpolar_mde_tin_reproduce_rampa():
    # metodo explícito: el default es 'spline', y el TIN es el
    # único que clava la rampa a 1e-6 (los vértices caen sobre los datos)
    curvas = _curvas_rampa()
    mde, tf = geo.interpolar_mde(curvas, "cota", resolucion=5, metodo="tin")
    assert tf[0] == 5 and tf[4] == -5
    # dentro de la envolvente el valor interpolado ~ coordenada y del píxel
    filas, cols = mde.shape
    for r in range(filas):
        for c in range(cols):
            z = mde[r, c]
            if np.isnan(z):
                continue
            y = tf.f + (r + 0.5) * tf.e  # tf.e es negativo
            assert abs(z - y) < 1e-6


def test_interpolar_mde_tin_avisa_planos_a_cota_de_curva(capsys):
    # cerro de circulos concentricos: dentro del mas chico no hay otra curva,
    # asi que el TIN lo llena con triangulos de tres vertices en la MISMA curva
    # y sale una meseta a su cota. Con muestreo fino el poligono ~ circulo de
    # r=100 (3.14 ha), menos el anillo de una celda que la ventana de Horn toca
    # ya con ladera (pi*95^2 = 2.84 ha). En la rampa no hay curva que se cierre
    # sobre si misma: ni una celda.
    from shapely.geometry import Point

    cerro = gpd.GeoDataFrame(
        {"cota": [30.0, 20.0, 10.0]},
        geometry=[Point(0, 0).buffer(r).exterior for r in (100, 200, 300)],
        crs=UTM,
    )
    mde, tf = geo.interpolar_mde(cerro, "cota", resolucion=5, intervalo_muestreo=5, metodo="tin")
    salida = capsys.readouterr().out
    ha = float(salida.split("Aviso: ")[-1].split(" ha")[0])
    assert 2.6 < ha <= 3.14, salida
    assert "MISMA curva" in salida
    # la mascara publica es la misma cuenta que el aviso
    assert geo.planos_de_curva(mde, tf, cerro, "cota").sum() * 25 / 10_000 == pytest.approx(ha, abs=0.01)
    # el spline tambien los hace si el muestreo (5 m) es mucho mas fino que la
    # separacion (100 m): los vecinos caen todos en el anillo. El aviso nombra
    # la causa del metodo, no la del TIN.
    geo.interpolar_mde(cerro, "cota", resolucion=5, intervalo_muestreo=5, vecinos=8)
    salida = capsys.readouterr().out
    assert "MDE SPLINE son planos" in salida and "anisotropa" in salida

    geo.interpolar_mde(_curvas_rampa(), "cota", resolucion=5, metodo="tin")
    assert "planos exactos" not in capsys.readouterr().out


def test_interpolar_mde_spline_reproduce_rampa():
    # el thin-plate spline reproduce exacto una rampa lineal (término polinomial)
    curvas = _curvas_rampa()
    mde, tf = geo.interpolar_mde(curvas, "cota", resolucion=5, metodo="spline")
    assert tf[0] == 5 and tf[4] == -5
    filas, cols = mde.shape
    validos = 0
    for r in range(filas):
        for c in range(cols):
            z = mde[r, c]
            if np.isnan(z):
                continue
            y = tf.f + (r + 0.5) * tf.e
            assert abs(z - y) < 1e-3
            validos += 1
    assert validos > 0


def test_spline_evalua_con_blas_a_un_hilo(monkeypatch):
    # el acantilado de 90 a 122 vecinos era OpenBLAS repartiendo cada sistema
    # chico entre todos los nucleos (ver `_interp_spline`). Si el limite se
    # pierde, el barrido de `vecinos` vuelve a tardar 6x en el ultimo paso.
    from scipy.interpolate import RBFInterpolator
    from threadpoolctl import threadpool_info

    hilos = []
    llamar = RBFInterpolator.__call__

    def espia(self, x):
        hilos.extend(i["num_threads"] for i in threadpool_info() if i["user_api"] == "blas")
        return llamar(self, x)

    monkeypatch.setattr(RBFInterpolator, "__call__", espia)
    geo.interpolar_mde(_curvas_rampa(), "cota", resolucion=5, metodo="spline")
    assert hilos and set(hilos) == {1}


def test_interpolar_mde_default_es_spline():
    # que el default no se vuelva a 'tin' sin que alguien lo note
    curvas = _curvas_rampa()
    por_defecto, _ = geo.interpolar_mde(curvas, "cota", resolucion=5)
    spline, _ = geo.interpolar_mde(curvas, "cota", resolucion=5, metodo="spline")
    assert np.array_equal(por_defecto, spline, equal_nan=True)


def _curvas_cono(radio_base=100.0, equidistancia=10.0):
    """Curvas de nivel de un cono perfecto (z = radio_base - r).

    La cima (r=0, z=100) no tiene curva propia, igual que en cartografía real:
    la curva más alta es la de 90 y encierra un disco de radio 10 sin dato.
    """
    import geopandas as gpd
    from shapely.geometry import Point

    cotas = np.arange(equidistancia, radio_base, equidistancia)
    geoms = [Point(0, 0).buffer(radio_base - z, quad_segs=64).exterior for z in cotas]
    return gpd.GeoDataFrame({"cota": cotas.astype(float)}, geometry=geoms, crs=UTM)


def test_tin_aplana_la_cima_y_spline_no():
    # el motivo de que 'spline' sea el default: dentro de la curva
    # más alta el TIN triangula entre vértices de la misma cota y deja un disco
    # plano. En campo eso es una cima con 0% de pendiente, y de ahí salían ~600
    # ha de falso 0-5% en el predio real. El cono verdadero tiene 100% en todas
    # partes, así que cualquier píxel llano de la cima es artefacto.
    curvas = _curvas_cono()
    llanos, cima_z = {}, {}
    for metodo in ("tin", "spline"):
        mde, tf = geo.interpolar_mde(
            curvas,
            "cota",
            resolucion=2,
            intervalo_muestreo=5,
            metodo=metodo,
        )
        pendiente = geo.calcular_pendiente(mde, abs(tf[0]))
        filas, cols = mde.shape
        y, x = np.mgrid[0:filas, 0:cols]
        cx = tf.c + (x + 0.5) * tf.a
        cy = tf.f + (y + 0.5) * tf.e
        cima = np.hypot(cx, cy) < 10  # el disco que encierra la curva de 90
        llanos[metodo] = int(((pendiente < 1.0) & cima).sum())
        cima_z[metodo] = float(np.nanmax(mde))
    # medido: tin 42 de 80 píxeles llanos, spline 0
    assert llanos["tin"] > 5 * llanos["spline"]
    # y el TIN trunca la cima en la última curva (90.0) en vez de levantarla
    # hacia los 100 reales del cono; el spline llega a ~94
    assert cima_z["tin"] == pytest.approx(90.0)
    assert cima_z["spline"] > cima_z["tin"] + 1


def test_defaults_salen_de_la_separacion_horizontal():
    # resolucion y muestreo salen de la separación HORIZONTAL entre
    # curvas, no de la equidistancia (que es vertical). _curvas_rampa: 4 líneas
    # rectas de 30 m en un cuadro de 30x30, separadas 10 m.
    # La separación se mide por G'(0) sobre el extent, no por
    # area/longitud (7.5 m aquí): con curvas rectas las dos son parecidas, pero
    # solo G'(0) aguanta el zigzag del trazo real.
    from geophis.raster import (
        _redondear_limpio,
        _resolver_parametros,
        _separacion_media,
    )

    curvas = _curvas_rampa()
    bounds = tuple(curvas.total_bounds)
    assert _separacion_media(curvas) == pytest.approx(7.5)  # el respaldo viejo
    resolucion, _, muestreo, sep = _resolver_parametros(
        curvas, "cota", None, None, None, bounds
    )
    assert resolucion == _redondear_limpio(sep / 2)
    assert muestreo == sep  # el muestreo YA NO se redondea
    # y el default llega hasta el transform
    _, tf = geo.interpolar_mde(curvas, "cota")
    assert tf[0] == resolucion


def test_muestreo_por_defecto_no_es_la_resolucion():
    # regresión del default viejo (`intervalo_muestreo = resolucion`), que
    # garantizaba la nube anisótropa que hace ondular al spline
    from geophis.raster import _resolver_parametros

    curvas = _curvas_rampa()
    resolucion, _, muestreo, _ = _resolver_parametros(
        curvas, "cota", 1.0, None, None, tuple(curvas.total_bounds)
    )
    assert resolucion == 1.0 and muestreo > resolucion


def test_interpolar_mde_fuera_de_envolvente_es_nan():
    curvas = _curvas_rampa()
    # cuadro más grande que las curvas: las esquinas exteriores quedan NaN
    cuadro = _gdf_box(-10, -10, 40, 40)
    mde, _ = geo.interpolar_mde(curvas, "cota", resolucion=5, cuadro=cuadro)
    assert np.isnan(mde).any()


def test_interpolar_mde_idw_no_deja_hueco_interior():
    curvas = _curvas_rampa()
    mde, _ = geo.interpolar_mde(curvas, "cota", resolucion=5, metodo="idw")
    # dentro de la envolvente IDW rellena todo; el rango queda acotado por las cotas
    interior = mde[~np.isnan(mde)]
    assert interior.min() >= 0 and interior.max() <= 30


def test_interpolar_mde_campo_inexistente_falla():
    with pytest.raises(ValueError, match="no está en curvas"):
        geo.interpolar_mde(_curvas_rampa(), "no_existe")


def test_interpolar_mde_cuadro_sin_interseccion_falla():
    curvas = _curvas_rampa()
    lejos = _gdf_box(1000, 1000, 1010, 1010)
    with pytest.raises(ValueError, match="no intersecta"):
        geo.interpolar_mde(curvas, "cota", resolucion=5, cuadro=lejos)


# ---- validación de MDE (hold-out) -----------------------------------------


def test_validar_mde_rampa_error_bajo():
    # rampa plana z=y: el TIN la reproduce, así que el error queda acotado por
    # el muestreo del píxel (<= resolucion), no por la interpolación.
    curvas = _curvas_rampa()
    metricas, puntos = geo.validar_mde(
        curvas, "cota", resolucion=5, intervalo_muestreo=1, semilla=0
    )
    assert set(metricas) == {
        "rms",
        "error_medio",
        "error_abs_medio",
        "p50",
        "p90",
        "p95",
        "max",
        "n_puntos_prueba",
    }
    assert metricas["n_puntos_prueba"] > 0
    assert metricas["max"] <= 5  # error de muestreo acotado por la resolucion
    assert "residual" in puntos.columns
    assert puntos.crs == curvas.crs


def test_validar_mde_marca_la_curva_de_origen():
    # con el origen, cazar una curva con la cota mal capturada es un groupby;
    # sin él hay que simbolizar mapas
    curvas = _curvas_rampa()  # cotas distintas por curva: la cota identifica
    _, puntos = geo.validar_mde(
        curvas, "cota", resolucion=5, intervalo_muestreo=1, semilla=0
    )
    assert (puntos["curva"].map(curvas["cota"]) == puntos["cota"]).all()


def test_validar_mde_semilla_reproducible():
    curvas = _curvas_rampa()
    kw = dict(resolucion=5, intervalo_muestreo=1, semilla=7)
    m1, _ = geo.validar_mde(curvas, "cota", **kw)
    m2, _ = geo.validar_mde(curvas, "cota", **kw)
    assert m1 == m2


def test_validar_mde_porcentaje_fuera_de_rango_falla():
    with pytest.raises(ValueError, match="fuera de"):
        geo.validar_mde(_curvas_rampa(), "cota", porcentaje_reserva=1.5)


def test_validar_mde_pocos_puntos_falla():
    # muestreo grueso: pocos puntos, no alcanza el mínimo de prueba
    with pytest.raises(ValueError, match="muy pocos puntos"):
        geo.validar_mde(_curvas_rampa(), "cota", resolucion=5, intervalo_muestreo=30)


def test_interpolar_mde_avisa_bajo_el_piso_antialias(capsys):
    """Curvas cada 10 m -> piso 5 m. h=2 aliasea el rizo del spline."""
    geo.interpolar_mde(_curvas_rampa(), "cota", resolucion=2)
    assert "piso antialias" in capsys.readouterr().out


def test_interpolar_mde_sobre_el_piso_no_avisa(capsys):
    geo.interpolar_mde(_curvas_rampa(), "cota", resolucion=5)
    assert "piso antialias" not in capsys.readouterr().out


def test_interpolar_mde_tin_no_tiene_rizo_que_aliasear(capsys):
    """El piso es de la RBF: al TIN no se le aplica."""
    geo.interpolar_mde(_curvas_rampa(), "cota", resolucion=2, metodo="tin")
    assert "piso antialias" not in capsys.readouterr().out


def test_interpolar_mde_con_amplitud_medida_reporta_en_vez_de_dictaminar(capsys):
    """Con `A` medida el piso deja de ser el juez: sale el numero, no el veredicto.

    SIGUIENTE §3.1 y §8: el piso global describe el LLANO y en un predio de dos
    regimenes se equivoca; cuando el criterio y la medicion directa chocan, gana
    la medicion. El aviso sigue saliendo (h SI esta bajo el piso), pero dice
    cuanta pendiente espuria entra de verdad, que es lo accionable.
    """
    geo.interpolar_mde(_curvas_rampa(), "cota", resolucion=2, amplitud_rizo=0.5)
    salida = capsys.readouterr().out
    assert "MEDIDA" in salida and "pendiente espuria" in salida
    # el texto viejo prometia un veredicto ("engorda la clase mas alta"); con `A`
    # medida no se puede prometer eso sin conocer los rangos
    assert "engorda la clase" not in salida


def test_pendiente_espuria_reproduce_la_cifra_medida():
    """A=1.4 m sobre lambda=76 m a h=5 m da ~11 % (SIGUIENTE §3.1).

    Es el unico numero de la formula que esta MEDIDO en campo; si esto se
    desvia, la formula dejo de ser la que se defendio en el expediente.
    """
    from geophis.raster import _pendiente_espuria

    assert _pendiente_espuria(1.4, 5.0, 76.0) == pytest.approx(11.25, abs=0.1)
    # se anula en 2h = lambda, que es la definicion del piso
    assert _pendiente_espuria(1.4, 38.0, 76.0) == pytest.approx(0.0, abs=1e-9)


def test_lambda_dada_no_se_vuelve_a_medir(monkeypatch):
    """Pasar `lambda_m` salta la medicion; sin ella se mide.

    Es el ahorro de `barrer_resolucion`: N corridas sobre un `cuadro` fijo
    miden el MISMO numero. Se comprueba contando llamadas, no cronometrando.
    """
    from geophis import raster as raster_mod

    llamadas = []
    real = raster_mod._separacion_horizontal

    def espia(curvas, bounds):
        llamadas.append(bounds)
        return real(curvas, bounds)

    monkeypatch.setattr(raster_mod, "_separacion_horizontal", espia)
    curvas = _curvas_rampa()
    geo.interpolar_mde(curvas, "cota", resolucion=5)
    assert len(llamadas) == 1
    geo.interpolar_mde(
        curvas,
        "cota",
        resolucion=5,
        intervalo_muestreo=10,
        lambda_m=10.0,
    )
    assert len(llamadas) == 1  # no volvio a medir


def test_lambda_no_finita_se_remide(monkeypatch):
    """NaN no es una lambda: `barrer_resolucion` la pone cuando no se pudo medir."""
    from geophis import raster as raster_mod

    llamadas = []
    real = raster_mod._separacion_horizontal

    def espia(curvas, bounds):
        llamadas.append(bounds)
        return real(curvas, bounds)

    monkeypatch.setattr(raster_mod, "_separacion_horizontal", espia)
    geo.interpolar_mde(_curvas_rampa(), "cota", resolucion=5, lambda_m=float("nan"))
    assert len(llamadas) == 1


def test_aviso_sin_dato_sobrevive_a_MOSTRAR_false(capsys, monkeypatch):
    """`geo.MOSTRAR = False` apaga el reporte, no la senal de defecto.

    Los scripts entregables imprimen lo suyo y llaman con `geo.MOSTRAR = False`. Si el
    hueco se fuera con el reporte, la corrida que se cita seria justo la que no
    puede ver que tiene superficie sin dato de origen.
    """
    monkeypatch.setattr(geo, "MOSTRAR", False)
    curvas = gpd.GeoDataFrame(
        {"COTA": [100.0, 120.0, 140.0]},
        geometry=[LineString([(0, i * 60), (300, i * 60)]) for i in range(3)],
        crs=UTM,
    )
    cuadro = gpd.GeoDataFrame(geometry=[box(-120, -120, 420, 240)], crs=UTM)
    mde, _ = geo.interpolar_mde(curvas, "COTA", resolucion=20, cuadro=cuadro)
    salida = capsys.readouterr().out
    assert np.isnan(mde).any()
    assert "sin dato de origen" in salida and "margen_borde_celdas" in salida
    assert "extent:" not in salida  # el reporte si se callo


def test_spline_vecindario_colineal_falla_diciendo_que_mirar():
    """Un `intervalo_muestreo` mil veces fino deja cada vecindario sobre UNA curva.

    scipy tira ahi un "Singular matrix ... rank 2/3" que no menciona curvas por
    ningun lado. El caso real que lo dispara es confundir `p_max` en % con la
    fraccion (`e/100` en vez de `e/1.0`), y el mensaje tiene que nombrarlo: un
    error opaco a mitad de una interpolacion de minutos cuesta la corrida.
    """
    with pytest.raises(ValueError, match="colineales"):
        geo.interpolar_mde(
            _curvas_rampa(),
            "cota",
            resolucion=5,
            intervalo_muestreo=0.1,
            vecinos=8,
        )


def test_spline_no_cambia_de_metodo_solo(capsys):
    """No hay respaldo automatico: el metodo mueve la superficie por rango.

    El TIN si cae a IDW (Qhull falla del todo y no hay parametro que salve);
    aqui la salida es subir `vecinos` o elegir 'tin' a mano, y elegir por el
    usuario seria cambiarle la cifra sin decirselo.
    """
    with pytest.raises(ValueError):
        geo.interpolar_mde(
            _curvas_rampa(),
            "cota",
            resolucion=5,
            intervalo_muestreo=0.1,
            vecinos=8,
        )
    assert "usando IDW" not in capsys.readouterr().out


# ---- invalidar (pendiente y aspecto) --------------------------------------


def test_invalidar_equivale_a_asignar_nan_despues():
    """El parametro existe para que el script no importe numpy por una linea.

    Tiene que ser IDENTICO a `pendiente[mascara] = np.nan`, o seria una funcion
    nueva disfrazada de atajo.
    """
    mde = np.arange(36, dtype=float).reshape(6, 6)
    mascara = np.zeros((6, 6), bool)
    mascara[2:4, 2:4] = True

    a = geo.calcular_pendiente(mde, TF)
    a[mascara] = np.nan
    b = geo.calcular_pendiente(mde, TF, invalidar=mascara)
    assert np.array_equal(a, b, equal_nan=True)

    with pytest.raises(ValueError, match="no cuadra"):
        geo.calcular_pendiente(mde, TF, invalidar=np.zeros((3, 3), bool))


def test_invalidar_tambien_en_aspecto():
    """Pendiente y aspecto describen la MISMA superficie: o las dos, o ninguna.

    Invalidar una zona en la pendiente y no en el aspecto deja dos opiniones
    distintas de donde hay dato.
    """
    mde = np.arange(36, dtype=float).reshape(6, 6)
    mascara = np.zeros((6, 6), bool)
    mascara[2:4, 2:4] = True
    assert (geo.calcular_orientacion(mde, TF, invalidar=mascara)[mascara] == 0).all()
    assert np.isnan(
        geo.calcular_orientacion(mde, TF, unidad="grados", invalidar=mascara)[mascara]
    ).all()


# ---- I/O en disco ---------------------------------------------------------


def test_guardar_y_cargar_raster_roundtrip(tmp_path):
    arr = np.arange(9, dtype=float).reshape(3, 3)
    ruta = str(tmp_path / "r.tif")
    # origen real, evita warning
    geo.guardar_raster(arr, ruta, Affine(1, 0, 500000, 0, -1, 2000000), UTM)
    leido, _, crs, res = geo.cargar_raster(ruta)
    assert leido.shape == (3, 3)
    assert crs.to_epsg() == 32613 and res == 1
    assert np.allclose(leido, arr)


def test_quiebres_imponen_el_talweg_que_el_tin_pelado_aplana():
    """`interpolar_mde(quiebres=)` y `cotas_por_cruce`, contra verdad analítica.

    Valle en V, `z = y + 0.5*|x - 50|`: las curvas son chevrones con el ápice
    sobre el talweg `x = 50`, y el cauce corre por él. La cota de cada cruce es
    la de la curva, y entre cruces es lineal, así que sobre el cauce la z es
    EXACTA. El TIN sin quiebres apoya triángulos en dos brazos del mismo
    chevrón y levanta el fondo casi 3 m; con quiebres el error es de redondeo.
    """
    crs = "EPSG:32613"
    curvas = gpd.GeoDataFrame(
        {"ELEV": [0.0, 10.0, 20.0, 30.0, 40.0]},
        geometry=[
            LineString([(0, c - 25), (50, c), (100, c - 25)]) for c in (0, 10, 20, 30, 40)
        ],
        crs=crs,
    )
    cauce = gpd.GeoDataFrame(geometry=[LineString([(50, -5), (50, 45)])], crs=crs)

    puntos = geo.cotas_por_cruce(cauce, curvas, "ELEV", 5.0)
    assert np.allclose(sorted(puntos["z"]), np.arange(0, 41, 5))  # sin extrapolar
    assert np.allclose(puntos.geometry.x, 50)

    cuadro = _gdf_box(1, 0, 99, 40)
    kw = dict(resolucion=2, intervalo_muestreo=20, cuadro=cuadro, metodo="tin")
    col = 24  # centro de pixel en x = 1 + 24.5*2 = 50, el talweg
    y = 40 - (np.arange(20) + 0.5) * 2
    liso, _ = geo.interpolar_mde(curvas, "ELEV", **kw)
    con, _ = geo.interpolar_mde(curvas, "ELEV", quiebres=cauce, **kw)
    assert np.nanmax(np.abs(liso[:, col] - y)) > 2.5
    assert np.nanmax(np.abs(con[:, col] - y)) < 1e-9

    # las guardas: otro CRS, ninguna linea con dos cruces, y el spline
    with pytest.raises(ValueError, match="reproyecta"):
        geo.cotas_por_cruce(cauce.to_crs(4326), curvas, "ELEV", 5.0)
    paralela = gpd.GeoDataFrame(geometry=[LineString([(0, 60), (100, 60)])], crs=crs)
    with pytest.raises(ValueError, match="cruza dos veces"):
        geo.cotas_por_cruce(paralela, curvas, "ELEV", 5.0)
    with pytest.raises(ValueError, match="metodo='tin'"):
        geo.interpolar_mde(curvas, "ELEV", resolucion=2, quiebres=cauce)


# ---- raster de QA ----------------------------------------------------------


def test_resumen_raster_conocido_y_todo_nan():
    arr = np.array([[1, 2, 3], [4, np.nan, 6], [7, 8, 9]], dtype=float)
    r = geo.resumen_raster(arr, Affine(10, 0, 0, 0, -10, 0))
    assert (r["n_celdas"], r["n_nan"]) == (9, 1)
    assert (r["min"], r["max"], r["media"], r["p50"]) == (1, 9, 5, 5)
    assert r["ha_sin_dato"] == pytest.approx(0.01)  # una celda de 100 m2
    assert "ha_sin_dato" not in geo.resumen_raster(arr)
    with pytest.raises(ValueError, match="sin datos"):
        geo.resumen_raster(np.full((2, 2), np.nan))


def test_sombreado_plano_y_orientacion_contra_aspecto():
    # plano horizontal: 255*cos(zen) en el interior, 0 en el anillo sin ventana
    plano = geo.sombreado(np.full((5, 5), 100.0), 10)
    assert plano.dtype == np.uint8
    assert (plano[1:-1, 1:-1] == round(255 * np.cos(np.radians(45)))).all()
    assert (plano[0] == 0).all()

    # z = fila + col sube al SE, asi que la ladera mira (cae) al NW
    r, c = np.mgrid[0:7, 0:7].astype(float)
    nw, se = (r + c) * 3, -(r + c) * 3
    asp = geo.calcular_orientacion(nw, 10, unidad="grados")
    assert asp[3, 3] == pytest.approx(315)  # el signo que usa el sombreado
    luz_nw, luz_se = geo.sombreado(nw, 10), geo.sombreado(se, 10)
    assert luz_nw[3, 3] > luz_se[3, 3]  # sol del NW (azimut 315)
    assert geo.sombreado(se, 10, azimut=135)[3, 3] == luz_nw[3, 3]
    with pytest.raises(ValueError, match="altitud"):
        geo.sombreado(nw, 10, altitud=0)


def test_estadisticas_zonales_nan_zona_chica_y_solape(gdf, capsys):
    arr = np.arange(16, dtype=float).reshape(4, 4)
    arr[0, 0] = np.nan
    zonas = gdf(
        box(0, -2, 2, 0),  # filas 0-1, cols 0-1: NaN, 1, 4, 5
        box(2, -4, 4, -2),  # filas 2-3, cols 2-3: 10, 11, 14, 15
        box(0.55, -3.95, 1.05, -3.45),  # 0.5 px, no cubre ningun centro
        ID=["A", "B", "C"],
    )
    t = geo.estadisticas_zonales(
        arr, TF, zonas, "ID", ("media", "min", "max", "n", "suma", "desv")
    )
    assert list(t["ID"]) == ["A", "B", "C"]
    assert t["media"].iloc[0] == pytest.approx(10 / 3)
    assert (t["min"].iloc[0], t["max"].iloc[0], t["n"].iloc[0]) == (1, 5, 3)
    assert (t["n_celdas"].iloc[0], t["n_nan"].iloc[0]) == (4, 1)
    assert t["media"].iloc[1] == 12.5 and t["suma"].iloc[1] == 50
    assert t["desv"].iloc[1] == pytest.approx(np.sqrt(4.25))
    assert t["n_celdas"].iloc[2] == 0 and t.iloc[2][["media", "min"]].isna().all()
    assert "1 zona(s) sin ninguna celda" in capsys.readouterr().out

    with pytest.raises(ValueError, match="resolver_por_prioridad"):
        geo.estadisticas_zonales(
            arr, TF, gdf(box(0, -2, 2, 0), box(1, -3, 3, -1), ID=[1, 2]), "ID"
        )
    with pytest.raises(ValueError, match="otro CRS"):
        geo.estadisticas_zonales(arr, TF, gdf(box(50, 50, 60, 60), ID=[1]), "ID")


def test_alinear_rasters_medio_pixel():
    # el mismo campo f(x) = x muestreado en dos mallas desplazadas medio pixel
    tf_a, tf_b = Affine(1, 0, 0, 0, -1, 10), Affine(1, 0, 0.5, 0, -1, 10)
    a = np.tile(np.arange(10) + 0.5, (10, 1))  # centros en x = c + 0.5
    b = np.tile(np.arange(10) + 1.0, (10, 1))  # centros en x = c + 1
    # la TRAMPA: misma forma, se restan sin error y dan -0.5 donde el campo es igual
    assert np.allclose(a - b, -0.5)
    (b_al,) = geo.alinear_rasters((a, tf_a, UTM), (b, tf_b, UTM))
    assert b_al.shape == a.shape
    assert np.allclose((a - b_al)[:, 1:], 0)
    with pytest.raises(ValueError, match="al menos un raster"):
        geo.alinear_rasters((a, tf_a, UTM))


def test_curvas_de_nivel_en_la_y_exacta_y_cortadas_por_nan():
    tf = Affine(1, 0, 0, 0, -1, 10)
    fila = np.arange(10)[:, None]
    mde = np.repeat(9.5 - fila, 8, axis=1).astype(float)  # z = y del centro
    curvas = geo.curvas_de_nivel(mde, tf, UTM, equidistancia=2)
    assert list(curvas["cota"]) == [2, 4, 6, 8]
    for cota, geom in zip(curvas["cota"], curvas.geometry):
        # con las esquinas en vez de los centros caerian en cota + 0.5
        assert np.allclose(np.asarray(geom.coords)[:, 1], cota)

    mde[:, 4] = np.nan  # los NaN cortan cada curva en dos, no se rellenan
    assert len(geo.curvas_de_nivel(mde, tf, UTM, 2)) == 8
    with pytest.raises(ValueError, match="equidistancia"):
        geo.curvas_de_nivel(mde, tf, UTM, 0)


@pytest.mark.filterwarnings("ignore::rasterio.errors.NotGeoreferencedWarning")
def test_vista_previa_array_se_relee_y_no_se_agranda(tmp_path):
    """Rampa con un NaN: se escribe y se relee igual, con varios grises, NaN en 0
    y el dato minimo en 1 (no en 0). Un array ancho se reduce a ancho_px."""
    import rasterio

    arr = np.tile(np.arange(50, dtype=float), (20, 1))
    arr[0, 0] = np.nan
    img = geo.vista_previa(arr, tmp_path / "rampa.png")
    with rasterio.open(tmp_path / "rampa.png") as f:
        leida = f.read(1)
    assert leida.shape == (20, 50)
    assert (leida == img).all()
    assert len(np.unique(leida)) > 2
    assert leida[0, 0] == 0 and leida[1, 0] == 1 and leida[1, -1] == 255

    tf = Affine(2.5, 0, 0, 0, -2.5, 50)
    img = geo.vista_previa(np.ones((20, 400)), tmp_path / "ancho.png", tf, ancho_px=100)
    assert img.shape == (5, 100)
    with pytest.raises(ValueError, match="png"):
        geo.vista_previa(arr, tmp_path / "x.tif")
    with pytest.raises(ValueError, match="sin datos"):
        geo.vista_previa(np.full((3, 3), np.nan), tmp_path / "nan.png")


def test_vista_previa_capa_y_capa_encima(tmp_path):
    """Un cuadrado solo: borde blanco, interior negro. Encima de un array: el
    borde pisa en 255 y el interior conserva el gris del array."""
    cuadrado = _gdf_box(0, 0, 100, 100)
    img = geo.vista_previa(cuadrado, tmp_path / "capa.png", ancho_px=102)
    assert img.shape[1] == 102
    assert set(np.unique(img)) == {0, 255}
    assert img[img.shape[0] // 2, 51] == 0  # interior vacio: es contorno

    x0, y0, x1, y1 = cuadrado.total_bounds
    res = (x1 - x0) / 10
    tf = Affine(res, 0, x0 - 5 * res, 0, -res, y1 + 5 * res)
    fondo = np.tile(np.arange(20, dtype=float), (20, 1))
    img = geo.vista_previa((fondo, cuadrado), tmp_path / "ambos.png", tf)
    assert img[5, 10] == 255  # borde superior del cuadrado
    assert 0 < img[10, 10] < 255  # interior: el gris del fondo
    with pytest.raises(ValueError, match="transform"):
        geo.vista_previa((fondo, cuadrado), tmp_path / "sin_tf.png")


# ---- escala y desfase del archivo ------------------------------------------


def _tif_s2(tmp_path, arr, nombre="s2.tif", crs=UTM, x0=500000, y0=2000000, res=1):
    """GeoTIFF uint16 con la escala y el desfase de un Sentinel-2 L2A >= 04.00."""
    import rasterio

    ruta = str(tmp_path / nombre)
    arr = np.asarray(arr)
    with rasterio.open(
        ruta,
        "w",
        driver="GTiff",
        crs=crs,
        transform=Affine(res, 0, x0, 0, -res, y0),
        width=arr.shape[1],
        height=arr.shape[0],
        count=1,
        dtype="uint16",
        nodata=0,
    ) as dst:
        dst.write(arr.astype("uint16"), 1)
        dst.scales = (0.0001,)
        dst.offsets = (-0.1,)
    return ruta


def test_lectores_aplican_escala_y_desfase(tmp_path):
    # nodata sobre el CRUDO y luego escala: el 0 no se vuelve -0.1
    ruta = _tif_s2(tmp_path, [[0, 1300, 4000]])
    esperado = [[np.nan, 0.03, 0.30]]
    r, *_ = geo.cargar_raster(ruta)
    np.testing.assert_allclose(r, esperado)
    b, *_ = geo.cargar_bandas(ruta, (1,))
    np.testing.assert_allclose(b[0], esperado)
    c, *_ = geo.recortar_raster(ruta, _gdf_box(500000, 1999999, 500003, 2000000))
    np.testing.assert_allclose(c, esperado)


def test_lectores_sin_escala_no_cambian(tmp_path):
    arr = np.array([[1.0, 2.5], [3.0, 4.0]])
    r, *_ = geo.cargar_raster(_tif(tmp_path, arr))
    np.testing.assert_array_equal(r, arr)


# ---- mosaico ---------------------------------------------------------------


def test_mosaico_solape_toma_la_primera(tmp_path):
    a = _tif_s2(tmp_path, np.full((10, 10), 2000), "a.tif")  # x 500000-500010
    b = _tif_s2(tmp_path, np.full((10, 10), 3000), "b.tif", x0=500005)
    predio = gpd.GeoDataFrame(geometry=[box(500001, 1999992, 500014, 1999998)], crs=UTM)
    arr, tf, crs, res = geo.mosaico([a, b], predio)
    dentro = geo.rasterizar(predio, arr.shape, tf) == 1
    assert not np.isnan(arr[dentro]).any()  # sin hueco en la costura
    assert np.isnan(arr[~dentro]).all()
    col = lambda x: int((x - tf.c) / res)  # noqa: E731
    fila = int((tf.f - 1999995) / res)
    assert arr[fila, col(500007.5)] == pytest.approx(0.1)  # solape: gana `a`
    assert arr[fila, col(500012.5)] == pytest.approx(0.2)
    assert crs == UTM and res == 1


def test_mosaico_otro_huso_sin_hueco(tmp_path):
    # la tesela del huso vecino se reproyecta al vuelo y cierra la costura
    a = _tif_s2(tmp_path, np.full((100, 60), 2000), "a.tif", res=10)
    # misma zona en UTM 14N: x14 ~ x13 - 6 grados. Se arma con la esquina real
    import pyproj

    t = pyproj.Transformer.from_crs(32613, 32614, always_xy=True)
    bx, by = t.transform(500500, 1999500)
    b = _tif_s2(
        tmp_path, np.full((100, 100), 3000), "b.tif", 32614, bx - 500, by + 500, 10
    )
    predio = gpd.GeoDataFrame(geometry=[box(500100, 1999200, 500900, 1999800)], crs=UTM)
    arr, tf, crs, _ = geo.mosaico([a, b], predio, crs=32613)
    dentro = geo.rasterizar(predio, arr.shape, tf) == 1
    assert crs == CRS.from_epsg(32613)
    assert not np.isnan(arr[dentro]).any()
    assert np.nanmin(arr) == pytest.approx(0.1) and np.nanmax(arr) == pytest.approx(0.2)


def test_mosaico_escalas_distintas_falla(tmp_path):
    a = _tif_s2(tmp_path, np.full((4, 4), 2000), "a.tif")
    b = _tif(tmp_path, np.ones((4, 4)))
    with pytest.raises(ValueError, match="escala"):
        geo.mosaico([a, b], _gdf_box(500000, 1999996, 500004, 2000000))


def test_mosaico_sin_cubrir_da_extents(tmp_path):
    a = _tif_s2(tmp_path, np.full((4, 4), 2000), "a.tif")
    with pytest.raises(ValueError, match="ninguna fuente pisa") as e:
        geo.mosaico([a], _gdf_box(600000, 1999996, 600004, 2000000))
    assert "500000" in str(e.value) and "600000" in str(e.value)


def test_mosaico_vacio_falla():
    with pytest.raises(ValueError, match="vac"):
        geo.mosaico([], _gdf_box(0, 0, 1, 1))


# ---- guardar_raster multibanda ---------------------------------------------


def test_guardar_raster_multibanda_con_nombres(tmp_path):
    import rasterio

    pila = np.arange(3 * 4 * 5, dtype=float).reshape(3, 4, 5)
    ruta = str(tmp_path / "m.tif")
    tf = Affine(1, 0, 500000, 0, -1, 2000000)
    geo.guardar_raster(list(pila), ruta, tf, UTM, nombres=("a", "b", "c"))
    leido, *_ = geo.cargar_bandas(ruta, (1, 2, 3))
    np.testing.assert_array_equal(leido, pila)
    with rasterio.open(ruta) as src:
        assert src.descriptions == ("a", "b", "c")


def test_guardar_raster_formas_distintas_falla(tmp_path):
    tf = Affine(1, 0, 0, 0, -1, 0)
    with pytest.raises(ValueError, match="formas distintas"):
        geo.guardar_raster(
            [np.ones((2, 2)), np.ones((3, 2))], str(tmp_path / "x.tif"), tf, UTM
        )
    with pytest.raises(ValueError, match="nombres"):
        geo.guardar_raster(
            np.ones((2, 2, 2)), str(tmp_path / "x.tif"), tf, UTM, nombres=("a",)
        )


# ---- ruta de costo minimo -------------------------------------------------


def test_ruta_costo_minimo_rodea_la_barrera_y_valida_puntos():
    # muro de costo alto en la columna 2 con un hueco abajo: la ruta lo usa
    costo = np.ones((5, 5))
    costo[:4, 2] = 1000
    pts = gpd.GeoDataFrame(geometry=[Point(0.5, -0.5), Point(4.5, -0.5)], crs=UTM)
    ruta = geo.ruta_costo_minimo(costo, TF, UTM, pts)
    linea = ruta.geometry.iloc[0]
    assert linea.coords[0] == (0.5, -0.5) and linea.coords[-1] == (4.5, -0.5)
    assert min(y for _, y in linea.coords) == -4.5  # bajo hasta el hueco
    assert ruta["COSTO"].iloc[0] < 1000

    # columna entera sin dato: sin paso
    costo[:, 2] = np.nan
    with pytest.raises(ValueError, match="no hay paso"):
        geo.ruta_costo_minimo(costo, TF, UTM, pts)

    fuera = gpd.GeoDataFrame(geometry=[Point(0.5, -0.5), Point(9.5, -0.5)], crs=UTM)
    with pytest.raises(ValueError, match="fuera del ráster"):
        geo.ruta_costo_minimo(np.ones((5, 5)), TF, UTM, fuera)


def test_ruta_a_pie_anisotropa():
    # plano inclinado al 45 % hacia el sur (fila = 1 m, z sube hacia el norte)
    z = np.tile(np.arange(21.0)[::-1, None] * 0.45, (1, 21))
    km_h_llano = 6 * np.exp(-3.5 * 0.05)

    # los dos puntos en la misma curva de nivel: va a nivel y cuesta como llano
    pts = gpd.GeoDataFrame(geometry=[Point(0.5, -10.5), Point(20.5, -10.5)], crs=UTM)
    r = geo.ruta_a_pie(z, TF, UTM, pts)
    assert r["HORAS"].iloc[0] == pytest.approx(0.020 / (km_h_llano * 0.6))

    # el mismo tramo por camino: 5/3 mas rapido
    r = geo.ruta_a_pie(z, TF, UTM, pts, caminos=np.ones(z.shape, dtype=bool))
    assert r["HORAS"].iloc[0] == pytest.approx(0.020 / km_h_llano)

    # subir no cuesta lo mismo que bajar
    sube = gpd.GeoDataFrame(geometry=[Point(10.5, -20.5), Point(10.5, -0.5)], crs=UTM)
    baja = gpd.GeoDataFrame(geometry=[Point(10.5, -0.5), Point(10.5, -20.5)], crs=UTM)
    assert geo.ruta_a_pie(z, TF, UTM, sube)["HORAS"].iloc[0] > geo.ruta_a_pie(z, TF, UTM, baja)["HORAS"].iloc[0]

    z[:, 10] = np.nan
    with pytest.raises(ValueError, match="no hay paso"):
        geo.ruta_a_pie(z, TF, UTM, pts)
