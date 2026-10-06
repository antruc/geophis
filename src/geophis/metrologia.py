"""Las REGLAS: aritmetica pura sobre numeros, sin tocar un raster.

Todo lo de aqui responde a "que valor tiene que tener este parametro" con una
formula cerrada, no con una medicion. No importa `raster` ni `geometria`: es la
capa de ABAJO y la importan las de arriba sin ciclos. Aqui NADA mide: `w` y
`lambda` los da quien llama. Lo que si mide esta en `barridos.py`.
"""

import math
from typing import Any

import numpy as np
import numpy.typing as npt

# Las CONVENCIONES de oficio: la misma cifra en todos los predios y sin medición
# que la sustituya. `derivar_parametros` las lleva a la tabla de procedencia.
LADO_MM = 2.0  # lado mínimo legible en carta; entra al cuadrado en la UMM
N_MIN = 4  # píxeles que tienen que caber dentro de la UMM
N_CRUCE = 3  # celdas que tienen que CRUZAR el ancho de la clase para trazarla
CUANTIL_VACIO = 90.0  # percentil de distancias que define el VACIO (2*q90)


def _de_parametros(parametros: dict[str, Any] | None, **valores: Any) -> dict[str, Any]:
    """Lo que quedó en None se toma de `parametros`, el dict de `derivar_parametros`.

    Un valor pasado a mano manda sobre el del dict. Devuelve los valores en el
    mismo orden en que entraron, para desempaquetar con `.values()`.
    """
    if parametros is None:
        return valores
    return {k: parametros.get(k) if v is None else v for k, v in valores.items()}


def _redondear_limpio(x: float, hacia: str = "cerca") -> float:
    """Ajusta un tamaño al valor 'limpio' (1, 2, 2.5, 5, 10...) en una dirección.

    `hacia` es de qué lado se puede fallar:

        "abajo"  para un TECHO (`h` <=): redondear hacia arriba lo violaría
        "arriba" para un PISO  (`h` >=): redondear hacia abajo lo violaría
        "cerca"  cuando no hay condición que respetar

    Techo conocido: la escalera es rala entre 5 y 10, así que `"arriba"` puede
    dar un salto grande (un piso de 6 sube a 10, un 67 %). Mira
    `resolucion_piso`, que se reporta crudo al lado, antes de citar el `h`.
    """
    if x <= 0:
        return x
    base = 10.0 ** math.floor(math.log10(x))
    candidatos = [m * base for m in (1, 2, 2.5, 5, 10)]
    if hacia == "abajo":
        # `base` <= x siempre (base <= x < 10*base), asi que nunca queda vacio
        return max(c for c in candidatos if c <= x * (1 + 1e-12))
    if hacia == "arriba":
        # `10*base` > x siempre
        return min(c for c in candidatos if c >= x * (1 - 1e-12))
    if hacia != "cerca":
        raise ValueError(f"hacia {hacia!r}: usa 'cerca', 'abajo' o 'arriba'")
    return min(candidatos, key=lambda c: abs(c - x))


def _paso_escalera(cotas: npt.ArrayLike) -> tuple[float, float]:
    """(paso, regularidad) de la escalera que forman las cotas únicas.

    Cada salto observado entre cotas únicas consecutivas se prueba como paso
    candidato; gana el que explica más saltos como MÚLTIPLOS suyos (empate al
    más chico). Un nivel faltante deja un salto de `2·paso` y sigue contando.
    Devuelve `(nan, 0.0)` si no hay ningún salto >= 1 m.
    """
    u = np.unique(np.asarray(cotas, dtype=float))
    u = u[~np.isnan(u)]
    d = np.round(np.diff(u), 3)
    d = d[d > 0]
    mejor_paso, mejor_reg = float("nan"), -1.0
    for p in np.unique(d):
        # ponytail: pasos < 1 m descartados; una capa con equidistancia
        # 0.5 m (escala muy grande) no se detecta. Techo conocido: bajar
        # la guarda a 0.5 si algun dia llega una capa asi.
        if p < 1:
            continue
        reg = float((np.abs(d / p - np.round(d / p)) < 1e-3).mean())
        if reg > mejor_reg + 1e-9 or (abs(reg - mejor_reg) < 1e-9 and p < mejor_paso):
            mejor_paso, mejor_reg = float(p), reg
    return mejor_paso, max(mejor_reg, 0.0)


def _detectar_equidistancia(cotas: npt.ArrayLike) -> float | None:
    """Equidistancia de las cotas: el paso de `_paso_escalera`, el mismo que `detectar_cota`.

    No la moda de los saltos: con curvas faltantes el salto doble puede ser el
    más frecuente (0, 20, 40, 80, 120... da 40). Devuelve None si no hay paso.
    """
    paso, _ = _paso_escalera(cotas)
    return None if math.isnan(paso) else paso


def _pendiente_espuria(amplitud: float, resolucion: float, lam: float) -> float:
    """Pendiente % que el rizo del spline mete en la ventana de Horn.

    `A*sin(2*pi*h/λ)/h`, con `A` la amplitud MEDIDA del rizo y λ la separación
    entre curvas. Se anula en `2h = λ` (el piso antialias) y crece de forma
    monótona por debajo, hasta `A/h`. Devuelve porcentaje, la misma unidad que
    `calcular_pendiente`.
    """
    if amplitud < 0:
        raise ValueError(f"amplitud {amplitud} debe ser >= 0")
    if resolucion <= 0 or lam <= 0:
        raise ValueError(f"resolucion {resolucion} y lambda {lam} deben ser > 0")
    return amplitud * abs(math.sin(2 * math.pi * resolucion / lam)) / resolucion * 100


def _p_max(rangos: list[tuple[Any, float, float]]) -> float:
    """Limite superior de pendiente (fraccion) de una lista de rangos.

    Cota FINITA mas alta entre todos los limites: el rango abierto de arriba
    (`>100`, con `inf` de tope) aporta su limite inferior.
    """
    finitos = [v for _, mn, mx in rangos for v in (mn, mx) if math.isfinite(v)]
    p = max(finitos) / 100
    if p <= 0:
        raise ValueError(f"rangos sin limite de pendiente positivo: {rangos}")
    return p


def min_pixeles_umm(umm_m2: float, resolucion: float) -> int:
    """Píxeles mínimos que hace una UMM a esa resolución: `ceil(umm_m2 / h^2)`.

    El `min_pixeles` de `limpiar_moteado`. `ceil` y no `round`: la UMM es un
    MÍNIMO, y redondear hacia abajo entrega manchas que no la alcanzan.

    OJO al leer un barrido: es ENTERO y salta de golpe (con umm 400, `h`=19 da
    2 y `h`=25.3 da 1). Y `limpiar_moteado` ABSORBE en el vecino mayor, así que
    endurecerlo puede AGRANDAR una clase. Ver `barrer_resolucion(min_pixeles=...)`.
    """
    if umm_m2 <= 0 or resolucion <= 0:
        raise ValueError(f"umm_m2 {umm_m2} y resolucion {resolucion} deben ser > 0")
    return math.ceil(umm_m2 / resolucion**2)


def umbral_celdas(area_aportadora_ha: float, resolucion: float) -> int:
    """Celdas que drenan a una superficie dada: `ceil(area_ha * 10000 / h^2)`.

    El `umbral` de `red_drenaje`, `orden_cauces` y `delimitar_cuenca`, que lo
    reciben en CELDAS. Las celdas no son portables entre resoluciones; lo que se
    declara es la SUPERFICIE aportadora. `ceil`: es un MÍNIMO.

    Techo conocido, de una celda: los consumidores usan `acumulacion > umbral`
    (estricto), así que la celda que drena exactamente la superficie declarada
    queda FUERA. Sesgo de un píxel, del lado seguro.
    """
    if area_aportadora_ha <= 0 or resolucion <= 0:
        raise ValueError(
            f"area_aportadora_ha {area_aportadora_ha} y resolucion {resolucion} "
            "deben ser > 0"
        )
    return math.ceil(area_aportadora_ha * 10_000 / resolucion**2)


def tolerancia_rasterizacion_ha(perimetro_m: float, resolucion: float) -> float:
    """Cuánto puede discrepar una capa RASTERIZADA de su polígono: `P*h/2/10000`.

    Tolerancia para cerrar contra el polígono de origen una capa que pasó por
    una máscara de celdas (`microcuencas`, `poligonizar`, cualquier vuelta
    ráster->vector): cada tramo del contorno cae hasta media celda dentro o
    fuera. Depende del perímetro, no de la superficie.

    Es un TECHO. Trampas:
    - **Compensarse no es cerrar**: un cierre NETO pequeño puede salir de un
      hueco y un exceso grandes que se cancelan. Compara hueco y exceso por
      separado (`comprobar_particion` ya lo hace).
    - `recortar` la capa contra el polígono NO arregla el cierre, lo empeora:
      quita lo que sobra y deja entero lo que la máscara no cubrió. Para
      cerrar exacto, compara contra la máscara.
    """
    if perimetro_m <= 0 or resolucion <= 0:
        raise ValueError(
            f"perimetro_m {perimetro_m} y resolucion {resolucion} deben ser > 0"
        )
    return perimetro_m * resolucion / 2 / 10_000


def margen_borde_celdas(
    vecinos: int, intervalo_terreno: float, s_vacio: float, resolucion: float
) -> int:
    """Celdas de datos extra que pide un vecindario de `vecinos` puntos.

    El `margen_borde_celdas` de `cuadros_mde`: `ceil(sqrt(n*i*s/pi) / h) + 2`.
    La celda del borde necesita curvas de FUERA del extent hasta el radio que
    abarcan sus `n` vecinos; el `+2` es el colchón de la ventana 3x3 de Horn.

    Tres trampas:
    - `s_vacio` es la separación del VACÍO (`2*q90` de `separaciones`), NUNCA la
      mediana (`s_equivalente_zona`, `2*q50`, vale la mitad).
    - `intervalo_terreno` es el muestreo sobre el TERRENO (`equidistancia /
      p_max`, `p_max` en FRACCIÓN), no el del trazo, que lleva el `zigzag`.
    - `s_vacio` depende del DOMINIO sobre el que se midió (predio, bbox,
      bbox+margen) y esta función no lo ve. Declara el dominio junto a la
      cifra. Pasarse es conservador; quedarse corto deja el borde sin soporte.

    Ejemplo aritmético: `n`=90, `i`=20, `s`=400, `h`=5 dan 98.
    """
    if vecinos < 1:
        raise ValueError(f"vecinos {vecinos} debe ser >= 1")
    if intervalo_terreno <= 0 or s_vacio <= 0 or resolucion <= 0:
        raise ValueError(
            f"intervalo_terreno {intervalo_terreno}, s_vacio {s_vacio} y "
            f"resolucion {resolucion} deben ser > 0"
        )
    radio = math.sqrt(vecinos * intervalo_terreno * s_vacio / math.pi)
    return math.ceil(radio / resolucion) + 2


def _distancia_max_al_limite(rangos: list[tuple[Any, float, float]]) -> float:
    """La distancia MÁXIMA posible entre una pendiente y su límite de clase.

    Mitad del ancho de la clase finita más estrecha: dentro de una clase de
    ancho `w` el punto más alejado de un límite es el centro. Es el juez de si
    una pendiente espuria puede mover un píxel de clase. NECESARIA Y NO
    SUFICIENTE: un píxel ya pegado a un límite se mueve con cualquier espuria.
    """
    anchos = [alto - bajo for _, bajo, alto in rangos if math.isfinite(alto)]
    if not anchos:
        raise ValueError("`rangos` no tiene ninguna clase de ancho finito")
    return min(anchos) / 2


def resolucion_regla(
    equidistancia: float,
    umm_m2: float,
    rangos: list[tuple[Any, float, float]],
    ancho_medido: float | None = None,
    lambda_m: float | None = None,
    amplitud_rizo: float | None = None,
) -> tuple[float, bool, dict[str, float]]:
    """La resolución `h` de un MDE de pendientes. Devuelve `(h, factible, cotas)`.

    `h` y `factible` salen de reducir `cotas` como se explica abajo; `cotas` es
    el dict de techos y piso, para reportar cuál mandó.

    TECHOS (`h` <= cada uno; manda el MENOR):
    - `umm` : `sqrt(umm_m2 / N_MIN)`, al menos `N_MIN` = 4 píxeles en la UMM.
    - `deteccion` : `equidistancia / (2 * p_max)`, un punto dentro del ancho
      transversal de la clase más alta (p_max = límite superior de `rangos`).
    - `trazado` : `w / N_CRUCE`, `N_CRUCE` = 3 celdas CRUZANDO ese ancho. `w` es
      `ancho_medido` si se da, si no el geométrico `equidistancia / p_max`.

    PISO (`h` >=), solo con `lambda_m` y si `amplitud_rizo` no lo desactiva:
    - `antialias` : `lambda_m / 2`. Con `metodo='spline'` la superficie ondula
      con la separación entre curvas (λ) y Horn la convierte en pendiente
      espuria (`_pendiente_espuria`), nula en `2h = λ`.

    REDUCCION a `h`: entre los techos manda el menor; el piso `antialias`, si
    está, tiene que quedar por debajo. Si lo supera no hay `h` que cumpla las
    dos: sale `factible=False` (un RESULTADO, no un error) y gana el PISO,
    porque con `h` en el techo el alias FABRICA superficie en la clase alta. El
    `h` sale REDONDEADO a un valor limpio hacia el lado que respeta la condición
    (`_redondear_limpio`): un techo hacia abajo, un piso hacia arriba. El aviso
    de INFACTIBLE sale SIEMPRE: es un defecto, no un reporte.

    NO reduzcas `cotas` con `min(...values())`: mezcla un piso con tres techos.
    Sin `lambda_m` el dict trae solo los tres techos.

    `lambda_m` sale de `separacion_media` o de `resumen['lambda_armonica']` de
    `separaciones`; NaN se ignora.

    `amplitud_rizo` : `A` MEDIDA, en metros (ver `amplitud_rizo`). Con `A` el
    piso deja de ser el juez: la espuria se evalúa en el techo que manda y, si
    queda bajo `_distancia_max_al_limite(rangos)`, el piso NO entra en el dict.

    Criterio NECESARIO Y NO SUFICIENTE:
    - Lo que gobierna es la distancia de la pendiente verdadera a su límite,
      que antes del MDE solo se acota por su máximo geométrico.
    - Un terreno justo sobre un límite se mueve con CUALQUIER espuria > 0: eso
      se mira después, con `pendiente_espuria_pct` y el mapa delante.
    - Se evalúa en UN punto, el techo que manda, no en todo el rango de `h`.
    """
    p_max = _p_max(rangos)
    w = equidistancia / p_max if ancho_medido is None else ancho_medido
    cotas = {
        "umm": math.sqrt(umm_m2 / N_MIN),
        "deteccion": equidistancia / (2 * p_max),
        "trazado": w / N_CRUCE,
    }
    if lambda_m is not None and math.isfinite(lambda_m):
        if amplitud_rizo is None:
            cotas["antialias"] = lambda_m / 2
        elif _pendiente_espuria(
            float(amplitud_rizo), min(cotas.values()), lambda_m
        ) >= _distancia_max_al_limite(rangos):
            cotas["antialias"] = lambda_m / 2
    return *_resolucion_h(cotas), cotas


def _resolucion_h(techos: dict[str, float]) -> tuple[float, bool]:
    """Reduce las cotas de `resolucion_regla` a `(h, factible)`. Ver allí."""
    solo_techos = {k: v for k, v in techos.items() if k != "antialias"}
    nombre = min(solo_techos, key=lambda k: solo_techos[k])
    techo = solo_techos[nombre]
    piso = techos.get("antialias")
    if piso is None or piso <= techo:
        return _redondear_limpio(techo, "abajo"), True
    h = _redondear_limpio(piso, "arriba")
    print(
        f"Aviso: INFACTIBLE. Piso antialias {piso:.1f} m > techo {techo:.1f} m "
        f"('{nombre}'). Se devuelve el PISO redondeado a {h:g} m: a "
        f"{techo:.1f} m el rizo del spline entra por alias y se cuenta como "
        f"pendiente. La clase más alta no se puede TRAZAR a esa UMM; "
        f"decláralo en vez de taparlo."
    )
    return h, False
