# geophis: decisiones y evidencia

Este documento explica **por qué** geophis hace las cosas como las hace. Cada regla de la librería salió de un error real o de una medición, y aquí está contado ese origen: qué pasaba antes, qué se midió, qué se decidió y qué alternativas se probaron y se descartaron.

## Para quién es

- **Para quien mantiene la librería.** Antes de cambiar una regla que parece rara o excesiva, busca aquí por qué existe. Casi todas protegen contra un defecto que ya pasó y que no avisaba.
- **Para quien tiene que defender una cifra.** Si en una memoria técnica te preguntan por qué la clase de pendiente más alta no pasa por el filtro de manchas, o por qué se usó un TIN y no un spline, la medición que lo respalda está aquí.
- **Para no repetir pruebas.** La última sección resume lo que ya se probó y no sirvió.

La versión anterior de este documento, con el detalle función por función, está en el historial de git (`git show f21d5c3:docs/DECISIONES.md`).

No es una guía de uso. Para escribir un script, la referencia es `GEOPHIS.md`, que tiene las firmas de las funciones, las recetas y las reglas en forma corta. Este documento no se carga en el Proyecto de Claude: duplicaría el peso sin ayudar a escribir scripts.

**Sobre las cifras.** "Un predio real" es un predio de trabajo de la región, medido con la cartografía estatal de curvas de nivel (equidistancia de 20 m) y la red hidrográfica estatal. Los predios van de 1 665 a 9 094 ha. Las cifras se dan para que se entienda el tamaño del efecto, no como constantes.

## Glosario mínimo

- **Equidistancia (e):** el salto de cota entre dos curvas de nivel, 20 m en la cartografía de trabajo.
- **Separación (λ):** la distancia horizontal media entre curvas vecinas. Manda sobre casi todo: el tamaño de píxel útil, cuánto ondula un spline, cuánto muestrear.
- **UMM:** unidad mínima de mapeo, la mancha más chica que se entrega. Sale de la escala: 2 mm en el papel, o sea 2 500 m² a 1:25 000.
- **h:** el tamaño de píxel del modelo digital de elevación (MDE).
- **Techo y piso de h:** un techo es un valor que `h` no debe superar (si no, la clase no se ve); un piso, uno que no debe bajar (si no, aparecen pendientes falsas).
- **w:** el ancho real de la franja de pendiente más alta (la "cinta" del escarpe), medido sobre la carta.
- **Sieve:** el filtro que absorbe las manchas menores a la UMM en su vecina más grande.
- **La carta:** las curvas de nivel originales. Es el juez externo: lo que dicen las curvas no depende de cómo se interpoló el MDE.
- **TIN, spline, malla:** los tres interpoladores. El TIN une puntos de las curvas con triángulos planos; el spline ajusta una superficie suave alrededor de cada celda; la malla ajusta una sola superficie suave para todo el predio.

## 1. Ningún número sin procedencia

### Qué se decidió

Un script no escribe a mano ninguna cantidad que se pueda medir. Las mide `derivar_parametros`, que devuelve cada valor con su **fuente** (declarado, medido, derivado, convención o default) y su **origen**, y esa tabla de procedencia va al entregable.

### Por qué

Al pasar un cálculo de un script a otro se perdieron cuatro decisiones que nadie había escrito, y la misma clase de pendiente pasó de **14.86 ha a 4.35 ha** sin que nada avisara. Con `h` puesto a ojo bajó a 1.68 ha. Las cuatro decisiones eran:

1. La UMM derivada de la escala, y no puesta a ojo.
2. El tamaño de píxel calculado con el ancho **medido** de la cinta (`w` = 15.5 m) y no con el geométrico (20 m): `h` pasaba de 5.17 a 6.67 m.
3. El número de vecinos del spline medido, y no el 48 por defecto.
4. La clase más alta exenta del filtro de manchas.

Un `48` escrito a mano y un `48` medido se ven igual en el código, en la consola y en el mapa. La única forma de distinguirlos es que el número lleve su procedencia.

### Detalles que costaron

- **El default de vecinos (48) se llevó el 12 % de la clase alta.** No estaba escrito en ninguna parte del script que lo usó. Con la rodilla medida (90), ese 12 % desapareció: era terraceo, no terreno.
- **Convertir un valor a `int` o `float` borra su procedencia.** Una segunda llamada recibía el `48` ya convertido y lo marcaba como medido: la tabla del entregable decía "48 medido" sin que nadie hubiera medido nada. Por eso un valor que ya trae procedencia se respeta tal cual.
- **Convención y default se separan.** Las convenciones de oficio (2 mm de lado mínimo, 4 píxeles por UMM, 3 celdas para trazar, cuantil 90) son iguales en todos los predios y no hay medición que las sustituya. Si salieran en la lista de "sin medir", esa lista tendría cinco nombres en cada corrida y se dejaría de mirar.
- **Lo medido depende de sobre QUÉ capa se midió.** La misma carta recortada al predio da un blanco de 10.28 ha, y recortada al cuadro de trabajo da 16.14. Por eso la tabla guarda la huella de las capas (entidades, CRS, extensión).
- **Medir cuesta, y se repetía.** Cada llamada a `derivar_parametros` volvía a medir la separación, el zigzag y el ancho de la cinta: entre 145 y 192 s. Con dos métodos y la segunda pasada eran cuatro llamadas. Ahora una llamada puede reusar lo medido por otra (`medidos=`): 0.7 s, y falla si las capas no son las mismas.

### Cifras firmes de un predio

Una vez que la primera corrida de un predio se revisa a mano, sus cifras se fijan en el script y cualquier cambio posterior que las mueva levanta un error. Con eso, la caída de 14.86 a 4.35 ha habría salido como error y no como una tabla nueva igual de creíble. Pero una cifra firme también puede ser un defecto que nadie miró: un faltante de **0.22 ha** se citó durante ocho versiones como "redondeo" y era el hueco del filtro de manchas (sección 3).

## 2. Superficies que cuadran

### El área se calcula al final

La superficie de cada polígono se calcula después del último paso que mueve la geometría, no solo después de recortar y reproyectar. `borrar` y `resolver_por_prioridad` recortan al final de la cadena y dejaban la columna de área desactualizada. Con la longitud pasó lo mismo en hidrología: calcular la longitud y luego recortar dejaba la columna diciendo **71.0 km sobre una geometría de 60.7 km** (14 % de más). `guardar` ahora avisa cuando la columna no cuadra.

### La comprobación de partición mide tres cosas por separado

Comprobar que las clases cubren el predio exige medir aparte el hueco (predio sin cubrir), el exceso (lo que se sale) y el doble conteo (lo que está en dos clases). Una cifra neta compensa: 0.5 ha sin cubrir dentro y 0.5 ha fuera dan cero. En un predio real el cierre neto daba **0.04 ha** mientras la superficie realmente no cubierta era **1.30 ha**. Y en el predio de referencia el doble conteo valía 0.0000 ha exactas mientras el hueco valía 0.22: una guarda que solo mirara el solape habría dado la capa por buena.

La tolerancia no tiene valor por defecto. Una tolerancia de 1 ha, heredada de un ejemplo, dejó pasar el hueco de 0.22 ha durante ocho versiones.

### Cuando la capa viene de un ráster

Una capa que pasó por una malla de celdas tiene el borde en escalera, y discrepa de su polígono hasta media celda a cada lado. La tolerancia correcta es el perímetro por media celda (`tolerancia_rasterizacion_ha`), que depende de lo dentado del contorno y no del área. **Recortar contra el polígono no arregla el cierre, lo empeora** (de 0.04 a 1.30 ha en un predio real): quita lo que sobra pero no puede añadir lo que la malla nunca cubrió.

### Otras decisiones de geometría

- **Máscaras con todas las celdas tocadas.** Si una máscara solo pinta las celdas cuyo centro cae dentro, la celda mordida por el lindero no se poligoniza nunca y la capa cierra con menos superficie que el predio. La fuga es de perímetro por píxel entre 8.
- **Simplificar polígonos como cobertura.** Simplificando cada polígono por su cuenta salían **40.03 ha de hueco y 29.81 ha de solape** en un predio real. Simplificar la cobertura completa resuelve eso, pero hoy falla sobre la salida de la poligonización (330 polígonos rechazados en un predio y 154 en otro): rasterio no pone vértice en las juntas en T. Se arregla con una función de shapely 2.2.
- **Cerrar rendijas por la unión, no por polígono.** Un cierre morfológico polígono por polígono invadía al vecino: 261 ha de solape.
- **La cadena del índice de vegetación sin simplificar ni cerrar rendijas.** Con esos dos pasos metía **303.74 ha de doble conteo** sin avisar.
- **Reparar geometrías con `make_valid`, no con `buffer(0)`**: en un polígono en forma de moño, `buffer(0)` se queda con media superficie.
- **El más cercano, sin duplicar.** El join al más cercano devuelve una fila por empate, y una capa pasó de **61 a 2 413 filas** sin avisar. `cercano` deja una fila por entidad y cuenta los empates.
- **Seleccionar por ubicación no es un join.** El join espacial duplica la fila si dos entidades la tocan; para filtrar, inflaba el total en silencio.

## 3. Clases de pendiente

### La clase más alta es una cinta, no una mancha

La pendiente más empinada (más de 100 %) aparece en los escarpes como una franja angosta y larga. Sobre una banda de 15.5 m de ancho, alcanzar una UMM de 0.25 ha exige 162 m continuos. Aplicar la UMM por superficie borraba entre el **47 y el 55 % del escarpe**, que es terreno real. Por eso esa clase queda exenta del filtro de manchas y la UMM se aplica como ancho mínimo: la cifra que se entrega es un piso defendible, y esa salvedad va pegada a la cifra.

### Una sola pasada del filtro

La receta anterior pasaba el filtro de manchas a todas las clases, luego a ninguna, y se quedaba con la clase alta de la versión sin filtrar. No era una partición: el filtro no borra, **reasigna** cada mancha chica a su vecina mayor, y así metía en la clase alta píxeles que en la versión sin filtrar eran de otra clase. Esos píxeles no estaban en ninguna de las dos capas y desaparecían. En el segundo predio en que se aplicó dejó **43.21 ha de hueco** (0.9 % del predio). En el primero el hueco era casi cero y la receta pasó por buena.

La solución es una sola pasada con la clase alta exenta: ni absorbe ni es absorbida, sale idéntica píxel a píxel y no hay hueco ni doble conteo.

### El ancho de la cinta se mide

Sin medirlo, el ancho de la clase alta se calcula como equidistancia entre pendiente (20 m para 100 %). Medido sobre la carta, reconstruyendo la banda donde las curvas están más juntas que esa separación, dio **15.5 m**, y con eso el tamaño de píxel bajó de 6.67 a 5.17 m. La medición también da el área que la carta sostiene en esa clase: el **blanco cartográfico** contra el que se compara el MDE.

Dos cuidados: el zigzag del trazo se cancela en el ancho pero manda en el área (pasar zigzag 1.0 por descuido infla el blanco por el factor entero), y el largo de los tramos de la banda es largo de trazo, que hay que dividir entre el zigzag (con zigzag 1.408, olvidarlo son 40 puntos porcentuales de "escarpe visto"). Y la escalera de cotas se toma de las curvas completas, antes del recorte: si no, dos niveles separados 2e quedan como contiguos y aparece una banda que no existe.

### Cuántos píxeles mide la UMM

`min_pixeles` se redondea hacia arriba porque la UMM es un mínimo (redondear dejaría pasar manchas de 361 m² con UMM de 400). Es un entero que salta de golpe al cambiar `h`, y como el filtro absorbe en vez de borrar, endurecerlo puede **agrandar** una clase: a `h` = 19 m, la clase alta medía 1 491.89 ha con 1 píxel mínimo y 1 733.38 con 2.

### Qué corte se puede entregar

`sostenimiento_por_clase` mide, para cada corte acumulado (≥ 35, ≥ 45...), la separación que sostienen las curvas dentro de esa clase. Un corte se entrega si el píxel cabe en el objeto y además no está bajo el piso de pendiente falsa. Dos hallazgos:

- **La separación sostenida es estable al 1 % frente a `h`** (dos predios, tres cortes, `h` de 20 a 52 m): el ancho del objeto es del terreno, no del píxel. Basta una interpolación.
- **El piso se calcula con la separación de ESA clase, no con la global.** En un predio mayormente suave, el piso global describe el llano: 37.0 m contra 15.5 m locales, un factor de 2.4, y con el global se condenaba una clase que sí era resoluble.

La condición es necesaria y no suficiente: un MDE externo de 15 m cumplía `9.6 ≤ 15 ≤ 19.2` y perdía el 90 % de la banda.

### El escarpe visto

Para comparar métodos se mide qué fracción del largo del escarpe de la carta cae dentro de la clase alta del MDE. Se citó así en un predio: "el MDE ve el 56 % del escarpe (10 436 m efectivos)". Comparar áreas premiaría al método que dibuja la cinta más ancha.

## 4. Del vector al MDE

### La separación entre curvas se mide por G'(0)

La separación media se calculaba como área entre longitud de curvas. El zigzag del trazo la sesga a la baja: 59.36 m contra **74.93 m** medidos por la derivada en cero de la escalera de buffers, un 26 % menos, y con el valor sesgado la clase de más de 100 % daba 7.16 ha en vez de 4.37. La separación así medida es una media armónica dominada por lo empinado, y subestima los llanos.

### Separación sobre el extent, zigzag sobre el predio

Son dos mediciones con dominios distintos. La separación gobierna cómo ondula el spline, que se construye sobre todo el extent del MDE; el zigzag convierte un muestreo de terreno en uno de trazo y debe describir las curvas del predio. Sacarlas de la misma pasada deja una en el dominio equivocado: la separación da 76.15 m sobre el polígono, 74.90 sobre su rectángulo y 68.34 sobre el rectángulo más 1 500 m; el zigzag, 1.413 sobre el polígono y 1.26 sobre el rectángulo (10 % en el muestreo, 12 % en el blanco).

### El tamaño de píxel: techos y piso

`resolucion_regla` junta tres techos (que la UMM quepa en 4 píxeles, detectar la cinta, trazarla con 3 celdas cruzando su ancho) y un piso que solo aplica al spline. Las decisiones:

- **El piso existe porque el spline ondula.** Entre curvas, la superficie forma un rizo con la longitud de onda de la separación. La pendiente se calcula con una ventana de 3 × 3 celdas (2h de ancho), y ese rizo entra como pendiente falsa `A·sin(2πh/λ)/h`, máxima cuando `h` ≈ λ/4. Con λ = 38 m, `h` = 10 m inventaba **57 % de pendiente** sobre 53 % real; `h` = 20 m la bajaba a ~5 %. El piso es λ/2.
- **Cuando el piso supera al techo, gana el piso.** No hay `h` que cumpla los dos. Con `h` en el piso, la clase alta no se puede trazar a la UMM pedida, y eso se reporta. Con `h` en el techo, el rizo fabrica superficie en esa misma clase y se cita como terreno. Las pérdidas no son simétricas.
- **Con la amplitud del rizo medida, el piso deja de mandar.** El piso es un sustituto para cuando no se conoce `A`. Con `A` medida se calcula la pendiente falsa y se compara contra la mitad del ancho de la clase más estrecha. En dos predios: uno con `A` = 0.00 m da 0.00 % y pasa; otro con `A` = 16.67 m da 245.73 % y no pasa. Sin `A`, el piso gritaba "infactible" en los dos.
- **El criterio tampoco es suficiente.** Con la misma pendiente falsa de 4.07 %, los píxeles que cambian de clase van de 0.00 % (si la pendiente real está lejos del límite) a 52.81 % (si está sobre él).
- **`h` se redondea a un valor limpio, en la dirección segura.** Un techo hacia abajo (5.17 → 5 m) y un piso hacia arriba (18.95 → 20 m). Al más cercano, un piso de 22 caería a 20, debajo de sí mismo. Son las decisiones que se venían tomando a mano en cada predio.
- **El intervalo de muestreo no se redondea**: la escalera de valores limpios salta de 50 a 100 con el corte en 75.0, y una separación de 74.93 decidiría el muestreo por centímetros.

### Los vecinos del spline se miden

El spline ajusta una superficie con los `n` puntos más cercanos a cada celda. Pocos vecinos aterrazan la superficie contra las curvas; más vecinos lo quitan hasta que la clase alta deja de moverse. La fórmula de tamaño solo da el orden de magnitud (14, 120 o 141 en la misma zona según qué separación se le meta). Se mide con un barrido que para en cuanto dos `n` seguidos dan la misma cifra (la "rodilla"): en un predio a 20 m tardó 8 min y la rodilla fue 64; en otro a 5 m, 50 min y 90.

Si el barrido no converge, o si la clase alta mide 0 ha a esa resolución, no hay rodilla y `vecinos` queda marcado como no medido. Una rodilla sin convergencia es "nadie lo eligió" con otro nombre.

El álgebra lineal del spline corre a un hilo a propósito: con varios hilos, `n` = 122 costaba 6 veces `n` = 90.

### Los cuadros del MDE

Las curvas se recortan a un cuadro más amplio que el predio, para que las celdas del borde se interpolen con datos a los dos lados. El margen sale del vecindario del spline (`ceil(sqrt(n·i·s/π)/h)+2`, con la separación del VACÍO, el doble del cuantil 90, no la mediana: con la mediana salían 40 m donde iban 400). El MDE de salida va dos celdas más ancho que el predio: la ventana de 3 × 3 deja sin dato la celda del borde, y sin ese margen la capa perdía un anillo (0.80 de 2.16 ha en la ladera de prueba).

### El sin dato no se tapa

Fuera de la envolvente de las curvas el interpolador devuelve sin dato. Rellenarlo copia el vecino más cercano y deja una meseta al 0 % sin marcar: superficie inventada que sale en la tabla como terreno llano. Por eso el aviso de "sin dato" se imprime siempre, aunque se apaguen los reportes, y `zonificar` entrega ese hueco como clase `SIN DATO` (188 ha en una costa sin curvas, que antes rompían la comprobación de partición).

### Curvas mal capturadas

Una sola curva con cota errónea hace un pico falso. En la carta estatal hay una de 3 302 m entre curvas de 1 180 m, y en hidrología produjo alturas sobre cauce de hasta 2 982 m. `detectar_cota` avisa siempre de cotas fuera de escalera. También tuvo que aprender a no elegir una columna a medias: en una costa, una columna de texto vacía en el 56 % de las curvas ganaba a la buena, y se habría descartado la mitad de las curvas.

### Por qué la validación por reserva no elige método

`validar_mde` aparta puntos de las curvas, interpola sin ellos y mide el error. Mide la isolínea, no el terreno entre curvas, que es donde se decide la pendiente. Tampoco sirve para comparar resoluciones (mide el centro de celda más cercano, y ese error crece con el píxel: de 10 a 20 m el RMS subió de 7.16 a 11.59 con la misma superficie) ni para ajustar el suavizado (suavizar es dejar de pasar por los puntos reservados). Sí sirve para cazar cotas mal capturadas, agrupando el error por curva. Un defecto viejo: los puntos de fuera del ráster tomaban la cota del borde y entraban como error real.

## 5. Qué interpolador para qué

### El método no se elige por receta

Medido en cinco predios contra la carta:

- **TIN con cauces como líneas de quiebre** para la clase alta. Mide la pendiente media entre dos curvas, que es lo mismo que mide la carta. En un predio vio el 45.7 % del escarpe contra 34.6 % del spline.
- **Pero el TIN deja mesetas falsas.** Un triángulo con sus tres vértices en la misma curva es plano. En un predio a 5 m: **535 ha (11.4 %) al 0 % exacto**, 778.35 ha contando su borde contaminado, el 80 % a media ladera, 353 ha en laderas de más de 15 % y el 47 % a más de 100 m de un cauce (las líneas de quiebre no las quitan). Del 5 al 19 % del extent según el predio. Por eso el TIN nunca da los rangos bajos ni la exposición.
- **En otro predio el TIN dio 28 % menos clase alta que el spline** y 76 ha más de 0-5 %. Por eso hay un juez y no una regla fija.
- **Las líneas de quiebre** dan cota a los cauces por sus cruces con las curvas y quitan los triángulos planos del fondo de las vaguadas: la clase alta pasó de 229.17 a 234.73 ha.

### La cascada: clase alta del TIN, rangos bajos de otra superficie

Cuando gana el TIN, la tabla completa se arma en cascada: la clase alta del TIN, los rangos bajos del spline (o de la malla) y lo que sobre, con la clase del TIN. En un predio real: 233.61 ha de clase alta del TIN, 4 426.26 ha de rangos bajos del spline y 51.89 ha de residuo.

**Los rangos bajos van sin filtro de manchas.** Medido contra la carta en cuatro predios, el corte ≥ 60 % pasa de entre −2.6 y +0.8 % sin filtro a entre +16.7 y +45.3 % con él. No depende de a quién se reparte la mancha: al vecino mayor, al píxel más cercano o a la clase contigua da casi lo mismo (+11 a +42 %). Las manchas chicas son terraceo del spline (pendiente baja junto a cada curva) que compensa lo que la superficie suave se empina a media ladera; quitarlas deja solo lo empinado. Promediar la pendiente antes del filtro fue lo menos malo (+4 a +18 % en ≥ 45) y aun así peor. El costo es que los rangos bajos quedan con miles de piezas bajo la UMM (entre 4 337 y 37 964 según el predio), y eso va como salvedad.

Los vecinos de ese spline se declaran en 90: la rodilla medida (64) se mide sobre la clase alta, que aquí no sale de él, y con 64 la exposición sacaba 3.52 ha sin orientación que con 90 no aparecen.

### La malla, para cuando el spline queda muy grueso

El piso del spline es λ/2. Si λ es grande, el spline queda muy por encima del tamaño de píxel del TIN y aplana las laderas. La señal es el cociente entre el `h` del spline y el techo de trazado:

| Factor | Rangos bajos del spline (≥60 / ≥45 / ≥35) | Rangos bajos de la malla a 5 m |
|---|---|---|
| 4.0 | −1.1 / +1.5 / +4.0 % | +8.3 / +2.5 / +2.6 % |
| 4.8 | −3.8 / +0.0 / +1.1 % | +7.4 / +3.4 / +2.5 % |
| 9.7 | **−40.1 / −23.3 / −14.6 %** | **+7.2 / +10.1 / +6.3 %** |

Dos predios más dan factor 4.0. El umbral se fijó en **9.5**, con margen sobre el único predio de factor alto (9.71): con 9.7 exacto, un techo un 0.3 % más ancho lo dejaba fuera. Entre 4.8 y 9.5 no hay mediciones.

La malla es una placa delgada resuelta como un solo sistema para todo el MDE (el núcleo del método de Hutchinson, sin imponer el drenaje). No deja mesetas, pero **sobreestima el corte ≥ 60 % de 7 a 8 % de forma estable**, porque una superficie suave se empina a media ladera por encima de la pendiente media entre curvas. No es cuestión de ajuste: el peso de la suavidad, barrido de 1e-3 a 1, solo lo bajó a +6.5 %. Por eso no es fuente de la clase alta. Pide más relleno de sumideros que el TIN, cuesta unos 860 bytes por celda (1.6 GB para 1.9 millones de celdas) y no necesita vecinos.

El prototipo resolvía por teselas con factorización directa y las costuras metían hasta 1.9 m de error junto al borde de cada tesela (62.7 ha cambiaban de clase). Con gradiente conjugado y multimalla se resuelve todo de una vez, sin costuras.

### Cuando el spline no ve la clase alta

Con λ grande, a su `h` (50 m en un predio real) el spline ve 0 ha de clase alta. El barrido de vecinos no tiene qué medir y no hay rodilla. No es un fallo: el spline no es candidato para la clase alta en ese predio, y la receta lo saca de las candidatas. Antes, la receta se detenía en ese punto.

## 6. Hidrología

### Dos MDE distintos

Quemar los cauces (bajar el terreno sobre ellos) y acondicionar el MDE (rellenar depresiones y dar micro-pendiente a los llanos) son correctos para calcular el flujo y dañinos para la pendiente: el quemado mete un escalón falso en cada orilla y llena la clase más alta de cauces que no existen. Por eso el MDE de pendiente se guarda antes de quemar.

### Rellenar antes de quemar

Una celda de cauce sobre sin dato da "sin dato menos profundidad", que sigue siendo sin dato. El relleno posterior copia el vecino válido más cercano, que está sin quemar: el lecho sube ahí y queda una presa dentro del cauce, con un MDE perfectamente válido y la red cortada. `quemar_cauces` avisa cuando pasa.

### El quemado fuerza la pendiente aguas abajo

Bajar el cauce una profundidad fija no basta: en un predio real, 16 km de cauce sobre una meseta del TIN quedaban como 1 613 celdas llanas que el acondicionado no drenaba. Ahora un tramo llano recibe micro-pendiente y uno que sube se rellena hasta su derrame. Avisa de las celdas de cauce que quedan por encima del terreno (en un predio real, 2 128 celdas más de 1 m, hasta 15.5 m): ahí la línea de la carta sube cuesta arriba.

### El MDE acondicionado no se guarda en disco

El acondicionado sale en precisión doble porque su micro-pendiente es del orden de 1e-5 m, y a 1 620 m de cota la precisión simple solo distingue 1.2e-4 m. Guardarlo y releerlo borra la micro-pendiente: los sumideros pasaron de 55 a **11 872** en un predio y de 60 a **373 595** en el TIN de otro.

### Los sumideros se avisan y no se reparan solos

`acumulacion_flujo` avisa cuando el flujo muere dentro del ráster: en un predio real, 55 celdas interiores sin salida, la mayor recogiendo el **21 % del ráster**. Son de una celda y con derrame de milímetros: residuo numérico del relleno de pysheds. Se descartó midiendo que fueran por precisión (en doble salen los mismos 55), por el orden de las operaciones o por el parámetro de micro-pendiente (subirlo solo reparte el daño: con 1e-1 salen 5 059 sumideros con el 4 % en el mayor).

La reparación es una función aparte, `rellenar_sumideros`, porque una endorreica real se taparía igual sin avisar. Sube cada sumidero hasta su derrame: el TIN liso convergió en una ronda subiendo 1.6 mm, el TIN con quiebres en cuatro subiendo 2.3 mm, y el ráster dejó de varar el 22.8 % de sus celdas. Cada ronda vuelve a acondicionar: sin eso, subir una celda la deja igual a su vecina, fabrica un llano nuevo y la iteración entra en un ciclo (8 y 9 sumideros alternando). Si se quemó, hay que pasarle los cauces: rellenar sobre la zanja la des-quema (1 612 celdas de cauce subidas más de 4.9 m en un predio real).

### Altura sobre el cauce

Las cotas salen del MDE base, sin quemar, y la máscara de cauce es la misma del quemado. Con otra máscara salían 163 ha de altura negativa en un predio real; con esta, 12 ha (depresiones del MDE base).

### El umbral de cauce va en celdas, y no es portable

Un cauce es una celda que drena más de `k` celdas. A 5 m de píxel eso es dieciséis veces más celdas que a 20 m, así que el mismo umbral da muchos más cauces. Se declara la superficie aportadora (ha) y la librería la convierte. La comparación es estricta, así que la celda que drena exactamente esa superficie queda fuera (0.2 %): se deja así y se escribe, en vez de esconder un "−1".

### La cuenca de un punto se ajusta al cauce

Un desagüe casi nunca cae exactamente sobre una celda de cauce, y a una celda de distancia la cuenca que sale es de unas pocas hectáreas, perfectamente formada y sin aviso. Por eso el punto se ajusta al cauce más cercano y se reporta cuánto se movió. Cuando el desagüe es una zona (el predio), un predio que drena por dos sitios devuelve la cuenca mayor: en un predio real, 737.82 ha de 1 765.15.

### Microcuencas: una partición por construcción

La forma obvia, la cuenca de cada desagüe, dejaba **1 115.45 ha (63 %) sin cubrir y 6 504 celdas solapadas** en un predio real, porque la cuenca de un desagüe contiene a otro aguas arriba. La solución es etiquetar cada celda por el desagüe por el que sale: cada celda tiene uno solo, así que no hay solape ni huérfanas. No se filtra por tamaño: de 1 737 piezas, 85 cubren el 97 %, pero un mínimo sería un número inventado. Una columna marca los desagües que son sumideros interiores: en un predio real, 13 con 1 063.38 ha, el 60 % del predio, que no drenan a ningún sitio.

### Un juez no puede ser insumo

La hidrografía que entra como línea de quiebre no puede juzgar la red que sale de ese MDE: se le parece por construcción. Pasó con la red estatal, que era el insumo de un cálculo y el "juez externo" del otro. Para juzgar hace falta campo, LiDAR, o reservar una parte de los cauces (`bloques_ajedrez`, en cuadros alternados y no en tramos sueltos, que quedarían contaminados por sus vecinos impuestos).

## 7. Carga de capas y sistemas de coordenadas

### Leer solo la ventana de trabajo

La cartografía de trabajo es estatal: la red hidrográfica tiene 384 554 tramos y 133 MB, las curvas 1 GB. Leer solo las entidades que tocan una ventana alrededor del predio bajó la carga de **33.1 s y 763 MB a 7.1 s y 164 MB**. No es un recorte: devuelve entidades enteras, y el recorte sigue haciendo falta.

### Pero no para lo que infiere una propiedad de la capa

`detectar_cota` decide la equidistancia por la forma de la columna completa. Con una ventana grande entró una sola curva mala (3 302 m entre curvas de 1 180) y bastó para que una detección por regularidad eligiera una columna de identificadores con equidistancia 1 m. Ampliar la ventana movía la equidistancia, y de ella cuelga todo lo demás. La regla: declarar un dominio estable, cargar con esa ventana y detectar una sola vez sobre él. Por eso también `detectar_cota` prefiere el paso más grueso que cumple la regularidad.

### Quién se reproyecta

`recortar`, `intersecar`, `borrar` y la unión espacial mueven siempre el segundo argumento al sistema del primero: la capa que se procesa no se mueve. `fusionar` no alinea, a propósito: concatenar dos sistemas da una capa mal ubicada en silencio. `quemar_cauces` recibe la transformación del ráster y no un sistema de coordenadas, así que no puede alinear: recortar los cauces y quemarlos sin reproyectar dio **0 píxeles quemados** con la red estatal en cónica conforme sobre un MDE en UTM.

La capa estatal no tiene código EPSG (viene como WKT suelto), así que pedir su código devuelve vacío y rompe la reproyección. Se pasa el sistema completo. Las funciones que miden en metros sin decirlo fallan con coordenadas en grados: antes, una pendiente calculada en grados salía en millones de por ciento sin error.

### El cambio de datum

Cambiar de proyección no cuesta nada, pero cambiar de datum puede: de NAD27 a UTM 13N en la zona de trabajo hay **12 m** de error sin que la reproyección diga nada. `reproyectar` avisa cuando la mejor transformación pasa de 1 m o falta la rejilla.

### Atributos

- **El join de tablas falla si no casa ninguna clave.** Un join que no casa devuelve la capa entera con nulos y el error sale cuatro pasos después como un mapa en blanco. Casi siempre es el tipo (`'007'` del CSV contra `7` del shapefile), y la conversión va del lado que conserva el dato (entero a texto, porque al revés se pierden los ceros).
- **Los nombres de columna del shapefile** se cortan a 10 caracteres y pueden chocar. Se avisa antes de la corrida, no se renombra: las dos veces que pasó, el arreglo bueno fue elegir un nombre corto con sentido (`SUP`).
- **El ancho de los campos del .dbf** se fija reescribiendo la cabecera, porque la librería de escritura no lo expone y la alternativa no instala en Python 3.14 en Windows. El texto cuenta bytes: una letra acentuada ocupa 2.

## 8. Rutas

- **A pie, el costo es por paso y con dirección.** Con una pendiente por píxel, caminar a media ladera se cobra como subir de frente (unas 4 veces de más en una ladera de 45 %). Con la pendiente de cada paso, en un predio real la ruta tardó entre 9 y 22 % menos y se separó hasta 1.3 km. Velocidad de Tobler; fuera de camino, 3/5. Ocupa unos 550 bytes por píxel.
- **En una red de calles, las velocidades deciden la ruta, no solo los minutos**: bajar las calles residenciales de 25 a 20 km/h cambió el 93 % del trazado en una ciudad real. Hay que calibrarlas con un trayecto medido.
- **Las capas de calles de las cartas urbanas son contornos de dibujo**, no ejes: ninguna línea toca a otra y no hay red. Hay que usar ejes de calle.
- **Las isócronas como polígonos** necesitan un ancho de servicio (hasta dónde se llega a pie desde la calle), que es un supuesto que se declara; no se usa una envolvente cóncava porque su parámetro no sale de ningún dato.

## 9. Imagen satelital

- **Solo Sentinel-2 por Earth Search.** Los mapas base comerciales prohíben guardar o analizar la imagen; otro servicio pide cuenta y token. La licencia de Copernicus exige citar "Contains modified Copernicus Sentinel data" con el año.
- **Escala y desfase del archivo.** Sin aplicarlos, una escena de línea base reciente daba un NDVI de 0.51 donde el correcto era 0.82.
- **La búsqueda pagina hasta el final**: con el límite lleno el servicio corta en silencio, y así se perdieron tres semanas de febrero. Hay días con dos pasadas de órbitas distintas, y una puede cubrir media franja.
- **La máscara de nubes de la escena es pobre**: confunde suelo brillante con nube y deja pasar sombras. La probabilidad de nube no la sustituye: en un predio de bosque la mitad de los píxeles marcados como nube tenía probabilidad menor a 26, y bajar el umbral "recuperaba" bordes de nube.
- **El relleno con otras fechas toma la más cercana, no la más limpia**, para no mezclar estaciones; mezclar fechas puede tapar un desmonte o una quema, y se declara.
- **El "antes" de un evento**: estirar las fechas hacia atrás hace que la fecha limpia de fuera sea la principal. El relleno de fuera se pasa aparte y nunca es la principal.
- **Bandas en enteros sin signo** hacen que rojo menos infrarrojo dé la vuelta (10 − 200 = 66) y el índice salga con el signo cambiado. Se leen en flotante.
- Medido en secas de 2026: 30 de 43 fechas salieron 100 % limpias según la máscara, pero un "antes" al 100 % traía una nube y su sombra visibles.
- **Las láminas** usan una fuente empaquetada (la de la librería de imágenes dibuja la ñ y la é como cajas) y comparten el estirado entre paneles comparables, o el antes y el después no se comparan. Dos imágenes pedidas por separado no salen en la misma malla y se alinean antes de restar.

## 10. Rendimiento

| Cambio | Antes | Después |
|---|---|---|
| Muestrear puntos sobre las curvas (búsqueda binaria en vez de recorrer la línea por cada punto) | 131.5 s | 0.6 s |
| Máscara de la envolvente sin crear un punto por celda (5.4 M celdas) | 20.3 s | 2.4 s |
| Barridos de resolución en paralelo, un proceso por `h` | 823 s | 293 s |
| Reusar lo medido entre llamadas a `derivar_parametros` | 145 a 192 s | 0.7 s |
| Barrido de vecinos con 3 procesos, sin volver a medir la separación (20 s) y con solo `n` tareas en vuelo (antes esperaba a las `n` grandes al converger) | 82 s | 15 s |
| El mismo barrido en serie cuando la malla es chica: arrancar procesos costaba más que las filas | 15 s | 3.9 s |

Los procesos del barrido corren con prioridad baja y dejan un núcleo libre, para que el equipo siga respondiendo. Se lanzan solo los que caben en el 80 % de la memoria libre (medido: el spline ocupa unos 2 KB por celda, la malla 860 B, el TIN 130 B, y cada proceso arranca con 300 MB). Un script no necesita la guarda `if __name__ == "__main__":` porque mientras arrancan los procesos se le esconde al script su ruta.

Con hilos en vez de procesos el spline iba más lento (0.77x): scipy recorre cada vecindario en un bucle de Python que retiene el intérprete.

## 11. Lo que se probó y se descartó

| Idea | Por qué no |
|---|---|
| Elegir el tamaño de píxel como lo hacen los programas de escritorio (lado del recuadro entre 250) | No mira el terreno: un predio de 3 km sale a 12 m y uno de 10 km a 40 m con cualquier equidistancia |
| Interpolar con todos los puntos (IDW global) | Memoria de puntos por celdas, inviable; y el IDW aplana cimas y fondos |
| TIN como método por defecto | Mesetas falsas del 5 al 19 % del extent |
| TIN sobre los vértices tal cual, sin remuestrear | Solo serviría para cotejar con otro programa; el muestreo a intervalo fijo evita el sesgo de densidad |
| Triangulación con aristas forzadas | Con las curvas densificadas, las aristas que saltan una curva son 0.35 % a 40 m de muestreo y 0.04 % a 5 m; y forzarlas no quita las mesetas |
| Reproducir el método completo de Hutchinson (con drenaje impuesto) | Código cerrado, impone el drenaje modificando el terreno y su salida depende de detalles internos; sí se reprodujo su núcleo (la malla) |
| Subir el peso de la suavidad de la malla para quitarle el sesgo | De +7.4 a +6.5 % como mucho: el sesgo es estructural |
| Superficie de "pendiente media entre curvas" (equidistancia entre la suma de distancias a las dos curvas) | Sobreestima de 8 a 11 % en cinco predios, y la carta usa la misma definición, así que no es un juez independiente |
| Cumplir la UMM en los rangos bajos de la cascada (filtro, vecino cercano, clase contigua, promedio previo) | Todas alejan el corte ≥ 60 % de la carta más que no filtrar |
| El spline en paralelo con hilos | 0.77x: más lento |
| Resolver la malla por teselas con factorización directa | Costuras de hasta 1.9 m |
| Que `acondicionar_mde` repare solo los sumideros | Taparía endorreicas reales; la reparación se pide aparte, después de leer el aviso |
| Una sola cuenca por desagüe para partir un predio | Deja el 63 % sin cubrir y solapa |
| Un "PLANO" en la exposición | El plano exacto del TIN es artefacto; se llama "SIN ORIENTACIÓN" y un plano con umbral sale de la pendiente |
