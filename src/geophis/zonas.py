"""Zonificar: de un raster continuo a poligonos por rangos.

La cadena de escritorio (Reclasificar -> Sieve -> Raster a poligono -> Recortar
-> Superficie) en un solo sitio, con sus cuatro trampas resueltas: mascara
`todo_tocado=True`, sieve solo dentro de la mascara, recorte vectorial despues y
SUP detras del ultimo paso que mueve la geometria.

Capa de arriba: usa `raster` y `geometria` a la vez, como `barridos`.
"""

from collections.abc import Iterable
from typing import Any

import geopandas as gpd
import numpy as np
import numpy.typing as npt
from pyproj import CRS
from rasterio import Affine

from .geometria import calcular_superficie, disolver_por_grupo, recortar, separar_multipartes
from .proyeccion import _a_crs
from .raster import limpiar_moteado, poligonizar, rasterizar, reclasificar_rangos


def zonificar(
    arr: npt.NDArray[Any],
    transform: Affine,
    crs: CRS | int | str,
    rangos: list[tuple[Any, float, float]],
    min_pixeles: int,
    recorte: gpd.GeoDataFrame | None = None,
    invalidar: npt.NDArray[np.bool_] | None = None,
    exentos: Iterable[Any] | None = None,
    campo: str = "CLASE",
    por_clase: bool = False,
) -> gpd.GeoDataFrame:
    """Raster continuo -> poligonos por rangos, con `gridcode`, `campo` y `SUP` (ha).

    Sirve igual para pendiente, HAND, NDVI o cualquier otro continuo:

        zonas = geo.zonificar(pendiente, tf, crs, RANGOS, p["min_pixeles"], recorte=predio)
        zonas = geo.zonificar(hand, tf, crs, RANGOS_HAND, min_px, recorte=predio,
                              invalidar=~(pendiente <= 10))  # NaN tambien fuera

    - `rangos` y la convencion de bordes son los de `reclasificar_rangos`.
    - `min_pixeles` es el del sieve (`min_pixeles_umm` o `p["min_pixeles"]`).
    - `recorte`: el sieve se limita a sus celdas (`todo_tocado=True`, porque
      despues se recorta en vector y con el centro de celda la capa cerraria
      con menos superficie que el predio) y la capa se recorta contra el. Sin
      `recorte`, el raster entero.
    - `invalidar`: celdas que no entran en ninguna clase (otra condicion, como
      la pendiente para el HAND). Quedan fuera del sieve: no absorben ni son
      absorbidas.
    - `exentos`: ETIQUETAS de `rangos` que el sieve no toca. Una clase que es
      una cinta (el escarpe de la pendiente alta) se borra si se le aplica la
      UMM por superficie; ver docs/GEOPHIS.md, "Cuando la CLASE MAS ALTA...".
    - `por_clase`: False da una fila por pieza (para medir, filtrar o
      `eliminar_menores`); True, una multiparte por clase (la tabla).

    El sieve no absorbe lo que solo toca celdas fuera de la mascara, asi que
    tras recortar pueden quedar piezas menores a la UMM junto al borde o a lo
    invalidado. No se tiran aqui: en una particion (pendientes) tirarlas abre
    huecos. Para quitarlas, `eliminar_menores` (absorbe) o filtrar por `SUP`.
    """
    codigos, etiquetas = reclasificar_rangos(arr, rangos)
    if invalidar is not None:
        if invalidar.shape != codigos.shape:
            raise ValueError(f"invalidar {invalidar.shape} y arr {codigos.shape} no son la misma malla")
        codigos[invalidar] = -1
    mascara = codigos > 0
    if recorte is not None:
        recorte = _a_crs(recorte, crs, "recorte")
        mascara &= rasterizar(recorte, codigos.shape, transform, todo_tocado=True) == 1

    por_etiqueta = {et: c for c, et in etiquetas.items()}
    desconocidas = [e for e in exentos or () if e not in por_etiqueta]
    if desconocidas:
        raise ValueError(f"exentos {desconocidas} no estan en rangos: {list(por_etiqueta)}")
    cod_exentos = [por_etiqueta[e] for e in exentos or ()]

    codigos = limpiar_moteado(codigos, min_pixeles, mascara=mascara, exentos=cod_exentos)
    gdf = poligonizar(codigos, transform, crs, mascara=mascara, campo="gridcode")
    if recorte is not None:
        gdf = recortar(gdf, recorte)
    gdf = disolver_por_grupo(gdf, "gridcode") if por_clase else separar_multipartes(gdf)
    gdf[campo] = gdf["gridcode"].map(etiquetas)
    # SUP al final: detras del ultimo paso que mueve la geometria
    return calcular_superficie(gdf, campo="SUP")
