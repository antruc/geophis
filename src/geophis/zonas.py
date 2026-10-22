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
    sin_dato: str | None = "SIN DATO",
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
      UMM por superficie; ver docs/GEOPHIS.md, receta P1.
    - `por_clase`: False da una fila por pieza (para medir, filtrar o
      `eliminar_menores`); True, una multiparte por clase (la tabla).
    - `sin_dato`: etiqueta de las celdas NaN de `arr` dentro de la zona (no
      las de `invalidar`, que se excluyen a proposito). Salen como una clase
      mas, fuera del sieve, y avisa con las ha. Sin ella eran un hueco mudo y
      `comprobar_particion` tronaba: un predio en la costa, con 188 ha por
      debajo de la curva mas baja. `None` = como antes, el hueco se queda.

    El sieve no absorbe lo que solo toca celdas fuera de la mascara, asi que
    tras recortar pueden quedar piezas menores a la UMM junto al borde o a lo
    invalidado. No se tiran aqui: en una particion (pendientes) tirarlas abre
    huecos. Para quitarlas, `eliminar_menores` (absorbe) o filtrar por `SUP`.
    """
    codigos, etiquetas = reclasificar_rangos(arr, rangos)
    vacia = np.isnan(np.asarray(arr, dtype=float))
    if invalidar is not None:
        if invalidar.shape != codigos.shape:
            raise ValueError(f"invalidar {invalidar.shape} y arr {codigos.shape} no son la misma malla")
        codigos[invalidar] = -1
        vacia &= ~invalidar
    mascara = codigos > 0
    if recorte is not None:
        recorte = _a_crs(recorte, crs, "recorte")
        en_zona = rasterizar(recorte, codigos.shape, transform, todo_tocado=True) == 1
        mascara &= en_zona
        vacia &= en_zona

    por_etiqueta = {et: c for c, et in etiquetas.items()}
    desconocidas = [e for e in exentos or () if e not in por_etiqueta]
    if desconocidas:
        raise ValueError(f"exentos {desconocidas} no están en rangos: {list(por_etiqueta)}")
    cod_exentos = [por_etiqueta[e] for e in exentos or ()]

    codigos = limpiar_moteado(codigos, min_pixeles, mascara=mascara, exentos=cod_exentos)
    if sin_dato is not None and vacia.any():
        # despues del sieve: el sin dato ni absorbe ni es absorbido
        cod_vacia = max(etiquetas) + 1
        codigos[vacia] = cod_vacia
        etiquetas = {**etiquetas, cod_vacia: sin_dato}
        mascara |= vacia
        print(
            f"Aviso zonificar: {float(vacia.sum()) * abs(transform.a * transform.e) / 10_000:.2f} ha "
            f"sin dato dentro de la zona (celdas NaN): van como clase '{sin_dato}'. "
            f"Es superficie sin dato de origen, no se reparte entre las clases."
        )
    gdf = poligonizar(codigos, transform, crs, mascara=mascara, campo="gridcode")
    if recorte is not None:
        gdf = recortar(gdf, recorte)
    gdf = disolver_por_grupo(gdf, "gridcode") if por_clase else separar_multipartes(gdf)
    gdf[campo] = gdf["gridcode"].map(etiquetas)
    # SUP al final: detras del ultimo paso que mueve la geometria
    return calcular_superficie(gdf, campo="SUP")
