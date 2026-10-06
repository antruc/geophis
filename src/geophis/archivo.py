"""Entrada y salida de capas (leer/escribir archivos vectoriales)."""

import os
import re
import shutil
import tempfile
import warnings
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from os import PathLike
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, MultiLineString
from shapely.geometry.base import BaseGeometry

from .metrologia import _paso_escalera
from .proyeccion import _UNIDADES_AREA, _UNIDADES_LONGITUD, _exigir_crs
from ._salida import _callado, _mostrar

# Formatos que exigen WGS84 lon/lat (EPSG:4326) y driver propio de GDAL.
# GPX y KML/KMZ solo hablan geográficas; hay que reproyectar al exportar.
_DRIVERS_GEO = {".gpx": "GPX", ".kml": "LIBKML", ".kmz": "LIBKML"}

# Formatos de tabla: solo atributos, sin geometría.
_EXT_TABLA = {".csv", ".xlsx"}

# Columnas que guardan área (calcular_superficie y sus recortes SHP a 10 chars).
_COLS_AREA = {
    "SUP",
    "SUPERFICIE_HA",
    "SUPERFICIE_M2",
    "SUPERFICIE_KM2",
    "SUPERFICIE",
    "AREA",
    "AREA_HA",
}
# Las que escribe `calcular_longitud` mas las abreviadas por el SHP a 10
# caracteres, que es como llegan de vuelta al cargar la capa guardada.
_COLS_LONGITUD = {
    "LONGITUD_M",
    "LONGITUD_KM",
    "LONGITUD_K",
    "LONGITUD",
    "LARGO",
    "LARGO_M",
    "PERIMETRO",
}
# calcular_superficie redondea a 2 decimales, así que la columna guardada puede
# diferir hasta 0.005 de la geometría viva sin estar obsoleta. Sin esta holgura
# absoluta, la tolerancia relativa (0.1%) daría aviso falso en polígonos < 5 ha.
_TOL_REDONDEO = 0.005

# Avisos de GDAL/pyogrio (en inglés) traducidos: fragmento a buscar -> texto ES.
#
# Ninguna traducción nombra un FORMATO que el aviso no nombre: los avisos de
# GDAL son genéricos, y nombrar otro formato manda a revisar el esquema de uno
# que no se está usando. El formato está en la extensión que se pidió.
_AVISOS_ES = {
    "Column names longer than 10 characters": "el formato SHP recorta los nombres de columna a 10 caracteres",
    "created as String field": "un campo de fecha/hora se guardó como texto (limitación del formato)",
    "Normalized/laundered field name": "el formato renombró un campo para ajustarlo a su esquema",
    "More than one layer found": "el archivo tiene varias capas; se leyó la predeterminada (usa capa= para elegir otra)",
}


@contextmanager
def _traducir_avisos() -> Iterator[None]:
    """Captura avisos de GDAL/pyogrio y los reemite en español.

    Los conocidos salen como 'Aviso: ...'; los desconocidos se reemiten tal cual
    para no ocultar nada nuevo.

    El reemitido va en `finally` y FUERA del `catch_warnings`. Fuera del
    `finally`, un cuerpo que lanza se lleva por delante los avisos
    capturados, que es justo cuando explican el fallo (`cargar_tracks` traduce
    la excepción de GDAL y el aviso que la acompaña se perdería). Y fuera
    del `catch_warnings` porque reemitir dentro volvería a capturar en la misma
    lista que se está recorriendo, o sea un bucle infinito.

    **La traducción conserva el DETALLE del aviso original**, que es lo que hay
    detrás de los dos puntos: los nombres, las capas, la cifra. Sin él, GDAL
    diciendo `Normalized/laundered field name: 'superficie_ha' to 'superfic_1'`
    saldría como "renombró un campo", sin decir cuál ni a qué, y ahí el nombre
    nuevo es todo el contenido, porque es el que hay que buscar en la tabla
    guardada. Un aviso sin la cifra no es una guarda.
    """
    capturados: list[warnings.WarningMessage] = []
    try:
        with warnings.catch_warnings(record=True) as registro:
            warnings.simplefilter("always")
            capturados = registro
            yield
    finally:
        for a in capturados:
            texto = str(a.message)
            es = next((v for k, v in _AVISOS_ES.items() if k in texto), None)
            if es:
                # GDAL pone el dato detrás de los dos puntos; los avisos que no
                # llevan ninguno (el de los 10 caracteres) no pierden nada.
                detalle = texto.split(":", 1)[1].strip() if ":" in texto else ""
                print(f"Aviso: {es}" + (f": {detalle}" if detalle else ""))
            else:
                warnings.warn(a.message, a.category)


@contextmanager
def _env(var: str, valor: str) -> Iterator[None]:
    """Fija una variable de entorno solo durante el bloque y luego la restaura."""
    previo = os.environ.get(var)
    os.environ[var] = valor
    try:
        yield
    finally:
        if previo is None:
            del os.environ[var]
        else:
            os.environ[var] = previo


def cargar(
    ruta: str | PathLike[str],
    capa: str | None = None,
    bbox: Any = None,
    crs: Any = None,
) -> gpd.GeoDataFrame:
    """Carga una capa vectorial (.shp, .gpkg, .geojson, .gpx, .dxf...) como GeoDataFrame.

    Parámetros
    ----------
    ruta : str
        Ruta al archivo.
    capa : str, opcional
        Nombre de la capa a leer. Necesario en formatos multicapa: GPX
        (waypoints/routes/tracks) y GeoPackage con varias capas. Si es None
        lee la primera/única.
    bbox : capa, geometría o (minx, miny, maxx, maxy), opcional
        Lee SOLO las entidades que tocan esa ventana, usando el índice
        espacial del archivo. No es un recorte: devuelve las entidades
        enteras que la tocan, así que el `recortar` sigue haciendo falta si
        quieres la geometría cortada.

    crs : EPSG, cadena u objeto CRS, opcional
        CRS de las coordenadas del archivo cuando éste no lo trae (DXF, un
        .shp sin .prj). Se ASIGNA con `set_crs`, no se reproyecta. Si el
        archivo no trae CRS y no se pasa, falla: una capa sin CRS se cruza
        con cualquier otra sin error y cae en otro sitio.

    **DXF (CAD).** Nunca trae CRS, así que `crs=` es obligatorio. La capa de
    CAD viene en la columna `Layer`, y es por donde se filtra:
    `seleccionar_por_atributo(gdf, "Layer == 'curvas'")`. Un DXF mezcla puntos, líneas y
    polígonos en la misma tabla; si pasa, se avisa, y se separa con
    `gdf[gdf.geom_type == "LineString"]` (`seleccionar_por_atributo` no ve la geometría).

    **`bbox` es contra la MEMORIA, no comodidad.** La cartografía de trabajo
    aquí es estatal: la hidrográfica del estado son 384 554 tramos y 133 MB, y
    las curvas de nivel 1 GB. Cargarlas enteras para luego recortar a un predio
    de 1765 ha cuesta lo que se midió al escribir esto:

        sin bbox   33.1 s   763 MB RSS   384 554 filas
        con bbox    7.1 s   164 MB RSS       297 filas

    y en las curvas es peor. No es solo lento: sin recorte previo, un buffer
    sobre la multilínea completa de cada nivel agota la memoria y el sistema
    mata el proceso sin traza. `recortar` no sirve para esto porque para
    recortar hay que haber cargado.

    El CRS lo alinea GDAL: `bbox` puede venir en otro sistema que el archivo y
    la ventana se reproyecta, no las entidades.

    Devuelve
    --------
    GeoDataFrame
    """
    if not os.path.exists(ruta):
        raise FileNotFoundError(f"no existe la ruta '{ruta}'")
    with _traducir_avisos():
        gdf = gpd.read_file(ruta, layer=capa, bbox=bbox)
    # De donde salio la capa, para que `derivar_parametros` pueda reportarlo sin
    # que nadie lo teclee. Es BEST EFFORT: `.attrs` de pandas no sobrevive a
    # todas las operaciones, asi que quien lo lee no puede depender de que este.
    # La huella geometrica (n, bbox, longitud) es la parte que no se pierde.
    gdf.attrs["ruta"] = str(ruta)
    if crs is not None:
        if gdf.crs is not None and not gdf.crs.equals(crs):
            raise ValueError(
                f"'{ruta}' ya trae CRS {gdf.crs} y se pidio crs={crs}; "
                "crs= asigna, no reproyecta: usa reproyectar despues de cargar"
            )
        gdf = gdf.set_crs(crs, allow_override=True)
    elif gdf.crs is None:
        raise ValueError(
            f"'{ruta}' no trae CRS (un DXF nunca lo trae, un .shp sin .prj "
            "tampoco): pasa crs=, p. ej. cargar(ruta, crs=32613)"
        )
    # Polygon con MultiPolygon es normal (un .shp no los distingue): solo se
    # avisa si se mezclan FAMILIAS, y el filtro sugerido es el de la mayoritaria
    familia = gdf.geom_type.dropna().str.replace("Multi", "", regex=False)
    if familia.nunique() > 1:
        mayor = familia.value_counts().index[0]
        tipos = [mayor, "Multi" + mayor]
        print(
            f"Aviso: '{ruta}' mezcla geometrias {sorted(set(gdf.geom_type.dropna()))}; "
            f"separa con gdf[gdf.geom_type.isin({tipos})]"
        )
    if _mostrar():
        print(f"Cargado '{ruta}': {len(gdf)} entidades | CRS: {gdf.crs}")
    if len(gdf) == 0:
        print(f"Aviso: '{ruta}' no tiene entidades")
    return gdf


def cargar_puntos(
    ruta: str | PathLike[str],
    x: str = "x",
    y: str = "y",
    epsg: int | None = None,
    orden: str | None = None,
) -> gpd.GeoDataFrame:
    """Carga un CSV o Excel con columnas de coordenadas como capa de puntos.

    Parámetros
    ----------
    x, y : str
        Nombres de las columnas con la coordenada X (este/lon) e Y (norte/lat).
    epsg : int, opcional
        CRS de esas coordenadas (p.ej. 32613 para UTM 13N, 4326 para lon/lat).
        El archivo de texto no lo trae, así que se define aquí. Sin él la capa
        queda sin CRS y no podrás reproyectar ni medir áreas.
    orden : str, opcional
        Columna de secuencia por la que ordenar las filas antes de armar la
        capa. Si es None se respeta el orden del archivo. Solo importa si luego
        conviertes los puntos a línea o polígono.

    Devuelve
    --------
    GeoDataFrame de puntos con todas las columnas del archivo como atributos.
    """
    if not os.path.exists(ruta):
        raise FileNotFoundError(f"no existe la ruta '{ruta}'")
    ext = os.path.splitext(str(ruta))[1].lower()
    leer = pd.read_excel if ext in (".xlsx", ".xls") else pd.read_csv
    df = leer(ruta)
    for col in (x, y):
        if col not in df.columns:
            raise ValueError(
                f"el archivo no tiene la columna '{col}'; columnas: {list(df.columns)}"
            )
    if orden is not None:
        if orden not in df.columns:
            raise ValueError(f"el archivo no tiene la columna de orden '{orden}'")
        df = df.sort_values(orden)
    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df[x], df[y]),
        crs=f"EPSG:{epsg}" if epsg else None,
    )
    if _mostrar():
        print(f"Cargado '{ruta}': {len(gdf)} puntos | CRS: {gdf.crs}")
    if epsg is None:
        print(
            "Aviso: capa sin CRS (no se pasó epsg); no podrás reproyectar ni "
            "medir áreas. Ej.: epsg=32613 para UTM 13N"
        )
    if len(gdf) == 0:
        print(f"Aviso: '{ruta}' no tiene filas")
    return gdf


def _avisar_medida_obsoleta(gdf: gpd.GeoDataFrame) -> None:
    """Avisa si una columna de área o de longitud ya no cuadra con la geometría.

    El área guardada como columna es una instantánea, y **cualquier** operación
    posterior que toque la geometría la deja obsoleta. La regla no es "calcúlala
    después de recortar y reproyectar": es "calcúlala después del ÚLTIMO paso que
    mueve la geometría", y esos son más de los que se enumeran solos:

        recortar, reproyectar, disolver, simplificar, reparar_geometrias,
        cerrar_microhuecos, zona_de_influencia, subdividir_por_area, eliminar_menores,
        borrar y resolver_por_prioridad

    Las dos últimas son las que muerden de verdad, porque van al FINAL de la
    cadena de pendientes, después del recorte: `resolver_por_prioridad` recorta
    cada capa contra la unión de las anteriores, así que las clases que ceden
    quedan con menos geometría y el mismo `SUP`: calcular el área justo detrás
    del clip parece correcto y no lo es.

    Compara contra la medida viva en cada unidad en que se pudo guardar
    (`_UNIDADES_AREA` / `_UNIDADES_LONGITUD`), porque no sabemos en cuál se
    guardó, y solo avisa si no cuadra con ninguna. Pide CRS proyectado: en geográficas
    ni el área significa m² ni la longitud metros.

    Cubre las dos medidas, y la de LONGITUD no es simetría de API: en la rama
    hidrológica es la medida que SE CITA: la densidad de drenaje es km de cauce entre km² de
    cuenca, y `calcular_longitud` seguido de `recortar` la deja obsoleta.

    `.length` sirve para las dos geometrías y las dos son legítimas: en líneas
    es la longitud, en polígonos el perímetro. Por eso la de longitud no filtra
    por tipo y la de área sí.
    """
    if gdf.crs is None or gdf.crs.is_geographic:
        return
    # a metros: con un CRS en pies la medida viva sale en ft y nunca cuadraría
    k = gdf.crs.axis_info[0].unit_conversion_factor
    tipos = set(gdf.geom_type.dropna())
    poligonal = bool(tipos & {"Polygon", "MultiPolygon"})
    # Una capa de PUNTOS no tiene largo: ahi `LONGITUD` es la coordenada
    # geografica (el CSV de campo con LATITUD/LONGITUD), no una medida, y
    # compararla contra un largo de 0 daría aviso falso en cada `guardar`.
    # ponytail: una capa de lineas con la coordenada en `LONGITUD` sigue dando
    # aviso falso; separarlo pediria mirar el rango de valores.
    medidas = (
        []
        if tipos <= {"Point", "MultiPoint"}
        else [(gdf.geometry.length * k, _COLS_LONGITUD, _UNIDADES_LONGITUD, "la longitud")]
    )
    if poligonal:
        medidas.insert(0, (gdf.geometry.area * k**2, _COLS_AREA, _UNIDADES_AREA, "el área"))

    for viva, columnas, unidades, que in medidas:
        for col in gdf.columns:
            if str(col).upper() not in columnas:
                continue
            guardada = pd.to_numeric(gdf[col], errors="coerce")
            if guardada.isna().all():
                continue
            # solo las filas con dato: NaN compara False y haría fallar .all()
            ok = guardada.notna()
            cuadra = any(
                (
                    (guardada - viva / f).abs() <= (viva / f).abs() * 1e-3 + _TOL_REDONDEO
                )[ok].all()
                for f in unidades.values()
            )
            if not cuadra:
                # el mensaje NO nombra un culpable: esta función no sabe cuál de
                # los pasos movió la geometría, y nombrar solo el recorte mandaría
                # a revisar justo el paso inocente. Lista los sospechosos que van
                # DESPUÉS del recorte, que son los que no se buscan solos.
                print(
                    f"Aviso: la columna '{col}' no cuadra con la geometría "
                    "actual; algún paso movió la geometría después de "
                    f"calcularla. Recalcula {que} como ÚLTIMO paso, detrás de "
                    "todo lo que la toque: recortar, reproyectar, disolver, "
                    "separar_multipartes, simplificar, zona_de_influencia y también borrar / "
                    "resolver_por_prioridad, que recortan al final de la cadena "
                    "y son los que suelen pasar desapercibidos."
                )


_LARGO_CAMPO_SHP = 10


def comprobar_nombres_shp(gdf: gpd.GeoDataFrame) -> dict[str, str]:
    """Columnas que NO sobreviven a un .shp, con el motivo. Vacío = todas pasan.

    El formato shapefile corta los nombres de campo a 10 caracteres, así que dos
    columnas pueden acabar siendo la misma y GDAL desempata inventando un
    sufijo: `superficie_ha` sale como `superfic_1` si `SUPERFICIE` ya ocupaba el
    nombre corto. El dato se escribe bien; lo que se pierde es el NOMBRE, y
    quien luego cargue la capa buscando el suyo no lo encuentra.

    **Es un aviso ANTICIPADO, no un renombrador, y la diferencia es deliberada.**
    GDAL ya resuelve la colisión y `guardar` ya la traduce con los dos nombres,
    así que reimplementar el desempate sería escalón 3 al revés:
    rehacer lo que la plataforma hace. Lo que ninguno de los dos da es saberlo
    ANTES: el aviso de `guardar` llega con el archivo ya escrito, y detrás de
    una corrida de diecisiete minutos eso es volver a empezar.

    **Y renombrar automáticamente sería resolver el problema equivocado.** El
    arreglo bueno es que el llamante elija un nombre corto CON SENTIDO (`SUP`):
    un `superfic_1` nuestro no vale más que el de GDAL. Un nombre de campo
    es una decisión de quien escribe la capa, como la UMM o el umbral.

    Detecta las dos causas por separado, porque se arreglan distinto: la
    primera pide acortar, la segunda pide DESAMBIGUAR. La comparación es
    insensible a mayúsculas: `SUPERFICIE` y `superficie_ha` colisionan.

    Solo mira nombres. Que el .shp además no guarde fechas ni columnas de más de
    254 caracteres es otro asunto y no se cuela aquí.
    """
    columnas = [c for c in gdf.columns if c != gdf.geometry.name]
    cortos: dict[str, list[str]] = {}
    for col in columnas:
        cortos.setdefault(col[:_LARGO_CAMPO_SHP].lower(), []).append(col)

    problemas: dict[str, str] = {}
    for corto, cols in cortos.items():
        if len(cols) > 1:
            for col in cols:
                otras = ", ".join(f"'{c}'" for c in cols if c != col)
                problemas[col] = f"choca con {otras} en '{corto}' (10 caracteres)"
        elif len(cols[0]) > _LARGO_CAMPO_SHP:
            problemas[cols[0]] = (
                f"{len(cols[0])} caracteres: se corta a '{cols[0][:_LARGO_CAMPO_SHP]}'"
            )

    if _mostrar() and problemas:
        print(f"comprobar_nombres_shp: {len(problemas)} columna(s) no sobreviven a un .shp")
        for col, motivo in problemas.items():
            print(f"   '{col}': {motivo}")
    return problemas


def _leer_dbf(ruta: str) -> tuple[bytes, list[tuple[str, str, int, int]], bytes]:
    """(cabecera de 32 bytes, [(nombre, tipo, ancho, decimales)], registros).

    El .dbf es fijo desde dBase III: 32 bytes de cabecera, un descriptor de 32
    por campo (nombre en 0-10, tipo en 11, ancho en 16, decimales en 17)
    cerrado por 0x0D, y luego un registro de ancho fijo por entidad, cada uno
    con un byte de borrado delante. Leerlo a mano evita depender de fiona.
    """
    with open(ruta, "rb") as f:
        datos = f.read()
    largo_cab = int.from_bytes(datos[8:10], "little")
    campos = []
    for i in range(32, largo_cab - 1, 32):
        d = datos[i : i + 32]
        if d[0] == 0x0D:
            break
        nombre = d[:11].split(b"\0")[0].decode("latin-1")
        campos.append((nombre, chr(d[11]), d[16], d[17]))
    return datos[:largo_cab], campos, datos[largo_cab:]


def _anchos_dbf(ruta: str) -> dict[str, tuple[str, int, int]]:
    """{campo: (tipo, ancho, decimales)} tal como quedaron escritos en el .dbf."""
    return {n: (t, a, d) for n, t, a, d in _leer_dbf(ruta)[1]}


def _fijar_anchos_dbf(ruta: str, anchos: dict[int, tuple[int, int]]) -> None:
    """Reescribe el .dbf con `{posicion_del_campo: (ancho, decimales)}`.

    pyogrio no deja fijar el ancho (y los decimales los deja como TODO en su
    código), así que el .dbf se escribe con los de GDAL y aquí se reescribe
    registro a registro. Valida TODO antes de escribir: un texto que no cabe
    se cortaría y un número que no cabe saldría como asteriscos.
    """
    cabecera, campos, cuerpo = _leer_dbf(ruta)
    n = int.from_bytes(cabecera[4:8], "little")
    largo_reg = int.from_bytes(cabecera[10:12], "little")
    inicios, pos = [], 1  # el byte 0 de cada registro es la marca de borrado
    for _, _, ancho, _ in campos:
        inicios.append(pos)
        pos += ancho

    nuevos = {i: (campos[i][2], campos[i][3]) for i in range(len(campos))}
    nuevos.update(anchos)
    columnas: list[list[bytes]] = []
    errores, redondeados = [], 0
    for i, (nombre, tipo, ancho, _) in enumerate(campos):
        crudos = [
            cuerpo[r * largo_reg + inicios[i] : r * largo_reg + inicios[i] + ancho]
            for r in range(n)
        ]
        a, d = nuevos[i]
        if i not in anchos:
            columnas.append(crudos)
            continue
        salida, largos = [], []
        for c in crudos:
            if tipo == "C":
                v = c.rstrip(b" ")
                if len(v) > a:  # en BYTES: una letra con acento ocupa 2 en UTF-8
                    largos.append((len(v), v.decode("utf-8", "replace")))
                salida.append(v.ljust(a))
                continue
            v = c.strip()
            if not v or v.startswith(b"*"):  # nulo numerico
                salida.append(b" " * a)
                continue
            x = float(v)
            texto = f"{x:.{d}f}"
            redondeados += float(texto) != x
            if len(texto) > a:
                largos.append((len(texto), texto))
            salida.append(texto.rjust(a).encode("ascii"))
        if largos:
            errores.append(
                f"  '{nombre}': ancho {a} y hacen falta {max(n for n, _ in largos)}, "
                f"p. ej. {[v for _, v in largos[:3]]}"
            )
        columnas.append(salida)
    if errores:
        raise ValueError(
            "hay valores que no caben en el ancho pedido; se cortarian o "
            "saldrian como asteriscos. El ancho de texto cuenta BYTES: una "
            "letra con acento o una enie ocupa 2.\n" + "\n".join(errores)
        )
    if redondeados:
        print(
            f"Aviso: {redondeados} valor(es) redondeados a los decimales pedidos "
            "en `anchos`"
        )

    cab = bytearray(cabecera)
    cab[10:12] = (1 + sum(a for a, _ in nuevos.values())).to_bytes(2, "little")
    for i, (a, d) in nuevos.items():
        cab[32 + 32 * i + 16] = a
        cab[32 + 32 * i + 17] = d
    registros = b"".join(
        cuerpo[r * largo_reg : r * largo_reg + 1] + b"".join(col[r] for col in columnas)
        for r in range(n)
    )
    with open(ruta, "wb") as f:
        f.write(bytes(cab) + registros + b"\x1a")


def _validar_anchos(
    gdf: gpd.GeoDataFrame, anchos: dict[str, Any], ext: str
) -> dict[int, tuple[int, int]]:
    """`{campo: 50 | (12, 4)}` -> `{posicion_en_el_dbf: (ancho, decimales)}`."""
    if ext != ".shp":
        raise ValueError(
            f"`anchos` solo aplica a .shp, no a '{ext}': los demas formatos no "
            "guardan un ancho fijo por campo"
        )
    atributos = [c for c in gdf.columns if c != gdf.geometry.name]
    faltan = [c for c in anchos if c not in atributos]
    if faltan:
        raise ValueError(
            f"`anchos` nombra columnas que la capa no tiene: {faltan}; "
            f"columnas: {atributos}"
        )
    salida = {}
    for campo, valor in anchos.items():
        a, d = (valor, 0) if isinstance(valor, int) else tuple(valor)
        if not 1 <= a <= 254 or d < 0 or (d and d > a - 2):
            raise ValueError(
                f"`anchos['{campo}'] = {valor}` no vale: el ancho va de 1 a 254 "
                "y los decimales caben dentro con el punto (12, 4 -> 1234567.1234)"
            )
        tipo = gdf[campo].dtype
        if pd.api.types.is_datetime64_any_dtype(tipo) or pd.api.types.is_bool_dtype(tipo):
            raise ValueError(f"'{campo}' es fecha o logico: su ancho es fijo en un .shp")
        if d and not pd.api.types.is_numeric_dtype(tipo):
            raise ValueError(f"'{campo}' es texto: lleva solo ancho, sin decimales")
        salida[atributos.index(campo)] = (a, d)
    return salida


def guardar(
    gdf: gpd.GeoDataFrame,
    ruta: str | PathLike[str],
    gpx_como: str = "track",
    anchos: dict[str, Any] | None = None,
) -> str | PathLike[str]:
    """Guarda un GeoDataFrame en disco.

    El formato se deduce de la extensión de `ruta`. Para .gpx/.kml/.kmz
    reproyecta automáticamente a EPSG:4326 (esos formatos solo aceptan
    lon/lat) y usa el driver adecuado. Así, convertir es cargar + guardar:

        geo.guardar(geo.cargar("predio.shp"), "predio.kmz")          # SHP -> KMZ
        geo.guardar(geo.cargar("ruta.shp"), "ruta.gpx")              # SHP -> GPX
        geo.guardar(geo.cargar("nueva.gpx", capa="tracks"), "x.shp") # GPX -> SHP

    Parámetros
    ----------
    gpx_como : "track" | "route"
        Solo aplica a GPX y a geometrías de línea. El driver GPX decide por
        tipo de geometría: MultiLineString -> track, LineString -> route. Con
        "track" (por defecto, que es como trabaja el GPS aquí) se envuelven
        las líneas como MultiLineString. Los puntos siempre van a waypoints.
    anchos : dict, solo .shp
        Ancho fijo por campo: `{"CLASE": 50}` para texto o entero,
        `{"SUP": (12, 4)}` para decimal (ancho total con el punto, y
        decimales). Sin él GDAL pone texto 80, entero 18 y decimal 24.15. Los
        campos que no se nombran quedan así. **Falla si un valor no cabe**, en
        vez de cortar el texto o escribir asteriscos, y avisa si los decimales
        pedidos redondean algún valor. Comprueba el resultado con
        `listar_campos("capa.shp")`.

            geo.guardar(capa, "PENDIENTES.shp", anchos={"PENDIENTES": 10, "SUP": (12, 4)})

    Antes de escribir, avisa si una columna de área (SUP, superficie_ha...) ya
    no cuadra con la geometría: pista de que se calculó antes de recortar o
    reproyectar y quedó obsoleta.
    """
    if len(gdf) == 0:
        print(f"Aviso: guardando capa vacía en '{ruta}'")
    _avisar_medida_obsoleta(gdf)
    ext = os.path.splitext(str(ruta))[1].lower()
    carpeta = os.path.dirname(os.path.abspath(ruta))
    os.makedirs(carpeta, exist_ok=True)

    if anchos:
        posiciones = _validar_anchos(gdf, anchos, ext)
        # se escribe aparte y se mueve al final: si un valor no cabe, la ruta
        # no queda con un .shp a medias ni con los anchos por defecto
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.splitext(os.path.basename(str(ruta)))[0]
            with _traducir_avisos():
                gdf.to_file(os.path.join(tmp, base + ".shp"))
            _fijar_anchos_dbf(os.path.join(tmp, base + ".dbf"), posiciones)
            for nombre in os.listdir(tmp):
                shutil.move(os.path.join(tmp, nombre), os.path.join(carpeta, nombre))
        if _mostrar():
            print(f"Guardado '{ruta}': {len(gdf)} entidades, anchos {anchos}")
        return ruta

    if ext in _DRIVERS_GEO:
        gdf = _a_geografico(gdf, ext)
        ctx = nullcontext()
        if ext == ".gpx":
            # extensiones ON para no perder atributos al escribir GPX.
            ctx = _env("GPX_USE_EXTENSIONS", "YES")
            # GPX reserva 'fid'; un SHP sin atributos trae esa columna y revienta.
            gdf = gdf.drop(columns=[c for c in gdf.columns if c.lower() == "fid"])
            gdf = _poligono_a_borde(gdf)
            if gpx_como == "track":
                gdf = _lineas_a_track(gdf)
        with _traducir_avisos(), ctx:
            gdf.to_file(ruta, driver=_DRIVERS_GEO[ext])
    else:
        with _traducir_avisos():
            gdf.to_file(ruta)

    if _mostrar():
        print(f"Guardado '{ruta}': {len(gdf)} entidades")
    return ruta


def exportar_tabla(
    gdf: gpd.GeoDataFrame | pd.DataFrame,
    ruta: str | PathLike[str],
) -> str | PathLike[str]:
    """Guarda la tabla de atributos (sin geometría) en .csv o .xlsx.

        geo.exportar_tabla(geo.cargar("predio.shp"), "atributos.xlsx")

    Avisa igual que `guardar` si una columna de área quedó obsoleta. Acepta
    también un DataFrame sin geometría (`gdf[cols]`, `resumir`), sin ese aviso.
    """
    ext = os.path.splitext(str(ruta))[1].lower()
    if ext not in _EXT_TABLA:
        raise ValueError(f"Extensión '{ext}' no es de tabla: usa .csv o .xlsx")
    os.makedirs(os.path.dirname(os.path.abspath(ruta)), exist_ok=True)
    # Sin geometría (`gdf[cols]` sin ella, o la salida de `resumir`) ya es
    # tabla: no hay nada contra qué comprobar el área ni qué quitar.
    if isinstance(gdf, gpd.GeoDataFrame):
        _avisar_medida_obsoleta(gdf)
        tabla = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
    else:
        tabla = pd.DataFrame(gdf)
    if ext == ".csv":
        # con BOM: sin el, Excel en Windows lee cp1252 y `°` sale `Â°`
        tabla.to_csv(ruta, index=False, encoding="utf-8-sig")
    else:
        tabla.to_excel(ruta, index=False)  # openpyxl ya es dependencia
    if _mostrar():
        print(f"Guardado '{ruta}': {len(tabla)} filas, {len(tabla.columns)} campos")
    return ruta


def convertir(
    entrada: str | PathLike[str],
    salida: str | PathLike[str],
    capa: str | None = None,
    gpx_como: str = "track",
) -> str | PathLike[str]:
    """Convierte un archivo vectorial de un formato a otro (cargar + guardar).

        geo.convertir("ruta.shp", "ruta.gpx")     # SHP -> GPX
        geo.convertir("nueva.gpx", "nueva.shp")   # GPX -> SHP
        geo.convertir("predio.shp", "predio.kmz") # SHP -> KMZ

    Si la entrada es GPX y no se indica `capa`, elige automáticamente la que
    trae datos: un GPX de solo tracks leído "a secas" sale vacío porque la
    capa por defecto es waypoints. `gpx_como` aplica solo a la salida GPX.
    """
    if str(entrada).lower().endswith(".gpx"):
        gdf = _cargar_gpx_limpio(entrada, capa)
    else:
        gdf = cargar(entrada, capa=capa)
    return guardar(gdf, salida, gpx_como=gpx_como)


def _cargar_gpx_limpio(
    ruta: str | PathLike[str], capa: str | None
) -> gpd.GeoDataFrame:
    """Carga un GPX con la tabla de atributos limpia.

    Waypoints -> cargar_waypoints; tracks -> cargar_tracks. Si no se indica
    `capa`, elige la que trae datos. Rutas (routes) u otras capas caen al
    lector genérico. ponytail: solo waypoints y tracks tienen tabla limpia,
    que son los que exporta el GPS aquí; agrega routes si algún día hace falta.
    """
    if capa is None:
        capa = _capa_gpx_con_datos(ruta)
    if capa == "waypoints":
        return cargar_waypoints(ruta)
    if capa == "tracks":
        return cargar_tracks(ruta)
    return cargar(ruta, capa=capa)


def _capa_gpx_con_datos(ruta: str | PathLike[str]) -> str | None:
    """Primera capa GPX (waypoints > routes > tracks) que trae entidades.

    ponytail: si un GPX mezclara waypoints y tracks devuelve solo el primero;
    en ese caso pasa `capa=` explícito. No ha pasado con los archivos reales.
    """
    for capa in ("waypoints", "routes", "tracks"):
        try:
            if len(gpd.read_file(ruta, layer=capa)) > 0:
                return capa
        except Exception:
            # GDAL no puede leerla (un <trkseg> de un punto, p. ej.): se devuelve
            # para que su lector dedicado falle con el motivo, no en silencio
            return capa
    return None


def _a_geografico(gdf: gpd.GeoDataFrame, ext: str) -> gpd.GeoDataFrame:
    """Reproyecta a EPSG:4326 si hace falta; GPX/KML/KMZ lo exigen."""
    _exigir_crs(gdf, "capa")
    if gdf.crs.to_epsg() != 4326:
        print(f"Reproyectando a EPSG:4326 para exportar a {ext}")
        return gdf.to_crs(epsg=4326)
    return gdf


def _poligono_a_borde(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """GPX no admite polígonos; exporta su contorno como línea."""
    if set(gdf.geom_type) & {"Polygon", "MultiPolygon"}:
        print("GPX no admite polígonos; exporto su contorno como línea")
        gdf = gdf.copy()
        # por nombre real: con el literal y la columna activa llamada distinto, se
        # añadiría una columna "geometry" y se exportaría el polígono original
        gdf[gdf.geometry.name] = gdf.boundary
    return gdf


def _fmt_hora_es(dt: pd.Timestamp) -> str:
    """Formatea a 'dd/mm/aaaa hh:mm:ss a. m./p. m.' (formato local español)."""
    if pd.isna(dt):
        return ""  # ponytail: track sin tiempo -> campo vacío
    return dt.strftime("%d/%m/%Y %I:%M:%S ") + ("a. m." if dt.hour < 12 else "p. m.")


def cargar_waypoints(ruta: str | PathLike[str]) -> gpd.GeoDataFrame:
    """Carga los waypoints de un GPX en una tabla de atributos limpia.

    Columnas de salida: NAME, LAYER (constante 'Waypoint'), ELEVATION, time
    (ISO UTC tal cual, p.ej. 2026-02-21T16:09:47Z), sym. Descarta las ~18
    columnas vacías del esquema fijo de GPX (magvar, hdop, link*, cmt...).
    """
    with _traducir_avisos():
        gdf = gpd.read_file(ruta, layer="waypoints")
    # ele/time pueden faltar en GPX de otras fuentes; None en vez de reventar
    ele = gdf.get("ele")
    t = pd.to_datetime(gdf["time"], utc=True) if "time" in gdf.columns else None
    salida = gpd.GeoDataFrame(
        {
            "NAME": gdf.get("name"),
            "LAYER": "Waypoint",  # ponytail: etiqueta de capa; no viene en el GPX
            "ELEVATION": ele.round(6) if ele is not None else None,
            "time": t.dt.strftime("%Y-%m-%dT%H:%M:%SZ") if t is not None else None,
            "sym": gdf.get("sym"),
        },
        geometry=gdf.geometry,
        crs=gdf.crs,
    )
    if _mostrar():
        print(f"Waypoints '{ruta}': {len(salida)} entidades")
    return salida


def cargar_tracks(
    ruta: str | PathLike[str], utc_offset: int = -6
) -> gpd.GeoDataFrame:
    """Carga los tracks de un GPX en una tabla de atributos limpia, una linea por tramo.

    Una línea por tramo (<trkseg>). Columnas: NAME, LAYER (constante
    'Tracklog'), gpxx_DisplayColor (el SHP lo recorta a 'gpxx_Displ'),
    START_TIME, END_TIME. Los tiempos salen del primer/último punto del tramo,
    convertidos a hora local (`utc_offset`, por defecto -6 = UTC-6) y con
    formato español.
    """
    with _traducir_avisos():
        try:
            meta = gpd.read_file(ruta, layer="tracks")
        except Exception as e:
            # GDAL no puede armar la geometria si un <trkseg> trae 1 solo punto
            if "point array" in str(e):
                raise ValueError(
                    f"'{ruta}' tiene un tramo de un solo punto y no se puede leer; "
                    "borra ese tramo en el GPS o edita el GPX"
                ) from e
            raise
        pts = gpd.read_file(ruta, layer="track_points")
    pts = pts.sort_values(["track_fid", "track_seg_id", "track_seg_point_id"])
    if "time" in pts.columns:
        t_local = pd.to_datetime(pts["time"], utc=True) + pd.Timedelta(hours=utc_offset)
    else:
        # GPX de otra fuente sin <time>: tiempos vacíos en vez de reventar
        t_local = pd.Series(pd.NaT, index=pts.index)
    pts = pts.assign(_t=t_local.values)

    filas = []
    for (fid, _seg), sub in pts.groupby(["track_fid", "track_seg_id"], sort=True):
        coords = sub.geometry.get_coordinates().to_numpy()
        if len(coords) < 2:
            continue  # ponytail: un tramo de 1 punto no es línea, se salta
        # `track_fid` es la POSICION de la pista en la capa `tracks`, no una
        # etiqueta, y viene de una capa distinta (`track_points`). Si las dos no
        # cuadran, `iloc` lanza un IndexError pelado que no menciona el GPX.
        i = int(fid)
        if not 0 <= i < len(meta):
            raise ValueError(
                f"'{ruta}': un tramo apunta a la pista {i} y la capa 'tracks' "
                f"tiene {len(meta)}; el GPX está inconsistente"
            )
        trk = meta.iloc[i]
        color = re.search(
            r"DisplayColor>([^<]+)<", str(trk.get("gpxx_TrackExtension", ""))
        )
        filas.append(
            {
                "NAME": trk.get("name"),
                "LAYER": "Tracklog",  # ponytail: etiqueta de capa constante
                "gpxx_DisplayColor": color.group(1) if color else None,
                "START_TIME": _fmt_hora_es(sub["_t"].iloc[0]),
                "END_TIME": _fmt_hora_es(sub["_t"].iloc[-1]),
                "geometry": LineString(coords),
            }
        )
    # sin tramos válidos: capa vacía CON columna geometry, para que guardar no truene
    salida = gpd.GeoDataFrame(filas if filas else {"geometry": []}, crs=pts.crs)
    if _mostrar():
        print(f"Tracks '{ruta}': {len(salida)} tramos")
    return salida


def _lineas_a_track(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Envuelve LineString como MultiLineString para que GPX las escriba como track."""

    def a_multi(g: BaseGeometry | None) -> BaseGeometry | None:
        return (
            MultiLineString([g]) if g is not None and g.geom_type == "LineString" else g
        )

    gdf = gdf.copy()
    gdf[gdf.geometry.name] = gdf.geometry.apply(a_multi)
    return gdf


def detectar_utm(gdf: gpd.GeoDataFrame) -> int:
    """Código EPSG del huso UTM (WGS84) que contiene el centroide de `gdf`.

    Evita declarar el huso a mano: lo mide del propio dato en vez de
    heredarlo de un script anterior con otro predio. Revienta si la capa
    cruza dos husos, porque ahí elegir uno es una decisión de campo y no una
    detección.
    """
    # sin CRS, el error de geopandas no dice qué capa
    _exigir_crs(gdf, "gdf")
    x0, y0, x1, y1 = gdf.to_crs(4326).total_bounds
    if int((x0 + 180) // 6) != int((x1 + 180) // 6):
        raise ValueError(
            f"la capa cruza dos husos UTM ({x0:.2f} a {x1:.2f}): elige uno a mano"
        )
    zona = int(((x0 + x1) / 2 + 180) // 6) + 1
    epsg = (32600 if (y0 + y1) / 2 >= 0 else 32700) + zona
    if _mostrar():
        print(f"huso UTM detectado: EPSG:{epsg}")
    return epsg


def detectar_cota(
    curvas: gpd.GeoDataFrame, regularidad_min: float = 0.9
) -> tuple[str, float]:
    """Columna de cota y equidistancia de una capa de curvas de nivel.

    Una columna de cota es numérica y sus valores únicos caen en una
    ESCALERA: cada salto entre niveles es MÚLTIPLO de un mismo paso.

    No tiene por qué repetirse entre filas: una capa con una entidad
    multiparte por nivel trae exactamente una fila por cota, así que "más de
    la mitad de valores son únicos = es un ID" descarta justo la columna
    buena. La escalera es el discriminador; lo único que se rechaza a ojo es
    una corrida de enteros consecutivos, que ninguna capa de curvas es.

    Múltiplo, no igual. Un nivel que falta en un tramo llano deja un hueco de
    `2·paso` y sigue siendo escalera, así que probar `hueco == paso` falla en
    cualquier capa real. El paso tampoco se asume: cada salto observado se
    prueba como candidato y gana el que explica más saltos (empate al más
    chico). Así una escalera mixta (maestras cada 100, ordinarias cada 20,
    auxiliares cada 10) sale en 10 y no como ruido.

    **`regularidad_min` es la PUERTA, y entre las que la pasan manda el paso
    MÁS GRUESO**, no la regularidad. Cualquier columna de enteros es una
    escalera perfecta de paso 1 (todo entero es múltiplo de 1), así que la
    regularidad sola no separa una cota de un código: una medida real trae un
    cuanto, y paso 1 sobre enteros es la respuesta degenerada. La guarda de
    "enteros consecutivos" no basta, porque una columna de IDs con huecos no es
    consecutiva y aun así puntúa 1.00 a paso 1.

    Con la regularidad mandando, UNA fila atípica en la cota basta para que
    una columna de códigos gane con **e = 1 m**. De la equidistancia cuelgan `interpolar_mde`, `resolucion_regla` y
    `margen_borde_celdas`, o sea la cadena entera con una cifra inventada.

    Si nada pasa, imprime el puntaje de cada columna: "ninguna calzó" sin los
    puntajes solo traslada la adivinanza a quien lea el traceback.

    Devuelve (campo, equidistancia). `geo.MOSTRAR` además imprime el reparto
    de saltos entre cotas: una capa con curvas auxiliares mezcladas da 10
    donde se esperaba 20, y ahí se mueven todas las cifras firmes sin que
    nada se vea roto.
    """
    filas = []
    for col in curvas.columns:
        if col == curvas.geometry.name:
            continue
        v = np.sort(pd.to_numeric(curvas[col], errors="coerce").dropna().unique())
        if len(v) < 3:
            filas.append((col, 0.0, float("nan"), f"{len(v)} valores unicos"))
            continue
        d = np.round(np.diff(v), 3)
        d = d[d > 0]
        if np.allclose(d, 1.0):
            filas.append(
                (
                    col,
                    0.0,
                    1.0,
                    f"{len(v)} enteros consecutivos: es un indice, no una medida",
                )
            )
            continue
        mejor_paso, mejor_reg = _paso_escalera(v)
        filas.append(
            (
                col,
                mejor_reg,
                mejor_paso,
                "" if mejor_reg >= regularidad_min else "escalera irregular",
            )
        )

    # `regularidad_min` es la PUERTA; entre las que la pasan manda el paso MAS
    # GRUESO. Con paso 1 sobre enteros la regularidad es 1.00 SIEMPRE, asi que
    # con la regularidad mandando cualquier columna de codigos ganaria a una
    # cota con el mas minimo defecto.
    def _clave(f: tuple[str, float, float, str]) -> tuple[float, float]:
        paso = f[2] if f[2] == f[2] else 0.0
        return (-paso, -f[1])

    pasan = sorted((f for f in filas if f[1] >= regularidad_min), key=_clave)
    fallan = sorted((f for f in filas if f[1] < regularidad_min), key=lambda f: -f[1])
    filas = pasan + fallan
    if not filas or filas[0][1] < regularidad_min:
        detalle = "\n".join(
            f"      {c:>12} regularidad {r:.2f} paso {p:g} {m}" for c, r, p, m in filas
        )
        raise ValueError(f"ninguna columna con cotas en escalera:\n{detalle}")

    col, reg, paso, _ = filas[0]
    if _mostrar():
        print(
            f"cota detectada: '{col}' (regularidad {reg:.2f}), equidistancia {paso:g} m"
        )
        v = np.sort(pd.to_numeric(curvas[col], errors="coerce").dropna().unique())
        d = np.round(np.diff(v), 3)
        pasos, cuentas = np.unique(d[d > 0], return_counts=True)
        orden = np.argsort(-cuentas)[:3]
        reparto = "  ".join(
            f"{pasos[k]:g} m: {100 * cuentas[k] / cuentas.sum():.0f} %" for k in orden
        )
        print(f"   saltos entre cotas: {reparto}")
        if len(filas) > 1 and filas[1][1] >= regularidad_min:
            print(
                f"   OJO: '{filas[1][0]}' tambien pasa (regularidad {filas[1][1]:.2f}, "
                f"paso {filas[1][2]:g} m). Dos escaleras en la misma capa"
            )
    _avisar_fuera_de_escalera(pd.to_numeric(curvas[col], errors="coerce"), col, paso)
    return col, paso


def _avisar_fuera_de_escalera(cotas: pd.Series, col: str, paso: float) -> None:
    """Avisa de cotas que no caen en la escalera o que saltan mas de 2 pasos.

    La regularidad tolera una cota mal capturada (1 salto de 124 en un predio real), y
    esa cota hace un pico falso: una carta real de curvas trae 3302 sobre 2760 con e =
    20, y el HAND llego a 2,982 m. Fuera de escalera = no esta en la fase
    dominante (`cota % paso`), asi que una carta desplazada (1105, 1125...) no
    salta. Un salto de 2 pasos es una curva que falta en un llano y no avisa.
    Sale siempre: es defecto del dato, no reporte.
    """
    v = np.sort(cotas.dropna().unique())
    resto = np.round(v % paso, 3)
    fases, n = np.unique(resto, return_counts=True)
    fuera = resto != fases[n.argmax()]
    salto = np.diff(v, prepend=v[0])
    raros = fuera | (salto > 2 * paso + 1e-3)
    if not raros.any():
        return
    lineas = [
        f"      {v[i]:g} ("
        + (f"salto {salto[i]:g} m desde {v[i - 1]:g}, " if i else "la mas baja, ")
        + f"{int((cotas == v[i]).sum())} entidad(es))"
        + ("  fuera de escalera" if fuera[i] else "")
        for i in np.nonzero(raros)[0][:5]
    ]
    print(
        f"Aviso: {int(raros.sum())} cota(s) de '{col}' fuera de la escalera de "
        f"{paso:g} m o con salto > 2 pasos:\n" + "\n".join(lineas) + "\n"
        "   Si no es un hueco real de la carta, es una curva mal capturada: hace un "
        "pico\n   o un escalon falso en el MDE. Corrigela o quitala antes de "
        "interpolar."
    )


def detectar_campo(
    gdf: gpd.GeoDataFrame, vocabulario: set[str]
) -> str:
    """Columna cuyos valores contienen todo `vocabulario`.

    El nombre de columna cambia con cada capa de origen; las categorías
    legales (p. ej. `{'PERENNE', 'INTERMITENTE'}` de un cauce) no. Declarar
    el vocabulario en vez del nombre de columna es lo que sobrevive a que
    alguien renombre el campo en la siguiente entrega.
    """
    # los valores se comparan en mayúsculas; el vocabulario también
    vocabulario = {str(v).strip().upper() for v in vocabulario}
    for col in gdf.columns:
        if col == gdf.geometry.name:
            continue
        valores = set(gdf[col].astype(str).str.strip().str.upper())
        if vocabulario <= valores:
            if _mostrar():
                print(f"campo detectado: '{col}' (contiene {sorted(vocabulario)})")
            return col
    raise ValueError(
        f"ninguna columna contiene {sorted(vocabulario)}; columnas: {list(gdf.columns)}"
    )


def listar_capas(ruta: str | PathLike[str]) -> list[str]:
    """Nombres de las capas que hay dentro de un archivo, sin cargar ninguna.

    `cargar` acepta `capa=` desde el principio y no habia forma de saber que
    poner ahi sin abrir el archivo en un SIG. Esto lo cierra: lee solo el
    catalogo, no las entidades, asi que da igual que la capa pese 1 GB.

    Util en GeoPackage (varias capas de verdad) y en GPX, que SIEMPRE lista
    cinco (waypoints, routes, tracks, route_points, track_points) aunque cuatro
    vengan vacias: por eso un GPX de solo tracks leido a secas sale vacio, y por
    eso existen `cargar_waypoints` y `cargar_tracks`.

    Devuelve la lista de nombres. Con `geo.MOSTRAR` imprime tambien el tipo de
    geometria de cada una, que es lo que distingue la capa que buscas de sus
    hermanas vacias.
    """
    if not os.path.exists(ruta):
        raise FileNotFoundError(f"no existe la ruta '{ruta}'")
    with _traducir_avisos():
        info = gpd.list_layers(ruta)
    nombres = [str(n) for n in info["name"]]
    if _mostrar():
        print(f"'{ruta}': {len(nombres)} capa(s)")
        for nombre, tipo in zip(info["name"], info["geometry_type"]):
            print(f"  {nombre} ({tipo or 'sin geometria'})")
    if not nombres:
        print(f"Aviso: '{ruta}' no declara ninguna capa")
    return nombres


def describir(
    objeto: Any, capa: str | None = None
) -> dict[str, Any]:
    """Ficha de una capa: CRS, extension, entidades, campos, nulos y geometria rota.

    Acepta un GeoDataFrame ya cargado o la ruta de un archivo (que carga con
    `cargar`, en silencio). Es lo primero que se hace con cartografia que llega
    de fuera.

    Las tres cifras que no da `print(gdf)` y son las que muerden:

        unidades     'metro' o 'grado'. Un CRS geografico convierte cualquier
                     superficie y cualquier buffer en un numero sin sentido, y
                     el aviso llega funcion por funcion, tarde. Por eso aqui
                     sale como AVISO y no como la palabra 'degree' entre otros
                     diez campos: un adjetivo impreso no adelanta nada.
        vacias       geometrias nulas o vacias, que sobreviven a un guardado y
                     revientan tres pasos despues (ver `limpiar_vacias`).
        invalidas    auto-intersecciones; lo que `reparar_geometrias` arregla.

    Devuelve el dict con todo (`n`, `tipos`, `crs`, `epsg`, `unidades`,
    `extension`, `campos`, `nulos`, `vacias`, `invalidas`) para poder afirmarlo
    en un script; `geo.MOSTRAR` solo decide si ademas se imprime.
    """
    if isinstance(objeto, (str, PathLike)):
        gdf = _callado(cargar)(objeto, capa=capa)
        origen = str(objeto)
    else:
        gdf = objeto
        origen = str(gdf.attrs.get("ruta", "capa en memoria"))

    geom = gdf.geometry.name
    hueca = gdf.geometry.is_empty | gdf.geometry.isna()
    vacias = int(hueca.sum())
    ficha: dict[str, Any] = {
        "origen": origen,
        "n": len(gdf),
        "tipos": sorted({str(t) for t in gdf.geom_type.dropna().unique()}),
        "crs": str(gdf.crs) if gdf.crs is not None else None,
        "epsg": gdf.crs.to_epsg() if gdf.crs is not None else None,
        "unidades": gdf.crs.axis_info[0].unit_name if gdf.crs is not None else None,
        "extension": tuple(gdf.total_bounds) if len(gdf) else None,
        "campos": {c: str(gdf[c].dtype) for c in gdf.columns if c != geom},
        "nulos": {c: int(gdf[c].isna().sum()) for c in gdf.columns if c != geom},
        "vacias": vacias,
        # las huecas se excluyen del conteo de invalidas, y no es simetria: una
        # vacia se BORRA (`limpiar_vacias`) y una invalida se REPARA
        # (`reparar_geometrias`). Ademas `is_valid` no las trata igual entre si
        # (nula -> False, poligono vacio -> True), asi que restarlas al final
        # daria un conteo negativo en cuanto hay un vacio en vez de un nulo.
        "invalidas": int((~gdf.geometry.is_valid & ~hueca).sum()),
    }
    if _mostrar():
        print(f"describir '{origen}'")
        print(f"  entidades  {ficha['n']} | tipo {', '.join(ficha['tipos']) or '(nada)'}")
        # El nombre y el codigo, NO el WKT: una capa estatal sin codigo EPSG
        # (Lambert ITRF92 en WKT suelto) son 700 caracteres en una linea que
        # entierran el unico dato accionable, que es que no tiene codigo. El WKT
        # entero sigue en ficha['crs'] para quien lo necesite.
        if gdf.crs is None:
            print(f"  CRS        (ninguno) | unidades: {ficha['unidades']}")
        else:
            codigo = f"EPSG:{ficha['epsg']}" if ficha["epsg"] else "SIN codigo EPSG"
            print(
                f"  CRS        {gdf.crs.name} ({codigo}) | unidades: {ficha['unidades']}"
            )
        if ficha["extension"] is not None:
            x0, y0, x1, y1 = ficha["extension"]
            print(f"  extension  ({x0:.2f}, {y0:.2f}) - ({x1:.2f}, {y1:.2f})")
        print(f"  campos     {len(ficha['campos'])}")
        for col, tipo in ficha["campos"].items():
            nulos = ficha["nulos"][col]
            aviso = f"  <- {nulos} nulos" if nulos else ""
            print(f"    {col}: {tipo}{aviso}")
        if vacias or ficha["invalidas"]:
            print(
                f"  Aviso: {vacias} geometrias vacias, {ficha['invalidas']} "
                "invalidas (limpiar_vacias / reparar_geometrias)"
            )
    if gdf.crs is None:
        print("Aviso: capa sin CRS; no podras reproyectar ni medir areas")
    elif gdf.crs.is_geographic:
        # El docstring dice que las unidades son de las tres que muerden porque
        # "el aviso llega funcion por funcion, tarde", y esta funcion existe para
        # darlo temprano. Imprimir la palabra 'degree' entre otros diez campos no
        # es darlo: un adjetivo no es un aviso.
        print(
            f"Aviso: CRS GEOGRAFICO (unidades '{ficha['unidades']}'). Cualquier "
            "superficie, longitud, buffer o distancia sale en grados y NO "
            "significa nada; reproyecta a UTM antes de medir (detectar_utm)"
        )
    return ficha
