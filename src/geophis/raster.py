"""Operaciones ráster: E/S, remuestreo, reproyección, interpolación del MDE, pendiente y rutas.

Wrapper delgado sobre rasterio / scipy / skimage, en la misma línea que el resto de
geophis: una función = una operación conocida, con nombre claro y valores por
defecto sensatos. Las funciones trabajan sobre arrays 2D (numpy) más su
`transform`/`crs`, que es como rasterio maneja los rasters.

QUÉ NO ESTÁ AQUÍ:

- `metrologia.py` : las REGLAS (`resolucion_regla`,
  `min_pixeles_umm`, ...). Aritmética pura, va DEBAJO de este módulo.
- `barridos.py` : lo que MIDE y los barridos. Va ENCIMA de `geometria`.
- `hidrologia.py` : todo pysheds. Va ENCIMA de este módulo; aquí no se importa
  pysheds.

Aquí queda una sola responsabilidad: el ráster y el MDE que sale de las curvas.
"""

import math
import warnings
import os
from collections.abc import Iterable
from os import PathLike
from typing import Any

import contourpy
import numpy as np
import numpy.typing as npt
import pandas as pd
import rasterio
from rasterio import Affine
from rasterio.crs import CRS
from rasterio.mask import mask as _mask
from rasterio.merge import merge as _merge
from rasterio.vrt import WarpedVRT
from rasterio.features import (
    rasterize as _rasterize,
    shapes as _shapes,
    sieve as _sieve,
)
from rasterio.warp import reproject, Resampling, calculate_default_transform
import shapely
from shapely.geometry import shape, LineString, box as _caja
from shapely.ops import unary_union
import geopandas as gpd
from scipy.ndimage import (
    binary_dilation,  # engorda una máscara booleana un píxel
    distance_transform_edt,  # distancia al píxel válido más cercano
    correlate,  # aplica un kernel 3x3 tal cual (para el algoritmo de Horn)
    minimum as _minimo_zonal,  # min por etiqueta (estadisticas_zonales)
    maximum as _maximo_zonal,
)
from scipy.interpolate import LinearNDInterpolator, RBFInterpolator
from scipy.spatial import cKDTree, QhullError
from scipy.sparse import csr_array
from scipy.sparse.csgraph import dijkstra
from skimage.graph import MCP_Geometric
from numpy.linalg import LinAlgError
from threadpoolctl import threadpool_limits

from .metrologia import (
    _de_parametros,
    _detectar_equidistancia,
    _pendiente_espuria,
    _redondear_limpio,
)
from .proyeccion import _a_crs, _exigir_crs
from ._salida import _mostrar

FloatArray = npt.NDArray[np.floating[Any]]

# mínimo de puntos de prueba para que las métricas de validación signifiquen algo
MIN_PRUEBA = 10


def _paso(res: float, crs: Any = None) -> str:
    """El tamaño de píxel para una línea de reporte, en las unidades del CRS.

    `.6g` para que un paso en grados se lea. Con un CRS proyectado dice `m/px`,
    que es lo que el usuario espera leer en el 99 % de las corridas; en
    cualquier otro caso (geográfico, o sin CRS) dice `u/px`, unidades del CRS.
    """
    proyectado = crs is not None and crs.is_projected
    return f"{res:.6g} {'m/px' if proyectado else 'u/px'} | CRS: {crs}"


def _a_valor(arr: FloatArray, src: Any, bandas: Iterable[int] = (1,)) -> FloatArray:
    """Crudo del archivo a valor físico: nodata a NaN, luego `crudo * scale + offset`.

    El helper que comparten los lectores (`cargar_raster`, `cargar_bandas`,
    `recortar_raster`) y `mosaico`: la escala vive en UN sitio.

    ORDEN OBLIGATORIO: el nodata se compara contra el valor CRUDO. Al revés, el
    0 de nodata de un Sentinel-2 se vuelve -0.1 y entra como dato.

    Por qué existe: un Sentinel-2 L2A de línea base 04.00 o posterior declara
    `scale=0.0001` y `offset=-0.1` y trae el desfase de +1000 SIN aplicar.
    Leído crudo, rojo 0.03 y NIR 0.30 dan un NDVI de 0.51 en vez de 0.82: el
    mapa se ve creíble y está mal. INEGI, CEM y casi todo lo demás declara
    `scale=1`, `offset=0`, y ahí esto no toca nada.

    `arr` ya en float; el nodata se marca en su sitio.
    """
    if src.nodata is not None:
        arr[arr == src.nodata] = np.nan
    bandas = list(bandas)
    esc = np.array([src.scales[b - 1] for b in bandas], dtype=float)
    off = np.array([src.offsets[b - 1] for b in bandas], dtype=float)
    if np.all(esc == 1) and np.all(off == 0):
        return arr
    forma = (-1, 1, 1) if arr.ndim == 3 else ()
    return arr * esc.reshape(forma) + off.reshape(forma)


def cargar_raster(ruta: str | PathLike[str]) -> tuple[FloatArray, Affine, CRS, float]:
    """Abre un raster de una banda.

    El nodata declarado en el archivo (p. ej. -9999) se convierte a NaN, que es
    como el resto del módulo marca el NoData (rellenar_nodata, remuestrear...).
    Aplica `scale`/`offset` del archivo si los declara (ver `_a_valor`).
    Devuelve (array 2D float, transform, crs, resolucion_m).
    """
    with rasterio.open(ruta) as src:
        arr = _a_valor(src.read(1).astype(float), src)
        tf = src.transform
        crs = src.crs
        res = abs(src.transform[0])
    if _mostrar():
        print(f"Raster '{ruta}': {arr.shape[1]}x{arr.shape[0]} px | {_paso(res, crs)}")
    return arr, tf, crs, res


def cargar_bandas(
    ruta: str | PathLike[str],
    bandas: tuple[int, ...] = (1, 2, 3),
) -> tuple[FloatArray, Affine, CRS, float]:
    """Abre varias bandas de un ráster. La hermana multibanda de `cargar_raster`.

    `cargar_raster` lee la banda 1 y ya; para un índice de vegetación hacen
    falta 2 o 3. Devuelve la MISMA tupla de 4 que `cargar_raster`, con el array
    en 3D `(banda, y, x)` en el orden en que pediste las bandas, así un script
    cambia una función por la otra sin tocar el resto.

    Las bandas se numeran DESDE 1, como rasterio y GDAL.

    El array sale en float aunque el archivo sea uint8 o uint16, y no es
    cosmético: los índices son cocientes de diferencias, y en aritmética entera
    `nir - rojo` hace underflow y devuelve un número grande y plausible en vez
    de un negativo. El mapa sale creíble y mal.

    Aplica `scale`/`offset` de cada banda si el archivo los declara.
    """
    bandas = tuple(bandas)
    if not bandas:
        raise ValueError("`bandas` no puede ir vacío")
    with rasterio.open(ruta) as src:
        malas = [b for b in bandas if not 1 <= b <= src.count]
        if malas:
            raise ValueError(
                f"'{ruta}' tiene {src.count} banda(s); pediste {malas}. "
                "Las bandas se numeran desde 1, como en rasterio y GDAL."
            )
        arr = _a_valor(src.read(list(bandas)).astype(float), src, bandas)
        tf = src.transform
        crs = src.crs
        res = abs(src.transform[0])
    if _mostrar():
        print(
            f"Raster '{ruta}': {len(bandas)} bandas {bandas} de "
            f"{arr.shape[2]}x{arr.shape[1]} px | {_paso(res, crs)}"
        )
    return arr, tf, crs, res


def guardar_raster(
    arr: Any,
    ruta: str | PathLike[str],
    transform: Affine,
    crs: Any,
    nombres: Iterable[str] | None = None,
) -> str:
    """Escribe un array a un GeoTIFF float32, de una banda o de varias.

    `transform` y `crs` son los que devolvió `interpolar_mde`, `cargar_raster`
    o `mosaico`. El NoData se declara NaN, que es como el módulo lo marca.

    `arr` puede ser cualquier cosa convertible a array 2D, un array 3D
    `(banda, y, x)` o una lista de arrays 2D de la misma forma: cada uno es una
    banda.

    NO guardes aquí el MDE de `acondicionar_mde` para usarlo después en flujo:
    es float64 y su micro-pendiente (~1e-5 m) no cabe en float32. Releído,
    vuelve a tener miles de sumideros. Acondiciona en la sesión del flujo.

    `nombres`: una descripción por banda, que los SIG de escritorio muestran. Se
    recomienda SIEMPRE en multibanda: el orden de bandas no es universal y el
    nombre es lo único que viaja con el archivo.
    """
    # ponytail: siempre float32, un categórico uint8 se guarda como float.
    # Agrega parámetro dtype si algún día pesa el tamaño de archivo.
    if isinstance(arr, (list, tuple)):
        formas = {np.shape(a) for a in arr}
        if len(formas) > 1:
            raise ValueError(f"las bandas tienen formas distintas: {sorted(formas)}")
    arr = np.asarray(arr, dtype=float)
    if arr.ndim == 2:
        arr = arr[None]
    if arr.ndim != 3:
        raise ValueError(f"el array debe ser 2D o 3D (banda, y, x); llego {arr.ndim}D")
    nombres = None if nombres is None else tuple(nombres)
    if nombres is not None and len(nombres) != arr.shape[0]:
        raise ValueError(f"{len(nombres)} nombres para {arr.shape[0]} banda(s)")
    # `nodata=NaN` siempre: se escribe float32 y el módulo marca el NoData con
    # NaN, así que el archivo tiene que DECLARAR NaN. Un .tif que dice -9999 y
    # trae NaN lo relee bien geophis, pero otros SIG leen esos NaN como dato.
    perfil = {
        "driver": "GTiff",
        "crs": crs,
        "transform": transform,
        "count": arr.shape[0],
        "dtype": "float32",
        "nodata": np.nan,
        "height": arr.shape[1],
        "width": arr.shape[2],
    }
    os.makedirs(os.path.dirname(os.path.abspath(ruta)), exist_ok=True)
    with rasterio.open(ruta, "w", **perfil) as dst:
        dst.write(arr.astype(np.float32))
        for i, nombre in enumerate(nombres or (), start=1):
            dst.set_band_description(i, nombre)
    if _mostrar():
        print(f"Raster guardado '{ruta}'")
    return str(ruta)


def recortar_raster(
    ruta: str | PathLike[str], mascara: gpd.GeoDataFrame, nodata: float = np.nan
) -> tuple[FloatArray, Affine, CRS, float]:
    """Recorta un raster al contorno de una capa vector.

    Reproyecta la máscara al CRS del raster si difieren. Los píxeles fuera
    del contorno quedan como `nodata`. Aplica `scale`/`offset` del archivo si
    los declara (ver `_a_valor`). Devuelve
    (array, transform, crs, resolucion_m).

    Cuando no se pisan, el error trae LOS DOS EXTENTS. No es decoración: como
    esta función YA alinea el CRS, un "no intersecta" solo puede significar que
    la escena no cubre la zona: el mosaico equivocado, la tesela vecina, otra
    fecha. Y eso se arregla consiguiendo otro archivo, no tocando el código. Un
    mensaje sin las dos cajas manda a revisar la proyección, que es justo lo que
    ya está bien. **El fallo no es no fallar, es no decir por qué.**
    """
    with rasterio.open(ruta) as src:
        m = _a_crs(mascara, src.crs, "mascara")
        geoms = [g.__geo_interface__ for g in m.geometry]
        try:
            # filled=False: rasterio rellena en el dtype del ARCHIVO, y un NaN
            # no cabe en int16 (cualquier producto INEGI entero). Se castea a
            # float y se rellena aqui, donde el NaN si cabe.
            recorte, tf = _mask(src, geoms, crop=True, filled=False)
        except ValueError as e:
            # rasterio lanza ValueError si las formas no pisan el raster
            rx0, ry0, rx1, ry1 = src.bounds
            mx0, my0, mx1, my1 = m.total_bounds
            dx = max(0.0, mx0 - rx1, rx0 - mx1)
            dy = max(0.0, my0 - ry1, ry0 - my1)
            raise ValueError(
                f"la máscara no intersecta el raster '{ruta}'.\n"
                f"  raster   ({rx0:.0f}, {ry0:.0f}) - ({rx1:.0f}, {ry1:.0f})\n"
                f"  mascara  ({mx0:.0f}, {my0:.0f}) - ({mx1:.0f}, {my1:.0f})\n"
                f"  separadas {dx / 1000:.1f} km en x y {dy / 1000:.1f} km en y, "
                f"en {src.crs}.\n"
                f"  El CRS ya se alineó aquí, así que no es de proyección: esta "
                f"escena no cubre la zona. Consigue la que la cubra."
            ) from e
        # NaN primero y `nodata` al final: la escala no debe tocar el relleno.
        # El nodata declarado del archivo (p. ej. -9999) también pasa a
        # `nodata`, igual que hace cargar_raster; si no, entraría como dato.
        arr = _a_valor(recorte[0].astype(float).filled(np.nan), src)
        arr[np.isnan(arr)] = nodata
        crs = src.crs
        res = abs(src.transform[0])
    return arr, tf, crs, res


def mosaico(
    rutas: Iterable[str | PathLike[str]],
    mascara: gpd.GeoDataFrame,
    crs: Any = None,
    metodo: Resampling = Resampling.nearest,
) -> tuple[FloatArray, Affine, CRS, float]:
    """Junta varios rásters de una banda en uno, leyendo solo el rectángulo de `mascara`.

    `rutas` son archivos o URL de COG: sobre un COG solo se bajan los bloques
    que pisa la máscara, no la escena. Devuelve la misma tupla de 4 que
    `recortar_raster`: `(array, transform, crs, resolucion)`, con NaN
    fuera del polígono.

    CRS de salida: `crs` si viene; si no, el del primer ráster. Una fuente en
    otro CRS (una tesela del huso vecino) se reproyecta al vuelo con `metodo`.
    La malla es la del primer ráster y la resolución también.

    **EL ORDEN DE `rutas` ES LA PRIORIDAD:** donde dos fuentes se solapan gana
    la primera. Pon primero la que prefieras.

    `metodo` es NEAREST por defecto, no bilinear: casi siempre se junta en la
    misma malla y no hay nada que remuestrear, y si se reproyecta una banda
    categórica (la `scl` de Sentinel-2) bilinear inventa clases. Es la misma
    trampa de `reproyectar_raster`.

    Aplica nodata y `scale`/`offset` como los lectores. Si las fuentes
    declaran escalas u offsets distintos entre sí, falla: `merge` junta
    valores CRUDOS y después no hay forma correcta de escalarlos.

    Donde el polígono queda sin dato (una tesela que falta) no es error: se
    avisa con el %, con `geo.MOSTRAR` o sin él.
    """
    rutas = list(rutas)
    if not rutas:
        raise ValueError("`rutas` va vacío: no hay nada que juntar")
    _exigir_crs(mascara, "mascara")
    # sin READDIR GDAL lista el "directorio" de S3 en cada apertura; los
    # reintentos cubren los 5xx sueltos al leer bloques de un COG remoto
    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        GDAL_HTTP_MAX_RETRY=3,
        GDAL_HTTP_RETRY_DELAY=1,
    ):
        origenes = [rasterio.open(r) for r in rutas]
        fuentes: list[Any] = []
        try:
            crs_sal = CRS.from_user_input(crs) if crs is not None else origenes[0].crs
            escalas = {(o.scales[0], o.offsets[0]) for o in origenes}
            if len(escalas) > 1:
                raise ValueError(
                    f"las fuentes declaran escala/desfase distintos {sorted(escalas)}: "
                    "merge junta valores crudos y no se pueden escalar despues"
                )
            fuentes = [
                o if o.crs == crs_sal else WarpedVRT(o, crs=crs_sal, resampling=metodo)
                for o in origenes
            ]
            m = mascara.to_crs(crs_sal)
            mx0, my0, mx1, my1 = m.total_bounds
            if not any(
                f.bounds.left < mx1
                and f.bounds.right > mx0
                and f.bounds.bottom < my1
                and f.bounds.top > my0
                for f in fuentes
            ):
                cajas = "\n".join(
                    f"  fuente   ({f.bounds.left:.0f}, {f.bounds.bottom:.0f}) - "
                    f"({f.bounds.right:.0f}, {f.bounds.top:.0f})"
                    for f in fuentes
                )
                raise ValueError(
                    f"ninguna fuente pisa la mascara, en {crs_sal}.\n{cajas}\n"
                    f"  mascara  ({mx0:.0f}, {my0:.0f}) - ({mx1:.0f}, {my1:.0f})\n"
                    f"  El CRS ya se alineó aquí: estas escenas no cubren la zona."
                )
            # bounds pegados a la malla de la primera fuente, para no remuestrear
            # medio pixel al leer
            t0 = fuentes[0].transform
            rx, ry = t0.a, -t0.e
            x0 = t0.c + math.floor((mx0 - t0.c) / rx) * rx
            x1 = t0.c + math.ceil((mx1 - t0.c) / rx) * rx
            y1 = t0.f - math.floor((t0.f - my1) / ry) * ry
            y0 = t0.f - math.ceil((t0.f - my0) / ry) * ry
            datos, tf = _merge(
                fuentes,
                bounds=(x0, y0, x1, y1),
                res=(rx, ry),
                nodata=np.nan,
                dtype="float64",
                resampling=metodo,
            )
            # el nodata ya salió NaN en merge (cada fuente se lee enmascarada)
            arr = _a_valor(datos[0], origenes[0])
        finally:
            for f in fuentes + origenes:
                f.close()
    dentro = rasterizar(m, arr.shape, tf, valor=1) == 1
    arr[~dentro] = np.nan
    if dentro.any():
        hueco = 100 * np.isnan(arr[dentro]).mean()
        if hueco > 0:
            print(f"mosaico: {hueco:.1f} % de la mascara sin dato")
    res = abs(tf.a)
    if _mostrar():
        print(
            f"mosaico: {len(rutas)} fuente(s) -> {arr.shape[1]}x{arr.shape[0]} px "
            f"| {_paso(res, crs_sal)}"
        )
    return arr, tf, crs_sal, res


def rellenar_nodata(arr: FloatArray) -> FloatArray:
    """Rellena los NaN con el valor del píxel válido más cercano.

    No interpola: copia el vecino, evitando inventar valores. Con NaN
    devuelve un array nuevo; sin NaN devuelve el mismo array, sin copiar.
    """
    nan_mask = np.isnan(arr)
    if not nan_mask.any():
        return arr
    idx = distance_transform_edt(nan_mask, return_distances=False, return_indices=True)
    return arr[tuple(idx)]


def mascara_relleno(
    mde_original: FloatArray, iteraciones: int = 1
) -> npt.NDArray[np.bool_]:
    """Celdas cuya ventana 3x3 quedó contaminada por `rellenar_nodata`.

    `rellenar_nodata` copia el píxel válido más cercano, así que la zona
    rellenada es una meseta plana: `calcular_pendiente` le da 0% y
    `calcular_orientacion` la deja sin orientación, sin que nada la marque. Un flujo
    que no rellena el NoData por copia no tiene este problema.

    Pásale el MDE ANTES de rellenar y aplica el resultado a la salida:

        relleno = mascara_relleno(mde)
        mde = rellenar_nodata(mde)
        pendiente = calcular_pendiente(mde, res)
        pendiente[relleno] = np.nan

    La máscara sale dilatada un píxel: el anillo alrededor de la zona
    rellenada también está contaminado, porque esos píxeles calcularon su
    ventana 3x3 con valores copiados. Sube `iteraciones` si encadenas otra
    operación de ventana después de la pendiente.

    Devuelve un array booleano del mismo shape. Sin NaN devuelve todo False.
    """
    if iteraciones < 1:
        raise ValueError(f"iteraciones {iteraciones} debe ser >= 1")
    # elemento 3x3 LLENO, no la cruz por defecto: el pixel pegado en diagonal a
    # una celda rellenada tambien la metio en su ventana de Horn. Con la cruz la
    # mascara dejaria fuera justo las cuatro esquinas del anillo.
    return binary_dilation(
        np.isnan(mde_original), structure=np.ones((3, 3), bool), iterations=iteraciones
    )


def remuestrear(
    arr: FloatArray,
    transform: Affine,
    crs: CRS,
    res_destino: float,
    metodo: Resampling = Resampling.bilinear,
) -> tuple[FloatArray, Affine]:
    """Cambia la resolución de un raster.

    Bilineal por defecto (correcto para datos continuos como la elevación:
    sin efecto escalera). Devuelve (array nuevo, transform nuevo) con el
    mismo origen geográfico.
    """
    if res_destino <= 0:
        raise ValueError(f"res_destino {res_destino} debe ser > 0")
    res_actual = abs(transform[0])
    factor = res_actual / res_destino
    # ceil: int() trunca y pierde la fila/columna del borde
    nuevo_ancho = math.ceil(arr.shape[1] * factor)
    nuevo_alto = math.ceil(arr.shape[0] * factor)

    tf_nuevo = rasterio.transform.Affine(
        res_destino,
        0.0,
        transform.c,
        0.0,
        -res_destino,
        transform.f,
    )
    # NaN y no empty: con ceil la malla sobresale de la fuente y la franja
    # sobrante quedaria en 0.0 o en memoria sin inicializar
    destino = np.full((nuevo_alto, nuevo_ancho), np.nan, dtype=np.float32)
    reproject(
        source=arr.astype(np.float32),
        destination=destino,
        src_transform=transform,
        src_crs=crs,
        src_nodata=np.nan,
        dst_transform=tf_nuevo,
        dst_crs=crs,
        dst_nodata=np.nan,
        resampling=metodo,
    )
    return destino.astype(float), tf_nuevo


def reproyectar_raster(
    arr: FloatArray,
    transform: Affine,
    crs: CRS,
    crs_destino: Any,
    res_destino: float | None = None,
    metodo: Resampling = Resampling.bilinear,
) -> tuple[FloatArray, Affine]:
    """Cambia el CRS de un ráster (warp).

    `remuestrear` cambia la resolución dentro del mismo CRS; esta cambia el CRS,
    y de paso la resolución si le das `res_destino`. Con `res_destino=None`
    rasterio elige la que conserva aproximadamente el número de píxeles, que es
    lo que hacen los SIG de escritorio por defecto.

    El tamaño de la malla de salida NO se hereda: se recalcula con
    `calculate_default_transform`, porque el rectángulo de entrada deja de ser
    un rectángulo al cambiar de proyección y forzar el shape recortaría las
    esquinas en silencio.

    TRAMPA, la de siempre con esta herramienta: `metodo` es BILINEAL por
    defecto, correcto para datos continuos (elevación, pendiente, un índice de
    vegetación) y **incorrecto para un ráster CATEGÓRICO**. Interpolar entre la
    clase 2 y la 4 inventa una clase 3 que no existe en ninguna leyenda, y
    nada avisa porque el resultado es un número válido. Para códigos de
    `reclasificar_rangos`, para cuencas etiquetadas o para cualquier cosa cuyo
    valor sea una ETIQUETA, pasa `metodo=Resampling.nearest`. No se puede
    detectar desde aquí: un array de códigos y uno de elevaciones son los dos
    floats.

    El NoData viaja como NaN en los dos sentidos, igual que en el resto del
    módulo. Devuelve (array nuevo, transform nuevo).
    """
    if res_destino is not None and res_destino <= 0:
        raise ValueError(f"res_destino {res_destino} debe ser > 0")
    if crs is None or crs_destino is None:
        # rasterio falla mas abajo con un mensaje que no dice cual de los dos es
        raise ValueError(f"hacen falta los dos CRS: crs={crs}, crs_destino={crs_destino}")

    alto, ancho = arr.shape
    limites = rasterio.transform.array_bounds(alto, ancho, transform)
    # array_bounds da (izq, abajo, der, arriba); calculate_default_transform los
    # quiere en ese mismo orden, pero como sueltos
    tf_nuevo, ancho_n, alto_n = calculate_default_transform(
        crs,
        crs_destino,
        ancho,
        alto,
        *limites,
        resolution=res_destino,
    )
    destino = np.full((alto_n, ancho_n), np.nan, dtype=np.float32)
    reproject(
        source=arr.astype(np.float32),
        destination=destino,
        src_transform=transform,
        src_crs=crs,
        src_nodata=np.nan,
        dst_transform=tf_nuevo,
        dst_crs=crs_destino,
        dst_nodata=np.nan,
        resampling=metodo,
    )
    if _mostrar():
        # 'u/px', no 'm/px', y con precision suficiente: esta es LA funcion que
        # cruza de metros a grados, y el resto del modulo imprime metros porque
        # asume UTM. Con EPSG:4326 el paso vale ~0.0001, que en '.2f' sale 0.00.
        print(
            f"reproyectar_raster: {ancho}x{alto} px -> {ancho_n}x{alto_n} px | "
            f"paso {abs(transform[0]):.6g} -> {abs(tf_nuevo[0]):.6g} u/px "
            f"(unidades del CRS) | {metodo.name}"
        )
    return destino.astype(float), tf_nuevo


# Los índices y las bandas que pide cada uno. Fuera de `__all__`: la lista de
# nombres válidos ya sale en el mensaje de error de `indice_vegetacion`.
_INDICES: dict[str, tuple[str, ...]] = {
    "ndvi": ("nir", "rojo"),
    "ndmi": ("nir", "swir1"),
    "nbr": ("nir", "swir2"),
    "ndwi": ("verde", "nir"),
    "mndwi": ("verde", "swir1"),
    "vari": ("rojo", "verde", "azul"),
    "exg": ("rojo", "verde", "azul"),
}


def indice_vegetacion(
    tipo: str,
    rojo: FloatArray | None = None,
    verde: FloatArray | None = None,
    azul: FloatArray | None = None,
    nir: FloatArray | None = None,
    swir1: FloatArray | None = None,
    swir2: FloatArray | None = None,
) -> FloatArray:
    """Índice de vegetación a partir de las bandas de `cargar_bandas`.

    Devuelve un array float continuo. **No umbraliza**: para pasar de índice a
    clases está `reclasificar_rangos`, que ya resuelve la convención de bordes
    `(mín, máx]` y devuelve el diccionario de etiquetas. Meter aquí un `umbral`
    sería la misma operación escrita dos veces.

    Las bandas van POR NOMBRE, no como un stack posicional, y es a propósito:
    el orden de bandas de una imagen no es universal (RGB, BGR, y las
    multiespectrales ponen el NIR donde les parece). Un stack se equivoca de
    banda en SILENCIO y el mapa resultante sigue pareciendo un mapa.

    Índices
    -------
    ndvi : `(nir - rojo) / (nir + rojo)`. El bueno, si la imagen trae NIR.
        Rango [-1, 1]; vegetación por encima de ~0.2, agua en negativo.
    ndmi : `(nir - swir1) / (nir + swir1)`. Humedad de la vegetación.
    nbr : `(nir - swir2) / (nir + swir2)`. Biomasa y perturbación (quemas).
    ndwi : `(verde - nir) / (verde + nir)` (McFeeters). Agua abierta en positivo.
    mndwi : `(verde - swir1) / (verde + swir1)` (Xu). Agua en positivo; separa
        mejor que `ndwi` el agua de lo construido y del suelo desnudo.
    vari : `(verde - rojo) / (verde + rojo - azul)`. Para RGB PURO (captura de
        pantalla de un mapa base, dron sin NIR). Ojo: su denominador puede acercarse
        a cero o cambiar de signo en píxeles muy azules o muy oscuros, y ahí el
        índice se dispara. Es una limitación del índice, no de esta función.
    exg : `(2*verde - rojo - azul) / (rojo + verde + azul)`, el *Excess Green*
        sobre coordenadas cromáticas. Más estable que `vari` en sombra, porque
        el denominador es una suma de positivos. Rango [-1, 2].

    Los píxeles con denominador 0 (negro puro) salen NaN, no infinito: son
    ausencia de dato, y NaN es como el módulo la marca.
    """
    dadas = {
        "rojo": rojo,
        "verde": verde,
        "azul": azul,
        "nir": nir,
        "swir1": swir1,
        "swir2": swir2,
    }
    if tipo not in _INDICES:
        raise ValueError(f"tipo '{tipo}' desconocido; usa uno de {sorted(_INDICES)}")
    faltan = [n for n in _INDICES[tipo] if dadas[n] is None]
    if faltan:
        raise ValueError(f"el indice '{tipo}' necesita las bandas {faltan}")

    b = {k: np.asarray(v, dtype=float) for k, v in dadas.items() if v is not None}
    formas = {a.shape for a in b.values()}
    if len(formas) > 1:
        raise ValueError(f"las bandas tienen formas distintas: {sorted(formas)}")

    if tipo == "vari":
        num, den = b["verde"] - b["rojo"], b["verde"] + b["rojo"] - b["azul"]
    elif tipo == "exg":
        num = 2 * b["verde"] - b["rojo"] - b["azul"]
        den = b["rojo"] + b["verde"] + b["azul"]
    else:  # diferencia normalizada: ndvi, ndmi, nbr, ndwi, mndwi
        x, y = (b[n] for n in _INDICES[tipo])
        num, den = x - y, x + y

    with np.errstate(divide="ignore", invalid="ignore"):
        idx = num / den
    idx[den == 0] = np.nan
    return idx


def _muestrear_curvas(
    curvas: gpd.GeoDataFrame, campo: str, paso: float
) -> tuple[FloatArray, FloatArray, FloatArray, npt.NDArray[Any]]:
    """Muestrea puntos (x, y, z) a lo largo de cada curva cada `paso` unidades.

    Muestrear a intervalo fijo, en vez de usar los vértices tal cual, evita el
    sesgo de densidad: los tramos sinuosos tienen muchos vértices y los rectos
    pocos.

    El muestreo va vectorizado (`shapely.line_interpolate_point` sobre todos
    los puntos de golpe): un `interpolate()` por punto son cientos de miles de
    llamadas a GEOS de una en una, el paso más lento de la etapa de MDE.

    Devuelve (x, y, z, origen); `origen` es el `curvas.index` de la curva de la
    que salió cada punto, para poder rastrear un residual hasta su curva.
    """
    geoms = curvas.geometry.to_numpy()
    z = curvas[campo].to_numpy(dtype=float)
    # una cota NaN envenena la interpolación (parches NaN)
    ok = ~(shapely.is_missing(geoms) | shapely.is_empty(geoms) | np.isnan(z))
    lineas, fila = shapely.get_parts(geoms[ok], return_index=True)
    if not len(lineas):
        return np.empty(0), np.empty(0), np.empty(0), np.empty(0)
    cotas = z[ok][fila]
    origen = curvas.index.to_numpy()[ok][fila]

    largos = shapely.length(lineas)
    # puntos por tramo: uno cada `paso` desde el origen, más el del origen.
    # El último cae en el múltiplo de `paso` que quepa, no en el final del
    # tramo (salvo que el tramo sea más corto que `paso`).
    n = np.maximum((largos // paso).astype(int), 1) + 1
    idx = np.repeat(np.arange(len(lineas)), n)
    # posición del punto dentro de su propio tramo: 0, 1, 2... reiniciando
    orden = np.arange(idx.size) - np.repeat(np.cumsum(n) - n, n)
    dist = np.minimum(orden * paso, largos[idx])
    pts = shapely.get_coordinates(shapely.line_interpolate_point(lineas[idx], dist))
    return pts[:, 0], pts[:, 1], cotas[idx], origen[idx]


def _interp_idw(
    pts: FloatArray,
    zs: FloatArray,
    malla: FloatArray,
    vecinos: int = 8,
    potencia: float = 2.0,
) -> FloatArray:
    """Distancia inversa ponderada sobre los `vecinos` más cercanos.

    Respaldo cuando el TIN falla por geometría degenerada. Extrapola: la
    envolvente la recorta `_interpolar_malla`.
    """
    arbol = cKDTree(pts)
    k = min(vecinos, len(pts))
    dist, idx = arbol.query(malla, k=k)
    if k == 1:
        dist = dist[:, None]
        idx = idx[:, None]
    dist = np.where(dist == 0, 1e-12, dist)  # coincidencia exacta: no dividir por 0
    w = 1.0 / dist**potencia
    return (w * zs[idx]).sum(axis=1) / w.sum(axis=1)


def _interp_spline(
    pts: FloatArray,
    zs: FloatArray,
    malla: FloatArray,
    vecinos: int = 48,
    suavizado: float = 0.0,
) -> FloatArray:
    """Spline de placa delgada (thin-plate) local sobre los `vecinos` más cercanos.

    Alternativa suave al TIN: evita el escalonado que producen los triángulos
    planos entre curvas (que sesga pendiente y aspecto). Usa RBF local (un ajuste
    por vecindario) para escalar a muchos puntos sin resolver un sistema denso
    global. Extrapola: la envolvente la recorta `_interpolar_malla`.
    `suavizado`=0 interpola exacto; >0 relaja el ajuste.

    Asume los puntos ya deduplicados por XY (lo hace `_interpolar_malla`): XY
    repetidos dejan singular el sistema RBF con `suavizado=0`.
    """
    k = min(vecinos, len(pts))
    interp = RBFInterpolator(
        pts, zs, kernel="thin_plate_spline", neighbors=k, smoothing=suavizado
    )
    # BLAS a UN hilo. scipy resuelve un sistema (k+3)x(k+3) por celda, y a
    # partir de k ~ 100 OpenBLAS reparte cada uno entre todos los nucleos: el
    # reparto cuesta mas que la cuenta. Medido en 4 nucleos, ms por sistema con
    # k = 90 / 100 / 122: 0.56 / 1.12 / 3.75 multihilo, 0.60 / 0.53 / 0.86 a un
    # hilo. Era el acantilado de 90 a 122 en el barrido de `vecinos` de un predio real
    # (168 s -> 937 s). ponytail: un solo nucleo; teselas en paralelo si hace
    # falta mas.
    try:
        with threadpool_limits(limits=1, user_api="blas"):
            z = interp(malla)
    except LinAlgError as e:
        # El TPS lleva cola polinomica de grado 1, que en 2D necesita tres
        # puntos NO colineales por vecindario. Si los `k` mas cercanos caen
        # todos sobre la misma curva, la matriz pierde rango y scipy tira un
        # "Singular matrix ... rank 2/3" que no menciona curvas por ningun
        # lado. Pasa cuando la nube es anisotropa: muestreo MUCHO mas fino a lo
        # largo de la curva que la separacion ENTRE curvas, que es la misma
        # anisotropia que hace ondular a la RBF (ver `intervalo_muestreo`), solo
        # que llevada al extremo. El caso tipico es un `intervalo_muestreo` mil
        # veces menor de lo que se creia, por confundir `p_max` en % con la
        # fraccion: `e/100` en vez de `e/1.0`.
        #
        # No hay respaldo automatico a proposito: el metodo que se usa es la
        # decision que mas mueve la superficie por rango, y cambiarlo solo seria
        # peor que fallar. Se falla diciendo que mirar.
        raise ValueError(
            f"el spline no puede resolver un vecindario: los {k} puntos mas "
            f"cercanos son colineales (todos sobre la misma curva), asi que la "
            f"cola polinomica del thin-plate queda singular. Causa casi "
            f"siempre: `intervalo_muestreo` mucho mas fino que la separacion "
            f"entre curvas. Revisa que no venga de dividir la equidistancia "
            f"entre `p_max` en POR CIENTO (e/100) en vez de entre la fraccion "
            f"(e/1.0). Si el muestreo es el que quieres, sube `vecinos` para "
            f"que el vecindario alcance la curva de al lado, o usa "
            f"`metodo='tin'`. Original: {e}"
        ) from e
    return z


def _separacion_horizontal(
    curvas: gpd.GeoDataFrame, bounds: tuple[float, float, float, float]
) -> float | None:
    """Separación horizontal media entre curvas dentro de `bounds`, por G'(0).

    Envuelve `geometria.separacion_media`, que mide el área de una banda
    estrecha alrededor de las curvas en vez del cociente área/longitud. La
    diferencia NO es cosmética: área/longitud divide entre la longitud REAL
    del trazo, así que el zigzag de la cartografía la infla y la separación
    sale baja por ese mismo factor. Con el valor sesgado el muestreo sale más
    fino que la separación, que es justo la anisotropía que hace ondular a la
    RBF, y esa ondulación cae en el rango de pendiente más alto.

    El import va diferido porque `geometria` importa de `raster`; a nivel de
    módulo sería circular.

    Devuelve None si no se puede medir (sin curvas dentro, sin extensión).
    """
    from .geometria import separacion_media

    zona = gpd.GeoDataFrame(geometry=[_caja(*bounds)], crs=curvas.crs)
    lam, _, _ = separacion_media(curvas, zona)
    if lam == lam:  # NaN != NaN
        return float(lam)
    # respaldo: sin curvas dentro de la zona, G'(0) no mide. El cociente
    # global sesga, pero es preferible a no tener parámetros.
    return _separacion_media(curvas)


def _separacion_media(curvas: gpd.GeoDataFrame) -> float | None:
    """Separación HORIZONTAL media entre curvas (área/longitud). RESPALDO.

    Sesgada a la baja por el zigzag del trazo; la usa `_separacion_horizontal`
    solo cuando G'(0) no puede medir. Ver ahí el porqué.

    Para n curvas más o menos paralelas dentro de un área A, cada una cubre una
    banda de ancho d, así que A ≈ largo_total · d y por tanto d = A / L. Es una
    estimación de brocha gorda, pero es el número que gobierna los dos
    parámetros de la malla, y es horizontal: la equidistancia es vertical y
    usarla como tamaño de píxel mezcla dimensiones (20 m de salto de cota no
    dicen nada sobre cuántos metros hay de una curva a la otra en el suelo).

    Devuelve None si no se puede medir (sin largo o sin extensión).
    """
    minx, miny, maxx, maxy = curvas.total_bounds
    largo = float(curvas.geometry.length.sum())
    area = float((maxx - minx) * (maxy - miny))
    if largo <= 0 or area <= 0:
        return None
    return area / largo


def _resolver_parametros(
    curvas: gpd.GeoDataFrame,
    campo_elevacion: str,
    resolucion: float | None,
    equidistancia: float | None,
    intervalo_muestreo: float | None,
    bounds: tuple[float, float, float, float],
    metodo: str = "spline",
    lambda_m: float | None = None,
    amplitud_rizo: float | None = None,
) -> tuple[float, float | None, float, float | None]:
    """Resuelve resolucion/equidistancia/muestreo con los defaults de interpolar_mde.

    Compartido por interpolar_mde y validar_mde para que ambos deriven los
    parámetros exactamente igual. `bounds` es el extent de interpolación: la
    separación se mide SOBRE ESA ZONA, no sobre el bounding box de las curvas,
    que puede ser bastante mayor. Devuelve
    (resolucion, equidistancia, intervalo_muestreo, separacion).

    Las guardas de `> 0` viven aquí y no en cada llamador porque los dos entran
    por esta puerta. Sin ellas, `intervalo_muestreo=0` llega a
    `_muestrear_curvas` como `largos // 0` -> `inf` -> `.astype(int)`, que es un
    entero basura enorme: no revienta, reserva memoria hasta que el sistema mata
    el proceso.
    """
    if resolucion is not None and resolucion <= 0:
        raise ValueError(f"resolucion {resolucion} debe ser > 0")
    if intervalo_muestreo is not None and intervalo_muestreo <= 0:
        raise ValueError(f"intervalo_muestreo {intervalo_muestreo} debe ser > 0")
    if equidistancia is not None and equidistancia <= 0:
        raise ValueError(f"equidistancia {equidistancia} debe ser > 0")
    detectada = _detectar_equidistancia(curvas[campo_elevacion])
    if equidistancia is None:
        equidistancia = detectada
    elif detectada is not None and not math.isclose(equidistancia, detectada):
        print(f"Aviso: equidistancia dada {equidistancia} != detectada {detectada}")

    # `lambda_m` dada = ya medida sobre ESTE mismo extent por quien llama, que
    # es el caso de `barrer_resolucion` (N interpolaciones sobre un solo
    # `cuadro`). Medirla es buffer + disolver + intersecar sobre todas las
    # curvas: repetirla por corrida es el mismo numero pagado N veces. No
    # finita = no medible, se vuelve a medir (da None y los defaults fallan
    # con su mensaje, que es lo correcto).
    if lambda_m is None or not math.isfinite(lambda_m):
        separacion = _separacion_horizontal(curvas, bounds)
    else:
        separacion = float(lambda_m)

    if resolucion is None:
        if separacion is None:
            raise ValueError(
                "no se pudo medir la separación entre curvas; pasa resolucion"
            )
        # mitad de la separación horizontal: hace falta al menos un píxel entre
        # curva y curva para que el relieve se vea. Más fino no agrega
        # información que no esté ya en la cartografía, solo peso de archivo.
        # HACIA ARRIBA: `separacion/2` es el PISO antialias, y redondear al mas
        # cercano lo bajaria por debajo de si mismo (lambda/2 = 22 -> 20): el
        # aviso de abajo saltaria contra una cifra que esta misma linea deriva.
        resolucion = _redondear_limpio(separacion / 2, "arriba")
    elif separacion is not None and resolucion > separacion:
        print(
            f"Aviso: resolucion {resolucion:g} mayor que la separación entre "
            f"curvas ({separacion:.1f}); el relieve entre curvas no se va a ver"
        )

    # PISO antialias. Una sola comprobación cubre los dos caminos: la resolución
    # que dio el usuario, y la derivada, que también puede caer bajo el piso
    # porque `_redondear_limpio` redondea al valor limpio MÁS CERCANO y ese
    # salta hacia abajo (λ/2 = 22 -> 20). Aquí se AVISA, no se corrige: quien
    # llama decide, y una resolución que cambia sola no se puede citar.
    # Solo con spline: el rizo es de la RBF, el TIN no lo tiene.
    if (
        metodo in ("spline", "tps")
        and separacion is not None
        and resolucion < separacion / 2
    ):
        if amplitud_rizo is None:
            print(
                f"Aviso: resolucion {resolucion:g} m por debajo del piso antialias "
                f"{separacion / 2:.1f} m (= lambda/2, con lambda {separacion:.1f} m "
                f"medida sobre el extent). La ventana 3x3 de Horn deriva sobre 2h, "
                f"asi que el rizo del spline entre curvas entra por alias y engorda "
                f"la clase de pendiente mas alta: medido a ~lambda/4, 57 % inventado "
                f"sobre 53 % real. Contrasta con "
                f"`resolucion_regla(..., lambda_m=...)`, o mide la "
                f"amplitud del rizo y pasala como `amplitud_rizo`: con `A` medida "
                f"este piso deja de ser el juez."
            )
        else:
            # `A` MEDIDA: manda la medicion, no el piso. El piso
            # es un proxy util cuando NO has medido `A`; con `A` en la mano la
            # pendiente espuria tiene forma cerrada y se REPORTA. Esta funcion
            # no conoce los rangos, asi que no puede dictaminar: da el numero.
            print(
                f"Aviso: resolucion {resolucion:g} m por debajo del piso antialias "
                f"{separacion / 2:.1f} m, PERO la amplitud del rizo esta MEDIDA "
                f"(A = {amplitud_rizo:g} m), y con `A` manda la medicion: la "
                f"pendiente espuria que entra por alias es ~"
                f"{_pendiente_espuria(amplitud_rizo, resolucion, separacion):.0f} % "
                f"(= A*sin(2*pi*h/lambda)/h, con lambda {separacion:.1f} m). "
                f"Comparala contra el limite inferior de tu clase mas alta antes "
                f"de citarla."
            )

    if intervalo_muestreo is None:
        # muestrear más fino que la separación entre curvas deja la nube más
        # densa A LO LARGO de la curva que ENTRE curvas. Esa anisotropía es lo
        # que hace ondular a la RBF e infla el rango >100%. Ver `metodo` en
        # interpolar_mde.
        #
        # SIN redondear (a diferencia de resolucion). Un tamaño de píxel gana
        # algo con ser limpio (alineación, reportes); un espaciamiento sobre
        # una polilínea no gana nada, y `_redondear_limpio` salta de 50 a 100
        # sin nada en medio: cerca del corte, el muestreo (y con él la
        # superficie del rango más alto) se decidiría por centímetros.
        intervalo_muestreo = separacion if separacion is not None else resolucion
    return resolucion, equidistancia, intervalo_muestreo, separacion


def _bounds_cuadro(
    curvas: gpd.GeoDataFrame, cuadro: gpd.GeoDataFrame | Any
) -> tuple[float, float, float, float]:
    """Extent de la malla: bounds de `cuadro` si se da (y pisa las curvas), o de las curvas."""
    if cuadro is not None:
        geom_cuadro = (
            unary_union(list(cuadro.geometry)) if hasattr(cuadro, "geometry") else cuadro
        )
        if not geom_cuadro.intersects(_caja(*curvas.total_bounds)):
            raise ValueError("cuadro no intersecta las curvas")
        return geom_cuadro.bounds
    return tuple(curvas.total_bounds)


def _interpolar_malla(
    pts: FloatArray,
    zs: FloatArray,
    bounds: tuple[float, float, float, float],
    resolucion: float,
    metodo: str,
    suavizado: float = 0.0,
    vecinos: int | None = None,
) -> tuple[FloatArray, Affine]:
    """Interpola puntos (x, y, z) sobre una malla regular. TIN, spline o IDW, sin extrapolar.

    `vecinos` None = el default de cada método (48 para spline, 8 para IDW):
    son escalas distintas y un solo número no sirve para los dos.
    """
    # ponytail: puntos XY coincidentes (p. ej. inicio==fin de una curva cerrada)
    # rompen a los tres de distinta forma: dejan singular el sistema RBF con
    # suavizado=0, y le dan al TIN geometría degenerada (QhullError -> respaldo
    # IDW sin que el usuario se entere de que cambió de método). Deduplicar una
    # vez aquí, antes del dispatch, en vez de dentro de cada método.
    _, keep = np.unique(pts, axis=0, return_index=True)
    pts, zs = pts[keep], zs[keep]

    minx, miny, maxx, maxy = bounds
    ancho = max(math.ceil((maxx - minx) / resolucion), 1)
    alto = max(math.ceil((maxy - miny) / resolucion), 1)
    tf = Affine(resolucion, 0, minx, 0, -resolucion, maxy)
    # centros de píxel de la malla regular
    gx = minx + (np.arange(ancho) + 0.5) * resolucion
    gy = maxy - (np.arange(alto) + 0.5) * resolucion
    GX, GY = np.meshgrid(gx, gy)
    malla = np.column_stack([GX.ravel(), GY.ravel()])

    kw: dict[str, Any] = {} if vecinos is None else {"vecinos": vecinos}

    if metodo == "tin":
        # el TIN no tiene ninguno de los dos; ignorarlos en silencio sería una
        # trampa, el usuario creería estar suavizando
        if suavizado or vecinos is not None:
            print("Aviso: 'tin' ignora suavizado y vecinos (no son parámetros suyos)")
        try:
            z = LinearNDInterpolator(pts, zs)(malla)
        except QhullError:
            print("Aviso: TIN falló (geometría degenerada), usando IDW")
            z = _interp_idw(pts, zs, malla)
    elif metodo in ("spline", "tps"):
        z = _interp_spline(pts, zs, malla, suavizado=suavizado, **kw)
    elif metodo == "idw":
        if suavizado:
            print("Aviso: 'idw' ignora suavizado (no es un parámetro suyo)")
        z = _interp_idw(pts, zs, malla, **kw)
    else:
        raise ValueError(
            f"metodo '{metodo}' no válido (usa 'tin', 'spline'/'tps' o 'idw')"
        )

    # IDW y spline extrapolan (y el TIN cae a IDW si falla): ninguno sale de la envolvente
    envolvente = shapely.multipoints(pts).convex_hull
    if envolvente.geom_type == "Polygon":
        # ponytail: envolvente degenerada (puntos colineales) no enmascara nada
        z[~shapely.covers(envolvente, shapely.points(malla))] = np.nan
    return z.reshape(alto, ancho).astype(float), tf


def planos_de_curva(
    mde: FloatArray, transform: Affine, curvas: gpd.GeoDataFrame, campo: str
) -> npt.NDArray[np.bool_]:
    """Máscara de las celdas planas EXACTAS a la cota de una curva.

    Salen de dos sitios. En el TIN, triángulos con sus tres vértices sobre la
    MISMA curva: interpolan la misma z en todo su interior. En el spline y el
    IDW, celdas cuyos `vecinos` cayeron todos en la misma curva (muestreo mucho
    más fino que la separación, o pocos vecinos): un ajuste local sobre puntos
    de igual cota devuelve un plano. En los dos casos la celda sale con
    gradiente 0 y cota igual a un nivel de `curvas[campo]`. En terreno
    real las dos cosas a la vez no pasan: una ladera no se queda horizontal
    justo a la cota de la curva. Salen con 0 % de pendiente (inflan el rango
    más bajo) y sin orientación, y aguas abajo no se distinguen de un llano.

    `interpolar_mde` ya avisa con las hectáreas, con cualquier método; esto es la
    máscara, para contarlas dentro del predio sin numpy
    (`superficie` = `mascara.sum()` celdas) o para dejarlas sin pendiente con
    `calcular_pendiente(..., invalidar=planos_de_curva(...))`.

    Mismo criterio de plano que `calcular_orientacion` (gradiente < 1e-9),
    para que la cifra coincida con la de "sin orientacion". Compara contra los
    NIVELES de la capa y no contra múltiplos de la equidistancia: una carta con
    cotas desplazadas (1105, 1125...) daría 0. Medido en un cerro de anillos a
    98 m con muestreo de 5 m: TIN 2.77 ha, spline 2.31 ha con 48 vecinos y
    23.28 con 8. En un predio real, el spline a 20 m con muestreo de 20 m: 0.

    Es un subconjunto del `atomo` de `histograma_fase`, que cuenta TODA celda
    a la cota de una curva: el borde de cada meseta tiene la ventana de Horn
    tocando ladera y sale con pendiente baja y no cero (en un predio real: 778.35 ha de
    `atomo` contra 535.05 de esta). Para el daño total, `atomo`.

    Devuelve un array booleano con la forma de `mde`.
    """
    if campo not in curvas.columns:
        raise ValueError(f"'{campo}' no está en curvas")
    dzdx, dzdy, valido, n_vecinos = _derivadas_horn(mde, transform)
    plano = valido & (n_vecinos >= 7) & (np.hypot(dzdx, dzdy) < 1e-9)
    niveles = np.unique(curvas[campo].to_numpy(dtype=float))
    # solo las celdas planas, que son pocas: celdas x niveles cabe en memoria.
    # 1 mm: la interpolación baricéntrica suma pesos y no da la cota al bit
    distancia = np.abs(np.asarray(mde, dtype=float)[plano][:, None] - niveles[None, :]).min(axis=1)
    plano[plano] = distancia < 1e-3
    return plano


_AVISO_PLANOS = {
    "tin": """   Son triangulos con sus tres vertices en la MISMA curva: interpolan una
   meseta horizontal aunque el terreno sea ladera. Los `quiebres` solo los
   quitan en las vaguadas: en cimas, lomos y a media ladera siguen. Si el rango
   bajo o la exposicion se citan, sacalos de un MDE spline y deja el TIN para
   la clase alta (docs/GEOPHIS.md, "Cuando gana el TIN").""",
    "rbf": """   Los `vecinos` de esas celdas cayeron TODOS en la misma curva, y un ajuste
   local sobre puntos de igual cota devuelve un plano. Pasa cuando el muestreo
   sobre las curvas es mucho mas fino que la separacion entre ellas (nube
   anisotropa) o con pocos `vecinos`: acerca `intervalo_muestreo` a la
   separacion o sube `vecinos` (`derivar_parametros` da los dos).""",
}
_COMUN_PLANOS = """   Salen con 0 % de pendiente (inflan el rango mas bajo) y sin orientacion, y
   aguas abajo no se distinguen de un llano: `planos_de_curva` da la mascara."""


# Aviso de las celdas fuera de la envolvente convexa. Vive aquí, y no en
# `acondicionar_mde`, porque `interpolar_mde` es quien crea el NaN y porque la
# rama de pendiente nunca pasa por el acondicionado: puesto allá, quien solo
# calcula pendientes no lo vería nunca.
_AVISO_SIN_DATO = """   Esa superficie NO tiene dato de origen: el interpolador no extrapola fuera
   de la envolvente convexa de los puntos muestreados sobre las curvas. Sale
   NaN, y aguas abajo queda como hueco (pendiente NaN, codigo -1 al
   reclasificar). Para darle mas curvas, en este orden:
     1. Sube `margen_borde_celdas` en `cuadros_mde` y vuelve a recortar del
        shapefile COMPLETO de curvas, no de una capa ya recortada: recortar dos
        veces no devuelve las curvas que el primer recorte ya tiro.
     2. Comprueba que el shapefile de curvas de verdad rebasa `cuadro_salida`.
        Si la cartografia se acaba en el lindero (o en el borde de la carta),
        no hay margen que valga: hace falta otra fuente que cubra el hueco.
     3. Si no hay mas cartografia, encoge `cuadro_salida` hasta lo que las
        curvas sostienen. Es preferible entregar menos superficie que
        entregarla inventada."""


def cotas_por_cruce(
    quiebres: gpd.GeoDataFrame,
    curvas: gpd.GeoDataFrame,
    campo: str,
    paso: float,
) -> gpd.GeoDataFrame:
    """Da cota a una capa de líneas sin z, por sus cruces con las curvas de nivel.

    Un cauce, un camino o un lomo digitalizado no traen elevación, así que no
    pueden entrar a un TIN como línea de quiebre. Pero cada cruce con una curva
    de nivel es un punto de cota conocida (la de la curva), y entre dos cruces
    la cota se interpola a lo largo de la propia línea. El resultado son puntos
    (x, y, z) que se suman a los de las curvas antes de triangular. Es lo que
    hace el *drainage enforcement* de ANUDEM.

    La cota NO sale de ningún MDE: sale de las dos capas de entrada. Esa es la
    diferencia entre acondicionar el drenaje y medirse a sí mismo.

    `paso` es la separación de los puntos generados a lo largo de la línea. Se
    quiere <= el tamaño de píxel, o entre dos puntos del quiebre cabe una celda
    que la triangulación resuelve como si el quiebre no existiera.

    NO EXTRAPOLA, igual que `interpolar_mde`: el tramo anterior al primer cruce
    y el posterior al último se descartan. Una línea con menos de dos cruces no
    aporta nada y se salta. Un cauce que corre POR ENCIMA de una curva da una
    línea, no un punto: no es cruce y no cuenta.

    Una línea cuya cota sube y baja (cruza un parteaguas, o hay un cruce mal
    capturado) se usa igual y se AVISA, con `geo.MOSTRAR` o sin él: corregirla aquí
    sería inventar cota, y el conteo es lo que manda a revisar la capa.

    Devuelve un GeoDataFrame de puntos con columnas `z` y `origen` (la fila de
    `quiebres` ya explotada a una parte por fila), para revisarlo en el SIG
    antes de creérselo. `interpolar_mde(..., quiebres=)` lo llama por dentro;
    llamarlo aparte sirve para guardar los puntos.
    """
    if quiebres.crs is None or curvas.crs is None:
        raise ValueError("las dos capas necesitan CRS para cruzarse")
    if quiebres.crs != curvas.crs:
        raise ValueError(
            f"quiebres en {quiebres.crs.name} y curvas en {curvas.crs.name}: "
            f"reproyecta antes, aquí no se alinea nada (la z sale del CRUCE, y "
            f"dos capas en sistemas distintos no se cruzan donde parece)"
        )
    if campo not in curvas.columns:
        raise ValueError(f"'{campo}' no está en las curvas: {list(curvas.columns)}")
    if not paso > 0:
        raise ValueError(f"paso = {paso}: tiene que ser positivo")

    # `project` pide una sola parte por fila. Es `geometria.separar_multipartes` a mano:
    # geometria vive una capa arriba y no se importa desde aqui.
    lineas = quiebres.explode(index_parts=False).reset_index(drop=True)
    indice = curvas.sindex
    geoms_curva = curvas.geometry.to_numpy()
    cotas_curva = curvas[campo].to_numpy(dtype=float)

    xs, ys, zs, origen = [], [], [], []
    n_con_cruces = n_no_monotonas = 0
    for etiqueta, linea in zip(lineas.index, lineas.geometry):
        if linea is None or linea.is_empty or linea.geom_type != "LineString":
            continue
        distancias, cotas = [], []
        for i in indice.query(linea, predicate="intersects"):
            z = cotas_curva[i]
            if not np.isfinite(z):
                continue
            corte = linea.intersection(geoms_curva[i])
            for parte in getattr(corte, "geoms", [corte]):
                if parte.geom_type == "Point" and not parte.is_empty:
                    distancias.append(linea.project(parte))
                    cotas.append(z)
        if len(distancias) < 2:
            continue
        d = np.asarray(distancias)
        z = np.asarray(cotas)
        orden = np.argsort(d)
        d, z = d[orden], z[orden]
        d, unicos = np.unique(d, return_index=True)  # dos curvas en el mismo punto
        z = z[unicos]
        if len(d) < 2:
            continue
        n_con_cruces += 1
        salto = np.diff(z)
        if np.any(salto > 0) and np.any(salto < 0):
            n_no_monotonas += 1
        # puntos entre el primer y el último cruce, sin extrapolar
        muestras = np.arange(d[0], d[-1] + paso, paso)
        muestras = muestras[muestras <= d[-1]]
        pts = shapely.get_coordinates(shapely.line_interpolate_point(linea, muestras))
        xs.append(pts[:, 0])
        ys.append(pts[:, 1])
        zs.append(np.interp(muestras, d, z))
        origen.append(np.full(muestras.size, etiqueta))

    if not xs:
        raise ValueError(
            f"ninguna de las {len(lineas)} líneas cruza dos veces las curvas: "
            f"quiebres {tuple(round(v) for v in quiebres.total_bounds)} contra "
            f"curvas {tuple(round(v) for v in curvas.total_bounds)}. O no se "
            f"tocan, o la capa de quiebres cae entre dos curvas."
        )
    x, y, z = np.concatenate(xs), np.concatenate(ys), np.concatenate(zs)
    puntos = gpd.GeoDataFrame(
        {"z": z, "origen": np.concatenate(origen)},
        geometry=gpd.points_from_xy(x, y),
        crs=curvas.crs,
    )
    if _mostrar():
        print(
            f"cotas_por_cruce: {len(puntos):,} puntos de {n_con_cruces} líneas "
            f"con >=2 cruces (de {len(lineas)}), paso {paso:g} m, "
            f"z {z.min():.1f}-{z.max():.1f} m"
        )
    if n_no_monotonas:
        print(
            f"Aviso: {n_no_monotonas} línea(s) de quiebre con cota que sube y "
            f"baja. O cruzan un parteaguas o hay un cruce mal capturado; se usan "
            f"igual, pero revísalas antes de citar lo que salga."
        )
    return puntos


def _preparar_mde(
    curvas: gpd.GeoDataFrame,
    campo: str,
    resolucion: float | None,
    equidistancia: float | None,
    intervalo_muestreo: float | None,
    cuadro: gpd.GeoDataFrame | Any,
    metodo: str | None,
    vecinos: int | None,
    lambda_m: float | None,
    amplitud_rizo: float | None,
    parametros: dict[str, Any] | None,
    quiebres: gpd.GeoDataFrame | None = None,
) -> tuple[Any, ...]:
    """Lo común a `interpolar_mde` y `validar_mde`: parámetros, extent y muestreo.

    Devuelve `(bounds, resolucion, equidistancia, intervalo_muestreo, separacion,
    metodo, vecinos, (xs, ys, zs, origen))`, derivado igual para las dos.
    """
    resolucion, equidistancia, intervalo_muestreo, vecinos, lambda_m, metodo = (
        _de_parametros(
            parametros,
            resolucion=resolucion,
            equidistancia=equidistancia,
            intervalo_muestreo=intervalo_muestreo,
            vecinos=vecinos,
            lambda_m=lambda_m,
            metodo=metodo,
        ).values()
    )
    metodo = metodo or "spline"
    if campo not in curvas.columns:
        raise ValueError(f"'{campo}' no está en curvas")
    if len(curvas) == 0:
        raise ValueError("curvas está vacío")
    _exigir_crs(curvas, "curvas")
    if quiebres is not None and metodo != "tin":
        raise ValueError(
            f"quiebres solo con metodo='tin', no '{metodo}': el spline sobre una "
            f"nube densa a lo largo del cauce ondula, que es justo lo que los "
            f"quiebres vienen a quitar"
        )

    minx, miny, maxx, maxy = _bounds_cuadro(curvas, cuadro)
    resolucion, equidistancia, intervalo_muestreo, separacion = _resolver_parametros(
        curvas,
        campo,
        resolucion,
        equidistancia,
        intervalo_muestreo,
        (minx, miny, maxx, maxy),
        metodo,
        lambda_m,
        amplitud_rizo,
    )

    xs, ys, zs, origen = _muestrear_curvas(curvas, campo, intervalo_muestreo)
    if xs.size == 0:
        raise ValueError("las curvas no produjeron puntos; revisa las geometrías")
    return (
        (minx, miny, maxx, maxy), resolucion, equidistancia, intervalo_muestreo,
        separacion, metodo, vecinos, (xs, ys, zs, origen),
    )


def interpolar_mde(
    curvas: gpd.GeoDataFrame,
    campo: str,
    resolucion: float | None = None,
    equidistancia: float | None = None,
    intervalo_muestreo: float | None = None,
    cuadro: gpd.GeoDataFrame | Any = None,
    metodo: str | None = None,
    suavizado: float = 0.0,
    vecinos: int | None = None,
    lambda_m: float | None = None,
    amplitud_rizo: float | None = None,
    quiebres: gpd.GeoDataFrame | None = None,
    paso_quiebre: float | None = None,
    parametros: dict[str, Any] | None = None,
) -> tuple[FloatArray, Affine]:
    """Genera un MDE continuo interpolando curvas de nivel vectoriales.

    Cierra el hueco entre el flujo vector (recortar/disolver) y las funciones
    que ya asumen un MDE (acondicionar_mde, quemar_cauces). Muestrea puntos a
    intervalos regulares sobre cada curva e interpola sobre una malla regular.
    Fuera de la envolvente convexa de las curvas devuelve NaN (no extrapola);
    rellena luego con `rellenar_nodata` si hace falta.

    Parámetros
    ----------
    curvas : GeoDataFrame de líneas, ya reproyectado y recortado.
    campo : columna con la cota de cada curva.
    resolucion : tamaño de píxel (unidades del CRS). Si None, la mitad de la
        separación horizontal media entre curvas medida sobre el extent (λ/2),
        redondeada a un valor limpio (aquí el redondeo SÍ sirve: un píxel
        limpio alinea y se reporta mejor). NO la equidistancia, que es una
        distancia VERTICAL: 20 m de salto de cota no dicen cuántos metros hay de
        una curva a otra en el suelo.

        λ/2 es además el PISO antialias de `resolucion_regla`: por debajo, el
        rizo del spline entre curvas entra por alias en la ventana 3x3 de Horn
        y engorda la clase de pendiente más alta. Se avisa si la resolución cae
        bajo ese piso, la hayas dado tú o la haya derivado esto (el redondeo al
        valor limpio más cercano salta hacia abajo: λ/2 = 22 -> 20). Es un
        aviso, no una corrección: una resolución que se mueve sola no se puede
        citar, y con el piso por encima del techo de la UMM no hay valor bueno,
        solo la elección que `resolucion_regla` documenta.
    equidistancia : salto de cota entre curvas. Si None, se detecta (el paso de la
        escalera de cotas, como `detectar_cota`). No fija la resolución; se usa para el reporte y para
        avisar si la que pasas no coincide con la detectada.
    intervalo_muestreo : espaciamiento del muestreo sobre cada línea. Si None,
        la separación horizontal media medida sobre el extent, SIN redondear.
        Muestrear más fino que la separación entre curvas deja la nube
        anisótropa y la RBF ondula. La separación se mide por G'(0)
        (`separacion_media`), no por área/longitud, que el zigzag del trazo
        sesga a la baja.

        TECHO CONOCIDO: es UN número para todo el predio. Si la separación
        varía mucho (un orden de magnitud entre banda empinada y llano es
        normal), ningún valor único es isótropo en los dos
        regímenes, y la superficie del rango más alto queda con una banda de
        incertidumbre real. Si esa clase es la que se cita, decláralo como
        rango, no como cifra. Upgrade: muestreo variable por densidad local.
    cuadro : GeoDataFrame o geometría que fija el extent. Si None, el bounding
        box de las curvas.
    metodo : 'spline'/'tps' (thin-plate, default si None), 'tin'
        (triangulación) o 'idw' (respaldo).

        El TIN sobre curvas solas da pendientes falsas: un triángulo con sus
        tres vértices sobre la misma curva sale plano. No solo en cimas, lomos
        y fondos de vaguada: medido en un predio real, el 80 % a media ladera y el
        14 % en cimas (del orden del 10 % del predio en sierra, justo donde la
        capa se usa para decidir). Lo dice el aviso de abajo.

        El precio del spline es ondulación entre curvas, que infla el rango
        >100%. Se controla con `intervalo_muestreo`. Muestrear más fino que la separación
        horizontal entre curvas no da detalle, da ruido.

        'tin' sigue siendo lo correcto si la cartografía trae líneas de quiebre
        y quieres que se respeten: ver `quiebres`.
    suavizado : solo 'spline'. 0 (default) interpola exacto, o sea la superficie
        pasa por todos los puntos de las curvas y entre curva y curva hace lo
        que quiere. >0 relaja el ajuste y amortigua esa ondulación.

        Es la perilla DIRECTA contra el rizo entre curvas; `intervalo_muestreo`
        es la indirecta. No está en metros ni en ninguna unidad del terreno
        (se suma a la diagonal del sistema RBF), así que se calibra a tientas:
        barre por órdenes de magnitud (0.1, 1, 10) y para en el primero que
        sirva.

        OJO al juzgarlo: `validar_mde` va a EMPEORAR con cada aumento, por
        definición, porque suavizar es dejar de pasar por los puntos y esos son
        justo los que el hold-out reserva. Si tuneas por RMS siempre vas a
        elegir 0. Júzgalo por la superficie del rango más alto de pendiente y
        por si las curvas dentro de ese rango sostienen esa pendiente
        (equidistancia / (area/largo) medido dentro de las piezas).
    vecinos : cuántos puntos usa cada ajuste local. None = el default del
        método (48 en spline, 8 en IDW); no comparten escala. Bajarlo en el
        spline también amortigua la ondulación, y de paso acelera. Ojo con el
        borde: el radio que abarcan esos vecinos es el margen de datos que
        necesita el cuadro de recorte alrededor del extent de salida (ver
        `cuadros_mde`); con menos vecinos, menos margen hace falta.
    lambda_m : separación horizontal media, si YA la mediste sobre este mismo
        extent. None (default) = se mide aquí. Medirla cuesta un buffer + un
        disolver + un intersecar sobre todas las curvas, y es el mismo número
        en cada corrida de un barrido que no mueve el `cuadro`: pásalo y lo
        pagas una vez (`barrer_resolucion` lo hace). Tiene que salir del MISMO
        dominio (`_bounds_cuadro(curvas, cuadro)`); una λ de otro dominio
        cambia los defaults y el piso. No finita = se mide.
    amplitud_rizo : amplitud `A` del rizo del spline entre curvas, en metros,
        MEDIDA. None (default) = no medida, y entonces el piso antialias
        (λ/2) hace de proxy y avisa cuando `resolucion` cae por debajo.

        Con `A` en la mano ese proxy sobra: la pendiente espuria que el rizo
        mete en la ventana de Horn es `A*sin(2*pi*h/λ)/h`, un número cerrado,
        y el aviso pasa a reportarlo en vez de dictaminar. Es la regla de
        gobernanza hecha código: cuando el criterio y la medición directa
        chocan, gana la medición. No cambia el
        cálculo, solo lo que se imprime: quien llama compara ese porcentaje
        contra el límite inferior de su clase alta.
    quiebres : líneas SIN cota (cauces, típicamente) que el TIN tiene que
        respetar. Cada una recibe z de sus cruces con las curvas
        (`cotas_por_cruce`) y sus puntos se suman a los de las curvas antes de
        triangular. Quita los triángulos horizontales del fondo de las
        vaguadas, que es donde el TIN sobre curvas solas se rompe, y deja el
        drenaje entregable con `rellenar_sumideros`. Solo `metodo='tin'`:
        el spline con una nube densa a lo largo del cauce es la anisotropía
        que lo hace ondular. Mismo CRS que `curvas`.

        TECHO CONOCIDO: DENSIFICA la línea, no la impone. Una Delaunay con
        restricciones garantiza que la arista exista; aquí las aristas siguen
        la línea porque hay puntos suficientes, que basta con
        `paso_quiebre <= resolucion` pero no es lo mismo. El escalón siguiente,
        si una medición lo pide, es `triangle` o CGAL.
    paso_quiebre : separación de los puntos sobre cada quiebre. None =
        `resolucion / 2`, al menos un punto por celda con margen.
    parametros : el dict de `derivar_parametros`. Lo que se deje en None se
        toma de ahí (`resolucion`, `equidistancia`, `intervalo_muestreo`, `vecinos`,
        `lambda_m` y `metodo`); lo que se pase a mano manda sobre el dict.
    `geo.MOSTRAR` : reporta equidistancia, resolución, muestreo, extent y rango de z.
        NO calla el aviso de superficie sin dato: un hueco no es reporte, es
        superficie sin dato de origen, y lo que salga de ahí es inventado.

    Devuelve (array 2D con NaN de nodata, transform), como remuestrear.
    """
    (minx, miny, maxx, maxy), resolucion, equidistancia, intervalo_muestreo, separacion, metodo, vecinos, (xs, ys, zs, _) = _preparar_mde(
        curvas, campo, resolucion, equidistancia, intervalo_muestreo, cuadro,
        metodo, vecinos, lambda_m, amplitud_rizo, parametros, quiebres,
    )

    if quiebres is not None:
        puntos = cotas_por_cruce(
            quiebres,
            curvas,
            campo,
            resolucion / 2 if paso_quiebre is None else paso_quiebre,
        )
        xs = np.concatenate([xs, puntos.geometry.x.to_numpy()])
        ys = np.concatenate([ys, puntos.geometry.y.to_numpy()])
        zs = np.concatenate([zs, puntos["z"].to_numpy()])

    pts = np.column_stack([xs, ys])
    mde, tf = _interpolar_malla(
        pts, zs, (minx, miny, maxx, maxy), resolucion, metodo, suavizado, vecinos
    )

    sin_dato_ha = float(np.isnan(mde).sum()) * resolucion**2 / 10_000
    if _mostrar():
        alto, ancho = mde.shape
        eq = "no detectada" if equidistancia is None else f"{equidistancia:g}"
        # la separación se reporta siempre, aunque los parámetros vengan dados:
        # es la referencia contra la que se juzgan resolucion y muestreo
        sep = "?" if separacion is None else f"{separacion:.1f}"
        print(
            f"MDE {ancho}x{alto} px | equidistancia: {eq} | separacion: {sep} | "
            f"resolucion: {resolucion:g} | muestreo: {intervalo_muestreo:g} | "
            f"extent: ({minx:.1f}, {miny:.1f}, {maxx:.1f}, {maxy:.1f}) | "
            f"z: {np.nanmin(mde):.1f}-{np.nanmax(mde):.1f} | "
            f"sin dato: {sin_dato_ha:.2f} ha"
        )
    # FUERA de `geo.MOSTRAR`, a proposito. Un hueco no es una linea de reporte: es
    # superficie sin dato de origen, y la cifra que salga de ahi es inventada.
    # `geo.MOSTRAR = False` apaga el reporte de una corrida que imprime lo suyo (el
    # caso de los scripts entregables); no puede apagar una senal de defecto.
    if sin_dato_ha > 0:
        print(f"Aviso: {sin_dato_ha:.2f} ha del MDE sin dato de origen.")
        print(_AVISO_SIN_DATO)
    # Tambien fuera de `geo.MOSTRAR`: es mudo aguas abajo. Una meseta falsa sale
    # como 0-5 % con la misma cara que una real (en un predio real: 535 ha, el 11 %).
    planos = planos_de_curva(mde, tf, curvas, campo)
    n = int(planos.sum())
    if n:
        ha = n * resolucion**2 / 10_000
        pct = 100 * n / max(int((~np.isnan(mde)).sum()), 1)
        # sobre el EXTENT: aqui no llega el predio, y el % es la cifra comparable
        print(
            f"Aviso: {ha:.2f} ha ({pct:.1f} %) del extent del MDE {metodo.upper()} son planos "
            f"exactos a la cota de una curva."
        )
        print(_AVISO_PLANOS["tin" if metodo == "tin" else "rbf"])
        print(_COMUN_PLANOS)
    return mde, tf


def validar_mde(
    curvas: gpd.GeoDataFrame,
    campo: str,
    resolucion: float | None = None,
    equidistancia: float | None = None,
    intervalo_muestreo: float | None = None,
    cuadro: gpd.GeoDataFrame | Any = None,
    metodo: str | None = None,
    suavizado: float = 0.0,
    vecinos: int | None = None,
    lambda_m: float | None = None,
    amplitud_rizo: float | None = None,
    porcentaje_reserva: float = 0.2,
    semilla: int | None = None,
    parametros: dict[str, Any] | None = None,
) -> tuple[dict[str, float], gpd.GeoDataFrame]:
    """Valida un MDE por reserva de puntos (hold-out), como el RMS de ANUDEM.

    Control de calidad para `interpolar_mde` que corre aparte de la cadena de
    producción: muestrea las curvas igual que `interpolar_mde`, aparta al azar
    `porcentaje_reserva` de los puntos de control, interpola con el resto y compara
    la elevación real contra la del MDE en los puntos reservados. Mismos parámetros
    de interpolación que `interpolar_mde`. Función pura: no muta `curvas`.

    NO sirve para elegir entre 'tin' y 'spline'. Los puntos reservados están
    SOBRE las curvas, y ahí el TIN es casi exacto porque el punto queda rodeado
    de vecinos de su misma cota: el RMS es ciego al triángulo plano, que es
    justo el defecto que importa, y por eso favorece al TIN. Para decidir el
    método, compara la superficie del rango más bajo de pendiente (ver `metodo`
    en `interpolar_mde`). Este hold-out sirve para lo que sí mide: cazar curvas
    con la cota mal capturada, mirando dónde se concentran los residuales.

    TAMPOCO sirve para tunear `suavizado`, y por la misma razón dada la vuelta:
    suavizar es dejar de pasar por los puntos de las curvas, que son justo los
    que este hold-out reserva, así que el RMS empeora siempre. Optimizando este
    número siempre elegirías suavizado=0.

    Y NO es comparable entre resoluciones. El MDE se muestrea en el CENTRO DE
    CELDA más cercano al punto reservado, así que el residual trae dos cosas
    sumadas: el error del interpolador y el error de discretizar la malla. El
    segundo crece con el tamaño de píxel: el RMS sube al engrosar la malla SIN
    que la superficie cambie. Para comparar resoluciones, compara superficie por rango.

    Parámetros
    ----------
    porcentaje_reserva : fracción de puntos apartada como prueba (0-1, default 0.2).
    semilla : fija la aleatoriedad del reparto (reproducible); None = sin fijar.
    (el resto, igual que interpolar_mde. Pásale los mismos suavizado/vecinos
    que a interpolar_mde o estarás validando otra superficie. `lambda_m` y
    `amplitud_rizo` están aquí solo para que las dos deriven los parámetros
    exactamente igual; no cambian el hold-out, solo lo que se mide y avisa.)

    Devuelve (metricas, puntos_validacion):
    - metricas : dict con rms, error_medio, error_abs_medio, p50, p90, p95, max
      (percentiles del error absoluto) y n_puntos_prueba.
    - puntos_validacion : GeoDataFrame de los puntos reservados con columna
      `residual` (real - interpolada) para mapear dónde se concentra el error,
      y `curva` con el índice de la curva de la que salió cada punto. Con esa
      columna, cazar una curva con la cota mal capturada es un `groupby`
      (`.groupby("curva")["residual"].mean()`): la curva mala sale con la media
      fuertemente sesgada, sin tener que simbolizar mapas.
    """
    if not 0 < porcentaje_reserva < 1:
        raise ValueError(f"porcentaje_reserva {porcentaje_reserva} fuera de (0, 1)")
    (minx, miny, maxx, maxy), resolucion, equidistancia, intervalo_muestreo, _, metodo, vecinos, (xs, ys, zs, origen) = _preparar_mde(
        curvas, campo, resolucion, equidistancia, intervalo_muestreo, cuadro,
        metodo, vecinos, lambda_m, amplitud_rizo, parametros,
    )

    n_total = int(xs.size)
    n_prueba = int(round(porcentaje_reserva * n_total))
    if n_prueba < MIN_PRUEBA:
        raise ValueError(
            f"muy pocos puntos ({n_total}) para reservar {MIN_PRUEBA} de prueba con "
            f"porcentaje_reserva={porcentaje_reserva}; baja intervalo_muestreo o sube "
            f"porcentaje_reserva"
        )

    pts = np.column_stack([xs, ys])
    orden = np.random.default_rng(semilla).permutation(n_total)
    idx_prueba, idx_entrena = orden[:n_prueba], orden[n_prueba:]

    mde, tf_mde = _interpolar_malla(
        pts[idx_entrena],
        zs[idx_entrena],
        (minx, miny, maxx, maxy),
        resolucion,
        metodo,
        suavizado,
        vecinos,
    )

    # muestrea el MDE en el píxel de cada punto reservado (vecino más cercano).
    # `extraer_valores` y no un `np.clip` sobre los índices: el clip metería el
    # valor del borde para cualquier punto de fuera, como si fuera residual.
    px = pts[idx_prueba]
    real = zs[idx_prueba]
    residual = real - extraer_valores(mde, tf_mde, px)

    # puntos fuera de la envolvente del set de entrenamiento salen NaN: fuera de métricas
    res_v = residual[~np.isnan(residual)]
    if res_v.size == 0:
        raise ValueError("ningún punto de prueba cayó dentro del MDE interpolado")
    abs_err = np.abs(res_v)
    metricas = {
        "rms": float(np.sqrt(np.mean(res_v**2))),
        "error_medio": float(np.mean(res_v)),
        "error_abs_medio": float(np.mean(abs_err)),
        "p50": float(np.percentile(abs_err, 50)),
        "p90": float(np.percentile(abs_err, 90)),
        "p95": float(np.percentile(abs_err, 95)),
        "max": float(abs_err.max()),
        "n_puntos_prueba": int(res_v.size),
    }

    puntos_validacion = gpd.GeoDataFrame(
        # ponytail: 'curva' y no 'curva_origen', el shapefile trunca a 10
        {campo: real, "residual": residual, "curva": origen[idx_prueba]},
        geometry=gpd.points_from_xy(px[:, 0], px[:, 1]),
        crs=curvas.crs,
    )

    if _mostrar():
        print(
            f"Validación MDE ({metodo}): n={metricas['n_puntos_prueba']} "
            f"(reserva {porcentaje_reserva:.0%}) | RMS: {metricas['rms']:.3f} | "
            f"error medio: {metricas['error_medio']:+.3f} | "
            f"|error| medio: {metricas['error_abs_medio']:.3f} | "
            f"p50: {metricas['p50']:.3f} | p90: {metricas['p90']:.3f} | "
            f"p95: {metricas['p95']:.3f} | máx: {metricas['max']:.3f}"
        )
    return metricas, puntos_validacion


def extraer_valores(
    arr: FloatArray,
    transform: Affine,
    puntos: gpd.GeoDataFrame | npt.ArrayLike,
    crs: CRS | None = None,
    fuera: float = np.nan,
) -> FloatArray:
    """Muestrea un ráster en la posición de cada punto (vecino más cercano).

    Devuelve un array float de un valor por punto, en el mismo orden.

    `puntos` acepta una capa de puntos o un array `(N, 2)` de `(x, y)`. Con
    `crs` dado y una capa en otro CRS, la reproyecta; sin `crs` se fía de las
    coordenadas tal como llegan.

    Los puntos que caen FUERA del ráster devuelven `fuera` (NaN por defecto),
    **no el valor del píxel del borde**: con un `np.clip` sobre los índices, un
    punto de fuera se lleva la cota del borde y entra en las métricas como un
    residual real. La inversión la hace el Affine completo (celda no cuadrada o
    rotada incluida).

    El borde superior del extent es CERRADO: un punto exactamente en `maxx` o
    en `miny` pertenece a la última celda, no a una celda de más.
    """
    if isinstance(puntos, gpd.GeoDataFrame):
        p = _a_crs(puntos, puntos.crs if crs is None else crs, "puntos")
        xy = np.column_stack([p.geometry.x, p.geometry.y])
    else:
        xy = np.asarray(puntos, dtype=float)
    if xy.ndim != 2 or xy.shape[1] != 2:
        raise ValueError(
            f"`puntos` debe ser una capa de puntos o un array (N, 2); llego {xy.shape}"
        )
    if arr.ndim != 2:
        raise ValueError(f"`arr` debe ser 2D; llego {arr.ndim}D (¿varias bandas?)")

    filas, cols, dentro = _celdas(transform, xy, arr.shape)
    valores = np.full(len(xy), float(fuera), dtype=float)
    valores[dentro] = arr[filas[dentro], cols[dentro]]
    return valores


def _celdas(
    transform: Affine, xy: npt.NDArray[np.floating[Any]], forma: tuple[int, ...]
) -> tuple[npt.NDArray[np.intp], npt.NDArray[np.intp], npt.NDArray[np.bool_]]:
    """(filas, cols, dentro) de cada `(x, y)`; `dentro` marca los que caen en la malla."""
    # posicion fraccionaria con el Affine completo (lo mismo que hace `rowcol`),
    # para distinguir "justo en el borde" de "dentro de la celda de mas alla"
    cols_f, filas_f = ~transform * (xy[:, 0], xy[:, 1])
    filas_f, cols_f = np.asarray(filas_f, dtype=float), np.asarray(cols_f, dtype=float)
    filas, cols = np.floor(filas_f).astype(int), np.floor(cols_f).astype(int)
    alto, ancho = forma
    # el extent cierra por arriba: `maxx` cae en la columna `ancho` por el floor
    # y pertenece a la ultima. SOLO el borde exacto: clampear `filas == alto`
    # meteria el valor del borde a todo punto de la celda siguiente, ya fuera.
    filas[np.isclose(filas_f, alto)] = alto - 1
    cols[np.isclose(cols_f, ancho)] = ancho - 1
    dentro = (filas >= 0) & (filas < alto) & (cols >= 0) & (cols < ancho)
    return filas, cols, dentro


def rasterizar(
    gdf: gpd.GeoDataFrame,
    forma: tuple[int, int],
    transform: Affine,
    valor: int = 1,
    todo_tocado: bool = False,
) -> npt.NDArray[np.uint8]:
    """Convierte las geometrías de una capa en una máscara raster uint8.

    Vector → raster: donde hay geometría toma `valor` (0 a 255), el resto 0.
    `forma` es (alto, ancho) del grid destino.

    `todo_tocado` : False (default, la convención habitual) pinta el píxel solo si su
        CENTRO cae dentro de la geometría. True pinta cualquier píxel que la
        geometría toque, aunque sea de refilón.

        Usa True cuando la máscara se va a recortar en VECTOR después: sobra
        por fuera y el `recortar` la deja en el límite exacto, así que no se
        pierde nada. Con False, la celda mordida por el lindero cuyo centro
        quedó afuera no se poligoniza nunca y el recorte posterior ya no puede
        devolverla: la capa sale con MENOS superficie que el polígono de
        referencia y el error crece con el tamaño de píxel (≈ perímetro · px/8).
        Es una fuga silenciosa, no un error.

        Déjalo en False cuando la máscara ES el resultado (p. ej. quemar
        cauces), no un paso intermedio.
    """
    if not 0 <= valor <= 255:
        raise ValueError(f"valor {valor} no cabe en uint8 (0 a 255)")
    return _rasterize(
        [
            (g.__geo_interface__, valor)
            for g in gdf.geometry
            if g is not None and not g.is_empty
        ],
        out_shape=forma,
        transform=transform,
        fill=0,
        dtype=np.uint8,
        all_touched=todo_tocado,
    )


def combinar_categorias(
    a: npt.NDArray[Any], b: npt.NDArray[Any], factor: int = 10
) -> npt.NDArray[np.int32]:
    """Empaqueta dos rasters categóricos en un ID único (map algebra).

    combinado = a * factor + b. Para recuperar: a = val // factor,
    b = val % factor. `factor` debe superar el nº de clases de `b`
    (p. ej. 4 orientaciones → factor 10). Devuelve array int32.
    """
    a, b = np.asarray(a), np.asarray(b)
    if int(a.min()) < 0 or int(b.min()) < 0:
        raise ValueError(
            "a y b deben ser >= 0 (con negativos, p. ej. nodata -1, los IDs chocan)"
        )
    bmax = int(b.max())
    if factor <= bmax:
        raise ValueError(f"factor {factor} <= max(b) {bmax}; los IDs se pisarían")
    # int64 antes de multiplicar: con uint8 (lo que da calcular_orientacion) 30*10 se desborda
    return (a.astype(np.int64) * factor + b).astype(np.int32)


def poligonizar(
    arr: npt.NDArray[Any],
    transform: Affine,
    crs: CRS,
    mascara: npt.NDArray[Any] | None = None,
    campo: str = "valor",
) -> gpd.GeoDataFrame:
    """Convierte regiones raster de igual valor en polígonos vector.

    Raster → vector. Ignora el valor 0. `mascara` puede ser un array booleano
    o de enteros (se coacciona a uint8, como pide rasterio); úsala para no
    vectorizar lo que está fuera del área de interés, que es trabajo que se
    tira después al recortar. Devuelve GeoDataFrame con la columna `campo`
    (el valor entero de cada región).
    """
    if mascara is not None:
        mascara = np.asarray(mascara).astype(np.uint8)
    geometrias: list[Any] = []
    valores: list[int] = []
    for geom, val in _shapes(arr, mask=mascara, transform=transform):
        codigo = int(val)
        if codigo > 0:
            geometrias.append(shape(geom))
            valores.append(codigo)
    # se arma desde columnas, no desde una lista de dicts: con muchas regiones
    # el camino from_records de pandas domina el tiempo. De paso, la salida
    # vacía conserva la columna geometry (una lista de dicts vacía no la crea
    # y el primer `.geometry` de quien llame truena con AttributeError).
    return gpd.GeoDataFrame({campo: valores}, geometry=geometrias, crs=crs)


def limpiar_moteado(
    arr: npt.NDArray[Any],
    min_pixeles: int,
    conectividad: int = 4,
    mascara: npt.NDArray[Any] | None = None,
    exentos: Iterable[int] | None = None,
) -> npt.NDArray[Any]:
    """Absorbe las regiones menores a `min_pixeles` en su vecino más grande.

    Es el sieve de GDAL. Quita el moteado de un raster categórico (el
    píxel suelto, la mancha de tres celdas) que no significa nada en campo y
    que al vectorizar se vuelve miles de polígonos diminutos. Va entre
    `reclasificar_rangos` y `poligonizar`.

    Parámetros
    ----------
    min_pixeles : tamaño mínimo que conserva una región; las menores se
        absorben. Con 1 no quita nada. Es una decisión de campo, no técnica:
        a 10 m/px, 4 píxeles son 400 m².
    conectividad : 4 (solo lados, default) u 8 (también diagonales), para
        decidir qué celdas forman una misma región.
    mascara : celdas válidas (booleana o entera). Pásale el NoData excluido
        (p. ej. `codigos > 0`) o las regiones del borde se absorben en él.
    exentos : códigos que el sieve NO toca. Una clase exenta ni se absorbe (sus
        regiones sobreviven por chicas que sean) ni absorbe (una región chica de
        otra clase se va a su mayor vecino NO exento). Es la decisión de campo
        de "esta clase es una CINTA, no una mancha": sobre una banda de 15.5 m
        de ancho, alcanzar una UMM de 0.25 ha exige 162 m continuos, y aplicar
        la UMM por superficie borra la mitad del escarpe, que es terreno real.

        **La alternativa mide mal.** Sievear todo, sievear nada, y resolver por
        prioridad quedándose con la clase alta del array crudo NO es una partición: el sieve mete en la clase alta
        píxeles que en el crudo eran de otra clase, y ésos no están ni en la
        capa cruda de la alta ni en la sieveada de las demás: queda un HUECO,
        que en un predio puede ser ~0 y en otro decenas de ha. Con `exentos`
        hay UN solo array, la clase exenta sale idéntica a la del crudo (píxel
        a píxel) y no hay ni hueco ni doble conteo.

        Techo conocido: una región chica rodeada ENTERA por clase exenta se
        queda, porque no tiene vecino legal que la absorba. Es correcto y es
        raro; si molesta, el arreglo es otra pasada, no relajar la exención.

    Devuelve un array nuevo del mismo shape y dtype; no muta la entrada.
    """
    if min_pixeles < 1:
        raise ValueError(f"min_pixeles {min_pixeles} debe ser >= 1")
    if mascara is not None:
        mascara = np.asarray(mascara).astype(bool)
    if exentos is not None:
        # el `mask` de GDAL ya tiene la semantica exacta que pide una exencion:
        # el pixel enmascarado conserva su valor y no cuenta como vecino.
        libre = ~np.isin(arr, np.asarray(list(exentos)))
        mascara = libre if mascara is None else (mascara & libre)
    return _sieve(arr, min_pixeles, mask=mascara, connectivity=conectividad)


# kernels de Horn (1981), diferencias finitas de 3er orden sobre ventana 3x3.
# correlate los aplica tal cual: salida[p] = sum vecinos * peso.
#   a b c
#   d e f   (e = centro)
#   g h i
# dz/dx num = (c + 2f + i) - (a + 2d + g)  -> columna derecha menos izquierda
# dz/dy num = (g + 2h + i) - (a + 2b + c)  -> fila inferior menos superior
_KX = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=float)
_KY = np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=float)
_VECINOS = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], dtype=float)


def _derivadas_horn(
    mde: FloatArray,
    res: float | tuple[float, float] | Affine,
    z_factor: float = 1.0,
) -> tuple[FloatArray, FloatArray, npt.NDArray[np.bool_], FloatArray]:
    """Derivadas dz/dx y dz/dy por el método de Horn (1981).

    Base común de `calcular_pendiente` y `calcular_orientacion`: las dos describen
    la misma superficie, así que tienen que derivarla igual.

    `dz/dy` es la derivada respecto a la fila, que crece hacia el sur (misma
    convención que Horn 1981).

    NoData es NaN o -9999 (el nodata que traen muchos .tif).

    Regla de NoData 7 de 8: los vecinos NoData que entran al cálculo se
    sustituyen por el valor de la celda central. Quien llame descarta los
    píxeles con `valido` False (centro sin dato) o `n_vecinos` < 7 (ventana
    incompleta), que es donde el resultado pasa a NoData.

    `z_factor` es el factor Z: cuántas unidades horizontales vale una
    unidad vertical. 1.0 (default) = z y xy en las mismas unidades.

    Devuelve (dzdx, dzdy, valido, n_vecinos).
    """
    if z_factor <= 0:
        raise ValueError(f"z_factor {z_factor} debe ser > 0")
    if isinstance(res, Affine):
        # el transform ES la fuente de verdad del tamaño de celda; sacarlo aquí
        # evita el `abs(transform[0])` de cada script, que ademas tira la celda
        # no cuadrada al quedarse solo con el eje x
        res_x, res_y = abs(res.a), abs(res.e)
    else:
        res_x, res_y = (res, res) if np.isscalar(res) else res  # type: ignore[misc]
    # ponytail: z_factor divide donde res multiplica (dz*zf/(8*res) es
    # dz/(8*res/zf)), asi que no necesita termino propio en las dos derivadas.
    res_x, res_y = res_x / z_factor, res_y / z_factor

    mde = np.where(mde == -9999, np.nan, np.asarray(mde, dtype=float))
    valido = ~np.isnan(mde)
    # e es a la vez el valor central y el término de vecinos válidos: el NoData
    # entra como 0 y su aporte real se reinyecta con `faltante`
    e = np.where(valido, mde, 0.0)
    faltante = (~valido).astype(float)

    # mode='constant': fuera del raster no hay dato que reflejar. Da igual qué
    # entre ahí, esas celdas caen solas por la regla de los 7 vecinos; el
    # conteo usa el mismo modo para que no haya dos que desincronizar.
    def _corr(arr: FloatArray, k: FloatArray) -> FloatArray:
        return correlate(arr, k, mode="constant", cval=0.0)

    dzdx = (_corr(e, _KX) + e * _corr(faltante, _KX)) / (8 * res_x)
    dzdy = (_corr(e, _KY) + e * _corr(faltante, _KY)) / (8 * res_y)
    n_vecinos = _corr(valido.astype(float), _VECINOS)
    return dzdx, dzdy, valido, n_vecinos


def _fuera_horn(
    valido: npt.NDArray[np.bool_],
    n_vecinos: FloatArray,
    invalidar: npt.NDArray[np.bool_] | None = None,
) -> npt.NDArray[np.bool_]:
    """Celdas sin resultado de Horn: la regla 7 de 8, más `invalidar` si se da."""
    fuera = ~valido | (n_vecinos < 7)
    if invalidar is None:
        return fuera
    invalidar = np.asarray(invalidar, dtype=bool)
    if invalidar.shape != fuera.shape:
        raise ValueError(f"invalidar {invalidar.shape} no cuadra con el MDE {fuera.shape}")
    return fuera | invalidar


def calcular_orientacion(
    mde: FloatArray,
    resolucion: float | tuple[float, float] | Affine,
    *,
    unidad: str = "clase",
    z_factor: float = 1.0,
    invalidar: npt.NDArray[np.bool_] | None = None,
) -> npt.NDArray[Any]:
    """Orientación (aspect): hacia dónde mira cada ladera del MDE.

    Deriva con Horn sobre la ventana 3x3, igual que `calcular_pendiente`: la
    ventana ya promedia los 8 vecinos, así que no hace falta suavizar el MDE
    antes.

    Los píxeles sin pendiente medible quedan sin orientación: su azimut es
    arbitrario (`atan2(0, 0)`). Misma regla de NoData que la pendiente: centro
    sin dato o menos de 7 vecinos válidos -> sin orientación. Eso incluye el
    anillo del borde del raster y la orilla de los huecos.

    Parámetros
    ----------
    unidad : 'clase' (default) o 'grados'.

        'clase' clasifica el azimut de máxima pendiente de bajada en 4
        cuadrantes y devuelve **uint8**: 0=plano/NoData, 1=Norte, 2=Este,
        3=Sur, 4=Oeste. Es la salida histórica y la que consume
        `combinar_categorias`.

        'grados' devuelve el azimut continuo 0-360 como **float32** (0=Norte,
        90=Este, 180=Sur, 270=Oeste), que es lo que hay que comparar contra un
        raster de orientación de otro SIG. OJO a la convención
        de NoData: aquí sale NaN, como en el resto del módulo; otros SIG marcan el
        plano con -1 o con -9999. Reclasifica antes de restar rasters.
    z_factor : factor Z, cuántas unidades horizontales vale una
        unidad vertical (z en pies y xy en metros -> 0.3048). 1.0 (default) =
        mismas unidades. No cambia el azimut salvo con celda no cuadrada, pero
        se acepta para que aspecto y pendiente describan la misma superficie.
    invalidar : máscara booleana de celdas que quedan sin orientación además de
        las que marca la regla 7 de 8 (el segundo paso de `mascara_relleno`).
        Gemelo del de `calcular_pendiente`, para que las dos sigan describiendo
        la misma superficie: si una zona se invalida en la pendiente y no en el
        aspecto, quedan dos opiniones distintas de dónde hay dato.

    Devuelve un array del mismo shape que `mde`; el dtype depende de `unidad`.
    """
    if unidad not in ("clase", "grados"):
        raise ValueError(f"unidad '{unidad}' no válida (usa 'clase' o 'grados')")

    dzdx, dzdy, valido, n_vecinos = _derivadas_horn(mde, resolucion, z_factor)

    # azimut compass del descenso: componente este = -dzdx, norte = +dzdy
    # (dzdy es respecto a la fila, que crece hacia el sur)
    deg = np.degrees(np.arctan2(-dzdx, dzdy)) % 360
    sin_orientacion = _fuera_horn(valido, n_vecinos, invalidar) | (np.hypot(dzdx, dzdy) < 1e-9)

    if unidad == "grados":
        return np.where(sin_orientacion, np.nan, deg).astype(np.float32)

    clase = np.zeros(deg.shape, dtype=np.uint8)
    clase[(deg >= 315) | (deg < 45)] = 1  # Norte
    clase[(deg >= 45) & (deg < 135)] = 2  # Este
    clase[(deg >= 135) & (deg < 225)] = 3  # Sur
    clase[(deg >= 225) & (deg < 315)] = 4  # Oeste
    clase[sin_orientacion] = 0  # sin pendiente, NoData o ventana incompleta
    return clase


def calcular_pendiente(
    mde: FloatArray,
    resolucion: float | tuple[float, float] | Affine,
    *,
    unidad: str = "porcentaje",
    z_factor: float = 1.0,
    invalidar: npt.NDArray[np.bool_] | None = None,
) -> npt.NDArray[np.float32]:
    """Pendiente de cada píxel del MDE (algoritmo planar de Horn).

    Método planar de Horn: diferencias
    finitas de tercer orden (Horn 1981) sobre la ventana 3x3, no una
    aproximación. Comparte la derivada con `calcular_orientacion`.

    Parámetros
    ----------
    resolucion : tamaño de celda. Pásale el `transform` (Affine) que devolvió
        `interpolar_mde` o `cargar_raster` y lo saca solo, ejes x e y por
        separado. También acepta un número (celda cuadrada) o (res_x, res_y),
        pero no le pases la constante del script: si la resolución se deriva
        sola, la constante se separa del transform sin avisar.
    unidad : 'porcentaje' (default) o 'grados'. Muchos SIG traen 'grados' por
        defecto; aquí no se cambia porque los rangos de reclasificación de la
        librería están en porcentaje.
    z_factor : factor Z, cuántas unidades horizontales vale una
        unidad vertical. 1.0 (default) = z y xy en las mismas unidades. Con z en pies y xy en metros son
        0.3048; sin él la pendiente sale 3.28 veces alta y en silencio.

    Regla de NoData 7 de 8: centro NoData -> NoData; si menos
    de 7 de los 8 vecinos son válidos -> NoData (afecta bordes y orillas de
    zonas sin dato). Los vecinos NoData que sí se computan se sustituyen por
    el valor de la celda central. Los píxeles NoData de
    salida quedan como NaN. Devuelve array float32 del mismo shape que `mde`.

    invalidar : máscara booleana de celdas que salen NaN además de las que
        marca la regla 7 de 8. Es el segundo paso de `mascara_relleno`, hecho
        aquí para que el script no tenga que importar numpy solo para escribir
        `pendiente[relleno] = np.nan`:

            relleno = mascara_relleno(mde)       # ANTES de rellenar
            mde = rellenar_nodata(mde)
            pendiente = calcular_pendiente(mde, tf, invalidar=relleno)

        Idéntico a asignar NaN después; lo único que cambia es de quién es el
        `import`.

    OJO: si el MDE viene de `rellenar_nodata`, las zonas rellenadas son
    mesetas planas que salen con 0% de pendiente sin quedar marcadas. Usa
    `mascara_relleno` sobre el MDE de ANTES de rellenar y pásala en `invalidar`.
    """
    if unidad not in ("porcentaje", "grados"):
        raise ValueError(f"unidad '{unidad}' no válida (usa 'porcentaje' o 'grados')")

    dzdx, dzdy, valido, n_vecinos = _derivadas_horn(mde, resolucion, z_factor)
    magnitud = np.hypot(dzdx, dzdy)

    if unidad == "porcentaje":
        salida = magnitud * 100.0
    else:
        salida = np.degrees(np.arctan(magnitud))

    salida[_fuera_horn(valido, n_vecinos, invalidar)] = np.nan
    return salida.astype(np.float32)


def _extremos_ruta(
    transform: Affine, crs: Any, puntos: gpd.GeoDataFrame, transitable: npt.NDArray[np.bool_]
) -> tuple[CRS, FloatArray, tuple[int, int], tuple[int, int]]:
    """Valida lo común a las rutas y devuelve (crs, xy, celda origen, celda destino)."""
    crs = CRS.from_user_input(crs)
    if crs.is_geographic:
        raise ValueError(
            f"crs {crs.name} es geográfico: la distancia de la ruta saldría en "
            "grados. Reproyecta el ráster a UTM antes."
        )
    if transform.b or transform.d:
        raise ValueError("transform rotado: la ruta asume celdas alineadas a los ejes")
    puntos = _a_crs(puntos, crs, "puntos")
    if len(puntos) != 2 or not (puntos.geom_type == "Point").all():
        raise ValueError(
            f"`puntos` debe tener exactamente dos puntos (origen, destino); "
            f"llegaron {len(puntos)} geometrías de tipo {sorted(set(puntos.geom_type))}"
        )
    xy = puntos.get_coordinates().to_numpy()
    filas, cols, dentro = _celdas(transform, xy, transitable.shape)
    for nombre, i in (("origen", 0), ("destino", 1)):
        if not dentro[i]:
            raise ValueError(f"el {nombre} {tuple(xy[i])} cae fuera del ráster")
        if not transitable[filas[i], cols[i]]:
            raise ValueError(f"el {nombre} cae en un píxel intransitable (sin dato)")
    return crs, xy, (int(filas[0]), int(cols[0])), (int(filas[1]), int(cols[1]))


def _capa_ruta(
    transform: Affine,
    crs: CRS,
    xy: FloatArray,
    celdas: list[tuple[int, int]],
    campo: str,
    valor: float,
) -> gpd.GeoDataFrame:
    """Línea por los centros de píxel, que empieza y termina en los puntos exactos."""
    centros = [transform * (c + 0.5, f + 0.5) for f, c in celdas]
    linea = LineString([tuple(xy[0]), *centros[1:-1], tuple(xy[1])])
    return gpd.GeoDataFrame({campo: [valor]}, geometry=[linea], crs=crs)


def ruta_a_pie(
    mde: FloatArray,
    transform: Affine,
    crs: Any,
    puntos: gpd.GeoDataFrame,
    caminos: npt.NDArray[np.bool_] | None = None,
) -> gpd.GeoDataFrame:
    """Ruta a pie más rápida entre dos puntos, con costo anisótropo (Tobler).

    El costo es de cada PASO entre píxeles vecinos (8 vecinos), no de cada
    píxel: la pendiente es la del paso, con signo, en la dirección de la
    marcha (desnivel entre centros / distancia). Así cruzar una ladera por la
    curva de nivel cuesta como llano, y subir cuesta distinto que bajar. Con
    una pendiente por píxel (la máxima, de Horn) el recorrido a media ladera
    se cobra como si se fuera de frente ladera arriba y abajo: con Tobler, en
    una ladera de 45 %, unas 4 veces de más.

    Velocidad de Tobler (1993): 6 * exp(-3.5 * |s + 0.05|) km/h con s =
    desnivel / distancia del paso; la máxima es en bajada suave de 5 %. Fuera de
    camino, 3/5 de esa velocidad (su factor para campo traviesa). Un paso cuenta
    como de camino si uno de sus dos píxeles lo es.

    `puntos` : exactamente dos puntos, origen y destino EN ESE ORDEN. La ruta es
        de un sentido: la vuelta es otra ruta y dura otro tiempo.
    `caminos` : máscara booleana de píxeles con camino, p. ej.
        `rasterizar(caminos, mde.shape, transform, todo_tocado=True) == 1`.

    NaN en el MDE = intransitable. Falla claro si un punto cae fuera del ráster
    o sin dato, si no hay paso entre los dos, si `caminos` no tiene la forma del
    MDE, si `crs` es geográfico o si el transform está rotado.

    Devuelve una capa con una línea y el campo `HORAS` (tiempo de marcha).
    """
    mde = np.asarray(mde, dtype=float)
    crs, xy, inicio, fin = _extremos_ruta(transform, crs, puntos, ~np.isnan(mde))
    if caminos is None:
        caminos = np.zeros(mde.shape, dtype=bool)
    caminos = np.asarray(caminos, dtype=bool)
    if caminos.shape != mde.shape:
        raise ValueError(f"caminos {caminos.shape} no cuadra con el MDE {mde.shape}")

    # ponytail: grafo dirigido explicito de scipy, ~550 bytes/px medido (5.4 Mpx
    # = 2.9 GB). El MCP de skimage no admite costo por direccion. Si un MDE no
    # cabe, recortarlo a la caja de los puntos con margen antes de llamar.
    alto, ancho = mde.shape
    res_x, res_y = transform.a, abs(transform.e)
    idx = np.arange(mde.size).reshape(mde.shape)
    origen, destino, horas = [], [], []
    for df in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if df == dc == 0:
                continue
            a = (slice(max(0, -df), alto - max(0, df)), slice(max(0, -dc), ancho - max(0, dc)))
            b = (slice(max(0, df), alto + min(0, df)), slice(max(0, dc), ancho + min(0, dc)))
            ok = ~(np.isnan(mde[a]) | np.isnan(mde[b]))
            dist = math.hypot(df * res_y, dc * res_x)
            s = (mde[b][ok] - mde[a][ok]) / dist
            km_h = 6 * np.exp(-3.5 * np.abs(s + 0.05))
            km_h = np.where((caminos[a] | caminos[b])[ok], km_h, km_h * 0.6)
            origen.append(idx[a][ok])
            destino.append(idx[b][ok])
            horas.append(dist / 1000 / km_h)
    grafo = csr_array(
        (np.concatenate(horas), (np.concatenate(origen), np.concatenate(destino))),
        shape=(mde.size, mde.size),
    )
    del origen, destino, horas

    n0, n1 = inicio[0] * ancho + inicio[1], fin[0] * ancho + fin[1]
    tiempo, previo = dijkstra(grafo, indices=n0, return_predecessors=True)
    if not np.isfinite(tiempo[n1]):
        raise ValueError("no hay paso entre los dos puntos: el MDE sin dato los separa")
    celdas, n = [], n1
    while n >= 0:
        celdas.append(divmod(int(n), ancho))
        n = previo[n]
    return _capa_ruta(transform, crs, xy, celdas[::-1], "HORAS", float(tiempo[n1]))


def ruta_costo_minimo(
    costo: FloatArray,
    transform: Affine,
    crs: Any,
    puntos: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Ruta de costo mínimo entre dos puntos sobre una superficie de costo.

    El trazado de costo mínimo del análisis de distancia: `costo` es el costo
    de cruzar cada píxel por unidad de distancia (p. ej. `pendiente + 1`), y la ruta es la que
    minimiza la suma de costo x metros recorridos, con 8 vecinos (la diagonal
    pesa raíz de 2). El modelo de costo es de quien llama; aquí solo se traza.
    El costo es del píxel, igual en cualquier dirección: para caminar sobre un
    MDE está `ruta_a_pie`, que cobra la pendiente en la dirección de la marcha.

    `puntos` : capa con exactamente dos puntos, origen y destino en ese orden.
        Si está en otro CRS se reproyecta a `crs`.

    NaN o inf en `costo` = intransitable. Falla claro si un punto cae fuera del
    ráster o en un píxel intransitable, si no hay paso entre los dos (un predio
    multiparte, un MDE partido por huecos), si hay costos negativos, si `crs`
    es geográfico (la distancia saldría en grados) o si el transform está rotado.

    Devuelve una capa con una línea y el campo `COSTO` (costo acumulado). La
    línea va por los centros de píxel y empieza y termina en los puntos exactos.
    """
    costo = np.where(np.isnan(costo), np.inf, costo).astype(float)
    if (costo < 0).any():
        raise ValueError("`costo` tiene valores negativos; el costo de un píxel es >= 0")
    crs, xy, inicio, fin = _extremos_ruta(transform, crs, puntos, np.isfinite(costo))

    # ponytail: MCP carga la malla entera, ~80 bytes/px medido (5.4 Mpx = 0.42
    # GB). Si un MDE LiDAR no cabe, recortar el costo a la caja de los puntos
    # con margen antes de llamar.
    mcp = MCP_Geometric(costo, fully_connected=True, sampling=(abs(transform.e), transform.a))
    acumulado, _ = mcp.find_costs([inicio], [fin])
    total = float(acumulado[fin])
    if not np.isfinite(total):
        raise ValueError("no hay paso entre los dos puntos: el costo intransitable los separa")
    return _capa_ruta(transform, crs, xy, mcp.traceback(fin), "COSTO", total)


def reclasificar_rangos(
    arr: FloatArray,
    rangos: list[tuple[Any, float, float]],
    nodata_valor: int = -1,
) -> tuple[npt.NDArray[np.int32], dict[int, Any]]:
    """Reclasifica un raster continuo en códigos enteros por rangos.

    Genérica (cualquier raster continuo, no solo pendientes). Asigna a cada
    rango un código 1, 2, 3... en el orden de la lista.

    Convención de límites: cada rango es
    `(minimo, maximo]` (mínimo excluido, máximo incluido), salvo el PRIMERO,
    que es `[minimo, maximo]` (incluye su mínimo, porque no hay rango previo
    que se lo quede). Así el límite compartido entre dos rangos pertenece
    siempre al de abajo. NaN comparado da False, así que los NoData del raster
    de entrada quedan en `nodata_valor` sin tratamiento aparte.

    Parámetros
    ----------
    rangos : lista de (etiqueta, minimo, maximo). Se asumen contiguos y
        ordenados; usa `float('inf')` para un rango abierto por arriba.
    nodata_valor : código para las celdas sin rango / NoData (default -1).

    Devuelve (array_codigos int32, dict {codigo: etiqueta}). El diccionario
    permite mapear luego los códigos a una columna de texto sin repetir la
    lista de rangos.
    """
    codigos = np.full(arr.shape, nodata_valor, dtype=np.int32)
    etiquetas: dict[int, Any] = {}
    for codigo, (etiqueta, mn, mx) in enumerate(rangos, start=1):
        etiquetas[codigo] = etiqueta
        dentro = (arr >= mn) if codigo == 1 else (arr > mn)
        codigos[dentro & (arr <= mx)] = codigo
    return codigos, etiquetas


def sombreado(
    mde: FloatArray,
    resolucion: float | tuple[float, float] | Affine,
    *,
    azimut: float = 315.0,
    altitud: float = 45.0,
    z_factor: float = 1.0,
) -> npt.NDArray[np.uint8]:
    """Sombreado del relieve (hillshade), uint8 de 0 a 255.

    Deriva con `_derivadas_horn`, la misma derivada de `calcular_pendiente` y
    `calcular_orientacion`: las tres describen la misma superficie. Fórmula de
    Burrough:

        255 * (cos(zen)*cos(pend) + sin(zen)*sin(pend)*cos(az_mat - asp))

    con `zen = 90 - altitud`, `az_mat = 360 - azimut + 90` y `asp` el ángulo
    MATEMÁTICO (desde el este, antihorario) hacia donde cae la ladera,
    recortada a 0 por abajo.

    **TRAMPA: el signo del aspecto.** `calcular_orientacion` da el azimut de
    brújula (desde el norte, horario) como `atan2(-dz/dx, dz/dy)`, porque
    `dz/dy` de Horn es respecto a la FILA, que crece hacia el sur. El ángulo
    matemático de esa misma dirección es `atan2(dz/dy, -dz/dx)`. Con el
    `atan2(dz/dy, dz/dx)` de los libros sale un relieve iluminado desde el lado
    contrario, que se ve igual de creíble (el ojo lo lee como valles donde hay
    lomos) y no avisa. El test compara contra `calcular_orientacion`.

    `azimut` en grados de brújula (315 = sol del NW, el default habitual) y
    `altitud` en grados sobre el horizonte, en `(0, 90]`.

    0 es NoData (centro sin dato o menos de 7 vecinos válidos, la regla
    7 de 8) y TAMBIÉN la sombra total de una ladera de espaldas al sol. Es la
    convención habitual; para separarlas, el NaN del MDE es la máscara.
    """
    if not 0 <= azimut <= 360:
        raise ValueError(f"azimut {azimut} fuera de [0, 360]")
    if not 0 < altitud <= 90:
        raise ValueError(f"altitud {altitud} fuera de (0, 90]")
    dzdx, dzdy, valido, n_vecinos = _derivadas_horn(mde, resolucion, z_factor)
    zen = math.radians(90.0 - altitud)
    az_mat = math.radians((360.0 - azimut + 90.0) % 360.0)
    pend = np.arctan(np.hypot(dzdx, dzdy))
    asp = np.arctan2(dzdy, -dzdx)  # matematico: ver la TRAMPA del docstring
    luz = 255.0 * (
        math.cos(zen) * np.cos(pend) + math.sin(zen) * np.sin(pend) * np.cos(az_mat - asp)
    )
    salida = np.clip(np.rint(luz), 0, 255).astype(np.uint8)
    salida[_fuera_horn(valido, n_vecinos)] = 0
    return salida


def resumen_raster(arr: FloatArray, transform: Affine | None = None) -> dict[str, float]:
    """Estadística de control de un ráster: lo primero que se mira de un .tif.

    Devuelve un dict con `n_celdas`, `n_nan`, `min`, `max`, `media`, `desv`,
    `p5`, `p50`, `p95` (NaN excluidos) y, si se pasa `transform`,
    `ha_sin_dato`: la superficie de las celdas NaN. `ha_sin_dato` asume un CRS
    en metros; con uno geográfico el número no significa nada.

    **TRAMPA: un ráster todo NaN.** `np.nanmean` solo AVISA y devuelve NaN, y
    un dict de NaN se imprime como un resumen más. Aquí levanta ValueError:
    un ráster sin datos no tiene resumen.

    OJO: solo NaN cuenta como sin dato. Un NoData numérico (-9999, 0 de
    INEGI) entra como valor, y se nota en `min`; `cargar_raster` ya lo pasa a
    NaN.
    """
    a = np.asarray(arr, dtype=float)
    if a.size == 0:
        raise ValueError("raster vacio: 0 celdas")
    nan = np.isnan(a)
    n_nan = int(nan.sum())
    if n_nan == a.size:
        raise ValueError(f"raster sin datos: las {a.size} celdas son NaN")
    p5, p50, p95 = np.nanpercentile(a, [5, 50, 95])
    resumen = {
        "n_celdas": int(a.size),
        "n_nan": n_nan,
        "min": float(np.nanmin(a)),
        "max": float(np.nanmax(a)),
        "media": float(np.nanmean(a)),
        "desv": float(np.nanstd(a)),
        "p5": float(p5),
        "p50": float(p50),
        "p95": float(p95),
    }
    if transform is not None:
        celda = abs(transform.a * transform.e - transform.b * transform.d)
        resumen["ha_sin_dato"] = n_nan * celda / 10_000
    return resumen


_ESTADISTICAS = ("media", "min", "max", "n", "suma", "desv")


def estadisticas_zonales(
    arr: FloatArray,
    transform: Affine,
    zonas: gpd.GeoDataFrame,
    campo: str,
    estadisticas: Iterable[str] = ("media", "min", "max", "n"),
) -> pd.DataFrame:
    """Estadística del ráster dentro de cada polígono (estadística zonal), en tabla.

    Una fila por entidad de `zonas`, en su orden y con su índice: `campo`,
    cada estadística pedida, `n_celdas` (celdas cuyo CENTRO cae en la zona,
    con o sin dato) y `n_nan` (las de esas que son NaN). `estadisticas` elige
    de `media`, `min`, `max`, `n` (celdas con dato), `suma` y `desv`
    (poblacional). Una celda pertenece a la zona si su centro cae dentro. `zonas` va en el CRS del ráster: la función no recibe el del
    ráster y no puede reproyectar.

    TRAMPAS, las tres dan un número plausible y equivocado sin avisar:

    1. **Zona más chica que el píxel.** No cubre ningún centro de celda: con un
       `groupby` sobre el ráster de etiquetas desaparece de la tabla. Aquí sale
       su fila con `n_celdas`=0 y NaN, y un aviso que la cuenta. Si NINGUNA
       zona toca una celda levanta ValueError: eso es otro CRS, no un tamaño.
    2. **Zonas solapadas.** `rasterize` deja ganar a la última y la de abajo
       pierde celdas en silencio. Si la suma de áreas supera la de la unión,
       ValueError: resuelve antes con `resolver_por_prioridad`.
    3. **NaN del ráster.** Se excluye del agregado y se cuenta en `n_nan`; una
       media sobre media zona sin dato se reconoce ahí.
    """
    arr = np.asarray(arr, dtype=float)
    if arr.ndim != 2:
        raise ValueError(f"`arr` debe ser 2D; llego {arr.ndim}D")
    _exigir_crs(zonas, "zonas")
    if zonas.empty:
        raise ValueError("`zonas` esta vacia")
    if campo not in zonas.columns:
        raise ValueError(f"campo '{campo}' no existe en `zonas`: {list(zonas.columns)}")
    estadisticas = tuple(estadisticas)
    malas = [e for e in estadisticas if e not in _ESTADISTICAS]
    if not estadisticas or malas:
        raise ValueError(f"estadisticas {malas or '()'} no validas; usa {_ESTADISTICAS}")

    geoms = zonas.geometry
    validas = (geoms.notna() & ~geoms.is_empty).to_numpy()
    areas = geoms[validas].area
    if areas.sum() <= 0:
        raise ValueError("`zonas` sin area: se esperan poligonos")
    solape = areas.sum() - unary_union(list(geoms[validas])).area
    if solape > 1e-6 * areas.sum():
        raise ValueError(
            f"`zonas` se solapan ({solape:.2f} unidades2): rasterize deja ganar a "
            "la ultima y la de abajo pierde celdas. Resuelve antes con "
            "resolver_por_prioridad"
        )

    n = len(zonas)
    ids = np.arange(1, n + 1)
    etiquetas = _rasterize(
        [(g.__geo_interface__, int(i)) for g, i, ok in zip(geoms, ids, validas) if ok],
        out_shape=arr.shape,
        transform=transform,
        fill=0,
        dtype=np.int32,
    ).ravel()
    valores = arr.ravel()
    nan = np.isnan(valores)
    n_celdas = np.bincount(etiquetas, minlength=n + 1)[1:]
    n_nan = np.bincount(etiquetas[nan], minlength=n + 1)[1:]
    if not n_celdas.any():
        raise ValueError(
            "ninguna zona cubre un centro de celda: ¿`zonas` en otro CRS que el raster?"
        )
    # el NaN se va a la etiqueta 0 (fondo): fuera de todo agregado
    lab = np.where(nan, 0, etiquetas)
    v = np.where(nan, 0.0, valores)
    cuenta = np.bincount(lab, minlength=n + 1)[1:]
    vacia = cuenta == 0

    with np.errstate(invalid="ignore", divide="ignore"):
        suma = np.bincount(lab, weights=v, minlength=n + 1)[1:]
        media = suma / cuenta
    tabla: dict[str, Any] = {campo: zonas[campo].to_numpy()}
    for e in estadisticas:
        if e == "media":
            tabla[e] = media
        elif e == "n":
            tabla[e] = cuenta
        elif e == "suma":
            tabla[e] = np.where(vacia, np.nan, suma)
        elif e == "desv":
            # dos pasadas: E[x2]-media2 cancela con cotas de miles de metros
            dif = np.where(lab > 0, v - np.concatenate([[0.0], media])[lab], 0.0)
            with np.errstate(invalid="ignore", divide="ignore"):
                var = np.bincount(lab, weights=dif * dif, minlength=n + 1)[1:] / cuenta
            tabla[e] = np.sqrt(var)
        else:
            fn = _minimo_zonal if e == "min" else _maximo_zonal
            r = np.asarray(fn(valores, labels=lab, index=ids), dtype=float)
            tabla[e] = np.where(vacia, np.nan, r)
    tabla["n_celdas"] = n_celdas
    tabla["n_nan"] = n_nan

    sin_celdas = n_celdas == 0
    if sin_celdas.any():
        nombres = list(zonas[campo].to_numpy()[sin_celdas][:5])
        print(
            f"Aviso: {int(sin_celdas.sum())} zona(s) sin ninguna celda (mas chicas "
            f"que el pixel o fuera del raster), salen con n_celdas=0 y NaN. "
            f"{campo}: {nombres}"
        )
    return pd.DataFrame(tabla, index=zonas.index)


def alinear_rasters(
    referencia: tuple[FloatArray, Affine, CRS],
    *otros: tuple[FloatArray, Affine, CRS],
    metodo: Resampling = Resampling.bilinear,
) -> list[FloatArray]:
    """Lleva cada ráster de `otros` a la malla de `referencia` (misma celda, origen y extensión).

    Cada ráster entra como la tupla `(arr, transform, crs)`. Devuelve una lista
    con un array por cada uno de `otros`, en su orden, con la forma, el
    transform y el CRS de `referencia`; el de referencia no se devuelve porque
    no cambia. Lo que queda fuera del ráster de origen sale NaN.

    **TRAMPA, la razón de que exista:** dos arrays de la misma FORMA con
    distinto transform se suman, se restan y se comparan sin ningún error, y
    el resultado es basura: cada celda opera contra la de otro sitio. Media
    celda de desfase entre dos MDE da una diferencia sistemática que parece
    un hallazgo. Una vez alineados, `a - b` o `(a > 5) & (b < 3)` ya son el
    álgebra de mapas, sin función aparte.

    `metodo`: bilinear (default) para continuos (elevación, pendiente, un
    índice). **Para categóricos, `Resampling.nearest`**: una clase interpolada
    no es una clase (entre la 2 y la 4 aparece una 3 que no está en ninguna
    leyenda).
    """
    if not otros:
        raise ValueError(
            "alinear_rasters necesita al menos un raster ademas de la referencia"
        )
    arr_ref, tf_ref, crs_ref = referencia
    if crs_ref is None:
        raise ValueError("la referencia no tiene CRS")
    forma = np.shape(arr_ref)
    if len(forma) != 2:
        raise ValueError(f"la referencia debe ser 2D; llego {len(forma)}D")
    salida = []
    for i, (arr, tf, crs) in enumerate(otros, start=1):
        if crs is None:
            raise ValueError(f"el raster {i} de `otros` no tiene CRS")
        a = np.asarray(arr, dtype=float)
        if a.ndim != 2:
            raise ValueError(f"el raster {i} de `otros` debe ser 2D; llego {a.ndim}D")
        destino = np.full(forma, np.nan)
        reproject(
            source=a,
            destination=destino,
            src_transform=tf,
            src_crs=crs,
            dst_transform=tf_ref,
            dst_crs=crs_ref,
            src_nodata=np.nan,
            dst_nodata=np.nan,
            resampling=metodo,
        )
        salida.append(destino)
    return salida


def curvas_de_nivel(
    mde: FloatArray,
    transform: Affine,
    crs: CRS,
    equidistancia: float,
    base: float = 0.0,
    campo: str = "cota",
) -> gpd.GeoDataFrame:
    """Curvas de nivel desde un MDE, con `contourpy`.

    El camino de vuelta de `interpolar_mde`, y el mejor QA que hay: redibujar
    las curvas del MDE y compararlas con las de partida
    (`concordancia_lineas`, una cota a la vez). Devuelve un GeoDataFrame de
    LineString con la columna `campo`; una curva cerrada sale como anillo.

    TRAMPAS:

    1. **Las coordenadas.** `contourpy` interpola entre los puntos que le
       pases. El valor de una celda es el de su CENTRO
       (`transform * (col + 0.5, fila + 0.5)`); con las esquinas todo sale
       corrido medio píxel, en dirección de la pendiente, y a 20 m de píxel son
       10 m que ningún error señala.
    2. **Los NaN cortan la curva.** Así debe ser: donde no hay MDE no hay
       curva, y no se rellena.
    3. **Los niveles** son `base + k*equidistancia`, con `k` ENTERO entre el
       mínimo y el máximo. Un `np.arange` en float acumula error y la cota 2400
       sale 2399.9999999: no casa con la curva de partida al unir por cota.

    Falla sin CRS, con `equidistancia` <= 0, con el transform rotado o con un
    MDE todo NaN.
    """
    if crs is None:
        raise ValueError("curvas_de_nivel necesita el CRS del MDE")
    if not equidistancia > 0:
        raise ValueError(f"equidistancia {equidistancia} debe ser > 0")
    if transform.b != 0 or transform.d != 0:
        raise ValueError("transform rotado: remuestrea a una malla norte-arriba antes")
    z = np.asarray(mde, dtype=float)
    if z.ndim != 2 or min(z.shape) < 2:
        raise ValueError(f"el MDE debe ser 2D de al menos 2x2; llego {z.shape}")
    if np.isnan(z).all():
        raise ValueError("MDE sin datos: todas las celdas son NaN")

    # centros de celda (TRAMPA 1), en orden creciente para contourpy
    alto, ancho = z.shape
    x = transform.c + transform.a * (np.arange(ancho) + 0.5)
    y = transform.f + transform.e * (np.arange(alto) + 0.5)
    if transform.a < 0:
        x, z = x[::-1], z[:, ::-1]
    if transform.e < 0:
        y, z = y[::-1], z[::-1]
    gen = contourpy.contour_generator(x, y, z, line_type="Separate")

    k_min = math.ceil((np.nanmin(z) - base) / equidistancia)
    k_max = math.floor((np.nanmax(z) - base) / equidistancia)
    cotas: list[float] = []
    lineas: list[LineString] = []
    for k in range(k_min, k_max + 1):
        nivel = base + k * equidistancia  # TRAMPA 3: k entero, sin acumular
        for trazo in gen.lines(nivel):
            if len(trazo) >= 2:
                cotas.append(nivel)
                lineas.append(LineString(trazo))
    return gpd.GeoDataFrame({campo: cotas}, geometry=lineas, crs=crs)


def vista_previa(
    capa_o_array: Any,
    ruta: str | PathLike[str],
    transform: Affine | None = None,
    ancho_px: int = 1200,
) -> npt.NDArray[np.uint8]:
    """Escribe un PNG en grises para MIRAR un resultado. No es un mapa.

    Sin leyenda, colores, escala ni norte (eso es la aplicación, no la
    librería) y sin matplotlib. `capa_o_array` puede ser:

    - un array 2D: se estira de p2 a p98 a grises 1-255; NaN en negro (0).
    - un GeoDataFrame: contorno blanco sobre negro, sobre su extent a
      `ancho_px` de ancho. Los polígonos se dibujan por su borde.
    - la tupla `(array, capa)`: la capa va encima del array, en blanco. Aquí
      `transform` es obligatorio, porque sin él no se sabe dónde cae la capa.

    Con array solo, `transform` es opcional: sin él se toma la malla en
    píxeles. Devuelve la imagen uint8 que se escribió.

    TRAMPA: un raster de decenas de millones de celdas no se escribe
    entero; se remuestrea a `ancho_px` con `remuestrear` ANTES de estirar.
    Nunca se agranda: un array más angosto que `ancho_px` sale a su tamaño.

    TRAMPA 2: el dato mínimo va al gris 1, no al 0. Con 0 se confundiría con
    el NaN y un hueco de datos no se vería.
    """
    if ancho_px < 2:
        raise ValueError(f"ancho_px {ancho_px} debe ser >= 2")
    if os.path.splitext(os.fspath(ruta))[1].lower() != ".png":
        raise ValueError(f"la ruta debe terminar en .png: {ruta}")

    arr, capa = None, None
    if isinstance(capa_o_array, gpd.GeoDataFrame):
        capa = capa_o_array
    elif isinstance(capa_o_array, tuple):
        arr, capa = capa_o_array
        if transform is None:
            raise ValueError("con (array, capa) hace falta transform para ubicar la capa")
    else:
        arr = capa_o_array

    if capa is not None:
        capa = capa[capa.geometry.notna() & ~capa.geometry.is_empty]
        if capa.empty:
            raise ValueError("capa vacia: no hay geometria que dibujar")

    if arr is None:
        # malla sobre el extent de la capa, con 1 px de margen para que el
        # borde exterior no caiga justo en el filo de la imagen
        x0, y0, x1, y1 = capa.total_bounds
        if x1 <= x0:
            raise ValueError("la capa no tiene ancho (una linea vertical o un punto)")
        res = (x1 - x0) / (ancho_px - 2)
        tf = Affine(res, 0.0, x0 - res, 0.0, -res, y1 + res)
        img = np.zeros((math.ceil((y1 - y0) / res) + 2, ancho_px), dtype=np.uint8)
    else:
        z = np.asarray(arr, dtype=float)
        if z.ndim != 2:
            raise ValueError(f"el array debe ser 2D; llego {z.shape}")
        tf = transform if transform is not None else Affine(1, 0, 0, 0, -1, z.shape[0])
        if z.shape[1] > ancho_px:
            # remuestrear pide CRS y no cambia de CRS: cualquiera proyectado
            # sirve, origen y destino son el mismo
            res = abs(tf.a) * z.shape[1] / ancho_px
            z, tf = remuestrear(z, tf, CRS.from_epsg(3857), res)
        if np.isnan(z).all():
            raise ValueError("raster sin datos: todas las celdas son NaN")
        p2, p98 = np.nanpercentile(z, [2, 98])
        escala = (z - p2) / (p98 - p2) if p98 > p2 else np.ones_like(z)
        img = np.where(np.isnan(z), 0, 1 + 254 * np.clip(escala, 0, 1)).astype(np.uint8)

    if capa is not None:
        trazos = capa.geometry.map(
            lambda g: g.boundary if g.geom_type in ("Polygon", "MultiPolygon") else g
        )
        tinta = rasterizar(
            gpd.GeoDataFrame(geometry=trazos), img.shape, tf, valor=1, todo_tocado=True
        )
        img[tinta == 1] = 255

    # un PNG sin georreferencia es a proposito: es para verlo, no para SIG
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", rasterio.errors.NotGeoreferencedWarning)
        with rasterio.open(
            ruta,
            "w",
            driver="PNG",
            height=img.shape[0],
            width=img.shape[1],
            count=1,
            dtype="uint8",
        ) as destino:
            destino.write(img, 1)
    return img
