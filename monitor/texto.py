"""Normalizacao de texto usada pelo matcher e pelo extrator."""

from __future__ import annotations

import re
import unicodedata

_NAO_ALFANUM = re.compile(r"[^0-9a-z ]+")
_ESPACOS = re.compile(r"\s+")
# Emojis, setas, dingbats e seletores de variacao comuns em posts de promocao.
_SIMBOLOS = re.compile(
    "["
    "\U0001f000-\U0001faff"
    "←-⇿⌀-➿⬀-⯿"
    "☀-⛿︎️‍⃣"
    "]"
)
_MARCACAO = re.compile(r"[*_`~|>#]+")


def sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def normalizar(texto: str) -> str:
    """Minusculas, sem acento, sem pontuacao, espacos colapsados."""
    texto = sem_acento(texto).lower()
    texto = _NAO_ALFANUM.sub(" ", texto)
    return _ESPACOS.sub(" ", texto).strip()


def compactar(texto: str) -> str:
    """So alfanumericos: faz 'rtx 4070' casar com 'rtx4070'."""
    return normalizar(texto).replace(" ", "")


def limpar_simbolos(texto: str) -> str:
    """Remove emojis e marcacao, preservando acentos e maiusculas."""
    texto = _SIMBOLOS.sub(" ", texto)
    texto = _MARCACAO.sub(" ", texto)
    return _ESPACOS.sub(" ", texto).strip(" -:.,•–—")
