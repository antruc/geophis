"""La tabla de atributos: seleccionar, resumir, unir, editar campos, capas y describir.

Cada prueba fija UNA cosa que, si se rompe, devuelve una capa con aspecto de
buena: una seleccion que duplica filas, un total sumado sobre una columna
obsoleta, un join que no casa nada.
"""

import geopandas as gpd
import pandas as pd
import pyproj
import pytest
from shapely.geometry import Point, Polygon, box

import geophis as geo

UTM = 32613


@pytest.fixture
def capa():
    """Tres cuadrados de 10 m: dos de clase A y uno de B, con SUP e ID."""
    return gpd.GeoDataFrame(
        {"CLASE": ["A", "B", "A"], "SUP": [1.0, 2.0, 3.0], "ID": [1, 2, 3]},
        geometry=[box(0, 0, 10, 10), box(20, 0, 30, 10), box(0, 20, 10, 30)],
        crs=UTM,
    )


def test_seleccionar_filtra_y_conserva_el_crs(capa):
    sel = geo.seleccionar_por_atributo(capa, "CLASE == 'A'")
    assert list(sel["ID"]) == [1, 3]
    assert sel.crs == capa.crs


def test_seleccionar_nombra_las_columnas_cuando_la_consulta_no_evalua(capa):
    """El error de pandas dice 'name X is not defined' y no lista las columnas.

    Con una capa ajena el nombre real esta a una linea de distancia, y sin el
    hay que abrirla en otro sitio para escribir la consulta.
    """
    with pytest.raises(ValueError, match="CLASE"):
        geo.seleccionar_por_atributo(capa, "CLASSE == 'A'")


def test_seleccionar_por_ubicacion_no_duplica_donde_el_join_si(capa):
    """La razon de que esta funcion exista y no se resuelva con `union_espacial`.

    Con DOS geometrias de `otro` tocando la misma entidad, el spatial join
    devuelve esa entidad dos veces (y cualquier suma posterior la cuenta doble);
    la seleccion la devuelve una.
    """
    dos = gpd.GeoDataFrame(geometry=[Point(2, 2), Point(8, 8)], crs=UTM)
    assert len(geo.union_espacial(capa, dos)) == 4  # 3 entidades, una duplicada
    sel = geo.seleccionar_por_ubicacion(capa, dos)
    assert list(sel["ID"]) == [1]


def test_seleccionar_por_ubicacion_con_indice_repetido():
    """Tras `concat` o `explode` sin reset el indice se repite, y seleccionar por
    ETIQUETA se llevaba tambien la fila que no cumple."""
    g = gpd.GeoDataFrame(
        {"ID": [1, 2]}, geometry=[Point(0, 0), Point(100, 100)], crs=UTM, index=[0, 0]
    )
    sel = geo.seleccionar_por_ubicacion(g, box(-1, -1, 1, 1))
    assert list(sel["ID"]) == [1]


def test_seleccionar_por_ubicacion_respeta_el_orden_del_predicado(capa):
    """`within` se lee gdf-dentro-de-otro, no al reves.

    El indice espacial aplica el predicado en el orden contrario, y un `within`
    invertido devuelve una capa plausible: aqui serian las 3 entidades en vez
    de la 1 que esta dentro del cuadro.
    """
    cuadro = gpd.GeoDataFrame(geometry=[box(-5, -5, 15, 15)], crs=UTM)
    sel = geo.seleccionar_por_ubicacion(capa, cuadro, predicado="within")
    assert list(sel["ID"]) == [1]


def test_seleccionar_por_ubicacion_con_distancia_dilata_la_otra_capa(capa):
    punto = gpd.GeoDataFrame(geometry=[Point(15, 5)], crs=UTM)
    assert len(geo.seleccionar_por_ubicacion(capa, punto)) == 0
    cerca = geo.seleccionar_por_ubicacion(capa, punto, distancia=6)
    assert set(cerca["ID"]) == {1, 2}  # los dos cuadrados a 5 m, no el de arriba


def test_resumir_da_n_y_la_suma_por_grupo(capa):
    tabla = geo.estadisticas_de_resumen(capa, por="CLASE", campos={"SUP": "sum"})
    fila = tabla.set_index("CLASE")
    assert list(fila.loc["A"]) == [2, 4.0]  # n y SUP_sum
    assert list(fila.loc["B"]) == [1, 2.0]


def test_resumir_sin_por_da_una_fila_con_el_total(capa):
    tabla = geo.estadisticas_de_resumen(capa, campos={"SUP": ["sum", "max"]})
    assert len(tabla) == 1
    assert tabla["n"][0] == 3
    assert tabla["SUP_sum"][0] == 6.0
    assert tabla["SUP_max"][0] == 3.0


def test_resumir_avisa_de_una_superficie_obsoleta(capa, capsys):
    """Sumar una columna de area que ya no cuadra es publicar una cifra muerta.

    Es el modo de fallo de `guardar`, y aqui es peor: de `estadisticas_de_resumen` sale la
    tabla que se cita.
    """
    capa = geo.calcular_superficie(capa, campo="superficie_ha")
    capa = geo.recortar(capa, box(0, 0, 5, 100))  # la mitad de la geometria
    geo.estadisticas_de_resumen(
        capa, por="CLASE", campos={"superficie_ha": "sum"}
    )
    assert "no cuadra con la" in capsys.readouterr().out


def test_resumir_no_agrupa_por_la_geometria(capa):
    with pytest.raises(ValueError, match="geometria"):
        geo.estadisticas_de_resumen(capa, por="geometry")


def test_unir_tabla_pega_las_columnas_por_la_clave(capa):
    tabla = pd.DataFrame({"ID": [1, 2, 3], "PROPIETARIO": ["x", "y", "z"]})
    unida = geo.unir_campo(capa, tabla, "ID")
    assert list(unida["PROPIETARIO"]) == ["x", "y", "z"]
    assert unida.crs == capa.crs


def test_unir_tabla_revienta_y_nombra_los_dos_tipos_si_no_casa_nada(capa):
    """El join silencioso es el defecto: devuelve la capa entera con nulos.

    Y la causa casi siempre es de tipo, asi que el mensaje tiene que dar los
    dos tipos; sin ellos se audita el contenido del CSV, que esta bien.

    OJO con el `match`: pedir solo 'int64' NO distingue este error del de
    pandas, que tambien lo dice y manda usar `pd.concat`. Se pide el texto de
    la libreria, que es el unico que dice que hacer.
    """
    tabla = pd.DataFrame({"ID": ["1", "2", "3"], "PROPIETARIO": ["x", "y", "z"]})
    with pytest.raises(ValueError, match="no caso ninguna fila") as e:
        geo.unir_campo(capa, tabla, "ID")
    texto = str(e.value)
    assert "tipo int64" in texto and "tipo str" in texto
    assert "astype(str)" in texto


def test_unir_tabla_da_su_mensaje_aunque_pandas_reviente_antes(capa):
    """La guarda vivia DESPUES del merge, o sea inalcanzable en el caso tipico.

    Con clave texto contra clave entera pandas no llega a unir: revienta con un
    ValueError propio que manda usar `pd.concat`, que aqui es el consejo
    equivocado. Medido contra cartografia real (PENDIENTES_ENTREGABLE.shp,
    gridcode int64 contra '001' del CSV): el mensaje que salia era el de pandas
    y el de la libreria no se ejecutaba nunca.
    """
    tabla = pd.DataFrame({"ID": ["001", "002"], "PROPIETARIO": ["x", "y"]})
    with pytest.raises(ValueError) as e:
        geo.unir_campo(capa, tabla, "ID")
    texto = str(e.value)
    assert "INCOMPATIBLES" in texto
    assert "ejemplos [1, 2, 3]" in texto  # la muestra del lado de la capa
    assert "'001'" in texto  # y la del lado de la tabla
    assert "pd.concat" not in texto


def test_unir_tabla_avisa_de_las_claves_repetidas_que_multiplican_filas(capa, capsys):
    tabla = pd.DataFrame({"ID": [1, 1, 2, 3], "DOC": ["a", "b", "c", "d"]})
    unida = geo.unir_campo(capa, tabla, "ID")
    assert len(unida) == 4  # la entidad 1 sale dos veces
    assert "claves repetidas" in capsys.readouterr().out


def test_listar_capas_lista_las_de_un_geopackage(capa, tmp_path):
    # con `to_file` y no con `guardar`: `guardar` no sabe nombrar capas, que es
    # justo por lo que un GPKG multicapa solo puede llegar de fuera
    ruta = tmp_path / "dos.gpkg"
    capa.to_file(ruta, layer="rodales")
    capa.to_file(ruta, layer="predio")
    assert sorted(geo.listar_capas(ruta)) == ["predio", "rodales"]


def test_describir_cuenta_nulos_vacias_y_las_unidades(capa):
    capa.loc[1, "CLASE"] = None
    ficha = geo.describir(capa)
    assert ficha["n"] == 3
    assert ficha["epsg"] == UTM
    assert ficha["unidades"] == "metre"
    assert ficha["nulos"]["CLASE"] == 1
    assert ficha["vacias"] == 0 and ficha["invalidas"] == 0
    assert ficha["extension"] == (0.0, 0.0, 30.0, 30.0)


def test_describir_separa_las_vacias_de_las_invalidas(capa):
    """Son dos poblaciones y se arreglan distinto.

    Una vacia se borra (`limpiar_vacias`) y una invalida se repara
    (`reparar_geometrias`). Y `is_valid` no las trata igual entre si: una nula
    da False y un poligono vacio da True, asi que un conteo que las mezcle sale
    mal en un sentido o en el otro segun cual llegue.
    """
    capa.loc[0, "geometry"] = Polygon()
    capa.loc[1, "geometry"] = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])  # pajarita
    ficha = geo.describir(capa)
    assert ficha["vacias"] == 1
    assert ficha["invalidas"] == 1


def test_describir_avisa_de_un_crs_geografico_y_no_solo_lo_imprime(capa, capsys):
    """El aviso temprano es la razon de que `unidades` este en la ficha.

    El docstring dice que el aviso por funcion llega tarde; imprimir la palabra
    'degree' entre otros diez campos no lo adelanta. Medido con un predio real
    en EPSG:4326, que es como llega la cartografia de fuera.
    """
    geo.describir(capa.to_crs(4326))
    salida = capsys.readouterr().out
    assert "Aviso" in salida and "GEOGRAFICO" in salida
    # y con CRS metrico NO grita: una guarda que salta siempre no es guarda
    geo.describir(capa)
    assert "GEOGRAFICO" not in capsys.readouterr().out


def test_describir_no_escupe_el_wkt_cuando_el_crs_no_tiene_codigo_epsg(capsys):
    """La hidrografia estatal (Lambert ITRF92) llega asi, en WKT suelto.

    Son ~700 caracteres en una linea, y entierran el unico dato accionable, que
    es que NO hay codigo. El WKT sigue entero en ficha['crs'].
    """
    wkt = (
        'PROJCS["Lambert_Conformal_Conic",GEOGCS["WGS 84",DATUM["WGS_1984",'
        'SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],'
        'UNIT["Degree",0.0174532925199433]],'
        'PROJECTION["Lambert_Conformal_Conic_2SP"],'
        'PARAMETER["latitude_of_origin",12],PARAMETER["central_meridian",-102],'
        'PARAMETER["standard_parallel_1",17.5],'
        'PARAMETER["standard_parallel_2",29.5],'
        'PARAMETER["false_easting",2500000],PARAMETER["false_northing",0],'
        'UNIT["metre",1]]'
    )
    capa = gpd.GeoDataFrame(
        {"ID": [1]}, geometry=[box(0, 0, 10, 10)], crs=pyproj.CRS.from_wkt(wkt)
    )
    ficha = geo.describir(capa)
    linea = next(x for x in capsys.readouterr().out.splitlines() if "CRS " in x)
    assert "SIN codigo EPSG" in linea
    assert "PROJCS" not in linea and len(linea) < 120
    assert ficha["epsg"] is None
    assert "PROJCS" in ficha["crs"]  # el WKT entero sigue disponible


@pytest.fixture
def texto():
    return gpd.GeoDataFrame(
        {"clase": ["conservación", "a"], "SUP": [1.5, 2.0], "nota": [None, "x"]},
        geometry=[box(0, 0, 10, 10), box(20, 0, 30, 10)],
        crs=UTM,
    )


def test_atributos_a_mayusculas_cambia_nombres_y_texto_sin_tocar_lo_demas(texto):
    antes = texto.copy()
    salida = geo.atributos_a_mayusculas(texto)
    assert list(salida.columns) == ["CLASE", "SUP", "NOTA", "geometry"]
    assert salida["CLASE"].iloc[0] == "CONSERVACIÓN"
    assert salida["SUP"].iloc[0] == 1.5
    assert pd.isna(salida["NOTA"].iloc[0])
    assert salida.geometry.equals(texto.geometry)
    assert salida.crs == texto.crs
    pd.testing.assert_frame_equal(texto, antes)


def test_atributos_conserva_la_geometria_con_otro_nombre(texto):
    salida = geo.atributos_a_mayusculas(texto.rename_geometry("geom"))
    assert salida.geometry.name == "geom"
    assert salida.crs == texto.crs


def test_atributos_convierte_el_tipo_string_de_pandas(texto):
    texto["clase"] = texto["clase"].astype("string")
    assert geo.atributos_a_mayusculas(texto)["CLASE"].iloc[1] == "A"


def test_atributos_falla_si_dos_nombres_chocan(texto):
    texto["sup"] = 0.0
    with pytest.raises(ValueError, match=r"'SUP'.*'sup'"):
        geo.atributos_a_mayusculas(texto)


def test_atributos_a_minusculas_es_el_espejo(texto):
    salida = geo.atributos_a_minusculas(geo.atributos_a_mayusculas(texto))
    assert list(salida.columns) == ["clase", "sup", "nota", "geometry"]
    assert salida["clase"].iloc[0] == "conservación"
    assert pd.isna(salida["nota"].iloc[0])


def test_atributos_falla_si_un_campo_choca_con_la_geometria(texto):
    texto["GEOMETRY"] = "x"
    with pytest.raises(ValueError, match=r"'geometry'.*'GEOMETRY'"):
        geo.atributos_a_minusculas(texto)


# --- editar campos -----------------------------------------------------------


def test_agregar_campo_falla_si_ya_existe_y_manda_a_calcular_campo(capa):
    with pytest.raises(ValueError, match="calcular_campo"):
        geo.agregar_campo(capa, "SUP", 0)


def test_agregar_campo_con_lista_asigna_por_posicion(capa):
    c = geo.agregar_campo(capa.set_index("ID"), "N", [10, 20, 30])
    assert list(c["N"]) == [10, 20, 30]


def test_calcular_campo_con_texto_o_funcion_y_avisa_al_sobrescribir(capa, capsys):
    c = geo.calcular_campo(capa, "SUP2", "SUP * 2")
    assert list(c["SUP2"]) == [2.0, 4.0, 6.0]
    c = geo.calcular_campo(c, "SUP2", lambda f: f.CLASE + str(f.ID))
    assert list(c["SUP2"]) == ["A1", "B2", "A3"]
    assert "sobrescribe 'SUP2'" in capsys.readouterr().out


def test_renombrar_campos_falla_con_nombre_viejo_mal_escrito(capa):
    """`rename` de pandas lo ignora y el script sigue con el nombre de antes."""
    with pytest.raises(ValueError, match="CLASSE"):
        geo.renombrar_campos(capa, {"CLASSE": "TIPO"})
    with pytest.raises(ValueError, match="repetidas"):
        geo.renombrar_campos(capa, {"CLASE": "SUP"})


def test_borrar_conservar_y_ordenar_campos(capa):
    assert list(geo.eliminar_campos(capa, "SUP").columns) == [
        "CLASE",
        "ID",
        "geometry",
    ]
    assert list(geo.conservar_campos(capa, ["ID", "CLASE"]).columns) == [
        "ID",
        "CLASE",
        "geometry",
    ]
    assert list(geo.ordenar_campos(capa, "ID").columns) == [
        "ID",
        "CLASE",
        "SUP",
        "geometry",
    ]
    with pytest.raises(ValueError, match="geometria"):
        geo.eliminar_campos(capa, "geometry")


def test_cambiar_tipo_falla_si_un_valor_no_convierte(capa):
    """`to_numeric(errors='coerce')` volveria nulo el 'S/D' sin decir nada."""
    capa["COD"] = ["1", "S/D", "3"]
    with pytest.raises(ValueError, match="S/D"):
        geo.cambiar_tipo(capa, "COD", "entero")
    c = geo.cambiar_tipo(capa, "COD", "entero", forzar=True)
    assert c["COD"].isna().sum() == 1 and str(c["COD"].dtype) == "Int64"


def test_cambiar_tipo_a_entero_no_trunca_decimales(capa):
    capa["SUP"] = [1.0, 2.7, 3.0]
    with pytest.raises(ValueError, match="trunca"):
        geo.cambiar_tipo(capa, "SUP", "entero")


def test_reemplazar_valores_conserva_lo_no_listado_y_falla_si_no_casa_nada(capa):
    c = geo.reemplazar_valores(capa, "ID", {1: 100})
    assert list(c["ID"]) == [100, 2, 3]  # Series.map dejaria 2 y 3 en nulo
    with pytest.raises(ValueError, match="1 no es '1'"):
        geo.reemplazar_valores(capa, "ID", {"1": 100})


def test_ordenar_filas_renumera_el_indice(capa):
    c = geo.ordenar_filas(capa, "SUP", descendente=True)
    assert list(c["ID"]) == [3, 2, 1] and list(c.index) == [0, 1, 2]


def test_crear_capa_exige_crs_y_tipa_una_capa_vacia():
    with pytest.raises(ValueError, match="crs"):
        geo.crear_capa([box(0, 0, 1, 1)], None)
    vacia = geo.crear_capa(
        [],
        UTM,
        {"CLASE": [], "SUP": []},
        tipos={"CLASE": "texto", "SUP": "decimal"},
    )
    assert len(vacia) == 0 and vacia.crs == UTM
    assert str(vacia["SUP"].dtype) == "float64"


def test_guardar_con_anchos_fija_el_dbf_y_no_corta_texto(capa, tmp_path):
    """pyogrio no fija anchos; `guardar` reescribe el .dbf y `listar_campos` lo lee."""
    capa["CLASE"] = ["A", "BÉ", "A"]  # É = 2 bytes en UTF-8
    ruta = tmp_path / "x.shp"
    geo.guardar(capa, ruta, anchos={"CLASE": 3, "SUP": (8, 2)})
    campos = geo.listar_campos(ruta).set_index("campo")
    assert (campos.loc["CLASE", "ancho"], campos.loc["SUP", "ancho"]) == (3, 8)
    assert campos.loc["SUP", "decimales"] == 2
    assert campos.loc["ID", "ancho"] == 18  # el que no se nombra queda como GDAL
    leida = geo.cargar(ruta)
    assert list(leida["CLASE"]) == ["A", "BÉ", "A"]
    assert list(leida["SUP"]) == [1.0, 2.0, 3.0]
    with pytest.raises(ValueError, match="BYTES"):
        geo.guardar(capa, tmp_path / "y.shp", anchos={"CLASE": 2})
    assert not (tmp_path / "y.shp").exists()  # nada a medias en la ruta
    with pytest.raises(ValueError, match="solo aplica a .shp"):
        geo.guardar(capa, tmp_path / "x.gpkg", anchos={"CLASE": 3})
