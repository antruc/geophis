"""Manejo de sistemas de coordenadas (CRS / proyecciones)."""

import warnings
from typing import Any

import geopandas as gpd
import numpy as np
from pyproj import CRS, Transformer
from pyproj.transformer import AreaOfInterest, TransformerGroup

# Exactitud (m) a partir de la cual el cambio de datum se avisa.
_TOL_DATUM_M = 1.0

# Unidades que se pueden pedir a las medidas, como cuántas unidades base (m² o
# m) caben en una. Aquí y no en geometria.py porque `guardar` las necesita
# también para reconocer una columna vieja, y archivo.py no importa geometria.
# Añadir una unidad es añadir una línea.
_UNIDADES_AREA = {"m2": 1.0, "ha": 1e4, "km2": 1e6}
_UNIDADES_LONGITUD = {"m": 1.0, "km": 1e3}


def _exigir_crs(gdf: gpd.GeoDataFrame, nombre: str = "capa") -> None:
    """Falla claro si la capa no tiene CRS; to_crs sobre None da error feo."""
    if gdf.crs is None:
        raise ValueError(
            f"{nombre} no tiene CRS definido; asigna uno antes de reproyectar"
        )


def _a_crs(gdf: gpd.GeoDataFrame, crs: Any, nombre: str = "capa") -> gpd.GeoDataFrame:
    """`gdf` en `crs`, exigiendo que tenga CRS. Sin copia si ya está en él."""
    _exigir_crs(gdf, nombre)
    return gdf if gdf.crs == crs else gdf.to_crs(crs)


def _exigir_proyectado(gdf: gpd.GeoDataFrame | None, nombre: str = "capa") -> None:
    """Falla si la capa no está en un CRS proyectado en METROS.

    Para funciones que miden en metros sin decirlo en la firma. Con grados o
    pies ninguna revienta: devuelven el número con la unidad equivocada.
    """
    if gdf is None:
        return
    _exigir_crs(gdf, nombre)
    if gdf.crs.is_geographic:
        raise ValueError(
            f"{nombre} está en coordenadas geográficas ({gdf.crs.name}): esta "
            "función mide en METROS y en grados daría números con la unidad "
            "equivocada, sin reventar. Proyecta antes: "
            "`epsg = detectar_utm(predio); capa = reproyectar(capa, epsg)`."
        )
    # un State Plane en pies pasa el filtro de arriba y daría ft2 con nombre de m2
    unidad = gdf.crs.axis_info[0]
    if unidad.unit_conversion_factor != 1.0:
        raise ValueError(
            f"{nombre} está en {unidad.unit_name} ({gdf.crs.name}): esta función "
            "mide en METROS. Reproyecta a un CRS en metros (UTM): "
            "`capa = reproyectar(capa, detectar_utm(capa))`."
        )


def _exigir_destino(destino: Any) -> None:
    """Falla claro si el destino es None; to_crs(None) da 'Must pass either
    crs or epsg', tres frames abajo y sin decir qué capa."""
    if destino is None:
        raise ValueError(
            "destino de reproyección es None; si viene de crs.to_epsg(), ese "
            "CRS no tiene código EPSG registrado: pasa otra_capa.crs directo"
        )


def _avisar_datum(gdf: gpd.GeoDataFrame, destino: Any, nombre: str) -> None:
    """Avisa si el cambio de datum de `gdf.crs` a `destino` es inexacto.

    `to_crs` toma la operación que PROJ tenga a mano, y entre NAD27 y WGS84 /
    ITRF92 en México esa operación es de 12 m (medido: 'NAD27 to WGS 84 (18)')
    o de exactitud desconocida, sin decir nada. Se avisa si la mejor operación
    disponible tiene exactitud desconocida o de más de 1 m, o si hay una mejor
    que no se puede usar porque falta su rejilla. Mismo datum (solo cambia la
    proyección): sale sin consultar nada. No descarga rejillas ni activa
    PROJ_NETWORK: la rejilla correcta la decide el usuario.
    """
    origen, dest = gdf.crs, CRS.from_user_input(destino)
    geo_o, geo_d = origen.geodetic_crs, dest.geodetic_crs
    if geo_o is None or geo_d is None or geo_o.equals(geo_d):
        return
    # El área de la capa elige la operación que aplica aquí, no la de Canadá.
    aoi = None
    if len(gdf) and np.isfinite(gdf.total_bounds).all():
        b = Transformer.from_crs(origen, 4326, always_xy=True).transform_bounds(
            *gdf.total_bounds
        )
        if np.isfinite(b).all():
            aoi = AreaOfInterest(*b)
    with warnings.catch_warnings():
        # TransformerGroup avisa en inglés de la rejilla que falta; se dice abajo
        warnings.simplefilter("ignore")
        grupo = TransformerGroup(origen, dest, always_xy=True, area_of_interest=aoi)
    exact = grupo.transformers[0].accuracy if grupo.transformers else -1.0
    faltan = [
        (op.accuracy, [g.short_name for g in op.grids if not g.available])
        for op in grupo.unavailable_operations
        if op.accuracy >= 0 and (exact < 0 or op.accuracy < exact)
    ]
    if 0 <= exact <= _TOL_DATUM_M and not faltan:
        return
    txt = "desconocida" if exact < 0 else f"{exact:g} m"
    print(
        f"Aviso: {nombre}: el cambio de datum de {geo_o.name} a {geo_d.name} "
        f"tiene exactitud {txt}; las coordenadas pueden derivar metros"
    )
    for acc, rejillas in faltan[:3]:
        print(
            f"Aviso: {nombre}: hay una operacion de {acc:g} m que no se usa "
            f"porque falta la rejilla {', '.join(rejillas)}"
        )


def reproyectar(
    gdf: gpd.GeoDataFrame, destino: Any, nombre: str = "capa"
) -> gpd.GeoDataFrame:
    """Deja la capa en el CRS indicado. Si ya está en él, no hace nada.

    `destino` es cualquier cosa que entienda pyproj: EPSG (int), cadena
    ('EPSG:32613', WKT, proj4) u objeto CRS. Para alinear con otra capa que no
    tiene código EPSG (INEGI en LCC ITRF92, p. ej.): reproyectar(gdf, otra.crs)

    Avisa por pantalla cuando reproyecta de verdad; en silencio significa que
    la capa ya venía bien. `nombre` es solo para ese aviso y los errores.

    Ejemplo: reproyectar(gdf, 32613)  # UTM 13N WGS84

    **TRAMPA: el cambio de DATUM.** Entre NAD27 y WGS84/ITRF92 en México la
    operación que usa PROJ por defecto es de 12 m, o de exactitud
    desconocida, y `to_crs` no dice nada. Aquí se avisa cuando la mejor
    operación disponible pasa de 1 m o no declara exactitud, y cuando falta la
    rejilla de una mejor. Cambiar solo la proyección no avisa ni cuesta.

    Devuelve LA MISMA capa (no una copia) cuando el CRS ya coincide. Reproyectar
    a donde ya estás es un no-op caro en capas grandes, así que se salta. Si vas
    a mutar el resultado, copia tú.
    """
    _exigir_crs(gdf, nombre)
    _exigir_destino(destino)
    # equals() compara por equivalencia: un CRS sin código EPSG (to_epsg() = None)
    # pero igual al destino no se reproyecta de más
    if gdf.crs.equals(destino):
        return gdf
    # ponytail: el aviso imprime el destino tal cual, así que con un int
    # sale "32613" y no "EPSG:32613". Formatearlo si estorba al leer logs.
    print(f"{nombre}: reproyectando a {destino}")
    _avisar_datum(gdf, destino, nombre)
    return gdf.to_crs(destino)
