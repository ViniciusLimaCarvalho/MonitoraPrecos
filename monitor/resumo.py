"""Resumo com o ultimo preco visto de cada item de interesse.

Enviado quando o monitor sobe (e sob demanda com `python main.py resumo`),
para voce saber onde cada item esta antes de comecar a esperar promocao nova.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime

from .config import Config
from .notificador import moeda

log = logging.getLogger(__name__)


async def ultimos_por_item(cliente, alvos, conf: Config, pipeline, limite: int = 120) -> dict:
    """Varre o historico recente e guarda a ocorrencia mais nova de cada item."""
    from .listener import nome_do_chat

    achados: dict[str, dict] = {}
    for alvo in alvos:
        canal = nome_do_chat(alvo)
        try:
            async for msg in cliente.iter_messages(alvo, limit=limite):
                texto = msg.message or ""
                if len(texto) < 15:
                    continue
                for alerta in await pipeline.processar(
                    texto, canal=canal, mensagem_id=msg.id, notificar=False
                ):
                    atual = achados.get(alerta.item.nome)
                    if atual is None or msg.date > atual["data"]:
                        achados[alerta.item.nome] = {
                            "data": msg.date,
                            "canal": canal,
                            "promo": alerta.promo,
                            "motivo": alerta.motivo,
                        }
        except Exception as exc:
            log.warning("Nao consegui ler o historico de %s: %s", canal, exc)
    return achados


def montar_texto(achados: dict, conf: Config, quando: datetime) -> str:
    """Uma mensagem so, com uma linha por item da lista de interesse."""
    linhas = [
        f"\U0001f4cb <b>Monitor no ar</b> · {quando.strftime('%d/%m %H:%M')}",
        f"{len(conf.itens)} itens · {len(conf.canais) or 'todos os'} canais",
        "",
    ]
    for item in conf.itens:
        achado = achados.get(item.nome)
        if not achado:
            linhas.append(f"\U0001f515 <b>{html.escape(item.nome)}</b> — sem registro recente")
            continue

        promo = achado["promo"]
        preco = moeda(promo.preco)
        if item.preco_alvo is not None and promo.preco is not None:
            marca = "✅" if promo.preco <= item.preco_alvo else "⚠️"
            preco += f" (alvo {moeda(item.preco_alvo)})"
        else:
            marca = "\U0001f4b0"

        linhas.append(f"{marca} <b>{html.escape(item.nome)}</b> — {preco}")
        detalhe = [achado["data"].astimezone().strftime("%d/%m")]
        if promo.loja:
            detalhe.append(html.escape(promo.loja))
        detalhe.append(html.escape(achado["canal"][:28]))
        linhas.append(f"   <i>{' · '.join(detalhe)}</i>")
        if promo.link:
            linhas.append(f"   {html.escape(promo.link)}")

    linhas.append("\n<i>Precos do historico do canal: podem ter mudado na loja.</i>")
    return "\n".join(linhas)
