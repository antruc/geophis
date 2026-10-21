"""Rutas de archivo: comprobarlas antes de abrir o escribir, y los errores de GDAL en español.

Aquí y no en archivo.py porque raster.py también abre y escribe archivos, y
las dos están en la misma capa: no pueden importarse entre sí.
"""

import difflib
import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from os import PathLike
from typing import Any

import geopandas as gpd


# Errores de GDAL (pyogrio y rasterio, en inglés): fragmento -> (tipo, texto ES).
# Se buscan por TEXTO y no por clase: la clase cambia según el motor (pyogrio,
# fiona, rasterio) y el texto es de GDAL, el mismo debajo de los tres.
_ERRORES_ES = {
    "not recognized as being in a supported file format": (
        ValueError,
        "'{ruta}' no es un formato que se pueda leer, o el archivo está dañado "
        "o incompleto (un .shp necesita su .shx y su .dbf al lado)",
    ),
    "Permission denied": (
        PermissionError,
        "sin permiso sobre '{ruta}': la carpeta es de solo lectura o el archivo "
        "está abierto en otro programa; ciérralo y repite",
    ),
    "Attempt to write non-": (
        ValueError,
        "'{ruta}' admite un solo tipo de geometría y la capa los mezcla: separa "
        "con gdf[gdf.geom_type == 'Polygon'] o guarda en .gpkg",
    ),
    "No such file or directory": (FileNotFoundError, "no existe el archivo '{ruta}'"),
}


@contextmanager
def _traducir_errores(ruta: Any) -> Iterator[None]:
    """Reemite en español los errores conocidos de GDAL; el resto pasa tal cual."""
    try:
        yield
    except Exception as e:
        texto = str(e)
        for clave, (tipo, es) in _ERRORES_ES.items():
            if clave in texto:
                raise tipo(es.format(ruta=ruta)) from e
        raise


def _es_remota(ruta: Any) -> bool:
    """URL o ruta virtual de GDAL (/vsis3/, /vsizip/...): no está en el disco."""
    return "://" in str(ruta) or str(ruta).startswith("/vsi")


def _exigir_tipo_ruta(ruta: Any, que: str) -> None:
    """Falla claro si `ruta` no es texto: el error de os.path no dice qué falta."""
    if isinstance(ruta, (str, PathLike)):
        return
    pista = ""
    if isinstance(ruta, gpd.GeoDataFrame):
        pista = ": es una capa ya cargada, pásala directo a la función que la usa"
    elif type(ruta).__name__ == "Affine":
        pista = ": es el transform, revisa el orden de los argumentos"
    raise TypeError(f"{que} debe ser texto; llegó {type(ruta).__name__}{pista}")


def _exigir_archivo(ruta: Any, carpeta_ok: bool = False) -> None:
    """Falla en español si `ruta` no es un archivo que exista.

    `carpeta_ok` deja pasar una carpeta (una File Geodatabase .gdb es una). Si el
    archivo no está, propone los nombres parecidos de esa carpeta: la causa más
    común es una errata o la extensión, y la segunda una ruta relativa que se
    busca desde otra carpeta, por eso se muestra la ruta absoluta.
    """
    _exigir_tipo_ruta(ruta, "la ruta")
    if not str(ruta).strip():
        raise ValueError("ruta vacía: falta el nombre del archivo")
    if _es_remota(ruta):
        return
    if os.path.isdir(ruta):
        if carpeta_ok:
            return
        raise IsADirectoryError(
            f"'{ruta}' es una carpeta: pasa el archivo de dentro, p. ej. "
            f"os.path.join(r'{ruta}', 'capa.shp')"
        )
    if os.path.exists(ruta):
        return
    absoluta = os.path.abspath(ruta)
    carpeta, nombre = os.path.split(absoluta)
    if not os.path.isdir(carpeta):
        raise FileNotFoundError(f"no existe el archivo '{ruta}': no existe la carpeta '{carpeta}'")
    parecidos = difflib.get_close_matches(nombre, os.listdir(carpeta), n=3, cutoff=0.6)
    donde = "" if os.path.isabs(ruta) else f" (se buscó en '{absoluta}')"
    detalle = f"; ¿quisiste decir {parecidos}?" if parecidos else ""
    raise FileNotFoundError(f"no existe el archivo '{ruta}'{donde}{detalle}")


def _exigir_salida(ruta: Any, extensiones: Iterable[str], que: str = "el archivo") -> str:
    """Comprueba la ruta donde se va a escribir y devuelve su extensión.

    Sin esto, una ruta vacía, una carpeta o una extensión desconocida no fallan
    claro: GDAL crea una CARPETA con un .shp dentro, o un GeoTIFF llamado .png,
    o archivos sin nombre ('.shp') en la carpeta actual.
    """
    _exigir_tipo_ruta(ruta, "la ruta de salida")
    extensiones = sorted(extensiones)
    ejemplo = f"'{que}{extensiones[0]}'"
    texto = str(ruta)
    if not texto.strip():
        raise ValueError(f"ruta de salida vacía: falta el nombre del archivo, p. ej. {ejemplo}")
    nombre = os.path.basename(texto.rstrip("/\\"))
    if os.path.isdir(texto) or texto.endswith(("/", "\\")):
        raise IsADirectoryError(
            f"'{ruta}' es una carpeta: añade el nombre del archivo, p. ej. "
            f"os.path.join(r'{ruta}', {ejemplo})"
        )
    raiz, ext = os.path.splitext(nombre)
    ext = ext.lower()
    if not raiz:
        raise ValueError(f"'{ruta}' no tiene nombre de archivo, p. ej. {ejemplo}")
    if ext not in extensiones:
        raise ValueError(
            f"'{ruta}': la extensión '{ext or '(ninguna)'}' no es de {que}; usa una de {extensiones}"
        )
    return ext
