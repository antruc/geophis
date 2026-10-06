"""`docs/GEOPHIS.md` documenta la API a mano; esto vigila que no se desfase del código.

Compara solo nombres de parámetro y defaults. Las anotaciones de tipo no se
documentan a propósito (los .md se leen, no se compilan), así que compararlas
marcaría todo como distinto.

`docs/API.md` se comprueba distinto y más flojo, y es a propósito: es un índice
que apunta a `GEOPHIS.md` y NO repite las firmas: una segunda copia de las
firmas se desfasaría. De él solo se exige cobertura.

# ponytail: un solo test por archivo en vez de parametrizar por función. El
# mensaje de fallo ya dice qué firma y dónde; parametrizar solo multiplica ids.
"""

import ast
import inspect
import re
from pathlib import Path

import pytest

import geophis as geo

RAIZ = Path(__file__).resolve().parent.parent


def test_cadenas_imprimibles_en_consola_windows():
    """Ninguna cadena que se IMPRIMA puede llevar caracteres fuera de cp1252.

    Una consola Windows por defecto codifica en cp1252. Un `print` con `λ`
    lanza UnicodeEncodeError y MATA el proceso: un aviso que revienta un
    barrido de minutos es peor que no avisar.

    Los docstrings quedan exentos: se leen como fuente (UTF-8) y no se
    codifican a stdout. Los acentos y la ñ sí caben en cp1252; el griego no.
    """
    # los ejemplos tambien: se corren a mano y a veces en Windows
    fuentes = sorted((RAIZ / "src").rglob("*.py")) + sorted((RAIZ / "ejemplos").glob("*.py"))
    problemas = []
    for ruta in fuentes:
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        docs = set()
        for nodo in ast.walk(arbol):
            if isinstance(nodo, (ast.Module, ast.FunctionDef, ast.ClassDef)):
                if ast.get_docstring(nodo, clean=False):
                    docs.add(id(nodo.body[0].value))
        for nodo in ast.walk(arbol):
            if not (isinstance(nodo, ast.Constant) and isinstance(nodo.value, str)):
                continue
            if id(nodo) in docs:
                continue
            try:
                nodo.value.encode("cp1252")
            except UnicodeEncodeError as e:
                malo = nodo.value[e.start : e.end]
                problemas.append(
                    f"  {ruta.name}:{nodo.lineno}: {malo!r} en "
                    f"{nodo.value.strip()[:60]!r}"
                )
    assert not problemas, (
        "cadenas que revientan en una consola cp1252 (usa el nombre en ASCII, "
        "p. ej. 'lambda'):\n" + "\n".join(problemas)
    )


# La documentación completa, y el único archivo que se carga en un Proyecto de
# Claude. El README de la raíz es presentación y ya no lleva firmas.
DOCS = {"geophis.md": [RAIZ / "docs" / "GEOPHIS.md"]}

# El índice: una fila por función, con enlace a la entrada de GEOPHIS.md.
INDICE = RAIZ / "docs" / "API.md"

# "- `nombre(args)`:" al inicio de línea
PATRON = re.compile(r"^- `(\w+)\((.*?)\)`", re.MULTILINE)


def _parte_args(s: str) -> list[str]:
    """Parte por comas de nivel 0, ignorando las de dentro de (), [] o comillas."""
    partes, prof, actual, comilla = [], 0, "", None
    for c in s:
        if comilla:
            comilla = None if c == comilla else comilla
        elif c in "\"'":
            comilla = c
        elif c in "([{":
            prof += 1
        elif c in ")]}":
            prof -= 1
        elif c == "," and prof == 0:
            partes.append(actual.strip())
            actual = ""
            continue
        actual += c
    if actual.strip():
        partes.append(actual.strip())
    return partes


NUMERO = re.compile(r"-?[\d.]+")


def _normaliza(arg: str) -> str:
    """'campo: str = "x"' -> 'campo=x'. Quita anotación, comillas y separadores.

    El .md y `repr` escriben el mismo default de formas distintas: comilla doble
    contra simple, `20_000_000` contra `20000000`, `uint8` contra
    `<class 'numpy.uint8'>`. Todas se llevan a una sola forma.
    """
    nombre, _, default = arg.partition("=")
    nombre = nombre.split(":")[0].strip()
    default = default.replace('"', "").replace("'", "").strip().strip("<>")
    default = default.replace("class", "").replace(" ", "").replace("_", "")
    default = default.split(":")[0]  # <Resampling.bilinear: 1>
    # quita el prefijo de módulo (numpy.uint8 -> uint8) sin tocar los números,
    # o 1.0 quedaría en 0 y no distinguiría 1.0 de 2.0
    if not NUMERO.fullmatch(default):
        default = default.rsplit(".", 1)[-1]
    return f"{nombre}={default}" if default else nombre


def _firma_real(fn) -> list[str]:
    return [_normaliza(str(p)) for p in inspect.signature(fn).parameters.values()]


def _ruta(nombre: str) -> Path:
    for c in DOCS[nombre]:
        if c.exists():
            return c
    pytest.skip(f"{nombre} no encontrado en {[str(c) for c in DOCS[nombre]]}")


def test_la_cadena_de_pendientes_documentada_corre_entera():
    """El ejemplo de `docs/GEOPHIS.md` §"cuando la CLASE MAS ALTA es la cifra".

    Los tests de firma comprueban que los NOMBRES cuadran; este comprueba que la
    cadena entera se puede ejecutar: que la salida de cada paso entra en el
    siguiente y que las guardas cierran. Un ejemplo que no corre es peor que no
    tener ejemplo, porque de ahi se escriben los scripts.

    Escribiendolo salieron dos defectos reales: un `p_max` en POR CIENTO donde
    iba la fraccion (dejaba el muestreo 100 veces fino), y que el spline
    respondia a eso con un `LinAlgError` crudo de scipy que no mencionaba
    curvas. No comprueba cifras de campo: comprueba que la cadena existe.
    """
    import geopandas as gpd
    import numpy as np
    from shapely.geometry import LineString, box

    UTM, E, P_MAX, UMM_M2, H_TANTEO = 32613, 20.0, 100.0, 2500.0, 10.0
    CLASE_ALTA = ">100"
    RANGOS = [("0-15", 0, 15), ("15-60", 15, 60), (">100", 100, float("inf"))]

    # ladera uniforme: curvas E-W cada 12 m, 20 m de cota -> 167 % de pendiente
    curvas = gpd.GeoDataFrame(
        {"COTA": [i * E for i in range(9)]},
        geometry=[LineString([(0, i * 12), (400, i * 12)]) for i in range(9)],
        crs=UTM,
    )
    predio = gpd.GeoDataFrame(geometry=[box(20, 20, 380, 80)], crs=UTM)

    # 1. la cartografia, antes que el MDE
    campo, e = geo.detectar_cota(curvas)
    lambda_m, _, zigzag = geo.separacion_media(curvas, predio)
    dist, masc, _ = geo.raster_distancia(curvas, predio, resolucion=H_TANTEO)
    _, resumen = geo.separaciones(dist, masc, H_TANTEO, lambda_m, zigzag)
    s_vacio = 2 * resumen["q90"]

    # 2. las dos mediciones que nada mas puede derivar
    _, banda = geo.ancho_banda(curvas, campo, predio, e, zigzag)
    w = banda["ancho_m"]
    assert w < banda["ancho_geometrico"]  # medir sirve de algo
    i_terreno = e / (P_MAX / 100)  # la fraccion, NO el porcentaje
    _, vecinos = geo.barrer_vecinos(
        curvas,
        campo,
        predio,
        resolucion=H_TANTEO,
        intervalo_terreno=i_terreno,
        s_vacio=s_vacio,
        zigzag=zigzag,
        enes=(16, 32),
    )

    # 3. la resolucion, de la regla (nunca min(techos.values()))
    h, _, _ = geo.resolucion_regla(e, UMM_M2, RANGOS, ancho_medido=w, lambda_m=lambda_m)
    margen = geo.margen_borde_celdas(vecinos, i_terreno, s_vacio, h)

    # 4. una interpolacion
    cuadro_recorte, cuadro_salida = geo.cuadros_mde(
        predio,
        resolucion=h,
        margen_borde_celdas=margen,
        margen_salida=2 * h,  # Horn deja NaN la celda del borde
    )
    mde, tf = geo.interpolar_mde(
        geo.recortar(curvas, cuadro_recorte),
        campo,
        resolucion=h,
        equidistancia=e,
        intervalo_muestreo=i_terreno * zigzag,  # la API muestrea el TRAZO
        vecinos=vecinos,
        cuadro=cuadro_salida,
        lambda_m=lambda_m,
        amplitud_rizo=1.4,
    )
    pendiente = geo.calcular_pendiente(mde, tf)  # el transform, no una constante
    # la rampa es 20 m de cota sobre 12 m: 167 %. Si esto se cae, la cadena
    # corre pero describe otro terreno.
    assert float(np.nanmedian(pendiente)) == pytest.approx(167, abs=15)

    # 5. UNA pasada de sieve, con la clase alta exenta. La receta de dos pasadas
    # + resolver_por_prioridad no era una particion: el sieve METE en la clase
    # alta pixeles que en el crudo eran de otra clase, y esos se perdian
    # (43.21 ha medidas en un predio real). Ver docs/GEOPHIS.md.
    entregable = geo.zonificar(
        pendiente, tf, predio.crs, RANGOS, geo.min_pixeles_umm(UMM_M2, h), recorte=predio,
        exentos=[CLASE_ALTA], campo="PENDIENTES", por_clase=True,
    )

    # la guarda del doc, sobre la SALIDA, y con la cifra en el mensaje. Es la
    # que caza el hueco: una sola pasada no puede solapar, pero si puede no
    # cubrir si algun paso pierde pixeles.
    # Una tolerancia de 1.0 ha taparia un anillo de 0.80 ha (el 37 % de esta
    # ladera): sin `margen_salida` Horn deja NaN la celda del borde del extent. Con el margen, lo que queda es el redondeo del area.
    falta = geo.superficie_total_ha(predio) - geo.superficie_total_ha(entregable)
    assert abs(falta) <= 0.01, f"la capa no cubre el predio: faltan {falta:.2f} ha"

    # 6. los tres jueces, externos al MDE
    geo.sostenimiento_por_clase(entregable, curvas, "PENDIENTES", RANGOS, e)
    geo.barrer_resolucion(
        curvas,
        campo,
        predio,
        RANGOS,
        UMM_M2,
        resoluciones=[h * 2],
        cuadro=cuadro_salida,
        vecinos=vecinos,
    )
    d2, m2, _ = geo.raster_distancia(curvas, predio, resolucion=h)
    geo.auditar_rangos(entregable, curvas, "PENDIENTES", RANGOS, e, d2, m2, h)


@pytest.mark.parametrize("doc", list(DOCS))
def test_documenta_toda_la_api(doc):
    """Ninguna función de __all__ se queda sin documentar."""
    texto = _ruta(doc).read_text(encoding="utf-8")
    documentadas = {n for n, _ in PATRON.findall(texto)}
    faltan = sorted(set(geo.__all__) - documentadas)
    assert not faltan, f"{doc}: sin documentar -> {', '.join(faltan)}"


def test_el_indice_cubre_toda_la_api():
    """`docs/API.md` tiene una fila por función de `__all__`.

    El índice no lleva firmas a propósito, así que aquí no hay nada que
    comparar contra `inspect.signature`. Lo que sí tiene que cumplir es
    cobertura, o deja de servir para lo único que hace: una función nueva sin
    fila queda invisible para quien busca por nombre, y eso no lo caza ningún
    otro test.
    """
    if not INDICE.exists():
        pytest.skip(f"{INDICE} no encontrado")
    texto = INDICE.read_text(encoding="utf-8")
    enlazadas = set(re.findall(r"^\| \[`(\w+)`\]", texto, re.MULTILINE))
    faltan = sorted(set(geo.__all__) - enlazadas)
    assert not faltan, f"API.md: sin fila en el índice -> {', '.join(faltan)}"


@pytest.mark.parametrize("doc", list(DOCS))
def test_firmas_documentadas_cuadran(doc):
    """La firma escrita en el .md coincide con la real (nombres y defaults)."""
    texto = _ruta(doc).read_text(encoding="utf-8")
    errores = []
    for nombre, args in PATRON.findall(texto):
        if nombre not in geo.__all__:
            continue  # ejemplos de código, no API
        escrita = [_normaliza(a) for a in _parte_args(args)]
        real = _firma_real(getattr(geo, nombre))
        if escrita != real:
            errores.append(f"  {nombre}\n    md:   {escrita}\n    real: {real}")
    assert not errores, f"{doc}: firmas desfasadas\n" + "\n".join(errores)


def test_la_receta_de_elegir_metodo_corre_entera():
    """`docs/GEOPHIS.md` §"Elegir el metodo de interpolacion", en una ladera.

    Misma ladera que la cadena de pendientes (curvas cada 12 m, 167 %) con un
    cauce que las cruza todas. Comprueba que la receta existe y encadena:
    `derivar_parametros(metodo="tin")` deja `sin_medir` vacio sin barrer nada,
    los quiebres entran, las dos candidatas se clasifican igual y el juez
    devuelve una recomendada. No comprueba cifras de campo.
    """
    import geopandas as gpd
    from shapely.geometry import LineString, box

    UTM, E, ESCALA, CLASE_ALTA = 32613, 20.0, 25_000, ">100"
    RANGOS = [("0-15", 0, 15), ("15-100", 15, 100), (">100", 100, float("inf"))]
    curvas = gpd.GeoDataFrame(
        {"COTA": [i * E for i in range(12)]},
        geometry=[LineString([(0, i * 12), (400, i * 12)]) for i in range(12)],
        crs=UTM,
    )
    cauces = gpd.GeoDataFrame(geometry=[LineString([(200, -5), (200, 140)])], crs=UTM)
    predio = gpd.GeoDataFrame(geometry=[box(40, 30, 360, 100)], crs=UTM)

    # 1. parametros, uno por metodo
    campo, e = geo.detectar_cota(curvas)
    p_tin = geo.derivar_parametros(curvas, campo, predio, RANGOS, ESCALA, metodo="tin")
    assert not geo.sin_medir(p_tin), geo.sin_medir(p_tin)
    p_spl = geo.derivar_parametros(curvas, campo, predio, RANGOS, ESCALA, vecinos=20)

    def clases(mde, tf, p):
        codigos, etiquetas = geo.reclasificar_rangos(
            geo.calcular_pendiente(mde, tf), RANGOS
        )
        mascara = (geo.rasterizar(predio, codigos.shape, tf, todo_tocado=True) == 1) & (
            codigos > 0
        )
        cod_alta = next(c for c, et in etiquetas.items() if et == CLASE_ALTA)
        codigos = geo.limpiar_moteado(
            codigos, p["min_pixeles"], mascara=mascara, exentos=[cod_alta]
        )
        gdf = geo.poligonizar(codigos, tf, predio.crs, mascara=mascara, campo="gridcode")
        gdf = geo.disolver_por_grupo(geo.recortar(gdf, predio), campo="gridcode")
        gdf["PENDIENTES"] = gdf["gridcode"].map(etiquetas)
        return geo.calcular_superficie(gdf, campo="SUP")

    # 2. TIN con quiebres
    # `parametros=p` en vez de copiar clave por clave; el `metodo` sale de `p`
    # (si no, `quiebres` con spline levantaria ValueError)
    cuadro_recorte, cuadro_salida = geo.cuadros_mde(
        predio,
        margen_salida=2 * float(p_tin["resolucion"]),  # sin esto Horn deja un anillo NaN
        parametros=p_tin,
    )
    curvas_rec = geo.recortar(curvas, cuadro_recorte)
    mde_tin, tf_tin = geo.interpolar_mde(
        curvas_rec,
        campo,
        cuadro=cuadro_salida,
        quiebres=geo.recortar(cauces, cuadro_recorte),
        parametros=p_tin,
    )
    clases_tin = clases(mde_tin, tf_tin, p_tin)

    # 3. spline
    h_spl = p_spl["resolucion"]
    rec_s, sal_s = geo.cuadros_mde(
        predio,
        resolucion=h_spl,
        margen_borde_celdas=p_spl["margen_borde_celdas"],
        margen_salida=2 * float(h_spl),
    )
    mde_spl, tf_spl = geo.interpolar_mde(
        geo.recortar(curvas, rec_s),
        campo,
        resolucion=h_spl,
        intervalo_muestreo=p_spl["intervalo_muestreo"],
        vecinos=p_spl["vecinos"],
        cuadro=sal_s,
        lambda_m=p_spl["lambda_m"],
    )
    clases_spl = clases(mde_spl, tf_spl, p_spl)
    for c in (clases_tin, clases_spl):
        assert abs(geo.superficie_total_ha(predio) - geo.superficie_total_ha(c)) < 0.05

    # 5. el juez
    filas, recomendada = geo.juzgar_clasificaciones(
        {
            "TIN": (clases_tin, "tin", float(p_tin["resolucion"])),
            "SPLINE": (clases_spl, "spline", float(h_spl)),
        },
        curvas,
        campo,
        predio,
        RANGOS,
        e,
        p_tin["zigzag"],
    )
    assert [f["nombre"] for f in filas] == ["TIN", "SPLINE"]
    assert recomendada in ("TIN", "SPLINE")
