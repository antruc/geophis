# geophis: índice de la API pública

**Versión:** 1.2.1 | todas las funciones de `__all__`

Índice para buscar por nombre. Cada fila es una línea y el enlace lleva a la entrada completa en `GEOPHIS.md`, donde están la firma, lo que devuelve y la trampa que la rodea. **Este archivo no documenta: apunta.** Si algo de aquí contradice a `GEOPHIS.md`, manda `GEOPHIS.md`.

```python
import geophis as geo
```

## [archivo.py: E/S de capas](GEOPHIS.md#archivopy-es-de-capas)

| Función | Qué hace |
|---|---|
| [`cargar`](GEOPHIS.md#archivopy-es-de-capas) | Lee capa vectorial (.shp/.gpkg/.geojson/.gpx/.dxf) → GeoDataFrame; `crs=` asigna el CRS si el archivo no lo trae |
| [`cargar_puntos`](GEOPHIS.md#archivopy-es-de-capas) | Puntos desde CSV/Excel a GeoDataFrame; `epsg` fija el CRS |
| [`convertir`](GEOPHIS.md#archivopy-es-de-capas) | `cargar`+`guardar` en una línea |
| [`guardar`](GEOPHIS.md#archivopy-es-de-capas) | Formato por extensión. .gpx/.kml/.kmz reproyecta a EPSG:4326 automáticamente; `anchos=` fija el ancho de campo en .shp |
| [`comprobar_nombres_shp`](GEOPHIS.md#archivopy-es-de-capas) | Devuelve dict `{columna: motivo}` con las columnas que NO sobreviven a un `.shp`; vacío = todas pasan |
| [`exportar_tabla`](GEOPHIS.md#archivopy-es-de-capas) | Tabla de atributos sin geometría a `.csv`/`.xlsx` |
| [`cargar_waypoints`](GEOPHIS.md#archivopy-es-de-capas) | Waypoints GPX con tabla limpia (NAME, LAYER, ELEVATION, time ISO UTC, sym) |
| [`cargar_tracks`](GEOPHIS.md#archivopy-es-de-capas) | Tracks GPX, una línea por `<trkseg>` (NAME, LAYER, gpxx_DisplayColor, START_TIME, END_TIME en hora local) |
| [`detectar_utm`](GEOPHIS.md#archivopy-es-de-capas) | EPSG del huso UTM (WGS84) del centroide de `gdf` |
| [`detectar_cota`](GEOPHIS.md#archivopy-es-de-capas) | Detecta el campo de cota y la equidistancia de una capa de curvas |
| [`detectar_campo`](GEOPHIS.md#archivopy-es-de-capas) | Columna cuyos valores contienen todo `vocabulario`, p. ej. `{'PERENNE','INTERMITENTE'}` |
| [`listar_capas`](GEOPHIS.md#archivopy-es-de-capas) | Devuelve lista de nombres de capa del archivo, sin cargar ninguna (lee el catálogo, no las entidades) |
| [`describir`](GEOPHIS.md#archivopy-es-de-capas) | Devuelve dict con `n`, `tipos`, `crs`, `epsg`, `unidades`, `extension`, `campos`, `nulos`, `vacias`, `invalidas` |

## [tabla.py: la tabla de atributos](GEOPHIS.md#tablapy-la-tabla-de-atributos)

| Función | Qué hace |
|---|---|
| [`seleccionar_por_atributo`](GEOPHIS.md#tablapy-la-tabla-de-atributos) | *Select by Attribute*, sintaxis de `DataFrame.query` (`"CLASE == 'A' and SUP > 5"`) |
| [`seleccionar_por_ubicacion`](GEOPHIS.md#tablapy-la-tabla-de-atributos) | *Select by Location* |
| [`estadisticas_de_resumen`](GEOPHIS.md#tablapy-la-tabla-de-atributos) | *Estadísticas de resumen*: DataFrame, una fila por grupo con `n` y los estadísticos pedidos |
| [`unir_campo`](GEOPHIS.md#tablapy-la-tabla-de-atributos) | *Unir campo*, el join NO espacial (planilla de campo contra shapefile) |
| [`atributos_a_mayusculas`](GEOPHIS.md#tablapy-la-tabla-de-atributos) | Nombres de campo y valores de texto en MAYÚSCULAS, antes de guardar |
| [`atributos_a_minusculas`](GEOPHIS.md#tablapy-la-tabla-de-atributos) | Nombres de campo y valores de texto en minúsculas, antes de guardar |
| [`listar_campos`](GEOPHIS.md#tablapy-la-tabla-de-atributos) | Devuelve DataFrame con campo, tipo, nulos y ejemplo; con ruta .shp, también el ancho escrito en el .dbf |
| [`agregar_campo`](GEOPHIS.md#tablapy-la-tabla-de-atributos) | *Agregar campo*; falla si el campo ya existe |
| [`calcular_campo`](GEOPHIS.md#tablapy-la-tabla-de-atributos) | *Calculadora de campos*: expresión de texto o función por fila; avisa si sobrescribe |
| [`renombrar_campos`](GEOPHIS.md#tablapy-la-tabla-de-atributos) | `{viejo: NUEVO}`; falla si un nombre viejo no existe o el nuevo choca |
| [`eliminar_campos`](GEOPHIS.md#tablapy-la-tabla-de-atributos) | *Eliminar campo* |
| [`conservar_campos`](GEOPHIS.md#tablapy-la-tabla-de-atributos) | Deja solo los campos listados, en ese orden |
| [`ordenar_campos`](GEOPHIS.md#tablapy-la-tabla-de-atributos) | Pone los campos listados primero |
| [`cambiar_tipo`](GEOPHIS.md#tablapy-la-tabla-de-atributos) | A texto, entero, decimal o fecha; falla si un valor no convierte |
| [`reemplazar_valores`](GEOPHIS.md#tablapy-la-tabla-de-atributos) | `{viejo: nuevo}`; lo no listado queda igual |
| [`ordenar_filas`](GEOPHIS.md#tablapy-la-tabla-de-atributos) | *Sort* por uno o más campos |
| [`crear_capa`](GEOPHIS.md#tablapy-la-tabla-de-atributos) | Capa desde cero: geometrías, CRS obligatorio y atributos |

## [proyeccion.py: CRS](GEOPHIS.md#proyeccionpy-crs)

| Función | Qué hace |
|---|---|
| [`reproyectar`](GEOPHIS.md#proyeccionpy-crs) | Deja la capa en ese CRS; si ya está, no hace nada y devuelve la MISMA capa (no copia: si la vas a mutar, copia tú) |

## [geometria.py: vector](GEOPHIS.md#geometriapy-vector)

| Función | Qué hace |
|---|---|
| [`recortar`](GEOPHIS.md#geometriapy-vector) | Clip por otra capa o por una geometría shapely suelta (reproyecta `mascara` al CRS de `gdf`) |
| [`disolver`](GEOPHIS.md#geometriapy-vector) | Une todo en una geometría; opcional etiqueta `campo=valor` |
| [`disolver_por_grupo`](GEOPHIS.md#geometriapy-vector) | Un polígono por cada valor único de `campo` (dissolve por grupo, p. ej. por `gridcode`); `campo` agrupa, no solo etiqueta |
| [`separar_multipartes`](GEOPHIS.md#geometriapy-vector) | Multiparte → una fila por pieza (*multipart to singlepart*) |
| [`zona_de_influencia`](GEOPHIS.md#geometriapy-vector) | *Zona de influencia* (Buffer), en unidades del CRS; devuelve copia |
| [`borrar`](GEOPHIS.md#geometriapy-vector) | *Borrar* (Erase): resta lo que pise `otro` (capa o geometría shapely suelta; prioridad a `otro`) |
| [`resolver_por_prioridad`](GEOPHIS.md#geometriapy-vector) | Lista ordenada de mayor a menor prioridad → una sola capa sin encimados |
| [`repartir_por_cercania`](GEOPHIS.md#geometriapy-vector) | Dos capas que se solapan sin jerarquía entre sí (asignación euclidiana: cada pedazo a la más cercana) |
| [`fusionar`](GEOPHIS.md#geometriapy-vector) | *Fusionar* (Merge): concatena lista de capas (CRS de la primera) |
| [`puntos_a_linea`](GEOPHIS.md#geometriapy-vector) | Puntos→línea en orden de filas; `campo` = una línea por grupo. ≥2 puntos |
| [`puntos_a_poligono`](GEOPHIS.md#geometriapy-vector) | Puntos→polígono (vértices en orden); `envolvente=True` usa convex hull. ≥3 puntos |
| [`linea_a_poligono`](GEOPHIS.md#geometriapy-vector) | Cierra cada línea en polígono |
| [`crear_cuadro`](GEOPHIS.md#geometriapy-vector) | Bounding box ampliado `margen` |
| [`cuadros_mde`](GEOPHIS.md#geometriapy-vector) | `(cuadro_recorte, cuadro_salida)`, el par de extents que pide `interpolar_mde` (práctica de ANUDEM) |
| [`limpiar_vacias`](GEOPHIS.md#geometriapy-vector) | Quita geometrías vacías/nulas |
| [`reparar_geometrias`](GEOPHIS.md#geometriapy-vector) | `make_valid` + descarta vacías e inválidas; no `buffer(0)`, que pierde la mitad de un polígono en moño |
| [`calcular_superficie`](GEOPHIS.md#geometriapy-vector) | Añade columna de área en la unidad pedida (m2, ha, km2), con cualquier CRS |
| [`superficie_total_ha`](GEOPHIS.md#geometriapy-vector) | Superficie total en ha, número y sin redondear; falla si el CRS no va en metros |
| [`superficie_total`](GEOPHIS.md#geometriapy-vector) | Superficie total en la unidad pedida, con cualquier CRS |
| [`comprobar_particion`](GEOPHIS.md#geometriapy-vector) | Comprueba que `capas` PARTEN `zona` |
| [`longitud_total_m`](GEOPHIS.md#geometriapy-vector) | Longitud total en m, sin redondear; falla si el CRS no va en metros |
| [`longitud_total`](GEOPHIS.md#geometriapy-vector) | Longitud total en la unidad pedida, con cualquier CRS |
| [`concordancia_lineas`](GEOPHIS.md#geometriapy-vector) | Precisión y cobertura de una red contra otra, por escalera de `tol`. El juez no puede ser insumo |
| [`bloques_ajedrez`](GEOPHIS.md#geometriapy-vector) | Cuadros alternados `pliegue` 0/1 para hold-out espacial |
| [`pendiente_sostenida`](GEOPHIS.md#geometriapy-vector) | `(separacion_m, pendiente_pct)` que la cartografía sostiene dentro de `zona` |
| [`ancho_banda`](GEOPHIS.md#geometriapy-vector) | El ancho transversal medido `w` de la clase más alta, por escalera de cortes -> `(tabla, resumen)`; con `devolver_banda=True` también la banda como geometría |
| [`sostenimiento_por_clase`](GEOPHIS.md#geometriapy-vector) | Qué separación sostiene la cartografía en cada clase ACUMULADA (`>= p`), de una sola clasificación |
| [`separacion_media`](GEOPHIS.md#geometriapy-vector) | `(lambda_m, longitud_efectiva_m, factor_zigzag)`. λ por `G'(0)` de la escalera de buffers, no por el cociente global (sesga hacia lo llano) |
| [`auditar_rangos`](GEOPHIS.md#geometriapy-vector) | Audita cada rango contra un techo cartográfico externo al MDE |
| [`intersecar`](GEOPHIS.md#geometriapy-vector) | *Intersecar* (Intersect): `overlay` de intersección; una parte por cada par de entidades que se pisan |
| [`union_espacial`](GEOPHIS.md#geometriapy-vector) | *Unión espacial*, sin cortar geometrías |
| [`entidad_a_punto`](GEOPHIS.md#geometriapy-vector) | *De entidad a punto*: un punto por entidad con atributos; por defecto siempre dentro, que NO es el centroide |
| [`cercano`](GEOPHIS.md#geometriapy-vector) | *Near*: `DIST_M` e `ID_CERCA` de la entidad más próxima; una fila por entidad aunque haya empate |
| [`puntos_sobre_linea`](GEOPHIS.md#geometriapy-vector) | Cadenamiento: puntos cada `intervalo` con `CADENA_M`, incluido el final |
| [`identidad`](GEOPHIS.md#geometriapy-vector) | *Identity*: `gdf` partida donde la pisa `otro`, con atributos de las dos |
| [`union`](GEOPHIS.md#geometriapy-vector) | *Unión* (Union): las dos capas partidas en sus cruces, con atributos de las dos |
| [`thiessen`](GEOPHIS.md#geometriapy-vector) | Polígonos de Voronoi recortados a `zona`, atributos por contención |
| [`partir_por_linea`](GEOPHIS.md#geometriapy-vector) | Parte polígonos con líneas (no con polígonos); avisa si una línea no atraviesa |
| [`calcular_longitud`](GEOPHIS.md#geometriapy-vector) | Añade longitud por geometría (mismo redondeo que `calcular_superficie`); sin `campo` el nombre es `longitud_<unidad>` |
| [`calcular_coordenadas`](GEOPHIS.md#geometriapy-vector) | Añade `LAT`/`LON`, `X`/`Y` o `LAT_GMS`/`LON_GMS` de cada punto, en el CRS pedido |
| [`medir_geodesico`](GEOPHIS.md#geometriapy-vector) | Añade `SUP_GEO` (ha) y `LONG_GEO` (m) sobre el elipsoide WGS84 e imprime la diferencia contra el plano |
| [`simplificar`](GEOPHIS.md#geometriapy-vector) | Simplifica la cobertura de polígonos sin romper la partición; líneas por Douglas-Peucker |
| [`cerrar_microhuecos`](GEOPHIS.md#geometriapy-vector) | Cierra rendijas entre vecinos sin invadir al vecino |
| [`rellenar_huecos`](GEOPHIS.md#geometriapy-vector) | Quita los huecos de DENTRO de cada polígono (los anillos interiores) |
| [`subdividir_por_area`](GEOPHIS.md#geometriapy-vector) | Parte polígonos grandes por bisección |
| [`eliminar_menores`](GEOPHIS.md#geometriapy-vector) | *Eliminar* (Eliminate): absorbe los polígonos chicos en su mejor vecino |
| [`ruta_red`](GEOPHIS.md#geometriapy-vector) | Ruta más rápida sobre una red de líneas, con velocidad por tipo de vía y sentidos únicos; campos `METROS` y `MINUTOS` |
| [`isocronas`](GEOPHIS.md#geometriapy-vector) | Lo que se alcanza por la red en menos de cada corte de tiempo, desde o hacia uno o varios orígenes; líneas o polígonos con `ancho` |
| [`nombre_campo_libre`](GEOPHIS.md#geometriapy-vector) | Nombre de columna sin colisión |

## [raster.py (trabajan sobre arrays 2D numpy + transform/crs)](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs)

| Función | Qué hace |
|---|---|
| [`cargar_raster`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Devuelve `(array, transform, crs, resolucion_m)` |
| [`cargar_bandas`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Devuelve la misma tupla de 4, con el array en 3D `(banda, y, x)` en el orden pedido |
| [`guardar_raster`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Array 2D, 3D o lista de 2D → GeoTIFF float32 con NoData NaN; `nombres=` por banda |
| [`recortar_raster`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Devuelve `(array, transform, crs, resolucion_m)` |
| [`mosaico`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Junta varios rásters de una banda (rutas o URL de COG) leyendo solo el rectángulo del predio; el orden es la prioridad |
| [`rellenar_nodata`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | NaN ← píxel válido más cercano |
| [`mascara_relleno`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Booleana, celdas cuya ventana 3x3 contaminó `rellenar_nodata` (la zona rellenada es meseta plana al 0% sin marcar) |
| [`remuestrear`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Devuelve `(array, transform)` |
| [`reproyectar_raster`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Devuelve `(array, transform)` |
| [`indice_vegetacion`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Devuelve array float |
| [`interpolar_mde`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Curvas de nivel vectoriales → MDE continuo → `(array, transform)` |
| [`planos_de_curva`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Máscara de las mesetas falsas del TIN (celdas planas a la cota exacta de una curva) |
| [`cotas_por_cruce`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Da z a líneas sin cota (cauces) por sus cruces con las curvas; son los quiebres de `interpolar_mde(quiebres=)` |
| [`validar_mde`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | *hold-out* sobre `interpolar_mde`: aparta al azar `porcentaje_reserva` de los puntos, interpola con el resto y compara |
| [`rasterizar`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Vector→máscara |
| [`extraer_valores`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Devuelve array float, un valor por punto y en el mismo orden |
| [`combinar_categorias`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Empaqueta 2 rasters (`a*factor+b`; recuperar con `//` y `%`) |
| [`poligonizar`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Raster→polígonos |
| [`limpiar_moteado`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Sieve de GDAL: absorbe las regiones menores a `min_pixeles` en su vecino mayor |
| [`resumen_raster`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Dict de control (n, NaN, min, max, media, percentiles, `ha_sin_dato`); todo NaN levanta error |
| [`sombreado`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Hillshade (fórmula de Burrough) sobre la derivada de Horn, uint8 0-255 |
| [`estadisticas_zonales`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Estadística zonal en tabla: una fila por polígono, con `n_celdas` y `n_nan` |
| [`alinear_rasters`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Lleva rasters a la malla de uno de referencia; después `a - b` ya es álgebra de mapas |
| [`curvas_de_nivel`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | MDE → curvas LineString con `campo` de cota |
| [`vista_previa`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | PNG en grises para mirar un array, una capa o la capa encima del array; no es un mapa |
| [`calcular_orientacion`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | *Orientación* (aspect): Horn 3x3 igual que `calcular_pendiente`, misma regla de NoData 7 de 8 (centro sin dato o <7 vecinos válidos → sin orientación). El 0 junta NoData y plano exacto (gradiente < 1e-9, cero numérico, no umbral) |
| [`calcular_pendiente`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Pendiente por píxel, algoritmo planar de Horn, ventana 3x3 |
| [`ruta_a_pie`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Ruta a pie más rápida sobre el MDE, costo anisótropo (Tobler por paso, con signo); campo `HORAS` |
| [`ruta_costo_minimo`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Línea de costo mínimo entre dos puntos sobre una superficie de costo; NaN = intransitable |
| [`reclasificar_rangos`](GEOPHIS.md#rasterpy-trabajan-sobre-arrays-2d-numpy-transformcrs) | Reclasifica un raster continuo en códigos por rangos (genérica) |

## [hidrologia.py (todo pysheds. Encima de `raster`: solo usa `rellenar_nodata`, `poligonizar` y `rasterizar`)](GEOPHIS.md#hidrologiapy-todo-pysheds-encima-de-raster-solo-usa-rellenar_nodata-poligonizar-y-rasterizar)

| Función | Qué hace |
|---|---|
| [`quemar_cauces`](GEOPHIS.md#hidrologiapy-todo-pysheds-encima-de-raster-solo-usa-rellenar_nodata-poligonizar-y-rasterizar) | Devuelve `(array, n_píxeles)` |
| [`acondicionar_mde`](GEOPHIS.md#hidrologiapy-todo-pysheds-encima-de-raster-solo-usa-rellenar_nodata-poligonizar-y-rasterizar) | Devuelve `(grid, mde_acondicionado)` |
| [`acumulacion_flujo`](GEOPHIS.md#hidrologiapy-todo-pysheds-encima-de-raster-solo-usa-rellenar_nodata-poligonizar-y-rasterizar) | Devuelve array |
| [`rellenar_sumideros`](GEOPHIS.md#hidrologiapy-todo-pysheds-encima-de-raster-solo-usa-rellenar_nodata-poligonizar-y-rasterizar) | *Rellenar* (Fill): devuelve `(mde, informe)`; termina el relleno |
| [`altura_sobre_cauce`](GEOPHIS.md#hidrologiapy-todo-pysheds-encima-de-raster-solo-usa-rellenar_nodata-poligonizar-y-rasterizar) | HAND: altura sobre el cauce con cotas del MDE base; devuelve array |
| [`etiquetar_laderas`](GEOPHIS.md#hidrologiapy-todo-pysheds-encima-de-raster-solo-usa-rellenar_nodata-poligonizar-y-rasterizar) | Laderas separadas por el cauce, no cuencas de drenaje: devuelve `(array, n_regiones)` |
| [`red_drenaje`](GEOPHIS.md#hidrologiapy-todo-pysheds-encima-de-raster-solo-usa-rellenar_nodata-poligonizar-y-rasterizar) | Devuelve GeoDataFrame de LineStrings en el CRS del ráster |
| [`orden_cauces`](GEOPHIS.md#hidrologiapy-todo-pysheds-encima-de-raster-solo-usa-rellenar_nodata-poligonizar-y-rasterizar) | Devuelve array int32 |
| [`delimitar_cuenca`](GEOPHIS.md#hidrologiapy-todo-pysheds-encima-de-raster-solo-usa-rellenar_nodata-poligonizar-y-rasterizar) | Devuelve GeoDataFrame con UN polígono |
| [`microcuencas`](GEOPHIS.md#hidrologiapy-todo-pysheds-encima-de-raster-solo-usa-rellenar_nodata-poligonizar-y-rasterizar) | Devuelve GeoDataFrame con UNA fila por desagüe |

## [satelite.py (imagen Sentinel-2 por Earth Search: sin cuenta, sin token, sin dependencias nuevas. Encima de `raster`)](GEOPHIS.md#satelitepy-imagen-sentinel-2-por-earth-search-sin-cuenta-sin-token-sin-dependencias-nuevas-encima-de-raster)

| Función | Qué hace |
|---|---|
| [`buscar_sentinel2`](GEOPHIS.md#satelitepy-imagen-sentinel-2-por-earth-search-sin-cuenta-sin-token-sin-dependencias-nuevas-encima-de-raster) | Escenas Sentinel-2 L2A que pisan el predio, una por fecha, de menos a más nubes, con su cobertura del predio |
| [`imagen_sentinel2`](GEOPHIS.md#satelitepy-imagen-sentinel-2-por-earth-search-sin-cuenta-sin-token-sin-dependencias-nuevas-encima-de-raster) | Bandas en reflectancia de la fecha más limpia (relleno con otras si hace falta), en español para `indice_vegetacion` |

## [metrologia.py (las REGLAS: aritmética pura, sin rasters. Capa de abajo; aquí nada mide)](GEOPHIS.md#metrologiapy-las-reglas-aritmetica-pura-sin-rasters-capa-de-abajo-aqui-nada-mide)

| Función | Qué hace |
|---|---|
| [`min_pixeles_umm`](GEOPHIS.md#metrologiapy-las-reglas-aritmetica-pura-sin-rasters-capa-de-abajo-aqui-nada-mide) | El `min_pixeles` de `limpiar_moteado`, `ceil(umm_m2/h²)` |
| [`umbral_celdas`](GEOPHIS.md#metrologiapy-las-reglas-aritmetica-pura-sin-rasters-capa-de-abajo-aqui-nada-mide) | El `umbral` de `red_drenaje`, `orden_cauces` y `delimitar_cuenca`, `ceil(area_ha·10000/h²)` |
| [`tolerancia_rasterizacion_ha`](GEOPHIS.md#metrologiapy-las-reglas-aritmetica-pura-sin-rasters-capa-de-abajo-aqui-nada-mide) | Cuánto puede discrepar de su polígono una capa que pasó por una máscara de celdas, `P·h/2/10000` |
| [`margen_borde_celdas`](GEOPHIS.md#metrologiapy-las-reglas-aritmetica-pura-sin-rasters-capa-de-abajo-aqui-nada-mide) | El `margen_borde_celdas` de `cuadros_mde`, `ceil(sqrt(n·i·s/pi)/h)+2` |
| [`resolucion_regla`](GEOPHIS.md#metrologiapy-las-reglas-aritmetica-pura-sin-rasters-capa-de-abajo-aqui-nada-mide) | La resolución `h` de un MDE de pendientes -> `(h, factible, cotas)`; `cotas` = `{umm, deteccion, trazado}` y, solo con `lambda_m`, `antialias` |

## [zonas.py (ráster continuo -> polígonos por rangos. Capa de arriba: usa vector y ráster a la vez)](GEOPHIS.md#zonaspy-raster-continuo---poligonos-por-rangos-capa-de-arriba-usa-vector-y-raster-a-la-vez)

| Función | Qué hace |
|---|---|
| [`zonificar`](GEOPHIS.md#zonaspy-raster-continuo---poligonos-por-rangos-capa-de-arriba-usa-vector-y-raster-a-la-vez) | Ráster continuo -> polígonos por rangos con `gridcode`, clase y `SUP`; sieve y recorte incluidos |

## [lamina.py (PNG de revisión con color, leyenda, escala y cita. Capa de arriba: usa vector y ráster a la vez)](GEOPHIS.md#laminapy-png-de-revision-con-color-leyenda-escala-y-cita-capa-de-arriba-usa-vector-y-raster-a-la-vez)

| Función | Qué hace |
|---|---|
| [`panel_rgb`](GEOPHIS.md#laminapy-png-de-revision-con-color-leyenda-escala-y-cita-capa-de-arriba-usa-vector-y-raster-a-la-vez) | Panel en color natural con `rojo`, `verde` y `azul` de `imagen_sentinel2`; estirado sobre las tres bandas juntas |
| [`panel_continuo`](GEOPHIS.md#laminapy-png-de-revision-con-color-leyenda-escala-y-cita-capa-de-arriba-usa-vector-y-raster-a-la-vez) | Panel de un continuo (NDVI, pendiente, dNDVI) con barra de color; paleta `gris`, `divergente` (0 al centro) o `vegetacion` |
| [`panel_clases`](GEOPHIS.md#laminapy-png-de-revision-con-color-leyenda-escala-y-cita-capa-de-arriba-usa-vector-y-raster-a-la-vez) | Panel de los códigos de `reclasificar_rangos` con leyenda por clase |
| [`lamina`](GEOPHIS.md#laminapy-png-de-revision-con-color-leyenda-escala-y-cita-capa-de-arriba-usa-vector-y-raster-a-la-vez) | Junta paneles de la misma malla en un PNG de revisión con título, leyenda, escala, norte y cita (obligatoria, `None` sin Sentinel-2) |
| [`rotulo_escenas`](GEOPHIS.md#laminapy-png-de-revision-con-color-leyenda-escala-y-cita-capa-de-arriba-usa-vector-y-raster-a-la-vez) | `"03-oct-2026 (64.9 %) y 05-oct-2026 (2.4 %)"` desde el `informe` de `imagen_sentinel2` |
| [`cita_sentinel2`](GEOPHIS.md#laminapy-png-de-revision-con-color-leyenda-escala-y-cita-capa-de-arriba-usa-vector-y-raster-a-la-vez) | La cita de Copernicus con los años de las fechas usadas |

## [barridos.py (lo que MIDE sobre el MDE y la cartografía; capa de arriba, usa vector y ráster a la vez. Ninguna decide: reportan)](GEOPHIS.md#barridospy-lo-que-mide-sobre-el-mde-y-la-cartografia-capa-de-arriba-usa-vector-y-raster-a-la-vez-ninguna-decide-reportan)

| Función | Qué hace |
|---|---|
| [`barrer_resolucion`](GEOPHIS.md#barridospy-lo-que-mide-sobre-el-mde-y-la-cartografia-capa-de-arriba-usa-vector-y-raster-a-la-vez-ninguna-decide-reportan) | Corre la cadena de pendientes completa a varios `h` y tabula cómo se mueve la clase más alta -> lista de dicts (de fino a grueso) con `h`,... |
| [`barrer_vecinos`](GEOPHIS.md#barridospy-lo-que-mide-sobre-el-mde-y-la-cartografia-capa-de-arriba-usa-vector-y-raster-a-la-vez-ninguna-decide-reportan) | La RODILLA de `vecinos` -> `(filas, n_rodilla)` |
| [`raster_distancia`](GEOPHIS.md#barridospy-lo-que-mide-sobre-el-mde-y-la-cartografia-capa-de-arriba-usa-vector-y-raster-a-la-vez-ninguna-decide-reportan) | `(distancia, mascara, transform)`, metros a la curva más cercana en cada celda de una malla sobre `zona` |
| [`separaciones`](GEOPHIS.md#barridospy-lo-que-mide-sobre-el-mde-y-la-cartografia-capa-de-arriba-usa-vector-y-raster-a-la-vez-ninguna-decide-reportan) | `(tabla, resumen)` con la distribución de separaciones dentro de `mascara`, leída del ráster de `raster_distancia` |
| [`juzgar_clasificaciones`](GEOPHIS.md#barridospy-lo-que-mide-sobre-el-mde-y-la-cartografia-capa-de-arriba-usa-vector-y-raster-a-la-vez-ninguna-decide-reportan) | Juzga N clasificaciones del mismo predio contra la carta (criterio por método, % del escarpe visto) y recomienda una |
| [`histograma_fase`](GEOPHIS.md#barridospy-lo-que-mide-sobre-el-mde-y-la-cartografia-capa-de-arriba-usa-vector-y-raster-a-la-vez-ninguna-decide-reportan) | Reparto de la fase del muestreo entre curvas: dice si el MDE ve el terreno o sólo las curvas |
| [`amplitud_rizo`](GEOPHIS.md#barridospy-lo-que-mide-sobre-el-mde-y-la-cartografia-capa-de-arriba-usa-vector-y-raster-a-la-vez-ninguna-decide-reportan) | `(a, detalle)`, PISO de la amplitud `A` del rizo del interpolador, en metros |

## [parametros.py (parámetros con PROCEDENCIA. Capa de arriba: encadena todo lo anterior)](GEOPHIS.md#parametrospy-parametros-con-procedencia-capa-de-arriba-encadena-todo-lo-anterior)

| Función | Qué hace |
|---|---|
| [`derivar_parametros`](GEOPHIS.md#parametrospy-parametros-con-procedencia-capa-de-arriba-encadena-todo-lo-anterior) | Encadena las mediciones y las reglas, y devuelve cada parámetro con su procedencia |
| [`sin_medir`](GEOPHIS.md#parametrospy-parametros-con-procedencia-capa-de-arriba-encadena-todo-lo-anterior) | Los nombres del dict anterior cuya fuente es `default`, o sea los que nadie eligió |
| [`tabla_procedencia`](GEOPHIS.md#parametrospy-parametros-con-procedencia-capa-de-arriba-encadena-todo-lo-anterior) | Rinde (e imprime) la tabla `parametro / valor / fuente / origen`, más el bloque de `SIN MEDIR`, el aviso de `INFACTIBLE` y la pendiente... |
| [`comprobar_cifras_firmes`](GEOPHIS.md#parametrospy-parametros-con-procedencia-capa-de-arriba-encadena-todo-lo-anterior) | Compara lo medido contra las cifras firmes del predio y falla si no las reproduce |

---

Índice derivado de `GEOPHIS.md`. `tests/test_docs.py` comprueba que todas las funciones de `__all__` estén aquí, así que una función nueva sin fila hace fallar la suite.