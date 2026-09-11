"""Envio do alerta: bot do Telegram (Bot API) com eco no console."""

from __future__ import annotations

import html
import logging
import time
from datetime import datetime

import requests

from .config import NotificacaoConf
from .matcher import Alerta

log = logging.getLogger(__name__)


def moeda(valor: float | None) -> str:
    if valor is None:
        return "nao informado"
    inteiro, _, centavos = f"{valor:,.2f}".partition(".")
    return "R$ " + inteiro.replace(",", ".") + "," + centavos


def montar_mensagem(alerta: Alerta, canal: str, quando: datetime) -> str:
    p = alerta.promo
    linhas = [f"<b>{html.escape(alerta.item.nome)}</b>"]

    if p.produto:
        linhas.append(html.escape(p.produto))

    preco = moeda(p.preco)
    if p.preco_original and p.preco:
        desconto = round((1 - p.preco / p.preco_original) * 100)
        preco += f" <s>{moeda(p.preco_original)}</s> (-{desconto}%)"
    linhas.append(f"\n\U0001f4b0 <b>{preco}</b>")

    if alerta.item.preco_alvo is not None:
        marca = "✅" if alerta.motivo == "abaixo do alvo" else "⚠️"
        linhas.append(f"{marca} alvo: {moeda(alerta.item.preco_alvo)} ({alerta.motivo})")
    if p.loja:
        linhas.append(f"\U0001f3ea {html.escape(p.loja)}")
    if p.cupom:
        linhas.append(f"\U0001f3f7️ cupom: <code>{html.escape(p.cupom)}</code>")

    linhas.append(f"\n\U0001f4e2 {html.escape(canal)} · {quando.strftime('%d/%m %H:%M')}")
    if p.link:
        linhas.append(html.escape(p.link))
    return "\n".join(linhas)


class Notificador:
    """Manda o alerta pelo bot; se o bot nao estiver configurado, so loga."""

    def __init__(self, conf: NotificacaoConf, avisar: bool = True) -> None:
        self.conf = conf
        self.ativo = bool(conf.bot_token and conf.chat_id)
        if not self.ativo and avisar:
            log.warning(
                "Bot de notificacao nao configurado (bot_token/chat_id). "
                "Os alertas sairao apenas no console."
            )

    def enviar_texto(self, texto: str, responder_a: int | None = None) -> int | None:
        """Envia e devolve o message_id (necessario para editar depois)."""
        if not self.ativo:
            return None
        corpo = {
            "chat_id": self.conf.chat_id,
            "text": texto,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        if responder_a:
            corpo["reply_to_message_id"] = responder_a
            corpo["allow_sending_without_reply"] = True
        return self._chamar("sendMessage", corpo)

    def editar_texto(self, notificacao_id: int, texto: str) -> bool:
        """Reescreve um alerta ja enviado (usado para marcar 'ENCERRADA')."""
        if not self.ativo or not notificacao_id:
            return False
        return self._chamar(
            "editMessageText",
            {
                "chat_id": self.conf.chat_id,
                "message_id": notificacao_id,
                "text": texto,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
        ) is not None

    def _chamar(self, metodo: str, corpo: dict) -> int | None:
        """Chama a Bot API, reenviando em caso de timeout/queda da rede."""
        url = f"https://api.telegram.org/bot{self.conf.bot_token}/{metodo}"
        for tentativa in range(1, max(1, self.conf.tentativas) + 1):
            try:
                r = requests.post(url, json=corpo, timeout=self.conf.timeout)
                if r.status_code != 200:
                    # 4xx e erro nosso (chat errado, texto invalido): nao adianta insistir
                    log.error("Telegram recusou %s (%s): %s", metodo, r.status_code, r.text[:300])
                    return None
                return (r.json().get("result") or {}).get("message_id")
            except Exception as exc:
                espera = 3 * tentativa
                if tentativa >= max(1, self.conf.tentativas):
                    log.error("Falha em %s apos %d tentativas: %s", metodo, tentativa, exc)
                    return None
                log.warning(
                    "Falha em %s (%s). Nova tentativa em %ds...", metodo, type(exc).__name__, espera
                )
                time.sleep(espera)
        return None

    def alertar(self, alerta: Alerta, canal: str, quando: datetime) -> int | None:
        mensagem = montar_mensagem(alerta, canal, quando)
        if self.conf.console or not self.ativo:
            log.info("ALERTA %s | %s | %s", alerta.item.nome, moeda(alerta.promo.preco), canal)
        return self.enviar_texto(mensagem)


def montar_mensagem_fim(registro, motivo: str, quando: datetime) -> str:
    """Aviso curto de que a promocao/cupom acabou."""
    item = registro["item"]
    linhas = [f"\U0001f6d1 <b>ACABOU: {html.escape(str(item))}</b>"]
    if registro["produto"]:
        linhas.append(f"<s>{html.escape(str(registro['produto']))}</s>")
    if registro["preco"] is not None:
        linhas.append(f"era {moeda(registro['preco'])}")
    if registro["cupom"]:
        linhas.append(f"cupom <code>{html.escape(str(registro['cupom']))}</code>")
    linhas.append(f"\nmotivo: {html.escape(motivo)}")
    linhas.append(f"\U0001f4e2 {html.escape(str(registro['canal']))} · {quando.strftime('%d/%m %H:%M')}")
    return "\n".join(linhas)


def marcar_alerta_encerrado(texto_original: str, motivo: str) -> str:
    """Reescreve o alerta original com o carimbo de encerrado no topo."""
    return f"\U0001f6d1 <b>ENCERRADA</b> — {html.escape(motivo)}\n\n{texto_original}"
