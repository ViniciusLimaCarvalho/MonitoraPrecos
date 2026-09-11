"""Fallback opcional: LLM local via Ollama (nenhuma API paga envolvida).

So e chamado quando o regex nao conseguiu extrair produto ou preco. Se o
Ollama estiver fora do ar, a funcao devolve None e o fluxo segue com o regex.
"""

from __future__ import annotations

import json
import logging
import re

import requests

from .config import LLMConf
from .extrator import Promocao, parse_preco

log = logging.getLogger(__name__)

_PROMPT = """Voce extrai dados de mensagens de promocao em portugues do Brasil.
Responda SOMENTE com um JSON valido no formato:
{"produto": string|null, "preco": number|null, "loja": string|null, "link": string|null}

Regras:
- "preco" e o valor final a vista, em reais, como numero (ex: 3399.90). Nunca o valor da parcela.
- Se a mensagem nao for uma promocao de produto, devolva todos os campos null.
- Nao invente dados que nao estejam na mensagem.

Mensagem:
---
{mensagem}
---
JSON:"""


def disponivel(conf: LLMConf) -> bool:
    """Testa se o Ollama responde e resolve a tag exata do modelo.

    O /api/generate exige a tag exata: pedir "qwen2.5:7b" quando o que existe
    e "qwen2.5:7b-instruct" da 404. Se so houver variante do mesmo modelo
    base, ajustamos conf.modelo para a tag que realmente existe.
    """
    try:
        r = requests.get(f"{conf.url}/api/tags", timeout=5)
        r.raise_for_status()
        modelos = [m.get("name", "") for m in r.json().get("models", []) if m.get("name")]
    except Exception as exc:
        log.debug("Ollama indisponivel: %s", exc)
        return False

    if conf.modelo in modelos:
        return True

    base = conf.modelo.split(":")[0]
    variantes = [m for m in modelos if m.split(":")[0] == base]
    if variantes:
        log.warning(
            "Modelo '%s' nao existe no Ollama; usando '%s'. "
            "Ajuste llm.modelo no config.yaml para nao depender disso.",
            conf.modelo,
            variantes[0],
        )
        conf.modelo = variantes[0]
        return True

    log.warning(
        "Modelo '%s' nao encontrado. Baixe com: ollama pull %s", conf.modelo, conf.modelo
    )
    return False


def _json_do_texto(bruto: str) -> dict | None:
    """Pega o primeiro objeto JSON da resposta, mesmo com texto em volta."""
    bruto = re.sub(r"^```(?:json)?|```$", "", bruto.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(bruto)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", bruto, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def consultar(texto: str, conf: LLMConf) -> dict | None:
    """Pergunta ao modelo local. Devolve dict ou None se falhar."""
    # replace() em vez de format(): o prompt contem chaves literais do JSON.
    prompt = _PROMPT.replace("{mensagem}", texto[:2000])
    try:
        r = requests.post(
            f"{conf.url}/api/generate",
            json={
                "model": conf.modelo,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            },
            timeout=conf.timeout,
        )
        r.raise_for_status()
        return _json_do_texto(r.json().get("response", ""))
    except Exception as exc:
        log.warning("Fallback LLM falhou (%s). Seguindo so com o regex.", exc)
        return None


def completar(promo: Promocao, conf: LLMConf) -> Promocao:
    """Preenche com o LLM apenas os campos que o regex nao achou."""
    dados = consultar(promo.texto, conf)
    if not dados:
        return promo

    if promo.preco is None:
        bruto = dados.get("preco")
        preco = (
            float(bruto)
            if isinstance(bruto, (int, float))
            else parse_preco(str(bruto)) if bruto else None
        )
        if preco and 0 < preco < 10_000_000:
            promo.preco = preco

    if not promo.produto and isinstance(dados.get("produto"), str):
        produto = dados["produto"].strip()
        if len(produto) >= 3:
            promo.produto = produto[:140]

    if not promo.loja and isinstance(dados.get("loja"), str) and dados["loja"].strip():
        promo.loja = dados["loja"].strip()[:60]

    if not promo.link and isinstance(dados.get("link"), str):
        link = dados["link"].strip()
        # so aceita link que realmente aparece na mensagem (evita alucinacao)
        if link.startswith("http") and link[:40] in promo.texto:
            promo.link = link

    promo.origem = "regex+llm"
    promo.avisos = [a for a in promo.avisos if "nao identificado" not in a]
    if promo.preco is None:
        promo.avisos.append("preco nao identificado (regex + LLM)")
    return promo
