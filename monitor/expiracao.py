"""Deteccao de promocao/cupom encerrado.

Os canais avisam de tres jeitos, todos cobertos aqui:
  1. mensagem nova nomeando o cupom  ("Cupom BOLSOCHEIO no Meli ESGOTADO");
  2. edicao da mensagem original     (marcador de fim, preco maior, cupom sumiu);
  3. remocao da mensagem original.

Marcador sozinho nao basta: "25% OFF" e "acaba de ganhar" apareciam como falso
positivo. Sempre exigimos marcador + identificador (cupom, resposta ou item).
"""

from __future__ import annotations

import re

from .extrator import Promocao

# "esgotado", "acabou", "encerrada", "expirou", "fora do ar", "voltou ao preco".
# Ancorado em palavra inteira para nao casar com "OFF", "acaba de ganhar" etc.
MARCADORES = re.compile(
    r"\b(?:esgotad\w*|acabou|acabaram|encerrad\w*|expirad\w*|expirou|"
    r"finalizad\w*|indisponi\w*|indispon[ií]vel|"
    r"saiu do ar|fora do ar|off do ar|"
    r"voltou\s+(?:o|ao|para o)\s+pre[cç]o|subiu\s+(?:o\s+)?pre[cç]o|"
    r"n[aã]o\s+(?:funciona|est[aá]|t[aá])\s+mais|"
    r"pegou\s+pegou|era boa|deu ruim)\b",
    re.IGNORECASE,
)

# Emojis que esses canais usam para marcar fim: X vermelho, cacule, pomba.
_EMOJI_FIM = re.compile("[❌☠\U0001f54a]")

# Tokens que parecem codigo de cupom (o filtro real e comparar com os cupons
# ja guardados, entao gerar candidato demais nao faz mal).
_TOKENS = re.compile(r"\b[A-Z0-9][A-Z0-9._-]{3,23}\b")


def parece_fim(texto: str) -> bool:
    """True se a mensagem sinaliza encerramento."""
    if not texto:
        return False
    return bool(MARCADORES.search(texto) or _EMOJI_FIM.search(texto))


def cupons_citados(texto: str) -> set[str]:
    """Codigos citados na mensagem, em maiusculas, para bater com os guardados."""
    if not texto:
        return set()
    limpo = texto.replace("`", " ").replace("*", " ")
    return {t.upper() for t in _TOKENS.findall(limpo) if any(c.isalpha() for c in t)}


def motivo_edicao(
    texto_novo: str,
    preco_antes: float | None,
    cupom_antes: str | None,
    promo_agora: Promocao,
) -> str | None:
    """Compara a promocao alertada com a versao editada da mensagem.

    Devolve o motivo do encerramento, ou None se a oferta continua de pe.
    """
    if parece_fim(texto_novo):
        return "o canal marcou a promocao como encerrada"

    if cupom_antes and not _cupom_presente(cupom_antes, texto_novo):
        return f"o cupom {cupom_antes} sumiu da mensagem"

    if preco_antes is not None and promo_agora.preco is not None:
        # margem de 1% absorve arredondamento entre PIX/boleto
        if promo_agora.preco > preco_antes * 1.01:
            return (
                f"o preco subiu de R$ {preco_antes:.2f} para R$ {promo_agora.preco:.2f}"
            )
    return None


def _cupom_presente(cupom: str, texto: str) -> bool:
    return cupom.upper() in cupons_citados(texto) or cupom.lower() in (texto or "").lower()
