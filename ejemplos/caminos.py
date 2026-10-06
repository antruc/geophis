"""
Ejemplo: buffer jerárquico de caminos (primario > secundario > saca).

Cada nivel recorta al inferior para que no se solapen. Muestra el uso
de geophis con un patrón repetido resuelto en un bucle.
"""

import geophis as geo

# ── CONFIG ───────────────────────────────────────────────────
EPSG_UTM = 32613

# declarado: ancho de derecho de via por jerarquia, en metros. El orden es la
# prioridad (mayor a menor).
NIVELES = [
    ("PRIMARIO", r"C:\SIG\proyecto\datos\camino_primario.shp", 5.0),
    ("SECUNDARIO", r"C:\SIG\proyecto\datos\camino_secundario.shp", 3.0),
    ("SACA", r"C:\SIG\proyecto\datos\camino_saca.shp", 1.75),
]
CAMPO_TIPO = "camino"

SALIDA_COMBINADO = r"C:\SIG\proyecto\salidas\CAMINOS_BUFFER_COMBINADO.shp"
SALIDA_DISUELTO = r"C:\SIG\proyecto\salidas\CAMINOS_BUFFER_DISUELTO.shp"

# ── 1. CARGAR, ASEGURAR CRS, DISOLVER Y BUFFER CADA NIVEL ────
capas = []
for nombre, ruta, dist in NIVELES:
    gdf = geo.cargar(ruta)
    gdf = geo.reproyectar(gdf, EPSG_UTM, nombre)
    gdf = geo.disolver(gdf, campo=CAMPO_TIPO, valor=nombre)
    gdf = geo.zona_de_influencia(gdf, dist)
    capas.append(gdf)

# ── 2. QUITAR SOLAPE: cada nivel pierde contra TODOS los superiores ──
# (recortar solo contra el nivel de arriba dejaba pasar el solape saca-primario
#  donde no hay secundario en medio, que es justo cada entronque)
combinado = geo.resolver_por_prioridad(capas)

# ── 3. GUARDAR ───────────────────────────────────────────────
geo.guardar(combinado, SALIDA_COMBINADO)

# ── 4. VERSIÓN DISUELTA (una sola geometría) ─────────────────
geo.guardar(geo.disolver(combinado, campo="caminos", valor="caminos"), SALIDA_DISUELTO)
