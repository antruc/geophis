"""Operaciones geométricas: recortar, disolver, zona_de_influencia, fusionar, etc."""

import re
from collections.abc import Iterable
from typing import Any

import geopandas as gpd
import numpy as np
import numpy.typing as npt
import pandas as pd
import shapely
from pyproj import Geod
from rasterio import Affine
from rasterio.transform import from_origin
from scipy.ndimage import distance_transform_edt
from scipy.sparse import csr_array
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree
from shapely.geometry import box, LineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely import voronoi_polygons
from shapely.errors import GEOSException
from shapely.ops import unary_union, split

from .metrologia import _de_parametros
from .proyeccion import (
    _UNIDADES_AREA,
    _UNIDADES_LONGITUD,
    _a_crs,
    _exigir_crs,
    _exigir_proyectado,
)
from .raster import poligonizar, rasterizar
from ._salida import _mostrar

# Decimales con que se reportan superficies y longitudes. 2 basta para el uso
# forestal/catastral (0.01 ha = 100 m²) y evita el falso detalle de 4.
_DECIMALES_AREA = 2


def nombre_campo_libre(gdf: gpd.GeoDataFrame, campo: str) -> str:
    """Devuelve un nombre de campo que no choque con los existentes.

    Si `campo` no existe en la capa, lo devuelve tal cual.
    Si ya existe, devuelve la primera variante libre (campo_1, campo_2...) para
    no pisar datos. Determinista: la misma capa da siempre el mismo nombre.
    El cambio de nombre se avisa siempre, con `geo.MOSTRAR` o sin él: quien
    busque la columna por su nombre de siempre no la encontraría.
    """
    if campo not in gdf.columns:
        return campo
    i = 1
    while f"{campo}_{i}" in gdf.columns:
        i += 1
    nuevo = f"{campo}_{i}"
    print(f"Aviso: '{campo}' ya existe, uso '{nuevo}'")
    return nuevo


def _alinear(gdf: gpd.GeoDataFrame, otro: Any, nombre: str = "otro") -> Any:
    """Exige CRS a las dos capas y devuelve `otro` en el CRS de `gdf`.

    Una geometría shapely suelta pasa tal cual: no lleva CRS que comprobar y se
    asume en el de `gdf`.
    """
    _exigir_crs(gdf, "gdf")
    return _a_crs(otro, gdf.crs, nombre) if hasattr(otro, "crs") else otro


def recortar(
    gdf: gpd.GeoDataFrame, mascara: gpd.GeoDataFrame | BaseGeometry
) -> gpd.GeoDataFrame:
    """Recorta `gdf` con la geometría de `mascara` (clip).

    `mascara` puede ser otro GeoDataFrame. Si tiene distinto CRS se
    reproyecta al de `gdf` automáticamente antes de recortar.

    `keep_geom_type=True`: el clip puede generar puntos/líneas donde el borde
    de la máscara solo toca un vértice de un polígono; el flag los descarta y
    conserva solo el tipo original. Sin él, guardar a shapefile (tipo único)
    truena con FeatureError al escribir esas esquirlas.
    """
    mascara = _alinear(gdf, mascara, "mascara")
    return gpd.clip(gdf, mascara, keep_geom_type=True)


def disolver(
    gdf: gpd.GeoDataFrame, campo: str | None = None, valor: Any = None
) -> gpd.GeoDataFrame:
    """Une todas las geometrías de la capa en una sola (dissolve total).

    Parámetros
    ----------
    campo, valor : opcionales
        Si se indican, la capa resultante lleva esa columna con ese valor
        (útil para etiquetar el resultado, p.ej. campo='CONDICION',
        valor='PERENNE').

    Devuelve
    --------
    GeoDataFrame con una única entidad. De una capa VACÍA sale igual una fila,
    con la geometría vacía, y avisa por pantalla: parece válida y no revienta
    hasta el resumen final, o nunca.
    """
    # ponytail: aviso, no excepción ni 0 filas. Devolver 0 filas haría que los
    # `if len(...) == 0` de aguas abajo funcionaran solos, pero rompe a quien
    # espere una fila siempre.
    if len(gdf) == 0:
        print("Aviso: disolver() de una capa vacía; sale una fila con geometría vacía")
    datos: dict[str, list[Any]] = {"geometry": [unary_union(gdf.geometry)]}
    if campo is not None:
        datos[campo] = [valor]
    return gpd.GeoDataFrame(datos, crs=gdf.crs)


def disolver_por_grupo(gdf: gpd.GeoDataFrame, campo: str) -> gpd.GeoDataFrame:
    """Fusiona las geometrías en un polígono por cada valor único de `campo`.

    Es disolver por el campo de clase (p. ej. `gridcode`): agrupa por `campo` y une
    (dissolve) las geometrías de cada grupo. A diferencia de `disolver` (que
    fusiona toda la capa en una sola entidad), aquí `campo` agrupa, no solo
    etiqueta. Los demás atributos toman el primer valor de cada grupo.
    Conserva el CRS. `campo` queda como columna, no como índice.
    """
    if campo not in gdf.columns:
        raise ValueError(f"la capa no tiene el campo '{campo}'")
    return gdf.dissolve(by=campo, as_index=False)


def separar_multipartes(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Parte cada entidad multiparte en una fila por pieza (multipart to singlepart).

    La INVERSA de `disolver_por_grupo`, que deja una fila multiparte por clase.
    Después de disolver, la capa tiene una entidad por valor de campo y sus
    piezas sueltas ya no se pueden contar, medir ni indexar por separado: eso es
    lo que devuelve esta función. Los atributos se copian a cada pieza.

    Va casi siempre pegada a una medición:

        alta = disolver_por_grupo(gdf, "gridcode")
        alta = separar_multipartes(alta[alta["clase"] == ">100"])
        alta = calcular_superficie(alta, campo="AREA_HA")   # ahora por PIEZA

    Los dos argumentos que envuelve no son adorno, son la trampa:
    `index_parts=False` evita la columna de índice de parte, y
    `reset_index(drop=True)` deshace el MultiIndex que `explode` deja puesto.
    Sin los dos, el `iloc[[k]]` o el `.loc` de la línea siguiente se comporta de
    otra forma, y ese es justo el patrón que sigue (elegir la pieza mayor,
    recorrerlas por índice).

    NO recalcula áreas: `calcular_superficie` es la otra mitad y se llama
    aparte, porque el área va detrás del ÚLTIMO paso que mueva la geometría y
    esta función no sabe si es el último.

    Conserva el CRS y el nombre de la columna de geometría.
    """
    return gdf.explode(index_parts=False).reset_index(drop=True)


def _avisar_geografico(gdf: gpd.GeoDataFrame, que: str) -> None:
    """Aviso (no error) para funciones que documentan "unidades del CRS"."""
    if gdf.crs is not None and gdf.crs.is_geographic:
        print(f"Aviso: CRS geográfico (grados); {que}. Usa UTM.")


def zona_de_influencia(gdf: gpd.GeoDataFrame, distancia: float) -> gpd.GeoDataFrame:
    """Zona de influencia (buffer) alrededor de cada geometría.

    La distancia va en las unidades del CRS (metros si es UTM).
    Devuelve una copia; no modifica la capa original.
    """
    _avisar_geografico(gdf, "la distancia se aplicará en grados")
    salida = gdf.copy()
    # `salida.geometry.name` y no el literal "geometry": una capa cuya columna
    # activa se llame distinto (GeoPackage con `geom`, `rename_geometry`) se
    # llevaria una columna NUEVA llamada "geometry" con el buffer, y la activa
    # intacta. La capa parece buffereada y `superficie_total_ha` devuelve el area sin
    # buffer, sin que nada avise. Mismo motivo en todo el modulo.
    salida[salida.geometry.name] = salida.geometry.buffer(distancia)
    return salida


def _dimension_dominante(geom: BaseGeometry) -> BaseGeometry:
    """De una GeometryCollection deja solo las partes de mayor dimensión.

    Un `difference` entre polígonos que comparten borde devuelve el área que
    queda MÁS los tramos de línea sueltos donde los bordes se rozaban. Esa
    mezcla no se puede guardar en un shapefile (tipo único) y hace que `clip`
    ya no pueda aplicar `keep_geom_type`, así que el error salta hasta el
    `guardar`, lejos de donde se produjo. Aquí se corta de raíz: se conserva la
    dimensión mayor presente (área sobre línea sobre punto), que es la parte
    con significado; el resto son esquirlas de contacto, no de solape.
    """
    if geom.is_empty or geom.geom_type != "GeometryCollection":
        return geom
    for tipos in (("Polygon", "MultiPolygon"), ("LineString", "MultiLineString")):
        partes = [g for g in geom.geoms if g.geom_type in tipos]
        if partes:
            return unary_union(partes)
    return geom


def borrar(
    gdf: gpd.GeoDataFrame, otro: gpd.GeoDataFrame | BaseGeometry
) -> gpd.GeoDataFrame:
    """Borrar (erase): quita de `gdf` lo que se solape con `otro`.

    Sirve para dar prioridad a una capa sobre otra: lo que pisa `otro`
    se elimina de `gdf`. Las geometrías que quedan vacías se quitan.
    Si `otro` tiene distinto CRS se reproyecta al de `gdf`, igual que `recortar`.

    Cuando el recorte deja una mezcla de área y línea (bordes que se rozan), se
    queda solo con la de mayor dimensión: tocarse no es solaparse, y esas
    esquirlas revientan al guardar a shapefile.
    """
    otro = _alinear(gdf, otro)
    recorte = unary_union(otro.geometry) if hasattr(otro, "geometry") else otro
    salida = gdf.copy()
    salida[salida.geometry.name] = salida.geometry.difference(recorte).apply(
        _dimension_dominante
    )
    return limpiar_vacias(salida)


def fusionar(capas: Iterable[gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    """Fusionar (merge): junta varias capas en una sola, concatenando entidades.

    No funde geometrías; para eso está `disolver`.

    `capas` es una lista de GeoDataFrames. Toma el CRS y el nombre de la columna
    de geometría de la primera.

    Comprueba las dos cosas que tienen que coincidir, no solo el CRS. Con
    nombres de columna de geometría distintos ('geometry' y 'geom'), `concat`
    no falla: alinea por nombre y saca una capa con DOS columnas de geometría,
    cada una medio llena de nulos. La mitad de las entidades queda con la
    geometría en la columna que no es la activa, así que `superficie_total_ha`
    devuelve la de solo media capa y nada avisa. Es el mismo modo de fallo que
    el literal "geometry" del resto del módulo, una capa más arriba.
    """
    capas = list(capas)
    if not capas:
        raise ValueError("fusionar recibió una lista vacía")
    if any(c.crs != capas[0].crs for c in capas[1:]):
        raise ValueError("las capas tienen CRS distintos; reproyecta antes de fusionar")
    col_geom = capas[0].geometry.name
    distintas = {c.geometry.name for c in capas[1:]} - {col_geom}
    if distintas:
        raise ValueError(
            f"las capas no tienen la misma columna de geometría: '{col_geom}' "
            f"contra {sorted(distintas)}. `concat` las alinearía por nombre y "
            f"sacaría dos columnas de geometría medio vacías, sin avisar. "
            f"Renombra antes: `capa = capa.rename_geometry('{col_geom}')`"
        )
    return gpd.GeoDataFrame(
        pd.concat(capas, ignore_index=True),
        geometry=col_geom,
        crs=capas[0].crs,
    )


def resolver_por_prioridad(capas: Iterable[gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    """Resuelve los solapes por prioridad y devuelve una sola capa sin encimados.

    El orden de la lista ES la prioridad, de mayor a menor: la primera capa se
    queda entera y cada siguiente pierde lo que pise de las anteriores.

    Cada capa se recorta contra la UNIÓN de TODAS las anteriores, no solo contra
    la inmediata superior. Con tres o más capas eso importa: recortando solo
    contra la vecina, la tercera conserva su solape con la primera allí donde la
    segunda no cubre (una brecha silenciosa que aparece justo en los cruces).

    Conserva los atributos de cada capa (`fusionar` las concatena), así que
    todas deben venir en el mismo CRS.
    """
    capas = list(capas)
    if not capas:
        raise ValueError("resolver_por_prioridad recibió una lista vacía")
    resultado = [capas[0]]
    acumulado = unary_union(capas[0].geometry)
    for capa in capas[1:]:
        libre = borrar(capa, acumulado)
        resultado.append(libre)
        acumulado = acumulado.union(unary_union(libre.geometry))
    return fusionar(resultado)


def _capa_vacia(crs: Any) -> gpd.GeoDataFrame:
    """Capa sin entidades con el esquema de salida de repartir_por_cercania."""
    return gpd.GeoDataFrame({"gana": []}, geometry=[], crs=crs)


def _trozos(
    mascara: npt.NDArray[Any], transform: Affine, crs: Any, nombre: str
) -> gpd.GeoDataFrame:
    """Poligoniza los píxeles ganados por una capa y los etiqueta con su nombre."""
    if not mascara.any():
        return _capa_vacia(crs)
    ganados = poligonizar(mascara.astype("uint8"), transform, crs, mascara=mascara)
    return ganados[[ganados.geometry.name]].assign(gana=nombre)


def _distancia_a(
    nucleo: BaseGeometry, forma: tuple[int, int], transform: Affine, crs: Any
) -> npt.NDArray[Any]:
    """Distancia de cada celda al núcleo, en píxeles. Sin núcleo -> infinito.

    Sale en píxeles, no en metros: las dos distancias solo se comparan entre sí
    sobre la misma malla cuadrada, así que el factor de escala se cancela.
    """
    if nucleo.is_empty:
        # capa contenida por completo en la otra: sin núcleo, pierde todo
        return np.full(forma, np.inf)
    ras = rasterizar(gpd.GeoDataFrame(geometry=[nucleo], crs=crs), forma, transform)
    if not ras.any():
        # núcleo más chico que un píxel: no aterrizó en la malla. Sin este corte
        # distance_transform_edt no tendría a qué medir y devolvería basura.
        return np.full(forma, np.inf)
    return distance_transform_edt(ras == 0)


def repartir_por_cercania(
    gdf_a: gpd.GeoDataFrame,
    gdf_b: gpd.GeoDataFrame,
    resolucion: float = 1.0,
    nombres: tuple[str, str] = ("A", "B"),
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Reparte el solape entre dos capas: cada trozo va a la capa más cercana.

    Para capas que en teoría no deberían solaparse y no tienen jerarquía entre
    sí. En vez de que una gane entera (`borrar`, `resolver_por_prioridad`),
    la zona en conflicto se parte y cada pedazo se le asigna a la capa cuyo
    NÚCLEO (su parte no conflictiva) esté más cerca.

    Es una asignación euclidiana (cada celda a la fuente más cercana)
    con dos matices: las fuentes son solo dos (los dos núcleos) y la asignación
    se aplica solo dentro del solape, no a todo el ráster. El resultado equivale
    a partir la zona en conflicto con polígonos de Thiessen respecto a los dos
    núcleos; la malla temporal (`resolucion`) es lo que aproxima ese borde.

    Ambas capas deben traer una sola entidad (disuélvelas antes): con varias no
    hay a qué fila asignarle lo ganado.

    Parámetros
    ----------
    resolucion : lado del píxel de la malla temporal, en unidades del CRS. Es la
        precisión del reparto: 1.0 = el borde entre capas queda definido al metro.
        Bajarlo afina el borde y encarece el cálculo al cuadrado.
    nombres : cómo se llaman las dos capas en la columna 'gana' de la salida.

    Tope de la malla: 20 millones de píxeles. Si la zona de conflicto no cabe,
    falla pidiendo subir `resolucion` en vez de agotarse la memoria.

    Devuelve (gdf_a, gdf_b, conflicto). `conflicto` es la zona repartida con la
    columna 'gana' diciendo a quién le tocó cada pedazo: guárdala para revisar el
    reparto a ojo. Sin solape sale vacía y las capas salen intactas.

    En empate exacto gana `gdf_a`, así que el orden de los argumentos sigue
    siendo una jerarquía; solo que es la que casi nunca se aplica.
    """
    gdf_b = _alinear(gdf_a, gdf_b, "gdf_b")
    if len(gdf_a) != 1 or len(gdf_b) != 1:
        raise ValueError(
            f"repartir_por_cercania espera una entidad por capa (recibió "
            f"{len(gdf_a)} y {len(gdf_b)}); disuélvelas antes con disolver()"
        )
    if resolucion <= 0:
        raise ValueError(f"resolucion {resolucion} debe ser > 0")

    crs = gdf_a.crs

    # overlay descarta los contactos de borde que salen como línea o punto:
    # tocarse no es solaparse
    zona = unary_union(intersecar(gdf_a, gdf_b).geometry)
    if zona.is_empty or zona.area == 0:
        return gdf_a, gdf_b, _capa_vacia(crs)

    poly_a = unary_union(gdf_a.geometry)
    poly_b = unary_union(gdf_b.geometry)
    # mismo cuidado que en borrar: el difference puede dejar tramos de
    # línea sueltos, y un núcleo con líneas ensucia el raster y la salida
    nucleo_a = _dimension_dominante(poly_a.difference(zona))
    nucleo_b = _dimension_dominante(poly_b.difference(zona))

    # margen: aire alrededor del conflicto para que el borde de la malla no
    # recorte el núcleo que sirve de referencia de distancia
    margen = resolucion * 20
    minx, miny, maxx, maxy = zona.bounds
    minx, miny, maxx, maxy = minx - margen, miny - margen, maxx + margen, maxy + margen
    ancho = max(int(np.ceil((maxx - minx) / resolucion)), 1)
    alto = max(int(np.ceil((maxy - miny) / resolucion)), 1)
    if ancho * alto > 20_000_000:
        raise ValueError(
            f"la malla del conflicto sería de {ancho}x{alto} px "
            f"({ancho * alto:,} > 20,000,000); sube `resolucion` "
            f"(vas en {resolucion:g}) o revisa si el solape es tan grande a propósito"
        )
    forma = (alto, ancho)
    transform = from_origin(minx, maxy, resolucion, resolucion)

    gana_a = _distancia_a(nucleo_a, forma, transform, crs) <= _distancia_a(
        nucleo_b, forma, transform, crs
    )
    en_conflicto = (
        rasterizar(gpd.GeoDataFrame(geometry=[zona], crs=crs), forma, transform) == 1
    )
    trozos_a = _trozos(en_conflicto & gana_a, transform, crs, nombres[0])
    trozos_b = _trozos(en_conflicto & ~gana_a, transform, crs, nombres[1])

    salida_a = gdf_a.copy()
    salida_b = gdf_b.copy()
    salida_a.iloc[0, salida_a.columns.get_loc(salida_a.geometry.name)] = unary_union(
        [nucleo_a, *trozos_a.geometry]
    )
    salida_b.iloc[0, salida_b.columns.get_loc(salida_b.geometry.name)] = unary_union(
        [nucleo_b, *trozos_b.geometry]
    )
    return salida_a, salida_b, fusionar([trozos_a, trozos_b])


def puntos_a_linea(gdf: gpd.GeoDataFrame, campo: str | None = None) -> gpd.GeoDataFrame:
    """Une los puntos de la capa en una línea, en el orden en que vienen.

    Con `campo` genera una línea por cada valor distinto de esa columna
    (agrupación); sin él, todos los puntos forman una sola línea. El orden es
    el de las filas: si necesitas otro, ordénalo al cargar con `cargar_puntos(..., orden=...)`.
    Requiere al menos 2 puntos por línea.
    """

    def _linea(sub: gpd.GeoDataFrame) -> LineString:
        coords = [(p.x, p.y) for p in sub.geometry]
        if len(coords) < 2:
            raise ValueError("se necesitan al menos 2 puntos para una línea")
        return LineString(coords)

    if campo is None:
        geoms = [_linea(gdf)]
        datos: dict[str, list[Any]] = {}
    else:
        grupos = list(gdf.groupby(campo, sort=False))
        geoms = [_linea(sub) for _, sub in grupos]
        datos = {campo: [clave for clave, _ in grupos]}
    return gpd.GeoDataFrame(datos, geometry=geoms, crs=gdf.crs)


def puntos_a_poligono(
    gdf: gpd.GeoDataFrame, campo: str | None = None, envolvente: bool = False
) -> gpd.GeoDataFrame:
    """Une los puntos en un polígono, usándolos como vértices en orden de filas.

    Con `envolvente=True` usa la envolvente convexa (convex hull) en vez del
    orden de los puntos: útil cuando los puntos no vienen ordenados por el
    contorno. Con `campo` genera un polígono por grupo. Requiere >=3 puntos.
    """

    def _poligono(sub: gpd.GeoDataFrame) -> Polygon:
        pts = list(sub.geometry)
        if len(pts) < 3:
            raise ValueError("se necesitan al menos 3 puntos para un polígono")
        if envolvente:
            return unary_union(pts).convex_hull
        return Polygon([(p.x, p.y) for p in pts])

    if campo is None:
        geoms = [_poligono(gdf)]
        datos: dict[str, list[Any]] = {}
    else:
        grupos = list(gdf.groupby(campo, sort=False))
        geoms = [_poligono(sub) for _, sub in grupos]
        datos = {campo: [clave for clave, _ in grupos]}
    return gpd.GeoDataFrame(datos, geometry=geoms, crs=gdf.crs)


def linea_a_poligono(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Cierra cada línea de la capa en un polígono (sus vértices como contorno).

    Si la línea no está cerrada, el polígono la cierra uniendo el último punto
    con el primero. Devuelve copia con los mismos atributos.
    """

    def _poligono(g: BaseGeometry) -> Polygon:
        # ponytail: asume LineString simple; MultiLineString usa el primer tramo.
        coords = (
            list(g.geoms[0].coords)
            if g.geom_type == "MultiLineString"
            else list(g.coords)
        )
        if len(coords) < 3:
            raise ValueError(
                "la línea necesita al menos 3 vértices para cerrar un polígono"
            )
        return Polygon(coords)

    salida = gdf.copy()
    salida[salida.geometry.name] = gdf.geometry.apply(_poligono)
    return salida


def crear_cuadro(gdf: gpd.GeoDataFrame, margen: float = 0) -> gpd.GeoDataFrame:
    """Crea un rectángulo (bounding box) alrededor de la capa.

    `margen` amplía el cuadro en las unidades del CRS. Devuelve un
    GeoDataFrame de una sola entidad, útil como máscara de recorte previo.
    """
    _avisar_geografico(gdf, "el margen se aplicará en grados")
    minx, miny, maxx, maxy = gdf.total_bounds
    geom = box(minx - margen, miny - margen, maxx + margen, maxy + margen)
    return gpd.GeoDataFrame(geometry=[geom], crs=gdf.crs)


def cuadros_mde(
    predio: gpd.GeoDataFrame,
    resolucion: float | None = None,
    margen_borde_celdas: int | None = None,
    margen_salida: float = 0.0,
    parametros: dict[str, Any] | None = None,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Genera (cuadro_recorte, cuadro_salida) según la práctica de ANUDEM.

    Replica la práctica de ANUDEM (Hutchinson): el extent de SALIDA debe
    quedar al menos ~10 celdas DENTRO de la extensión de los datos de entrada,
    para que las celdas de borde se interpolen con datos a ambos lados y no con
    la mitad (equivalente a su parámetro Margin, cuyo default son 20 celdas).
    Ver la doc del parámetro `extent`/`Margin` de la herramienta.

    En este flujo se traduce a dos rectángulos derivados del predio:

    - `cuadro_salida`  = predio ampliado `margen_salida` (unidades del CRS). Es el
      ráster que se conserva; pásalo como `cuadro` a interpolar_mde / validar_mde.
    - `cuadro_recorte` = `cuadro_salida` ampliado `margen_borde_celdas * resolucion`.
      A él se recortan curvas y cauces, de modo que la ENTRADA sobresale ese borde
      respecto a la SALIDA (los puntos de fuera sostienen la interpolación del borde).

    El borde escala con la resolución: con `resolucion=20` y `margen_borde_celdas=10`
    el borde de datos extra es 200 m; con `resolucion=10`, 100 m.

    Parámetros
    ----------
    predio : capa del área de interés (ya reproyectada). Se usa su bounding box.
    resolucion : tamaño de píxel del MDE (unidades del CRS). Debe coincidir con el
        que se pase a interpolar_mde.
    margen_borde_celdas : ancho del borde de datos, en celdas. None = el de
        `parametros`, o 10 (mínimo recomendado para ANUDEM) sin él.
    margen_salida : cuánto ampliar el extent de salida respecto al predio (unidades
        del CRS). Default 0 = la salida es el predio. Súbelo si quieres conservar
        una franja alrededor del predio (p. ej. para análisis hidrológico).

        **Para PENDIENTE, `2 * resolucion` como mínimo.** La ventana 3x3 de
        Horn deja NaN la celda del borde del ráster, y con 0 la capa de clases
        pierde ese anillo allí donde el predio toca su bbox (en una ladera
        rectangular, más de un tercio de la superficie). En un predio irregular
        el hueco es menor pero no cero. El default no se cambia: movería el extent, y con él las cifras,
        de todo lo que ya llama sin el argumento.

    parametros : el dict de `derivar_parametros`. Lo que se deje en None se
        toma de ahí (`resolucion` y `margen_borde_celdas`); lo que se pase a mano manda sobre el dict.

    Devuelve (cuadro_recorte, cuadro_salida), ambos GeoDataFrame de una entidad.
    """
    _exigir_proyectado(predio, "predio")
    resolucion, margen_borde_celdas = _de_parametros(
        parametros, resolucion=resolucion, margen_borde_celdas=margen_borde_celdas
    ).values()
    if resolucion is None:
        raise ValueError("falta `resolucion` (o `parametros`)")
    if margen_borde_celdas is None:
        margen_borde_celdas = 10
    if resolucion <= 0:
        raise ValueError(f"resolucion {resolucion} debe ser > 0")
    if margen_borde_celdas < 0:
        raise ValueError(f"margen_borde_celdas {margen_borde_celdas} debe ser >= 0")
    borde = margen_borde_celdas * resolucion
    cuadro_salida = crear_cuadro(predio, margen=margen_salida)
    cuadro_recorte = crear_cuadro(predio, margen=margen_salida + borde)
    return cuadro_recorte, cuadro_salida


def limpiar_vacias(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Elimina entidades con geometría vacía o nula."""
    salida = gdf[~gdf.geometry.is_empty & ~gdf.geometry.isna()]
    return salida.copy()


def reparar_geometrias(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Repara geometrías inválidas con make_valid y descarta vacías/inválidas.

    `make_valid` y no `buffer(0)`: en un polígono en moño `buffer(0)` se queda
    con la mitad de la superficie. Las esquirlas de línea o punto que deja
    `make_valid` se quitan con `_dimension_dominante`. Devuelve copia; no muta
    la entrada.
    """
    salida = gdf.copy()
    salida[salida.geometry.name] = salida.geometry.make_valid().apply(
        _dimension_dominante
    )
    salida = salida[salida.geometry.is_valid & ~salida.geometry.is_empty]
    return salida.copy()


_GEOD = Geod(ellps="WGS84")


def _factor_unidad(unidad: str, tabla: dict[str, float], que: str) -> float:
    if unidad not in tabla:
        raise ValueError(f"unidad de {que} {unidad!r} no válida; usa una de {list(tabla)}")
    return tabla[unidad]


def _area_m2(gdf: gpd.GeoDataFrame, geodesica: bool = False) -> pd.Series:
    """Área de cada geometría en m², sea cual sea el CRS.

    Proyectado: plana, convertida desde la unidad del CRS (un State Plane en
    pies da ft²). Geográfico, o `geodesica=True`: sobre el elipsoide WGS84,
    porque en grados el área plana no tiene unidad. `abs()` porque
    `geometry_area_perimeter` da negativa con anillo horario.
    """
    _exigir_crs(gdf, "gdf")
    if not (geodesica or gdf.crs.is_geographic):
        return gdf.geometry.area * gdf.crs.axis_info[0].unit_conversion_factor ** 2
    geo = gdf.geometry.to_crs(4326)
    return pd.Series(
        [0.0 if g is None else abs(_GEOD.geometry_area_perimeter(g)[0]) for g in geo],
        index=gdf.index,
        dtype=float,
    )


def _longitud_m(gdf: gpd.GeoDataFrame, geodesica: bool = False) -> pd.Series:
    """Longitud (perímetro en polígonos) de cada geometría en m. Igual que `_area_m2`.

    En geodésica sale de `geometry_length` y no de `geometry_area_perimeter`,
    que en una línea da el doble (la cierra ida y vuelta).
    """
    _exigir_crs(gdf, "gdf")
    if not (geodesica or gdf.crs.is_geographic):
        return gdf.geometry.length * gdf.crs.axis_info[0].unit_conversion_factor
    geo = gdf.geometry.to_crs(4326)
    return pd.Series(
        [0.0 if g is None else _GEOD.geometry_length(g) for g in geo],
        index=gdf.index,
        dtype=float,
    )


def calcular_superficie(
    gdf: gpd.GeoDataFrame,
    campo: str | None = None,
    unidad: str = "ha",
    decimales: int = _DECIMALES_AREA,
) -> gpd.GeoDataFrame:
    """Añade una columna con el área de cada geometría, en la `unidad` pedida.

    `unidad`: "m2", "ha" o "km2". Sin `campo` la columna se llama
    `superficie_<unidad>`. Vale con cualquier CRS: proyectado mide plano (y
    convierte si el CRS va en pies); geográfico mide sobre el elipsoide. El
    resultado se redondea a `decimales` (2 por defecto: 0.01 ha = 100 m²).
    Devuelve copia.

    VA AL FINAL, y "al final" significa detrás del ÚLTIMO paso que toque la
    geometría, no solo detrás del recorte y la reproyección. En la cadena de
    pendientes el último es `resolver_por_prioridad`, que recorta cada capa
    contra la unión de las anteriores: calcular el área dentro del helper que
    arma cada capa cumple "después de recortar" y aun así sale obsoleta, porque
    las clases que ceden pierden geometría después. `guardar` y
    `exportar_tabla` avisan si no cuadra.

    **El default `superficie_ha` son 13 caracteres y NO sobrevive a un .shp**,
    que corta a 10. Pasa `campo="SUP"` (o el nombre corto que quieras) cuando la
    capa vaya a shapefile, y no se ha cambiado el default por eso porque el
    nombre largo es el correcto en GeoPackage y en `exportar_tabla`, que es
    donde el default se lee. Es la única función de la librería que añade una
    columna justo antes de guardar, así que es de donde salen las colisiones
    (`superfic_1`). `comprobar_nombres_shp` lo dice ANTES de escribir.
    """
    factor = _factor_unidad(unidad, _UNIDADES_AREA, "área")
    salida = gdf.copy()
    salida[campo or f"superficie_{unidad}"] = (_area_m2(gdf) / factor).round(decimales)
    return salida


def superficie_total(gdf: gpd.GeoDataFrame, unidad: str = "ha") -> float:
    """Superficie total en la `unidad` pedida ("m2", "ha", "km2"), sin redondear.

    Vale con cualquier CRS, como `calcular_superficie`. Capa vacía -> 0.0.
    """
    return float(_area_m2(gdf).sum()) / _factor_unidad(unidad, _UNIDADES_AREA, "área")


def superficie_total_ha(gdf: gpd.GeoDataFrame) -> float:
    """Superficie total de la capa, en hectáreas y SIN redondear.

    El escalar con que se compara contra una tolerancia y se imprime, no una
    columna: `calcular_superficie` es la otra mitad del par y redondea a 2
    decimales, justo el orden de la tolerancia típica (0.01 ha), así que no
    puede reusarse para cuadrar totales.

    De una capa vacía sale 0.0, no error: sumar nada es cero, y quien necesite
    distinguir "vacía" de "cero" ya tiene `len(gdf)`.

    Falla con un CRS geográfico: el nombre promete hectáreas, y en grados
    saldría un número sin unidad con nombre de hectáreas. Se queda plana y
    estricta a propósito, no se pasa a `superficie_total`: las guardas que la
    usan (`comprobar_particion`) la restan de `.area` de shapely, que es plana,
    y una geodésica de un lado daría hueco falso. Para cualquier CRS o unidad:
    `superficie_total`.
    """
    _exigir_proyectado(gdf, "gdf")
    return float(gdf.geometry.area.sum()) / 10_000


def comprobar_particion(
    capas: Iterable[gpd.GeoDataFrame],
    zona: gpd.GeoDataFrame,
    tolerancia_ha: float,
) -> tuple[float, float]:
    """Comprueba que `capas` PARTEN `zona`: ni hueco ni doble conteo. En ha.

    Devuelve `(falta_ha, solape_ha)` y falla si el hueco, el exceso o el
    solape pasan `tolerancia_ha`, CADA UNO por separado. `falta` = hueco -
    exceso: positiva = la partición no llega a cubrir la zona; negativa = se
    sale de ella. Es SOLO INFORMATIVA: la guarda no la usa. `solape` =
    superficie contada dos veces.

    Con UNA capa comprueba que sus propias filas parten la zona (el caso del
    entregable de pendientes); con varias, que la cascada de prioridades cerró.

    **Tres cifras: dos diferencias geométricas y una resta.** El hueco es la
    zona menos la unión de las capas (lo que ninguna cubre); el exceso, la unión
    menos la zona (lo que cubren fuera). El doble conteo sale de restar: la
    suma de las áreas cuenta el solape tantas veces como capas lo pisen y la
    unión lo cuenta una. Hueco y exceso van SEPARADOS a propósito: una cifra
    NETA (`zona - unión` en superficie) compensa, y una capa que deja 0.5 ha
    sin cubrir dentro y se sale 0.5 ha fuera daría 0.0 y pasaría con cualquier
    tolerancia. Si falla, el mensaje dice dónde está lo más grande: número de piezas y
    centroide de la pieza mayor del hueco o del exceso.

    Es la única guarda que ve un hueco de la cadena: desde
    `resolver_por_prioridad` es invisible (lo que ésa recorta es el solape) y
    desde `limpiar_moteado` también (reasignar es lo que hace un sieve).

    Parámetros
    ----------
    tolerancia_ha : SIN default, a propósito. Es un `declarado` y se escribe con
        su razón al lado, no heredado de otro predio: un número que nadie eligió
        es justo lo que la REGLA DE LOS NUMEROS prohíbe. Y el orden importa:
        todas las capas van recortadas contra la misma `zona`, así que el borde
        exterior es idéntico y el residuo legítimo es de reparación de
        geometría, no de píxel: con 1.0 ha en 1765 un hueco de 0.22 pasa por
        redondeo.

    Todas las capas y `zona` deben venir en el mismo CRS; no reproyecta sola.
    """
    juntas = fusionar(capas)  # valida CRS y columna de geometría entre capas
    if zona.crs != juntas.crs:
        raise ValueError("`zona` viene en otro CRS que las capas; reproyecta antes")
    suma_ha = superficie_total_ha(juntas)
    try:
        cubierto = unary_union(juntas.geometry)
        zona_geom = unary_union(zona.geometry)
        geom_hueco = zona_geom.difference(cubierto)
        geom_exceso = cubierto.difference(zona_geom)
    except GEOSException as e:
        raise ValueError(
            f"no se pudo comparar la zona con las capas ({e}); alguna geometría "
            "es inválida: pasa `zona` y las capas por `reparar_geometrias`"
        ) from e
    hueco = geom_hueco.area / 10_000
    exceso = geom_exceso.area / 10_000
    solape = suma_ha - cubierto.area / 10_000
    falta = hueco - exceso
    if _mostrar():
        print(
            f"cierre: hueco {hueco:.4f} ha | exceso {exceso:.4f} ha | "
            f"doble conteo {solape:.4f} ha"
        )
    if hueco > tolerancia_ha or exceso > tolerancia_ha or solape > tolerancia_ha:
        nombre, peor = max(
            ("hueco", geom_hueco), ("exceso", geom_exceso), key=lambda t: t[1].area
        )
        piezas = [g for g in getattr(peor, "geoms", [peor]) if not g.is_empty]
        donde = ""
        # solo si lo que se ubica es lo que falló: si falló el doble conteo, una
        # astilla de hueco por debajo de la tolerancia mandaría a buscar mal
        if piezas and peor.area / 10_000 > tolerancia_ha:
            mayor = max(piezas, key=lambda g: g.area).centroid
            donde = (
                f". Lo mayor es el {nombre}: {len(piezas)} piezas, la mayor "
                f"centrada en ({mayor.x:.0f}, {mayor.y:.0f})"
            )
        raise ValueError(
            f"la partición no cierra contra la zona: hueco {hueco:.4f} ha, "
            f"exceso {exceso:.4f} ha, doble conteo {solape:.4f} ha, "
            f"tolerancia {tolerancia_ha} ha{donde}"
        )
    return falta, solape


def longitud_total_m(gdf: gpd.GeoDataFrame) -> float:
    """Longitud total de la capa, en metros y sin redondear. Exige CRS proyectado.

    Gemelo de `superficie_total_ha` para líneas. No convierte a km: la comparación de
    la que sale (separación entre curvas, largo dentro de un polígono) se hace
    en metros, y dividir aquí solo obliga a multiplicar allá.

    Falla con un CRS geográfico: el nombre promete metros, y en grados
    saldría un número sin unidad con nombre de metros. Plana y estricta por lo
    mismo que `superficie_total_ha`; para cualquier CRS o unidad:
    `longitud_total`.
    """
    _exigir_proyectado(gdf, "gdf")
    return float(gdf.geometry.length.sum())


def longitud_total(gdf: gpd.GeoDataFrame, unidad: str = "m") -> float:
    """Longitud total (perímetro en polígonos) en la `unidad` pedida ("m", "km").

    Sin redondear y con cualquier CRS, como `calcular_longitud`.
    """
    return float(_longitud_m(gdf).sum()) / _factor_unidad(
        unidad, _UNIDADES_LONGITUD, "longitud"
    )


def _largo_cerca_m(
    medidas: gpd.GeoDataFrame, otras: gpd.GeoDataFrame, tol: float
) -> float:
    """Metros de `medidas` que caen a <= `tol` de `otras`.

    El buffer se disuelve antes de recortar: sin disolver, donde dos buffers se
    solapan el mismo tramo se cuenta dos veces.
    """
    if medidas.empty or otras.empty:
        return 0.0
    return longitud_total_m(recortar(medidas, disolver(zona_de_influencia(otras, tol))))


def concordancia_lineas(
    lineas: gpd.GeoDataFrame,
    referencia: gpd.GeoDataFrame,
    tolerancias: Iterable[float],
    zona: gpd.GeoDataFrame | None = None,
) -> pd.DataFrame:
    """Cuánto se parecen dos redes de líneas, medido EN LAS DOS DIRECCIONES.

    precision_pct   % del largo de `lineas` a <= tol de `referencia`
    cobertura_pct   % del largo de `referencia` a <= tol de `lineas`

    Las dos van juntas porque una sola se engaña: una red más corta gana
    precisión y pierde cobertura por construcción. El uso típico es juzgar una
    red de drenaje derivada contra una cartográfica.

    `tolerancias` es una ESCALERA y no un número: no hay un `tol` verdadero
    (la referencia trae su error posicional y el MDE su píxel), así que un solo
    valor decidiría el veredicto por decreto. Lo que se lee al comparar dos
    redes es si el ORDEN entre ellas aguanta la escalera.

    `zona` limita lo que se MIDE, no lo que casa: el largo de cada capa se
    cuenta solo dentro de `zona`, pero la otra capa entra entera hasta
    `max(tolerancias)` fuera de ella. Si no, un cauce pegado al borde por
    dentro perdería a su pareja de un metro más allá y el borde bajaría la
    cobertura sin que la red tuviera la culpa.

    **TRAMPA: un juez no puede ser insumo.** Si
    `referencia` entró en la cadena que produjo `lineas` (p. ej. como
    `quiebres` de `interpolar_mde`), el parecido sale en buena parte por
    construcción y NO valida nada. Para ese caso: hold-out con
    `bloques_ajedrez`, y aquí se pasa como `referencia` SOLO la parte que no
    entró.

    Devuelve un DataFrame, una fila por `tol` en orden creciente: `tol_m`,
    `km_lineas`, `km_referencia`, `precision_pct`, `cobertura_pct`.

    Falla si las capas no comparten CRS, si el CRS es geográfico (la tolerancia
    va en metros), si alguna `tolerancia` no es > 0, o si alguna capa queda sin
    largo dentro de `zona`: un porcentaje sobre cero no es un resultado.
    COSTO: dos buffer+disolver+recorte vectoriales por peldaño.
    """
    _exigir_crs(lineas, "lineas")
    _exigir_crs(referencia, "referencia")
    if lineas.crs != referencia.crs:
        raise ValueError(
            "`lineas` y `referencia` vienen en distinto CRS; reproyecta antes"
        )
    _exigir_proyectado(lineas, "lineas")
    tols = sorted(float(t) for t in tolerancias)
    if not tols or tols[0] <= 0:
        raise ValueError(f"tolerancias {tols} deben ser > 0 y al menos una")
    lin, ref = lineas, referencia
    if zona is not None:
        # Alcance exacto: mas alla de max(tols) nada puede casar. Recortar aqui
        # es lo que deja pasar una capa estatal sin bufferearla entera.
        alcance = zona_de_influencia(zona, tols[-1])
        lin_z, ref_z = recortar(lineas, zona), recortar(referencia, zona)
        lin, ref = recortar(lineas, alcance), recortar(referencia, alcance)
    else:
        lin_z, ref_z = lineas, referencia
    lin_z, ref_z = limpiar_vacias(lin_z), limpiar_vacias(ref_z)
    m_lin, m_ref = longitud_total_m(lin_z), longitud_total_m(ref_z)
    if m_lin == 0 or m_ref == 0:
        raise ValueError(
            f"sin largo dentro de la zona: lineas {m_lin:.1f} m, referencia "
            f"{m_ref:.1f} m. Un porcentaje sobre cero no se reporta."
        )
    filas = []
    for tol in tols:
        filas.append(
            {
                "tol_m": tol,
                "km_lineas": m_lin / 1000,
                "km_referencia": m_ref / 1000,
                "precision_pct": 100 * _largo_cerca_m(lin_z, ref, tol) / m_lin,
                "cobertura_pct": 100 * _largo_cerca_m(ref_z, lin, tol) / m_ref,
            }
        )
    return pd.DataFrame(filas)


def bloques_ajedrez(zona: gpd.GeoDataFrame, lado: float) -> gpd.GeoDataFrame:
    """Parte `zona` en cuadros de `lado` alternados como un tablero: columna `pliegue` 0/1.

    Es la partición del HOLD-OUT ESPACIAL. Con una capa de referencia que
    también es insumo (cauces que entran como `quiebres`), el pliegue 0 impone
    y el pliegue 1 juzga, y luego al revés:

        b = geo.bloques_ajedrez(cuadro_recorte, lado=1000)
        impone = geo.recortar(cauces, b[b["pliegue"] == 0])
        juzga = geo.zona_de_influencia(b[b["pliegue"] == 1], -guarda)   # franja de guarda
        # ... MDE con quiebres=impone, red derivada ...
        geo.concordancia_lineas(red, geo.recortar(cauces, juzga), tols, zona=juzga)

    **Por qué bloques y no tramos al azar:** un tramo reservado al azar queda
    entre sus vecinos impuestos del mismo cauce, que ya fijan la vaguada; el
    juez sale contaminado por vecindad. Con bloques, lo reservado está lejos de
    lo impuesto salvo en las aristas, y ahí va la guarda. Los cuadros del mismo
    pliegue solo se tocan en las esquinas, así que la `zona_de_influencia` negativa por
    cuadro quita la franja de TODAS las aristas.

    `lado` es DECLARADO: grande frente al alcance del flujo que cruza la arista
    (si no, la guarda se come el bloque) y chico frente al predio (si no, un
    pliegue cae entero en otro relieve). La rejilla se ancla en múltiplos de
    `lado`, así que la misma `zona` da siempre los mismos cuadros.

    TECHO: el drenaje NO es local. Una red derivada dentro de un cuadro del
    pliegue que juzga recibe el agua de los cuadros que imponen aguas arriba;
    la guarda lo recorta cerca de la arista, no lo elimina.

    Devuelve los cuadros recortados a `zona`, con `pliegue` (0/1), `fila` y
    `col`. Falla con `lado` <= 0 o CRS geográfico.
    """
    _exigir_proyectado(zona, "zona")
    if lado <= 0:
        raise ValueError(f"lado {lado} debe ser > 0")
    minx, miny, maxx, maxy = zona.total_bounds
    i0, j0 = int(np.floor(minx / lado)), int(np.floor(miny / lado))
    i1, j1 = int(np.ceil(maxx / lado)), int(np.ceil(maxy / lado))
    filas, cols = (
        m.ravel() for m in np.meshgrid(np.arange(j0, j1), np.arange(i0, i1), indexing="ij")
    )
    cuadros = gpd.GeoDataFrame(
        {"fila": filas, "col": cols, "pliegue": (filas + cols) % 2},
        geometry=shapely.box(cols * lado, filas * lado, (cols + 1) * lado, (filas + 1) * lado),
        crs=zona.crs,
    )
    return limpiar_vacias(recortar(cuadros, zona)).reset_index(drop=True)


def pendiente_sostenida(
    curvas: gpd.GeoDataFrame, zona: gpd.GeoDataFrame, equidistancia: float
) -> tuple[float, float]:
    """Pendiente % que las curvas de nivel pueden sostener dentro de `zona`.

    Control de la CARTOGRAFÍA de entrada, e independiente del MDE a propósito:
    juzgar una capa de pendientes con un sombreado del mismo ráster que la
    produjo es circular. Aquí se contrasta la separación horizontal real entre
    curvas contra su equidistancia vertical, que es el techo de lo que el dato
    de origen puede afirmar. Sirve sobre todo para el rango más alto, que suele
    ser ondulación de la interpolación, no terreno.

    La separación se mide con `separacion_media` (G'(0), la derivada en el
    origen de la escalera de buffers), NO con el cociente área/largo.

    Área/largo divide entre la longitud REAL del trazo, así que el zigzag de la
    cartografía la infla y la separación sale baja por ese mismo factor.
    Importa porque `s_sostenida` se compara contra `h` y contra el piso
    antialias, y los dos se miden SIN ese sesgo. Comparar dos estimadores distintos es peor que un
    estimador sesgado, porque el sesgo no se ve.

    G'(0) es igual de robusta a que la zona venga troceada en muchos polígonos
    y a las curvas que entran y salen, que es lo que rompe cualquier medición
    vértice a vértice.

    Parámetros
    ----------
    curvas : capa de curvas de nivel, SIN recortar (se recorta aquí a `zona`).
    zona : polígonos a juzgar (p. ej. el rango >100%). Mismo CRS, proyectado.
    equidistancia : separación vertical entre curvas, en unidades del CRS.

    Devuelve (separacion_m, pendiente_pct). Sin curvas dentro devuelve
    (nan, nan): no hay cartografía que sostenga NADA ahí, que es distinto de
    sostener 0% y suele significar que la zona es un artefacto. Compruébalo con
    `sep == sep` (NaN != NaN), el mismo idioma que el resto del flujo.
    """
    _exigir_proyectado(curvas, "curvas")
    if equidistancia <= 0:
        raise ValueError(f"equidistancia {equidistancia} debe ser > 0")
    separacion, _, _ = separacion_media(curvas, zona)
    if separacion != separacion:  # NaN != NaN
        return float("nan"), float("nan")
    return separacion, equidistancia / separacion * 100


def separacion_media(
    curvas: gpd.GeoDataFrame,
    zona: gpd.GeoDataFrame,
    paso: float | None = None,
    resolucion: float | None = None,
) -> tuple[float, float, float]:
    """Separación horizontal media entre curvas dentro de `zona`, por G'(0).

    A diferencia del cociente global área/longitud (que sesga hacia las
    zonas llanas, donde la curva recorre más área por metro), esto mide la
    derivada en el origen de la escalera de buffers: una banda de ancho
    `paso` alrededor de las curvas, área de esa banda dentro de `zona` sobre
    `paso`. Un solo `paso` chico ya es la estimación; no hace falta barrer
    varios valores, la estabilidad medida es del 0.17 % entre 2.5 y 5.0 m.

    Parámetros
    ----------
    paso : el `d` de la diferencia finita, en unidades del CRS. Si es None y
        se da `h`, usa `h / 2`; si tampoco se da `h`, 2.5 m (el de las
        corridas de referencia).
    resolucion : resolución del MDE que se va a interpolar después. Conveniencia para
        no declarar `paso` a mano cuando `h` ya se conoce.

    Devuelve (lambda_m, longitud_efectiva_m, factor_zigzag):
    - `lambda_m` : separación horizontal media (λ), media armónica ponderada
      por área. La dominan las zonas empinadas y SUBESTIMA los llanos: no
      derives parámetros de λ sola, mira los cuantiles de `separaciones`.
    - `longitud_efectiva_m` : longitud de curva "sin zigzag" (área de la
      banda sobre `paso`, entre 2). Convierte un intervalo de muestreo de
      TERRENO al que espera `interpolar_mde` sobre el trazo real
      (`intervalo_trazo = intervalo_terreno * factor_zigzag`).
    - `factor_zigzag` : longitud real de curva dentro de `zona` dividida
      entre `longitud_efectiva_m`.

    Sin curvas dentro de `zona` devuelve (nan, nan, nan).
    """
    _exigir_proyectado(curvas, "curvas")
    _exigir_proyectado(zona, "zona")
    if paso is None:
        paso = resolucion / 2 if resolucion else 2.5
    dentro = recortar(curvas, zona)
    if dentro.empty:
        return float("nan"), float("nan"), float("nan")
    banda = disolver(zona_de_influencia(dentro, paso))
    area_banda_m2 = superficie_total_ha(intersecar(zona, banda)) * 10_000
    longitud_efectiva = area_banda_m2 / paso / 2
    if longitud_efectiva <= 0:
        return float("nan"), float("nan"), float("nan")
    lam = superficie_total_ha(zona) * 10_000 / longitud_efectiva
    # La banda de una curva no debe tocar la de su vecina. Cuando `paso` se
    # acerca a λ/2 las bandas se funden, el área se satura y `longitud_efectiva`
    # sale corta, así que λ Y el zigzag salen INFLADOS los dos, en silencio y
    # justo hacia donde uno los creería. No es sinuosidad: es la vecina.
    if paso > lam / 4:
        # ASCII a proposito: esto se IMPRIME, y una consola cp1252 (Windows) no
        # puede codificar el caracter lambda. Un aviso que revienta el proceso a
        # mitad de un barrido de minutos es peor que no avisar. En docstrings si
        # va el simbolo: esos se leen, no se codifican a stdout.
        print(
            f"Aviso: paso {paso:g} m > lambda/4 ({lam / 4:.1f} m): la banda toca "
            f"la curva vecina, el area se satura y lambda ({lam:.1f}) y el zigzag "
            f"salen inflados. Baja `paso`; el default de 2.5 m sirve casi siempre."
        )
    return lam, longitud_efectiva, longitud_total_m(dentro) / longitud_efectiva


def ancho_banda(
    curvas: gpd.GeoDataFrame,
    campo: str,
    zona: gpd.GeoDataFrame,
    equidistancia: float,
    zigzag: float,
    pendiente_pct: float = 100.0,
    cortes: Iterable[float] | None = None,
    devolver_banda: bool = False,
) -> (
    tuple[list[dict[str, float]], dict[str, float]]
    | tuple[list[dict[str, float]], dict[str, float], gpd.GeoDataFrame]
):
    """Ancho transversal MEDIDO `w` de la clase más alta, por escalera de cortes.

    Es la medición que `resolucion_regla(..., ancho_medido=w)` no puede hacer y
    sin la cual su techo de `trazado` cae al ancho GEOMÉTRICO `equidistancia /
    p_max`. La diferencia no es cosmética: mueve `h` un 30 % en el predio de
    referencia.

    También es EXTERNA al MDE, que es el punto entero: la clase alta del MDE se
    juzga contra la cartografía, no contra un sombreado del mismo ráster que la
    produjo.

    Cómo mide, en dos partes:

    1. **Reconstruye la banda.** Un tramo de la curva de cota `b` pertenece a la
       clase si su nivel contiguo `a` pasa a menos de `s_max = equidistancia /
       (pendiente_pct/100)`: esa es la separación horizontal a la que el terreno
       sostiene esa pendiente. Con `pendiente_pct` = 100 (default), `s_max` = la
       equidistancia.
    2. **Escalera de cortes.** Repite el recorte a cada `x` de `cortes`. El
       largo que APARECE entre `x_previo` y `x` está a esa distancia de su
       vecina, así que aporta una franja de ancho medio `(x + x_previo)/2`.
       Sumadas, esas franjas son el área que la carta sostiene en la clase, y
       `w = area / largo_efectivo` es la banda media.

    Los largos van SIN zigzag (divididos entre `zigzag`): el trazo de la carta
    es más largo que la banda que describe.

    OJO, y es contraintuitivo: **el zigzag se CANCELA en `w`**, porque divide el
    área y el largo efectivo por igual. Sí manda en `area_ha` y en
    `largo_efectivo_m`, y `area_ha` es el blanco cartográfico contra el que se
    compara la clase del MDE, o sea una cifra que se cita. Pasar `zigzag=1.0`
    cuando el trazo zigzaguea no mueve `w`, pero infla ese blanco por el factor
    entero.

    Parámetros
    ----------
    curvas : capa de curvas de nivel, con una fila (o varias) por cota. No hace
        falta recortarla: se recorta aquí a `zona`.
    campo : columna con la cota.
    zona : polígono contra el que se mide (el predio). Mismo CRS, proyectado.
    equidistancia : salto de cota, en unidades del CRS.
    zigzag : `factor_zigzag` de `separacion_media`, medido sobre `zona`. Se pasa
        y no se recalcula, igual que en `separaciones`. No cambia `w` (se
        cancela); sí cambia `area_ha`, que es el blanco cartográfico.
    pendiente_pct : límite inferior de la clase que se mide (default 100, la
        clase `>100 %`). Fija `s_max = equidistancia / (pendiente_pct/100)`.
    cortes : distancias (m) de la escalera. None (default) = `s_max` por
        (0.4, 0.6, 0.8, 1.0), que con equidistancia 20 da los 8/12/16/20 con que
        se midió `w` = 15.5 m. `s_max` se añade siempre si no está: el último
        corte TIENE que cerrar la banda o el largo efectivo sale corto.
    devolver_banda : devuelve además la banda como GEOMETRÍA (ver abajo).
    `geo.MOSTRAR` : imprime la escalera y `w`.

    COSTO: `len(cortes) x (nº de cotas - 1)` buffer + recorte vectoriales. Con
    59 cotas y 4 cortes son ~236 pasadas: segundos a minutos, no horas, pero no
    es gratis. Se mide UNA vez por predio.

    NO hace falta recortar `curvas` antes de llamar: se recorta aquí a `zona`
    engordada `max(cortes)`, que es exacto (más allá de ese alcance ninguna
    geometría puede cambiar el resultado) y es lo que hace la función usable con
    cartografía estatal. Sin ese recorte, el buffer se aplica a la multilínea
    completa de cada nivel y con una carta estatal eso agota la memoria y el
    sistema mata el proceso sin traza.

    Devuelve `(tabla, resumen)`, como `separaciones`:
    - `tabla` : un dict por corte con `distancia`, `largo_nuevo_m` (el que
      aparece en ese bracket, ya sin zigzag), `ancho_medio_m` y `area_ha`.
    - `resumen` : `ancho_m` (la `w`), `area_ha` (el blanco cartográfico de la
      clase), `largo_efectivo_m`, `separacion_max` y `ancho_geometrico`
      (= `s_max`, con el que hay que contrastar `w`).

    Sin banda medible dentro de `zona` levanta ValueError: eso es un resultado
    ("esta cartografía no tiene una banda limpia"), no un cero que se pueda
    seguir usando.

    Con `devolver_banda=True` devuelve `(tabla, resumen, banda)`. `banda` son
    los TRAMOS DE CURVA de la clase al último corte, ya recortados a `zona`: la
    misma geometría con la que se contó `largo_efectivo_m`, no una reconstruida
    aparte. Es lo que hace falta para la cifra que el resumen numérico NO puede
    dar, del tipo *"el MDE ve el 56 % del escarpe cartográfico (10 436 m
    efectivos)"*. Ese porcentaje es el largo de la banda
    que cae DENTRO de la clase del MDE sobre el largo total, y sin la geometría
    no sale:

        _, resumen, banda = ancho_banda(curvas, "COTA", predio, 20, zigzag,
                                        devolver_banda=True)
        visto = longitud_total_m(recortar(banda, alta)) / zigzag   # `alta` = clase del MDE
        print(f"el MDE ve el {100 * visto / resumen['largo_efectivo_m']:.0f} %")

    OJO con el zigzag, y es la trampa de esta salida: `longitud_total_m(banda)` es el
    largo del TRAZO, con zigzag dentro, mientras que `largo_efectivo_m` ya viene
    dividido entre él. Dividir el largo medido sobre `banda` antes de compararlo
    NO es cosmética: en un predio con zigzag 1.408 son 40 puntos porcentuales.

    Son líneas, no polígono, porque es lo que ya se calcula por dentro y lo que
    la cifra necesita. El polígono es `zona_de_influencia(banda, w/2)` en la llamada, y sale
    con el `w` que esta misma función acaba de devolver.
    """
    _exigir_proyectado(curvas, "curvas")
    _exigir_proyectado(zona, "zona")
    if equidistancia <= 0:
        raise ValueError(f"equidistancia {equidistancia} debe ser > 0")
    if pendiente_pct <= 0:
        raise ValueError(f"pendiente_pct {pendiente_pct} debe ser > 0")
    if zigzag <= 0:
        raise ValueError(f"zigzag {zigzag} debe ser > 0 (sale de separacion_media)")
    if campo not in curvas.columns:
        raise ValueError(f"'{campo}' no está en curvas")

    s_max = equidistancia / (pendiente_pct / 100)
    if cortes is None:
        cortes = [s_max * f for f in (0.4, 0.6, 0.8, 1.0)]

    # RECORTE PREVIO, y es EXACTO, no una aproximación. El largo solo se cuenta
    # dentro de `zona`, y un tramo de la cota `b` dentro de `zona` solo puede
    # entrar si tiene un tramo de `a` a menos de `x <= max(cortes)`. Fuera de
    # `zona` engordada ese máximo, ninguna geometría puede cambiar el resultado.
    #
    # Sin esto, `zona_de_influencia(por_cota[a], x)` se aplica a la geometría COMPLETA de esa
    # cota. Con cartografía estatal eso es una multilínea de cientos de miles de
    # vértices por nivel, y el buffer de todas ellas pasa de 16 GB de RAM y el
    # sistema mata el proceso SIN traza: no es lentitud. Recortar
    # primero deja la misma cifra y la vuelve barata.
    # `s_max` entra siempre: el largo efectivo de la banda es el del ÚLTIMO
    # corte, así que si la escalera no llega hasta ahí, `w` sale inflada.
    escalera = sorted({float(c) for c in cortes if c > 0} | {s_max})

    # La ESCALERA DE COTAS sale de las curvas COMPLETAS, antes del recorte. Si se
    # recalcula sobre lo recortado y un nivel entero queda fuera, dos cotas que
    # NO son contiguas pasan a serlo y se mide la proximidad entre niveles
    # separados 2e como si fueran vecinos. El recorte puede cambiar qué
    # geometría hay; no puede cambiar cuál es el nivel contiguo.
    cotas = sorted(pd.to_numeric(curvas[campo], errors="coerce").dropna().unique())
    if len(cotas) < 2:
        raise ValueError("menos de dos cotas: no hay nivel contiguo que medir")

    # `escalera[-1]` y no `max(cortes)`: `cortes` es un `Iterable`, ya se recorrió
    # arriba, y un generador queda agotado (el `max` de un iterador consumido
    # revienta con "empty sequence", lejos de la causa). `escalera` está ordenada
    # y siempre trae `s_max`, así que su último elemento ES este alcance.
    alcance = escalera[-1]
    recortadas = recortar(curvas, zona_de_influencia(zona, alcance * 1.01))
    por_cota = {c: recortadas[recortadas[campo] == c] for c in cotas}

    if _mostrar():
        print(
            f"   {'banda s (m)':>14} {'largo nuevo (m)':>17} {'ancho medio (m)':>16} "
            f"{'area (ha)':>11}"
        )
    tabla: list[dict[str, float]] = []
    area_m2, largo_previo, x_previo = 0.0, 0.0, 0.0
    trozos: list[gpd.GeoDataFrame] = []
    for x in escalera:
        largo = 0.0
        # la cobertura es monótona y `escalera` va en orden, así que el ÚLTIMO
        # corte ya trae la banda entera: no hay que acumular los anteriores ni
        # deduplicarlos después.
        ultimo = devolver_banda and x == escalera[-1]
        for a, b in zip(cotas, cotas[1:]):
            # un nivel que el recorte dejó vacío aporta 0, que es lo correcto:
            # estaba a más de `alcance` de `zona` y no podía tocar la banda
            if por_cota[a].empty or por_cota[b].empty:
                continue
            cerca = recortar(por_cota[b], zona_de_influencia(por_cota[a], x))
            if not cerca.empty:
                dentro = recortar(cerca, zona)
                largo += longitud_total_m(dentro)
                if ultimo and not dentro.empty:
                    trozos.append(dentro)
        # `max(0, ...)`: la cobertura es monótona por construcción, pero una
        # geometría rota puede dar un largo que baja. Un aporte negativo
        # restaría área a la clase sin que nada lo diga.
        nuevo = max(0.0, largo - largo_previo) / zigzag
        ancho_medio = (x + x_previo) / 2
        aporte = nuevo * ancho_medio
        area_m2 += aporte
        tabla.append(
            {
                "distancia": x,
                "largo_nuevo_m": nuevo,
                "ancho_medio_m": ancho_medio,
                "area_ha": aporte / 10_000,
            }
        )
        if _mostrar():
            print(
                f"   {x_previo:6.1f}-{x:<7.1f} {nuevo:17.0f} {ancho_medio:16.1f} "
                f"{aporte / 10_000:11.2f}"
            )
        largo_previo, x_previo = largo, x

    largo_efectivo = largo_previo / zigzag
    if largo_efectivo <= 0 or area_m2 <= 0:
        raise ValueError(
            f"ninguna banda medible dentro de `zona` a menos de {s_max:g} m del "
            f"nivel contiguo. O la clase no existe en este predio (que es un "
            f"resultado), o `zona` y `curvas` no se pisan: revisa el CRS."
        )
    ancho = area_m2 / largo_efectivo

    resumen = {
        "ancho_m": ancho,
        "area_ha": area_m2 / 10_000,
        "largo_efectivo_m": largo_efectivo,
        "separacion_max": s_max,
        "ancho_geometrico": s_max,
    }
    if _mostrar():
        print(
            f"   w = banda media medida = {ancho:.1f} m sobre "
            f"{largo_efectivo:.0f} m efectivos | blanco {area_m2 / 10_000:.2f} ha"
        )
        print(
            f"   ancho geometrico (equidistancia/p) = {s_max:.1f} m. Pasa `w` como "
            f"`ancho_medido` a `resolucion_regla`: con el geometrico el techo de "
            f"trazado sale {s_max / ancho:.2f} veces mas grueso."
        )
    if devolver_banda:
        # `trozos` no puede venir vacía aquí: sin geometría el largo sería 0 y
        # el ValueError de arriba ya habría saltado. Se concatena y ya.
        banda = gpd.GeoDataFrame(pd.concat(trozos, ignore_index=True), crs=recortadas.crs)
        if _mostrar():
            print(
                f"   banda devuelta: {len(banda)} tramos, "
                f"{longitud_total_m(banda):.0f} m de TRAZO (con zigzag dentro). "
                f"Dividelo entre zigzag antes de compararlo con el largo efectivo."
            )
        return tabla, resumen, banda
    return tabla, resumen


def sostenimiento_por_clase(
    gdf: gpd.GeoDataFrame,
    curvas: gpd.GeoDataFrame,
    campo: str,
    rangos: list[tuple[Any, float, float]],
    equidistancia: float,
) -> list[dict[str, Any]]:
    """Qué separación sostiene la cartografía en cada clase ACUMULADA (`>= p`).

    Una sola clasificación responde por todos los cortes posibles de la clase
    alta. Es la medición que decide qué clase puede entregar un predio, y de
    UNA interpolación, no de una por corte.

    Para cada límite inferior `p` de `rangos` se toma la unión de esa clase y
    todas las superiores (lo mismo que reagrupar `rangos` con la abierta
    empezando en `p`) y se le mide `pendiente_sostenida`.

    Por qué de una sola corrida: `s_sostenida` resultó estable al 1 % frente a
    la resolución. El ancho del objeto es del
    terreno y de las curvas, no del píxel; lo que sí se mueve con `h` es la
    superficie, y eso lo tabula `barrer_resolucion`.

    Cómo se usa, que es el punto entero:

        filas = sostenimiento_por_clase(gdf, curvas, "clase", rangos, 20)
        for f in filas:                       # de la más alta a la más baja
            if f["piso_local"] <= h <= f["s_sostenida"]:
                break                         # el corte más alto que aguanta

    Las dos condiciones son distintas y las dos hacen falta:
    - `h >= piso_local` : el rizo del spline ondula con la separación LOCAL
      entre curvas y Horn deriva sobre `2h`, así que hay alias cuando
      `2h < s`. Es el mismo `antialias` de `resolucion_regla`, con la λ de
      ESTA clase en vez de la del extent. **No uses el piso global aquí**: en
      un predio mayormente suave describe el llano y sobre-restringe la banda
      empinada.
    - `h <= s_sostenida` : el objeto tiene que ser más ancho que el píxel, o su
      superficie no es resoluble por bien ubicado que esté.

    `s_sostenida` NaN = ninguna curva dentro de esa clase: rizo puro, ese corte
    no se cita. Los cortes con `p <= 0` se saltan (`>= 0` es el predio entero).

    Devuelve una lista de dicts, de mayor a menor corte, con `corte`,
    `etiquetas` (las de `rangos` que entran), `area_ha`, `n_piezas`,
    `s_sostenida`, `piso_local` (= `s_sostenida/2`) y `p_sostenida`.
    """
    cortes = sorted({p for _, p, _ in rangos if p > 0}, reverse=True)
    if not cortes:
        raise ValueError(f"ningún rango con límite inferior > 0: {rangos}")

    filas: list[dict[str, Any]] = []
    for corte in cortes:
        etiquetas = [et for et, p, _ in rangos if p >= corte]
        sub = gdf[gdf[campo].isin(etiquetas)]
        s_c, p_c = (
            pendiente_sostenida(curvas, sub, equidistancia)
            if len(sub)
            else (float("nan"), float("nan"))
        )
        filas.append(
            {
                "corte": corte,
                "etiquetas": etiquetas,
                "area_ha": superficie_total_ha(sub),
                "n_piezas": len(sub),
                "s_sostenida": s_c,
                "piso_local": s_c / 2,
                "p_sostenida": p_c,
            }
        )

    if _mostrar():
        print(
            f"   {'corte':>7} {'area_ha':>10} {'piezas':>7} {'s_sost':>8} "
            f"{'piso_loc':>9} {'p_sost':>8}  clases"
        )
        for f in filas:
            print(
                f"   {f['corte']:6g}% {f['area_ha']:10.2f} {f['n_piezas']:7d} "
                f"{f['s_sostenida']:8.1f} {f['piso_local']:9.1f} "
                f"{f['p_sostenida']:8.1f}  {'+'.join(str(e) for e in f['etiquetas'])}"
            )
        print(
            "   Un corte es entregable si existe `h` con "
            "`piso_local <= h <= s_sost`. Toma el corte MAS ALTO que lo cumpla: "
            "fundir clases ensancha el objeto, y un objeto mas ancho cabe en el "
            "pixel. `s_sost` NaN = sin curvas dentro, rizo puro, no se cita."
        )
    return filas


def auditar_rangos(
    gdf: gpd.GeoDataFrame,
    curvas: gpd.GeoDataFrame,
    campo: str,
    rangos: list[tuple[Any, float, float]],
    equidistancia: float,
    distancia: npt.NDArray[Any],
    mascara: npt.NDArray[Any],
    resolucion: float,
) -> list[dict[str, Any]]:
    """Audita cada rango de pendiente contra un techo cartográfico EXTERNO al MDE.

    Para un límite `p`, la separación equivalente entre curvas es `e/p`, y el
    área a más de `e/(2p)` de TODA curva no puede sostener `p`: el
    complemento acota la clase por ARRIBA. Es una cota FLOJA (estar a 10 m de
    una curva no dice dónde está la siguiente; en medio de un vacío de 400 m
    también se está a 10 m de una), así que para el rango más alto puede dar
    cientos de hectáreas contra un blanco cartográfico firme mucho menor.

    El juez real por rango es `pendiente_sostenida` (columnas `s_sostenida` /
    `p_sostenida`): NaN ahí significa "sin curvas dentro, es rizo puro", una
    señal binaria que el techo no puede dar porque solo acota área.

    Parámetros
    ----------
    distancia, mascara, h : el ráster de distancia a la curva más cercana y
        su máscara de zona (de `raster_distancia`), sobre el MISMO dominio de
        píxeles que se usó para producir `gdf`. Compartir ese ráster entre
        varios rangos es lo que evita comparar longitudes de dominios
        distintos.
    `geo.MOSTRAR` : además de devolver la tabla, la imprime.

    Devuelve una lista de dicts, uno por rango con límite inferior > 0:
    `etiqueta`, `area_mde_ha`, `acumulado_ha` (área del MDE en este rango y
    todos los superiores), `techo_ha`, `s_sostenida`, `p_sostenida`,
    `bandera`.
    """
    px_ha = resolucion**2 / 10_000
    d = distancia[mascara]
    area_zona = d.size * px_ha
    areas = {et: superficie_total_ha(gdf[gdf[campo] == et]) for et, _, _ in rangos}

    filas: list[dict[str, Any]] = []
    for etiqueta, p_inf, _ in rangos:
        if p_inf <= 0:
            continue
        acum = sum(areas[e2] for e2, p2, _ in rangos if p2 >= p_inf)
        techo = area_zona - float((d > equidistancia / (2 * p_inf / 100)).sum()) * px_ha
        sub = gdf[gdf[campo] == etiqueta]
        s_c, p_c = (
            pendiente_sostenida(curvas, sub, equidistancia)
            if not sub.empty
            else (float("nan"), float("nan"))
        )
        if acum > techo:
            bandera = "EXCEDE el techo"
        elif s_c != s_c:
            bandera = "nan: sin curvas dentro, es rizo puro"
        else:
            bandera = f"{100 * acum / techo:.0f}% del techo"
        filas.append(
            {
                "etiqueta": etiqueta,
                "area_mde_ha": areas[etiqueta],
                "acumulado_ha": acum,
                "techo_ha": techo,
                "s_sostenida": s_c,
                "p_sostenida": p_c,
                "bandera": bandera,
            }
        )

    if _mostrar():
        print(
            f"   {'rango':>8} {'MDE ha':>9} {'acum >=p':>9} {'techo ha':>9} "
            f"{'s sost':>8} {'p sost':>8}  bandera"
        )
        print(
            "   (techo = cota SUPERIOR floja, no el blanco cartografico firme; "
            "el juez por rango es p_sostenida)"
        )
        for f in filas:
            col_s = (
                f"{f['s_sostenida']:8.1f}"
                if f["s_sostenida"] == f["s_sostenida"]
                else f"{'nan':>8}"
            )
            col_p = (
                f"{f['p_sostenida']:8.1f}"
                if f["p_sostenida"] == f["p_sostenida"]
                else f"{'nan':>8}"
            )
            print(
                f"   {f['etiqueta']:>8} {f['area_mde_ha']:9.2f} {f['acumulado_ha']:9.2f} "
                f"{f['techo_ha']:9.2f} {col_s} {col_p}  {f['bandera']}"
            )
    return filas


def intersecar(gdf: gpd.GeoDataFrame, otro: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Intersecar con `overlay`: corta `gdf` al solape con `otro`.

    Conserva los atributos de `gdf`; de `otro` solo usa la forma, no trae
    sus columnas. A diferencia de `recortar` (clip), genera una parte por
    cada par de entidades que se solapan.

    `keep_geom_type=True` explícito: dos polígonos que comparten borde intersecan
    también en líneas y puntos, y esas esquirlas de contacto no son solape. Pasarlo
    a mano hace lo mismo que el default, pero sin el UserWarning de geopandas.

    `otro` puede ser una geometría shapely suelta, igual que en `recortar` y
    `borrar`; se asume en el CRS de `gdf`, porque una geometría cruda no
    lleva CRS que comprobar.
    """
    otro = _alinear(gdf, otro)
    if hasattr(otro, "columns"):
        # por nombre real, no por el literal: una capa con la geometría en `geom`
        # daría KeyError aquí, y esta función está dentro de `separacion_media`
        otro_geom = otro[[otro.geometry.name]]
    else:
        # `overlay` solo habla GeoDataFrame; la geometría suelta se envuelve en vez
        # de reventar tres frames abajo con un error que no la menciona
        otro_geom = gpd.GeoDataFrame(geometry=[otro], crs=gdf.crs)
    return gpd.overlay(gdf, otro_geom, how="intersection", keep_geom_type=True)


def union_espacial(
    gdf: gpd.GeoDataFrame,
    otro: gpd.GeoDataFrame,
    como: str = "left",
    predicado: str = "intersects",
) -> gpd.GeoDataFrame:
    """Unión espacial (spatial join): pega los atributos de `otro` a `gdf` según su posición.

    A diferencia de `intersecar`, no corta geometrías: conserva las de `gdf`
    y solo le añade las columnas de `otro` donde se cumple `predicado`
    ('intersects', 'within', 'contains'...). Reproyecta `otro` si tiene otro CRS.

    TRAMPA: si una entidad de `gdf` cumple con DOS de `otro`, sale DOS veces, y
    cualquier suma posterior la cuenta doble. Se avisa con el conteo. Para
    filtrar sin duplicar está `seleccionar_por_ubicacion`.
    """
    otro = _alinear(gdf, otro)
    # indice posicional para contar duplicados de verdad aunque el de `gdf` se
    # repita; al final se le devuelve el suyo
    unido = gpd.sjoin(gdf.reset_index(drop=True), otro, how=como, predicate=predicado)
    if como in ("left", "inner"):
        repetidas = unido.index[unido.index.duplicated()].nunique()
        if repetidas:
            print(
                f"Aviso: {repetidas} entidad(es) cumplen con varias de `otro` y "
                f"salen repetidas: {len(gdf)} -> {len(unido)} filas. Una suma "
                "posterior las cuenta doble; para filtrar sin duplicar, "
                "seleccionar_por_ubicacion"
            )
        unido.index = gdf.index[unido.index]
    # ponytail: sjoin deja el índice de `otro` como columna; sobra.
    return unido.drop(columns="index_right", errors="ignore")


def entidad_a_punto(gdf: gpd.GeoDataFrame, dentro: bool = True) -> gpd.GeoDataFrame:
    """De entidad a punto: un punto por entidad, con sus atributos.

    Con `dentro=True` (default) usa `representative_point()`, que siempre cae
    DENTRO de la geometría y NO es el centroide; con `False`, el `centroid`
    geométrico.

    TRAMPA: el centroide de una U, de una media luna o de un multipolígono cae
    FUERA de la entidad. Un punto de muestreo o una etiqueta que se asigna a
    otra parcela por `union_espacial` da un dato plausible y equivocado sin
    avisar. Por eso `dentro=True` va por defecto: pide `False` solo cuando
    quieras el centro de masa, sepas que puede caer fuera y te dé igual.
    """
    _exigir_crs(gdf, "gdf")
    salida = gdf.copy()
    geoms = salida.geometry
    salida[geoms.name] = geoms.representative_point() if dentro else geoms.centroid
    return salida


def cercano(
    gdf: gpd.GeoDataFrame,
    otro: gpd.GeoDataFrame,
    campo_id: str | None = None,
    max_distancia: float | None = None,
) -> gpd.GeoDataFrame:
    """Añade la distancia a la entidad más próxima de `otro` y su id (análisis de proximidad).

    Columnas nuevas: `DIST_M` (unidades del CRS, m en UTM) e `ID_CERCA` (el
    valor de `campo_id` en `otro`, o su índice si no se da). 10 caracteres o
    menos: sobreviven a un `.shp`. Con `max_distancia`, lo que quede más lejos
    sale con NaN en las dos. Reproyecta `otro` al CRS de `gdf`.

    TRAMPA: cuando hay EMPATE, `sjoin_nearest` devuelve UNA FILA POR EMPATE y
    la capa crece sin avisar (medido: 61 entidades salen como 2413 filas). Aquí sale una fila por entidad de `gdf`, la del
    primer empatado, y un aviso cuenta cuántas entidades tenían empate: su
    `ID_CERCA` es uno de varios igual de cerca, no EL más cercano.

    Falla sin CRS, con CRS geográfico (la distancia saldría en grados), con
    `otro` vacía, con `campo_id` inexistente o con `max_distancia` <= 0.
    """
    otro = _alinear(gdf, otro)
    _exigir_proyectado(gdf, "gdf")
    if len(otro) == 0:
        raise ValueError("`otro` esta vacia: no hay entidad cercana que buscar")
    if campo_id is not None and campo_id not in otro.columns:
        raise ValueError(f"`otro` no tiene el campo '{campo_id}'")
    if max_distancia is not None and max_distancia <= 0:
        raise ValueError(f"max_distancia {max_distancia} debe ser > 0, o None")
    c_dist, c_id = nombre_campo_libre(gdf, "DIST_M"), nombre_campo_libre(gdf, "ID_CERCA")
    ids = otro[campo_id].values if campo_id is not None else otro.index.values
    derecha = gpd.GeoDataFrame({c_id: ids}, geometry=otro.geometry.values, crs=gdf.crs)
    # índice posicional: con un índice de `gdf` repetido, `duplicated` confundiría
    # entidades distintas con empates
    izq = gdf.reset_index(drop=True)
    unido = gpd.sjoin_nearest(
        izq, derecha, how="left", max_distance=max_distancia, distance_col=c_dist
    )
    empatadas = unido.index[unido.index.duplicated()].nunique()
    if empatadas:
        print(
            f"Aviso: {empatadas} entidades con empate de distancia; "
            f"se conserva el primer {c_id} de cada una"
        )
    salida = unido[~unido.index.duplicated(keep="first")].drop(columns="index_right")
    salida.index = gdf.index
    return salida


def puntos_sobre_linea(
    gdf: gpd.GeoDataFrame, intervalo: float, incluir_final: bool = True
) -> gpd.GeoDataFrame:
    """Puntos cada `intervalo` a lo largo de cada línea (cadenamiento).

    Estaciones cada 20 m sobre un camino, por ejemplo. Explota las multilíneas
    primero (`separar_multipartes`), así que cada pieza se cadenea desde su propio 0. Los
    atributos se copian a cada punto y se añaden `CADENA_M` (distancia desde el
    inicio de la pieza) e `ID_LINEA` (número de pieza tras separar_multipartes, desde 0).
    El sentido es el del trazo: invertir la línea invierte las cadenas.

    TRAMPA: con `arange(0, largo, intervalo)` se pierde el punto final, y en
    cadenamiento el final existe (una línea de 50 m cada 20 da 0, 20, 40 y
    50). `incluir_final=True` lo añade si no coincide con el último. Las
    cadenas salen de múltiplos ENTEROS de `intervalo`, no de un float
    acumulado.

    Falla sin CRS, con CRS geográfico, con `intervalo` <= 0 o si alguna
    geometría no es línea.
    """
    _exigir_proyectado(gdf, "gdf")
    if intervalo <= 0:
        raise ValueError(f"intervalo {intervalo} debe ser > 0")
    lineas = separar_multipartes(gdf)
    tipos = set(lineas.geometry.geom_type) - {"LineString"}
    if tipos:
        raise ValueError(f"puntos_sobre_linea espera lineas; la capa trae {tipos}")
    c_cad, c_id = nombre_campo_libre(gdf, "CADENA_M"), nombre_campo_libre(gdf, "ID_LINEA")
    filas: list[int] = []
    cadenas: list[float] = []
    for i, geom in enumerate(lineas.geometry):
        largo = geom.length
        cad = list(np.arange(int(largo // intervalo) + 1) * intervalo)
        if incluir_final and not np.isclose(cad[-1], largo):
            cad.append(largo)
        filas.extend([i] * len(cad))
        cadenas.extend(cad)
    salida = lineas.iloc[filas].reset_index(drop=True)
    col_geom = salida.geometry.name
    salida[col_geom] = salida.geometry.interpolate(cadenas)
    salida[c_cad] = cadenas
    salida[c_id] = filas
    return salida


def identidad(gdf: gpd.GeoDataFrame, otro: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Identidad: `gdf` entera, partida donde la pisa `otro`, con los atributos de los dos.

    `gpd.overlay(how="identity")`. Lo que `otro` no cubre queda con sus
    columnas en NaN. Reproyecta `otro` al CRS de `gdf`. `keep_geom_type=True`
    por lo mismo que en `intersecar`: las esquirlas línea/punto de bordes
    compartidos no son solape.

    TRAMPA: MUEVE la geometría (parte entidades), así que el área y la
    longitud van DESPUÉS (regla 2). Una columna de superficie que viniera de
    antes queda copiada tal cual en cada pedazo: la suma sale inflada y
    parece correcta.
    """
    otro = _alinear(gdf, otro)
    return gpd.overlay(gdf, otro, how="identity", keep_geom_type=True)


def union(gdf: gpd.GeoDataFrame, otro: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Unión (union): todo lo de las dos capas, partido en sus cruces.

    `gpd.overlay(how="union")`. Cada pedazo lleva los atributos de las dos
    capas; donde solo hay una, las columnas de la otra van en NaN. NO es
    `disolver` ni `fusionar`: no funde ni concatena, recorta. Reproyecta
    `otro` al CRS de `gdf`; `keep_geom_type=True` como en `intersecar`.

    TRAMPA: MUEVE la geometría, así que el área va DESPUÉS (regla 2). Una
    superficie heredada se repite en cada pedazo y la suma no cuadra.
    """
    otro = _alinear(gdf, otro)
    return gpd.overlay(gdf, otro, how="union", keep_geom_type=True)


def thiessen(puntos: gpd.GeoDataFrame, zona: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Polígonos de Thiessen (Voronoi): a cada punto, lo que tiene más cerca.

    Una celda por punto, recortada a `zona` y con los atributos de su punto.
    Un punto cuya celda no toca `zona` no sale. Reproyecta `zona` al CRS de
    `puntos`. Parte `zona` exacta: `comprobar_particion([celdas], zona, ...)`
    cierra.

    Gemelo vectorial de `repartir_por_cercania`: el mismo criterio (gana lo
    más cercano) sin malla temporal ni tope de píxeles, pero solo desde
    puntos. Para repartir entre dos POLÍGONOS, aquella.

    TRAMPA 1: `shapely.voronoi_polygons` devuelve las celdas SIN ORDEN respecto
    de los puntos. Pegar atributos por posición da cada celda con los datos
    del vecino, y el mapa se ve perfecto. Aquí se asignan por contención.
    TRAMPA 2: dos puntos en la misma coordenada no tienen frontera entre sí;
    uno se queda sin celda. Levanta ValueError y los nombra.

    Falla sin CRS, con CRS geográfico, con capas vacías o si `puntos` no son
    puntos.
    """
    zona = _alinear(puntos, zona)
    _exigir_proyectado(puntos, "puntos")
    if len(puntos) == 0 or len(zona) == 0:
        raise ValueError("thiessen necesita puntos y zona no vacios")
    tipos = set(puntos.geometry.geom_type) - {"Point"}
    if tipos:
        raise ValueError(f"thiessen espera puntos; la capa trae {tipos}")
    duplicados = puntos.geometry.to_wkb().duplicated(keep=False)
    if duplicados.any():
        dup = puntos[duplicados.values]
        raise ValueError(
            f"puntos duplicados (indice: coordenada): "
            f"{dict(zip(dup.index, dup.geometry.to_wkt()))}"
        )
    limite = unary_union(zona.geometry)
    if len(puntos) == 1:
        celdas = [limite]
    else:
        # extend_to: GEOS amplía al envolvente de los puntos si `limite` es menor
        celdas = list(
            voronoi_polygons(unary_union(puntos.geometry), extend_to=limite).geoms
        )
    capa_celdas = gpd.GeoDataFrame(geometry=celdas, crs=puntos.crs)
    pos = gpd.sjoin(puntos.reset_index(drop=True), capa_celdas, predicate="within")[
        "index_right"
    ]
    if len(pos) != len(puntos) or pos.index.duplicated().any():
        raise ValueError("no se pudo asignar una celda unica a cada punto")
    salida = puntos.copy()
    col_geom = salida.geometry.name
    salida[col_geom] = capa_celdas.geometry.values[pos.sort_index().values]
    salida[col_geom] = salida.geometry.intersection(limite).apply(_dimension_dominante)
    return limpiar_vacias(salida)


def partir_por_linea(
    poligonos: gpd.GeoDataFrame, lineas: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """Parte cada polígono con las líneas que lo cruzan.

    Parte con LÍNEAS, no con polígonos. La alternativa habitual, poligonizar
    bordes y líneas juntos, pierde los atributos y obliga a recuperarlos en un
    paso aparte; aquí cada pieza los conserva.

    Un camino que parte un rodal. Cada polígono se corta con `shapely.ops.split`
    contra la unión de las líneas que lo tocan; cada pieza es una fila con los
    atributos del polígono de origen. Reproyecta `lineas` al CRS de
    `poligonos`. MUEVE la geometría: el área va después (regla 2).

    TRAMPA: una línea que no ATRAVIESA el polígono de borde a borde (entra y se
    queda, o se corta a un metro del lindero por un error de digitalización)
    no lo parte, y `split` no dice nada: el rodal sale entero. Aquí se cuentan
    los polígonos tocados que salieron enteros y se avisa; si el conteo no es
    0, revisa el trazo (un `snap` al borde suele bastar).

    Falla sin CRS o si `lineas` no son líneas.
    """
    lineas = _alinear(poligonos, lineas)
    tipos = set(lineas.geometry.geom_type) - {"LineString", "MultiLineString"}
    if tipos:
        raise ValueError(f"partir_por_linea espera lineas; `lineas` trae {tipos}")
    if len(poligonos) == 0 or len(lineas) == 0:
        return poligonos.copy()
    col_geom = poligonos.geometry.name
    piezas_todas: list[BaseGeometry] = []
    origen: list[int] = []
    enteros = 0
    for i, geom in enumerate(poligonos.geometry.values):
        tocan = lineas.sindex.query(geom, predicate="intersects")
        if len(tocan) == 0:
            piezas_todas.append(geom)
            origen.append(i)
            continue
        partes_antes = len(geom.geoms) if hasattr(geom, "geoms") else 1
        piezas = split(geom, unary_union(lineas.geometry.values[tocan])).geoms
        # solo cuenta si alguna línea ENTRA: un camino que corre por el lindero
        # solo lo toca, no tiene que partirlo, y avisarlo sería ruido
        entra = not all(g.touches(geom) for g in lineas.geometry.values[tocan])
        if len(piezas) <= partes_antes and entra:
            enteros += 1
        piezas_todas.extend(piezas)
        origen.extend([i] * len(piezas))
    if enteros:
        print(
            f"Aviso: {enteros} poligonos tocados por lineas salieron enteros; "
            f"la linea no los atraviesa de borde a borde"
        )
    salida = poligonos.iloc[origen].reset_index(drop=True)
    salida[col_geom] = gpd.GeoSeries(piezas_todas, index=salida.index, crs=poligonos.crs)
    return salida


def calcular_longitud(
    gdf: gpd.GeoDataFrame,
    campo: str | None = None,
    unidad: str = "m",
    decimales: int = _DECIMALES_AREA,
) -> gpd.GeoDataFrame:
    """Añade una columna con la longitud de cada geometría, en la `unidad` pedida.

    `unidad`: "m" o "km". Sin `campo` la columna se llama `longitud_<unidad>`.
    En polígonos es el perímetro. Vale con cualquier CRS, igual que
    `calcular_superficie`, y redondea igual. Devuelve copia.
    """
    factor = _factor_unidad(unidad, _UNIDADES_LONGITUD, "longitud")
    salida = gdf.copy()
    salida[campo or f"longitud_{unidad}"] = (_longitud_m(gdf) / factor).round(decimales)
    return salida


def _gms(valor: float, decimales: int, positivo: str, negativo: str) -> str | None:
    """Grados decimales -> `20°32'29.44" N`. NaN (punto vacío) -> None."""
    if pd.isna(valor):
        return None
    # En enteros de 1/10**decimales de segundo: redondear al final daría 60.00".
    escala = 10**decimales
    n = round(abs(valor) * 3600 * escala)
    grados, resto = divmod(n, 3600 * escala)
    minutos, seg = divmod(resto, 60 * escala)
    letra = positivo if valor >= 0 else negativo
    ancho = 3 + decimales if decimales else 2  # "05.30" o "05": cero a la izquierda
    return f"{grados}°{minutos:02d}'{seg / escala:0{ancho}.{decimales}f}\" {letra}"


def calcular_coordenadas(
    gdf: gpd.GeoDataFrame,
    crs: Any = None,
    formato: str = "decimal",
    decimales: int | None = None,
) -> gpd.GeoDataFrame:
    """Añade las coordenadas de cada punto como columnas, en el `crs` pedido.

    `crs`: el sistema en que se EXPRESAN, sin mover la geometría; sin él, el de
    la capa. Geográfico -> `LAT` y `LON`; proyectado -> `X` y `Y`.
    `formato`: "decimal" (números; `decimales` por defecto 6 en grados, que es
    ~0.1 m, y 2 en metros) o "gms" (texto `20°32'29.44" N` en `LAT_GMS` y
    `LON_GMS`; `decimales` de los segundos, 2 por defecto, ~0.3 m). "gms" solo
    con CRS geográfico. Hemisferios N/S y E/O. Devuelve copia.

    Solo puntos: de una línea o un polígono habría que elegir qué punto, y eso
    es `entidad_a_punto` (centroide o punto interior), no algo que decidir aquí.

    En `.shp`, `°` ocupa 2 bytes en el .dbf: cuenta con ello si fijas `anchos`.
    """
    if formato not in ("decimal", "gms"):
        raise ValueError(f"formato '{formato}': usa 'decimal' o 'gms'")
    _exigir_crs(gdf, "gdf")
    tipos = set(gdf.geom_type.dropna())
    if tipos - {"Point"}:
        raise ValueError(
            f"calcular_coordenadas es solo para puntos y la capa trae {sorted(tipos)}: "
            "pasa antes por entidad_a_punto"
        )
    destino = _a_crs(gdf, crs if crs is not None else gdf.crs, "gdf")
    geografico = destino.crs.is_geographic
    x, y = destino.geometry.x, destino.geometry.y
    salida = gdf.copy()
    if formato == "gms":
        if not geografico:
            raise ValueError(
                f"'gms' es para grados y {destino.crs} es proyectado: pasa crs=4326"
            )
        d = 2 if decimales is None else decimales
        salida["LAT_GMS"] = [_gms(v, d, "N", "S") for v in y]
        salida["LON_GMS"] = [_gms(v, d, "E", "O") for v in x]
    elif geografico:
        d = 6 if decimales is None else decimales
        salida["LAT"], salida["LON"] = y.round(d), x.round(d)
    else:
        d = _DECIMALES_AREA if decimales is None else decimales
        salida["X"], salida["Y"] = x.round(d), y.round(d)
    return salida


def medir_geodesico(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Área (ha) y longitud o perímetro (m) sobre el elipsoide WGS84.

    Añade `SUP_GEO` (ha) y `LONG_GEO` (m, longitud en líneas y perímetro en
    polígonos) con `pyproj.Geod.geometry_area_perimeter`, reproyectando antes
    a geográficas. No depende de la proyección: sirve para comprobar cuánto
    deforma la UTM en un predio grande o lejos del meridiano central. Con
    `geo.MOSTRAR` imprime la diferencia en % contra el cálculo plano del CRS de la
    capa (solo si es proyectado). Devuelve copia.

    Las dos trampas de `pyproj.Geod` (área negativa con anillo horario,
    "perímetro" doble en líneas) las resuelven `_area_m2` y `_longitud_m`.
    """
    area = _area_m2(gdf, geodesica=True)
    largo = _longitud_m(gdf, geodesica=True)
    salida = gdf.copy()
    salida["SUP_GEO"] = (area / 10000).round(_DECIMALES_AREA)
    salida["LONG_GEO"] = largo.round(_DECIMALES_AREA)
    if _mostrar() and not gdf.crs.is_geographic:
        plano_a, plano_l = _area_m2(gdf).sum(), _longitud_m(gdf).sum()
        if area.sum() > 0:
            d = 100 * (plano_a - area.sum()) / area.sum()
            print(
                f"Area plana {plano_a / 10000:.2f} ha, elipsoide "
                f"{area.sum() / 10000:.2f} ha: diferencia {d:+.3f} %"
            )
        if largo.sum() > 0:
            d = 100 * (plano_l - largo.sum()) / largo.sum()
            print(
                f"Longitud plana {plano_l:.2f} m, elipsoide "
                f"{largo.sum():.2f} m: diferencia {d:+.3f} %"
            )
    return salida


def simplificar(
    gdf: gpd.GeoDataFrame, tolerancia: float, conservar_topologia: bool = True
) -> gpd.GeoDataFrame:
    """Simplifica (reduce vértices). Con polígonos simplifica la COBERTURA.

    Con `conservar_topologia=True` (por defecto) y una capa solo de polígonos,
    usa `simplify_coverage`: cada arista compartida entre vecinos se simplifica
    UNA vez, así que los dos lados siguen encajando, y el borde EXTERIOR de la
    capa no se toca (`simplify_boundary=False`): con la capa ya recortada contra
    el predio, moverlo cambiaría la superficie total contra la referencia. Con
    líneas, puntos, mezcla o `conservar_topologia=False`, simplifica cada
    geometría por su cuenta (`simplify` de shapely, Douglas-Peucker). Devuelve
    copia.

    Falla con `ValueError` si la capa de polígonos no es una partición (hay
    solapes o bordes casi coincidentes): `simplify_coverage` no avisa y
    devuelve basura. Mide con `comprobar_particion` y arregla con `borrar`
    o `resolver_por_prioridad`. Si de verdad quieres simplificar cada polígono
    por su cuenta, `conservar_topologia=False`.

    OJO, con polígonos la tolerancia NO es una distancia máxima:
    `simplify_coverage` usa Visvalingam-Whyatt (área). Medido sobre una
    escalera de píxel de 10 m:

        tolerancia   vértices (izq, der)   Hausdorff contra el original
        (entrada)         (14, 13)                 -
           5              (14, 13)               0.00   no quita nada
          10               (8, 7)                6.00
          20               (7, 6)                7.07

    Por debajo del escalón no hace nada; con `tolerancia` = tamaño de píxel
    quita la escalera y el borde se mueve menos que el píxel.
    """
    salida = gdf.copy()
    if len(salida) == 0:
        return salida
    geo = salida.geometry
    tipos = set(geo[~geo.is_empty & geo.notna()].geom_type)
    if conservar_topologia and tipos and tipos <= {"Polygon", "MultiPolygon"}:
        # TODO(shapely 2.2 final, shapely/shapely#2413): la salida de
        # `poligonizar` no es NODED (juntas en T sin vertice) y aqui cae con
        # ValueError (330 de 724 en un predio real). Arreglo medido con 2.2.0rc1:
        #   geoms = shapely.coverage_clean(geo.values, snapping_distance=0.0)
        # antes de `coverage_is_valid`, y simplificar sobre `geoms`. OJO:
        # `coverage_clean` tambien resuelve solapes reales; decidir si siguen
        # dando ValueError (medir solapes antes y limpiar solo el noding).
        # Medido con la rc1: 330 -> 0 invalidos en un predio real, 154 -> 0
        # en otro, 0 m2 movidos, solape 0 tras simplificar.
        # Al hacerlo: `pyproject.toml` a "shapely>=2.2", test de escalera con
        # junta en T en `test_geometria.py`, y devolver `simplificar` a la
        # receta de vegetacion en `docs/`. Revisado 2026-09-28: PyPI sigue en
        # 2.1.2 estable, la rama de lineas no necesita nada.
        geoms = geo.values
        if not shapely.coverage_is_valid(geoms):
            aristas = shapely.coverage_invalid_edges(geoms)
            n = int((~shapely.is_empty(aristas) & ~shapely.is_missing(aristas)).sum())
            raise ValueError(
                f"la capa no es una particion ({n} poligonos con aristas "
                "invalidas: solapes o bordes casi coincidentes). Mide con "
                "comprobar_particion y arregla con borrar o "
                "resolver_por_prioridad; si de verdad quieres simplificar cada "
                "poligono por su cuenta, conservar_topologia=False"
            )
        salida[geo.name] = geo.simplify_coverage(tolerancia, simplify_boundary=False)
        return salida
    salida[geo.name] = geo.simplify(tolerancia, preserve_topology=conservar_topologia)
    return salida


def cerrar_microhuecos(gdf: gpd.GeoDataFrame, distancia: float) -> gpd.GeoDataFrame:
    """Cierra las rendijas ENTRE polígonos vecinos, sin invadir al vecino.

    Hace un closing morfológico (expande y contrae `distancia`, unidades del
    CRS) de la UNIÓN de la capa. Cada pieza nueva que toca 2 o más polígonos es
    una rendija entre vecinos y va al vecino con el que comparte más borde. La
    que toca uno solo (hueco de dentro de un polígono o entrante de su borde)
    no se toca: eso es `rellenar_huecos`. El resto de la capa queda idéntico.
    Devuelve copia reparada.

    NO conoce la zona: una muesca del lindero entre dos clases cuenta como
    rendija y se rellena, saliéndose del predio (décimas de ha con
    `distancia` 10 m). Si la capa ya se recortó, `recortar` otra vez después.

    Sobre la salida de `poligonizar` no hace falta: los vecinos comparten
    arista y no hay rendijas.
    """
    _exigir_proyectado(gdf, "gdf")
    salida = gdf.copy()
    geo = salida.geometry
    geoms = geo.values
    union = shapely.union_all(geoms)
    # junta en inglete: redondeada, el closing mete arcos donde habia esquinas
    cerrada = union.buffer(distancia, join_style="mitre").buffer(
        -distancia, join_style="mitre"
    )
    arbol = shapely.STRtree(geoms)
    destino: dict[int, list[BaseGeometry]] = {}
    for pieza in shapely.get_parts(cerrada.difference(union)):
        vecinos = arbol.query(pieza, predicate="intersects")
        if len(vecinos) < 2:
            continue  # hueco o entrante de un solo poligono: rellenar_huecos
        borde = [pieza.boundary.intersection(geoms[i].boundary).length for i in vecinos]
        destino.setdefault(int(vecinos[int(np.argmax(borde))]), []).append(pieza)
    nuevas = list(geoms)
    for i, piezas in destino.items():
        nuevas[i] = shapely.union_all([geoms[i], *piezas])
    salida[geo.name] = gpd.GeoSeries(nuevas, index=salida.index, crs=salida.crs)
    return reparar_geometrias(salida)


def _sin_anillos(geom: BaseGeometry, area_max: float | None) -> BaseGeometry:
    """Quita los anillos interiores de un polígono o multipolígono."""
    if isinstance(geom, Polygon):
        if not geom.interiors:
            return geom
        # un anillo se CONSERVA si supera el maximo; con None se quitan todos
        quedan = [
            a
            for a in geom.interiors
            if area_max is not None and Polygon(a).area > area_max
        ]
        return Polygon(geom.exterior, quedan)
    if isinstance(geom, MultiPolygon):
        return MultiPolygon([_sin_anillos(p, area_max) for p in geom.geoms])
    return geom  # puntos y lineas no tienen huecos: pasan intactos


def rellenar_huecos(
    gdf: gpd.GeoDataFrame, area_max_ha: float | None = None
) -> gpd.GeoDataFrame:
    """Quita los huecos que quedan DENTRO de cada polígono (los anillos interiores).

    Devuelve una copia; no muta la entrada.

    NO es lo mismo que `cerrar_microhuecos`, y las dos hacen falta:
    `cerrar_microhuecos` cierra las rendijas ENTRE polígonos vecinos con un
    closing morfológico, y no toca un hueco rodeado por un solo polígono.
    Eso es lo que hace esta. Los huecos de dentro salen a puñados de
    `poligonizar`: cada mancha de otra clase encerrada por la clase de al lado
    deja un anillo, y a la escala de trabajo la mayoría están por debajo de la
    UMM y no se cartografían.

    `area_max_ha` es el filtro y es la decisión de campo: se quitan los huecos
    MENORES o iguales a esa superficie y se conservan los mayores. Ponle la UMM
    y la capa queda coherente con el resto de la cadena. Con `None` (default)
    se quitan TODOS, que es lo que hace un borrado de huecos sin
    condición, y hay que usarlo sabiendo que un claro grande y real también
    desaparece.

    Va DESPUÉS de `poligonizar` y antes de `calcular_superficie`: rellenar
    cambia el área, así que medir antes es medir otra cosa (la misma regla que
    `SUP`, que se calcula detrás del último paso que mueva la geometría).
    """
    if area_max_ha is not None and area_max_ha <= 0:
        raise ValueError(f"area_max_ha {area_max_ha} debe ser > 0, o None")
    area_max = None if area_max_ha is None else area_max_ha * 10_000

    if area_max is not None:
        _exigir_proyectado(gdf, "gdf")
    salida = gdf.copy()
    antes = int(shapely.get_num_interior_rings(shapely.get_parts(salida.geometry.values)).sum())
    salida[salida.geometry.name] = [_sin_anillos(g, area_max) for g in salida.geometry]
    if _mostrar():
        despues = int(
            shapely.get_num_interior_rings(shapely.get_parts(salida.geometry.values)).sum()
        )
        limite = "todos" if area_max_ha is None else f"<= {area_max_ha:g} ha"
        print(
            f"rellenar_huecos: {antes - despues} de {antes} huecos rellenados "
            f"({limite}) | quedan {despues}"
        )
    return salida


def _bisectar(
    geom: BaseGeometry,
    area_max_ha: float,
    min_esquirla: float,
    profundidad: int,
    max_prof: int,
) -> list[BaseGeometry]:
    """Parte recursivamente `geom` por el eje más largo hasta cumplir el máximo."""
    if (geom.area / 10000) <= area_max_ha or profundidad >= max_prof:
        return [geom]

    minx, miny, maxx, maxy = geom.bounds
    ancho, alto = maxx - minx, maxy - miny

    if ancho >= alto:
        mid = minx + ancho / 2
        linea = LineString([(mid, miny - 1), (mid, maxy + 1)])
    else:
        mid = miny + alto / 2
        linea = LineString([(minx - 1, mid), (maxx + 1, mid)])

    try:
        partes = split(geom, linea).geoms
    except Exception:
        # ponytail: geometría muy irregular que shapely no parte, se deja igual
        return [geom]

    partes = [p for p in partes if not p.is_empty]
    grandes = [p for p in partes if p.area >= min_esquirla]
    if not grandes:
        return [geom]
    # Las esquirlas no se tiran: se suman a la pieza mayor del mismo corte para
    # que la superficie total se conserve.
    esquirlas = [p for p in partes if p.area < min_esquirla]
    if esquirlas:
        i = max(range(len(grandes)), key=lambda k: grandes[k].area)
        grandes[i] = unary_union([grandes[i], *esquirlas])

    resultado = []
    for parte in grandes:
        resultado.extend(
            _bisectar(parte, area_max_ha, min_esquirla, profundidad + 1, max_prof)
        )
    return resultado


def subdividir_por_area(
    gdf: gpd.GeoDataFrame,
    area_max_ha: float,
    campo_area: str = "superficie_ha",
    min_esquirla: float = 100,
) -> gpd.GeoDataFrame:
    """Parte los polígonos que superen `area_max_ha` por bisección recursiva.

    Cada polígono grande se corta por la mitad de su eje más largo hasta que
    todas las partes cumplen el máximo. Recalcula `campo_area` (en ha) para
    las partes nuevas. `min_esquirla` (m²): las astillas menores se suman a la
    pieza mayor del corte, no se descartan. La recursión para a 8 niveles
    (hasta 256 partes por polígono). Devuelve GeoDataFrame nuevo.

    ESPERA PIEZAS SIMPLES. Razona por FILA, así que una fila multiparte (lo que
    deja `disolver_por_grupo`) se juzga por la suma de sus piezas: un grupo de
    diez manchas de 5 ha entra como una de 50 y se bisecta por un eje que no
    atraviesa ninguna. Pásale `separar_multipartes(gdf)` antes si vienes de un disolver.
    """
    _exigir_proyectado(gdf, "gdf")
    col_geom = gdf.geometry.name
    piezas: list[BaseGeometry] = []
    origen: list[int] = []
    areas: list[float] = []  # NaN = fila que no se partio, conserva su area
    for i, geom in enumerate(gdf.geometry.values):
        if (geom.area / 10000) <= area_max_ha:
            partes = [geom]
            areas.append(float("nan"))
        else:
            partes = _bisectar(geom, area_max_ha, min_esquirla, 0, 8)
            areas.extend(round(p.area / 10000, _DECIMALES_AREA) for p in partes)
        piezas.extend(partes)
        origen.extend([i] * len(partes))
    salida = gdf.iloc[origen].reset_index(drop=True)
    salida[col_geom] = gpd.GeoSeries(piezas, index=salida.index, crs=gdf.crs)
    nuevas = ~np.isnan(np.asarray(areas, dtype=float))
    if nuevas.any():
        salida.loc[nuevas, campo_area] = np.asarray(areas)[nuevas]
    return salida


def eliminar_menores(
    gdf: gpd.GeoDataFrame,
    area_min_ha: float,
    area_max_ha: float,
    campo_area: str = "superficie_ha",
) -> gpd.GeoDataFrame:
    """Eliminar: absorbe los polígonos menores que `area_min_ha` en su mejor vecino.

    Itera mientras haya polígonos bajo el mínimo: cada uno se absorbe en el
    vecino (que toca o intersecta) cuya área sea más cercana a `area_min_ha`,
    siempre que la fusión no supere `area_max_ha`. Los pequeños sin vecino
    válido se conservan tal cual. Para a las 30 pasadas: evita bucles infinitos.
    Devuelve GeoDataFrame nuevo.

    ESPERA PIEZAS SIMPLES, igual que `subdividir_por_area`: `area_min_ha` se
    compara contra la fila, no contra la pieza, así que una fila multiparte con
    veinte manchas diminutas puede superar el mínimo sin que ninguna lo cumpla.
    Pásale `separar_multipartes(gdf)` antes si vienes de un disolver.
    """
    _exigir_proyectado(gdf, "gdf")
    if campo_area not in gdf.columns:
        raise ValueError(f"la capa no tiene el campo '{campo_area}'")
    gdf = gdf.reset_index(drop=True).copy()
    col_geom = gdf.geometry.name

    for _ in range(30):
        pequenos = gdf.index[gdf[campo_area] < area_min_ha].tolist()
        if not pequenos:
            break

        sindex = gdf.sindex
        fusionados = set()

        for idx in pequenos:
            if idx in fusionados or idx not in gdf.index:
                continue

            p = gdf.loc[idx]
            candidatos = [
                c
                for c in sindex.intersection(p[col_geom].bounds)
                if c != idx
                and c not in fusionados
                and c in gdf.index
                and gdf.loc[c, col_geom].intersects(p[col_geom])
                and (gdf.loc[c, campo_area] + p[campo_area]) <= area_max_ha
            ]
            if not candidatos:
                continue

            mejor = min(
                candidatos, key=lambda c: abs(gdf.loc[c, campo_area] - area_min_ha)
            )
            nueva = _dimension_dominante(
                shapely.make_valid(gdf.at[mejor, col_geom].union(p[col_geom]))
            )
            gdf.at[mejor, col_geom] = nueva
            gdf.at[mejor, campo_area] = round(nueva.area / 10000, _DECIMALES_AREA)
            fusionados.add(idx)

        if not fusionados:
            break
        gdf = gdf.drop(index=list(fusionados)).reset_index(drop=True)

    return gdf


# Extremos a menos de esto (en m) son el mismo nodo de la red: absorbe el ruido
# de coordenadas de un .shp o de una reproyeccion, no huecos de dibujo.
_TOL_NODO = 0.01
_SENTIDO_IDA = {"yes", "true"}


def _sentidos(caminos: gpd.GeoDataFrame, campo_sentido: str | None) -> npt.NDArray[np.int_]:
    """Por vía: 1 = solo en el sentido del dibujo, -1 = solo al revés, 0 = los dos."""
    if campo_sentido is None:
        return np.zeros(len(caminos), dtype=int)
    if campo_sentido in caminos.columns:
        valores = caminos[campo_sentido]
    elif "other_tags" in caminos.columns:
        # el driver OSM de GDAL deja las etiquetas sin columna propia en
        # `other_tags`, como texto: "oneway"=>"yes","surface"=>"asphalt"
        patron = rf'"{re.escape(campo_sentido)}"=>"([^"]*)"'
        valores = caminos["other_tags"].astype("string").str.extract(patron)[0]
    else:
        campos = [c for c in caminos.columns if c != caminos.geometry.name]
        raise ValueError(
            f"caminos no tiene el campo '{campo_sentido}' (ni 'other_tags' de OSM "
            f"donde buscarlo). Campos: {campos}"
        )
    v = valores.astype("string").str.strip().str.lower().fillna("")
    # una columna numerica llega como "1.0": se compara el numero, no el texto
    num = pd.to_numeric(v, errors="coerce").to_numpy(dtype=float, na_value=np.nan)
    ida = v.isin(_SENTIDO_IDA).to_numpy(dtype=bool) | (num == 1)
    return np.where(ida, 1, np.where(num == -1, -1, 0))


class _Red:
    """La red ya armada: tramos rectos vértice a vértice y el grafo dirigido.

    Por tramo (u -> v en el sentido del dibujo): `metros`, `segundos` y si se
    puede recorrer `ida` (u -> v) y `vuelta` (v -> u). `grafo` y `largo` son
    csr de nodo a nodo; con aristas paralelas se queda la más rápida.
    """

    def __init__(
        self,
        caminos: gpd.GeoDataFrame,
        velocidades: float | dict[Any, float],
        campo: str | None,
        campo_sentido: str | None,
        nodar: bool,
    ) -> None:
        _exigir_proyectado(caminos, "caminos")
        tipos = set(caminos.geom_type.dropna())
        if not tipos or not tipos <= {"LineString", "MultiLineString"}:
            raise ValueError(f"caminos debe ser una capa de líneas; trae {sorted(tipos)}")
        self.crs, self.campo_sentido = caminos.crs, campo_sentido

        if isinstance(velocidades, dict):
            if campo is None:
                raise ValueError(
                    "`velocidades` es un dict por tipo de vía: falta `campo`, la columna "
                    "que dice el tipo de cada vía"
                )
            if campo not in caminos.columns:
                raise ValueError(f"caminos no tiene el campo '{campo}'")
            kmh = caminos[campo].map(velocidades)
            faltan = caminos.loc[kmh.isna(), campo].value_counts(dropna=False)
            if len(faltan):
                lista = ", ".join(f"{k!r} ({n} vías)" for k, n in faltan.head(8).items())
                raise ValueError(
                    f"sin velocidad para estos valores de '{campo}': {lista}"
                    f"{' y otros' if len(faltan) > 8 else ''}. Añádelos a `velocidades`, o "
                    f"quita esas vías antes: caminos[caminos['{campo}'].isin(velocidades)]. "
                    "Ojo con el tipo: '1' (texto) no es 1 (número)"
                )
        else:
            kmh = pd.Series(velocidades, index=caminos.index)
        kmh = kmh.to_numpy(dtype=float)
        if not (kmh > 0).all():
            raise ValueError(
                "todas las velocidades deben ser > 0 km/h; una vía cerrada se quita de la capa"
            )

        lineas = gpd.GeoDataFrame(
            {"kmh": kmh, "sentido": _sentidos(caminos, campo_sentido)},
            geometry=caminos.geometry.values,
            crs=caminos.crs,
        ).explode(index_parts=False)
        lineas = lineas[~lineas.geometry.is_empty]
        geoms = lineas.geometry.values
        kmh, sentido = lineas["kmh"].to_numpy(), lineas["sentido"].to_numpy()

        if nodar:
            piezas = shapely.get_parts(shapely.node(shapely.multilinestrings(geoms)))
            # cada pieza hereda de la línea original sobre la que cae
            medio = shapely.line_interpolate_point(piezas, 0.5, normalized=True)
            de_pieza, cual = lineas.sindex.nearest(medio)
            cual = cual[np.unique(de_pieza, return_index=True)[1]]
            # el nodado puede invertir el sentido del dibujo: sin esto, un
            # sentido único quedaría al revés
            a = shapely.line_locate_point(geoms[cual], shapely.get_point(piezas, 0))
            b = shapely.line_locate_point(geoms[cual], shapely.get_point(piezas, -1))
            geoms, kmh = piezas, kmh[cual]
            sentido = np.where(b < a, -sentido[cual], sentido[cual])

        coords, de_linea = shapely.get_coordinates(geoms, return_index=True)
        sigue = de_linea[1:] == de_linea[:-1]
        a, b, de_linea = coords[:-1][sigue], coords[1:][sigue], de_linea[:-1][sigue]
        # por distancia y no redondeando a una reja de 1 cm: la reja separaria
        # extremos a 2 mm que caen a lados distintos de una de sus lineas.
        # ponytail: el grupo encadena, una linea con vertices a menos de 1 cm
        # entre si colapsa en un nodo; partir por radio si aparece ese dibujo
        extremos = np.vstack([a, b])
        pares = cKDTree(extremos).query_pairs(_TOL_NODO, output_type="ndarray")
        unidos = csr_array(
            (np.ones(len(pares)), (pares[:, 0], pares[:, 1])), shape=(len(extremos),) * 2
        )
        _, idx = connected_components(unidos, directed=False)
        self.xy = np.zeros((idx.max() + 1, 2))
        self.xy[idx] = extremos
        u, v = idx[: len(a)], idx[len(a) :]
        util = u != v
        self.u, self.v, de_linea = u[util], v[util], de_linea[util]
        self.metros = np.hypot(*(self.xy[self.v] - self.xy[self.u]).T)
        self.segundos = self.metros * 3.6 / kmh[de_linea]
        self.ida, self.vuelta = sentido[de_linea] >= 0, sentido[de_linea] <= 0

        ida, vuelta = self.ida, self.vuelta
        fi = np.r_[self.u[ida], self.v[vuelta]]
        co = np.r_[self.v[ida], self.u[vuelta]]
        t = np.r_[self.segundos[ida], self.segundos[vuelta]]
        m = np.r_[self.metros[ida], self.metros[vuelta]]
        # csr suma los duplicados: de cada par de nodos se queda la arista más rápida
        par = fi * len(self.xy) + co
        orden = np.lexsort((t, par))
        k = orden[np.unique(par[orden], return_index=True)[1]]
        forma = (len(self.xy), len(self.xy))
        self.grafo = csr_array((t[k], (fi[k], co[k])), shape=forma)
        self.largo = csr_array((m[k], (fi[k], co[k])), shape=forma)

    def enganchar(
        self, puntos: gpd.GeoDataFrame
    ) -> tuple[npt.NDArray[np.int_], npt.NDArray[np.float64]]:
        """Vértice más cercano a cada punto, y a cuántos metros queda."""
        puntos = _a_crs(puntos, self.crs, "puntos")
        xy_p = puntos.get_coordinates().to_numpy()
        distancia, cerca = cKDTree(self.xy).query(xy_p)
        return cerca, distancia


def _exigir_puntos(puntos: gpd.GeoDataFrame, nombre: str, cuantos: int | None) -> None:
    """Capa de puntos con CRS; con `cuantos`, exactamente esa cantidad."""
    _exigir_crs(puntos, nombre)
    solo_puntos = len(puntos) and (puntos.geom_type == "Point").all()
    if not solo_puntos or (cuantos is not None and len(puntos) != cuantos):
        que = f"exactamente {cuantos} puntos" if cuantos else "al menos un punto"
        raise ValueError(
            f"`{nombre}` debe tener {que}; llegaron {len(puntos)} geometrías de "
            f"tipo {sorted(set(puntos.geom_type))}"
        )


def ruta_red(
    caminos: gpd.GeoDataFrame,
    puntos: gpd.GeoDataFrame,
    velocidades: float | dict[Any, float],
    campo: str | None = None,
    campo_sentido: str | None = None,
    nodar: bool = False,
) -> gpd.GeoDataFrame:
    """Ruta más rápida entre dos puntos sobre una red de líneas (caminos, calles).

    La pareja vectorial de `ruta_costo_minimo`: aquí solo se va por las líneas.
    Cada línea se parte vértice a vértice; dos líneas se conectan donde
    COMPARTEN un vértice (dos vértices a menos de 1 cm son el mismo). Peso de
    cada tramo = metros / km/h de su tipo. Dijkstra de scipy, sin dependencias
    nuevas.

    `puntos` : exactamente dos puntos, origen y destino EN ESE ORDEN. Cada uno
        se engancha al vértice de la red más cercano; la distancia de enganche
        se imprime. Si está en otro CRS se reproyecta al de `caminos`.
    `velocidades` : km/h. Un número vale para todas las vías; un dict
        {valor de `campo`: km/h} da una por tipo. Las velocidades deciden la
        RUTA, no solo los minutos: en una ciudad real (OSM) bajar las residenciales de
        25 a 20 km/h cambió el 93 % del trazado de ida. Una vía cerrada se
        quita de la capa, no se pone a 0.
    `campo` : la columna con el tipo de vía, si `velocidades` es dict.
    `campo_sentido` : columna de sentido único. 'yes'/'true'/'1' = solo en el
        sentido en que está dibujada la línea, '-1' = solo al revés, cualquier
        otra cosa = los dos. Si la columna no existe y la capa viene de un .osm
        o .pbf (`cargar(..., capa="lines")`), se busca en `other_tags`.
    `nodar` : False (default) conecta solo en vértices compartidos: es lo
        correcto con OSM y redes limpias, y un puente no se cruza con lo que
        pasa por debajo. True parte las líneas en cada cruce, para capas de CAD
        donde los cruces no comparten vértice; también convierte los puentes en
        cruces.

    Falla claro si `caminos` no es de líneas o está en grados, si un tipo de
    vía no tiene velocidad (dice cuáles y cuántas vías), si falta `campo` para
    el dict, o si no hay camino: dice si la red está partida (en cuántas
    piezas, y de cuántos km la del origen y la del destino) o si lo impiden
    los sentidos únicos.

    Devuelve una capa con una línea por los vértices de la red y los campos
    `METROS` y `MINUTOS`. La línea empieza y acaba en los vértices enganchados,
    no en los puntos exactos.
    """
    red = _Red(caminos, velocidades, campo, campo_sentido, nodar)
    _exigir_puntos(puntos, "puntos", 2)
    (o, d), enganche = red.enganchar(puntos)
    if o == d:
        raise ValueError(
            f"origen y destino se enganchan al mismo vértice de la red (a {enganche[0]:.0f} m "
            f"y {enganche[1]:.0f} m): no hay ruta que trazar"
        )
    tiempo, previo = dijkstra(red.grafo, indices=o, return_predecessors=True)
    if not np.isfinite(tiempo[d]):
        n, pieza = connected_components(red.grafo, connection="weak")
        if pieza[o] == pieza[d]:
            raise ValueError(
                f"no hay camino respetando los sentidos únicos de '{campo_sentido}': sin "
                "ellos sí lo hay. Revisa el sentido de las vías cerca del origen y del "
                f"destino, o los valores de '{campo_sentido}' ('yes'/'true'/'1' = sentido "
                "del dibujo, '-1' = al revés)"
            )
        km = np.bincount(pieza[red.u], weights=red.metros, minlength=n) / 1000
        raise ValueError(
            f"no hay camino: la red está partida en {n} piezas que no se tocan. El origen "
            f"cae en una de {km[pieza[o]]:.1f} km y el destino en otra de "
            f"{km[pieza[d]]:.1f} km (la mayor tiene {km.max():.1f} de {km.sum():.1f} km). "
            "Suele ser: cruces dibujados sin vértice común (prueba nodar=True), líneas "
            "que casi se tocan pero no, o un punto enganchado a un camino suelto "
            f"(enganches a {enganche[0]:.0f} m y {enganche[1]:.0f} m de la red)"
        )
    camino = [int(d)]
    while camino[-1] != o:
        camino.append(int(previo[camino[-1]]))
    camino = camino[::-1]
    total_m = float(red.largo[camino[:-1], camino[1:]].sum())
    minutos = float(tiempo[d]) / 60
    if _mostrar():
        print(
            f"ruta_red: {total_m:,.0f} m, {minutos:.1f} min | red de {red.metros.sum() / 1000:.1f} km, "
            f"{len(red.xy)} vértices | enganche a {enganche[0]:.0f} m y {enganche[1]:.0f} m"
        )
    return gpd.GeoDataFrame(
        {"METROS": [total_m], "MINUTOS": [minutos]},
        geometry=[LineString(red.xy[camino])],
        crs=red.crs,
    )


def isocronas(
    caminos: gpd.GeoDataFrame,
    origenes: gpd.GeoDataFrame,
    velocidades: float | dict[Any, float],
    cortes: Iterable[float],
    campo: str | None = None,
    campo_sentido: str | None = None,
    nodar: bool = False,
    hacia: bool = False,
    ancho: float | None = None,
) -> gpd.GeoDataFrame:
    """Lo que se alcanza por la red en menos de cada corte de tiempo (isócronas).

    La misma red que `ruta_red` (mismos `velocidades`, `campo`,
    `campo_sentido` y `nodar`), con un Dijkstra sin destino: el tiempo desde
    el origen a cada vértice. Los tramos se cortan a media calle donde cae el
    corte: si una calle se recorre en 60 s y se entra a los 9:30 de un corte de
    10 min, la banda llega hasta la mitad.

    `origenes` : uno o varios puntos. Con varios, cada tramo toma el tiempo del
        origen MÁS CERCANO en tiempo (p. ej. la base de brigada más próxima).
        Cada uno se engancha al vértice más cercano; se imprime a cuánto.
    `cortes` : minutos, crecientes, p. ej. [5, 10, 15] -> bandas 0-5, 5-10, 10-15.
    `hacia` : False (default) = tiempo SALIENDO de los orígenes (una brigada
        que sale de la base). True = tiempo LLEGANDO a ellos (un herido que va
        al hospital). Con sentidos únicos no da lo mismo.
    `ancho` : None (default) devuelve LÍNEAS: la red real, sin inventar nada.
        Con un número (m), polígonos: la zona a menos de `ancho` m de los
        tramos de cada banda, sin solaparse (cada sitio va en la banda más
        rápida que lo alcanza). Es un supuesto que se declara, p. ej. "servido
        a pie hasta 100 m de la calle"; no sale de la red. Sin envolvente
        cóncava a propósito: su parámetro de concavidad no sale de ningún dato
        y mueve la mancha.

    Lo que pasa de una vía a más de `ancho` m (o fuera de la red) no aparece:
    para un rodal sin camino, combina con `ruta_a_pie`. Las velocidades mueven
    todo el borde de cada banda: ver la trampa de `ruta_red`.

    Devuelve una fila por banda: `MIN_DESDE`, `MIN_HASTA`, `KM` (km de red en
    la banda) y la geometría (vacía si la banda no alcanza nada).
    """
    cortes = [float(c) for c in cortes]
    if not cortes or cortes[0] <= 0 or any(b <= a for a, b in zip(cortes, cortes[1:])):
        raise ValueError(f"`cortes` deben ser minutos > 0 y crecientes; llegó {cortes}")
    if ancho is not None and ancho <= 0:
        raise ValueError(f"ancho {ancho} debe ser > 0 m, o None para líneas")
    red = _Red(caminos, velocidades, campo, campo_sentido, nodar)
    _exigir_puntos(origenes, "origenes", None)
    cerca, enganche = red.enganchar(origenes)

    tope = cortes[-1] * 60
    grafo = red.grafo.T.tocsr() if hacia else red.grafo
    t = dijkstra(grafo, indices=np.unique(cerca), min_only=True, limit=tope)
    # Tiempo en un punto a s metros de `u` por el tramo u-v: el mejor de
    # entrar por u (t_u + s/L * seg) o por v (t_v + (L-s)/L * seg), cada uno
    # solo si el sentido deja recorrer el tramo en esa dirección.
    por_u = red.vuelta if hacia else red.ida
    por_v = red.ida if hacia else red.vuelta
    t_u = np.where(por_u, t[red.u], np.inf)
    t_v = np.where(por_v, t[red.v], np.inf)
    a, b, seg = red.xy[red.u], red.xy[red.v], red.segundos

    def hasta(corte: float) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
        """Fracción del tramo alcanzada desde u y desde v antes de `corte`."""
        return (
            np.clip((corte - t_u) / seg, 0, 1),
            np.clip((corte - t_v) / seg, 0, 1),
        )

    filas, geoms, previo = [], [], (np.zeros(len(seg)), np.zeros(len(seg)))
    for desde, corte in zip([0.0, *cortes[:-1]], cortes):
        fu, fv = hasta(corte * 60)
        pu, pv = previo
        # lo alcanzado a este corte menos lo alcanzado al anterior: un trozo
        # que avanza desde u y otro desde v. Si se juntan, cubren el tramo
        lleno = fu + fv >= 1
        fu, fv = np.where(lleno, 1 - pv, fu), np.where(lleno, 0, fv)
        trozos = []
        for f0, f1, desde_v in ((pu, fu, False), (pv, fv, True)):
            hay = f1 > f0 + 1e-9
            ini, fin = (a, b) if not desde_v else (b, a)
            p0 = ini[hay] + (fin[hay] - ini[hay]) * f0[hay, None]
            p1 = ini[hay] + (fin[hay] - ini[hay]) * f1[hay, None]
            trozos.append(shapely.linestrings(np.stack([p0, p1], axis=1)))
        trozos = np.concatenate(trozos)
        linea = shapely.line_merge(shapely.multilinestrings(trozos)) if len(trozos) else None
        filas.append({"MIN_DESDE": desde, "MIN_HASTA": corte, "KM": shapely.length(trozos).sum() / 1000})
        geoms.append(linea if linea is not None else LineString())
        previo = (np.maximum(pu, fu), np.maximum(pv, fv))

    if ancho is not None:
        hecho: BaseGeometry = Polygon()
        poligonos = []
        for g in geoms:
            zona = shapely.union(hecho, g.buffer(ancho)) if not g.is_empty else hecho
            poligonos.append(shapely.difference(zona, hecho))
            hecho = zona
        geoms = poligonos

    if _mostrar():
        bandas = ", ".join(f"{f['MIN_DESDE']:g}-{f['MIN_HASTA']:g} min {f['KM']:.1f} km" for f in filas)
        print(
            f"isocronas: {bandas} | red de {red.metros.sum() / 1000:.1f} km | {len(cerca)} origen(es), "
            f"enganche máximo {enganche.max():.0f} m"
        )
    return gpd.GeoDataFrame(filas, geometry=geoms, crs=red.crs)
