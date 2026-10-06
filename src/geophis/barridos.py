"""Lo que MIDE sobre el MDE y la cartografia, y los barridos que lo recorren.

La capa de ARRIBA: cada funcion de aqui orquesta la cadena entera (interpolar ->
pendiente -> reclasificar -> sieve -> poligonizar -> recortar) o lee un raster de
distancia, asi que necesita `raster` y `geometria` a la vez.

Ninguna de estas funciones decide: reportan. `barrer_resolucion` da la banda de
incertidumbre y no el veredicto.
"""

import math
import time
from collections.abc import Iterable
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
import geopandas as gpd
from rasterio import Affine
from rasterio.transform import from_origin
from scipy.ndimage import distance_transform_edt

from .geometria import (
    ancho_banda,
    cuadros_mde,
    longitud_total_m,
    pendiente_sostenida,
    recortar,
    sostenimiento_por_clase,
    superficie_total_ha,
)
from .metrologia import (
    _de_parametros,
    _detectar_equidistancia,
    margen_borde_celdas,
    min_pixeles_umm,
)
from .zonas import zonificar
from .raster import (
    FloatArray,
    _bounds_cuadro,
    _separacion_horizontal,
    calcular_pendiente,
    interpolar_mde,
    rasterizar,
)
from ._salida import _callado, _mostrar


def _exigir_mismo_crs(curvas: gpd.GeoDataFrame, otra: gpd.GeoDataFrame, nombre: str) -> None:
    """Falla si `otra` no está en el CRS de `curvas`: la malla sale de un CRS y
    la otra capa se rasteriza sobre ella sin reproyectar. Como
    `cotas_por_cruce`: no alinea, exige."""
    if curvas.crs != otra.crs:
        a = curvas.crs.name if curvas.crs else None
        b = otra.crs.name if otra.crs else None
        raise ValueError(
            f"curvas en {a} y {nombre} en {b}: reproyecta antes, aquí no se "
            f"alinea nada"
        )


def raster_distancia(
    curvas: gpd.GeoDataFrame,
    zona: gpd.GeoDataFrame,
    resolucion: float,
) -> tuple[FloatArray, npt.NDArray[np.bool_], Affine]:
    """Malla de distancia (m) a la curva más cercana, sobre el extent de `zona`.

    Sustituye la escalera de buffers vectorial (hasta ~30 pasadas) y los
    techos cartográficos por rango (uno por buffer) por UNA sola malla:
    rasteriza las curvas y corre la transformada de distancia sobre el
    complemento. Un ráster no puede contar área doble, la cobertura es
    monótona por construcción, y cualquier lectura posterior (percentiles,
    techos por rango) comparte el mismo dominio de píxeles: un cuantil ya no
    puede heredar la resolución de un corte de buffer fijo en el que cayó de
    pura casualidad.

    Llamarla UNA vez y pasar (distancia, mascara, transform) a `separaciones`
    y a `auditar_rangos` es lo que garantiza que las dos midan sobre el mismo
    dominio; llamarla dos veces con el mismo `zona`/`h` da el mismo resultado
    pero cuesta el doble.

    Parámetros
    ----------
    resolucion : tamaño de celda (unidades del CRS). Tiene que ser el mismo `h` que
        usa el MDE que se está auditando, o las hectáreas de esta malla y las
        del MDE no comparan.

    Devuelve (distancia, mascara, transform):
    - `distancia` : array 2D, metros a la curva más cercana en cada celda.
    - `mascara` : array 2D booleano, True en toda celda que `zona` TOCA
      (`rasterizar(..., todo_tocado=True)`), no solo donde cae el centro: el
      área de la máscara sobra por el borde respecto a la de `zona`.
    - `transform` : el `Affine` de la malla, para pasar a `poligonizar` o
      `rasterizar` cualquier resultado que se derive de ella.
    """
    _exigir_mismo_crs(curvas, zona, "zona")
    if resolucion <= 0:
        raise ValueError(f"resolucion {resolucion} debe ser > 0")
    x0, y0, x1, y1 = zona.total_bounds
    ncol = max(int(math.ceil((x1 - x0) / resolucion)), 1)
    nrow = max(int(math.ceil((y1 - y0) / resolucion)), 1)
    transform = from_origin(x0, y0 + nrow * resolucion, resolucion, resolucion)
    forma = (nrow, ncol)

    quemado = rasterizar(curvas, forma, transform, todo_tocado=True) == 1
    if not quemado.any():
        raise ValueError("ninguna curva cae en la malla: revisa el CRS")
    distancia = distance_transform_edt(~quemado, sampling=resolucion)
    mascara = rasterizar(zona, forma, transform, todo_tocado=True) == 1
    if _mostrar():
        area_ha = float(mascara.sum()) * resolucion**2 / 10_000
        print(f"raster_distancia: {ncol}x{nrow} px a {resolucion:g} m | zona {area_ha:.2f} ha")
    return distancia, mascara, transform


def separaciones(
    distancia: FloatArray,
    mascara: npt.NDArray[np.bool_],
    resolucion: float,
    lambda_m: float,
    zigzag: float,
    cortes: npt.ArrayLike | None = None,
) -> tuple[list[dict[str, float]], dict[str, float]]:
    """Distribución de separaciones entre curvas dentro de `mascara`, leída
    del ráster de distancia de `raster_distancia`.

    Reemplaza la escalera de buffers vectorial. Cuatro de sus siete trampas
    históricas dejan de ser código y pasan a ser estructura del ráster: no
    puede contar área doble, su cobertura es monótona por construcción, esta
    función y `auditar_rangos` comparten un solo dominio de píxeles si
    reciben la misma `distancia`/`mascara`, y los cuantiles son percentiles
    de la distribución real, no del bracket en que cayó un corte fijo.

    Parámetros
    ----------
    distancia, mascara, h : lo que devuelve `raster_distancia`.
    lambda_m, zigzag : de `separacion_media`; se pasan tal cual al resumen,
      no se recalculan (evita medir dos veces lo mismo).
    cortes : bordes de distancia (m) para la tabla acumulada. None (default)
        = derivados de esta misma zona (percentiles 0 a 99.5 en 32 pasos):
        una lista fija deja que un cuantil caiga en un bracket ancho y la
        cifra hereda esa resolución sin avisar.

    Devuelve (tabla, resumen). `tabla`: lista de dicts con `distancia`,
    `area_ha`, `area_acum_ha`, `s_equivalente` (= 2·distancia). `resumen`:
    dict con `lambda_armonica`, `factor_zigzag`, `q50`, `q80`, `q90`,
    `area_sin_curvas_ha` (área más allá del último corte; solo tiene sentido
    con `cortes` explícitos, con derivados sale NaN porque el último corte es
    un percentil y la cifra sería 0.5 % por construcción),
    `s_equivalente_zona` (= 2·q50: la mitad de una separación real, NO la `s`
    del vacío que usa `derivar_parametros`, que es 2·q90).

    Sin curvas dentro (`lambda_m` es NaN) o `mascara` vacía: tabla `[]` y
    resumen con todo en NaN salvo `area_sin_curvas_ha`.
    """
    d = distancia[mascara]
    px_ha = resolucion**2 / 10_000
    area_zona = d.size * px_ha
    if d.size == 0 or lambda_m != lambda_m:
        vacio = float("nan")
        return [], {
            "lambda_armonica": vacio,
            "factor_zigzag": vacio,
            "q50": vacio,
            "q80": vacio,
            "q90": vacio,
            "area_sin_curvas_ha": area_zona,
            "s_equivalente_zona": vacio,
        }

    derivados = cortes is None
    if derivados:
        cortes = np.percentile(d, np.linspace(0, 99.5, 33)[1:])
    u = np.unique(cortes)
    acum = np.searchsorted(np.sort(d), u, side="right") * px_ha
    tabla: list[dict[str, float]] = [
        {
            "distancia": float(c),
            "area_ha": float(a),
            "area_acum_ha": float(ac),
            "s_equivalente": 2 * float(c),
        }
        for c, a, ac in zip(u, np.diff(acum, prepend=0.0), acum)
    ]

    q50, q80, q90 = (float(np.percentile(d, q)) for q in (50, 80, 90))
    resumen = {
        "lambda_armonica": lambda_m,
        "factor_zigzag": zigzag,
        "q50": q50,
        "q80": q80,
        "q90": q90,
        # ponytail: con cortes derivados el ultimo corte es el percentil 99.5,
        # asi que "mas alla del ultimo corte" seria 0.5% de la zona POR
        # CONSTRUCCION, digan lo que digan las curvas. Solo con cortes
        # explicitos (umbral fisico) la cifra significa algo; si no, NaN.
        "area_sin_curvas_ha": float("nan")
        if derivados
        else area_zona - (float(acum[-1]) if acum.size else 0.0),
        "s_equivalente_zona": 2 * q50,
    }
    return tabla, resumen


def barrer_resolucion(
    curvas: gpd.GeoDataFrame,
    campo: str,
    predio: gpd.GeoDataFrame,
    rangos: list[tuple[Any, float, float]],
    umm_m2: float | None = None,
    resoluciones: Iterable[float] | None = None,
    lambda_m: float | None = None,
    equidistancia: float | None = None,
    intervalo_muestreo: float | None = None,
    cuadro: gpd.GeoDataFrame | Any = None,
    metodo: str | None = None,
    suavizado: float = 0.0,
    vecinos: int | None = None,
    etiqueta_alta: Any = None,
    min_pixeles: int | None = None,
    quiebres: gpd.GeoDataFrame | None = None,
    paso_quiebre: float | None = None,
    exentos: Iterable[Any] = (),
    parametros: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Corre la cadena de pendientes a varios `h` y tabula cómo se mueve la clase alta.

    **Qué es:** la BANDA DE INCERTIDUMBRE de la cifra frente a `h`. Corre la
    cadena de producción entera (interpolar → pendiente → reclasificar → sieve
    → poligonizar → recortar) a cada `h` y tabula la superficie resultante. El
    rango entre la primera y la última fila es lo que se declara junto a la
    cifra en el expediente. Nadie más lo mide.

    **Qué NO es:** el juez de si la clase es resoluble. `delta_pct` arrastra
    dos confusiones que no son del terreno:

    1. `min_pixeles` = `ceil(umm/h²)` es ENTERO y salta de golpe. Y el sieve no
       borra, ABSORBE en el vecino mayor, así que endurecerlo puede AGRANDAR la
       clase alta. Un escalón de `min_px` entre dos filas mete un brinco que se
       lee como terreno.
    2. El piso GLOBAL (λ/2 del extent), en un predio de dos regímenes, es el
       piso del LLANO y sobre-restringe la banda empinada.

    El veredicto vive en **`sostenimiento_por_clase`**, que lo saca de UNA
    interpolación comparando `piso_local <= h <= s_sostenida`. Decide allá;
    aquí ven a poner la barra de error.

    Cómo leer la tabla: `delta_pct` es el cambio contra el `h` inmediatamente
    más fino, y solo significa algo si `min_pixeles` no saltó entre las dos
    filas, fíjalo para leerlo limpio. `p_sostenida` es el juez cartográfico,
    externo al MDE: la pendiente que las curvas de esas zonas pueden sostener;
    NaN ahí = ninguna curva dentro, o sea rizo puro. Las demás clases se mueven
    poco con `h`; por eso se tabula la alta.

    El EXTENT NO cambia entre corridas: se pasa un solo `cuadro` para que `h`
    sea la única variable. Rehacer el cuadro por `h` (los márgenes de
    `cuadros_mde` escalan con la resolución) confunde el efecto que se mide.
    Por lo mismo `intervalo_muestreo` y `vecinos` se dejan fijos: si son None,
    `interpolar_mde` los deriva de λ y del extent, que aquí no se mueven.

    Lo que sí depende de `h`, y por eso se recalcula en cada pasada, es
    `min_pixeles` del sieve (`umm_m2 / h²`): es parte de la cadena de
    producción, no una variable libre.

    Parámetros
    ----------
    curvas : capa de curvas ya recortada al cuadro y reproyectada, la MISMA que
        usa producción.
    predio : polígono al que se recorta cada corrida y contra el que se
        reportan porcentajes.
    resoluciones : resoluciones a probar. None (default) = `λ · (1/4, 1/3, 1/2, 2/3, 1)`,
        que bracketea el piso λ/2. **Con `metodo="tin"` es obligatorio**: el TIN
        no ondula, no tiene piso λ/2, y su `h` sale de `w/3`. Pásalo alrededor del `h`
        de producción, p. ej. `(h/2, h, 1.5h)`. No baja de λ/4 a propósito: ahí ya se sabe
        que la clase alta se dispara y una malla así de fina cuesta el
        cuadrado. Si el barrido tarda de más, recorta esta lista antes que
        cualquier otra cosa.
    lambda_m : separación horizontal media. None (default) = se mide sobre el
        EXTENT de `cuadro`, no sobre `predio`. Los dos dominios dan números
        distintos y con ellos dos pisos distintos, así que un `h` puede pasar la
        comprobación de aquí y disparar el aviso de `interpolar_mde`. Manda el extent porque es donde el spline construye la
        superficie y donde ondula, y es el dominio que usa `interpolar_mde`
        para su propio piso. La λ del predio es informativa, no la que gobierna.
    etiqueta_alta : clase a seguir. None = la última de `rangos`.
    min_pixeles : DIAGNÓSTICO. None (default) = `ceil(umm_m2/h²)`, la cadena de
        producción. Un entero lo fija igual en todas las corridas.

        Para qué: `ceil(umm_m2/h²)` es entero, así que salta de golpe y ese
        escalón cae DENTRO de la columna `delta_pct`. Y el sieve no borra,
        ABSORBE en el vecino mayor, así que subir el umbral puede AGRANDAR la
        clase alta (se come los huecos de clase baja dentro de la masa). Con el
        escalón en medio, `delta_pct` mezcla resolución y sieve y la meseta no
        se puede leer.

        Fijándolo, `h` vuelve a ser la única variable y el barrido responde la
        pregunta que se le hace. La cifra que se ENTREGA sale igual de una
        corrida con None: esta es para saber qué se está midiendo, no para
        producir.

    quiebres, paso_quiebre : se pasan tal cual a `interpolar_mde` (solo
        `metodo="tin"`). Sin ellos, el barrido de una receta con quiebres mide
        OTRA superficie que la de producción.
    exentos : ETIQUETAS de `rangos` que el sieve no toca, las mismas que
        producción pasa como `exentos` a `limpiar_moteado` (en la receta TIN,
        la clase alta, que se cartografía como elemento lineal). Se traducen a
        código aquí. Sin ellas el sieve absorbe justo la clase que se tabula.
    parametros : el dict de `derivar_parametros`. Lo que se deje en None se
        toma de ahí (`umm_m2`, `lambda_m`, `equidistancia`, `intervalo_muestreo`,
        `vecinos` y `metodo`); lo que se pase a mano manda sobre el dict.

    Devuelve una lista de dicts (uno por `h`, de fino a grueso) con `h`,
    `lambda_m`, `n_celdas`, `min_pixeles`, `area_alta_ha`, `pct_predio`,
    `delta_pct`, `n_piezas`, `s_sostenida`, `p_sostenida`, `sin_clase_ha` y
    `segundos`.
    `geo.MOSTRAR` imprime cada fila al terminarla: el barrido es largo y una
    tabla que solo aparece al final no deja ver dónde va.
    """
    _exigir_mismo_crs(curvas, predio, "predio")
    umm_m2, lambda_m, equidistancia, intervalo_muestreo, vecinos, metodo = (
        _de_parametros(
            parametros,
            umm_m2=umm_m2,
            lambda_m=lambda_m,
            equidistancia=equidistancia,
            intervalo_muestreo=intervalo_muestreo,
            vecinos=vecinos,
            metodo=metodo,
        ).values()
    )
    metodo = metodo or "spline"
    if umm_m2 is None:
        raise ValueError("falta `umm_m2` (o `parametros`)")
    if etiqueta_alta is None:
        etiqueta_alta = rangos[-1][0]
    if equidistancia is None:
        equidistancia = _detectar_equidistancia(curvas[campo])
        if equidistancia is None:
            raise ValueError(
                "no se pudo detectar la equidistancia; pásala: sin ella no hay "
                "juez cartográfico (`pendiente_sostenida`) que reportar"
            )
    if lambda_m is None:
        # MISMO dominio que el piso interno de `interpolar_mde`: el extent de
        # interpolación. Medirla sobre `predio` da otro número y con él un piso
        # que no es el que va a comprobar el interpolador.
        lambda_m = _separacion_horizontal(curvas, _bounds_cuadro(curvas, cuadro))
        if lambda_m is None:
            lambda_m = float("nan")
    exentos = set(exentos)
    _desconocidas = exentos - {r[0] for r in rangos}
    if _desconocidas:
        raise ValueError(
            f"`exentos` {sorted(map(str, _desconocidas))} no estan en `rangos`"
        )
    if resoluciones is None and metodo == "tin":
        raise ValueError(
            "con metodo='tin' pasa `resoluciones`: la escalera de lambda es del spline y en "
            "un TIN queda toda por encima del h de trazado (w/3)"
        )
    if resoluciones is None:
        if not math.isfinite(lambda_m):
            raise ValueError("sin lambda medible no hay rango que derivar; pasa `resoluciones`")
        resoluciones = [round(lambda_m * f, 1) for f in (0.25, 1 / 3, 0.5, 2 / 3, 1.0)]
    resoluciones = sorted({float(h) for h in resoluciones})
    if not resoluciones:
        raise ValueError("`resoluciones` vacío")

    sup_predio = superficie_total_ha(predio)
    if _mostrar():
        piso = f"{lambda_m / 2:.1f}" if math.isfinite(lambda_m) else "?"
        cola = (
            "metodo tin: sin piso antialias"
            if metodo == "tin"
            else f"piso antialias lambda/2 = {piso} m (lambda medida sobre el extent)"
        )
        print(
            f"barrido de {len(resoluciones)} resoluciones sobre {sup_predio:.1f} ha: "
            f"{', '.join(f'{h:g}' for h in resoluciones)} m | {cola}"
        )
        # `s_sost` es el ancho al que están las curvas dentro de la clase: si
        # cae por debajo de `h`, el objeto es más angosto que el píxel y su
        # superficie no es resoluble, por muy bien ubicado que esté.
        print(
            f"   {'h':>6} {'celdas':>10} {'min_px':>7} {'area_ha':>9} {'% predio':>9} "
            f"{'delta%':>8} {'piezas':>7} {'s_sost':>8} {'piso_loc':>9} {'p_sost':>8} "
            f"{'s/clase':>8} {'seg':>7}"
        )

    filas: list[dict[str, Any]] = []
    for h in resoluciones:
        t0 = time.perf_counter()
        mde, tf = _callado(interpolar_mde)(
            curvas,
            campo,
            resolucion=h,
            equidistancia=equidistancia,
            intervalo_muestreo=intervalo_muestreo,
            cuadro=cuadro,
            metodo=metodo,
            suavizado=suavizado,
            vecinos=vecinos,
            # ya medida arriba sobre ESTE mismo extent (`cuadro` no se mueve
            # entre corridas, que es la premisa del barrido). Sin esto,
            # `interpolar_mde` la remide en cada `h`: el mismo numero, N veces,
            # a un buffer + disolver + intersecar sobre todas las curvas.
            lambda_m=lambda_m,
            quiebres=quiebres,
            paso_quiebre=paso_quiebre,
        )
        # misma cadena que producción, la de `zonificar` y no una copia: NaN
        # intacto (regla 7 de 8 en la pendiente), sieve por UMM, recorte
        # vectorial al final. La regla de la UMM vive en `min_pixeles_umm`.
        min_px = min_pixeles_umm(umm_m2, h) if min_pixeles is None else min_pixeles
        gdf = zonificar(
            calcular_pendiente(mde, tf), tf, predio.crs, rangos, min_px,
            recorte=predio, exentos=exentos, campo="_clase",
        )

        alta = gdf[gdf["_clase"] == etiqueta_alta]
        area = superficie_total_ha(alta)
        s_c, p_c = (
            pendiente_sostenida(curvas, alta, equidistancia)
            if len(alta)
            else (float("nan"), float("nan"))
        )
        previo = filas[-1]["area_alta_ha"] if filas else None
        fila = {
            "h": h,
            # la del EXTENT, la que gobierna el piso. Va en cada fila para que
            # quien lea la tabla no tenga que volver a medirla (ni medirla
            # sobre otro dominio, que es de donde salen los pisos que no cuadran)
            "lambda_m": lambda_m,
            "n_celdas": int(mde.size),
            "min_pixeles": min_px,
            "area_alta_ha": area,
            "pct_predio": area / sup_predio * 100 if sup_predio else float("nan"),
            # contra el h anterior de la tabla, que es el inmediatamente MÁS
            # FINO. La meseta es donde esta columna se aplana.
            "delta_pct": float("nan") if previo is None else _delta_pct(previo, area),
            "n_piezas": len(alta),
            "s_sostenida": s_c,
            # El rizo del spline ondula con la separación LOCAL entre curvas, y
            # Horn deriva sobre 2h: hay alias cuando 2h < s_local, o sea
            # h < s_local/2. Misma derivación que el piso global, con la λ de
            # ESTA clase en vez de la del extent. Importa cuando el predio tiene
            # dos regímenes: el global es el piso del LLANO y sobre-restringe la
            # banda empinada.
            "piso_local": s_c / 2,
            "p_sostenida": p_c,
            "sin_clase_ha": sup_predio - superficie_total_ha(gdf),
            "segundos": time.perf_counter() - t0,
        }
        filas.append(fila)
        if _mostrar():
            print(
                f"   {h:6.1f} {fila['n_celdas']:10,d} {min_px:7d} {area:9.2f} "
                f"{fila['pct_predio']:8.1f}% {fila['delta_pct']:8.1f} "
                f"{fila['n_piezas']:7d} {s_c:8.1f} {fila['piso_local']:9.1f} "
                f"{p_c:8.1f} {fila['sin_clase_ha']:8.2f} {fila['segundos']:7.1f}"
            )

    if _mostrar():
        # Rango de la cifra en el barrido: ESO es lo que mide esta funcion. El
        # veredicto de resolubilidad NO sale de aqui, sale de
        # `sostenimiento_por_clase` (ver el docstring, seccion "Que NO es").
        areas = [f["area_alta_ha"] for f in filas]
        if len(filas) > 1 and max(areas):
            print(
                f"   BANDA: {min(areas):.2f} a {max(areas):.2f} ha entre "
                f"h={filas[0]['h']:g} y {filas[-1]['h']:g} m "
                f"(+/-{(max(areas) - min(areas)) / max(areas) * 100 / 2:.0f} % "
                f"sobre el maximo). Esa es la incertidumbre de la cifra frente a "
                f"`h`, y es lo que se declara junto a ella."
            )
        print(
            "   `delta%` es el cambio contra el `h` inmediatamente mas fino. Ojo "
            "al leerlo: `min_pixeles` es entero y salta, y el sieve ABSORBE en el "
            "vecino mayor, asi que un escalon de `min_px` entre dos filas mete un "
            "brinco que no es del terreno. Fija `min_pixeles` para leerlo limpio. "
            "`p_sost` NaN = ninguna curva dentro de esa clase: rizo puro."
        )
    return filas


def _delta_pct(previo: float, actual: float) -> float:
    """Cambio de `actual` contra `previo`, en %. Con `previo` 0 no hay
    proporcion: 0 si sigue en 0 (nada que comprar), inf si aparecio algo."""
    if previo:
        return (actual - previo) / previo * 100
    return 0.0 if not actual else math.inf


def _avisos_rodilla(filas: list[dict[str, Any]], tolerancia: float) -> list[str]:
    """Las razones por las que la rodilla de `filas` puede no ser una rodilla.

    Lista vacia = la cifra convergio y el numero es una medicion. El barrido
    para en cuanto converge, asi que si convergio el ultimo `delta%` esta
    dentro de `tolerancia`; si no, la lista se acabo antes. Es criterio puro
    sobre las filas: vive aparte para fijarlo con filas fabricadas, sin pagar
    una interpolacion por `n`.
    """
    # 0 ha contra 0 ha "converge" sin medir nada: un predio real a h=50 m no tiene
    # clase alta y daria `20 medido` con la meseta todavia cayendo 24 %.
    if not filas[-1]["alta_ha"]:
        return [
            "la clase alta es 0 ha a esta resolucion: no hay cifra que converja y "
            "el `n` devuelto no mide nada. Baja `resolucion` hasta que la clase "
            "exista, o `vecinos` pasa a DECLARADO con su razon al lado."
        ]
    ultimo = float(filas[-1]["delta_pct"])
    if len(filas) > 1 and abs(ultimo) <= tolerancia * 100:
        return []
    cambio = (
        "no hay otro `n` con que comparar"
        if len(filas) == 1
        else f"todavia cambia {abs(ultimo):.1f} %"
    )
    return [
        f"la clase alta NO convergio: entre los dos `n` mas grandes {cambio}, "
        f"y la tolerancia es el {tolerancia:.0%}. El `n` devuelto es el borde de "
        f"la lista y no una propiedad del predio: o extiendes `enes`, o "
        f"`vecinos` pasa a DECLARADO con su razon al lado."
    ]


class Rodilla(int):
    """El `n` de la rodilla, con los avisos que dicen si es rodilla de verdad.

    Es un `int` y se usa como tal; lo que lleva encima es `avisos`. Si no esta
    vacio, la clase alta NO convergio dentro de `enes` y el numero no es una
    medicion: es el borde de la rejilla barrida. `derivar_parametros` lo mira
    para no estampar `medido` sobre algo que nadie eligio.
    """

    avisos: tuple[str, ...]

    def __new__(cls, valor: int, avisos: Iterable[str] = ()) -> "Rodilla":
        obj = super().__new__(cls, int(valor))
        obj.avisos = tuple(avisos)
        return obj


def barrer_vecinos(
    curvas: gpd.GeoDataFrame,
    campo: str,
    zona: gpd.GeoDataFrame,
    resolucion: float,
    intervalo_terreno: float,
    s_vacio: float,
    enes: Iterable[int] = (20, 32, 48, 64, 90, 122, 160, 200),
    zigzag: float = 1.0,
    equidistancia: float | None = None,
    tolerancia: float = 0.01,
    p_max: float = 1.0,
) -> tuple[list[dict[str, Any]], int]:
    """La RODILLA de `vecinos`: el `n` más chico a partir del cual la cifra
    que se entrega ya no se mueve.

    `vecinos` es el único parámetro de `interpolar_mde` que NO se deriva de
    nada, y la fórmula
    `pi*k^2*s/(4*i)` solo dimensiona el orden de magnitud. Sale de este barrido
    o no sale, y correr con el default de 48 en vez de la rodilla medida es una
    de las decisiones que se pierden en silencio al cambiar de script.

    QUÉ MIDE. La cifra que se entrega: las ha de la zona con pendiente
    `> p_max` (la clase alta, `>100` con `p_max` 1.0). Pocos vecinos aterrazan
    la superficie contra las curvas y eso mueve la clase alta; más vecinos lo
    quitan hasta que deja de moverse. Recorre `enes` en orden y PARA en cuanto
    dos `n` seguidos dan la misma cifra dentro de `tolerancia`: la rodilla es
    el menor de los dos. Así el resultado no depende de dónde acabe `enes`
    (extenderla no lo mueve, porque el barrido ya no llega ahí) y los `n`
    grandes, que dominan el costo, solo se pagan si hacen falta.

    `meseta_ha` (ha con pendiente < 1 %) va en las filas como diagnóstico del
    terraceo, no como criterio: anclaría la rodilla al mínimo de la rejilla.

    POR QUÉ `h` Y EL EXTENT NO SE MUEVEN. `cuadros_mde` da el mismo
    `cuadro_salida` en todas las corridas (el bbox de `zona`); lo único que
    crece con `n` es `cuadro_recorte`, o sea cuántas curvas de FUERA sostienen
    el borde, que es exactamente lo que un vecindario más grande necesita.
    Así `n` es la única variable. λ se mide UNA vez sobre ese extent fijo y se
    reparte a todas las corridas.

    Parámetros
    ----------
    zona : polígono sobre el que se mide (una ventana representativa, o el
        predio). Su bbox es el extent de salida de todas las corridas.
    resolucion : resolución, la MISMA en todo el barrido.
    intervalo_terreno : `equidistancia / p_max`, el intervalo de muestreo
        medido sobre el TERRENO. Va al margen de borde
        (`ceil(sqrt(n*i*s/pi)/h)+2`); el muestreo que recibe `interpolar_mde`
        es este por `zigzag`, porque la API muestrea sobre el TRAZO.
    s_vacio : separación característica del VACÍO entre curvas (`2*q90` de
        `separaciones`), no la mediana: `s_equivalente_zona` es `2*q50` y vale
        la mitad, y meterla aquí pone 40 donde van 400.
    enes : los `n` a probar, en orden. El barrido para al converger.
    zigzag : `factor_zigzag` de `separacion_media`. 1.0 (default) = trazo sin
        zigzag; pásalo o el muestreo sale fino por ese factor.
    tolerancia : cambio relativo de la clase alta entre dos `n` seguidos que ya
        cuenta como "no se mueve" (default 0.01).
    p_max : límite de la clase alta, en FRACCIÓN (1.0 = 100 %), el de `_p_max`.
    equidistancia : None = se detecta.

    Devuelve `(filas, n_rodilla)`. Cada fila trae `vecinos`,
    `margen_borde_celdas`, `n_celdas`, `alta_ha`, `delta_pct` (de `alta_ha`
    contra el `n` inmediatamente menor), `meseta_ha` y `segundos`.

    `n_rodilla` es un `Rodilla`, o sea un `int` con `.avisos`. Si `.avisos` no
    esta vacio la cifra no convergio dentro de `enes`: el barrido devolvio un
    numero pero no midio nada, y quien lo use tiene que decirlo. Los avisos se
    imprimen SIEMPRE, con `geo.MOSTRAR` o sin el: `geo.MOSTRAR` decide si se
    imprime la TABLA, no si la cifra vale.
    """
    _exigir_mismo_crs(curvas, zona, "zona")
    if resolucion <= 0:
        raise ValueError(f"resolucion {resolucion} debe ser > 0")
    if intervalo_terreno <= 0 or s_vacio <= 0:
        raise ValueError(
            f"intervalo_terreno {intervalo_terreno} y s_vacio {s_vacio} deben ser > 0"
        )
    if not 0 <= tolerancia < 1:
        raise ValueError(f"tolerancia {tolerancia} fuera de [0, 1)")
    enes = sorted({int(n) for n in enes})
    if not enes:
        raise ValueError("`enes` vacío")
    if equidistancia is None:
        equidistancia = _detectar_equidistancia(curvas[campo])

    area_zona = superficie_total_ha(zona)
    intervalo_trazo = intervalo_terreno * zigzag
    # el extent de SALIDA no depende de `n`: se arma una vez con el margen mas
    # chico y se reusa, para que ni un redondeo lo mueva entre corridas.
    _, cuadro_salida = cuadros_mde(zona, resolucion=resolucion, margen_borde_celdas=0)
    lambda_m = _separacion_horizontal(curvas, _bounds_cuadro(curvas, cuadro_salida))
    ha_celda = resolucion**2 / 10_000

    if _mostrar():
        print(
            f"barrido de hasta {len(enes)} vecindarios sobre {area_zona:.1f} ha a "
            f"h={resolucion:g} m | alta = pendiente > {p_max * 100:g} %, para al "
            f"moverse <= {tolerancia:.0%}"
        )
        print(
            f"   {'n':>5} {'margen':>7} {'celdas':>10} {'alta_ha':>10} "
            f"{'delta%':>8} {'meseta_ha':>10} {'seg':>7}"
        )

    filas: list[dict[str, Any]] = []
    for n in enes:
        t0 = time.perf_counter()
        margen = margen_borde_celdas(n, intervalo_terreno, s_vacio, resolucion)
        cuadro_recorte, _ = cuadros_mde(zona, resolucion=resolucion, margen_borde_celdas=margen)
        mde, tf = _callado(interpolar_mde)(
            recortar(curvas, cuadro_recorte),
            campo,
            resolucion=resolucion,
            equidistancia=equidistancia,
            intervalo_muestreo=intervalo_trazo,
            cuadro=cuadro_salida,
            metodo="spline",
            vecinos=n,
            lambda_m=lambda_m,
        )
        pendiente = calcular_pendiente(mde, tf)
        en_zona = rasterizar(zona, pendiente.shape, tf, todo_tocado=True) == 1
        # conteo de pixeles, igual que el arnes de produccion. NaN > x es False,
        # asi que las celdas sin dato quedan fuera sin tratamiento aparte.
        alta = float((en_zona & (pendiente > p_max * 100)).sum()) * ha_celda
        meseta = float((en_zona & (pendiente < 1.0)).sum()) * ha_celda
        fila = {
            "vecinos": n,
            "margen_borde_celdas": margen,
            "n_celdas": int(mde.size),
            "alta_ha": alta,
            "delta_pct": _delta_pct(filas[-1]["alta_ha"], alta) if filas else float("nan"),
            "meseta_ha": meseta,
            "segundos": time.perf_counter() - t0,
        }
        filas.append(fila)
        if _mostrar():
            print(
                f"   {n:5d} {margen:7d} {fila['n_celdas']:10,d} {alta:10.2f} "
                f"{fila['delta_pct']:8.2f} {meseta:10.2f} {fila['segundos']:7.1f}"
            )
        # ponytail: para en el primer par de `n` seguidos que empata. Una cifra
        # que se quede quieta y luego vuelva a moverse engaña a esto; si pasa,
        # exigir dos pares seguidos.
        if len(filas) > 1 and abs(fila["delta_pct"]) <= tolerancia * 100:
            break

    avisos = _avisos_rodilla(filas, tolerancia)
    n_rodilla = filas[-1]["vecinos"] if avisos else filas[-2]["vecinos"]
    # Los avisos van FUERA de `geo.MOSTRAR`: esa bandera decide si se imprime la
    # TABLA, no si la cifra vale. Un aviso que no sale siempre no es un aviso.
    # Y viajan ademas en el resultado, para que quien
    # estampa la procedencia no tenga que re-derivar el criterio aqui.
    if _mostrar() and not avisos:
        print(
            f"   rodilla: n={n_rodilla} (margen "
            f"{next(f['margen_borde_celdas'] for f in filas if f['vecinos'] == n_rodilla)}"
            f" celdas), la clase alta se mueve <= {tolerancia:.0%} al pasar a "
            f"n={filas[-1]['vecinos']}."
        )
    for aviso in avisos:
        print(f"   OJO: {aviso}")
    return filas, Rodilla(n_rodilla, avisos)


def histograma_fase(
    mde: FloatArray,
    curvas: gpd.GeoDataFrame,
    campo: str,
    resolucion: float,
    mascara: npt.NDArray[Any] | None = None,
    ancho_bin: float | None = None,
) -> tuple[list[float], float, float, float, int]:
    """Terraceo del MDE contra la equidistancia de `curvas`, por fase.

    `φ(z) = (z − c0) mod e`, plegada a `[0, e/2]`. Bajo una rampa lineal, φ
    es uniforme con independencia de la separación local, la pendiente local
    y la hipsometría: cualquier desviación es terraceo del interpolador, no
    terreno. `e` se detecta de `curvas[campo]` (el paso de la escalera de
    cotas), igual que `interpolar_mde`.

    Parámetros
    ----------
    resolucion : tamaño de celda de `mde` (unidades del CRS). Sin ella no hay
        forma de convertir conteo de píxeles a hectáreas: pásale
        `abs(transform[0])`, igual que a `calcular_pendiente`.
    mascara : celdas a considerar. None (default) = todo `mde` con dato
        (`np.isfinite`).
    ancho_bin : None (default) = `e/160`. Tiene que dividir `e/2` exacto o el
        conteo redondea, y `phi_0` = `e/10` tiene que caer en un borde del
        histograma.

    Devuelve (densidad, exceso, evacuacion, atomo, n_intervalos_completos):
    - `densidad` : lista, densidad de φ por bin relativa a la uniforme
      (1.0 = sin terraceo en ese bin).
    - `exceso` : en HECTÁREAS, superficie con φ ≤ `phi_0` por encima de la que
      pondría una fase uniforme. Único citable junto con su δ; la razón sola
      solo ordena.
    - `evacuacion` : fracción con φ ≥ `e/4` (mitad superior del histograma
      plegado), blanco 1.0. Bajar es empeorar.
    - `atomo` : en hectáreas, superficie con φ < 1 mm (pegada a la cota).
    - `n_intervalos_completos` : de los `len(cotas)-1` intervalos entre cotas
      consecutivas, cuántos tienen las 80 sub-franjas de fase cubiertas. Un
      intervalo parcialmente cubierto sesga la fase (+9.9 % medido; los
      parciales suelen ser las cimas), así que se cuenta aparte en vez de
      mezclarse con el resto.
    """
    cotas = sorted(pd.to_numeric(curvas[campo], errors="coerce").dropna().unique())
    if len(cotas) < 2:
        raise ValueError("menos de dos cotas: la fase no esta definida")
    e = _detectar_equidistancia(cotas)
    if e is None:
        raise ValueError("no se pudo detectar la equidistancia de curvas[campo]")
    ancho_bin = e / 160 if ancho_bin is None else ancho_bin
    phi_0 = e / 10

    nf = int(round(e / 2 / ancho_bin))
    if abs(nf * ancho_bin - e / 2) > 1e-9:
        raise ValueError(f"ancho_bin={ancho_bin:g} no divide e/2={e / 2:g}")
    if abs(phi_0 / ancho_bin - round(phi_0 / ancho_bin)) > 1e-9 or phi_0 > e / 2:
        raise ValueError(f"phi_0={phi_0:g} no cae en un borde del histograma")
    idx_phi0 = int(round(phi_0 / ancho_bin)) - 1

    dentro = np.isfinite(mde) if mascara is None else (mascara & np.isfinite(mde))
    n = int(dentro.sum())
    if n == 0:
        raise ValueError("la mascara no deja ningun pixel con dato")

    z = mde[dentro] - cotas[0]
    fase = z % e
    fase = np.where(fase <= e / 2, fase, e - fase)

    bin_fase = np.minimum((fase / ancho_bin).astype(int), nf - 1)
    conteo = np.bincount(bin_fase, minlength=nf)[:nf]
    uniforme = ancho_bin / (e / 2)
    densidad = (conteo / n / uniforme).tolist()
    f_phi0 = float(conteo[: idx_phi0 + 1].sum()) / n
    exceso_frac = f_phi0 - phi_0 / (e / 2)
    evacuacion = float(conteo[nf // 2 :].sum()) / n * 2
    frac_atomo = float((fase < 1e-3).sum()) / n

    # ponytail: no hay chequeo exceso contra deficit. Con un solo histograma son
    # la misma expresion (f_phi0 - 2*phi_0/e) y no puede fallar nunca. Si algun dia se
    # quiere de verdad, hay que contar exceso y deficit de mascaras separadas.
    # ponytail: asume escalera de cotas SIN huecos (intervalo k = [k*e, (k+1)*e)
    # desde cotas[0]). Un nivel faltante corre el conteo y recorta por arriba;
    # si el reparto de saltos de detectar_cota avisa huecos, este numero se lee
    # con esa sal. Upgrade: indexar contra las cotas reales, no contra k*e.
    n_sub, n_int = 80, len(cotas) - 1
    ok = (z >= 0) & (z < n_int * e)
    idx = (z[ok] / e).astype(int)
    sub = np.minimum((z[ok] % e / (e / n_sub)).astype(int), n_sub - 1)
    visto = np.zeros((n_int, n_sub), bool)
    visto[idx, sub] = True
    n_completos = int(visto.all(axis=1).sum())

    px_ha = resolucion**2 / 10_000
    return (
        densidad,
        exceso_frac * n * px_ha,
        evacuacion,
        frac_atomo * n * px_ha,
        n_completos,
    )


def amplitud_rizo(
    mde: FloatArray,
    transform: Affine,
    curvas: gpd.GeoDataFrame,
    campo: str,
    mascara: npt.NDArray[Any] | None = None,
    cuantil: float = 99.0,
) -> tuple[float, dict[str, float]]:
    """PISO de la amplitud `A` del rizo del interpolador, en metros.

    LO PRIMERO, y va antes que el metodo porque condiciona como se cita: **esto
    da un PISO de `A`, no `A`.** El rizo que ondula DENTRO del intervalo entre
    las dos cotas que enmarcan un pixel no se ve desde aqui, asi que la
    amplitud real es esta o mayor. Un piso es utilizable en un solo sentido: si
    ya supera lo que toleras, terminaste; si no lo supera, NO dice que el rizo
    sea menor.

    Al reportar la cifra, di que es un piso: un techo de `A` sería otra
    afirmación.

    EL METODO. Entre dos curvas de nivel contiguas, el terreno esta acotado por
    ellas: una superficie sin rizo se queda en `[z_lo, z_hi]`. Salirse de ahi no
    es terreno ni curvatura, es artefacto puro. Se mide `max(0, z - z_hi,
    z_lo - z)` por pixel y se toma un cuantil alto.

    Las dos cotas envolventes salen de una transformada de distancia POR NIVEL
    (una por cota, sobre la misma malla del MDE), quedandose con los dos
    niveles mas cercanos a cada pixel.

    LA DISCRIMINANTE, que es la unica parte no obvia: los dos niveles mas
    cercanos no siempre ENMARCAN al pixel. Dentro de la curva de cumbre no hay
    nada por encima, y sus dos vecinas mas cercanas caen las dos hacia el mismo
    lado; contarlas como banda inventaria una excursion enorme en cada cima y en
    cada hoya. Se distingue por geometria: un pixel esta ENTRE dos curvas si los
    puntos mas cercanos de una y de otra quedan a lados opuestos, o sea si el
    producto escalar de los dos vectores es negativo. Cimas y hoyas CERRADAS
    quedan fuera, y salen contadas en `frac_usada`.

    LO QUE LA DISCRIMINANTE NO ATRAPA, y hay que leerlo antes de citar la cifra:
    el lomo o la vaguada que NO llegan al siguiente nivel. Un lomo cuya cumbre
    esta a 118 con la curva de 100 a los dos lados no tiene curva de 120 que lo
    cierre; sus dos niveles mas cercanos pueden salir 100 por un flanco y 80 por
    el otro, que son lados opuestos, y entonces la banda que se le atribuye es
    `[80, 100]` cuando la verdadera es `[100, 120]`. Ese pixel reporta una
    excursion de hasta casi `e` que NO es rizo: es una curva que no existe. Por
    eso una excursion comparable a la equidistancia se avisa: a esa escala la
    cifra habla de la hipsometria, no del interpolador.

    **BAJAR EL CUANTIL NO ESTABILIZA.** La cola sin hombro es del METODO, no
    del predio: mientras la discriminante no cierre el lomo sin curva, la cola
    la dominan esos pixeles y no el rizo. No hay que volver a correrla
    esperando otro predio.

    **Es CIEGA por debajo de `A ~ 0.6*e`** (calibrada contra una `A` conocida),
    y en el primer punto que ve recupera el ~12 %. Depende de `A/e`, no de
    `lambda`, `h` ni predio. Consecuencias:

    - **`A` = 0.00 m NO significa "no hay rizo".** Significa `A` por debajo del
      piso de ceguera, que con `e` = 20 m es una banda de 0 a ~12 m.
    - **El test de rayo por pixel NO puede arreglarlo**: la ceguera no es de la
      discriminante, es del metodo. El rizo que no se sale de `[z_lo, z_hi]` no
      deja rastro que medir desde aqui.
    - Para ver rizo por debajo de `0.6*e` hace falta OTRO observable, no un
      arreglo de este.

    Calibracion (rampa + rizo sinusoidal, `e` = 20 m): `A` puesta 12
    mide 0.00, 16.669 mide 1.962 (11.8 %), 24 mide 9.101 (37.9 %), igual en
    los dos regimenes. El barrido q50..q99 no tiene hombro en ningun predio.

    Parametros
    ----------
    mde : el MDE que se va a auditar, ya interpolado. Se mide sobre el que se
        entrega, no sobre uno hecho aparte para medir.
    transform : el `Affine` de `mde`. Las curvas se rasterizan sobre esa misma
        malla; con otro transform el resultado no significa nada.
    mascara : celdas a considerar. None (default) = todo `mde` con dato.
    cuantil : percentil de la excursion (default 99). ALTO a proposito: la
        excursion solo asoma cerca de las curvas, asi que un cuantil central
        mide sobre todo pixeles de mitad de banda, donde no hay nada que ver.
        Mira `frac_excursion` antes que el cuantil: si es 0.01, cualquier
        percentil por debajo de 99 sale 0.00 y no es que no haya medicion, es
        que no hay excursion.

    Devuelve (a, detalle):
    - `a` : el piso de `A`, en metros (unidades de `mde`).
    - `detalle` : `px_usados`, `frac_usada`, `frac_excursion`, `excursion_max`,
      `cuantil`, `niveles`. `frac_usada` no es decoracion: un piso medido sobre
      el 5 % de la malla no es la misma afirmacion que uno medido sobre el 80 %.
    """
    if not 0 < cuantil <= 100:
        raise ValueError(f"cuantil {cuantil} fuera de (0, 100]")
    vals = pd.to_numeric(curvas[campo], errors="coerce")
    cotas = np.asarray(sorted(vals.dropna().unique()), dtype=float)
    if cotas.size < 2:
        raise ValueError("menos de dos cotas: no hay banda que enmarque nada")

    forma = mde.shape
    hx, hy = abs(transform.a), abs(transform.e)
    d1 = np.full(forma, np.inf)
    d2 = np.full(forma, np.inf)
    k1 = np.full(forma, -1, dtype=np.int32)
    k2 = np.full(forma, -1, dtype=np.int32)
    # punto mas cercano de cada uno de los dos niveles, en (fila, col)
    p1 = np.zeros((2, *forma), dtype=np.int32)
    p2 = np.zeros((2, *forma), dtype=np.int32)

    for k, z in enumerate(cotas):
        # ponytail: `todo_tocado` en False, al reves que `raster_distancia`. Alli
        # la mascara es un paso intermedio que se recorta en vector despues; aqui
        # la curva quemada ES contra lo que se mide, y una traza de dos pixeles de
        # ancho deja a los del borde a distancia 0 de su propia curva, que es
        # justo donde asoma la excursion.
        quemado = rasterizar(curvas[vals == z], forma, transform) == 1
        if not quemado.any():
            continue  # nivel que no toca la malla: nunca sale como envolvente
        d, idx = distance_transform_edt(~quemado, sampling=(hy, hx), return_indices=True)
        mejor = d < d1
        segundo = ~mejor & (d < d2)
        # el segundo se resuelve ANTES que el primero: si este nivel pasa a ser
        # el mas cercano, el que baja a segundo es el primero VIEJO.
        d2 = np.where(mejor, d1, np.where(segundo, d, d2))
        k2 = np.where(mejor, k1, np.where(segundo, k, k2))
        p2 = np.where(mejor, p1, np.where(segundo, idx, p2))
        d1 = np.where(mejor, d, d1)
        k1 = np.where(mejor, k, k1)
        p1 = np.where(mejor, idx, p1)

    filas, cols = np.indices(forma)
    # lados opuestos = producto escalar negativo. Ver LA DISCRIMINANTE.
    v1x, v1y = (p1[1] - cols) * hx, (p1[0] - filas) * hy
    v2x, v2y = (p2[1] - cols) * hx, (p2[0] - filas) * hy
    escalar = v1x * v2x + v1y * v2y
    dentro = np.isfinite(mde) if mascara is None else (mascara & np.isfinite(mde))
    util = dentro & (k2 >= 0) & (np.abs(k1 - k2) == 1) & (escalar < 0)
    n_dentro = int(dentro.sum())
    if n_dentro == 0:
        raise ValueError("la mascara no deja ningun pixel con dato")
    if not util.any():
        raise ValueError(
            "ningun pixel queda enmarcado por dos cotas contiguas: comprueba que "
            "`transform` es el de `mde` y que las curvas caen en esa malla"
        )

    z_a, z_b = cotas[k1[util]], cotas[k2[util]]
    z_lo, z_hi = np.minimum(z_a, z_b), np.maximum(z_a, z_b)
    z = mde[util]
    excursion = np.maximum(0.0, np.maximum(z - z_hi, z_lo - z))
    a = float(np.percentile(excursion, cuantil))
    n_util = int(util.sum())
    detalle = {
        "cuantil": float(cuantil),
        "px_usados": float(n_util),
        "frac_usada": n_util / n_dentro,
        "frac_excursion": float((excursion > 0).mean()),
        "excursion_max": float(excursion.max()),
        "niveles": float(cotas.size),
    }
    e = _detectar_equidistancia(cotas)
    if _mostrar():
        print(
            f"amplitud_rizo: A >= {a:.2f} m (p{cuantil:g} de la excursion fuera de "
            f"[z_lo, z_hi] sobre {n_util:,} px, {detalle['frac_usada'] * 100:.0f} % "
            f"de la mascara; maximo {detalle['excursion_max']:.2f} m)"
        )
        print(
            f"   Excursion > 0 en el {detalle['frac_excursion'] * 100:.1f} % de esos "
            f"pixeles. Es un PISO de A, no A: el rizo que"
        )
        print(
            "   ondula DENTRO del intervalo no se ve. Si ya supera lo que toleras, "
            "terminaste; si no,"
        )
        print("   no dice que sea menor.")
    # guardas sobre la SALIDA: las dos formas de que la cifra no sea rizo. FUERA
    # de `geo.MOSTRAR`: ese decide el reporte, no si la cifra vale.
    if detalle["frac_usada"] < 0.5:
        print(
            f"   Aviso: amplitud_rizo: solo el {detalle['frac_usada'] * 100:.0f} % de "
            f"la mascara queda entre dos cotas contiguas. Cimas y hoyas cerradas no "
            f"tienen envolvente y no cuentan; si la fraccion es baja de mas, "
            f"revisa que las curvas cubran el extent de `mde`."
        )
    if e is not None and a > e / 4:
        print(
            f"   Aviso: amplitud_rizo: {a:.2f} m es mucho para un rizo con "
            f"equidistancia {e:g} m. A esa escala la cifra suele ser el lomo o la "
            f"vaguada que no llegan al siguiente nivel (no hay curva que los "
            f"cierre, y se les atribuye la banda de abajo), no el interpolador. "
            f"Baja el `cuantil` y mira donde se estabiliza."
        )
    return a, detalle


def _veredicto(fila: dict[str, Any], h: float, metodo: str) -> tuple[str, str]:
    """SI / CONDICIONAL / NO de un corte, con el criterio de SU metodo."""
    s = fila["s_sostenida"]
    if s != s:  # NaN: ninguna curva dentro
        return "NO", "sin curvas dentro: rizo puro"
    if h > s:
        return "NO", f"ancho: h {h:g} > s_sostenida {s:.2f}"
    if metodo != "tin" and h < fila["piso_local"]:
        return "CONDICIONAL", f"alias: h {h:g} < piso_local {fila['piso_local']:.2f}"
    return "SI", ""


def juzgar_clasificaciones(
    candidatas: dict[str, tuple[gpd.GeoDataFrame, str, float]],
    curvas: gpd.GeoDataFrame,
    campo: str,
    zona: gpd.GeoDataFrame,
    rangos: list[tuple[Any, float, float]],
    equidistancia: float,
    zigzag: float,
    campo_clase: str = "PENDIENTES",
) -> tuple[list[dict[str, Any]], str | None]:
    """Juzga N clasificaciones del MISMO predio contra la carta y recomienda una.

    Cada capa de clases trae la etiqueta de su rango en `campo_clase`
    (`zonificar(..., campo=...)`).

    `candidatas` = `{nombre: (clases, metodo, h)}`, con `metodo` en `"tin"` /
    `"spline"` y `h` la resolucion de SU MDE. Todo se mide contra la
    CARTOGRAFIA, no contra el MDE: juzgar pendientes con un sombreado del mismo
    raster es circular.

    Por candidata, sobre la clase mas alta de `rangos`:

    - **veredicto**, con el criterio de SU metodo: `h <=
      s_sostenida` para los dos (el objeto cabe en el pixel), y ademas `h >=
      piso_local` para el spline (el piso antialias es del METODO: un TIN no
      ondula). Falla de ancho = `NO`. Spline que falla solo por alias =
      `CONDICIONAL`: es entregable si la amplitud del rizo esta MEDIDA y su
      espuria no alcanza la clase, y eso no lo
      decide una funcion.
    - **% visto**: largo del escarpe cartografico que cae dentro de la clase,
      sobre el largo efectivo (`ancho_banda(devolver_banda=True)`). Es la cifra
      comparable: area contra area (`pct_blanco`) premia al que dibuja mas
      ancho, el largo no.
    - **ancho_x_w**: area de la clase / largo visto, en veces `w`.
    - **corte**: el corte acumulado MAS ALTO que pasa sin condiciones, que es
      lo que se entrega cuando la clase alta no.

    LA RECOMENDACION es una regla escrita, no un optimo: entre las candidatas
    `SI` y `CONDICIONAL`, la de mayor % visto. Si ninguna pasa, None. El metodo
    NO se elige por receta: en un predio gana el TIN, en otro el spline.

    `zigzag` es el de `derivar_parametros` (sobre la zona): divide el largo de
    TRAZO de la banda. Pasarlo mal mueve el % visto por el mismo factor.

    Devuelve `(filas, recomendada)`. `filas`: una por candidata, en el orden de
    entrada, con `nombre`, `metodo`, `h`, `area_ha`, `veredicto`, `motivo`,
    `s_sostenida`, `piso_local`, `pct_visto`, `ancho_x_w`, `pct_blanco`,
    `corte` (dict de `sostenimiento_por_clase` o None) y `cortes` (todos los
    cortes si la clase alta no pasa; si pasa, solo el suyo).

    Tambien reporta, en `geo.MOSTRAR`, la BANDA de la clase alta entre las que
    pasan: dos metodos legitimos que difieren dan una banda, no un numero.

    COSTO: un corte de `sostenimiento_por_clase` por candidata, y los ocho solo
    si la clase alta no pasa (cada corte, minutos en un predio de miles de ha),
    mas un `ancho_banda`.
    """
    if not candidatas:
        raise ValueError("sin candidatas")
    for nombre, (_, metodo, h) in candidatas.items():
        if metodo not in ("tin", "spline", "tps"):
            raise ValueError(f"{nombre}: metodo '{metodo}', usa 'tin' o 'spline'")
        if not h > 0:
            raise ValueError(f"{nombre}: h {h} tiene que ser > 0")
    alta = max(rangos, key=lambda r: r[1])
    etiqueta_alta, p_alta = alta[0], alta[1]
    _, resumen, banda = _callado(ancho_banda)(
        curvas,
        campo,
        zona,
        equidistancia,
        zigzag,
        pendiente_pct=p_alta,
        devolver_banda=True,
    )
    w = float(resumen["ancho_m"])
    largo_ef = float(resumen["largo_efectivo_m"])
    blanco = float(resumen["area_ha"])

    filas: list[dict[str, Any]] = []
    for nombre, (clases, metodo, h) in candidatas.items():
        if campo_clase not in clases.columns:
            raise ValueError(f"{nombre}: sin campo '{campo_clase}'")
        # Primero SOLO la clase alta: cada corte es una escalera de buffers
        # (minutos en un predio grande) y los de abajo solo hacen falta si la
        # alta no pasa. Un rango con solo la alta da exactamente ese corte.
        cortes = _callado(sostenimiento_por_clase)(
            clases, curvas, campo_clase, [alta], equidistancia
        )
        f_alta = cortes[0]
        v, motivo = _veredicto(f_alta, h, metodo)
        if v != "SI":
            cortes = sostenimiento_por_clase(
                clases, curvas, campo_clase, rangos, equidistancia
            )
        capa = clases[clases[campo_clase] == etiqueta_alta]
        area = superficie_total_ha(capa) if len(capa) else 0.0
        visto = longitud_total_m(recortar(banda, capa)) / zigzag if area else 0.0
        filas.append(
            {
                "nombre": nombre,
                "metodo": metodo,
                "h": h,
                "area_ha": area,
                "veredicto": v,
                "motivo": motivo,
                "s_sostenida": f_alta["s_sostenida"],
                "piso_local": f_alta["piso_local"],
                "pct_visto": 100 * visto / largo_ef,
                "ancho_x_w": area * 10_000 / visto / w if visto else float("nan"),
                "pct_blanco": 100 * area / blanco,
                "corte": next(
                    (c for c in cortes if _veredicto(c, h, metodo)[0] == "SI"), None
                ),
                "cortes": cortes,
            }
        )

    vivas = [f for f in filas if f["veredicto"] in ("SI", "CONDICIONAL")]
    recomendada = max(vivas, key=lambda f: f["pct_visto"])["nombre"] if vivas else None

    if _mostrar():
        print(
            f"\nescarpe de carta ({etiqueta_alta}): w {w:.4f} m | largo efectivo "
            f"{largo_ef:,.0f} m | blanco {blanco:.2f} ha"
        )
        print(
            f"{'candidata':>14} {'metodo':>7} {'h':>5} {'ha':>9} {'% blanco':>9} "
            f"{'% visto':>8} {'x w':>6}  veredicto"
        )
        for f in filas:
            print(
                f"{f['nombre']:>14} {f['metodo']:>7} {f['h']:5g} {f['area_ha']:9.2f} "
                f"{f['pct_blanco']:8.1f}% {f['pct_visto']:7.1f}% {f['ancho_x_w']:6.2f}"
                f"  {f['veredicto']} {f['motivo']}"
            )
            if f["veredicto"] != "SI":
                c = f["corte"]
                txt = (
                    "ninguno"
                    if c is None
                    else f">={c['corte']:g} = {c['area_ha']:.2f} ha"
                )
                print(f"{'':>14} corte que pasa sin condiciones: {txt}")
        if recomendada is None:
            print(f"RECOMENDADA: ninguna sostiene {etiqueta_alta}; no se cita.")
        else:
            r = next(f for f in filas if f["nombre"] == recomendada)
            print(f"RECOMENDADA: {recomendada} (mayor % visto entre las que pasan)")
            if r["veredicto"] == "CONDICIONAL":
                print(
                    f"   CONDICIONAL: {r['motivo']}. Entregable solo con la amplitud"
                    " del rizo MEDIDA y su espuria por debajo de la clase."
                )
            if len(vivas) > 1:
                areas = [f["area_ha"] for f in vivas]
                print(
                    f"   BANDA de {etiqueta_alta} entre las que pasan: "
                    f"{min(areas):.2f} a {max(areas):.2f} ha. Se cita como banda."
                )
    return filas, recomendada
