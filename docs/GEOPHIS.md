# geophis: documentación de referencia

**Versión:** 1.3.0 | **Paquete:** `geophis` (Python >= 3.11)

Wrapper en español sobre geopandas/shapely (vector), rasterio (raster) y pyproj (proyecciones), con scipy para el análisis espacial. Una función clara por operación SIG. Distancias en unidades del CRS (metros si es UTM). EPSG de trabajo típico: **32613** (UTM 13N).

```python
import geophis as geo
```

Este archivo basta para escribir un script correcto sin leer el código fuente: instrucciones de trabajo, reglas, recetas y la API con la trampa de cada función. **Las mediciones que justifican cada regla y las alternativas descartadas están en `DECISIONES.md`**, que es para personas y no se sube al Proyecto.

Las reglas se citan como **R1** a **R9** y las recetas de pendiente como **P1** a **P3**.

## Cómo responder

1. **La respuesta es el código.** Sin preámbulo, sin anunciar lo que vas a hacer, sin resumen al final. Después del script, tres líneas como máximo: qué se decidió, qué queda sin medir o supuesto, qué comando correr.
2. **Lo declarado que el pedido no dice, se pregunta o se marca.** La escala, los rangos, la clase que se cita o la temporada son decisiones del usuario (R1). Si faltan y cambian la cifra, pregunta; si se puede seguir, ponlas en `CONFIG` marcadas `SUPUESTO` y dilo en esas tres líneas.
3. **Pide la salida de consola entera.** Los defectos de esta librería avisan por consola y no se ven en el mapa (mesetas falsas, sin dato, `sin_medir`, el veredicto del juez). Recomienda pegarla de vuelta, dentro de las tres líneas del final.
4. **La brevedad no se relaja después.** Ante un error o un cambio: el fragmento corregido y una línea de qué cambió, sin repetir el script entero ni disculparse. Un error se responde con dónde está, qué lo causa y el arreglo, en ese orden.
5. La excepción es que el usuario pida una explicación, un recorrido o una auditoría: eso se entrega completo.

## Vocabulario

Los símbolos que usan las reglas, las recetas y la consola. La columna de la derecha dice qué función lo produce.

| Término | Qué es | De dónde sale |
|---|---|---|
| carta | la cartografía de curvas de nivel de entrada; el juez externo del MDE | el archivo de curvas |
| `e` | equidistancia: diferencia de cota entre curvas contiguas | `detectar_cota` |
| λ (`lambda_m`) | separación horizontal media entre curvas; subestima los llanos | `separacion_media` |
| zigzag | largo real del trazo de curva entre su largo efectivo; pasa de intervalo de terreno a intervalo de trazo | `separacion_media` |
| `h` (`resolucion`) | lado de celda del MDE, en metros | `derivar_parametros` (`resolucion_regla`) |
| UMM (`umm_m2`) | unidad mínima de mapeo: `(2 mm · escala)²`; a 1:25 000 son 2 500 m² | consecuencia de `ESCALA` |
| `min_pixeles` | la UMM en celdas: lo que el sieve absorbe | `min_pixeles_umm` |
| sieve | limpieza que pasa cada mancha menor a `min_pixeles` a su vecino mayor | `limpiar_moteado`, dentro de `zonificar` |
| clase alta | el rango más alto, el que se cita como cifra (p. ej. `>100`); casi siempre una cinta de escarpe estrecha | `RANGOS`, declarado |
| `w` (`ancho_w`) | ancho medido de la banda de clase alta en la carta | `ancho_banda` |
| blanco de la carta (`blanco_carta_ha`) | superficie de la clase alta según las curvas; contra ella se compara el MDE | `ancho_banda` |
| techo de trazado, `factor_techo` | `w/3`, la `h` más gruesa que aún traza la banda; `factor_techo` = `h` del spline / ese techo | `resolucion_regla` |
| `s_vacio` | separación del hueco más grande entre curvas (`2·q90`); fija el margen de borde | `separaciones` |
| rizo, `A` (`amplitud_rizo`) | ondulación del spline entre curvas, que sale como pendiente falsa; `A` es su amplitud en m | `amplitud_rizo`, sobre el MDE ya hecho |
| terraceo, mesetas falsas | escalones planos a la cota de cada curva (spline con pocos `vecinos`, triángulos del TIN) | `histograma_fase`, `planos_de_curva` |
| `vecinos`, rodilla | puntos que usa el spline por celda; la rodilla es el `n` desde el cual la clase alta deja de moverse | `barrer_vecinos` |
| `s_sostenida`, `piso_local` | separación entre curvas que la carta sostiene dentro de una clase, y su mitad; un corte se entrega si `piso_local <= h <= s_sostenida` | `sostenimiento_por_clase` |
| TIN, spline, malla | los tres métodos de `interpolar_mde`; qué da cada uno está en "Pendiente: elegir la receta" | `metodo=` |
| quiebres | líneas sin cota (cauces) que el TIN o la malla respetan como arista | `interpolar_mde(quiebres=)` |
| procedencia | la fuente de cada número: `declarado`, `medido`, `derivado`, `convencion` o `default` (R1) | `tabla_procedencia` |

## Las reglas que no se negocian

**R1. No se inventa ningún número.** Un número medible no se teclea nunca, ni "para tantear": se mide (`derivar_parametros`) o se recibe medido. Si hay que teclearlo, al lado va de dónde sale, qué procedimiento lo produce y qué pasa si está mal. Inmediatamente después de cada `derivar_parametros` van estas dos líneas, una vez por diccionario, y no se repiten al final del script:

```python
geo.tabla_procedencia(p)
assert not geo.sin_medir(p), f"nadie eligio: {geo.sin_medir(p)}"
```

| fuente | ¿lo escribe el script? | ejemplos |
|---|---|---|
| `declarado` | **Sí**, con su razón al lado: lo decide el ingeniero | escala, `RANGOS`, `CLASE_ALTA`, que esa clase va exenta del sieve, `TOL` |
| `medido` | **No.** Se mide o se recibe medido | `w`, λ, zigzag, `s_vacio`, `vecinos`, `A` |
| `derivado` | **Nunca.** Es aritmética de la librería | UMM, `min_pixeles`, `margen_borde_celdas`, intervalos, `h` |
| `convencion` | No hace falta: constante de oficio | `LADO_MM`=2, `N_MIN`=4, `N_CRUCE`=3, `CUANTIL_VACIO`=90 |
| `default` | **Nunca a sabiendas.** Nadie lo eligió y sale en `sin_medir` | `vecinos`=48 |

`ESCALA = 25_000` no dice nada; `ESCALA = 25_000  # compromiso de entrega; la UMM es consecuencia` sí. Cuando una cantidad medible se teclea de todos modos, va con su ficha de tres partes (el caso real es `vecinos`):

```python
# vecinos = 90
#   COMO SE OBTIENE: `barrer_vecinos` (o `derivar_parametros(vecinos="medir")`):
#     el primer n cuya clase alta se mueve <= 1 % al pasar al siguiente.
#   COSTO: una interpolacion por cada n hasta converger.
#   SI ESTA MAL: con el default 48, en un predio real sobraba el 12 % de la
#     clase alta, y era terraceo. No avisa.
```

Con varias llamadas a `derivar_parametros` sobre el mismo predio (otro método, la segunda pasada con `A`), pasa `medidos=p` de la primera: no vuelve a medir y conserva la procedencia. No pases `int(...)`/`float(...)` de un parámetro: el cast borra la procedencia.

**R2. El área y la longitud van detrás del ÚLTIMO paso que mueva la geometría.** No solo detrás de recortar y reproyectar: `borrar`, `resolver_por_prioridad`, `simplificar`, `cerrar_microhuecos`, `rellenar_huecos`, `subdividir_por_area`, `eliminar_menores`, `identidad`, `union` y `partir_por_linea` también la mueven. `guardar` avisa si quedó obsoleta.

**R3. El CRS se alinea solo en cuatro funciones, y siempre moviendo el SEGUNDO argumento.** `recortar`, `intersecar`, `borrar` y `union_espacial` reproyectan la máscara al CRS de la capa; no pongas `reproyectar` antes. `fusionar` NO alinea (falla). `quemar_cauces` recibe `transform` y no CRS: reproyecta tú los cauces. Nunca pases `capa.crs.to_epsg()` como destino (da `None` sin código EPSG): pasa `capa.crs`. Las funciones que miden en metros sin decirlo (`superficie_total_ha`, `ancho_banda`, `cercano`, `interpolar_mde`...) fallan con CRS en grados o pies.

**R4. `bbox=` es seguro para lo que trabaja geometría por geometría, no para lo que infiere una propiedad de la CAPA.** Declara un dominio (`crear_cuadro(predio, margen=...)`), carga con `bbox=` de ese dominio, detecta `campo` y equidistancia UNA vez sobre él (`detectar_cota`, `detectar_campo` leen la columna entera) y no los repitas sobre capas recortadas. El dominio tiene que contener al `cuadro_recorte`: compruébalo con un `assert`.

**R5. Para contar o medir PIEZAS después de un disolver, `separar_multipartes` primero.** `disolver_por_grupo` deja una fila por clase y todo lo que razona por fila mide el grupo, no la mancha.

**R6. El MDE de pendiente y el de hidrología son distintos.** Guarda el MDE base ANTES de quemar. `quemar_cauces` y `acondicionar_mde` son correctos para flujo y veneno para pendiente. Rellena el NoData ANTES de quemar (si no, el relleno copia el vecino sin quemar y deja una presa en el cauce). El ráster de `acondicionar_mde` y `rellenar_sumideros` es float64: úsalo en la sesión, no lo guardes a .tif (float32 borra su micro-pendiente).

**R7. Un script no importa `math` ni `numpy`.** Si lo necesita, falta una función en la librería (`min_pixeles_umm`, `margen_borde_celdas`, `umbral_celdas`, `tolerancia_rasterizacion_ha`, `calcular_pendiente(invalidar=)`). Excepción: medir algo que la librería no mide todavía.

**R8. El sin dato no se tapa.** `interpolar_mde` no extrapola: fuera de las curvas devuelve NaN y avisa con las ha. Para pendiente no lo rellenes (`calcular_pendiente` recibe el NaN); la respuesta es dar más curvas. `zonificar` lo entrega como clase `SIN DATO`. Una máscara que luego se recorta en vector va con `rasterizar(..., todo_tocado=True)`.

**R9. Las guardas miran la SALIDA y dicen la cifra.** `comprobar_particion([capa], predio, tolerancia_ha=TOL)` al final de toda capa que deba partir el predio. `TOL` no tiene default ni se hereda de otro predio: es `declarado` con su razón, o se deriva con `tolerancia_rasterizacion_ha` si la capa salió de un ráster.

## Estilo de script

- Docstring corto al inicio: qué, y por qué si no es obvio.
- Bloque `CONFIG` en mayúsculas arriba (rutas, EPSG, distancias, campos), cada valor declarado con su razón al lado.
- Toda ruta es una constante del `CONFIG` que dice si se lee o se escribe. Entrada: `ENTRADA` en el nombre (`ENTRADA_PREDIO`); en una lista de tuplas, la variable del `for` se llama `entrada`. Salida: `SALIDA` en el nombre, `SALIDA_BUFFER` para un archivo, `SALIDAS` para la carpeta (`SALIDAS + r"\PENDIENTES.shp"`). Nunca una ruta escrita dentro de `cargar` o `guardar`: el usuario distingue de un vistazo qué lee y qué escribe.
- Pasos numerados `# ── N. VERBO EN MAYÚSCULAS ──`, lineal, sin funciones propias, reasignando la misma variable (`gdf = geo.algo(gdf)`).
- Tres o más casos parecidos → lista de tuplas y `for` (el orden puede codificar prioridad). Con dos, escribe los bloques directo.
- `print()` como reporte. `geo.MOSTRAR = False` calla los reportes de la librería; los avisos de defecto salen igual.
- `geo.NUCLEOS` (default: núcleos menos uno, prioridad baja) es cuántos procesos usan los barridos. Con una malla chica van solos en serie; `geo.NUCLEOS = 1` los fuerza en serie.
- Variables en español y cortas (`gdf`, `capas`, `combinado`); constantes en mayúsculas. Antes de una corrida larga, `comprobar_nombres_shp(gdf)`: el `.shp` corta los nombres a 10 caracteres.

```python
"""Buffer jerarquico de caminos."""
import geophis as geo

EPSG_UTM = 32613
SALIDA_BUFFER = r"C:\SIG\proyecto\salidas\CAMINOS_BUFFER.shp"
NIVELES = [  # el orden es la prioridad
    ("PRIMARIO", r"C:\SIG\proyecto\datos\camino_prim.shp", 5.0),
    ("SECUNDARIO", r"C:\SIG\proyecto\datos\camino_sec.shp", 3.0),
    ("SACA", r"C:\SIG\proyecto\datos\camino_saca.shp", 1.75),
]

capas = []
for nombre, entrada, dist in NIVELES:
    gdf = geo.reproyectar(geo.cargar(entrada), EPSG_UTM, nombre)
    gdf = geo.disolver(gdf, campo="camino", valor=nombre)
    capas.append(geo.zona_de_influencia(gdf, dist))

combinado = geo.resolver_por_prioridad(capas)
geo.guardar(combinado, SALIDA_BUFFER)
```

## Receta vectorial: capa, tabla y superficie

El patrón de casi todo trabajo vectorial: revisar la capa de fuera, alinearla, recortarla, unirle su tabla, medir al final y comprobar la salida antes de guardar.

```python
"""Superficie por rodal con los datos del inventario, que vienen en tabla aparte."""
import geophis as geo

# ── CONFIG ─────────────────────────────────────────────────────────────
ENTRADA_PREDIO = r"C:\SIG\proyecto\datos\predio.shp"
ENTRADA_RODALES = r"C:\SIG\proyecto\datos\rodales.shp"
ENTRADA_INVENTARIO = r"C:\SIG\proyecto\datos\inventario.xlsx"  # una fila por rodal
SALIDA_RODALES = r"C:\SIG\proyecto\salidas\RODALES.shp"
SALIDA_TABLA = r"C:\SIG\proyecto\salidas\RODALES.xlsx"
EPSG_UTM = 32613  # el huso del predio
CLAVE = ("RODAL", "rodal")  # columna de la capa, columna de la tabla
CAMPO_RESUMEN = "ESPECIE"  # de la tabla: por esto se suma la superficie
TOL = 0.01  # los rodales parten el predio: el residuo legitimo es reparacion de geometria

# ── 1. CARGAR Y REVISAR ANTES DE NADA ──────────────────────────────────
predio = geo.reproyectar(geo.cargar(ENTRADA_PREDIO), EPSG_UTM, "predio")
rodales = geo.cargar(ENTRADA_RODALES)
geo.describir(rodales)  # imprime CRS, vacias, invalidas
rodales = geo.reparar_geometrias(geo.reproyectar(rodales, EPSG_UTM, "rodales"))

# ── 2. RECORTAR AL PREDIO ──────────────────────────────────────────────
rodales = geo.recortar(rodales, predio)

# ── 3. UNIR EL INVENTARIO ──────────────────────────────────────────────
# si falla por tipo ('007' contra 7): cambiar_tipo del lado entero a 'texto'
rodales = geo.unir_campo(rodales, ENTRADA_INVENTARIO, CLAVE)
rodales = geo.eliminar_campos(rodales, [CLAVE[1]])  # la clave de la tabla repite la de la capa

# ── 4. SUPERFICIE AL FINAL (R2) Y GUARDAS SOBRE LA SALIDA (R9) ─────────
rodales = geo.calcular_superficie(rodales, campo="SUP")
geo.comprobar_particion([rodales], predio, tolerancia_ha=TOL)
assert not geo.comprobar_nombres_shp(rodales), geo.comprobar_nombres_shp(rodales)
geo.guardar(rodales, SALIDA_RODALES)
geo.exportar_tabla(rodales, SALIDA_TABLA, hoja="RODALES")
print(geo.estadisticas_de_resumen(rodales, por=CAMPO_RESUMEN, campos={"SUP": "sum"}))
```

Si la capa no parte el predio (puntos, líneas, polígonos sueltos), `comprobar_particion` no aplica: la guarda es el conteo de filas antes y después de `unir_campo`.

## Pendiente: elegir la receta

**Lee esto primero.** La cadena ingenua (MDE → pendiente → reclasificar) corre, no avisa de nada, y da una clase alta que puede estar tres veces abajo de la buena (medido: 14.86 ha con el método completo, 4.35 ha sin él). Qué receta usar depende de qué se cita:

| Lo que se entrega | Receta |
|---|---|
| Tabla de rangos de pendiente, sin citar la clase más alta como cifra | **P1** |
| La clase más alta: su superficie o su capa (p. ej. "lo de más de 100 %") | **P2** |
| La tabla completa **y** la clase alta citada, o la exposición | **P2** y después **P3** |

Tres señales de la consola deciden dentro de las recetas, y las tres salen de `tabla_procedencia`:

| Señal | Qué significa | Qué se hace |
|---|---|---|
| `vecinos ... barrido SIN rodilla: la clase alta es 0 ha a esta resolucion` | El spline, a su `h`, no ve la clase alta | El spline no es candidato en P2 (el script lo saca solo) |
| `factor_techo` < 5 | El spline queda cerca de la `h` del TIN | En P3, rangos bajos del spline |
| `factor_techo` ≥ 9.5 (`FACTOR_MALLA`, aviso `FACTOR ... >= 9.5`) | El spline queda muy grueso y aplana las laderas | En P3, rangos bajos de `metodo="malla"` |

Entre 5 y 9.5 no hay mediciones: en P3 se arman las dos variantes y se comparan contra `blanco_carta_ha` (o `ancho_banda` a cada corte) antes de entregar. El umbral 9.5 sale de un solo predio real.

**Cómo se reparten los métodos**, medido en cinco predios contra la carta:

- **TIN con quiebres:** el mejor para la clase alta, porque mide la pendiente media entre dos curvas, que es lo que mide la carta. Deja mesetas falsas a cota de curva (del 5 al 19 % del extent), así que **nunca da los rangos bajos ni la exposición**.
- **Spline:** los rangos bajos y la exposición cuando `factor_techo` es bajo (−1.1 a −3.8 % en ≥60 %). A su `h` suele fallar la clase alta por ancho.
- **Malla:** los rangos bajos cuando el spline queda muy grueso (con factor 9.7: malla +7.2 % en ≥60 %, spline −40.1 %). Sobreestima ≥60 % de 7 a 8 % siempre, así que **no es fuente de la clase alta**.

### P1. Tabla de rangos con spline

Una interpolación, la clase alta exenta del sieve y una sola pasada (si se sievea todo y luego se rescata la clase alta, el sieve mete en ella píxeles de otras clases y se pierden: 43.21 ha de hueco en un predio real).

```python
"""Clases de pendiente del predio con spline (GEOPHIS.md, P1)."""
import geophis as geo

# ── CONFIG ─────────────────────────────────────────────────────────────
ENTRADA_PREDIO = r"C:\SIG\proyecto\datos\predio.shp"
ENTRADA_CURVAS = r"C:\SIG\proyecto\datos\curvas.shp"
SALIDAS = r"C:\SIG\proyecto\salidas"
EPSG_UTM = 32613  # el huso del predio
ESCALA = 25_000  # compromiso de entrega; la UMM es consecuencia
RANGOS = [("0-15", 0, 15), ("15-45", 15, 45), ("45-100", 45, 100), (">100", 100, float("inf"))]
CLASE_ALTA = ">100"  # cinta de escarpe: exenta del sieve, la UMM va como ancho
MARGEN_DOMINIO = 3000  # ventana de lectura; el paso 2 comprueba que contiene el recorte
TOL = 0.01  # el borde es el del predio: el residuo legitimo es reparacion de geometria

# ── 1. CARGAR (R4) ─────────────────────────────────────────────────────
predio = geo.reproyectar(geo.cargar(ENTRADA_PREDIO), EPSG_UTM, "predio")
dominio = geo.crear_cuadro(predio, margen=MARGEN_DOMINIO)
curvas = geo.reproyectar(geo.recortar(geo.cargar(ENTRADA_CURVAS, bbox=dominio), dominio), EPSG_UTM, "curvas")
campo, e = geo.detectar_cota(curvas)

# ── 2. PARAMETROS (R1) ─────────────────────────────────────────────────
p = geo.derivar_parametros(curvas, campo, predio, RANGOS, ESCALA, vecinos="medir")
geo.tabla_procedencia(p)
assert not geo.sin_medir(p), f"nadie eligio: {geo.sin_medir(p)}"
h = p["resolucion"]  # float con procedencia: se pasa tal cual
cuadro_recorte, cuadro_salida = geo.cuadros_mde(predio, margen_salida=2 * float(h), parametros=p)  # Horn deja NaN el borde
assert cuadro_recorte.union_all().within(dominio.union_all()), "sube MARGEN_DOMINIO"

# ── 3. EL MDE ──────────────────────────────────────────────────────────
mde, tf = geo.interpolar_mde(geo.recortar(curvas, cuadro_recorte), campo, cuadro=cuadro_salida, parametros=p)
geo.guardar_raster(mde, SALIDAS + r"\MDE_BASE.tif", tf, EPSG_UTM)

# ── 4. `A`, QUE SOLO SE MIDE SOBRE EL MDE, Y LOS PARAMETROS CON ELLA ───
mascara = geo.rasterizar(predio, mde.shape, tf, todo_tocado=True) == 1
a, det = geo.amplitud_rizo(mde, tf, curvas, campo, mascara=mascara)
p = geo.derivar_parametros(
    curvas, campo, predio, RANGOS, ESCALA, vecinos=p["vecinos"], resolucion=h, amplitud_rizo=a, medidos=p,
)
geo.tabla_procedencia(p)

# ── 5. CLASES: UNA PASADA DE SIEVE, LA CLASE ALTA EXENTA ───────────────
clases = geo.zonificar(
    geo.calcular_pendiente(mde, tf), tf, predio.crs, RANGOS, p["min_pixeles"],
    recorte=predio, exentos=[CLASE_ALTA], campo="PENDIENTES", por_clase=True,
)
geo.comprobar_particion([clases], predio, tolerancia_ha=TOL)
geo.guardar(clases, SALIDAS + r"\PENDIENTES.shp")
print(geo.estadisticas_de_resumen(clases, por="PENDIENTES", campos={"SUP": "sum"}))
```

Si `sin_medir` trae `vecinos` porque el barrido salió sin rodilla, el spline no ve la clase alta a su `h`: ve a P2.

**Los jueces de la cifra**, todos externos al MDE (juzgar pendientes con un sombreado del mismo ráster es circular):

- **Qué corte se entrega:** `sostenimiento_por_clase(clases, curvas, "PENDIENTES", RANGOS, e)`: el corte más alto con `piso_local <= h <= s_sostenida`.
- **La banda frente a `h`:** `barrer_resolucion(..., parametros=p)` da la banda de incertidumbre de la cifra frente a `h`. Se declara junto a ella; no dictamina.
- **Techo por rango:** `auditar_rangos`, techo cartográfico por rango, cota floja.

La clase alta se entrega como **elemento lineal**: la UMM se aplicó como ancho y no como superficie, y la cifra es un piso defendible. Esa salvedad va pegada a la cifra.

### P2. La clase alta: el juez entre métodos

El método no se elige por receta: el juez mide cada candidata contra la carta con el criterio de SU método. Candidatas: el TIN con quiebres siempre, y el spline si ve la clase alta.

```python
"""Superficie con pendiente mayor al 100 % del predio (GEOPHIS.md, P2)."""
import geophis as geo

# ── CONFIG ─────────────────────────────────────────────────────────────
ENTRADA_PREDIO = r"C:\SIG\proyecto\datos\predio.shp"
ENTRADA_CURVAS = r"C:\SIG\proyecto\datos\curvas.shp"
ENTRADA_CAUCES = r"C:\SIG\proyecto\datos\cauces.shp"  # lineas SIN cota: los quiebres del TIN
SALIDAS = r"C:\SIG\proyecto\salidas"
EPSG_UTM = 32613  # el huso del predio
ESCALA = 25_000  # compromiso de entrega; la UMM es consecuencia
RANGOS = [("0-100", 0, 100), (">100", 100, float("inf"))]  # en P3, todos los rangos
CLASE_ALTA = ">100"  # cinta de escarpe: exenta del sieve
MARGEN_DOMINIO = 3000  # ventana de lectura; los pasos 3 y 4 comprueban que contiene el recorte
TOL = 0.01  # el borde es el del predio: el residuo legitimo es reparacion de geometria

# ── 1. CARGAR (R4) ─────────────────────────────────────────────────────
predio = geo.reproyectar(geo.cargar(ENTRADA_PREDIO), EPSG_UTM, "predio")
dominio = geo.crear_cuadro(predio, margen=MARGEN_DOMINIO)
curvas = geo.reproyectar(geo.recortar(geo.cargar(ENTRADA_CURVAS, bbox=dominio), dominio), EPSG_UTM, "curvas")
cauces = geo.recortar(geo.cargar(ENTRADA_CAUCES, bbox=dominio), dominio)
campo, e = geo.detectar_cota(curvas)

# ── 2. PARAMETROS, UNO POR METODO ──────────────────────────────────────
p_tin = geo.derivar_parametros(curvas, campo, predio, RANGOS, ESCALA, metodo="tin")
geo.tabla_procedencia(p_tin)
assert not geo.sin_medir(p_tin), f"nadie eligio: {geo.sin_medir(p_tin)}"
p_spl = geo.derivar_parametros(curvas, campo, predio, RANGOS, ESCALA, vecinos="medir", medidos=p_tin)
geo.tabla_procedencia(p_spl)
spline_candidato = not geo.sin_medir(p_spl)  # sin rodilla = no ve la clase alta a su h
print(f"spline candidato para {CLASE_ALTA}: {spline_candidato}")

# ── 3. TIN CON LINEAS DE QUIEBRE ───────────────────────────────────────
h_tin = p_tin["resolucion"]
rec_t, sal_t = geo.cuadros_mde(predio, margen_salida=2 * float(h_tin), parametros=p_tin)
assert rec_t.union_all().within(dominio.union_all()), "sube MARGEN_DOMINIO"
cauces_t = geo.reproyectar(geo.recortar(cauces, rec_t), curvas.crs, "cauces")  # quiebres exige el CRS de las curvas
mde_t, tf_t = geo.interpolar_mde(geo.recortar(curvas, rec_t), campo, cuadro=sal_t, quiebres=cauces_t, parametros=p_tin)
geo.guardar_raster(mde_t, SALIDAS + r"\MDE_TIN.tif", tf_t, EPSG_UTM)
clases_tin = geo.zonificar(
    geo.calcular_pendiente(mde_t, tf_t), tf_t, predio.crs, RANGOS, p_tin["min_pixeles"],
    recorte=predio, exentos=[CLASE_ALTA], campo="PENDIENTES", por_clase=True,
)
geo.comprobar_particion([clases_tin], predio, tolerancia_ha=TOL)
candidatas = {"TIN": (clases_tin, "tin", float(h_tin))}

# ── 4. SPLINE, SOLO SI ES CANDIDATO (pasos 3 a 5 de P1) ────────────────
if spline_candidato:
    h_spl = p_spl["resolucion"]
    rec_s, sal_s = geo.cuadros_mde(predio, margen_salida=2 * float(h_spl), parametros=p_spl)
    assert rec_s.union_all().within(dominio.union_all()), "sube MARGEN_DOMINIO"
    mde_s, tf_s = geo.interpolar_mde(geo.recortar(curvas, rec_s), campo, cuadro=sal_s, parametros=p_spl)
    mascara_s = geo.rasterizar(predio, mde_s.shape, tf_s, todo_tocado=True) == 1
    a, det = geo.amplitud_rizo(mde_s, tf_s, curvas, campo, mascara=mascara_s)
    p_spl = geo.derivar_parametros(
        curvas, campo, predio, RANGOS, ESCALA, vecinos=p_spl["vecinos"], resolucion=h_spl,
        amplitud_rizo=a, medidos=p_spl,
    )
    clases_spl = geo.zonificar(
        geo.calcular_pendiente(mde_s, tf_s), tf_s, predio.crs, RANGOS, p_spl["min_pixeles"],
        recorte=predio, exentos=[CLASE_ALTA], campo="PENDIENTES", por_clase=True,
    )
    geo.comprobar_particion([clases_spl], predio, tolerancia_ha=TOL)
    candidatas["SPLINE"] = (clases_spl, "spline", float(h_spl))

# ── 5. EL JUEZ, CON EL CRITERIO DE CADA METODO ─────────────────────────
filas, recomendada = geo.juzgar_clasificaciones(candidatas, curvas, campo, predio, RANGOS, e, p_tin["zigzag"])
assert recomendada, "ninguna sostiene la clase alta: se entrega el corte que diga la tabla del juez"

# ── 6. LA CLASE ALTA, EN PIEZAS (R5), A SHAPEFILE ──────────────────────
alta = geo.seleccionar_por_atributo(candidatas[recomendada][0], f"PENDIENTES == '{CLASE_ALTA}'")
alta = geo.separar_multipartes(alta)
alta = geo.agregar_campo(geo.conservar_campos(alta, ["PENDIENTES"]), "METODO", recomendada)
alta = geo.calcular_superficie(alta, campo="SUP")
geo.guardar(alta, SALIDAS + r"\PENDIENTE_MAYOR_100.shp")
print(f"{CLASE_ALTA} ({recomendada}): {alta['SUP'].sum():.2f} ha en {len(alta)} piezas")
print(f"blanco de la carta: {float(p_tin['blanco_carta_ha']):.2f} ha")
```

**Cómo se lee la tabla del juez:**

1. `NO` por ancho (`h > s_sostenida`): el objeto no cabe en el píxel; su `corte` dice qué corte sí se entrega.
2. `CONDICIONAL`: spline que falla solo por alias. Entregable si `A` está medida y su espuria no alcanza la clase; lo decide quien firma.
3. Entre las que pasan gana la que ve más escarpe en LARGO (`pct_visto`), no la de más área: el área premia al que dibuja la cinta más ancha.
4. Si pasan dos, la clase alta se cita como **banda** entre ellas, con la salvedad de elemento lineal.

La hidrografía que entró como quiebre no puede juzgar la red de drenaje de ese MDE: se le parece por construcción. Para hidrología con este MDE, `acondicionar_mde` y `rellenar_sumideros`, sin quemar (los quiebres sustituyen al quemado).

### P3. Tabla completa con la clase alta citada

Continúa P2 cuando recomendó el TIN y hacen falta los demás rangos o la exposición. Los rangos bajos no pueden salir del TIN (mesetas falsas al 0 % y sin orientación, medido: 535 ha en un predio real): salen del spline o de la malla según `factor_techo`, **sin sieve**. Sievear los rangos bajos infla ≥60 % entre 17 y 45 % contra la carta, de cualquier forma que se reparta la mancha; las manchas chicas son terraceo que compensa lo que la superficie suave se empina.

```python
# ── CONFIG (además de la de P2, con RANGOS completos) ───────────────────
# vecinos del spline de los rangos bajos = 90
#   COMO SE OBTIENE: aqui el spline no da la clase alta, asi que la rodilla
#     de `vecinos="medir"` (que se mide sobre la clase alta) no aplica.
#   SI ESTA MAL: con la rodilla medida (64) la exposicion saca 3.52 ha SIN
#     ORIENTACION en un predio real que con 90 no aparecen.
VECINOS_SPLINE = 90

# ── 7. LOS RANGOS BAJOS: SPLINE, O MALLA SI EL SPLINE QUEDA MUY GRUESO ─
assert recomendada == "TIN", "si gano el spline, toda la tabla sale de P1"
p_bajos = geo.derivar_parametros(curvas, campo, predio, RANGOS, ESCALA, vecinos=VECINOS_SPLINE, medidos=p_tin)
if float(p_bajos["factor_techo"]) >= geo.parametros.FACTOR_MALLA:
    p_bajos = geo.derivar_parametros(curvas, campo, predio, RANGOS, ESCALA, metodo="malla", medidos=p_tin)
geo.tabla_procedencia(p_bajos)
assert not geo.sin_medir(p_bajos), f"nadie eligio: {geo.sin_medir(p_bajos)}"
rec_b, sal_b = geo.cuadros_mde(predio, margen_salida=2 * float(p_bajos["resolucion"]), parametros=p_bajos)
assert rec_b.union_all().within(dominio.union_all()), "sube MARGEN_DOMINIO"
mde_b, tf_b = geo.interpolar_mde(
    geo.recortar(curvas, rec_b), campo, cuadro=sal_b, parametros=p_bajos,
    quiebres=geo.reproyectar(geo.recortar(cauces, rec_b), curvas.crs, "cauces") if p_bajos["metodo"] == "malla" else None,
)
mascara_b = geo.rasterizar(predio, mde_b.shape, tf_b, todo_tocado=True) == 1

# ── 8. EXPOSICION, DE LA MISMA SUPERFICIE ──────────────────────────────
orientacion = geo.calcular_orientacion(mde_b, tf_b) + 1  # +1: el 0 (SIN ORIENTACION) no se perderia al poligonizar
expo = geo.poligonizar(orientacion, tf_b, predio.crs, mascara=mascara_b, campo="gridcode")

# ── 9. RANGOS: CASCADA DE TRES SUPERFICIES ─────────────────────────────
# gana la clase alta del TIN; luego los rangos bajos; lo que sobre (los bajos
# ven clase alta y el TIN no) toma la clase del TIN
cod_b, etq_b = geo.reclasificar_rangos(geo.calcular_pendiente(mde_b, tf_b), RANGOS)
cls_b = geo.poligonizar(cod_b, tf_b, predio.crs, mascara=mascara_b & (cod_b > 0), campo="gridcode")
cls_b["PENDIENTES"] = cls_b["gridcode"].map(etq_b)
fuentes = [
    ("TIN", geo.seleccionar_por_atributo(clases_tin, f"PENDIENTES == '{CLASE_ALTA}'")),
    ("BAJOS", geo.seleccionar_por_atributo(cls_b, f"PENDIENTES != '{CLASE_ALTA}'")),
    ("TIN RESIDUO", clases_tin),
]
rangos = geo.resolver_por_prioridad(
    [geo.agregar_campo(geo.conservar_campos(g, ["gridcode", "PENDIENTES"]), "ORIGEN", o) for o, g in fuentes]
)
rangos = geo.calcular_superficie(geo.recortar(rangos, predio), campo="SUP")
print(geo.estadisticas_de_resumen(rangos, por=["ORIGEN"], campos={"SUP": "sum"}))  # va a la memoria
rangos = geo.disolver_por_grupo(rangos, campo="gridcode")
rangos = geo.calcular_superficie(geo.conservar_campos(rangos, ["gridcode", "PENDIENTES"]), campo="SUP")
geo.comprobar_particion([rangos], predio, tolerancia_ha=TOL)
geo.guardar(rangos, SALIDAS + r"\PENDIENTES.shp")
```

**La salvedad va pegada a la tabla:** son dos superficies y a dos resoluciones, la clase alta del TIN y el resto del spline o de la malla; los rangos bajos quedan con piezas menores a la UMM (miles, según el predio). El `SIN DATO` del TIN entra por la fila `TIN RESIDUO` y la partición cierra igual.

**La malla, lo que va con la cifra:** sobreestima ≥60 % de 7 a 8 %; pide más relleno de sumideros que el TIN (para hidrología, el TIN con quiebres); un solo proceso, unos 860 B por celda (a 5 m: 1.9 M celdas, 73 s, 1.6 GB). `juzgar_clasificaciones` la acepta con el criterio del TIN, que es un supuesto: un SÍ del juez no la vuelve fuente de la clase alta.

## MDE e hidrología

El MDE de pendiente es el de P1 o P2 y se guarda ANTES de nada más (R6). La rama hidrológica parte de él y el orden tiene dos trampas silenciosas:

1. **Rellena ANTES de quemar** (R6). Si no vas a quemar, no rellenes: `acondicionar_mde` lo hace solo.
2. **El `umbral` va en CELDAS y no es portable**: a `h` m/px, `k` celdas son `k·h²/10000` ha. Se declara en superficie y se deriva con `umbral_celdas`.

```python
# mde, tf, p, predio, curvas, cauces y SALIDAS: los de P1 (o P2 con el MDE del TIN)
h = tf.a  # lado de celda de ESTE MDE, del transform y no de una constante

# ── 1. QUEMAR (opcional; con el TIN con quiebres no se quema) ──────────
cauces = geo.reproyectar(cauces, curvas.crs, "cauces")  # R3: quemar_cauces no alinea
mde_h = geo.rellenar_nodata(mde)  # ANTES de quemar
mde_h, n = geo.quemar_cauces(mde_h, geo.disolver(cauces), tf, profundidad=5)
if n == 0:
    raise ValueError("ningun pixel quemado: revisa el CRS de la hidrologia")

# ── 2. ACONDICIONAR: pysheds lee de disco ──────────────────────────────
geo.guardar_raster(mde_h, SALIDAS + r"\MDE_HIDRO.tif", tf, curvas.crs)
grid, dem = geo.acondicionar_mde(SALIDAS + r"\MDE_HIDRO.tif")  # float64: no se guarda (R6)

# ── 3. FLUJO ───────────────────────────────────────────────────────────
flujo = geo.acumulacion_flujo(grid, dem)

# ── 4. ENTREGABLES: un solo umbral para los tres ───────────────────────
AREA_APORTADORA_HA = 1.25  # declarado: lo que hace que una vaguada sea cauce
UMBRAL = geo.umbral_celdas(AREA_APORTADORA_HA, h)
red = geo.red_drenaje(flujo, UMBRAL)
orden = geo.orden_cauces(flujo, UMBRAL)
cuenca = geo.delimitar_cuenca(flujo, geo.disolver(predio), UMBRAL)  # UNA geometria: "el desague de esto"
micro = geo.microcuencas(flujo, predio, UMBRAL)  # si el predio drena por varios sitios
micro = geo.calcular_superficie(micro, campo="SUP")  # SUP: el .shp corta a 10 caracteres
TOL = geo.tolerancia_rasterizacion_ha(geo.longitud_total_m(predio), h)  # derivada, no tecleada
geo.comprobar_particion([micro], predio, tolerancia_ha=TOL)  # NO recortes antes: empeora el cierre
geo.guardar(red, SALIDAS + r"\RED_DRENAJE.shp")
geo.guardar_raster(orden, SALIDAS + r"\ORDEN.tif", tf, curvas.crs)
```

Qué mirar en la consola:

- `acondicionar_mde: N ha sin dato parcheadas`: si no es 0 y no vienes de quemar, es superficie inventada (R8).
- `N celda(s) INTERIOR(es) sin salida de flujo`: el flujo muere dentro y lo que drena ahí desaparece de la red. Mira la fracción; si son de una celda y milímetros, `rellenar_sumideros` y su `informe` viaja con la cifra.
- `orden_cauces: N celdas numeradas de M en la mascara`: si M es mucho mayor que N, el umbral marca ladera baja.
- `delimitar_cuenca: desague movido X m`: la cifra clave; X grande es coordenada mala o umbral demasiado alto.

## Clasificación de superficies (capas que se pisan)

Con jerarquía manda el orden (`resolver_por_prioridad`); sin jerarquía, el núcleo más cercano (`repartir_por_cercania`). **Primero resta, luego reparte**: a las capas sin jerarquía quítales antes lo que se llevan las superiores, o las ha del reparto no son las del shapefile. El polígono general entra al final como relleno y la suma cuadra por construcción.

```python
"""Clasificacion de superficies del predio: tres capas con jerarquia y dos sin ella."""
import geophis as geo

# ── CONFIG ─────────────────────────────────────────────────────────────
ENTRADA_PREDIO = r"C:\SIG\proyecto\datos\predio.shp"
ENTRADA_PRODUCCION = r"C:\SIG\proyecto\datos\produccion.shp"
ENTRADA_RESTAURACION = r"C:\SIG\proyecto\datos\restauracion.shp"
SUPERIORES = [  # el orden es la prioridad: la primera gana donde se pisan
    ("CAMINOS", r"C:\SIG\proyecto\salidas\CAMINOS_BUFFER.shp"),
    ("HIDROLOGIA", r"C:\SIG\proyecto\salidas\CAUCES_BUFFER.shp"),
    ("PENDIENTE >100", r"C:\SIG\proyecto\salidas\PENDIENTE_MAYOR_100.shp"),
]
SALIDA_CLASES = r"C:\SIG\proyecto\salidas\CLASIFICACION.shp"
SALIDA_CONFLICTOS = r"C:\SIG\proyecto\salidas\CONFLICTOS.shp"
EPSG_UTM = 32613  # el huso del predio
RESOLUCION_REPARTO = 1.0  # el borde del reparto queda al metro; mas fino cuesta al cuadrado
TOL = 0.01  # el borde es el del predio: el residuo legitimo es reparacion de geometria

# ── 1. CARGAR: TODO AL MISMO CRS (fusionar no alinea, R3) ──────────────
predio = geo.reproyectar(geo.cargar(ENTRADA_PREDIO), EPSG_UTM, "predio")
capas = []
for clase, entrada in SUPERIORES:
    gdf = geo.recortar(geo.reproyectar(geo.cargar(entrada), EPSG_UTM, clase), predio)
    capas.append(geo.disolver(gdf, campo="CLASS_SUP", valor=clase))

# ── 2. PRIMERO RESTA: LO QUE SE LLEVAN LAS SUPERIORES ──────────────────
superiores = geo.disolver(geo.fusionar(capas))
produccion = geo.recortar(geo.reproyectar(geo.cargar(ENTRADA_PRODUCCION), EPSG_UTM, "produccion"), predio)
produccion = geo.disolver(geo.borrar(produccion, superiores), campo="CLASS_SUP", valor="PRODUCCION")
restauracion = geo.recortar(geo.reproyectar(geo.cargar(ENTRADA_RESTAURACION), EPSG_UTM, "restauracion"), predio)
restauracion = geo.disolver(geo.borrar(restauracion, superiores), campo="CLASS_SUP", valor="RESTAURACION")

# ── 3. LUEGO REPARTE: EL SOLAPE VA AL NUCLEO MAS CERCANO ───────────────
produccion, restauracion, conflicto = geo.repartir_por_cercania(
    produccion, restauracion, resolucion=RESOLUCION_REPARTO, nombres=("PRODUCCION", "RESTAURACION")
)
print(f"solape repartido: {geo.superficie_total_ha(conflicto):.2f} ha")
if len(conflicto):
    geo.guardar(conflicto, SALIDA_CONFLICTOS)  # revisar el reparto a ojo

# ── 4. COMBINAR: EL PREDIO ENTRA AL FINAL COMO RELLENO ─────────────────
conservacion = geo.disolver(predio, campo="CLASS_SUP", valor="CONSERVACION")
combinado = geo.resolver_por_prioridad(capas + [produccion, restauracion, conservacion])
gdf = geo.disolver_por_grupo(geo.recortar(combinado, predio), campo="CLASS_SUP")
gdf = geo.calcular_superficie(gdf, campo="SUP")  # R2: al final
geo.comprobar_particion([gdf], predio, tolerancia_ha=TOL)  # R9
geo.guardar(gdf, SALIDA_CLASES)
print(geo.estadisticas_de_resumen(gdf, por="CLASS_SUP", campos={"SUP": "sum"}))
```

Si una capa no pisa el predio, `disolver` FALLA con la unión vacía: quítala de `SUPERIORES` o del reparto y dilo en las tres líneas.

## Del predio a la imagen: Sentinel-2

El polígono del predio, en cualquier CRS, basta: sale en reflectancia 0 a 1, en el UTM del predio y recortada a él, sin cuenta ni descarga manual. **La cita es obligatoria** en todo entregable que use la imagen o algo derivado: `Contains modified Copernicus Sentinel data AAAA` (`cita_sentinel2`).

```python
# ── CONFIG ─────────────────────────────────────────────────────────────
ENTRADA_PREDIO = r"C:\SIG\proyecto\datos\predio.shp"
SALIDAS = r"C:\SIG\proyecto\salidas"
FECHAS = ("2026-02-01", "2026-05-31")  # secas en la region: decision de campo, sin default
NOMBRES = ["azul", "verde", "rojo", "nir", "swir1", "swir2"]  # el nombre viaja con la banda

# ── 1. LA IMAGEN, Y EL INFORME ANTES QUE LA IMAGEN ─────────────────────
predio = geo.cargar(ENTRADA_PREDIO)
bandas, tf, crs, res, informe, origen = geo.imagen_sentinel2(predio, fechas=FECHAS)
print(informe.to_string(index=False))
print(f"predio sin dato limpio: {100 - informe['aporta_pct'].sum():.2f} %")

# ── 2. GUARDAR, CON NOMBRES Y CON LA CITA ──────────────────────────────
geo.guardar_raster([bandas[n] for n in NOMBRES], SALIDAS + r"\S2_BANDAS.tif", tf, crs, nombres=NOMBRES)
geo.guardar_raster(origen, SALIDAS + r"\S2_ORIGEN.tif", tf, crs)  # de que fecha salio cada pixel
CITA = geo.cita_sentinel2(informe)

# ── 3. MIRAR ANTES DE CREER ────────────────────────────────────────────
ndvi = geo.indice_vegetacion("ndvi", **bandas)
geo.guardar_raster(ndvi, SALIDAS + r"\NDVI.tif", tf, crs)
print(geo.resumen_raster(ndvi, tf))
geo.vista_previa((ndvi, geo.reproyectar(predio, crs)), SALIDAS + r"\NDVI.png", transform=tf)

# ── 4. LAMINA DE REVISION (no es el mapa final) ────────────────────────
p_rgb = geo.panel_rgb(bandas, tf, crs, "Color natural · " + geo.rotulo_escenas(informe), contorno=predio)
p_ndvi = geo.panel_continuo(ndvi, tf, crs, "NDVI", paleta="vegetacion", unidad="NDVI", contorno=predio)
geo.lamina([p_rgb, p_ndvi], SALIDAS + r"\LAMINA.png", titulo="Predio · NDVI", cita=CITA)
```

**El `informe`**, una fila por fecha revisada: `limpio_pct` es lo que decide (la nubosidad de la búsqueda es de la tesela entera, no del predio); `aporta_pct` suma el % con dato; con más de una fila `usada` la imagen mezcla fechas y eso se declara con `S2_ORIGEN.tif` (rellenar con otra fecha puede tapar un desmonte o una quema).

**Antes y después de un evento.** La principal tiene que salir de la ventana del "antes"; si se estira `fechas` para tapar nubes, la fecha limpia de fuera pasa a ser la principal. El relleno de fuera va aparte, con los topes calculados de las fechas:

```python
antes = geo.buscar_sentinel2(predio, ("2026-09-01", "2026-09-25"))
fuera = geo.buscar_sentinel2(predio, ("2026-08-15", "2026-08-31"))
bandas, tf, crs, res, informe, origen = geo.imagen_sentinel2(
    predio, escenas=antes, escenas_relleno=fuera,
    max_fechas=len(antes) + len(fuera),  # el relleno entra DESPUES de todas las propias
    max_dias=41,  # 2026-09-25 menos 2026-08-15
)
```

### La lámina del antes y el después

Dos llamadas a `imagen_sentinel2` no salen en la misma malla: la segunda va con `crs=` de la primera y sus bandas se alinean antes de restar (`lamina` falla si los paneles no comparten malla).

```python
b_a, tf, crs, res, inf_a, origen_a = geo.imagen_sentinel2(predio, fechas=("2026-09-01", "2026-09-25"))
b_d, tf_d, _, _, inf_d, origen_d = geo.imagen_sentinel2(predio, fechas=("2026-09-27", "2026-10-10"), crs=crs)
b_d = dict(zip(b_d, geo.alinear_rasters((b_a["rojo"], tf, crs), *[(v, tf_d, crs) for v in b_d.values()])))
dndvi = geo.indice_vegetacion("ndvi", **b_d) - geo.indice_vegetacion("ndvi", **b_a)  # perdida en negativo
geo.guardar_raster(dndvi, SALIDAS + r"\DNDVI.tif", tf, crs)  # el dato va en .tif
contorno = geo.reproyectar(predio, crs)
p_antes = geo.panel_rgb(b_a, tf, crs, "ANTES · " + geo.rotulo_escenas(inf_a), contorno=contorno)
p_desp = geo.panel_rgb(b_d, tf, crs, "DESPUÉS · " + geo.rotulo_escenas(inf_d), contorno=contorno)
p_cambio = geo.panel_continuo(dndvi, tf, crs, "Cambio de NDVI", paleta="divergente", unidad="dNDVI", contorno=contorno)
geo.lamina([p_antes, p_desp, p_cambio], SALIDAS + r"\LAMINA.png",
           titulo="Predio · antes y después del evento", cita=geo.cita_sentinel2(inf_a, inf_d))
```

**Lámina, cuándo y qué paleta.** Una figura para revisar o para el informe es un PNG de `lamina`, además del `.tif` o el `.shp`. NDVI, NDMI o NBR → `"vegetacion"`; una diferencia → `"divergente"` (pérdida en café); pendiente, HAND o MDE → `"gris"`; clases → `panel_clases` con colores en orden de la variable. `estirado=` es un número tecleado (R1): solo si se pide una escala fija.

**Lo que NO es.** No es ortofoto: píxel de 10 m, y `swir1`/`swir2` son de 20 m reales. `scl` es una máscara de nubes pobre (confunde suelo brillante con nube, deja pasar sombras): mira la `vista_previa`. No es mediana de temporada. Del índice a polígonos: `zonificar` → `eliminar_menores` → `calcular_superficie` → `comprobar_particion`, sin `cerrar_microhuecos` ni `simplificar` (la salida de `poligonizar` no es cobertura noded). La clasificación es un primer borrador que el técnico corrige: sombra y pasto seco entran como suelo desnudo y el umbral cambia con la fecha.

## Síntoma, causa, arreglo

Cada fila es un fallo que ya pasó. La columna del medio es lo que de verdad lo causaba.

| Síntoma | Causa | Arreglo |
|---|---|---|
| `assert not sin_medir(p_spl)` falla con `vecinos` (barrido SIN rodilla) | el spline, a su `h`, ve 0 ha de clase alta | no es candidato para la clase alta: P2 lo saca solo |
| la clase alta se encoge sin explicación | `vecinos` en el default 48 (terraceo), o la clase sieveada como mancha | `vecinos="medir"` y `exentos=[CLASE_ALTA]` (P1) |
| el rango >100 % se dispara | rizo del spline: `h` cerca de λ/4 | mide `amplitud_rizo` y vuelve a derivar (P1, paso 4) |
| mucha superficie "SIN ORIENTACION", o el rango más bajo inflado | mesetas falsas a cota de curva: TIN (triángulos en una curva) o spline (`vecinos` en una curva) | TIN: rangos bajos y exposición de otra superficie (P3). Spline: acerca `intervalo_muestreo` a la separación o sube `vecinos` |
| `detectar_cota` avisa de una cota fuera de escalera | curva mal capturada: hace un pico falso | si cae dentro del cuadro de recorte, quítala (`seleccionar_por_atributo`) o corrígela |
| `interpolar_mde: sin dato: N ha` | el extent de salida se sale de la envolvente de las curvas | más curvas: sube el margen y recorta del archivo COMPLETO, o encoge `cuadro_salida` (R8) |
| `quemar_cauces` devuelve `n_pixeles = 0` | cauces en otro CRS o en otro predio | `reproyectar(cauces, crs_del_raster)` antes (R3) |
| `N celda(s) INTERIOR(es) sin salida de flujo` | sumideros del priority-flood | mira la fracción y `ES_SUMIDER`; `rellenar_sumideros` con su `informe` |
| la cuenca sale de media finca | el predio drena por dos sitios | `microcuencas` |
| `delimitar_cuenca: desague movido X m` con X grande | coordenada mala o `umbral` muy alto | corrige la coordenada o baja el umbral |
| `unir_campo` falla o el mapa sale en blanco | el tipo de la clave (`'007'` no es `7`) | convierte del lado que conserva el dato: entero a texto |
| una columna se renombró sola al guardar `.shp` | corte a 10 caracteres o colisión | `comprobar_nombres_shp` antes de la corrida larga; un nombre corto con sentido (`SUP`) |
| la capa cierra con menos superficie que el polígono | `rasterizar(todo_tocado=False)` y luego recorte vectorial | `todo_tocado=True` (R8) |
| `OJO: BrokenProcessPool` o `MemoryError` en un barrido | una malla por proceso no cabe | nada: sigue en serie con las mismas filas; `geo.NUCLEOS = 1` lo evita |
| `repartir_por_cercania` falla por el tope de 20 M píxeles | el tope mide el rectángulo envolvente del conflicto | sube `resolucion` a 2 o 5 |
| `lamina` falla con "no comparte malla" | dos `imagen_sentinel2` en mallas distintas | `crs=` de la primera y `alinear_rasters` |
| el relleno de `escenas_relleno` no sale como `usada` | `max_fechas` o `max_dias` se agotan con las propias | `max_fechas=len(escenas)+len(relleno)`, `max_dias` de las fechas |

## Lo que la librería no hace

No hace teledetección clasificada, ni geocodificación, ni se conecta con SIG de escritorio. El único servicio externo es Earth Search (Sentinel-2); OpenStreetMap se baja a mano y se lee con `cargar(ruta, capa="lines")`. No dibuja mapas finales: `lamina` arma PNG de revisión y `vista_previa` un PNG en grises. Produce capas, rásters y números con procedencia; la cartografía final se arma en el SIG.

## API pública

Plantilla de cada entrada: firma → qué devuelve. Qué hace. **TRAMPA** lo que muerde en silencio, **AVISA** lo que imprime siempre, **FALLA** cuándo levanta error, **VER** la regla o receta que la usa.

### archivo.py: E/S de capas

- `cargar(ruta, capa=None, bbox=None, crs=None)` → GeoDataFrame (.shp/.gpkg/.geojson/.gpx/.dxf/.osm/.pbf). `capa` elige en multicapa (`listar_capas`). `bbox` (capa, geometría o tupla, en cualquier CRS) lee SOLO las entidades que tocan esa ventana: no recorta, `recortar` sigue haciendo falta. Con cartografía estatal es memoria, no comodidad (33.1 s / 763 MB contra 7.1 s / 164 MB). `crs` ASIGNA el CRS cuando el archivo no lo trae (DXF nunca: `cargar("plano.dxf", crs=32613)`; la capa de CAD va en la columna `Layer`). FALLA si no hay CRS y no se pasa, o si lo trae distinto. VER: R4.
- `cargar_puntos(ruta, x="x", y="y", epsg=None, orden=None)` → puntos desde CSV/Excel. `epsg` fija el CRS (sin él no reproyecta ni mide: AVISA). `orden` ordena filas antes de convertir a línea o polígono.
- `convertir(entrada, salida, capa=None, gpx_como=None)`: `cargar` + `guardar` en una línea; autodetecta la capa GPX con datos.
- `guardar(gdf, ruta, gpx_como=None, anchos=None)`: formato por extensión; .gpx/.kml/.kmz reproyectan a EPSG:4326. **A GPX no elijas el tipo: lo decide la geometría.** Puntos → waypoints (multipuntos, uno por punto), líneas → track, polígonos → contorno como track. Los atributos viajan tal cual (`NAME` → `<name>`, el resto como extensiones): no recortes ni renombres campos. No pases `gpx_como`; solo `gpx_como="route"` y solo si el usuario pide rutas (AVISA si la capa no tiene líneas). `anchos` (solo .shp) fija el ancho del campo en el .dbf: `{"CLASE": 50}`, `{"SUP": (12, 4)}`. AVISA si una columna de área o longitud ya no cuadra con la geometría (R2). FALLA si un valor no cabe en `anchos` (el texto cuenta BYTES: una letra acentuada ocupa 2).
- `comprobar_nombres_shp(gdf)` → dict `{columna: motivo}` de las que no sobreviven a un .shp (corte a 10 caracteres o colisión); vacío = pasan. Aviso anticipado, no renombra: elige tú un nombre corto con sentido.
- `exportar_tabla(gdf, ruta, hoja="Hoja1")`: tabla sin geometría a .csv (UTF-8 con BOM) o .xlsx. En .xlsx: pestaña `hoja`, encabezado fijo, ancho por contenido, fechas DD/MM/AAAA. AVISA de área obsoleta como `guardar`.
- `cargar_waypoints(ruta)`: waypoints GPX con tabla limpia (NAME, LAYER, ELEVATION, time ISO UTC, sym).
- `cargar_tracks(ruta, utc_offset=-6)`: tracks GPX, una línea por `<trkseg>` (START_TIME y END_TIME en hora local).
- `detectar_utm(gdf)` → EPSG del huso UTM (WGS84) del centroide. FALLA si la capa cruza dos husos.
- `detectar_cota(curvas, regularidad_min=0.9)` → `(campo, equidistancia)`: la columna cuyos valores caen en escalera, con el paso más grueso que pasa `regularidad_min`. La columna tiene que traer cota en al menos esa fracción de las curvas. AVISA siempre de cotas fuera de escalera o con salto de más de 2 pasos (una curva mal capturada hace un pico falso) y de curvas sin cota. VER: R4.
- `detectar_campo(gdf, vocabulario)`: la columna cuyos valores contienen todo `vocabulario` (`{'PERENNE','INTERMITENTE'}`). VER: R4.
- `listar_capas(ruta)` → nombres de capa sin cargar ninguna, con su tipo de geometría (un GPX lista siempre cinco).
- `describir(objeto, capa=None)` → dict `n`, `tipos`, `crs`, `epsg`, `unidades`, `extension`, `campos`, `nulos`, `vacias`, `invalidas`. Capa o ruta. Lo primero con cartografía de fuera. AVISA si el CRS es geográfico. Vacías se borran (`limpiar_vacias`), inválidas se reparan (`reparar_geometrias`).

### tabla.py: la tabla de atributos

- `seleccionar_por_atributo(gdf, consulta)`: sintaxis de `DataFrame.query` (`"CLASE == 'A' and SUP > 5"`; nombres con espacios entre acentos graves). Devuelve una vista. AVISA si sale vacía.
- `seleccionar_por_ubicacion(gdf, otro, predicado="intersects", distancia=0)`: cada entidad de `gdf` una vez, sin columnas nuevas. Se lee `gdf <predicado> otro`. `distancia` > 0 dilata `otro`. TRAMPA: no es `union_espacial`, que duplica filas si dos entidades la tocan.
- `estadisticas_de_resumen(gdf, por=None, campos=None)` → DataFrame, una fila por grupo con `n` y `columna_estadistico`. `campos={col: estadistico}` o lista; un nombre suelto asume `'sum'` y lo dice. Nulos como grupo propio. FALLA si una columna de área o longitud no cuadra con la geometría.
- `unir_campo(gdf, tabla, clave, como="left")`: join NO espacial con DataFrame o ruta .csv/.xlsx/.xls. `clave`: nombre o `(columna_capa, columna_tabla)`. AVISA de claves repetidas (multiplican filas). FALLA si no casa ninguna clave, con los dos tipos: casi siempre es TIPO (`'007'` contra `7`); convierte del lado que conserva el dato (entero a texto).
- `atributos_a_mayusculas(gdf)`: copia con nombres de campo y valores de texto en mayúsculas. Va al final, después de `SUP`. TRAMPA: la geometría se reconoce por `gdf.geometry.name`. FALLA si dos nombres chocan (`sup` y `SUP`).
- `atributos_a_minusculas(gdf)`: gemela en minúsculas, mismas reglas.

**Editar campos.** Todas devuelven copia, no tocan la geometría y FALLAN si un nombre de campo no existe (pandas callaría).

- `listar_campos(capa)` → DataFrame `campo`, `tipo`, `nulos`, `ejemplo`; con la ruta de un .shp, también `tipo_dbf`, `ancho` y `decimales` escritos.
- `agregar_campo(gdf, campo, valor=None, tipo=None)`: `valor` uno solo o una lista por POSICIÓN; `tipo` `'texto'`/`'entero'`/`'decimal'`/`'fecha'`. FALLA si el campo existe (sobrescribir es `calcular_campo`).
- `calcular_campo(gdf, campo, expresion)`: texto de `DataFrame.eval` o función por fila. Crea o sobrescribe; AVISA si sobrescribe.
- `renombrar_campos(gdf, nombres)`: `{"viejo": "NUEVO"}`. FALLA si un nuevo choca con otro campo.
- `eliminar_campos(gdf, campos)`.
- `conservar_campos(gdf, campos)`: deja solo esos, en ese orden.
- `ordenar_campos(gdf, campos)`: esos primero, el resto detrás.
- `cambiar_tipo(gdf, campo, tipo, forzar=False)`: FALLA si un valor no convierte (lo muestra); `forzar=True` lo deja nulo y AVISA. A entero no trunca.
- `reemplazar_valores(gdf, campo, reemplazos)`: `{viejo: nuevo}`; lo no listado queda igual. FALLA si ninguna clave aparece (casi siempre `1` contra `'1'`).
- `calcular_fecha_hora(gdf, campo="time", utc_offset=-6)`: añade `FECHA` (fecha) y `HORA_LOCAL` (texto HH:MM:SS) desde un tiempo UTC del GPS. **TRAMPA** leído sin convertir, un punto de la tarde sale con fecha del día siguiente. FALLA si un valor no se lee como fecha.
- `ordenar_filas(gdf, por, descendente=False)`: nulos al final, índice renumerado.
- `crear_capa(geometrias, crs, atributos=None, tipos=None)`: capa desde cero; `crs` obligatorio; `tipos` necesario en una capa vacía.

### proyeccion.py: CRS

- `reproyectar(gdf, destino, nombre="capa")`: deja la capa en ese CRS; si ya está, devuelve la MISMA capa (no copia). `destino`: EPSG, cadena u objeto CRS (nunca `to_epsg()`, R3). AVISA cuando el cambio de datum pasa de 1 m o falta la rejilla (NAD27 → UTM 13N: 12 m sin que `to_crs` diga nada). FALLA si la capa no trae CRS. VER: R3.

### geometria.py: vector

- `recortar(gdf, mascara)`: clip por capa o geometría; reproyecta `mascara` (R3). Conserva solo el tipo de geometría de entrada.
- `disolver(gdf, campo=None, valor=None)`: todo en una geometría, opcional `campo=valor`. Pierde los demás atributos (`disolver_por_grupo` los conserva). FALLA si la unión sale vacía.
- `disolver_por_grupo(gdf, campo)`: una geometría por valor de `campo` (p. ej. `gridcode`). VER: R5.
- `separar_multipartes(gdf)`: una fila por pieza, atributos copiados. La inversa de `disolver_por_grupo`. No recalcula áreas. VER: R5.
- `zona_de_influencia(gdf, distancia)`: buffer en unidades del CRS; copia.
- `borrar(gdf, otro)`: resta lo que pise `otro` (gana `otro`); alinea CRS. Mueve la geometría (R2).
- `resolver_por_prioridad(capas)`: lista de mayor a menor prioridad → una capa sin encimados; cada una se recorta contra la unión de TODAS las anteriores. Mueve la geometría (R2).
- `repartir_por_cercania(gdf_a, gdf_b, resolucion=1.0, nombres=("A","B"))` → `(gdf_a, gdf_b, conflicto)`: dos capas sin jerarquía; cada pedazo del solape va al núcleo más cercano (empate → A). Una entidad por capa (`disolver` antes). FALLA si la malla pasa de 20 M píxeles: el tope mide el rectángulo envolvente, sube `resolucion` a 2 o 5.
- `fusionar(capas)`: concatena (CRS de la primera). FALLA si alguna viene en otro CRS (R3).
- `puntos_a_linea(gdf, campo=None)`: en orden de filas; `campo` = una línea por grupo; ≥2 puntos.
- `puntos_a_poligono(gdf, campo=None, forma="orden", lado_max=0.0)`: ≥3 puntos; con `campo`, uno por grupo. `forma`: `"orden"` (vértices en orden de filas; AVISA si sale en moño y lo devuelve), `"convexa"` (convex hull, no depende del orden) o `"concava"` (Delaunay erosionado desde el borde: quita los triángulos de borde con algún lado > `lado_max` m; siempre una pieza, sin huecos, con todos los puntos dentro). `lado_max` solo con `"concava"`: 0 = lo más ajustado; ≥ la arista más larga = la convexa. Es un supuesto; imprime la rejilla uniforme `sqrt(area convexa / n)` (de 2 a 4 veces, referencia) y el umbral relativo 0-1 equivalente, que son metros distintos en cada archivo. Con margen `d` después, por encima de `2d` rellena vacíos que el margen no uniría. FALLA con `lado_max` en otra forma, con puntos en línea o con CRS en grados.
- `linea_a_poligono(gdf)`: cierra cada línea en polígono.
- `crear_cuadro(gdf, margen=0)`: bounding box ampliado `margen`. VER: R4.
- `cuadros_mde(predio, resolucion=None, margen_borde_celdas=None, margen_salida=0.0, parametros=None)` → `(cuadro_recorte, cuadro_salida)`. `cuadro_salida` = el extent del MDE; `cuadro_recorte` = salida ampliada `margen_borde_celdas × resolucion`, al que se recortan curvas y cauces. Con `parametros=p` toma los dos del dict. TRAMPA: sin `margen_salida=2*h` la ventana de Horn deja NaN el anillo del borde. VER: P1.
- `limpiar_vacias(gdf)`: quita geometrías vacías o nulas.
- `reparar_geometrias(gdf)`: `make_valid` y descarta vacías y esquirlas. No `buffer(0)` (en un moño se queda con media superficie).
- `calcular_superficie(gdf, campo=None, unidad="ha", decimales=2)`: columna de área (`"m2"`, `"ha"`, `"km2"`) con cualquier CRS (geográfico: elipsoide). Redondea a 2 decimales. VER: R2.
- `superficie_total_ha(gdf)` → número sin redondear; capa vacía 0.0. FALLA si el CRS no es proyectado en metros. La estricta de las guardas.
- `superficie_total(gdf, unidad="ha")` → número sin redondear, con cualquier CRS.
- `comprobar_particion(capas, zona, tolerancia_ha)` → `(falta_ha, solape_ha)`. Mide por separado hueco, exceso y doble conteo, y compara cada uno contra la tolerancia (una cifra neta compensaría). Con una capa, comprueba que sus filas parten la zona. FALLA si alguno pasa, con piezas y centroide. VER: R9.
- `longitud_total_m(gdf)` → metros sin redondear. FALLA si el CRS no es proyectado en metros.
- `longitud_total(gdf, unidad="m")` → longitud (perímetro en polígonos) con cualquier CRS.
- `concordancia_lineas(lineas, referencia, tolerancias, zona=None)` → DataFrame por `tol`: `km_lineas`, `km_referencia`, `precision_pct`, `cobertura_pct`, en las DOS direcciones (una sola engaña). `tolerancias` es escalera: se lee si el orden entre redes aguanta todos los peldaños. TRAMPA: un juez no puede ser insumo; si `referencia` entró como `quiebres`, se parece por construcción (usa `bloques_ajedrez`). FALLA con CRS distinto o geográfico.
- `bloques_ajedrez(zona, lado)` → cuadros alternados con `pliegue` (0/1), `fila`, `col`: la partición de un hold-out espacial (un pliegue impone, el otro juzga). `lado` es declarado.
- `pendiente_sostenida(curvas, zona, equidistancia)` → `(separacion_m, pendiente_pct)` que la cartografía sostiene dentro de `zona`, por G'(0). Juez independiente del MDE. Sin curvas dentro → `(nan, nan)`.
- `ancho_banda(curvas, campo, zona, equidistancia, zigzag, pendiente_pct=100.0, cortes=None, devolver_banda=False)` → `(tabla, resumen)`: el ancho MEDIDO `w` de la clase alta y su área en carta (`resumen`: `ancho_m`, `area_ha`, `largo_efectivo_m`, `separacion_max`, `ancho_geometrico`). `area_ha` es el blanco cartográfico que se cita. `devolver_banda=True` añade los tramos de curva de la banda, para el % del escarpe que el MDE ve. No hace falta recortar `curvas` antes. TRAMPA: `zigzag` se cancela en `w` pero manda en `area_ha`; `longitud_total_m(banda)` es largo de trazo, divídelo entre `zigzag`. FALLA sin banda medible (es un resultado).
- `sostenimiento_por_clase(gdf, curvas, campo, rangos, equidistancia)` → lista por corte acumulado (`>= p`) con `corte`, `area_ha`, `n_piezas`, `s_sostenida`, `piso_local`, `p_sostenida`. Decide qué corte se entrega: el más alto con `piso_local <= h <= s_sostenida`, de una sola interpolación. `s_sostenida` NaN = rizo puro. TRAMPA: la condición es necesaria, no suficiente. VER: P1.
- `separacion_media(curvas, zona, paso=None, resolucion=None)` → `(lambda_m, longitud_efectiva_m, factor_zigzag)` por G'(0). λ subestima los llanos. Sin curvas → NaN.
- `auditar_rangos(gdf, curvas, campo, rangos, equidistancia, distancia, mascara, resolucion)` → lista por rango con `area_mde_ha`, `techo_ha`, `s_sostenida`, `p_sostenida`, `bandera`: techo cartográfico flojo. `distancia`/`mascara` de `raster_distancia` sobre el mismo dominio.
- `intersecar(gdf, otro)`: una parte por par que se pisa; atributos de `gdf` (para los de `otro`, `union_espacial`). Mueve la geometría.
- `union_espacial(gdf, otro, como="left", predicado="intersects")`: join espacial sin cortar geometrías. TRAMPA: duplica la fila si dos entidades la tocan.
- `entidad_a_punto(gdf, dentro=True)`: un punto por entidad. `dentro=True` usa `representative_point()` (siempre dentro). TRAMPA: el centroide de una U cae fuera y un join posterior le pega datos del vecino.
- `vertices_a_puntos(gdf)`: un punto por vértice con los atributos de su entidad y `PARTE`, `ANILLO` (0 exterior, 1+ huecos) y `VERTICE`. Sin el vértice de cierre repetido. Acepta multipartes sin separar. NO copia las columnas de área ni de longitud (`SUP`, `AREA`, `PERIMETRO`...): repetidas por vértice, su suma multiplica la superficie. Para un punto por entidad, `entidad_a_punto`.
- `cercano(gdf, otro, campo_id=None, max_distancia=None)`: añade `DIST_M` y `ID_CERCA`. Una fila por entidad aunque haya empates (AVISA cuántos). FALLA con CRS geográfico.
- `puntos_sobre_linea(gdf, intervalo, incluir_final=True)`: cadenamiento con `CADENA_M` e `ID_LINEA`; `incluir_final` añade el extremo.
- `identidad(gdf, otro)`: `gdf` entera partida donde la pisa `otro`, con columnas de las dos. Mueve la geometría (R2): una superficie heredada se copia a cada pedazo.
- `union(gdf, otro)`: todo lo de las dos partido en sus cruces. No es `disolver` ni `fusionar`. Mueve la geometría.
- `thiessen(puntos, zona)`: Voronoi recortado a `zona` con los atributos de su punto; parte `zona` exacta. FALLA con puntos duplicados o CRS geográfico.
- `partir_por_linea(poligonos, lineas)`: una fila por pieza. AVISA de polígonos tocados que salieron enteros (línea que no atraviesa). Mueve la geometría.
- `calcular_longitud(gdf, campo=None, unidad="m", decimales=2)`: columna de longitud (`"m"`, `"km"`), cualquier CRS; en polígonos es el perímetro. VER: R2.
- `calcular_coordenadas(gdf, crs=None, formato="decimal", decimales=None)`: coordenadas de cada PUNTO en `crs` sin mover la geometría (`LAT`/`LON` o `X`/`Y`; `formato="gms"` en texto). FALLA con líneas o polígonos (usa `entidad_a_punto`) y con `gms` en CRS proyectado.
- `medir_geodesico(gdf)`: añade `SUP_GEO` (ha) y `LONG_GEO` (m) sobre el elipsoide; imprime la diferencia contra el cálculo plano.
- `simplificar(gdf, tolerancia, conservar_topologia=True)`: con polígonos simplifica la cobertura (los vecinos siguen encajando); la tolerancia es por área, usa el tamaño de píxel. FALLA si la capa de polígonos no es una partición, y hoy sobre la salida de `poligonizar` (no es cobertura noded).
- `cerrar_microhuecos(gdf, distancia)`: cada rendija entre 2+ polígonos va al vecino de borde más largo. No conoce la zona: `recortar` otra vez después. Tras `poligonizar` no hace falta.
- `rellenar_huecos(gdf, area_max_ha=None)`: quita anillos interiores ≤ `area_max_ha` (ponle la UMM); `None` los quita todos, también el claro real. Antes de `calcular_superficie`.
- `subdividir_por_area(gdf, area_max_ha, campo_area="superficie_ha", min_esquirla=100)`: parte polígonos grandes por bisección.
- `eliminar_menores(gdf, area_min_ha, area_max_ha, campo_area="superficie_ha")`: absorbe los polígonos chicos en su mejor vecino. Razona por fila (R5).
- `ruta_red(caminos, puntos, velocidades, campo=None, campo_sentido=None, nodar=False)` → línea con `METROS` y `MINUTOS` sobre una red de LÍNEAS (se conectan donde comparten vértice). `velocidades`: número o `{valor de campo: km/h}`. `puntos`: origen y destino, en ese orden. `nodar=True` parte en cada cruce (CAD). TRAMPA: las velocidades deciden la ruta, no solo los minutos; las capas de calles de CAD son contornos, no ejes. FALLA sin velocidad para un tipo, con red partida o sentidos que lo impiden.
- `isocronas(caminos, origenes, velocidades, cortes, campo=None, campo_sentido=None, nodar=False, hacia=False, ancho=None)` → una fila por banda con `MIN_DESDE`, `MIN_HASTA`, `KM`. Misma red que `ruta_red`. `hacia=True` mide llegando. `ancho` (m) da polígonos en vez de líneas y es un supuesto que se declara.
- `nombre_campo_libre(gdf, campo)`: nombre de columna sin colisión.

### raster.py (trabajan sobre arrays 2D numpy + transform/crs)

- `cargar_raster(ruta)` → `(array, transform, crs, resolucion_m)`. NoData entra como NaN; aplica `scale`/`offset` (sin él, un Sentinel-2 L2A da un NDVI sesgado).
- `cargar_bandas(ruta, bandas=(1, 2, 3))` → la misma tupla con el array 3D `(banda, y, x)`, bandas numeradas desde 1. Sale en float (en uint8, `nir - rojo` hace underflow). Con una banda por archivo, usa `recortar_raster` por banda.
- `guardar_raster(arr, ruta, transform, crs, nombres=None)`: GeoTIFF float32 con NoData NaN; 2D, 3D o lista de 2D (una banda cada uno). `nombres` en multibanda siempre: es lo único que viaja con el archivo. TRAMPA: no guardes el MDE acondicionado (R6).
- `recortar_raster(ruta, mascara, nodata=nan)` → la tupla de 4; también sobre la URL de un COG. Sale float aunque el archivo sea entero. El `crs` es de rasterio (sin `.name`: `pyproj.CRS(crs).name`). FALLA "no intersecta" cuando la escena no cubre la zona (no es de proyección), con los dos extents.
- `mosaico(rutas, mascara, crs=None, metodo=nearest)` → la tupla de 4: junta rásters de una banda leyendo solo el rectángulo de `mascara`. En el solape gana la primera ruta. AVISA del % de predio sin dato. FALLA con escalas u offsets distintos entre fuentes.
- `rellenar_nodata(arr)`: NaN ← píxel válido más cercano. Solo para hidrología (R6, R8).
- `mascara_relleno(mde_original, iteraciones=1)`: celdas cuya ventana 3x3 tocó el relleno. Salida de emergencia para un .tif que ya venía relleno: `calcular_pendiente(..., invalidar=relleno)`.
- `remuestrear(arr, transform, crs, res_destino, metodo=bilinear)` → `(array, transform)` en el mismo CRS.
- `reproyectar_raster(arr, transform, crs, crs_destino, res_destino=None, metodo=bilinear)` → `(array, transform)`; la malla se recalcula. TRAMPA: bilineal inventa clases en un categórico; usa `Resampling.nearest`.
- `indice_vegetacion(tipo, rojo=None, verde=None, azul=None, nir=None, swir1=None, swir2=None)` → array float. `"ndvi"`, `"ndmi"`, `"ndwi"`, `"mndwi"`, `"nbr"`; sin NIR, `"vari"` o `"exg"`. Las bandas van por NOMBRE (`indice_vegetacion("ndvi", **bandas)`). No umbraliza: sigue `reclasificar_rangos` o `zonificar`. Denominador 0 → NaN.
- `interpolar_mde(curvas, campo, resolucion=None, equidistancia=None, intervalo_muestreo=None, cuadro=None, metodo=None, suavizado=0.0, vecinos=None, lambda_m=None, amplitud_rizo=None, quiebres=None, paso_quiebre=None, parametros=None)` → `(array, transform)`. Curvas → MDE; no extrapola (NaN fuera de la envolvente). `parametros=p` rellena `resolucion`, `equidistancia`, `intervalo_muestreo`, `vecinos`, `lambda_m` y `metodo`; lo pasado a mano manda. `metodo`: `"spline"`/`"tps"` (default), `"tin"`, `"malla"` (opcional, P3) o `"idw"` (respaldo). `quiebres`: líneas sin cota (cauces) que reciben z de sus cruces (`cotas_por_cruce`), cada `paso_quiebre` (default `h/2`); solo con `"tin"` o `"malla"`. `suavizado` y `vecinos` son del spline; la malla los ignora y muestrea a `min(intervalo_muestreo, h/2)`. Defaults sin `parametros`: `resolucion` = λ/2 hacia arriba e `intervalo_muestreo` = λ, nunca la equidistancia. TRAMPA: con spline, `h` bajo λ/2 deja entrar el rizo como pendiente espuria `A·sin(2πh/λ)/h` (máxima en `h ≈ λ/4`); con `amplitud_rizo=A` medida, el aviso reporta esa espuria. AVISA siempre de `sin dato: N ha` (R8) y de los planos exactos a cota de curva, con las ha. FALLA con CRS no proyectado en metros. VER: P1, P2, P3.
- `planos_de_curva(mde, transform, curvas, campo)` → máscara de celdas planas exactas a la cota de una curva (TIN: triángulos en una curva; spline: `vecinos` en una curva). Salen al 0 % y sin orientación. Es un subconjunto del `atomo` de `histograma_fase`. Para dejarlas sin pendiente: `calcular_pendiente(..., invalidar=planos)`. VER: P3.
- `cotas_por_cruce(quiebres, curvas, campo, paso)` → puntos con `z` y `origen`: cada cruce vale la cota de la curva, entre cruces se interpola, no extrapola. Mismo CRS en las dos capas (no reproyecta). AVISA de líneas que suben y bajan. `interpolar_mde(quiebres=)` la llama por dentro; aparte sirve para revisar los puntos.
- `validar_mde(curvas, campo, resolucion=None, equidistancia=None, intervalo_muestreo=None, cuadro=None, metodo=None, suavizado=0.0, vecinos=None, lambda_m=None, amplitud_rizo=None, porcentaje_reserva=0.2, semilla=None, parametros=None)` → `(metricas, puntos)`: hold-out sobre la isolínea (`rms`, percentiles; `residual` y `curva` por punto). Sirve para cazar cotas mal capturadas (`groupby("curva")["residual"].mean()`). TRAMPA: no sirve para elegir método, tunear `suavizado` ni comparar resoluciones (mide la isolínea, no el terreno entre curvas).
- `rasterizar(gdf, forma, transform, valor=1, todo_tocado=False)` → máscara uint8. `False` pinta el píxel si su centro cae dentro; `True`, cualquiera que toque. VER: R8.
- `extraer_valores(arr, transform, puntos, crs=None, fuera=nan)` → un valor por punto, en orden. Fuera del ráster devuelve `fuera`, no el borde.
- `combinar_categorias(a, b, factor=10)`: `a*factor+b` (se recupera con `//` y `%`). FALLA si `factor` no supera al máximo de `b` o hay negativos (el -1 de NoData).
- `poligonizar(arr, transform, crs, mascara=None, campo="valor")`: ráster → polígonos; ignora el 0 (recodifica si es una clase). Usa `mascara` para no vectorizar de más.
- `limpiar_moteado(arr, min_pixeles, conectividad=4, mascara=None, exentos=None)`: sieve de GDAL; las regiones menores a `min_pixeles` van a su vecino mayor. `mascara` con el NoData fuera (`codigos > 0`). `exentos` = códigos que ni se absorben ni absorben (la cinta de la clase alta, en una sola pasada). VER: P1.
- `resumen_raster(arr, transform=None)` → dict `n_celdas`, `n_nan`, `min`, `max`, `media`, `desv`, `p5`, `p50`, `p95` y, con `transform`, `ha_sin_dato`. FALLA si todo es NaN. Un -9999 numérico se ve en `min`.
- `sombreado(mde, resolucion, azimut=315.0, altitud=45.0, z_factor=1.0)` → uint8 0-255 (Horn). 0 es NoData y sombra total.
- `estadisticas_zonales(arr, transform, zonas, campo, estadisticas=("media", "min", "max", "n"))` → DataFrame por zona con `n_celdas` y `n_nan`; una celda es de la zona si su centro cae dentro. AVISA de zonas más chicas que el píxel. FALLA con zonas solapadas (`resolver_por_prioridad`) o si ninguna cubre una celda (otro CRS).
- `alinear_rasters(referencia, *otros, metodo=bilinear)` → arrays en la malla de `referencia`; cada ráster es `(arr, transform, crs)`. TRAMPA: misma forma con distinto transform se suma sin error y da basura. Categóricos: `nearest`.
- `curvas_de_nivel(mde, transform, crs, equidistancia, base=0.0, campo="cota")` → líneas con `campo`, niveles `base + k*equidistancia`. QA del MDE contra las curvas de partida con `concordancia_lineas`.
- `vista_previa(capa_o_array, ruta, transform=None, ancho_px=1200)`: PNG en grises para mirar (array estirado p2-p98, capa en blanco; tupla `(array, capa)` con `transform`). No es una lámina.
- `calcular_orientacion(mde, resolucion, unidad="clase", z_factor=1.0, invalidar=None)`: Horn 3x3. `"clase"` → uint8 0 = sin orientación (NoData o exactamente plano), 1 N, 2 E, 3 S, 4 O; `"grados"` → 0-360 con NaN. TRAMPA: en un TIN el plano es artefacto; para entregar, `orientacion + 1` y "SIN ORIENTACION", no "PLANO". VER: P3.
- `calcular_pendiente(mde, resolucion, unidad="porcentaje", z_factor=1.0, invalidar=None)` → float32. Horn 3x3; `resolucion` = el `transform`, no una constante. Regla NoData 7 de 8. `z_factor=0.3048` con z en pies. 100 % = 45°. Recibe el NaN intacto (R8).
- `ruta_a_pie(mde, transform, crs, puntos, caminos=None)` → línea con `HORAS`. Costo anisótropo por paso con Tobler (fuera de camino, 3/5); `caminos` = máscara booleana. Origen y destino en ese orden. NaN = intransitable. ~550 B/px: recorta a la caja de los puntos si no cabe. FALLA si un punto cae fuera o sin dato, o no hay paso.
- `ruta_costo_minimo(costo, transform, crs, puntos)` → línea con `COSTO`. `costo` por unidad de distancia lo arma el script (p. ej. `pendiente + 1`). Isótropo: para caminar, `ruta_a_pie`. FALLA con costos negativos o sin paso.
- `reclasificar_rangos(arr, rangos, nodata_valor=-1)` → `(códigos int32, {código: etiqueta})`. `rangos` = `(etiqueta, mín, máx)`; cada rango `(mín, máx]`, el primero `[mín, máx]`.

### hidrologia.py (todo pysheds. Encima de `raster`: solo usa `rellenar_nodata`, `poligonizar` y `rasterizar`)

- `quemar_cauces(mde, cauces, transform, profundidad)` → `(array, n_pixeles)`. Fuerza la pendiente aguas abajo en el cauce (un tramo llano recibe micro-pendiente, uno que sube se rellena). Cauces en el CRS del ráster (R3). `n_pixeles=0` = no pisa el ráster: imprime los dos extents. AVISA si hay NoData bajo el cauce (rellena antes, R6), tramos sin salida y cauce por encima del terreno.
- `acondicionar_mde(mde_path)` → `(grid, mde_acondicionado)`. Rellena el NoData dentro y reporta las ha parcheadas (superficie sin dato de origen, R8). Sale float64: no se guarda (R6).
- `acumulacion_flujo(grid, dem)` → `Flujo(grid, fdir, acumulacion)`. AVISA cuando el flujo muere dentro del ráster, con la fracción que se traga el mayor sumidero; avisa y no repara.
- `rellenar_sumideros(grid, dem, cauces=None)` → `(mde, informe)`: sube cada sumidero interior hasta su derrame, en rondas, hasta que no queda ninguno. El `informe` (`rondas`, `celdas`, `subida_max_m`, `pits_iniciales`, `pits_finales`, `convergio`) viaja con la cifra. TRAMPA: taparía una endorreica real; comprueba antes que sean de una celda y milímetros. Si se quemó, pasa los mismos `cauces` (AVISA si los des-quema).
- `altura_sobre_cauce(flujo, mde, cauces)` → array HAND: cuánto sube cada celda sobre el cauce al que drena; cotas del MDE BASE sin quemar. AVISA de las ha que no llegan a ningún cauce.
- `etiquetar_laderas(acumulacion, umbral)` → `(array, n_regiones)`: laderas separadas por el cauce, no cuencas (para una cuenca, `delimitar_cuenca`).
- `red_drenaje(flujo, umbral)` → líneas. `umbral` en celdas (`umbral_celdas`). FALLA si la red sale vacía, con la acumulación máxima.
- `orden_cauces(flujo, umbral)` → array de Strahler. El 0 es fuera de la máscara o sin tramo trazado; el reporte da numeradas contra máscara.
- `delimitar_cuenca(flujo, desague, umbral)` → un polígono. `desague`: punto (coordenada de campo) o zona ("el desagüe de esto", toma la celda de mayor acumulación). Ajusta el punto al cauce más cercano y reporta cuánto lo movió. TRAMPA: una zona con dos salidas devuelve solo la mayor (AVISA si la cuenca es menos de la mitad de la zona; usa `microcuencas`).
- `microcuencas(flujo, zona, umbral=None)` → una fila por desagüe que parte la zona (cada celda va al desagüe por el que sale). Columnas `ID_MICRO`, `DESAGUE_X`, `DESAGUE_Y`, `ACUM`, `EN_CAUCE` (con `umbral`), `ES_SUMIDER`. Sin superficie (R2) ni filtro de tamaño. TRAMPA: el cierre exacto es en celdas; contra el polígono, la tolerancia sale de `tolerancia_rasterizacion_ha` y recortar empeora el cierre.

### satelite.py (imagen Sentinel-2 por Earth Search: sin cuenta, sin token, sin dependencias nuevas. Encima de `raster`)

Solo Sentinel-2 L2A (`sentinel-2-c1-l2a`). Ni mapas base comerciales ni XYZ (sus licencias prohíben analizarlos). Cita obligatoria: `cita_sentinel2`.

- `buscar_sentinel2(predio, fechas, nubes_max=80)` → escenas, una por fecha, de menos a más nubes: dict `fecha`, `teselas`, `cubre` (% del predio), `nubes` (de la tesela entera, no del predio) y `urls`. `fechas` obligatoria. FALLA sin resultados (ValueError) o con la red caída (RuntimeError).
- `imagen_sentinel2(predio, fechas=None, escenas=None, bandas=None, limpio_min=99.0, max_fechas=3, max_revisar=10, crs=None, margen_nube=2, max_dias=30, escenas_relleno=None)` → `(bandas, transform, crs, resolucion, informe, origen)`. `fechas` o `escenas`, exactamente uno. `bandas` sale como dict `azul`, `verde`, `rojo`, `nir` (B8), `swir1`, `swir2` en reflectancia 0 a 1, NaN fuera, en nube y sin dato; acepta otros assets (`{asset: nombre}`). Elige la primera fecha con `limpio_min` % limpio en `scl` que cubra el predio; si no, rellena píxel a píxel con la fecha más cercana hasta `max_fechas` y `max_dias`. `escenas_relleno` nunca son la principal. `informe`: `fecha`, `teselas`, `nubes`, `cubre`, `dias`, `limpio_pct`, `aporta_pct`, `usada`; `origen`: la fila de cada píxel. TRAMPA: un nombre fuera de los seis rompe `indice_vegetacion(**bandas)`; `scl` es pobre; `swir` es de 20 m reales. AVISA del % sin dato. Si el predio cruza dos husos, pasa `crs=`.

### metrologia.py (las REGLAS: aritmética pura, sin rasters. Capa de abajo; aquí nada mide)

- `min_pixeles_umm(umm_m2, resolucion)` → `ceil(umm_m2/h²)`, el `min_pixeles` del sieve. Entero que salta de golpe; como el sieve absorbe, endurecerlo puede agrandar una clase.
- `umbral_celdas(area_aportadora_ha, resolucion)` → `ceil(area·10000/h²)`, el `umbral` de la hidrología. Se declara la superficie, no las celdas.
- `tolerancia_rasterizacion_ha(perimetro_m, resolucion)` → `P·h/2/10000`, la tolerancia de `comprobar_particion` para capas que salieron de un ráster. Es un techo; depende del contorno, no del área. VER: R9.
- `margen_borde_celdas(vecinos, intervalo_terreno, s_vacio, resolucion)` → `ceil(sqrt(n·i·s/pi)/h)+2`. `s_vacio` es la del VACÍO (`2·q90`), `intervalo_terreno` el del terreno.
- `resolucion_regla(equidistancia, umm_m2, rangos, ancho_medido=None, lambda_m=None, amplitud_rizo=None)` → `(h, factible, cotas)`. Techos `umm`, `deteccion`, `trazado` (= `w/3`) y, con `lambda_m`, el piso `antialias` (λ/2, del spline). Con `amplitud_rizo` medida el piso solo entra si la espuria pasa de la mitad del ancho de la clase más estrecha. Si el piso supera al techo, `factible=False` y gana el piso. `h` sale redondeado a un valor limpio (techo hacia abajo, piso hacia arriba). TRAMPA: no reduzcas `cotas` con `min(...)`, mezcla piso y techos.

### zonas.py (ráster continuo -> polígonos por rangos. Capa de arriba: usa vector y ráster a la vez)

- `zonificar(arr, transform, crs, rangos, min_pixeles, recorte=None, invalidar=None, exentos=None, campo="CLASE", por_clase=False, sin_dato="SIN DATO")` → GeoDataFrame con `gridcode`, `campo` y `SUP`. Reclasificar → sieve → poligonizar → recortar → superficie, para cualquier continuo, con las trampas ya resueltas (máscara `todo_tocado=True`, sieve solo dentro, `SUP` al final). `invalidar`: celdas fuera de toda clase. `exentos`: ETIQUETAS que el sieve no toca. `por_clase=True`: una multiparte por clase. `sin_dato`: las celdas NaN dentro salen como clase propia (AVISA con las ha); `None` las deja como hueco. No tira las piezas bajo la UMM que deja el recorte. VER: P1, R8.

### lamina.py (PNG de revisión con color, leyenda, escala y cita. Capa de arriba: usa vector y ráster a la vez)

Para revisar, no para entregar: PNG sin georreferencia ni mapa base. Los paneles se arman con los `panel_*` y se pasan en lista a `lamina`, en orden de lectura.

- `panel_rgb(bandas, transform, crs, titulo, contorno=None, estirado=None)` → panel en color natural con `rojo`, `verde`, `azul`; estirado sobre las tres juntas.
- `panel_continuo(arr, transform, crs, titulo, paleta, unidad, contorno=None, estirado=None)` → panel con barra de color; `paleta` `"gris"`, `"divergente"` (simétrica en 0) o `"vegetacion"`. `estirado=(vmin, vmax)` es un número tecleado (R1).
- `panel_clases(arr, transform, crs, titulo, etiquetas, colores, contorno=None)` → panel con leyenda; `etiquetas` el dict de `reclasificar_rangos`, `colores` con las mismas claves.
- `lamina(paneles, ruta, cita, columnas=None, titulo=None, pie=None, ancho_px=2000)` → dict con `ruta`, tamaño, escala y una fila por panel. `cita` es keyword sin default (`cita_sentinel2(informe)` o `None` con datos propios). Paneles con la misma `(paleta, unidad)` comparten estirado; sin dato en lavanda con su %. FALLA si los paneles no comparten forma, `transform` y `crs` (`alinear_rasters`), o con CRS en grados.
- `rotulo_escenas(informe)` → texto con las fechas usadas y su `aporta_pct`, para el título.
- `cita_sentinel2(*informes)` → `"Contains modified Copernicus Sentinel data AAAA"` con los años usados; acepta varios informes.

### barridos.py (lo que MIDE sobre el MDE y la cartografía; capa de arriba, usa vector y ráster a la vez. Ninguna decide: reportan)

- `barrer_resolucion(curvas, campo, predio, rangos, umm_m2=None, resoluciones=None, lambda_m=None, equidistancia=None, intervalo_muestreo=None, cuadro=None, metodo=None, suavizado=0.0, vecinos=None, etiqueta_alta=None, min_pixeles=None, quiebres=None, paso_quiebre=None, exentos=(), parametros=None, nucleos=None)` → una fila por `h` (de fino a grueso) con `area_alta_ha`, `delta_pct`, `n_piezas`, `s_sostenida`, `piso_local`, `p_sostenida`, `segundos`... La banda de incertidumbre de la clase alta frente a `h`: se declara junto a la cifra, no dictamina (el juez es `sostenimiento_por_clase`). `parametros=p` toma los del dict. `resoluciones=None` = `λ·(1/4, 1/3, 1/2, 2/3, 1)`; con `"tin"` o `"malla"` es obligatorio. Pasa los mismos `quiebres` y `exentos` que producción, o mide otra cadena. TRAMPA: `delta_pct` arrastra el salto de `min_pixeles`; fíjalo para leerlo limpio. Corre en procesos (`nucleos`, `geo.NUCLEOS`), con las mismas filas.
- `barrer_vecinos(curvas, campo, zona, resolucion, intervalo_terreno, s_vacio, enes=(20, 32, 48, 64, 90, 122, 160, 200), zigzag=1.0, equidistancia=None, tolerancia=0.01, p_max=1.0, nucleos=None, lambda_m=None)` → `(filas, n_rodilla)`: el `n` más chico a partir del cual la clase alta deja de moverse (para en el primer par dentro de `tolerancia`). `n_rodilla` es un `int` con `.avisos`: si no está vacío, no hubo rodilla (no convergió, o la clase alta es 0 ha a esa `h`) y `vecinos` no se mide. AVISA siempre. `derivar_parametros(vecinos="medir")` lo llama y le pasa `lambda_m`.
- `raster_distancia(curvas, zona, resolucion)` → `(distancia, mascara, transform)`: metros a la curva más cercana. Llamarla una vez y compartirla entre `separaciones` y `auditar_rangos`.
- `separaciones(distancia, mascara, resolucion, lambda_m, zigzag, cortes=None)` → `(tabla, resumen)` con `lambda_armonica`, `factor_zigzag`, `q50`, `q80`, `q90`. TRAMPA: `s_equivalente_zona` es `2·q50`; la `s` del vacío es `2·q90`.
- `histograma_fase(mde, curvas, campo, resolucion, mascara=None, ancho_bin=None)` → `(densidad, exceso_ha, evacuacion, atomo_ha, n_intervalos_completos)`: terraceo por fase de la cota. `exceso_ha` es el citable; `atomo_ha` el área de mesetas a cota de curva.
- `juzgar_clasificaciones(candidatas, curvas, campo, zona, rangos, equidistancia, zigzag, campo_clase="PENDIENTES")` → `(filas, recomendada)`. `candidatas = {nombre: (clases, metodo, h)}` con `metodo` `"tin"`, `"spline"` o `"malla"` (la malla con el criterio del TIN: supuesto). Por candidata: `veredicto` (`SI`/`CONDICIONAL`/`NO`), `pct_visto`, `ancho_x_w`, `corte`. Recomendada = mayor `pct_visto` entre `SI` y `CONDICIONAL`, o None; imprime la BANDA entre las que pasan. `zigzag` el de `derivar_parametros`. VER: P2.
- `amplitud_rizo(mde, transform, curvas, campo, mascara=None, cuantil=99.0)` → `(a, detalle)`: PISO de la amplitud del rizo (excursión fuera de `[z_lo, z_hi]` entre curvas contiguas). Se lee en un solo sentido. AVISA si pasa de `e/4` (lomos sin cerrar). Mira `frac_excursion` y `frac_usada`. Alimenta `derivar_parametros(amplitud_rizo=a)`. VER: P1.

### parametros.py (parámetros con PROCEDENCIA. Capa de arriba: encadena todo lo anterior)

- `derivar_parametros(curvas, campo, zona, rangos, escala, vecinos=None, equidistancia=None, lambda_m=None, zigzag=None, ancho_medido=None, s_vacio=None, amplitud_rizo=None, resolucion=None, cuadro=None, medir_ancho=True, metodo="spline", medidos=None)` → dict de `Valor`/`ValorEntero` (floats e ints con `.fuente` y `.origen`) que se pasa entero como `parametros=p` a `interpolar_mde`, `validar_mde`, `cuadros_mde` y `barrer_resolucion`. Encadena equidistancia → λ (sobre el extent) y zigzag (sobre la zona) → `w` (`ancho_banda`) → `h` (`resolucion_regla`) → `s_vacio` → margen, `min_pixeles`, intervalos. Claves principales: `metodo`, `umm_m2`, `equidistancia`, `lambda_m`, `zigzag`, `ancho_w`, `blanco_carta_ha`, `resolucion`, `resolucion_piso`, `factible`, `factor_techo` (spline: `h / techo de trazado`), `s_vacio`, `intervalo_muestreo`, `vecinos`, `margen_borde_celdas`, `min_pixeles`, `insumos`. `vecinos`: un entero (declarado), `"medir"` (barre con `barrer_vecinos`) o una lista de `n`; sin rodilla entra `default` y sale en `sin_medir`. `metodo="tin"` o `"malla"`: sin piso antialias, sin `vecinos`, margen `ceil(s_vacio/h)+2`. `medidos=p` de otra llamada sobre las mismas capas reusa equidistancia, λ, zigzag, `w` y el blanco con su procedencia (192 s → 0.7 s; FALLA si la huella de las capas cambia). Un valor dado a mano salta su medición y entra `declarado`, salvo que sea un `Valor`, que conserva su fuente. No decide nada: devuelve los números y `sin_medir`. VER: R1, P1 a P3.
- `sin_medir(params)` → nombres con fuente `default`: lo que nadie eligió. Vacía = ningún número inventado. VER: R1.
- `tabla_procedencia(params)` → texto (y lo imprime) de `parametro / valor / fuente / origen`, con los bloques `SIN MEDIR`, `INFACTIBLE`, `FACTOR ... >= 9.5` y la espuria si `A` está medida. Va al entregable.
- `comprobar_cifras_firmes(medidos, firmes, tol=0.05, nombre="cifras firmes")`: FALLA si lo medido se sale de `tol` respecto a las cifras firmes del predio. En la primera corrida no hay cifras firmes y no se inventan: se revisa a mano y se fijan. Un `firmes` de otro predio no protege nada.
