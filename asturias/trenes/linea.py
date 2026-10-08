# -*- coding: utf-8 -*-
"""Compatibilidad: el modelo de una sola línea se sustituyó por el de red (ver red.py), que
trata una línea suelta como una red pequeña y da los mismos cruces."""
from .red import Red as Linea, Viaje  # noqa: F401
