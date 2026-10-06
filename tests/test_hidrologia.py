"""Tests de la rama hidrologica (pysheds): flujo D8, acumulacion y cuencas.

Lo que se comprueba aqui es el CONTENIDO, no que la llamada no truene:

- que la direccion apunta al vecino BAJO, con el codigo D8 correcto. Un swap
  en el dirmap pasa todos los tests de shape y manda el agua cuesta arriba.
- que la acumulacion cuenta las celdas que drenan, con la identidad exacta
  `acc[j+1] == acc[j] + 1` sobre una rampa, donde cada celda aporta una.
- que `acondicionar_mde` rellena la depresion, no solo el NoData. Una cuenca
  endorreica sin rellenar corta la red de drenaje en silencio.
- que la cadena entera concentra el flujo donde toca (valle en V) y que
  `etiquetar_laderas` separa las dos laderas.

El MDE es sintetico y la respuesta se deriva a mano. No hace falta un MDE real:
lo que se prueba son identidades, no un paisaje.

Convenio D8 de pysheds (dirmap por defecto): N=64, NE=128, E=1, SE=2, S=4,
SW=8, W=16, NW=32.

El efecto de `quemar_cauces` sobre el flujo tambien se prueba aqui, con el
cauce CUESTA ABAJO: uno transversal a la pendiente crea una depresion que
`fill_depressions` vuelve a rellenar, y entonces el test mediria el
acondicionado y no el desvio. Lo que `quemar_cauces` hace por si sola (bajar la
cota, dejar el trazo 8-conectado, avisar sobre NoData) esta en test_raster.py.
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from rasterio.crs import CRS
from rasterio.transform import Affine
from shapely.geometry import box, LineString, Point
from shapely.ops import unary_union

import geophis as geo
from geophis.hidrologia import _acumular as _acumular_fdir, _sin_salida

pytest.importorskip("pysheds")

UTM = CRS.from_epsg(32613)
ESTE = 1  # codigo D8 de pysheds para "fluye al este"


def _a_disco(z, tmp_path, res=10.0):
    """Escribe el MDE a .tif (pysheds lee de disco) y devuelve la ruta."""
    ruta = tmp_path / "mde.tif"
    tf = Affine(res, 0, 500_000, 0, -res, 2_000_000)
    geo.guardar_raster(z, ruta, tf, UTM)
    return ruta


def _rampa_al_este(filas=12, columnas=12):
    """Plano que baja hacia el este: toda celda drena a su vecino de la derecha."""
    return np.tile(100.0 - np.arange(columnas, dtype=float), (filas, 1))


# ---- el parche de compatibilidad y su TECHO -------------------------------


def test_parche_repone_in1d_con_la_semantica_correcta():
    # `in1d` es `isin` aplanando la entrada. pysheds la llama SIEMPRE sobre un
    # `.ravel()`, asi que el alias es exacto para este uso. Si algun dia se
    # llamara con un array 2D, `isin` conservaria la forma y `in1d` no.
    geo.hidrologia._parchear_pysheds_numpy2()
    plano = np.array([1, 2, 64, 7])
    assert np.in1d(plano, [1, 2, 4, 8, 16, 32, 64, 128]).tolist() == [
        True,
        True,
        True,
        False,
    ]


def test_el_parche_de_pysheds_sigue_haciendo_falta():
    """El techo escrito en `_parchear_pysheds_numpy2`, cobrado por un test.

    Un techo documentado que nadie comprueba no se cobra nunca: el parche se
    queda ahi para siempre porque borrarlo da miedo y nada dice que ya sobra.
    Esto lo dice. Falla el dia que pysheds publique una version que no llame a
    `np.in1d`, y entonces la accion es BORRAR el parche, su llamada en
    `acondicionar_mde` y estos dos tests.

    Se lee el fuente en vez de llamar a la funcion porque el parche, una vez
    aplicado, hace que numpy tenga `in1d` otra vez: preguntarle a numpy no
    distingue "pysheds ya no la usa" de "el parche ya corrio".
    """
    sgrid = pytest.importorskip("pysheds.sgrid")
    fuente = Path(sgrid.__file__).read_text(encoding="utf-8", errors="replace")
    assert "np.in1d" in fuente, (
        "pysheds ya no llama a `np.in1d`: el parche `_parchear_pysheds_numpy2` "
        "sobra.\nBorralo, borra su llamada en `acondicionar_mde` y borra estos "
        "dos tests."
    )
    assert not hasattr(np, "in1d") or np.in1d is np.isin, (
        "numpy volvio a traer `in1d` propia: comprueba si el parche sigue "
        "pisando algo que ya existe."
    )


# ---- direccion de flujo ---------------------------------------------------


def test_direccion_flujo_apunta_al_vecino_bajo(tmp_path):
    # el test que pillaria un swap en el dirmap: sobre una rampa que baja al
    # este, TODO el interior tiene que llevar el codigo del este y ninguno otro.
    grid, dem = geo.acondicionar_mde(_a_disco(_rampa_al_este(), tmp_path))
    fdir = np.asarray(geo.acumulacion_flujo(grid, dem).fdir)
    interior = fdir[1:-1, 1:-1]
    assert (interior == ESTE).all(), f"codigos encontrados: {np.unique(interior)}"


def test_direccion_flujo_tapa_el_hoyo_que_fill_pits_alcanza(tmp_path):
    # un hoyo aislado con salida hacia el este: si fill_pits no lo tapa, la
    # celda del fondo no apunta a ningun lado y pysheds la deja en 0.
    #
    # OJO CON EL ALCANCE, el nombre no promete "no deja pits":
    # esto cubre el hoyo de UNA celda y BIEN HUNDIDO, que es justo el caso que
    # `fill_pits` resuelve. Sobre cartografia real quedan sumideros interiores
    # que el acondicionado NO tapa (55 en el MDE entregable de un predio real, el
    # mayor recogiendo el 21 % del raster), porque son residuos de decimas de
    # milimetro del priority-flood y no hoyos de 20 m. Lo que los cobra es
    # `test_acumulacion_flujo_avisa_del_sumidero_interior`; este test no los ve
    # y no pretende verlos.
    z = _rampa_al_este()
    z[6, 6] -= 20
    grid, dem = geo.acondicionar_mde(_a_disco(z, tmp_path))
    assert np.asarray(geo.acumulacion_flujo(grid, dem).fdir)[6, 6] == ESTE


# ---- acumulacion ----------------------------------------------------------


def test_acumulacion_cuenta_una_celda_por_paso(tmp_path):
    # identidad exacta, no cota: en una rampa cada celda aporta exactamente la
    # suya al vecino de aguas abajo, asi que la acumulacion sube de uno en uno.
    # Se mide sobre el interior porque el borde lo rutea pysheds a su manera.
    grid, dem = geo.acondicionar_mde(_a_disco(_rampa_al_este(), tmp_path))
    acc = geo.acumulacion_flujo(grid, dem).acumulacion
    fila = acc[6, 1:-1]
    assert (np.diff(fila) == 1).all(), fila.tolist()


def _fdir_con_sumidero(grid, dem, fila, col):
    """La direccion de flujo de la rampa, con UNA celda interior sin salida.

    Se pincha el codigo en vez de fabricar un MDE que lo produzca, y a proposito:
    el sumidero real es un residuo numerico de `fill_depressions` de decimas de
    milimetro, que no se reproduce a mano de forma estable. Lo que la guarda
    tiene que leer es el CODIGO NEGATIVO en `fdir`, asi que se le da el codigo.
    """
    from pysheds.sview import Raster

    fdir = geo.acumulacion_flujo(grid, dem).fdir
    d = np.asarray(fdir).copy()
    d[fila, col] = -2  # el codigo de pit de pysheds
    # el viewfinder del PROPIO fdir, no el del dem: el del dem lleva nodata NaN
    # y el array es int64, que pysheds rechaza.
    return Raster(d, fdir.viewfinder)


def test_acumulacion_flujo_avisa_del_sumidero_interior(tmp_path, capsys):
    # El defecto medido en un predio real: el flujo muere DENTRO del raster y, sin
    # aviso, `red_drenaje`, `orden_cauces` y `delimitar_cuenca` corren encima.
    grid, dem = geo.acondicionar_mde(_a_disco(_rampa_al_este(), tmp_path))
    fdir = _fdir_con_sumidero(grid, dem, 6, 6)
    _acumular_fdir(grid, fdir)
    salida = capsys.readouterr().out
    assert "INTERIOR" in salida, salida
    # sin la cifra no es una guarda: tiene que decir CUANTO se traga
    assert "% del raster" in salida, salida


def test_acumulacion_flujo_calla_la_condicion_fuerte_si_la_red_llega_al_borde(
    tmp_path, capsys
):
    # La otra mitad, y es la que hace util a la condicion fuerte. En la rampa el
    # sumidero pinchado se traga UNA fila; el borde este se traga las demas, o
    # sea que la red SI llega al borde y el aviso gordo no debe salir. Si saliera
    # aqui, saldria siempre, y volveria a ser ruido en vez de guarda.
    grid, dem = geo.acondicionar_mde(_a_disco(_rampa_al_este(), tmp_path))
    _acumular_fdir(grid, _fdir_con_sumidero(grid, dem, 6, 6))
    assert "sumidero MAYOR" not in capsys.readouterr().out


def test_acumulacion_flujo_avisa_fuerte_si_la_red_no_llega_al_borde(tmp_path, capsys):
    # El caso de un predio real: el sumidero mayor del raster es INTERIOR. Se
    # construye pinchando la celda interior de MAXIMA acumulacion del valle, que
    # por definicion se traga mas que cualquier celda del borde en cuanto corta
    # el paso: aguas abajo de ella ya no llega nada al borde.
    grid, dem = geo.acondicionar_mde(_a_disco(_valle_en_v(), tmp_path))
    acc = geo.acumulacion_flujo(grid, dem).acumulacion
    interior = np.zeros(acc.shape, dtype=bool)
    interior[1:-1, 1:-1] = True
    f, c = np.unravel_index(int(np.where(interior, acc, -1).argmax()), acc.shape)
    _acumular_fdir(grid, _fdir_con_sumidero(grid, dem, int(f), int(c)))
    salida = capsys.readouterr().out
    assert "sumidero MAYOR del raster es interior" in salida, salida


def test_acumulacion_flujo_no_avisa_sin_sumidero_interior(tmp_path, capsys, monkeypatch):
    # una guarda que siempre grita no es una guarda. En la rampa limpia el agua
    # sale por el borde este y no hay ninguna celda interior sin salida.
    monkeypatch.setattr(geo, "MOSTRAR", False)
    grid, dem = geo.acondicionar_mde(_a_disco(_rampa_al_este(), tmp_path))
    geo.acumulacion_flujo(grid, dem)
    assert capsys.readouterr().out == ""


def test_acumulacion_flujo_mostrar_false_no_calla_el_aviso(tmp_path, capsys):
    # la fuga es un defecto, no una linea de reporte: `geo.MOSTRAR = False` no la apaga
    grid, dem = geo.acondicionar_mde(_a_disco(_rampa_al_este(), tmp_path))
    fdir = _fdir_con_sumidero(grid, dem, 6, 6)
    _acumular_fdir(grid, fdir)
    assert "INTERIOR" in capsys.readouterr().out


def test_acumulacion_flujo_devuelve_numpy(tmp_path):
    # la firma promete array numpy, no el Raster de pysheds: los scripts lo
    # pasan a reclasificar_rangos y a poligonizar, que no saben de pysheds.
    grid, dem = geo.acondicionar_mde(_a_disco(_rampa_al_este(), tmp_path))
    acc = geo.acumulacion_flujo(grid, dem).acumulacion
    assert type(acc) is np.ndarray


# ---- acondicionamiento ----------------------------------------------------


def test_acondicionar_mde_rellena_la_depresion(tmp_path):
    # fill_depressions es lo que separa un MDE utilizable de uno que corta la
    # red en silencio: la cuenca endorreica se traga el flujo y aguas abajo
    # sale un cauce que no existe.
    z = _rampa_al_este(14, 14)
    z[5:9, 5:9] -= 30  # bacia cerrada de 4x4
    _, dem = geo.acondicionar_mde(_a_disco(z, tmp_path))
    salida = np.asarray(dem)
    # el fondo subio hasta poder desaguar, y nada bajo
    assert (salida[5:9, 5:9] > z[5:9, 5:9]).all()
    assert (salida >= z - 1e-6).all()


# ---- cadena completa y cuencas -------------------------------------------


def _valle_en_v(filas=20, columnas=21, eje=10):
    """Valle en V con el eje en `eje`, que baja hacia el sur."""
    j = np.abs(np.arange(columnas) - eje) * 2.0
    i = np.arange(filas) * 1.0
    return 100.0 - i[:, None] + j[None, :]


def test_valle_en_v_concentra_el_flujo_en_el_eje(tmp_path):
    # la cadena entera. El maximo de acumulacion tiene que caer EN el eje del
    # valle: es exacto y falla si la direccion se invierte o se transpone.
    grid, dem = geo.acondicionar_mde(_a_disco(_valle_en_v(), tmp_path))
    acc = geo.acumulacion_flujo(grid, dem).acumulacion
    assert np.unravel_index(acc.argmax(), acc.shape)[1] == 10


# `etiquetar_laderas` no toca pysheds: su entrada es un array. Se prueba con
# acumulaciones escritas a mano, donde la respuesta se cuenta con el dedo.


def test_etiquetar_laderas_separa_las_laderas_que_el_cauce_parte():
    # cauce de arriba abajo por la columna central: deja dos laderas inconexas.
    acc = np.zeros((6, 7))
    acc[:, 3] = 100
    etiquetas, n = geo.etiquetar_laderas(acc, umbral=10)
    assert n == 2
    assert etiquetas[0, 0] != etiquetas[0, -1]  # una a cada lado del cauce


def test_etiquetar_laderas_absorbe_el_pixel_de_cauce_suelto():
    # esto es para lo que esta el binary_fill_holes, y no lo cubria nada: una
    # celda que pasa el umbral pero no llega a ningun borde no es un
    # parteaguas, es ruido, y no debe partir la ladera en dos.
    acc = np.zeros((6, 7))
    acc[3, 3] = 100
    etiquetas, n = geo.etiquetar_laderas(acc, umbral=10)
    assert n == 1
    assert etiquetas[3, 3] == 1  # absorbida en la ladera, no dejada en 0


# ---- entregables vectoriales: red, orden y cuenca -------------------------


@pytest.fixture
def valle(tmp_path):
    """La cadena hasta la acumulacion, que las tres de abajo comparten."""
    grid, dem = geo.acondicionar_mde(_a_disco(_valle_en_v(), tmp_path))
    return geo.acumulacion_flujo(grid, dem)


def test_red_drenaje_sale_en_el_crs_del_raster(valle):
    flujo = valle
    grid, fdir, acc = flujo
    red = geo.red_drenaje(flujo, umbral=20)
    assert not red.empty
    assert red.crs is not None and red.crs.to_epsg() == 32613
    assert set(red.geom_type) == {"LineString"}
    # el cauce del valle en V es el eje: cae dentro de la columna 10 (x del
    # centro de esa celda = 500_000 + 10.5*10). Media celda de holgura.
    xs = np.concatenate([np.asarray(g.coords)[:, 0] for g in red.geometry])
    assert np.abs(xs - 500_105).max() <= 5.0


def test_red_drenaje_umbral_imposible_falla_diciendo_el_maximo(valle):
    # guarda sobre la SALIDA: si sale vacia, el mensaje trae la cifra con la
    # que corregir el umbral, no un "no se encontro nada".
    flujo = valle
    grid, fdir, acc = flujo
    with pytest.raises(ValueError, match="acumulacion maxima"):
        geo.red_drenaje(flujo, umbral=acc.max() + 1)


def test_orden_cauces_numera_dentro_de_la_mascara_y_solo_ahi(valle, capsys):
    flujo = valle
    grid, fdir, acc = flujo
    orden = geo.orden_cauces(flujo, umbral=20)
    mascara = acc > 20
    assert orden.dtype == np.int32
    # la contencion que SI se cumple, y es de un solo sentido: todo lo numerado
    # esta en la mascara, pero no todo lo de la mascara sale numerado.
    assert (orden[~mascara] == 0).all()
    assert orden.max() >= 2  # hay confluencia: Strahler sube de 1 a 2
    assert "celdas numeradas de" in capsys.readouterr().out


def test_orden_cauces_avisa_de_las_celdas_de_mascara_sin_numerar(valle, capsys):
    # el reciproco NO se cumple, y es el aviso: en el valle en V media ladera
    # baja pasa el umbral sin estar sobre un tramo trazado. Se queda en 0, que
    # es el mismo valor que el fuera de cauce, asi que hay que decirlo.
    flujo = valle
    grid, fdir, acc = flujo
    orden = geo.orden_cauces(flujo, umbral=20)
    mascara = acc > 20
    assert (mascara & (orden == 0)).any()
    assert "NO estan sobre un tramo trazado" in capsys.readouterr().out


def test_delimitar_cuenca_ajusta_el_punto_al_cauce_y_lo_reporta(valle, capsys):
    # el punto va a proposito FUERA del cauce, en la ladera. Sin ajuste la
    # cuenca seria la de esa celda suelta; con ajuste, la del cauce.
    flujo = valle
    grid, fdir, acc = flujo
    fuera = gpd.GeoDataFrame(geometry=[Point(500_255, 1_999_905)], crs=UTM)
    cuenca = geo.delimitar_cuenca(flujo, fuera, umbral=20)
    salida = capsys.readouterr().out

    assert "desague movido" in salida
    assert "NO caia sobre cauce" in salida  # el aviso, no solo la cifra
    assert cuenca.geometry.iloc[0].geom_type in ("Polygon", "MultiPolygon")
    assert len(cuenca) == 1  # una fila: la union, no las piezas


def test_delimitar_cuenca_sobre_el_cauce_no_avisa(valle, capsys):
    flujo = valle
    grid, fdir, acc = flujo
    sobre = gpd.GeoDataFrame(geometry=[Point(500_105, 1_999_905)], crs=UTM)
    geo.delimitar_cuenca(flujo, sobre, umbral=20)
    assert "NO caia sobre cauce" not in capsys.readouterr().out


def test_delimitar_cuenca_reproyecta_el_punto(valle):
    # el punto llega en geograficas, como sale de un GPS. Tiene que dar la
    # misma cuenca que en UTM, no reventar ni delinear en el sitio equivocado.
    flujo = valle
    grid, fdir, acc = flujo
    utm = gpd.GeoDataFrame(geometry=[Point(500_105, 1_999_905)], crs=UTM)
    en_geo = utm.to_crs(4326)
    a = geo.delimitar_cuenca(flujo, utm, umbral=20)
    b = geo.delimitar_cuenca(flujo, en_geo, umbral=20)
    assert a.area.sum() == pytest.approx(b.area.sum())


def test_delimitar_cuenca_acepta_una_ZONA_y_mide_su_desague(valle, capsys):
    """Con poligono significa "el desague de esto", que es el caso normal.

    La zona tiene que dar la MISMA cuenca que el punto equivalente, o no seria la
    misma operacion escrita de otra forma.
    """
    flujo = valle
    grid, fdir, acc = flujo
    zona = gpd.GeoDataFrame(
        geometry=[box(500_000, 1_999_800, 500_300, 2_000_000)], crs=UTM
    )

    por_zona = geo.delimitar_cuenca(flujo, zona, umbral=20)
    salida = capsys.readouterr().out
    assert "desague MEDIDO en la zona" in salida
    assert "mayor acumulacion" in salida

    # la celda de mayor acumulacion de la zona, a mano, tiene que dar lo mismo
    tf = fdir.viewfinder.affine
    dentro = geo.rasterizar(zona, np.asarray(acc).shape, tf).astype(bool)
    f, c = np.unravel_index(
        int(np.where(dentro, np.asarray(acc), -1.0).argmax()), acc.shape
    )
    punto = gpd.GeoDataFrame(geometry=[Point(tf * (c + 0.5, f + 0.5))], crs=UTM)
    por_punto = geo.delimitar_cuenca(flujo, punto, umbral=20)

    assert por_zona.area.sum() == pytest.approx(por_punto.area.sum())


def test_delimitar_cuenca_avisa_si_la_zona_tiene_dos_salidas(tmp_path, capsys):
    """El techo escrito: una zona que drena por dos sitios devuelve la mayor.

    Salio en la primera corrida real (en un predio real: cuenca 737.82 ha de un
    predio de 1765.15 ha). Sin el aviso es un poligono plausible de media
    finca, que es justo el modo de fallo caro de este repo.

    Un LOMO (V invertida) drena a los dos lados, asi que una zona que lo cubre
    entero tiene dos desagues y el argmax elige uno.
    """
    columnas, filas, cumbre = 21, 20, 10
    j = -np.abs(np.arange(columnas) - cumbre) * 2.0  # lomo: baja a los dos lados
    z = 100.0 - np.arange(filas)[:, None] * 1.0 + j[None, :]
    grid, dem = geo.acondicionar_mde(_a_disco(z, tmp_path))
    flujo = geo.acumulacion_flujo(grid, dem)
    grid, fdir, acc = flujo

    todo = gpd.GeoDataFrame(
        geometry=[box(499_000, 1_999_000, 501_000, 2_001_000)], crs=UTM
    )
    cuenca = geo.delimitar_cuenca(flujo, todo, umbral=5)
    salida = capsys.readouterr().out

    assert "no drena por un solo sitio" in salida, salida
    # y la cifra que lo sostiene: la cuenca es menos de media zona
    zona_ha = np.asarray(acc).size * 100 / 10_000
    assert cuenca.area.sum() / 10_000 < 0.5 * zona_ha


def test_delimitar_cuenca_zona_fuera_del_raster_falla_con_su_nombre(valle):
    flujo = valle
    grid, fdir, acc = flujo
    lejos = gpd.GeoDataFrame(geometry=[box(0, 0, 100, 100)], crs=UTM)
    with pytest.raises(ValueError, match="no toca ni una celda"):
        geo.delimitar_cuenca(flujo, lejos, umbral=20)


def test_delimitar_cuenca_exige_una_sola_geometria(valle):
    flujo = valle
    grid, fdir, acc = flujo
    dos = gpd.GeoDataFrame(
        geometry=[Point(500_105, 1_999_905), Point(500_105, 1_999_805)], crs=UTM
    )
    with pytest.raises(ValueError, match="UNA geometria"):
        geo.delimitar_cuenca(flujo, dos, umbral=20)


# ---- microcuencas: la particion de una zona por desague --------------------


def _lomo(columnas=21, filas=20, cumbre=10):
    """V invertida: drena a los dos lados, o sea una zona con DOS desagues."""
    j = -np.abs(np.arange(columnas) - cumbre) * 2.0
    return 100.0 - np.arange(filas)[:, None] * 1.0 + j[None, :]


@pytest.fixture
def lomo(tmp_path):
    grid, dem = geo.acondicionar_mde(_a_disco(_lomo(), tmp_path))
    return geo.acumulacion_flujo(grid, dem)


def test_microcuencas_parten_la_zona_EXACTO_y_sin_solape(lomo):
    """La identidad que justifica la funcion, comprobada exacta y no por %.

    Es lo que la via descartada NO cumple: las cuencas de los desagues (una
    llamada a `delimitar_cuenca` por desague) se solapan y dejan huecos, medido
    en un predio real en 6504 celdas solapadas y 1115.45 ha sin cubrir. La
    particion por desague cierra por construccion, asi que se exige exacta.
    """
    flujo = lomo
    grid, fdir, acc = flujo
    zona = gpd.GeoDataFrame(
        geometry=[box(499_000, 1_999_000, 501_000, 2_001_000)], crs=UTM
    )
    mc = geo.microcuencas(flujo, zona, umbral=5)

    tf = fdir.viewfinder.affine
    celdas = int(geo.rasterizar(zona, np.asarray(acc).shape, tf).sum())
    res = abs(tf.a)
    assert mc.area.sum() == pytest.approx(celdas * res**2)
    # sin solape: la union mide lo mismo que la suma, no menos
    assert unary_union(mc.geometry.values).area == pytest.approx(mc.area.sum())
    assert mc["ID_MICRO"].is_unique


def test_microcuencas_un_lomo_da_al_menos_dos(lomo):
    # el caso que existe para resolver: `delimitar_cuenca` sobre esta zona
    # devuelve UNA cuenca de media finca y lo avisa; aqui salen las dos.
    flujo = lomo
    grid, fdir, acc = flujo
    zona = gpd.GeoDataFrame(
        geometry=[box(499_000, 1_999_000, 501_000, 2_001_000)], crs=UTM
    )
    mc = geo.microcuencas(flujo, zona, umbral=5)
    assert len(mc) >= 2
    grandes = mc.sort_values("ACUM", ascending=False).head(2)
    # las dos vertientes del lomo, no una grande y una esquirla
    assert grandes.area.min() > 0.1 * grandes.area.max()


def test_microcuencas_reporta_el_cierre_y_el_reparto_por_cauce(lomo, capsys):
    flujo = lomo
    grid, fdir, acc = flujo
    zona = gpd.GeoDataFrame(
        geometry=[box(499_000, 1_999_000, 501_000, 2_001_000)], crs=UTM
    )
    geo.microcuencas(flujo, zona, umbral=5)
    salida = capsys.readouterr().out
    assert "cierre EXACTO" in salida, salida
    assert "sobre cauce" in salida, salida


def test_microcuencas_marca_el_desague_que_es_sumidero(lomo):
    """`ES_SUMIDER` distingue "sale de la zona" de "el flujo muere aqui".

    Es la otra cara del aviso de `acumulacion_flujo`, contada por microcuenca: en
    En un predio real son 13 desagues que se llevan 1063.38 ha, el 60 % del predio.
    Sin la columna, esas microcuencas parecen salidas normales.
    """
    from pysheds.sview import Raster

    flujo = lomo

    grid, fdir, acc = flujo
    d = np.asarray(fdir).copy()
    d[10, 10] = -2  # sumidero interior pinchado
    fdir_roto = Raster(d, fdir.viewfinder)
    flujo_roto = _acumular_fdir(grid, fdir_roto)
    zona = gpd.GeoDataFrame(
        geometry=[box(499_000, 1_999_000, 501_000, 2_001_000)], crs=UTM
    )
    mc = geo.microcuencas(flujo_roto, zona)
    tf = fdir.viewfinder.affine
    x, y = tf * (10 + 0.5, 10 + 0.5)
    fila = mc[(mc["DESAGUE_X"] == x) & (mc["DESAGUE_Y"] == y)]
    assert len(fila) == 1, mc[["DESAGUE_X", "DESAGUE_Y", "ES_SUMIDER"]]
    assert bool(fila["ES_SUMIDER"].iloc[0]) is True
    assert not mc.loc[mc["ID_MICRO"] != fila["ID_MICRO"].iloc[0], "ES_SUMIDER"].all()


def test_microcuencas_sin_umbral_no_trae_columna_de_cauce(lomo):
    # `umbral` es opcional a proposito: sin el no hay corte que declarar, y la
    # funcion no se inventa uno.
    flujo = lomo
    grid, fdir, acc = flujo
    zona = gpd.GeoDataFrame(
        geometry=[box(499_000, 1_999_000, 501_000, 2_001_000)], crs=UTM
    )
    mc = geo.microcuencas(flujo, zona)
    assert "EN_CAUCE" not in mc.columns


def test_microcuencas_reproyecta_la_zona(lomo):
    flujo = lomo
    grid, fdir, acc = flujo
    zona = gpd.GeoDataFrame(
        geometry=[box(499_000, 1_999_000, 501_000, 2_001_000)], crs=UTM
    ).to_crs("EPSG:4326")
    mc = geo.microcuencas(flujo, zona)
    assert mc.crs.to_epsg() == 32613
    assert len(mc) >= 2


def test_microcuencas_zona_fuera_del_raster_falla_con_su_nombre(lomo):
    flujo = lomo
    grid, fdir, acc = flujo
    lejos = gpd.GeoDataFrame(geometry=[box(0, 0, 100, 100)], crs=UTM)
    with pytest.raises(ValueError, match="no toca ni una celda"):
        geo.microcuencas(flujo, lejos)


# ---- quemar_cauces: el efecto sobre el flujo -------------------------------
#
# Lo que `quemar_cauces` hace por si sola (bajar la cota, dejar el trazo
# 8-conectado) esta en test_raster.py. Aqui se prueba PARA QUE se hace: que el
# agua siga el cauce conocido en vez de la superficie interpolada.
#
# El cauce quemado va CUESTA ABAJO a proposito. Un cauce transversal a la
# pendiente crea una depresion que `fill_depressions` vuelve a rellenar, y el
# test no mediria el desvio sino el acondicionamiento. Aqui la columna quemada
# baja 1 m por fila, asi que desagua y sobrevive al acondicionado.


def _plano_al_sur(filas=24, columnas=21):
    """Plano que baja al sur, SIN pendiente transversal.

    Sin quemar, cada columna drena sola y la acumulacion es la misma en todas:
    no hay ninguna concentracion natural que confunda el resultado.
    """
    return np.repeat((100.0 - np.arange(filas, dtype=float))[:, None], columnas, axis=1)


def _acumular(z, tmp_path, nombre="m.tif"):
    ruta = tmp_path / nombre
    tf = Affine(10, 0, 500_000, 0, -10, 2_000_000)
    geo.guardar_raster(z, ruta, tf, UTM)
    grid, dem = geo.acondicionar_mde(ruta)
    return geo.acumulacion_flujo(grid, dem).acumulacion


def test_quemar_cauces_desvia_el_flujo_al_cauce_conocido(tmp_path):
    z = _plano_al_sur()
    tf = Affine(10, 0, 500_000, 0, -10, 2_000_000)
    # cauce N-S por la columna 15 (x del centro = 500_000 + 15.5*10)
    x = 500_000 + 15.5 * 10
    cauce = gpd.GeoDataFrame(
        geometry=[LineString([(x, 2_000_000), (x, 2_000_000 - 24 * 10)])], crs=UTM
    )
    quemado, n = geo.quemar_cauces(z, cauce, tf, profundidad=5)
    assert n == 24  # una celda por fila: el cauce cruza el raster entero

    sin_quemar = _acumular(z, tmp_path, "sin.tif")
    con_quemar = _acumular(quemado, tmp_path, "con.tif")

    # sin quemar no hay concentracion: todas las columnas acumulan igual
    fila = sin_quemar[-2, 1:-1]
    assert fila.min() == fila.max()

    # quemado, el maximo cae EN la columna del cauce
    assert np.unravel_index(con_quemar.argmax(), con_quemar.shape)[1] == 15
    # y el cauce lleva mas agua que la misma celda sin quemar: el desvio existe
    assert con_quemar[-2, 15] > sin_quemar[-2, 15]


def test_quemar_cauces_no_crea_depresion_si_el_cauce_baja(tmp_path):
    # la razon de que el test de arriba sea valido: si el acondicionado tuviera
    # que rellenar el cauce, lo medido seria fill_depressions, no el desvio.
    z = _plano_al_sur()
    tf = Affine(10, 0, 500_000, 0, -10, 2_000_000)
    x = 500_000 + 15.5 * 10
    cauce = gpd.GeoDataFrame(
        geometry=[LineString([(x, 2_000_000), (x, 2_000_000 - 24 * 10)])], crs=UTM
    )
    quemado, _ = geo.quemar_cauces(z, cauce, tf, profundidad=5)
    ruta = tmp_path / "q.tif"
    geo.guardar_raster(quemado, ruta, tf, UTM)
    _, dem = geo.acondicionar_mde(ruta)
    # el lecho sale intacto: el acondicionado no subio ni un centimetro
    assert np.asarray(dem)[:, 15] == pytest.approx(quemado[:, 15])


def test_etiquetar_laderas_un_valle_solo_da_una_cuenca(tmp_path):
    # La trampa del nombre, y va con la cadena real para que no parezca teoria.
    # `etiquetar_laderas` cuenta LADERAS separadas por cauce, no cuencas de
    # drenaje: en un valle en V las dos vertientes se tocan por encima de la
    # cabecera, donde la acumulacion aun no llega al umbral, asi que sale UNA
    # region por mucho que el ojo vea dos vertientes. Para partirlas, el cauce
    # tiene que cruzar el raster de lado a lado.
    grid, dem = geo.acondicionar_mde(_a_disco(_valle_en_v(), tmp_path))
    acc = geo.acumulacion_flujo(grid, dem).acumulacion
    _, n = geo.etiquetar_laderas(acc, umbral=acc.max() / 2)
    assert n == 1


def test_derrame_es_lo_que_falta_para_desbordar_por_el_vecino_mas_bajo():
    # Verdad analitica, que es lo unico que hace de verdad `rellenar_sumideros`:
    # el resto es iterar. Los dos casos que importan son el hoyo profundo y el
    # residuo de milimetros, porque el segundo es el que se mide en produccion.
    from geophis.hidrologia import _derrame

    z = np.full((5, 5), 100.0)
    z[2, 2] = 90.0
    assert _derrame(z, 2, 2) == pytest.approx(10.0)
    # con un vecino a 90.002, el derrame es de 2 mm y no de 10 m: manda el mas
    # bajo DE LOS QUE ESTAN POR ENCIMA, no el maximo ni la media
    z[2, 3] = 90.002
    assert _derrame(z, 2, 2) == pytest.approx(0.002)
    # una celda sin ningun vecino por encima no tiene cota que subir: lo que le
    # falta es micro-pendiente, y devolver algo > 0 aqui inventaria terreno
    assert _derrame(np.zeros((3, 3)), 1, 1) == 0.0
    # y el borde no se sale del array
    assert _derrame(np.array([[5.0, 9.0], [9.0, 9.0]]), 0, 0) == pytest.approx(4.0)


def test_rellenar_sumideros_no_toca_nada_si_no_hay_sumidero_interior(tmp_path):
    # La rampa drena entera al este, o sea no hay nada que cerrar. Lo que se fija
    # es que la funcion NO invente una ronda: un MDE que sale tocado de aqui deja
    # de ser el interpolado, y eso cambia la procedencia de sus cifras.
    grid, dem = geo.acondicionar_mde(_a_disco(_rampa_al_este(), tmp_path))
    reparado, informe = geo.rellenar_sumideros(grid, dem)
    assert informe["celdas"] == 0 and informe["rondas"] == 0
    assert informe["convergio"] and informe["pits_iniciales"] == 0
    assert informe["subida_max_m"] == 0.0
    assert np.array_equal(np.asarray(reparado), np.asarray(dem))


def test_rellenar_sumideros_cierra_un_pit_inyectado_y_dice_cuanto_subio(tmp_path):
    # El pit se inyecta DESPUES de acondicionar, a proposito: los sumideros que
    # importan son justo los que sobreviven a `fill_depressions`, y un hoyo
    # puesto antes lo tapa pysheds solo (eso ya lo fija
    # test_direccion_flujo_tapa_el_hoyo_que_fill_pits_alcanza).
    from pysheds.sview import Raster

    grid, dem = geo.acondicionar_mde(_a_disco(_rampa_al_este(), tmp_path))
    z = np.asarray(dem, dtype=np.float64).copy()
    z[6, 6] -= 5.0
    roto = Raster(z, dem.viewfinder)

    dentro, _ = _sin_salida(geo.acumulacion_flujo(grid, roto).fdir)
    assert dentro[6, 6], "el pit inyectado tiene que verse como sumidero interior"

    reparado, informe = geo.rellenar_sumideros(grid, roto)
    assert informe["convergio"], informe
    assert informe["pits_finales"] == 0 and informe["pits_iniciales"] >= 1
    # subir hasta el derrame deja la celda IGUAL que su vecino, o sea fabrica un
    # llano, y un llano sin micro-pendiente es otro sumidero. Si el bucle deja de
    # cerrar cada ronda con `resolve_flats`, esto entra en ciclo limite y no
    # converge.
    assert informe["rondas"] <= 2, "sin cerrar la ronda esto no converge"
    # 4 m, no 5: la rampa baja 1 m por columna, asi que la celda pasa de 94 a 89
    # y su vecino mas bajo DE LOS QUE ESTAN POR ENCIMA es el del este, a 93.
    assert informe["subida_max_m"] == pytest.approx(4.0, abs=0.01)
    # y el MDE sale reparado, no igual
    assert np.asarray(reparado)[6, 6] > z[6, 6]
    # float64 de principio a fin: castear a float32 borra la micro-pendiente de
    # `resolve_flats` (1e-5 m contra un paso de 1.2e-4 m a cota 1620)
    assert np.asarray(reparado).dtype == np.float64


def test_altura_sobre_cauce_mide_con_el_mde_base_y_no_con_el_quemado(tmp_path):
    # Valle en V: cada celda baja en diagonal hacia el eje (2 m de lado + 1 m
    # hacia el sur = 3 m por columna), salvo la vecina del eje, que con el eje
    # quemado 5 m cae recto (2 m). HAND = 3 * |columna - eje| - 1, exacto. El
    # flujo sale del quemado: si las cotas tambien, el HAND subiria 5 m.
    z = _valle_en_v()
    tf = Affine(10.0, 0, 500_000, 0, -10.0, 2_000_000)
    x = 500_000 + 10 * 10 + 5  # centro de la columna del eje
    cauce = gpd.GeoDataFrame(geometry=[LineString([(x, 2_000_000), (x, 2_000_000 - 200)])], crs=UTM)
    quemado, _ = geo.quemar_cauces(z, cauce, tf, 5)
    grid, dem = geo.acondicionar_mde(_a_disco(quemado, tmp_path))
    hand = geo.altura_sobre_cauce(geo.acumulacion_flujo(grid, dem), z, cauce)
    d = np.abs(np.arange(21) - 10)
    esperado = np.where(d > 0, 3.0 * d - 1, 0.0)
    np.testing.assert_allclose(hand[5, 1:-1], esperado[1:-1])
    assert np.isnan(hand[5, 0])  # el borde drena fuera, no a un cauce


def test_quemar_cauces_da_pendiente_a_la_zanja_llana(capsys):
    # En chico: cauce sobre una meseta (el TIN a cota de curva), del borde
    # oeste a una punta interior. Quemado a secas queda una zanja horizontal que
    # `resolve_flats` puede no drenar; tiene que bajar hacia el borde, y una
    # zanja cerrada en medio del llano (sin salida) tiene que avisar.
    tf = Affine(10, 0, 500_000, 0, -10, 2_000_000)
    y = 2_000_000 - 55  # centro de la fila 5
    cauce = gpd.GeoDataFrame(geometry=[LineString([(500_000, y), (500_065, y)])], crs=UTM)
    quemado, n = geo.quemar_cauces(np.full((12, 12), 100.0), cauce, tf, 5)
    lecho = quemado[5, :7]
    assert n == 7 and (np.diff(lecho) > 0).all() and lecho[0] == 95.0 and lecho.max() < 95.01
    assert "sin salida" not in capsys.readouterr().out

    dentro = gpd.GeoDataFrame(geometry=[LineString([(500_035, y), (500_075, y)])], crs=UTM)
    geo.quemar_cauces(np.full((12, 12), 100.0), dentro, tf, 5)
    assert "5 celda(s) de cauce quemado en tramos sin salida" in capsys.readouterr().out


def test_rellenar_sumideros_avisa_si_sube_el_cauce(tmp_path, capsys):
    # rellenar sobre la zanja es des-quemar: en un predio real, 1,612 celdas de cauce
    # subidas ~5 m sin que nada lo dijera.
    from pysheds.sview import Raster

    grid, dem = geo.acondicionar_mde(_a_disco(_rampa_al_este(), tmp_path))
    z = np.asarray(dem, dtype=np.float64).copy()
    z[6, 6] -= 5.0
    y = 2_000_000 - 65  # centro de la fila 6
    cauce = gpd.GeoDataFrame(geometry=[LineString([(500_000, y), (500_120, y)])], crs=UTM)
    geo.rellenar_sumideros(grid, Raster(z, dem.viewfinder), cauces=cauce)
    assert "1 celda(s) de cauce subidas mas de 2 mm (hasta 4.000 m)" in capsys.readouterr().out


def test_quemar_cauces_avisa_del_lecho_por_encima_del_terreno(capsys):
    # cauce del borde oeste que cruza una loma de 10 m: para que el agua salga
    # al borde el lecho tiene que llenarse hasta la loma, 5 m sobre el terreno
    # de la punta interior. Eso es la linea subiendo cuesta arriba, y se avisa.
    tf = Affine(10, 0, 500_000, 0, -10, 2_000_000)
    z = np.full((12, 12), 100.0)
    z[:, 3] = 110.0
    y = 2_000_000 - 55
    cauce = gpd.GeoDataFrame(geometry=[LineString([(500_000, y), (500_065, y)])], crs=UTM)
    quemado, _ = geo.quemar_cauces(z, cauce, tf, 5)
    assert quemado[5, 6] > z[5, 6]
    out = capsys.readouterr().out
    assert "3 celda(s) de cauce quedan por ENCIMA del terreno" in out
    assert "x 500,065 y 1,999,945" in out
