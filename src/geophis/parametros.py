"""Parámetros con PROCEDENCIA: se deriva lo derivable, se marca lo que no.

El problema que resuelve no es aritmético, es de trazabilidad. Las reglas
(`resolucion_regla`, `min_pixeles_umm`, `margen_borde_celdas`, `ancho_banda`,
`separacion_media`) encadenadas a mano en cada script dan un número, y el de
encadenarlas mal no se distingue del de encadenarlas bien. Perder cuatro decisiones al
cambiar de script (UMM, `h`, `vecinos`, exención del sieve) movió el `>100 %`
de 14.86 a 4.35 ha sin que ninguna avisara.

Dos piezas de este módulo:

- `Valor`, un float (o int) que además lleva `fuente` y `origen`. Pasa por
  cualquier sitio donde va un número, así que no hay que desempaquetarlo, pero
  la salida puede decir de dónde salió cada cifra.
- `derivar_parametros`, que encadena las mediciones en el único orden en que se
  pueden encadenar y devuelve todo con su procedencia. Lo que no puede derivar
  (`vecinos`) lo marca `default` en vez de fingir que lo eligió alguien.

Cinco fuentes, y la asimetría entre ellas es el punto entero:

    declarado   lo decide el ingeniero y ningún dato lo sabe (escala, rangos)
    medido      sale de la cartografía con un procedimiento (w, lambda, zigzag)
    derivado    consecuencia aritmética de los anteriores (UMM, min_pixeles...)
    convencion  constante de oficio, la MISMA en todos los predios y sin
                medición que la sustituya (2 mm de la UMM, 3 celdas de trazado)
    default     NADIE lo eligió, y SÍ hay una medición que lo sustituye. Es el
                que cuesta dinero. Es el único que sale en `sin_medir`.

`convencion` y `default` se separan a propósito. Los dos son valores que nadie
escribió, pero solo uno se arregla midiendo: `vecinos` tiene rodilla y `w` tiene
escalera de cortes, mientras que `n_cruce` = 3 es el mismo 3 en todos los
predios y no hay experimento que lo mueva. Meterlos en la misma lista haría que
`sin_medir` saliera con cinco nombres en cada corrida, y una lista que siempre
tiene contenido es una lista que se deja de mirar. Visibles los dos en la tabla;
accionable solo uno.

REGLA DE GOBERNANZA: una derivación REPORTA, no dictamina. `derivar_parametros`
no se niega a correr porque un valor venga de un default ni porque `h` caiga
bajo el piso antialias: calcula, marca y devuelve. Cuando el criterio derivado y
la medición directa chocan, gana la medición.
"""

import math
from collections.abc import Iterable
from typing import Any

import geopandas as gpd
import numpy as np

from .geometria import (
    ancho_banda,
    crear_cuadro,
    longitud_total_m,
    separacion_media,
    superficie_total_ha,
)
from .barridos import barrer_vecinos, raster_distancia
from .metrologia import (
    CUANTIL_VACIO,
    LADO_MM,
    N_CRUCE,
    N_MIN,
    _detectar_equidistancia,
    _p_max,
    _pendiente_espuria,
    margen_borde_celdas,
    min_pixeles_umm,
    resolucion_regla,
)
from .proyeccion import _exigir_proyectado
from ._salida import _callado, _mostrar

FUENTES = ("declarado", "medido", "derivado", "convencion", "default")

# `vecinos` de la RBF cuando nadie lo mide. Es el de `_interp_spline`, repetido
# aquí a proposito: este modulo tiene que poder decir "esto es un default" sin
# importar la funcion que lo aplica.
VECINOS_DEFAULT = 48


class Valor(float):
    """Un float que recuerda de dónde salió.

    Subclase de `float` y no un envoltorio: se pasa tal cual a `interpolar_mde`,
    a `numpy` o a un f-string sin desempaquetar nada. Lo único que agrega es
    `fuente` (una de `FUENTES`) y `origen` (la regla o medición, en texto).

    Ojo: cualquier operación aritmética devuelve un `float` pelado y PIERDE la
    procedencia. Es a propósito. Un valor calculado a partir de otro ya no tiene
    la misma procedencia que su operando, y heredarla sería mentir.
    """

    __slots__ = ("fuente", "origen")

    def __new__(cls, valor: float, fuente: str, origen: str) -> "Valor":
        obj = super().__new__(cls, valor)
        obj.fuente = fuente
        obj.origen = origen
        return obj

    def __repr__(self) -> str:
        return f"{float(self):g} [{self.fuente}: {self.origen}]"


class ValorEntero(int):
    """`Valor` para los parámetros que tienen que ser `int`.

    `vecinos`, `margen_borde_celdas` y `min_pixeles` van a APIs que indexan o
    cuentan con ellos (`RBFInterpolator(neighbors=...)`, `limpiar_moteado`), y
    un float ahí revienta o convierte en silencio. Misma interfaz que `Valor`.

    Sin `__slots__`, a diferencia de `Valor`: `int` es de longitud variable y
    CPython no admite `__slots__` no vacío en sus subtipos (`TypeError` al
    importar el módulo). `float` es de tamaño fijo y sí lo admite.
    """

    def __new__(cls, valor: int, fuente: str, origen: str) -> "ValorEntero":
        obj = super().__new__(cls, valor)
        obj.fuente = fuente
        obj.origen = origen
        return obj

    def __repr__(self) -> str:
        return f"{int(self)} [{self.fuente}: {self.origen}]"


def _v(valor: Any, fuente: str, origen: str) -> Any:
    """Construye el `Valor` del tipo que toca. `bool` no cuenta como entero."""
    if fuente not in FUENTES:
        raise ValueError(f"fuente '{fuente}' no válida (usa {FUENTES})")
    if isinstance(valor, bool) or not isinstance(valor, int):
        return Valor(valor, fuente, origen)
    return ValorEntero(valor, fuente, origen)


def _de_fuera(valor: Any, origen: str, entero: bool = False) -> Any:
    """El `Valor` de un número que ENTRA POR LA PUERTA, sin inventarle nada.

    Dos casos, y la asimetría es el punto:

    1. **Ya es un `Valor`/`ValorEntero`: se devuelve TAL CUAL.** Su fuente y su
       origen los puso quien de verdad lo produjo, y reconstruirlos aquí sería
       inventarlos. Esta es la mitad que cierra el LAVADO: un `default` que
       atraviesa una segunda llamada sigue siendo `default` y sigue saliendo en
       `sin_medir`.
    2. **Es un número pelado: entra como `declarado`.** La librería NO MIDIÓ
       NADA, así que `medido` sería una afirmación sobre algo que no le consta.
       Lo decidió quien llama, y su razón vive ahí.

    Corolario para quien llama: si quieres que la tabla diga de dónde salió tu
    número, no pases `int(...)`/`float(...)`, que es justo donde muere la
    procedencia. Pasa el `Valor` que te devolvió la medición.
    """
    if isinstance(valor, (Valor, ValorEntero)):
        return valor
    return _v(int(valor) if entero else float(valor), "declarado", origen)


def _huella(gdf: gpd.GeoDataFrame | None, medida: str) -> dict[str, Any]:
    """Con qué capa se midió: ruta si se sabe, y la huella geométrica siempre.

    La procedencia de un PARAMETRO no basta. `s_vacio` y `blanco_carta_ha` salen
    marcados `medido` con dos capas distintas del mismo predio y dan números
    distintos: medido sobre QUE no estaba en ninguna parte.

    La `ruta` sale de `attrs["ruta"]`, que pone `cargar`, y es BEST EFFORT: los
    `.attrs` de pandas no sobreviven a toda operacion. Lo que sí sobrevive es la
    huella: número de entidades, CRS, bbox y tamaño. Con eso, dos capas
    distintas del mismo predio ya no se leen igual, aunque nadie declare rutas.
    """
    if gdf is None:
        return {"ruta": "no dada", "n": 0}
    x0, y0, x1, y1 = (float(v) for v in gdf.total_bounds)
    return {
        "ruta": gdf.attrs.get("ruta", "no declarada (attrs sin `ruta`)"),
        "n": len(gdf),
        "crs": str(gdf.crs.name) if gdf.crs is not None else "sin CRS",
        "bbox": f"({x0:.0f}, {y0:.0f}) - ({x1:.0f}, {y1:.0f})",
        "medida": medida,
    }


def sin_medir(params: dict[str, Any]) -> list[str]:
    """Nombres de los parámetros que salieron de un default: nadie los eligió.

    Es la lista que hay que mirar antes de citar una cifra. Vacía = todos los
    números vienen de una declaración, una medición o una derivación de las dos.
    """
    return sorted(k for k, v in params.items() if getattr(v, "fuente", None) == "default")


def derivar_parametros(
    curvas: gpd.GeoDataFrame | None,
    campo: str,
    zona: gpd.GeoDataFrame | None,
    rangos: list[tuple[Any, float, float]],
    escala: float,
    *,
    vecinos: int | str | Iterable[int] | None = None,
    equidistancia: float | None = None,
    lambda_m: float | None = None,
    zigzag: float | None = None,
    ancho_medido: float | None = None,
    s_vacio: float | None = None,
    amplitud_rizo: float | None = None,
    resolucion: float | None = None,
    cuadro: gpd.GeoDataFrame | None = None,
    medir_ancho: bool = True,
    metodo: str = "spline",
) -> dict[str, Any]:
    """Encadena las mediciones y las reglas, y devuelve todo con su procedencia.

    Sustituye el bloque de constantes a mano de un script de producción. Lo que un valor dado a mano hace aquí es SALTARSE su
    medición, no discutir con ella: si lo pasas, entra como `declarado`.

    ORDEN, y es el único posible porque cada paso alimenta al siguiente:

        1. `equidistancia` (paso de la escalera de cotas) y `p_max` (de `rangos`)
        2. `lambda` y `zigzag` (`separacion_media` sobre `zona`)
        3. `w` (`ancho_banda`: escalera de cortes contra el nivel contiguo)
        4. techos y piso -> `h` (`resolucion_regla`)
        5. `s_vacio` = 2*q90 (`raster_distancia` + `separaciones`, ya con `h`)
        6. margen, min_pixeles, intervalos (aritmética sobre lo anterior)

    El paso 5 va DESPUES del 4 y no antes: `resolucion_regla` no usa `s_vacio`,
    así que no hay circularidad, y medir las distancias a la resolución que de
    verdad se va a usar es lo que hace comparables sus hectáreas con las del MDE.

    Parámetros
    ----------
    curvas, campo, zona : la cartografía y el polígono contra el que se mide.
        Pueden ser None SOLO si se pasan a mano todos los valores que se medirían
        (`equidistancia`, `lambda_m`, `zigzag`, `ancho_medido`, `s_vacio`); si
        falta alguno se levanta ValueError diciendo cuál.
    rangos : la lista de `reclasificar_rangos`. De aquí sale `p_max`, que es lo
        que gobierna el intervalo de muestreo y el techo de detección.
    escala : el denominador (25000 para 1:25 000). Es lo único DECLARADO de la
        malla; la UMM es consecuencia suya, no un número aparte.
    vecinos : la rodilla del barrido. NO es DERIVABLE (la fórmula
        `pi*k^2*s/(4*i)` solo dimensiona el orden de magnitud), pero sí es
        MEDIBLE, y las dos cosas no son lo mismo. Cuatro caminos:

        - un `ValorEntero` (el `p["vecinos"]` de otra llamada, o el de un
          barrido) : **pasa TAL CUAL, con su fuente y su origen intactos**. Es
          lo que impide que una segunda llamada lave el `default` de la primera.
        - un int pelado : entra como `declarado`. La librería no midió nada, así
          que no puede decir `medido`; la razón del número vive en quien lo pasó.
        - `"medir"` : se barre aquí con `barrer_vecinos`. Entra como `medido`
          SOLO si la clase alta convergió; si no, entra como `default` con
          el aviso pegado y sale en `sin_medir`, porque una rodilla sin
          convergencia es "nadie lo eligió" con otro nombre. **CUESTA UNA
          INTERPOLACION POR CADA `n`** hasta converger: es el único parámetro
          cuya medición se cuenta en minutos, y por eso no es el default.
        - una lista de `n` (p. ej. `(20, 32, 48)`) : igual que `"medir"`, pero
          barriendo ESA rejilla. Es lo primero que pide el aviso de "la clase
          alta no convergió".
        - None (default) : el default de la RBF, marcado `default`, y sale en
          `sin_medir`.

        Lo mismo vale para `equidistancia`, `lambda_m`, `zigzag`,
        `ancho_medido`, `s_vacio`, `h` y `amplitud_rizo`: los siete respetan el
        `Valor` que les llegue y marcan `declarado` el número pelado. NO les
        pases `int(...)` ni `float(...)`: ese cast es donde muere la
        procedencia.
    ancho_medido : `w` de la clase alta. None y `medir_ancho` = se mide aquí.
    s_vacio : separación del VACIO, `2*q90`. NUNCA `s_equivalente_zona`, que es
        `2*q50` y vale la mitad. None = se mide aquí.
    amplitud_rizo : `A` del rizo del spline, en metros, MEDIDA (ver
        `amplitud_rizo`). Si se da, se reporta la pendiente espuria en forma
        cerrada y esa medición manda sobre el piso antialias: se pasa a
        `resolucion_regla`, que retira el piso cuando la espuria en el techo
        que manda queda por debajo del ancho de la clase más estrecha de
        `rangos`. O sea que `factible` y `resolucion_regla` CAMBIAN al darla.
    resolucion : la resolución que se va a usar de verdad. None = la de la regla. Si la
        das y difiere de la regla, mandan las dos en su sitio: `h` para el margen
        y `min_pixeles` (describen la malla real), la regla como referencia.
    cuadro : el EXTENT de interpolación, o sea el `cuadro_salida` de
        `cuadros_mde`. Es el dominio sobre el que se miden `lambda`, el `zigzag`
        y `s_vacio`, y NO es lo mismo que `zona`. None (default) = el bbox de
        `zona`, que es justo lo que `cuadros_mde` devuelve como `cuadro_salida`
        (no depende del margen de borde, así que no hay circularidad).

        NO es un detalle: el dominio mueve lambda y `s_vacio` varios metros.
        Medido en un predio real, misma capa: polígono 76.15 / 340.1, bbox 74.90 / 260.0,
        bbox + 1500 m 68.34 / 186.8. La memoria cita 74.93, el bbox; con
        76.15 el piso antialias sale 1.2 m alto. Manda el extent porque es donde el spline
        construye la superficie y ondula: el piso antialias es `lambda/2`.

        `zigzag` y `w`, en cambio, se miden sobre `zona`. El zigzag convierte un
        muestreo de terreno en uno de trazo, y el trazo que importa es el de las
        curvas que describen el predio (`ancho_banda` divide área y largo por
        él, así que manda sobre el blanco cartográfico). Son dos llamadas a `separacion_media` con dominios distintos: sacar los dos
        números de una sola pasada es la trampa fácil, y uno de ellos saldría del
        dominio equivocado sin que nada avise.
    Las CONVENCIONES de oficio (`LADO_MM` = 2 mm, `N_MIN` = 4, `N_CRUCE` = 3,
        `CUANTIL_VACIO` = 90, en `metrologia`) no son parámetros: son la misma
        cifra en todos los predios. Salen en la tabla marcadas `convencion`,
        pero NO en `sin_medir`: no hay medición que las sustituya.
    medir_ancho : apaga la medición de `w`. Apagarla deja el
        techo de trazado en el ancho GEOMETRICO y eso se marca `default`.
    metodo : el interpolador para el que se derivan los parametros, `"spline"`
        (default) o `"tin"`. NO es cosmetico, cambia tres cosas:

        - **el piso antialias no aplica al TIN** : sale de que el
          spline ondula con lambda, y un TIN es lineal a trozos. Con `"tin"`,
          `resolucion_regla` corre sin `lambda_m` ni `amplitud_rizo`, la
          `resolucion` sale solo de los techos y `resolucion_piso` es NaN.
        - **no hay `vecinos`**: es parametro de la RBF, la clave no sale y no
          cae en `sin_medir`. Pasarlo con `"tin"` levanta ValueError.
        - **el margen de borde sale de `s_vacio`**, no del vecindario:
          `ceil(s_vacio/h)+2`. Un triangulo en el borde de la malla solo
          necesita la curva siguiente de FUERA, y la mayor distancia a ella
          es la del vacio.
    `geo.MOSTRAR` : imprime la tabla de procedencia al terminar.

    Devuelve un dict. Todos los valores son `Valor`/`ValorEntero` salvo
    `metodo` (str), `factible` (bool) y `techos` (dict crudo de
    `resolucion_regla`). Se pasa entero como `parametros=` a `interpolar_mde`,
    `validar_mde`, `barrer_resolucion` y `cuadros_mde`.
    Claves: `metodo`, `escala`, `umm_m2`, `equidistancia`, `p_max`, `lambda_m`, `zigzag`,
    `ancho_w`, `blanco_carta_ha` (solo si `w` se midió), `resolucion`,
    `resolucion_regla`, `resolucion_piso`, `factible`, `s_vacio`,
    `intervalo_muestreo_terreno`, `intervalo_muestreo`, `vecinos`,
    `margen_borde_celdas`, `min_pixeles`, `pendiente_espuria_pct` (solo con `A`).

    NO decide nada. Ni se niega a correr por un `default`, ni corrige `h` cuando
    cae bajo el piso. Devuelve los números y la lista de `sin_medir`; quien
    llama decide, y para eso tiene que poder ver.
    """
    if escala <= 0:
        raise ValueError(f"escala {escala} debe ser > 0")
    if metodo not in ("spline", "tps", "tin"):
        raise ValueError(f"metodo '{metodo}': usa 'spline'/'tps' o 'tin'")
    es_tin = metodo == "tin"
    # "medir" o una rejilla de `n`: se barre aqui. Un int (o `ValorEntero`) no.
    medir_vecinos = isinstance(vecinos, str) or (
        vecinos is not None and not isinstance(vecinos, (int, float, np.number))
    )
    if isinstance(vecinos, str) and vecinos != "medir":
        raise ValueError(f"vecinos '{vecinos}': usa un entero, 'medir' o una lista de n")
    enes = tuple(vecinos) if medir_vecinos and not isinstance(vecinos, str) else None
    if medir_vecinos:
        vecinos = None
    if es_tin and (vecinos is not None or medir_vecinos):
        raise ValueError(
            "`vecinos` es de la RBF: con metodo='tin' no entra en ningun sitio, y "
            "pasarlo haria creer que se eligio algo"
        )
    # Antes de medir nada: en grados todo esto sale con la unidad equivocada y
    # sin reventar. None se salta (el camino de aritmetica pura no mide).
    _exigir_proyectado(curvas, "curvas")
    _exigir_proyectado(zona, "zona")

    def _exigir_capas(que: str) -> None:
        if curvas is None or zona is None:
            raise ValueError(
                f"para medir {que} hacen falta `curvas` y `zona`; o pásalo a mano"
            )

    # el interpolador para el que valen estas cifras: `interpolar_mde(parametros=p)`
    # lo lee de aqui, y un TIN corrido con cifras de spline no avisa solo
    p: dict[str, Any] = {"metodo": "spline" if metodo == "tps" else metodo}

    # 1. Lo declarado, y lo que es consecuencia aritmética inmediata suya.
    p["escala"] = _v(float(escala), "declarado", "compromiso de entrega")
    umm = (LADO_MM * 1e-3 * escala) ** 2
    p["umm_m2"] = _v(umm, "derivado", f"({LADO_MM:g} mm * escala)^2")
    p_max = _p_max(rangos)
    p["p_max"] = _v(p_max, "derivado", "limite finito mas alto de `rangos`")
    # Las convenciones, en la tabla y no escondidas en la firma. Son las que
    # antes vivian como default invisible de esta funcion: entran al cuadrado en
    # la UMM (`lado_mm`) o mandan directo sobre `h` (`n_min`, `n_cruce`), asi que
    # no verlas es el mismo defecto que no ver `vecinos`. Fuera de `sin_medir`
    # porque ninguna medicion las sustituye.
    p["lado_mm"] = _v(LADO_MM, "convencion", "lado minimo legible en carta")
    p["n_min"] = _v(N_MIN, "convencion", "pixeles que caben dentro de la UMM")
    p["n_cruce"] = _v(N_CRUCE, "convencion", "celdas CRUZANDO el ancho, para trazar")
    p["cuantil_vacio"] = _v(
        CUANTIL_VACIO, "convencion", "percentil de distancias que define el VACIO"
    )

    if equidistancia is None:
        _exigir_capas("la equidistancia")
        if campo not in curvas.columns:
            raise ValueError(f"'{campo}' no está en curvas")
        e_det = _detectar_equidistancia(curvas[campo])
        if e_det is None:
            raise ValueError(
                "no se pudo detectar la equidistancia (menos de dos cotas "
                "distintas); pásala como `equidistancia`"
            )
        p["equidistancia"] = _v(e_det, "medido", "paso de la escalera de cotas")
    else:
        p["equidistancia"] = _de_fuera(equidistancia, "dada a mano")
    e = float(p["equidistancia"])

    # 2. La cartografía: lambda y zigzag, del mismo `separacion_media` para que
    # no salgan de dominios distintos.
    #
    # SOBRE EL EXTENT, NO SOBRE EL PREDIO: es donde `interpolar_mde` construye la
    # superficie y ondula, y el piso antialias es lambda/2.
    dominio = cuadro
    if dominio is None and zona is not None:
        # bbox de `zona`, que es el `cuadro_salida` de `cuadros_mde`. No depende
        # del margen de borde (eso solo mueve `cuadro_recorte`), asi que no hay
        # circularidad con `s_vacio`.
        dominio = crear_cuadro(zona)
    # DOS llamadas, con dominios DISTINTOS, y no es un descuido:
    #
    #   lambda  -> EXTENT. Es la longitud de onda del rizo del spline, y el
    #              spline construye la superficie sobre el extent. De ahi sale
    #              el piso antialias (lambda/2).
    #   zigzag  -> ZONA. Convierte un muestreo de TERRENO en uno de TRAZO, y el
    #              trazo que importa es el de las curvas que describen el
    #              predio. Ademas divide area y largo en `ancho_banda`, asi que
    #              manda sobre el blanco cartografico.
    #
    # `separacion_media` devuelve los dos de una sola pasada, asi que tomarlos de
    # la misma llamada es la trampa facil: uno de los dos sale del dominio
    # equivocado y nada avisa. Cuesta una segunda pasada.
    # SIN `resolucion=`: con `paso = h/2` la lambda medida dependeria de la `h` que luego
    # juzga. El `paso` es el default de `separacion_media`.
    if lambda_m is None:
        _exigir_capas("lambda")
        lam = separacion_media(curvas, dominio)[0]
    else:
        lam = float(lambda_m)
    if zigzag is None:
        _exigir_capas("el zigzag")
        zz = separacion_media(curvas, zona)[2]
    else:
        zz = float(zigzag)
    if not math.isfinite(zz) or zz <= 0:
        raise ValueError(
            f"factor_zigzag {zz}: sin curvas dentro de `zona`, o `zona` y "
            "`curvas` no se pisan. Revisa el CRS."
        )
    p["lambda_m"] = (
        _v(lam, "medido", "G'(0) de `separacion_media` sobre el EXTENT")
        if lambda_m is None
        else _de_fuera(lambda_m, "dada a mano")
    )
    p["zigzag"] = (
        _v(zz, "medido", "largo real / largo efectivo sobre la ZONA")
        if zigzag is None
        else _de_fuera(zigzag, "dado a mano")
    )

    # 3. `w`. Sin ella el techo de trazado cae al ancho geométrico: no es cosmético.
    if ancho_medido is not None:
        w = float(ancho_medido)
        p["ancho_w"] = _de_fuera(ancho_medido, "dado a mano (medido aparte)")
    elif medir_ancho:
        _exigir_capas("el ancho `w` de la clase alta")
        try:
            _, res_w = _callado(ancho_banda)(
                curvas, campo, zona, e, zz, pendiente_pct=p_max * 100
            )
        except ValueError as exc:
            # Una cartografía sin banda limpia es un RESULTADO, no un fallo. Pero
            # seguir con el ancho geométrico sin decirlo es justo el defecto que
            # este módulo existe para cerrar: se marca `default` y sale en
            # `sin_medir`, con el motivo pegado.
            w = None
            p["ancho_w"] = _v(
                e / p_max,
                "default",
                f"GEOMETRICO e/p_max: no hay banda medible ({exc})",
            )
        else:
            w = float(res_w["ancho_m"])
            p["ancho_w"] = _v(w, "medido", "escalera de cortes de `ancho_banda`")
            p["blanco_carta_ha"] = _v(
                float(res_w["area_ha"]),
                "medido",
                "área de la banda en carta (juez externo)",
            )
    else:
        w = None
        p["ancho_w"] = _v(e / p_max, "default", "GEOMETRICO e/p_max: `medir_ancho=False`")

    # 4. El sándwich. `w` None hace que `resolucion_regla` use el geométrico,
    # que es exactamente el valor que acabamos de marcar `default`.
    h_regla, factible, techos = resolucion_regla(
        e,
        umm,
        rangos,
        ancho_medido=w,
        # el piso es del spline. Sin `lambda_m` no hay piso.
        lambda_m=None if es_tin else lam,
        amplitud_rizo=None if es_tin else amplitud_rizo,
    )
    h_uso = h_regla if resolucion is None else float(resolucion)
    nombre_techo = min((k for k in techos if k != "antialias"), key=lambda k: techos[k])
    # Cuando es infactible `resolucion_regla` devuelve el PISO, no el techo. Decir
    # "manda el techo" ahi seria justo la clase de origen que este modulo existe
    # para que no se escriba: falso y sin forma de notarlo.
    origen_h = (
        f"manda el techo '{nombre_techo}' = {techos[nombre_techo]:.2f}"
        if factible
        else f"INFACTIBLE: manda el piso antialias sobre el techo "
        f"'{nombre_techo}' = {techos[nombre_techo]:.2f}"
    )
    p["resolucion_regla"] = _v(h_regla, "derivado", origen_h)
    p["resolucion"] = (
        _v(h_uso, "derivado", "la de la regla")
        if resolucion is None
        else _de_fuera(resolucion, "dada a mano; la regla queda de referencia")
    )
    p["resolucion_piso"] = _v(
        techos.get("antialias", float("nan")),
        "derivado",
        "no aplica: un TIN no ondula"
        if es_tin
        else "lambda/2, piso antialias",
    )
    p["factible"] = factible
    p["techos"] = techos

    # 5. `s_vacio`, ya con `h`: las hectáreas de esta malla comparan con las del
    # MDE solo si las dos se leen a la misma resolución.
    if s_vacio is not None:
        p["s_vacio"] = _de_fuera(s_vacio, "dado a mano")
    else:
        _exigir_capas("`s_vacio`")
        # el EXTENT, no el predio, por lo mismo que lambda: `s_vacio` dimensiona
        # el margen de borde, o sea cuantas curvas de FUERA sostienen el borde de
        # la malla, y esa malla se construye sobre el extent.
        distancia, mascara, _ = _callado(raster_distancia)(curvas, dominio, h_uso)
        # el percentil directo, no `separaciones`: esa funcion tabula la
        # distribucion entera y solo expone q50/q80/q90 fijos, asi que
        # `CUANTIL_VACIO` no se podria honrar. Es el MISMO numero (lee
        # `distancia[mascara]` igual) por una llamada menos.
        d = distancia[mascara]
        if d.size == 0:
            raise ValueError(
                "la máscara de `zona` no deja ningún píxel: `zona` y `curvas` no "
                "se pisan, o el CRS no coincide. Pasa `s_vacio` a mano."
            )
        s_medido = 2 * float(np.percentile(d, CUANTIL_VACIO))
        if s_medido <= 0:
            # a esta `h` cada celda toca una curva: la malla no ve el vacio
            raise ValueError(
                f"`s_vacio` mide 0 a h={h_uso:g} m: la malla es mas gruesa que la "
                f"separacion entre curvas (lambda {lam:.1f} m) y cada celda toca una. "
                "Pasa `resolucion` menor o `s_vacio` a mano."
            )
        p["s_vacio"] = _v(
            s_medido,
            "medido",
            f"2*q{CUANTIL_VACIO:g} de las distancias sobre el EXTENT",
        )
    s = float(p["s_vacio"])

    # 6. Aritmética sobre lo anterior. Nada de esto debería escribirse nunca.
    i_terreno = e / p_max
    p["intervalo_muestreo_terreno"] = _v(i_terreno, "derivado", "equidistancia / p_max")
    p["intervalo_muestreo"] = _v(
        i_terreno * zz, "derivado", "i_terreno * zigzag (la API muestrea el TRAZO)"
    )
    if es_tin:
        pass  # sin vecinos: no es parametro del TIN (ver `metodo`)
    elif vecinos is not None:
        # `_de_fuera` respeta la procedencia que traiga y, si no trae, dice
        # `declarado`, que es lo unico que consta.
        p["vecinos"] = _de_fuera(
            vecinos, "lo paso quien llama; su razon vive ahi, no aqui", entero=True
        )
    elif medir_vecinos:
        # No se DERIVA (la formula pi*k^2*s/(4*i) solo dimensiona el orden de
        # magnitud), pero sí se MIDE, y medirla es mecánico. Va aquí y no antes
        # porque necesita `h` y `s_vacio`, y va antes del margen porque el margen
        # depende de ella.
        _exigir_capas("la rodilla de `vecinos`")
        _, rodilla = barrer_vecinos(
            curvas,
            campo,
            zona,
            resolucion=h_uso,
            intervalo_terreno=i_terreno,
            s_vacio=s,
            zigzag=zz,
            equidistancia=e,
            p_max=p_max,
            **({} if enes is None else {"enes": enes}),
        )
        # Una rodilla solo es rodilla si la cifra convergio. Si el barrido avisa,
        # el numero es el borde de la rejilla y no una medicion: entra como
        # `default` para que caiga en `sin_medir`, que es donde se mira antes de
        # citar una cifra.
        if rodilla.avisos:
            p["vecinos"] = _v(
                int(rodilla),
                "default",
                "barrido SIN rodilla: " + " ".join(rodilla.avisos),
            )
        else:
            p["vecinos"] = _v(int(rodilla), "medido", "rodilla de `barrer_vecinos`")
    else:
        p["vecinos"] = _v(
            VECINOS_DEFAULT,
            "default",
            "NADIE lo eligió. Pásalo, o `vecinos='medir'` para barrerlo",
        )
    if es_tin:
        p["margen_borde_celdas"] = _v(
            math.ceil(s / h_uso) + 2,
            "derivado",
            "TIN: ceil(s_vacio/h)+2, la curva siguiente de FUERA",
        )
    else:
        p["margen_borde_celdas"] = _v(
            margen_borde_celdas(int(p["vecinos"]), i_terreno, s, h_uso),
            "derivado",
            "ceil(sqrt(n*i*s/pi)/h)+2",
        )
    p["min_pixeles"] = _v(min_pixeles_umm(umm, h_uso), "derivado", "ceil(umm_m2/h^2)")
    if amplitud_rizo is not None and math.isfinite(lam) and not es_tin:
        # La espuria HEREDA la fuente de `A`: es una funcion de `A`, y no puede
        # ser mas firme que su operando.
        a_val = _de_fuera(amplitud_rizo, "A dada a mano")
        p["pendiente_espuria_pct"] = _v(
            _pendiente_espuria(float(amplitud_rizo), h_uso, lam),
            a_val.fuente,
            f"A*sin(2*pi*h/lambda)/h con A={float(a_val):g} m ({a_val.fuente})",
        )

    # Con QUE se midio, no solo con que fuente. Ver `_huella`.
    p["insumos"] = {
        "curvas": _huella(
            curvas,
            "-"
            if curvas is None
            else f"{longitud_total_m(curvas) / 1000:.1f} km de trazo",
        ),
        "zona": _huella(
            zona, "-" if zona is None else f"{superficie_total_ha(zona):.2f} ha"
        ),
    }

    if _mostrar():
        tabla_procedencia(p)
    return p


def tabla_procedencia(params: dict[str, Any]) -> str:
    """Rinde la tabla parámetro / valor / fuente / origen de `derivar_parametros`.

    Es la tabla que hoy se escribe a mano en la memoria técnica de cada predio.
    Que se genere sola es el punto: un valor `default` aparece en el ENTREGABLE,
    no en un aviso de consola que ya se aprendió a ignorar.

    Devuelve el texto además de imprimirlo, para pegarlo en la memoria.
    """
    filas = [(k, v) for k, v in params.items() if isinstance(v, (Valor, ValorEntero))]
    ancho = max((len(k) for k, _ in filas), default=10)
    lineas = [f"{'parametro':>{ancho}} {'valor':>12}  {'fuente':<10} origen"]
    for nombre, v in filas:
        num = f"{int(v):12d}" if isinstance(v, ValorEntero) else f"{float(v):12.3f}"
        lineas.append(f"{nombre:>{ancho}} {num}  {v.fuente:<10} {v.origen}")

    insumos = params.get("insumos")
    if insumos:
        lineas.append("")
        lineas.append("   INSUMOS (con que se midio, no solo con que fuente):")
        for capa, h in insumos.items():
            lineas.append(f"      {capa:>7}: {h['ruta']}")
            if h["n"]:
                lineas.append(
                    f"               {h['n']} entidades | {h['crs']} | "
                    f"{h['medida']} | bbox {h['bbox']}"
                )

    faltan = sin_medir(params)
    if faltan:
        lineas.append("")
        lineas.append(
            f"   SIN MEDIR ({len(faltan)}): {', '.join(faltan)}. Nadie eligió estos "
            "valores."
        )
        lineas.append(
            "   No invalida la corrida, pero una cifra que dependa de ellos no es "
            "firme: es vieja."
        )
    if params.get("factible") is False:
        lineas.append("")
        lineas.append(
            "   INFACTIBLE: el piso antialias supera al techo. Ningún `h` cumple las dos."
        )
        lineas.append(
            "   La cartografía no sostiene la clase alta a esa UMM. Decláralo en "
            "vez de taparlo."
        )
    esp = params.get("pendiente_espuria_pct")
    if esp is not None:
        lineas.append("")
        lineas.append(
            f"   Pendiente espuria con `A` medida: {float(esp):.1f} %. Compárala "
            "contra el límite"
        )
        lineas.append(
            "   inferior de tu clase alta. Si no lo alcanza, el piso antialias no "
            "es el juez aquí."
        )
    texto = "\n".join(lineas)
    if _mostrar():
        print(texto)
    return texto


def comprobar_cifras_firmes(
    medidos: dict[str, float],
    firmes: dict[str, float],
    tol: float = 0.05,
    nombre: str = "cifras firmes",
) -> None:
    """Compara lo medido contra las cifras firmes del predio. Revienta si no cae.

    Es el arnés de un script de flujo por predio, generalizado. La idea: la primera corrida
    buena de un predio FIJA sus cifras, se escriben, y a partir de ahí cualquier
    cambio de librería, de script o de parámetro que las mueva sale como
    excepción en vez de como una tabla nueva que parece igual de válida.

    Va antes de la tabla, no después: si el arnés no cae, nada de abajo es
    citable, así que imprimirlo primero es ofrecer una cifra que se acaba de
    invalidar.

    Parámetros
    ----------
    medidos : lo que dio esta corrida.
    firmes : lo que tiene que dar. Cada clave tiene que estar en `medidos`.
    tol : tolerancia RELATIVA (default 0.05 = 5 %). Con un esperado de 0 se
        compara en absoluto contra `tol`.

    No devuelve nada: o pasa, o levanta ValueError con las que fallaron. Un
    booleano invitaría a ignorarlo con un `if`, que es justo lo que no se quiere.
    """
    if not firmes:
        raise ValueError("`firmes` vacío: un arnés sin cifras no comprueba nada")
    if tol < 0:
        raise ValueError(f"tol {tol} debe ser >= 0")
    faltan = [k for k in firmes if k not in medidos]
    if faltan:
        raise ValueError(f"`medidos` no trae {faltan}; no se puede comprobar")

    fallas: list[str] = []
    if _mostrar():
        print(f"\narnés contra {nombre}:")
    for clave, esperado in firmes.items():
        visto = float(medidos[clave])
        margen = tol * abs(esperado) if esperado else tol
        ok = abs(visto - esperado) <= margen
        if _mostrar():
            print(
                f"   {clave:>22} {visto:10.2f} contra {esperado:10.2f}  "
                f"{'ok' if ok else 'FALLA'}"
            )
        if not ok:
            fallas.append(clave)
    if fallas:
        raise ValueError(
            f"el arnés NO reproduce {fallas} (tolerancia {tol:.0%}).\n"
            "Esta corrida no está donde estaba la que fijó las cifras. NO cites la "
            "tabla:\n"
            "compara los parámetros con `tabla_procedencia` antes de tocar nada."
        )
