"""El interruptor global de reportes: `geo.MOSTRAR = False` calla todos.

Los AVISOS de defecto no pasan por aquí: salen siempre.
"""

import functools
import sys
from collections.abc import Callable
from typing import Any


def _mostrar() -> bool:
    """El valor actual de `geophis.MOSTRAR`."""
    return getattr(sys.modules.get("geophis"), "MOSTRAR", True)


def _callado(fn: Callable[..., Any]) -> Callable[..., Any]:
    """`fn` con los reportes apagados, para las llamadas internas de la librería."""

    @functools.wraps(fn)
    def llamada(*args: Any, **kwargs: Any) -> Any:
        paquete = sys.modules["geophis"]
        antes, paquete.MOSTRAR = paquete.MOSTRAR, False
        try:
            return fn(*args, **kwargs)
        finally:
            paquete.MOSTRAR = antes

    return llamada
