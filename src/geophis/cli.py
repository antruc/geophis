"""CLI de geophis: convierte uno o varios archivos entre formatos SIG.

Un solo comando. ponytail: sin subcomando porque hoy solo convierte. Si algun
dia agregas mas operaciones (buffer, recortar...), migra a subparsers y el uso
pasa a `geophis convertir ARCHIVO TIPO`. El lote es simplemente varios archivos
(arrastrados a la terminal); sin glob ni flags porque el usuario no tecnico los
arrastra, no escribe comodines.
"""

import argparse
import os
import sys

TIPOS = ("shp", "gpkg", "geojson", "gpx", "kml", "kmz")

AYUDA = """\
uso: geophis ARCHIVO [ARCHIVO ...] TIPO

Convierte uno o varios archivos entre formatos SIG.

argumentos:
  ARCHIVO   archivo(s) a convertir. Para varios, arrastralos juntos a la
            terminal (quedan separados por espacios) y luego escribe el TIPO.
  TIPO      formato de salida: shp, gpkg, geojson, gpx, kml, kmz

ejemplos:
  geophis predio.shp kmz              -> crea predio.kmz
  geophis ruta.gpx shp                -> crea ruta.shp
  geophis a.shp b.shp c.shp kmz       -> crea a.kmz, b.kmz, c.kmz

opciones avanzadas (normalmente no hacen falta):
  --capa NOMBRE             capa a leer si el archivo trae varias (GPX/GeoPackage)
  --gpx-como {track,route}  como escribir lineas al exportar a GPX (def: track)
  --si                      sobrescribe salidas existentes sin preguntar
  -h, --ayuda               muestra esta ayuda
"""


class _Parser(argparse.ArgumentParser):
    """Error breve y en español en vez del volcado tecnico de argparse."""

    def error(self, message: str) -> None:
        sys.stderr.write(
            f"Error: {message}\nEscribe 'geophis ayuda' para ver como se usa.\n"
        )
        raise SystemExit(2)


def _build() -> _Parser:
    p = _Parser(add_help=False)
    p.add_argument(
        "archivos", nargs="+"
    )  # uno o varios; argparse deja el ultimo para tipo
    p.add_argument("tipo")
    p.add_argument("--capa")
    p.add_argument(
        "--gpx-como", dest="gpx_como", choices=("track", "route"), default="track"
    )
    p.add_argument("--si", action="store_true")  # sobrescribir sin preguntar
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Sin argumentos o pidiendo ayuda: muestra la ayuda y sale.
    if not argv or argv[0] in ("ayuda", "help", "-h", "--ayuda", "--help"):
        print(AYUDA)
        return 0

    args = _build().parse_args(argv)

    tipo = args.tipo.lower().lstrip(".")
    if tipo not in TIPOS:
        print(f"Error: tipo '{args.tipo}' no valido. Usa uno de: {', '.join(TIPOS)}.")
        return 2

    from . import convertir  # diferido sin efecto: main ya importa geophis/__init__, que carga todo

    ok = fallo = 0
    for archivo in args.archivos:
        if not os.path.exists(archivo):
            print(f"Error: no existe el archivo '{archivo}'.")
            fallo += 1
            continue
        # Salida autonombrada: misma carpeta y nombre, extension nueva.
        salida = os.path.splitext(archivo)[0] + "." + tipo
        if os.path.abspath(salida) == os.path.abspath(archivo):
            print(f"Aviso: '{archivo}' ya es {tipo}, se salta.")
            continue
        if os.path.exists(salida) and not args.si:
            try:
                pregunta = f"'{salida}' ya existe. Sobrescribir? [s/N]: "
                resp = input(pregunta).strip().lower()
            except EOFError:
                # sin terminal (salida redirigida, tuberia, doble clic) no hay a
                # quien preguntar. Se salta, que es lo que responde el default
                # [N]. Sin esto sale el traceback crudo que `_Parser.error`
                # existe para evitar.
                print(f"'{salida}' ya existe y no hay terminal para preguntar.")
                print("Usa --si para sobrescribir sin preguntar. Saltado.")
                continue
            if resp not in ("s", "si", "sí"):
                print("Saltado.")
                continue
        try:
            convertir(archivo, salida, capa=args.capa, gpx_como=args.gpx_como)
            ok += 1
        except Exception as e:
            print(f"Error convirtiendo '{archivo}': {e}")
            fallo += 1

    if len(args.archivos) > 1:
        print(f"\nListo: {ok} convertidos, {fallo} con error.")
    return 1 if fallo else 0


if __name__ == "__main__":
    raise SystemExit(main())
