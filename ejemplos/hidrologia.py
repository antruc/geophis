"""
Ejemplo: buffer de hidrología por condición (perenne / intermitente).

Muestra cómo, con la librería geophis, el mismo proceso queda mucho más
corto: cargar solo lo del ejido → reproyectar → recortar → separar →
disolver → zona de influencia → resolver solapes → recortar → guardar.
"""

import geophis as geo

# ── CONFIG ─────────────────────────────────────────────────
RUTA_HIDRO = r"C:\SIG\proyecto\datos\hidrologia.shp"
RUTA_EJIDO = r"C:\SIG\proyecto\datos\ejido.shp"

EPSG_UTM = 32613
BUFFER_PERENNE = 20  # declarado: franja ribereña de cauce perenne, criterio del programa de manejo
BUFFER_INTERMITENTE = 10  # declarado: idem para intermitente
CAMPO_AREA = "SUP"  # el SHP recorta los nombres de campo a 10 caracteres

SALIDA_CONDICION = r"C:\SIG\proyecto\salidas\HIDRO_BUFFER_CONDICION.shp"
SALIDA_DISUELTO = r"C:\SIG\proyecto\salidas\HIDRO_BUFFER_DISUELTO.shp"

# ── 1. CARGAR (la hidrología, solo lo que toca el ejido) ───
# `bbox` lee por el índice espacial del archivo: la hidrografía estatal entera
# son cientos de miles de tramos y cientos de MB, y para recortar hay que haber
# cargado. GDAL alinea el CRS de la ventana. Devuelve entidades ENTERAS, así
# que el recorte al ejido sigue haciendo falta.
ejido = geo.reproyectar(geo.cargar(RUTA_EJIDO), EPSG_UTM, "ejido")
hidro = geo.cargar(RUTA_HIDRO, bbox=ejido)
# el vocabulario es legal y no cambia; el nombre de la columna sí, con cada
# entrega. Se detecta una vez, sobre la ventana del ejido.
condicion = geo.detectar_campo(hidro, {"PERENNE", "INTERMITENTE"})

# ── 2. REPROYECTAR A UTM ───────────────────────────────────
hidro = geo.reproyectar(hidro, EPSG_UTM, "hidro")

# ── 3. RECORTAR AL EJIDO ───────────────────────────────────
hidro = geo.recortar(hidro, ejido)
print(f"Hidrología en el ejido: {len(hidro)} entidades")
print(hidro[condicion].value_counts().to_string())  # lo que no es PERENNE/INTERMITENTE no lleva franja

# ── 4. SEPARAR, DISOLVER Y BUFFER POR CONDICION ────────────
perenne = geo.disolver(
    geo.seleccionar_por_atributo(hidro, f"{condicion} == 'PERENNE'"),
    campo="CONDICION",
    valor="PERENNE",
)
perenne = geo.zona_de_influencia(perenne, BUFFER_PERENNE)

intermitente = geo.disolver(
    geo.seleccionar_por_atributo(hidro, f"{condicion} == 'INTERMITENTE'"),
    campo="CONDICION",
    valor="INTERMITENTE",
)
intermitente = geo.zona_de_influencia(intermitente, BUFFER_INTERMITENTE)

# ── 5. QUITAR SOLAPE (el orden de la lista es la prioridad) ─
# resolver_por_prioridad en vez de borrar a mano: con dos capas da igual,
# pero si mañana entra una tercera condición (efímero) sigue saliendo bien
resultado = geo.resolver_por_prioridad([perenne, intermitente])

# ── 6. RECORTAR AL EJIDO Y CALCULAR SUPERFICIE ─────────────
resultado = geo.recortar(resultado, ejido)
resultado = geo.calcular_superficie(resultado, campo=CAMPO_AREA)  # área al final
geo.guardar(resultado, SALIDA_CONDICION)

# ── 7. VERSIÓN DISUELTA (una sola geometría) ───────────────
disuelto = geo.disolver(resultado, campo="CONDICION", valor="HIDROLOGIA")
geo.guardar(disuelto, SALIDA_DISUELTO)

for cond, sup in zip(resultado["CONDICION"], resultado[CAMPO_AREA]):
    print(f"  {cond:14s}: {sup:8.2f} ha")
print(f"-> {SALIDA_CONDICION}")
