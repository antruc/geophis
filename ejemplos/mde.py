"""
Ejemplo: construcción de un MDE desde curvas de nivel + hidrología.

Produce dos rasters que no son intercambiables:

- MDE_BASE.tif: el terreno tal como se interpoló, CON su NoData intacto y sin
  deformar. Insumo de pendientes y de cualquier medición de forma del terreno.
  Se guarda ANTES de `rellenar_nodata` a propósito: `calcular_pendiente`
  recibe el NaN y lo trata con la regla 7 de 8, mientras que el relleno copia
  el vecino más cercano y deja meseta plana al 0 % sin marcar.
- MDE_HIDRO.tif: relleno y quemado, la entrada de `acondicionar_mde`. Solo
  para flujo: quemar deforma el terreno a propósito. El ACONDICIONADO no se
  guarda: sale en float64 y su micro-pendiente (~1e-5 m) no cabe en el
  float32 del .tif; releído, vuelve a tener miles de sumideros. Se acondiciona
  en la misma sesión que calcula el flujo.

Ningún parámetro de la malla se teclea: `derivar_parametros` los mide o los
deriva, y cada corrida cierra con su tabla de procedencia (REGLA DE LOS
NÚMEROS, docs/GEOPHIS.md).

El manejo del extent replica la práctica de ANUDEM: curvas y cauces se
recortan a un cuadro amplio y el MDE se interpola sobre el predio, que queda
metido dentro de los datos para que las celdas de borde se interpolen con
información a ambos lados.
"""

import geophis as geo

# ── CONFIG ─────────────────────────────────────────────────
CURVAS_SHP = r"C:\SIG\proyecto\datos\curvas_nivel.shp"
CAUCES_SHP = r"C:\SIG\proyecto\datos\hidrologia.shp"
PREDIO_SHP = r"C:\SIG\proyecto\datos\predio.shp"
MDE_BASE = r"C:\SIG\proyecto\salidas\MDE_BASE.tif"  # terreno sin deformar, para pendientes
MDE_HIDRO = r"C:\SIG\proyecto\salidas\MDE_HIDRO.tif"  # relleno + quemado, entrada de acondicionar_mde
PROCEDENCIA = r"C:\SIG\proyecto\salidas\PROCEDENCIA.txt"  # tabla para la memoria técnica

EPSG_UTM = 32613  # UTM 13N
ESCALA = 25_000  # compromiso de entrega; la UMM y la resolución son consecuencia
RANGOS = [
    ("0-5", 0, 5), ("5-10", 5, 10), ("10-15", 10, 15), ("15-25", 15, 25),
    ("25-35", 25, 35), ("35-45", 35, 45), ("45-60", 45, 60), ("60-100", 60, 100),
    (">100", 100, float("inf")),
]  # declarado: rangos legales de pendiente, en %. De aquí sale p_max
PROFUNDIDAD_QUEMA = 5  # m; basta con superar el error local del MDE (ver el RMS del hold-out)

# metros extra alrededor del predio en el extent de SALIDA, ADEMÁS de las dos
# celdas que pide Horn. 0 = solo el predio. Súbelo hasta cubrir la cuenca aguas
# arriba si vas a delimitar cuencas o medir acumulación: con solo el predio,
# el área contribuyente queda cortada en el borde.
MARGEN_CUENCA = 0

# declarado: dominio ESTABLE donde se detecta la cota (regla del bbox). Tiene
# que contener al cuadro de recorte; el assert del paso 3 lo comprueba.
DOMINIO_M = 1500

VALIDAR = True  # hold-out de control de calidad antes de interpolar
SEMILLA = 0  # fija el reparto del hold-out (reproducible)

# ── 1. CARGAR PREDIO Y CURVAS DEL DOMINIO ──────────────────
# el predio es una capa chica: reproyectarlo de entrada no cuesta nada. La
# carta de curvas cubre todo el estado: `bbox` lee solo lo que toca el dominio,
# y se recorta en su CRS original (`recortar` mueve el cuadro, no las curvas)
# antes de reproyectar.
predio = geo.reproyectar(geo.cargar(PREDIO_SHP), EPSG_UTM, "predio")
dominio = geo.crear_cuadro(predio, margen=DOMINIO_M)
curvas = geo.cargar(CURVAS_SHP, bbox=dominio)
curvas = geo.reproyectar(geo.recortar(curvas, dominio), EPSG_UTM, "curvas")
campo, e = geo.detectar_cota(curvas)  # una vez, sobre el dominio declarado

# ── 2. DERIVAR LOS PARÁMETROS, CON SU PROCEDENCIA ──────────
# spline (default): superficie suave, sin el escalonado que dejan los
# triángulos planos del TIN entre curvas. Para TIN con cauces como quiebres,
# ver docs/GEOPHIS.md §"Elegir el método de interpolación".
# `vecinos="medir"` cuesta una interpolación por cada n; sin él entra el
# default 48, que nadie eligió, y el assert de abajo lo para.
p = geo.derivar_parametros(curvas, campo, predio, RANGOS, ESCALA, vecinos="medir")
open(PROCEDENCIA, "w", encoding="utf-8").write(geo.tabla_procedencia(p))
assert not geo.sin_medir(p), f"nadie eligio: {geo.sin_medir(p)}"
h = p["resolucion"]  # float con procedencia; se pasa tal cual

# ── 3. GENERAR LOS CUADROS DEL MDE ─────────────────────────
# `margen_salida` de dos celdas como mínimo: la ventana 3x3 de Horn deja NaN
# la celda del borde, y sin él la capa de pendientes pierde ese anillo.
cuadro_recorte, cuadro_salida = geo.cuadros_mde(
    predio, margen_salida=2 * float(h) + MARGEN_CUENCA, parametros=p
)
assert dominio.union_all().contains(cuadro_recorte.union_all()), (
    "el cuadro de recorte se sale del dominio: sube DOMINIO_M"
)

# ── 4. RECORTAR CURVAS Y CAUCES AL CUADRO ──────────────────
curvas = geo.recortar(curvas, cuadro_recorte)
# la hidrografía estatal no hace falta cargarla entera: `bbox` lee solo lo que
# toca el cuadro (GDAL alinea el CRS de la ventana), y `recortar` mueve el
# cuadro al CRS de los cauces, no los cauces al del cuadro.
cauces = geo.cargar(CAUCES_SHP, bbox=cuadro_recorte)
cauces = geo.recortar(cauces, cuadro_recorte)
cauces = geo.reproyectar(cauces, EPSG_UTM, "cauces")
cauces = geo.disolver(cauces)

# ── 5. VALIDAR LA INTERPOLACIÓN (hold-out, mismos parámetros) ──
if VALIDAR:
    metricas, puntos = geo.validar_mde(
        curvas, campo, cuadro=cuadro_salida, semilla=SEMILLA, parametros=p
    )

# ── 6. INTERPOLAR ──────────────────────────────────────────
mde, transform = geo.interpolar_mde(curvas, campo, cuadro=cuadro_salida, parametros=p)

# ── 7. GUARDAR LA BASE (terreno sin deformar, NaN intacto) ─
# Antes de rellenar y de quemar/acondicionar. Es el insumo de pendientes:
# medir pendiente sobre el quemado llena el rango >100% de líneas de cauce que
# no existen, y guardar DESPUÉS de rellenar esconde el NoData real bajo
# mesetas planas de relleno.
geo.guardar_raster(mde, MDE_BASE, transform, EPSG_UTM)

# ── 8. RELLENAR NoData ANTES DE QUEMAR ─────────────────────
# el interpolador devuelve NaN fuera de la envolvente convexa de las curvas.
# Quemar primero sería inútil ahí: NaN - profundidad sigue siendo NaN, y el
# relleno posterior copiaría el vecino SIN quemar, perdiendo el cauce en
# silencio. Con el borde de `cuadros_mde` la envolvente suele cubrir todo el
# cuadro de salida y esto no cambia nada, pero si la carta de curvas no cubre
# el predio entero (predio al borde de la hoja, hueco de cobertura) este
# orden es el que salva el quemado.
mde = geo.rellenar_nodata(mde)

# ── 9. QUEMAR CAUCES ───────────────────────────────────────
mde_quemado, n_px = geo.quemar_cauces(mde, cauces, transform, PROFUNDIDAD_QUEMA)
if n_px == 0:
    raise ValueError(
        f"ningún píxel quemado: '{CAUCES_SHP}' no toca el raster; revisa el CRS "
        f"de la hidrología o si de verdad cae fuera del predio"
    )

# ── 10. GUARDAR LA BASE HIDROLÓGICA ────────────────────────
# pysheds lee del disco. Se guarda el quemado, NO el acondicionado: ese es
# float64 y el .tif float32 le borra la micro-pendiente. Para flujo, en la
# sesión que lo calcule:
#     grid, dem = geo.acondicionar_mde(MDE_HIDRO)
#     flujo = geo.acumulacion_flujo(grid, dem)
geo.guardar_raster(mde_quemado, MDE_HIDRO, transform, EPSG_UTM)

print(f"\nPíxeles de cauce quemados: {n_px:,}")
if VALIDAR:
    print(
        f"RMS del hold-out: {metricas['rms']:.2f} m ({metricas['n_puntos_prueba']} pts)"
    )
print(f"-> {MDE_BASE} (terreno, insumo de pendientes)")
print(f"-> {MDE_HIDRO} (quemado; acondicionar_mde en la sesión del flujo)")
