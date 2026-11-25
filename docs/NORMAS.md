# Normas del proyecto

<!-- Ejemplo de normas para trabajar con geophis. Dos usos:
     - Claude Desktop: pegar este archivo en el campo "Instrucciones" del Proyecto (no subirlo como archivo).
     - Agente que ejecuta código (Claude Code, Codex, Cursor...): copiarlo como AGENTS.md en la carpeta de trabajo; en Claude Code, con un CLAUDE.md que diga @AGENTS.md.
     Remite a un NOMENCLATURA_SIG.md propio: adáptalo o quita la norma 3. -->

**Dónde aplica cada norma.** Las normas 1, 2, 3, 6, 9 y 11 aplican siempre. Las normas 4, 5, 7, 8 y 10 aplican cuando trabajas en un chat de Claude (Proyecto de Claude Desktop o claude.ai). Si eres un agente que ejecuta código, en la computadora del usuario o en la nube (Claude Code, Codex, Cursor u otro), la norma 12 reemplaza a las normas 4, 5, 7, 8 y 10.

Las normas están numeradas. Cuando una norma remita a otra ("aplica la norma N"), lee y aplica esa norma completa.

Estas normas son del proyecto y no tienen relación con las reglas R1 a R9, las recetas P1 a P3 ni los puntos IA1 a IA5 de GEOPHIS.md. Al remitir a una norma, escribe siempre "norma N". Lo de GEOPHIS.md se cita con su código y el nombre del archivo: "R5 de GEOPHIS.md", "IA2 de GEOPHIS.md".

## Flujo general
1. Si la tarea involucra geophis, lee GEOPHIS.md (norma 1).
2. Reúne las rutas de entrada y pregunta lo que falte (norma 6 e IA2 de GEOPHIS.md).
3. Decide dónde se ejecuta el proceso: en tu entorno, en la computadora del usuario o en ninguno (normas 7 y 8; si eres un agente, norma 12).
4. Nombra los archivos (norma 3) y escribe las rutas del script (norma 11).
5. Entrega, verifica y cierra el mensaje (normas 4 y 5; si eres un agente, norma 12).

Los scripts hechos con cfasig siguen la norma 9. El formato de respuesta sigue la norma 10.

## Norma 1. Lectura de GEOPHIS.md
Lee completo GEOPHIS.md (del conocimiento del proyecto o del skill `geophis`) antes de:
- escribir, modificar, convertir o ejecutar código con geophis;
- generar archivos con geophis;
- responder preguntas sobre la librería (su funcionamiento, lo que puede o no hacer).

GEOPHIS.md contiene la documentación de geophis, sus reglas (R1 a R9), sus recetas (P1 a P3) y cómo responder (IA1 a IA5). No escribas código sin haberlo leído y no respondas de memoria sobre la librería.
- Léelo completo con una lectura, no con una búsqueda de fragmentos. Si no puedes acceder a GEOPHIS.md o solo puedes buscar fragmentos, avisa al usuario y detente. No improvises.
- Si lo leíste correctamente, no se lo confirmes al usuario; continúa con la tarea.
- DECISIONES.md, que GEOPHIS.md menciona, no está en el proyecto: no lo busques ni lo pidas.
- Para preguntas que no se relacionen con la librería ni impliquen código o archivos, no es necesario leerlo.

## Norma 2. Prioridad de GEOPHIS.md
Si una instrucción de GEOPHIS.md contradice alguna de estas normas, prevalece GEOPHIS.md, excepto en la entrega de archivos, el cierre del mensaje y las rutas de los scripts (normas 3, 4, 5, 10, 11 y 12), donde prevalecen estas normas.

En particular, IA1 e IA4 de GEOPHIS.md dicen que la respuesta es el script o el fragmento corregido. En este proyecto el script va como archivo y el cierre del mensaje sigue la norma 4. El resto de IA1 a IA5 se aplica tal cual.

## Norma 3. Nombres de archivos de salida
Antes de decidir el nombre de cualquier archivo de salida (script, entregable o intermedio), lee NOMENCLATURA_SIG.md y aplica su convención.
- Léelo completo con una lectura, no con una búsqueda de fragmentos. Si no puedes acceder a NOMENCLATURA_SIG.md o solo puedes buscar fragmentos, avisa al usuario en lugar de continuar sin leerlo.
- Los nombres de salida de las recetas de GEOPHIS.md (`PENDIENTES.shp`, `MDE_BASE.tif`, `NDVI.tif`...) son ejemplos: renómbralos con la convención.
- Si el documento no contempla un código para ese tipo de archivo, improvisa uno coherente con sus reglas y avisa al usuario cuáles códigos improvisaste.
- Si no conoces el cliente, usa `CLIENTE` tal cual. No improvises un código de cliente ni te detengas por eso; avisa en una línea que debe reemplazarse.
- El script lleva el nombre base del archivo que genera, con extensión `.py`, a menos que el usuario pida otro nombre. Si genera varios archivos, toma el nombre base del entregable principal (el que pidió el usuario).

## Norma 4. Destino de entrega y cierre del mensaje
- **El script se entrega siempre**, aunque el usuario haya pedido un archivo y el proceso se haya ejecutado con éxito. En ese caso entregas el archivo producido y, junto a él, el script que lo generó.
- **Carpeta de destino:** la que indique el usuario. Si no indicó ninguna, usa la carpeta conectada donde están los datos de entrada. Si no hay datos de entrada o hay más de una carpeta posible, pregunta cuál usar.
- **Si hay carpeta disponible:** guarda ahí cada entrega. Copia con la herramienta de archivos del dispositivo (por ejemplo, `device_commit_files`), no con el shell de su computadora, que puede no estar disponible. Después aplica la norma 5.
- **Si no hay carpeta disponible:** entrega cada archivo como adjunto.
- No pegues código en el chat, salvo que el usuario lo pida.
- **Correcciones y cambios (IA4 de GEOPHIS.md):** vuelve a entregar el script completo con el mismo nombre, sin sufijos (`_1`, `_FINAL`), y en el chat una línea de qué cambió. Si el script está en la carpeta del usuario, parte de esa versión y no de tu copia, porque pudo haberla editado (por ejemplo, para reemplazar rutas `SUPUESTO`). Si lo entregaste como adjunto y el usuario dice que lo editó, pídele que lo vuelva a adjuntar.

**Cierre del mensaje.** Después de entregar, el chat lleva solo estas líneas, en este orden y sin preámbulo:
1. Entrega: la ruta final o el adjunto de cada archivo, con el resultado de la verificación (norma 5).
2. Avisos de estas normas, solo los que apliquen, una línea cada uno: códigos improvisados o `CLIENTE` por reemplazar (norma 3), versión de geophis distinta (norma 7), qué no pudo hacerse y por qué (norma 8), resumen de la conversión (norma 9, que puede ocupar más de una línea).
3. Las tres líneas de IA1 de GEOPHIS.md: qué se decidió, qué queda sin medir o supuesto, qué comando correr. Si el usuario es quien ejecuta, incluye ahí la petición de la consola de IA3 de GEOPHIS.md.
4. Si el entregable usa Sentinel-2, la cita que da `cita_sentinel2`.

Las líneas 1, 2 y 4 no cuentan dentro del máximo de tres de IA1.

## Norma 5. Verificación de copias a la carpeta del usuario
No des por hecho que una copia funcionó. Para cada archivo copiado:
1. Comprueba en la carpeta del usuario que el archivo existe y que su tamaño coincide con el original (por ejemplo, con `device_list_dir`, o con el shell de su computadora si responde).
2. Informa la ruta final y el resultado de la verificación en la línea de entrega (norma 4).
3. Si la verificación falla, reintenta la copia una sola vez. Si vuelve a fallar, detente y avisa al usuario. No afirmes que el archivo fue entregado si no lo comprobaste.

## Norma 6. Rutas y datos de entrada
Aplica cuando el usuario pida un script o un archivo que use archivos de entrada.
1. Busca las rutas en su mensaje, en las carpetas conectadas y en los adjuntos.
2. Si no puedes obtenerlas (menciona archivos sin dar rutas, o el proceso necesita datos que no mencionó), pídele la carpeta o las rutas.
   - Haz la pregunta en el mismo mensaje que las de IA2 de GEOPHIS.md, antes de cualquier código. Cuenta dentro de su máximo de tres y, como ellas, lleva un valor propuesto: una ruta que hayas encontrado o, si no hay ninguna, seguir con rutas `SUPUESTO`.
   - Avísale que con rutas `SUPUESTO` no podrás ejecutar el script y deberá editarlo y correrlo él.
   - Pregunta aunque el proceso vaya a terminar en la norma 8: las rutas se necesitan para el `CONFIG` aunque no leas los datos.
3. Si no las da o te pide seguir, entrega el script con rutas `SUPUESTO` y aplica la norma 8. Esto vale también si había pedido un archivo, porque sin los datos no puedes producirlo.
4. Si dio las rutas pero no puedes leer los archivos, no te detengas: escribe el script con esas rutas y aplica la norma 8.

## Norma 7. Cuando el usuario pide un archivo en lugar de un script

**Paso 0. Compuerta (antes de instalar, leer datos o ejecutar nada).**
1. Aplica la norma 1.
2. Aplica la norma 6. Si faltan valores declarados (IA2 de GEOPHIS.md) o rutas, pregunta primero y espera la respuesta.
3. Decide si el proceso incluye algún paso que necesite red fuera de PyPI y GitHub. Lo es toda función del módulo `satelite.py` de GEOPHIS.md (`buscar_sentinel2`, `imagen_sentinel2`), cualquier función que lea una URL (por ejemplo, `recortar_raster` sobre la URL de un COG) y cualquier descarga de imágenes. Si el proceso mezcla pasos locales con alguno de estos, todo el proceso cuenta como de red.
4. Si no lo es, sigue con los pasos 1 a 5.
5. Si lo es, en tu entorno no instales geophis, no pruebes la conexión y no leas ni ejecutes nada. Haz una única comprobación, y solo en la computadora del usuario:
   - Si no tienes shell en su computadora, aplica la norma 8.
   - Si lo tienes, haz una sola llamada con límite de 15 segundos, con el intérprete de Python que correrá el script (en Windows, `python` o `py`; no uses `python3`, que en Windows suele abrir la Microsoft Store):
     ```
     python -c "import geophis, urllib.request as u; print(geophis.__version__, u.urlopen('https://earth-search.aws.element84.com/v1', timeout=10).status)"
     ```
     Esta es la única prueba de conexión permitida.
   - Si imprime la versión y `200`: compara la versión con la de GEOPHIS.md (si no coinciden, avísalo en el cierre, norma 4) y ejecuta el proceso en esa computadora sin instalar nada. El script que ejecutas ahí es el mismo que entregas. Luego sigue con los pasos 4 y 5.
   - Si la llamada falla, no arranca, tarda más de 15 segundos o falta algo, aplica la norma 8. No reintentes, no repitas la comprobación en la conversación y no uses otra vía (navegador u otra herramienta).

**Pasos para los procesos que no necesitan red:**
1. Instala geophis desde PyPI (`pip install geophis`). Si falla, intenta una vez desde el repositorio público de GitHub (antruc/geophis). Si ninguna funciona, aplica la norma 8 con el error.
2. Compara la versión instalada con la de GEOPHIS.md. Si no coinciden, avísalo en el cierre (norma 4) y continúa.
3. Prepara el script para el usuario con las rutas reales y ejecuta en tu entorno una copia de trabajo con las rutas de tu entorno. Esa copia no se entrega (norma 11, punto 5).
4. Revisa tú la salida de consola completa. Resume los `Aviso` en la línea de IA1 sobre qué queda sin medir o supuesto. No le pidas la consola al usuario: IA3 de GEOPHIS.md aplica solo cuando él ejecuta.
5. Nombra el script y los archivos producidos (norma 3) y entrégalos (norma 4).

## Norma 8. Cuando no puedas ejecutar el proceso
Esta norma tiene prioridad sobre cualquier paso de las normas 6 y 7 que lea datos, instale o ejecute. Mientras aplica, no corres nada de geophis ni lees los datos de entrada del usuario; el script los leerá al ejecutarse (por ejemplo, detecta el CRS con `detectar_utm`).

"Tu entorno" es el entorno en la nube de Claude. La computadora del usuario solo se usa para cuatro cosas: la comprobación del paso 0 de la norma 7, la ejecución del proceso cuando esa comprobación tuvo éxito, la copia de archivos (norma 4) y la verificación de copias (norma 5).

Aplica esta norma en cualquiera de estos casos:
- el proceso necesita red fuera de PyPI y GitHub y la comprobación de la norma 7 no tuvo éxito;
- no tienes los datos de entrada o sus rutas (norma 6);
- la instalación de geophis falla por PyPI y por GitHub;
- la ejecución del proceso falla.

Haz siempre lo siguiente:
1. Entrega el script (norma 4). Si tiene rutas `SUPUESTO`, indica que debe editarlas antes de correrlo.
2. En el cierre (norma 4), explica qué no pudo hacerse y por qué, con el error si lo hubo, y pide que lo ejecute y pegue la consola completa (IA3 de GEOPHIS.md).

No sustituyas el proceso por otro método sin avisar, ni intentes descargar o ejecutar con el navegador o con otra herramienta.

Una guarda de geophis que detiene el script (el `assert` de `sin_medir`, `comprobar_particion`) no es una falla de ejecución y no aplica esta norma. Busca el aviso en "Síntoma, causa, arreglo" de GEOPHIS.md. Si el arreglo pide una decisión de IA2 de GEOPHIS.md (escala, rangos, clase que se cita, temporada) o cambia de receta (por ejemplo, de P1 a P2), pregunta antes; si no, corrige, vuelve a ejecutar y dilo en el cierre (norma 4).

## Norma 9. Scripts hechos con "cfasig"
cfasig es una versión muy anterior de geophis con una estructura similar. Si el usuario entrega scripts que la usan:
1. Aplica la norma 1.
2. Analiza el funcionamiento y la intención del script.
3. Escribe un script nuevo con geophis, siguiendo GEOPHIS.md. No hagas una traducción línea por línea.
4. Aplica la norma 11 aunque el original tuviera rutas con un nombre de usuario fijo.
5. Entrega solo la versión nueva (norma 4). El original no se modifica ni se reemplaza.
6. En el cierre (norma 4), avisa que hiciste la conversión y resume qué cambió, señalando cualquier parte sin equivalente directo o que requiera revisión.

## Norma 10. Formato de respuesta
Estas normas tienen prioridad sobre la preferencia general del usuario de recibir solo texto en el chat: en este proyecto, guardar o adjuntar scripts y archivos de salida es lo esperado, sin que el usuario lo pida cada vez.

No generes imágenes, PNG, PDF ni otros documentos (por ejemplo `lamina` o `vista_previa` de geophis) a menos que el usuario los pida.

La tabla de procedencia (la imprime `derivar_parametros` al terminar) es solo de consola: no la conviertas en archivo ni la pegues en el chat salvo que el usuario la pida. Si la pide, va como texto en el chat.

En todo lo demás, responde en texto en el chat.

## Norma 11. Rutas dinámicas en los scripts entregados (Windows)
Aplica a todo script que entregues y que use rutas de Windows, de entrada o de salida, en el `CONFIG` o en cualquier otra parte del código. El objetivo es que el script funcione en otros equipos Windows sin editar el nombre de usuario. Las rutas son `Path` y se componen con `/`, como en las recetas de GEOPHIS.md.
1. **Rutas dentro de la carpeta del usuario.** Si una ruta cuelga de la carpeta de perfil (`<unidad>:\Users\<usuario>\...`, o está escrita con `%USERPROFILE%` o `~`), no escribas el nombre de usuario. Constrúyela con `Path.home()` seguido de lo que va después del nombre de usuario, respetando carpetas intermedias como `OneDrive`.
   - Ejemplo: `C:\Users\Juan\Documents\Proyecto\datos.shp` se escribe `Path.home() / "Documents" / "Proyecto" / "datos.shp"`.
2. **Herramienta.** Usa `pathlib` (`from pathlib import Path`). No uses `os.environ["USERPROFILE"]` ni `os.path.expanduser` para construir estas rutas.
3. **Rutas fuera de la carpeta del usuario** (por ejemplo `D:\SIG\datos` o `\\servidor\carpeta`): déjalas literales, como las dio el usuario, con `Path(r"...")`.
4. **Rutas `SUPUESTO`.** Si se asumen dentro de la carpeta del usuario, también van con `Path.home()`. Mantén la marca `SUPUESTO` junto a ellas.
5. **Solo el script entregado.** En un chat de Claude, la copia de trabajo que ejecutas en tu entorno (norma 7, paso 3) lleva las rutas de tu entorno y no se entrega. Antes de entregar, confirma que el script entregado es igual al que ejecutaste salvo por las rutas del `CONFIG`.
6. **Otros sistemas.** Si las rutas del usuario no son de Windows (`/Users/...`, `/home/...`), déjalas como las dio, también con `Path`.
7. **Sin nombre de usuario real.** No dejes el nombre de usuario real en comentarios, docstrings, mensajes ni nombres de variables.
8. **Nombres de usuario con punto.** Los nombres como `USUARIO.1234` funcionan con `Path.home()` sin tratamiento especial. Pero `pathlib` toma lo que va después del último punto como extensión, también en carpetas, así que no uses `.stem`, `.suffix`, `.with_suffix()` ni `.with_stem()` sobre `Path.home()` ni sobre ninguna carpeta; úsalos solo sobre nombres de archivo.

## Norma 12. Cuando eres un agente que ejecuta código
Reemplaza a las normas 4, 5, 7, 8 y 10. Vale para Claude Code, Codex, Cursor y otros agentes que escriben, ejecutan y leen la consola ellos mismos.
1. **Ejecución.** Ejecuta tú el script con el intérprete de Python que tenga geophis. Si no está instalado, instálalo con `pip install geophis`. El script que ejecutas es el que entregas.
2. **Destino.** Guarda el script y los archivos producidos en la carpeta que indique el usuario. Si no indicó ninguna, usa la carpeta de los datos de entrada; si no hay datos de entrada o hay más de una carpeta posible, pregunta cuál usar. Al corregir, sobrescribe el script con el mismo nombre, sin sufijos (`_1`, `_FINAL`), partiendo de la versión que está en la carpeta, porque el usuario pudo editarla.
3. **Consola.** Lee tú la salida de consola completa y resume los `Aviso` en la línea de IA1 sobre qué queda sin medir o supuesto. No le pidas la consola al usuario.
4. **Cuando no puedas ejecutar** (faltan los datos o sus rutas, no hay red para un proceso que la necesita, falla la instalación o la ejecución): entrega el script, y en el cierre explica qué no pudo hacerse y por qué, con el error si lo hubo, y pide que lo ejecute y pegue la consola completa (IA3 de GEOPHIS.md). No sustituyas el proceso por otro método sin avisar. El último párrafo de la norma 8 (las guardas de geophis no son fallas de ejecución) aplica igual.
5. **Cierre.** Termina con las líneas del cierre de la norma 4. En la línea de entrega va la ruta de cada archivo guardado. No generes imágenes ni documentos que el usuario no pidió, ni conviertas en archivo la tabla de procedencia (norma 10).
