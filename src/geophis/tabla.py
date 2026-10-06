"""La tabla de atributos: seleccionar, resumir, unir y editar campos.

Módulo aparte y no dentro de `geometria.py` a propósito: casi nada de aquí
toca la geometría (`seleccionar_por_atributo` filtra filas, `estadisticas_de_resumen`
agrega columnas, `unir_campo` pega un CSV por una clave, y las de
editar campos solo cambian columnas). Meterlas en un archivo llamado
`geometria` sería un nombre mintiendo.

Lo que estas funciones añaden sobre pandas no es traducir al español, es la
guarda: un `merge` que no casa ninguna clave devuelve columnas de nulos sin una
palabra, y un `groupby` sobre una columna de superficie obsoleta devuelve un
total con aspecto de bueno. Las dos cosas se avisan aquí.
"""

import os
from os import PathLike
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

from .archivo import _anchos_dbf, _avisar_medida_obsoleta
from .proyeccion import _a_crs, _exigir_crs, _exigir_proyectado
from ._salida import _callado, _mostrar


def seleccionar_por_atributo(
    gdf: gpd.GeoDataFrame, consulta: str
) -> gpd.GeoDataFrame:
    """Seleccionar por atributo: las filas que cumplen `consulta`.

    `consulta` es la sintaxis de `DataFrame.query`, que es la que más se parece
    a la de un SIG:

        "PENDIENTES == '>100'"
        "SUP > 5 and CLASE in ['A', 'B']"
        "COTA >= 2000"

    Dos trampas de esa sintaxis, y por eso están escritas aquí en vez de en un
    error que llega tarde: un nombre de columna con espacios o acentos va entre
    acentos graves (`` `CLASE DE USO` == 'x' ``), y las comillas de dentro tienen
    que ser distintas de las de fuera.

    Devuelve una vista con el mismo CRS. No copia: si vas a añadirle columnas,
    `.copy()` primero, o pandas avisará de escritura sobre una rebanada.

    Avisa si la selección sale vacía. Una capa vacía no revienta nada por sí
    sola y reaparece cuatro pasos después como un total que no cuadra.
    """
    try:
        salida = gdf.query(consulta)
    except Exception as e:
        atributos = [c for c in gdf.columns if c != gdf.geometry.name]
        raise ValueError(
            f"no se pudo evaluar la consulta '{consulta}' "
            f"({type(e).__name__}: {e}). Columnas disponibles: {atributos}. "
            "Un nombre con espacios o acentos va entre acentos graves, y el "
            "texto entre comillas simples: `CLASE DE USO` == 'agostadero'"
        ) from e
    if _mostrar():
        print(f"seleccionar_por_atributo: {len(salida)} de {len(gdf)} entidades")
    if len(salida) == 0:
        print(f"Aviso: la consulta '{consulta}' no seleccionó ninguna entidad")
    return salida


def seleccionar_por_ubicacion(
    gdf: gpd.GeoDataFrame,
    otro: Any,
    predicado: str = "intersects",
    distancia: float = 0,
) -> gpd.GeoDataFrame:
    """Seleccionar por ubicación: las entidades de `gdf` que cumplen `predicado` con `otro`.

    Es la hermana de `union_espacial`, y la diferencia importa: `union_espacial`
    es un JOIN (devuelve las mismas filas más las columnas de `otro`, y duplica
    la fila si dos entidades de `otro` la tocan), esto es una SELECCIÓN
    (devuelve un subconjunto de las filas de `gdf`, cada una una vez, sin añadir
    ni una columna). Cuando lo que se quiere es filtrar, el join duplica filas
    en silencio y el total sale de más.

    `otro` puede ser una capa o una geometría shapely suelta; una capa se
    reproyecta al CRS de `gdf`, y una geometría cruda se asume ya en él porque
    no lleva CRS que comprobar. Basta con que UNA entidad de `otro` cumpla.

    `predicado`: 'intersects' (por defecto), 'within', 'contains', 'crosses',
    'touches', 'overlaps'. Se lee siempre en el orden `gdf <predicado> otro`:
    'within' devuelve las de `gdf` que están dentro de `otro`.

    `distancia` > 0 aplica el predicado contra `otro` DILATADO esa distancia
    (unidades del CRS), que es la selección "a menos de una distancia": con
    'intersects' salen las que están a menos de `distancia`, con 'within' las
    que además están enteras dentro de esa orla.
    """
    _exigir_crs(gdf, "gdf")
    if hasattr(otro, "crs"):
        otro = _a_crs(otro, gdf.crs, "otro")
        otro_g = gpd.GeoDataFrame(geometry=otro.geometry.reset_index(drop=True))
        otro_g = otro_g.set_crs(gdf.crs, allow_override=True)
    else:
        otro_g = gpd.GeoDataFrame(geometry=[otro], crs=gdf.crs)
    if distancia < 0:
        raise ValueError(f"distancia debe ser >= 0, no {distancia}")
    if distancia:
        _exigir_proyectado(gdf, "gdf")
        otro_g = gpd.GeoDataFrame(geometry=otro_g.geometry.buffer(distancia), crs=gdf.crs)
    # sjoin y no `sindex.query`: la dirección del predicado en el índice espacial
    # es (geometria_consultada, geometria_del_arbol), o sea la contraria a la que
    # se lee en el nombre, y un 'within' silenciosamente invertido devuelve una
    # capa plausible. sjoin lo aplica como gdf-predicado-otro, que es lo escrito.
    # indice POSICIONAL: con etiquetas repetidas (tras `concat` o `explode` sin
    # reset), `isin` sobre la etiqueta se llevaria filas que no cumplen
    izq = gdf[[gdf.geometry.name]].reset_index(drop=True)
    pares = gpd.sjoin(izq, otro_g, how="inner", predicate=predicado)
    salida = gdf.iloc[np.unique(pares.index)]
    if _mostrar():
        orla = f", a {distancia} m" if distancia else ""
        print(
            f"seleccionar_por_ubicacion: {len(salida)} de {len(gdf)} entidades "
            f"({predicado}{orla})"
        )
    if len(salida) == 0:
        print(f"Aviso: ninguna entidad cumple '{predicado}' con la capa dada")
    return salida


def estadisticas_de_resumen(
    gdf: gpd.GeoDataFrame,
    por: str | list[str] | None = None,
    campos: Any = None,
) -> pd.DataFrame:
    """Estadísticas de resumen: una fila por grupo, con `n` y los estadísticos pedidos.

    Es la tabla de hectáreas por clase que hoy se arma a mano al final de cada
    flujo:

        tabla = geo.estadisticas_de_resumen(entregable, por="PENDIENTES", campos={"SUP": "sum"})

    `por`: columna o lista de columnas. Con `por=None` sale una sola fila con el
    total de la capa. Los nulos cuentan como grupo propio (`dropna=False`): un
    grupo que desaparece de la tabla es superficie que se va sin avisar.

    `campos`: dict `{columna: estadístico}` o `{columna: [estadísticos]}`, con
    los nombres de pandas ('sum', 'mean', 'min', 'max', 'std', 'median',
    'count'). Si se pasa solo el nombre de una columna o una lista de nombres,
    se asume 'sum' y se dice por pantalla, porque asumir un estadístico en
    silencio es inventar la cifra que se va a citar.

    Las columnas de salida se llaman `columna_estadistico` (`SUP_sum`), y
    siempre hay una `n` con el conteo de entidades del grupo. Devuelve un
    DataFrame sin geometría: es una tabla, y va a `exportar_tabla`.

    Antes de agregar comprueba que las columnas de área y longitud sigan
    cuadrando con la geometría, porque sumar una columna obsoleta es la forma
    más directa de publicar una superficie que ya no existe.
    """
    claves = [] if por is None else ([por] if isinstance(por, str) else list(por))

    if campos is None:
        spec: dict[str, list[str]] = {}
    elif isinstance(campos, dict):
        spec = {c: [s] if isinstance(s, str) else list(s) for c, s in campos.items()}
    else:
        cols = [campos] if isinstance(campos, str) else list(campos)
        spec = {c: ["sum"] for c in cols}
        print(
            "Aviso: `campos` sin estadistico; se asume 'sum' para "
            f"{cols}. Declaralo si querias otro: campos={{'{cols[0]}': 'mean'}}"
        )

    geom = gdf.geometry.name if gdf.geometry is not None else None
    faltan = [c for c in claves + list(spec) if c not in gdf.columns]
    if faltan:
        atributos = [c for c in gdf.columns if c != geom]
        raise ValueError(
            f"la capa no tiene la(s) columna(s) {faltan}; columnas: {atributos}"
        )
    if geom in claves or geom in spec:
        raise ValueError(
            f"'{geom}' es la geometria, no un atributo: no se puede agrupar ni "
            "sumar por ella. Para superficie usa calcular_superficie y resume "
            "esa columna"
        )

    _avisar_medida_obsoleta(gdf)
    df = pd.DataFrame(gdf.drop(columns=geom, errors="ignore"))

    if claves:
        grupos = df.groupby(claves, dropna=False)
        if spec:
            salida = grupos.agg(spec)
            salida.columns = [f"{c}_{s}" for c, s in salida.columns]
            salida.insert(0, "n", grupos.size())
            salida = salida.reset_index()
        else:
            salida = grupos.size().rename("n").reset_index()
    else:
        fila: dict[str, Any] = {"n": len(df)}
        for c, estadisticos in spec.items():
            for s in estadisticos:
                fila[f"{c}_{s}"] = df[c].agg(s)
        salida = pd.DataFrame([fila])

    if _mostrar():
        print(f"estadisticas_de_resumen: {len(salida)} grupos sobre {len(gdf)} entidades")
    return salida


def unir_campo(
    gdf: gpd.GeoDataFrame,
    tabla: Any,
    clave: str | tuple[str, str],
    como: str = "left",
) -> gpd.GeoDataFrame:
    """Unir campo: pega las columnas de una tabla a la capa por una clave común.

    El join NO espacial, que es el caso de la planilla de campo contra el
    shapefile de rodales. `tabla` puede ser un DataFrame o la ruta de un .csv,
    .xlsx o .xls.

    `clave`: el nombre de la columna si se llama igual en las dos, o
    `(columna_capa, columna_tabla)` si no.

    `como`: 'left' (por defecto, conserva todas las entidades de la capa),
    'inner' (solo las que casan), 'right', 'outer'.

    **Falla si no casa NINGUNA clave**, y esa es la razón de que esta función
    exista en vez de un `merge` pelado. Un join que no casa devuelve la capa
    entera con columnas de nulos, o sea algo que sigue pareciendo una capa, y el
    error aparece cuatro pasos después como un mapa en blanco. La causa casi
    siempre es de TIPO, no de datos: la clave leída de un CSV es texto y la del
    shapefile entero, así que '007' nunca es igual a 7. El mensaje da los dos
    tipos y una muestra de cada lado, porque con eso la conversión se escribe
    sola y sin ella se audita el archivo equivocado.

    Ese mensaje INTERCEPTA el de pandas. Con tipos incompatibles `merge` revienta
    antes de unir, con un ValueError que manda usar `pd.concat`, que aquí es el
    consejo equivocado. La conversión que se propone va del lado que CONSERVA el dato
    (entero a texto, no al revés): pasar '007' a número se come los ceros.

    Avisa también si la tabla trae claves repetidas: eso MULTIPLICA filas de la
    capa (una fila por coincidencia) y es la otra forma de inflar una
    superficie total sin que nada reviente.

    Las columnas que existen en las dos, salvo la clave, entran con el sufijo
    `_tabla` para no pisar las de la capa.
    """
    if isinstance(tabla, (str, PathLike)):
        if not os.path.exists(tabla):
            raise FileNotFoundError(f"no existe la ruta '{tabla}'")
        ext = os.path.splitext(str(tabla))[1].lower()
        if ext in (".xlsx", ".xls"):
            tabla = pd.read_excel(tabla)
        elif ext == ".csv":
            tabla = pd.read_csv(tabla)
        else:
            raise ValueError(
                f"extension '{ext}' no soportada para una tabla; usa .csv, .xlsx o .xls"
            )

    izq, der = (clave, clave) if isinstance(clave, str) else clave
    if izq not in gdf.columns:
        raise ValueError(
            f"la capa no tiene la columna '{izq}'; columnas: {list(gdf.columns)}"
        )
    if der not in tabla.columns:
        raise ValueError(
            f"la tabla no tiene la columna '{der}'; columnas: {list(tabla.columns)}"
        )

    repetidas = int(tabla[der].duplicated().sum())
    pisadas = sorted((set(gdf.columns) & set(tabla.columns)) - {izq, der})

    def _sin_coincidencias(motivo: str) -> ValueError:
        return ValueError(
            f"el join por '{izq}' = '{der}' no caso ninguna fila.\n"
            f"  capa   tipo {gdf[izq].dtype}, ejemplos {list(gdf[izq].head(3))}\n"
            f"  tabla  tipo {tabla[der].dtype}, ejemplos {list(tabla[der].head(3))}\n"
            f"{motivo}\n"
            "Si son los tipos, convierte antes de unir, y hazlo del lado que "
            "conserva el dato: pasar texto a numero se come los ceros a la "
            f"izquierda, asi que va al reves -> gdf['{izq}'] = "
            f"gdf['{izq}'].astype(str).str.zfill(3)"
        )

    try:
        salida = gdf.merge(
            tabla,
            left_on=izq,
            right_on=der,
            how=como,
            suffixes=("", "_tabla"),
            indicator="_union",
        )
    except ValueError as e:
        # pandas revienta ANTES de unir cuando los tipos son incompatibles, y su
        # mensaje manda usar `pd.concat`, que aqui es el consejo equivocado: lo
        # que hay que hacer es igualar el tipo de la clave. Sin este paso la
        # guarda de abajo seria inalcanzable justo en el caso comun: '007' del
        # CSV contra 7 del .shp. El texto de
        # pandas no se copia a proposito; queda en la excepcion encadenada.
        raise _sin_coincidencias(
            "Los tipos son INCOMPATIBLES y pandas ni intento unir."
        ) from e

    casadas = int((salida["_union"] == "both").sum())
    salida = salida.drop(columns="_union")

    if casadas == 0:
        raise _sin_coincidencias(
            "Las dos columnas existen y los tipos son compatibles, asi que no "
            "es de nombre ni de tipo: los valores son de otro universo."
        )
    if repetidas:
        print(
            f"Aviso: la tabla tiene {repetidas} claves repetidas en '{der}'; la "
            f"capa paso de {len(gdf)} a {len(salida)} filas. Cada repetida "
            "duplica la entidad, y eso infla cualquier total que se sume despues"
        )
    if pisadas:
        print(
            f"Aviso: columnas presentes en las dos, la de la tabla entra como "
            f"'_tabla': {pisadas}"
        )
    if _mostrar():
        print(
            f"unir_campo: {casadas} de {len(gdf)} entidades casaron por '{izq}'"
        )
    return salida


def _atributos_en(gdf: gpd.GeoDataFrame, convertir: Any) -> gpd.GeoDataFrame:
    """Copia de `gdf` con nombres de campo y valores de texto pasados por `convertir`."""
    geom = gdf.geometry.name  # no "geometry": un .gpkg la trae como `geom`
    nuevos: dict[str, str] = {geom: geom}  # la geometría también ocupa su nombre
    for col in gdf.columns:
        if col == geom:
            continue
        nuevo = convertir(col)
        if nuevo in nuevos.values():
            viejo = next(c for c, n in nuevos.items() if n == nuevo)
            raise ValueError(
                f"las columnas '{viejo}' y '{col}' quedarían las dos como "
                f"'{nuevo}'; renombra una antes"
            )
        nuevos[col] = nuevo
    salida = gdf.rename(columns=nuevos)
    for col in salida.columns:
        # ponytail: `category` no se toca; `cargar` desde .shp nunca la produce
        # y `rename_categories` puede chocar al juntar `a` y `A`
        if col == geom or isinstance(salida[col].dtype, pd.CategoricalDtype):
            continue
        if salida[col].dtype == object or pd.api.types.is_string_dtype(salida[col]):
            # Series.map pasaria None a NaN; con el dtype original el nulo queda igual
            salida[col] = pd.Series(
                [convertir(v) if isinstance(v, str) else v for v in salida[col]],
                index=salida.index,
                dtype=salida[col].dtype,
            )
    return salida


def atributos_a_mayusculas(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Nombres de campo y valores de texto en MAYÚSCULAS. Devuelve copia.

    No toca la geometría (ni el nombre de su columna), el CRS, las columnas
    numéricas, las `category` ni los nulos. La entrada no cambia. Falla si dos
    nombres chocan al convertir (`sup` y `SUP`): no crea columnas duplicadas ni
    desempata sola.

    Va al final, después de calcular `SUP` y de todo lo que use los nombres
    originales, justo antes de guardar:

        geo.guardar(geo.atributos_a_mayusculas(entregable), "PENDIENTES.shp")
    """
    return _atributos_en(gdf, str.upper)


def atributos_a_minusculas(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Nombres de campo y valores de texto en minúsculas. Devuelve copia.

    Gemela de `atributos_a_mayusculas`, con las mismas reglas: no toca la
    geometría, el CRS, los números, las `category` ni los nulos; la entrada no
    cambia; falla si dos nombres chocan (`sup` y `SUP`). Va al final, después de
    calcular `SUP`, justo antes de `guardar` o `exportar_tabla`.
    """
    return _atributos_en(gdf, str.lower)


# --- Editar la tabla: un paso, una funcion ------------------------------------
#
# Casi todas son una linea de pandas. Existen igual por dos razones: el nombre
# dice que hace a quien lee el script, y la guarda revienta donde pandas calla.
# `rename` con una columna mal escrita no hace nada, `to_numeric` convierte
# 'S/D' en NaN y `map` deja nulo todo valor sin pareja. Las tres cosas producen
# una capa con aspecto de buena. Todas devuelven COPIA y no tocan la geometria.

_TIPOS = ("texto", "entero", "decimal", "fecha")


def _atributos(gdf: gpd.GeoDataFrame) -> list[str]:
    return [c for c in gdf.columns if c != gdf.geometry.name]


def _exigir_campos(gdf: gpd.GeoDataFrame, campos: list[str]) -> None:
    """Falla si falta alguna columna o si se pide la geometría como atributo."""
    if gdf.geometry.name in campos:
        raise ValueError(
            f"'{gdf.geometry.name}' es la geometria, no un atributo: esta "
            "funcion no la toca"
        )
    faltan = [c for c in campos if c not in gdf.columns]
    if faltan:
        raise ValueError(
            f"la capa no tiene la(s) columna(s) {faltan}; columnas: {_atributos(gdf)}"
        )


def _como_lista(campos: str | list[str]) -> list[str]:
    return [campos] if isinstance(campos, str) else list(campos)


def _convertir(serie: pd.Series, tipo: str) -> pd.Series:
    """`serie` al tipo en español; lo que no convierte queda nulo (lo mira el llamante)."""
    if tipo == "texto":
        # astype(str) escribiria 'nan' y 'None' como texto: el nulo sigue nulo
        return pd.Series(
            [None if pd.isna(v) else str(v) for v in serie],
            index=serie.index,
            dtype=object,
        )
    if tipo == "decimal":
        return pd.to_numeric(serie, errors="coerce").astype("float64")
    if tipo == "entero":
        num = pd.to_numeric(serie, errors="coerce")
        fraccion = num.notna() & (num != num.round())
        if fraccion.any():
            raise ValueError(
                f"'{serie.name}' tiene {int(fraccion.sum())} valor(es) con "
                f"decimales, p. ej. {list(num[fraccion].head(3))}: pasarlo a "
                "entero los trunca. Redondea antes con calcular_campo(capa, "
                f"'{serie.name}', '{serie.name}.round()') o usa tipo='decimal'"
            )
        # Int64 con nulos, int64 sin ellos: un NaN no cabe en un entero de numpy
        return num.astype("Int64" if num.isna().any() else "int64")
    if tipo == "fecha":
        # dia primero: aqui se escribe 31/12/2025
        return pd.to_datetime(serie, errors="coerce", dayfirst=True)
    raise ValueError(f"tipo '{tipo}' no existe; usa uno de {list(_TIPOS)}")


def listar_campos(capa: Any) -> pd.DataFrame:
    """Los campos de una capa: nombre, tipo, nulos, un ejemplo y, en .shp, el ancho.

    `capa` es un GeoDataFrame o la ruta de un archivo. Con la ruta de un .shp
    lee además la cabecera del .dbf y da `tipo_dbf`, `ancho` y `decimales` tal
    como quedaron ESCRITOS: es lo que hay que mirar cuando otro programa corta
    un texto o no acepta una cifra. El ancho no existe en memoria, solo en el
    archivo, así que con un GeoDataFrame esas columnas no salen.

        geo.listar_campos("PENDIENTES.shp")
    """
    ruta = None
    if isinstance(capa, (str, PathLike)):
        ruta = str(capa)
        if not os.path.exists(ruta):
            raise FileNotFoundError(f"no existe la ruta '{ruta}'")
        capa = gpd.read_file(ruta)
    filas = []
    for c in _atributos(capa):
        con_dato = capa[c].dropna()
        filas.append(
            {
                "campo": c,
                "tipo": str(capa[c].dtype),
                "nulos": int(capa[c].isna().sum()),
                "ejemplo": con_dato.iloc[0] if len(con_dato) else None,
            }
        )
    tabla = pd.DataFrame(filas, columns=["campo", "tipo", "nulos", "ejemplo"])
    if ruta and os.path.splitext(ruta)[1].lower() == ".shp":
        anchos = _anchos_dbf(os.path.splitext(ruta)[0] + ".dbf")
        for i, col in enumerate(("tipo_dbf", "ancho", "decimales")):
            tabla[col] = [anchos.get(c, (None, None, None))[i] for c in tabla["campo"]]
    if _mostrar():
        print(f"listar_campos: {len(tabla)} campos, {len(capa)} entidades")
        print(tabla.to_string(index=False))
    return tabla


def agregar_campo(
    gdf: gpd.GeoDataFrame,
    campo: str,
    valor: Any = None,
    tipo: str | None = None,
) -> gpd.GeoDataFrame:
    """Agregar campo: columna nueva, vacía o con un valor. Devuelve copia.

    `valor`: uno solo para todas las filas (`"A"`, `0`), o una lista/Serie con
    uno por entidad, que se asigna por POSICIÓN. `tipo`: 'texto', 'entero',
    'decimal' o 'fecha'; sin él, el que deduzca pandas del valor. Una columna
    vacía sin tipo sale como texto, que es lo que crearía un SIG.

    **Falla si el campo ya existe.** Añadir sobre uno existente lo pisa, y eso
    es `calcular_campo`, que lo avisa. Para un nombre libre: `nombre_campo_libre`.

        capa = geo.agregar_campo(capa, "CLASE", "A")
        capa = geo.agregar_campo(capa, "FECHA", tipo="fecha")
    """
    if campo in gdf.columns:
        raise ValueError(
            f"el campo '{campo}' ya existe. Para sobrescribirlo usa "
            f"calcular_campo; para un nombre libre, nombre_campo_libre(capa, '{campo}')"
        )
    if isinstance(valor, (list, tuple, pd.Series, np.ndarray)):
        if len(valor) != len(gdf):
            raise ValueError(
                f"'{campo}': {len(valor)} valores para {len(gdf)} entidades; pasa "
                "uno solo (se repite) o exactamente uno por entidad"
            )
        valor = list(valor)  # por posicion: el indice de una Serie no cuenta
    salida = gdf.copy()
    salida[campo] = valor
    if tipo is None and valor is None:
        tipo = "texto"
    if tipo is not None:
        salida[campo] = _convertir(salida[campo], tipo)
    if _mostrar():
        print(
            f"agregar_campo: '{campo}' ({salida[campo].dtype}) en {len(salida)} entidades"
        )
    return salida


def calcular_campo(
    gdf: gpd.GeoDataFrame,
    campo: str,
    expresion: Any,
) -> gpd.GeoDataFrame:
    """Calcular campo: llena `campo` con una expresión. Devuelve copia.

    `expresion` es texto con la sintaxis de `DataFrame.eval` (operaciones entre
    columnas) o una función que recibe cada fila:

        capa = geo.calcular_campo(capa, "SUP_M2", "SUP * 10000")
        capa = geo.calcular_campo(capa, "CLAVE", lambda f: f"{f.PREDIO}-{f.ID}")

    Crea el campo si no existe y lo SOBRESCRIBE si existe, avisando:
    sobrescribir en silencio es perder el dato anterior sin saberlo. Una
    columna con espacios o acentos va entre acentos graves, como en
    `seleccionar_por_atributo`.
    """
    try:
        if callable(expresion):
            valores = gdf.apply(expresion, axis=1)
        else:
            valores = pd.DataFrame(gdf.drop(columns=gdf.geometry.name)).eval(expresion)
    except Exception as e:
        raise ValueError(
            f"no se pudo calcular '{campo}' ({type(e).__name__}: {e}). "
            f"Columnas disponibles: {_atributos(gdf)}. Un nombre con espacios o "
            "acentos va entre acentos graves: `CLASE DE USO`"
        ) from e
    if campo in gdf.columns:
        print(f"Aviso: calcular_campo sobrescribe '{campo}', que ya existia")
    salida = gdf.copy()
    salida[campo] = valores
    if _mostrar():
        nulos = int(salida[campo].isna().sum())
        extra = f", {nulos} nulos" if nulos else ""
        print(f"calcular_campo: '{campo}' en {len(salida)} entidades{extra}")
    return salida


def renombrar_campos(
    gdf: gpd.GeoDataFrame, nombres: dict[str, str]
) -> gpd.GeoDataFrame:
    """Cambia nombres de campo con `{"viejo": "NUEVO"}`. Devuelve copia.

    Lo que añade sobre `rename`: **falla si un nombre viejo no existe** (pandas
    lo ignora sin decir nada y el script sigue con el nombre de antes) y
    **falla si un nombre nuevo choca** con otro campo, porque quedarían dos
    columnas con el mismo nombre.

        capa = geo.renombrar_campos(capa, {"superficie_ha": "SUP"})
    """
    _exigir_campos(gdf, list(nombres))
    finales = [nombres.get(c, c) for c in gdf.columns]
    repetidos = sorted({n for n in finales if finales.count(n) > 1})
    if repetidos:
        raise ValueError(
            f"al renombrar quedarian columnas repetidas: {repetidos}. Renombra "
            "o borra antes la que ya tiene ese nombre"
        )
    salida = gdf.rename(columns=nombres)
    if _mostrar():
        pares = ", ".join(f"'{v}' -> '{n}'" for v, n in nombres.items())
        print(f"renombrar_campos: {pares}")
    return salida


def eliminar_campos(
    gdf: gpd.GeoDataFrame, campos: str | list[str]
) -> gpd.GeoDataFrame:
    """Eliminar campo: quita columnas. Devuelve copia. Falla si alguna no existe.

    capa = geo.eliminar_campos(capa, ["FID_1", "Shape_Leng"])
    """
    campos = _como_lista(campos)
    _exigir_campos(gdf, campos)
    salida = gdf.drop(columns=campos)
    if _mostrar():
        print(f"eliminar_campos: {campos}; quedan {_atributos(salida)}")
    return salida


def conservar_campos(
    gdf: gpd.GeoDataFrame, campos: str | list[str]
) -> gpd.GeoDataFrame:
    """Deja SOLO estos campos (y la geometría), en este orden. Devuelve copia.

    El inverso de `eliminar_campos`, y el útil antes de entregar: se escribe la
    lista de lo que va, no la de lo que sobra, que cambia con cada capa de
    origen.

        entrega = geo.conservar_campos(capa, ["PENDIENTES", "SUP"])
    """
    campos = _como_lista(campos)
    _exigir_campos(gdf, campos)
    fuera = [c for c in _atributos(gdf) if c not in campos]
    salida = gdf[campos + [gdf.geometry.name]].copy()
    if _mostrar():
        print(f"conservar_campos: {campos}; se quitan {fuera}")
    return salida


def ordenar_campos(
    gdf: gpd.GeoDataFrame, campos: str | list[str]
) -> gpd.GeoDataFrame:
    """Pone estos campos PRIMERO, en este orden; el resto detrás, como estaba.

    No quita nada (para eso `conservar_campos`). La geometría va al final.

        capa = geo.ordenar_campos(capa, ["ID", "CLASE"])
    """
    campos = _como_lista(campos)
    _exigir_campos(gdf, campos)
    resto = [c for c in _atributos(gdf) if c not in campos]
    salida = gdf[campos + resto + [gdf.geometry.name]].copy()
    if _mostrar():
        print(f"ordenar_campos: {_atributos(salida)}")
    return salida


def cambiar_tipo(
    gdf: gpd.GeoDataFrame,
    campo: str,
    tipo: str,
    forzar: bool = False,
) -> gpd.GeoDataFrame:
    """Convierte un campo a 'texto', 'entero', 'decimal' o 'fecha'. Devuelve copia.

    **Falla si algún valor no convierte**, y los muestra. Pandas convierte
    'S/D' o '1,5' en nulo sin decir nada, y ese nulo reaparece como un total
    que no cuadra. Con `forzar=True` los deja nulos a propósito y avisa cuántos.

    Dos pérdidas que se cortan aquí:

    - A 'entero' falla si hay decimales, en vez de truncar 2.7 a 2.
    - A 'texto' no inventa ceros a la izquierda: `7` queda `'7'`, no `'007'`.
      Si la clave los lleva: `calcular_campo(capa, 'ID', lambda f:
      str(f.ID).zfill(3))`.

    'fecha' lee día primero (31/12/2025). Un .shp guarda la fecha sin hora.

        capa = geo.cambiar_tipo(capa, "ID", "texto")
    """
    _exigir_campos(gdf, [campo])
    original = gdf[campo]
    nuevo = _convertir(original, tipo)
    perdidos = original.notna() & nuevo.isna()
    if perdidos.any():
        muestra = list(original[perdidos].unique()[:5])
        if not forzar:
            raise ValueError(
                f"'{campo}': {int(perdidos.sum())} valor(es) no convierten a "
                f"{tipo}, p. ej. {muestra}. Corrigelos antes con "
                "reemplazar_valores, o pasa forzar=True para dejarlos nulos"
            )
        print(
            f"Aviso: '{campo}': {int(perdidos.sum())} valor(es) quedaron nulos "
            f"al pasar a {tipo}, p. ej. {muestra}"
        )
    salida = gdf.copy()
    salida[campo] = nuevo
    if _mostrar():
        print(f"cambiar_tipo: '{campo}' {original.dtype} -> {nuevo.dtype}")
    return salida


def reemplazar_valores(
    gdf: gpd.GeoDataFrame,
    campo: str,
    reemplazos: dict[Any, Any],
) -> gpd.GeoDataFrame:
    """Cambia valores de un campo según `{viejo: nuevo}`. Devuelve copia.

    Los valores que no están en `reemplazos` se QUEDAN como estaban, al
    contrario que `Series.map`, que los deja nulos. **Falla si ninguna clave
    aparece** en la capa y avisa de las que no aparecen: casi siempre es el
    tipo (`1` contra `'1'`) o una errata, y sin aviso el reemplazo no ocurre.

        capa = geo.reemplazar_valores(capa, "gridcode", {1: "0-15", 2: "15-60"})
    """
    _exigir_campos(gdf, [campo])
    presentes = set(gdf[campo].dropna().unique())
    ausentes = [k for k in reemplazos if k not in presentes]
    if reemplazos and len(ausentes) == len(reemplazos):
        raise ValueError(
            f"ninguna clave de reemplazos aparece en '{campo}'.\n"
            f"  reemplazos  claves {list(reemplazos)[:5]}\n"
            f"  capa        tipo {gdf[campo].dtype}, valores "
            f"{sorted(presentes, key=str)[:5]}\n"
            "Si se ven iguales, es el tipo: 1 no es '1'"
        )
    if ausentes:
        print(f"Aviso: claves que no aparecen en '{campo}': {ausentes}")
    salida = gdf.copy()
    salida[campo] = salida[campo].replace(reemplazos)
    if _mostrar():
        n = int(gdf[campo].isin(list(reemplazos)).sum())
        print(f"reemplazar_valores: {n} de {len(gdf)} entidades cambiaron en '{campo}'")
    return salida


def ordenar_filas(
    gdf: gpd.GeoDataFrame,
    por: str | list[str],
    descendente: bool = False,
) -> gpd.GeoDataFrame:
    """Ordenar: filas ordenadas por uno o más campos. Devuelve copia.

    Los nulos van al final. El índice se renumera 0..n-1, que evita etiquetas
    repetidas en los pasos siguientes.

        capa = geo.ordenar_filas(capa, "SUP", descendente=True)
    """
    por = _como_lista(por)
    _exigir_campos(gdf, por)
    salida = gdf.sort_values(por, ascending=not descendente).reset_index(drop=True)
    if _mostrar():
        sentido = "descendente" if descendente else "ascendente"
        print(f"ordenar_filas: {len(salida)} entidades por {por} ({sentido})")
    return salida


def crear_capa(
    geometrias: Any,
    crs: Any,
    atributos: Any = None,
    tipos: dict[str, str] | None = None,
) -> gpd.GeoDataFrame:
    """Una capa desde cero: geometrías, CRS y atributos.

    `geometrias`: lista de geometrías shapely, puede estar vacía. `crs`:
    obligatorio (`32613` o `"EPSG:32613"`); una capa sin CRS no se alinea con
    nada después. `atributos`: dict `{campo: lista}` o DataFrame, una fila por
    geometría. `tipos`: `{campo: 'texto' | 'entero' | 'decimal' | 'fecha'}`,
    necesario sobre todo en una capa vacía, donde no hay valores de los que
    deducir el tipo.

        predio = geo.crear_capa([box(0, 0, 100, 100)], 32613,
                                {"NOMBRE": ["LOTE 1"]})
        vacia = geo.crear_capa([], 32613, {"CLASE": [], "SUP": []},
                               tipos={"CLASE": "texto", "SUP": "decimal"})
    """
    if crs is None:
        raise ValueError(
            "crear_capa necesita crs (p. ej. 32613). Una capa sin CRS no se "
            "puede recortar, medir ni reproyectar contra otra"
        )
    geometrias = list(geometrias)
    tabla = pd.DataFrame(atributos if atributos is not None else {})
    if len(tabla.columns) and len(tabla) != len(geometrias):
        raise ValueError(
            f"{len(geometrias)} geometrias y {len(tabla)} filas de atributos: "
            "tiene que haber una fila por geometria"
        )
    salida = gpd.GeoDataFrame(
        tabla.reset_index(drop=True),
        geometry=gpd.GeoSeries(geometrias, dtype="geometry"),
        crs=crs,
    )
    for campo, tipo in (tipos or {}).items():
        salida = _callado(cambiar_tipo)(salida, campo, tipo)
    if _mostrar():
        print(
            f"crear_capa: {len(salida)} entidades, campos {_atributos(salida)}, "
            f"CRS {salida.crs.to_string()}"
        )
    return salida
