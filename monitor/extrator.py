"""Extracao de produto, preco, loja, link e cupom do texto da mensagem.

Regex primeiro (rapido e 100% offline). Se o regex nao achar preco ou produto,
o chamador pode acionar o fallback de LLM local (ver monitor/llm.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .texto import limpar_simbolos, normalizar

# ---------------------------------------------------------------- preco

_NUMERO = r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+,\d{1,2}|\d{1,7}\.\d{2}|\d{1,7}"
_MOEDA = r"(?:R\$|RS)\s*"
_RE_PRECO = re.compile(
    rf"(?:{_MOEDA})({_NUMERO})|({_NUMERO})\s*(?:reais|conto)\b", re.IGNORECASE
)

_RE_PARCELA = re.compile(r"(\d{1,2})\s*x\s*(?:de\s*)?$", re.IGNORECASE)
_RE_FINAL = re.compile(
    r"(?:por|sai por|apenas|somente|s[oó]|[aà] vista|no pix|no boleto|fica|"
    r"pre[cç]o final|de volta por)\s*[:\-]?\s*$",
    re.IGNORECASE,
)
_RE_ORIGINAL = re.compile(
    r"(?:de|era|antes|estava|valor original)\s*[:\-]?\s*$", re.IGNORECASE
)

_RE_LINK = re.compile(r"https?://[^\s\)\]\>]+", re.IGNORECASE)
# aceita "cupom: X", "cupom X" e "Cupom `X`" (crase/asterisco/aspas ao redor)
_RE_CUPOM = re.compile(
    r"(?:cupom|c[oó]digo|coupon)\s*[:\-]?[\s`*\"']*([A-Za-z0-9][A-Za-z0-9._-]{2,24})",
    re.IGNORECASE,
)

_LOJAS = {
    "amazon": "Amazon",
    "amzn.to": "Amazon",
    "amzn": "Amazon",
    "mercadolivre": "Mercado Livre",
    "mercado livre": "Mercado Livre",
    "mercadolibre": "Mercado Livre",
    "meli.la": "Mercado Livre",
    "magazineluiza": "Magalu",
    "magazine luiza": "Magalu",
    "magazinevoce": "Magalu",
    "magalu": "Magalu",
    "americanas": "Americanas",
    "casasbahia": "Casas Bahia",
    "casas bahia": "Casas Bahia",
    "pontofrio": "Ponto",
    "ponto frio": "Ponto",
    "kabum": "KaBuM!",
    "terabyteshop": "Terabyte",
    "terabyte": "Terabyte",
    "pichau": "Pichau",
    "aliexpress": "AliExpress",
    "shopee": "Shopee",
    "submarino": "Submarino",
    "carrefour": "Carrefour",
    "fastshop": "Fast Shop",
    "fast shop": "Fast Shop",
    "shoptime": "Shoptime",
    "netshoes": "Netshoes",
    "centauro": "Centauro",
    "samsung": "Samsung",
    "dell": "Dell",
    "lenovo": "Lenovo",
}

# Linhas que quase nunca sao o nome do produto.
_RUIDO = re.compile(
    r"^(?:cupom|c[oó]digo|frete|link|canal|grupo|entrega|vendido|valor|pre[cç]o|"
    r"promo[cç][ãa]o|oferta|ofertas|aproveite|corre|compre|clique|acesse|assine|"
    r"menor pre[cç]o|hist[oó]rico|estoque|limite|obs|aten[cç][ãa]o|dica|"
    r"chegou|imperd[ií]vel|baixou)\b",
    re.IGNORECASE,
)


@dataclass
class Promocao:
    """Resultado da extracao de uma mensagem."""

    texto: str
    produto: str | None = None
    preco: float | None = None
    preco_original: float | None = None
    loja: str | None = None
    link: str | None = None
    cupom: str | None = None
    origem: str = "regex"  # regex | llm | regex+llm
    avisos: list[str] = field(default_factory=list)

    @property
    def completa(self) -> bool:
        return self.preco is not None and bool(self.produto)


def parse_preco(bruto: str) -> float | None:
    """Converte 1.234,56 / 1.234 / 99,90 / 1999 / 19.99 em float."""
    txt = (bruto or "").strip().replace(" ", "")
    if not txt:
        return None
    tem_virgula, tem_ponto = "," in txt, "." in txt
    if tem_virgula and tem_ponto:
        # o separador que aparece por ultimo e o decimal
        if txt.rfind(",") > txt.rfind("."):
            txt = txt.replace(".", "").replace(",", ".")
        else:
            txt = txt.replace(",", "")
    elif tem_virgula:
        txt = txt.replace(",", ".")
    elif tem_ponto:
        inteiro, _, frac = txt.rpartition(".")
        # 1.999 e milhar; 19.99 e decimal
        txt = txt.replace(".", "") if len(frac) == 3 else f"{inteiro}.{frac}"
    try:
        valor = float(txt)
    except ValueError:
        return None
    return valor if 0 < valor < 10_000_000 else None


def _candidatos_preco(texto: str) -> list[tuple[float, str]]:
    """Lista (valor, tipo). Tipos: final, original, parcela, simples."""
    achados: list[tuple[float, str]] = []
    for m in _RE_PRECO.finditer(texto):
        valor = parse_preco(m.group(1) or m.group(2) or "")
        if valor is None:
            continue
        antes = texto[max(0, m.start() - 24) : m.start()]
        parcela = _RE_PARCELA.search(antes)
        if parcela:
            achados.append((valor * int(parcela.group(1)), "parcela"))
        elif _RE_FINAL.search(antes):
            achados.append((valor, "final"))
        elif _RE_ORIGINAL.search(antes):
            achados.append((valor, "original"))
        else:
            achados.append((valor, "simples"))
    return achados


def extrair_precos(texto: str) -> tuple[float | None, float | None]:
    """Devolve (preco a vista, preco original riscado)."""
    candidatos = _candidatos_preco(texto)
    if not candidatos:
        return None, None

    por_tipo: dict[str, list[float]] = {}
    for valor, tipo in candidatos:
        por_tipo.setdefault(tipo, []).append(valor)

    original = min(por_tipo["original"]) if "original" in por_tipo else None

    preco = None
    for tipo in ("final", "simples", "parcela"):
        if tipo in por_tipo:
            preco = min(por_tipo[tipo])
            break

    # Se so veio o valor precedido por "de", ele e o preco mesmo.
    if preco is None and original is not None:
        preco, original = original, None
    if preco is not None and original is not None and original <= preco:
        original = None
    return preco, original


# ---------------------------------------------------------------- produto


def extrair_produto(texto: str) -> str | None:
    """Heuristica: primeira linha de conteudo, sem preco e sem ruido."""
    for linha in texto.splitlines():
        linha = limpar_simbolos(linha)
        if not linha or _RUIDO.match(linha):
            continue
        if linha.lower().startswith(("http", "www.")):
            continue
        # corta a parte de preco da propria linha ("Mouse X - R$ 99,90")
        corte = _RE_PRECO.search(linha)
        if corte:
            linha = limpar_simbolos(linha[: corte.start()])
            linha = re.sub(r"\b(?:por|de|apenas|somente|sai por)\s*$", "", linha, flags=re.I)
        linha = _RE_LINK.sub("", linha).strip(" -:|,.")
        if len(normalizar(linha).replace(" ", "")) < 4:
            continue
        if len(linha) > 140:
            linha = linha[:140].rsplit(" ", 1)[0]
        return linha
    return None


def extrair_link(texto: str) -> str | None:
    m = _RE_LINK.search(texto)
    return m.group(0).rstrip(".,;)") if m else None


def extrair_cupom(texto: str) -> str | None:
    m = _RE_CUPOM.search(texto)
    if not m:
        return None
    cupom = m.group(1).strip(".,;")
    return cupom if any(c.isdigit() or c.isupper() for c in cupom) else None


def extrair_loja(texto: str, link: str | None) -> str | None:
    """Prioridade: dominio do link > "via/na <loja>" > mencao solta."""
    if link:
        host = (urlparse(link).hostname or "").lower()
        for chave, nome in _LOJAS.items():
            if chave in host:
                return nome
    normalizado = normalizar(texto)
    # "Produto via Amazon" e mais confiavel que uma mencao solta, que
    # costuma ser a marca do produto ("Monitor Samsung via Mercado Livre").
    for chave, nome in _LOJAS.items():
        if re.search(rf"\b(?:via|na|no|em|pela|pelo)\s+{re.escape(chave)}\b", normalizado):
            return nome
    for chave, nome in _LOJAS.items():
        if re.search(rf"\b{re.escape(chave)}\b", normalizado):
            return nome
    return None


def extrair(texto: str) -> Promocao:
    """Extracao completa por regex (sem rede, sem LLM)."""
    texto = texto or ""
    preco, original = extrair_precos(texto)
    link = extrair_link(texto)
    promo = Promocao(
        texto=texto,
        produto=extrair_produto(texto),
        preco=preco,
        preco_original=original,
        loja=extrair_loja(texto, link),
        link=link,
        cupom=extrair_cupom(texto),
    )
    if promo.preco is None:
        promo.avisos.append("preco nao identificado pelo regex")
    if not promo.produto:
        promo.avisos.append("produto nao identificado pelo regex")
    return promo
