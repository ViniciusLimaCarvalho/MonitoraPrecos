"""Compara a promocao extraida com a lista de itens de interesse."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Config, Item
from .extrator import Promocao
from .texto import compactar, normalizar


@dataclass
class Alerta:
    item: Item
    promo: Promocao
    palavra: str
    motivo: str  # "abaixo do alvo" | "sem preco alvo" | "preco desconhecido"

    @property
    def dentro_do_alvo(self) -> bool:
        return self.item.preco_alvo is not None and self.promo.preco is not None


def _casa(palavra: str, texto_norm: str, texto_comp: str) -> bool:
    """Casa por palavra inteira; tambem casa ignorando espacos (rtx 4070/rtx4070)."""
    alvo = normalizar(palavra)
    if not alvo:
        return False
    if re.search(rf"(?<![0-9a-z]){re.escape(alvo)}(?![0-9a-z])", texto_norm):
        return True

    # Fallback compacto: existe so para "rtx 4070" casar com "rtx4070". Como ele
    # ignora fronteira de palavra, restringimos a palavras-chave com espaco ou
    # digito -- senao "ocarina" casaria dentro de "feminino Carina Lux".
    if " " not in alvo and not any(c.isdigit() for c in alvo):
        return False
    compacto = compactar(palavra)
    return len(compacto) >= 4 and compacto in texto_comp


def _palavra_encontrada(item: Item, texto_norm: str, texto_comp: str) -> str | None:
    for palavra in item.palavras_chave:
        if _casa(palavra, texto_norm, texto_comp):
            return palavra
    return None


def casa_item(item: Item, texto: str) -> bool:
    """O texto menciona este item de interesse? (respeita 'excluir')"""
    norm = normalizar(texto)
    comp = norm.replace(" ", "")
    if any(_casa(neg, norm, comp) for neg in item.excluir):
        return False
    return _palavra_encontrada(item, norm, comp) is not None


def avaliar(promo: Promocao, conf: Config) -> list[Alerta]:
    """Devolve um alerta por item de interesse que casou com a mensagem.

    Regras:
    - palavra-chave e buscada no texto inteiro da mensagem (mais robusto que so
      no nome extraido), somando o produto extraido pelo LLM quando existir;
    - 'excluir' derruba o item (ex.: evitar capa/suporte do produto);
    - com preco_alvo definido: alerta so se o preco for <= alvo;
    - mensagem sem preco identificado (noticia, recado do canal) so alerta se
      conf.alertar_sem_preco for True, com ou sem preco_alvo definido.
    """
    base = promo.texto
    if promo.produto and promo.produto not in base:
        base = f"{promo.produto}\n{base}"
    texto_norm = normalizar(base)
    texto_comp = texto_norm.replace(" ", "")

    alertas: list[Alerta] = []
    for item in conf.itens:
        palavra = _palavra_encontrada(item, texto_norm, texto_comp)
        if not palavra:
            continue
        if any(_casa(neg, texto_norm, texto_comp) for neg in item.excluir):
            continue
        # piso de preco: separa o produto de acessorios/jogos que citam o
        # mesmo nome ("Mario Kart World - Switch 2 - R$ 349")
        if (
            item.preco_minimo is not None
            and promo.preco is not None
            and promo.preco < item.preco_minimo
        ):
            continue

        # sem preco a mensagem costuma ser noticia/recado, nao promocao
        if promo.preco is None and not conf.alertar_sem_preco:
            continue

        if item.preco_alvo is None:
            motivo = "sem preco alvo" if promo.preco is not None else "preco desconhecido"
        elif promo.preco is None:
            motivo = "preco desconhecido"
        elif promo.preco <= item.preco_alvo:
            motivo = "abaixo do alvo"
        else:
            continue

        alertas.append(Alerta(item=item, promo=promo, palavra=palavra, motivo=motivo))
    return alertas
