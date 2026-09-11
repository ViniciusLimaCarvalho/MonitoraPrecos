"""Une extrator, LLM opcional, matcher, dedup e notificador."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from . import expiracao, extrator, llm
from .armazenamento import Armazenamento
from .config import Config
from .extrator import Promocao
from .matcher import Alerta, avaliar, casa_item
from .notificador import (
    Notificador,
    marcar_alerta_encerrado,
    montar_mensagem,
    montar_mensagem_fim,
)

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(
        self,
        conf: Config,
        armazenamento: Armazenamento,
        notificador: Notificador,
        usar_llm: bool | None = None,
    ) -> None:
        self.conf = conf
        self.db = armazenamento
        self.notificador = notificador
        # usar_llm=None -> decide pelo config (e valida o Ollama no doctor/run)
        self.usar_llm = conf.llm.habilitado if usar_llm is None else usar_llm

    async def extrair(self, texto: str) -> Promocao:
        promo = extrator.extrair(texto)
        if self.usar_llm and not promo.completa:
            # requests e bloqueante: sai do event loop para nao travar o listener
            promo = await asyncio.to_thread(llm.completar, promo, self.conf.llm)
        return promo

    async def processar(
        self,
        texto: str,
        canal: str,
        mensagem_id: int | None = None,
        quando: datetime | None = None,
        notificar: bool = True,
        forcar: bool = False,
    ) -> list[Alerta]:
        """Processa uma mensagem e devolve os alertas efetivamente disparados.

        forcar=True ignora as duas travas de duplicata. E o que o comando
        manual 'ultimo' usa: se o usuario pediu, tem que chegar.
        """
        texto = (texto or "").strip()
        if len(texto) < 8:
            return []
        if notificar and not forcar and not self.db.mensagem_nova(canal, mensagem_id):
            log.debug("Mensagem %s de %s ja processada.", mensagem_id, canal)
            return []

        promo = await self.extrair(texto)
        candidatos = avaliar(promo, self.conf)
        if not candidatos:
            if self.conf.log_mensagens:
                log.debug("Sem match | %s | %s", canal, texto[:80].replace("\n", " "))
            return []

        quando = quando or datetime.now(timezone.utc)
        disparados: list[Alerta] = []
        for alerta in candidatos:
            chave = self.db.chave(alerta.item.nome, promo.produto, promo.preco, texto)
            if notificar and not forcar and self.db.ja_alertado(chave):
                log.info("Duplicado ignorado: %s (%s)", alerta.item.nome, canal)
                continue
            if notificar:
                local = quando.astimezone()
                # requests bloqueia: em rede lenta, 40s parados travariam o listener
                notificacao_id = await asyncio.to_thread(
                    self.notificador.alertar, alerta, canal, local
                )
                self.db.registrar(
                    chave,
                    alerta.item.nome,
                    promo.produto,
                    promo.preco,
                    canal,
                    mensagem_id,
                    promo.link,
                    cupom=promo.cupom,
                    notificacao_id=notificacao_id,
                    texto_alerta=montar_mensagem(alerta, canal, local),
                )
            disparados.append(alerta)
        return disparados

    # ------------------------------------------------------ fim da promocao

    async def encerrar(self, registro, motivo: str, quando: datetime | None = None) -> None:
        """Marca o alerta como encerrado, edita o alerta antigo e avisa."""
        quando = (quando or datetime.now(timezone.utc)).astimezone()
        self.db.marcar_expirado(registro["chave"], motivo)

        if registro["texto_alerta"] and registro["notificacao_id"]:
            await asyncio.to_thread(
                self.notificador.editar_texto,
                registro["notificacao_id"],
                marcar_alerta_encerrado(registro["texto_alerta"], motivo),
            )
        await asyncio.to_thread(
            self.notificador.enviar_texto,
            montar_mensagem_fim(registro, motivo, quando),
            registro["notificacao_id"],
        )
        log.info("ACABOU %s | %s", registro["item"], motivo)

    async def verificar_fim_por_mensagem(
        self,
        texto: str,
        canal: str,
        responde_a: int | None = None,
        quando: datetime | None = None,
    ) -> int:
        """Mensagem nova que anuncia o fim de alguma promocao ja alertada."""
        if not expiracao.parece_fim(texto):
            return 0

        janela = self.conf.janela_fim_horas
        registros = {}

        # 1) cupom nomeado ("Cupom BOLSOCHEIO ESGOTADO") - sinal mais forte
        for r in self.db.ativos_por_cupom(expiracao.cupons_citados(texto), janela):
            registros[r["chave"]] = (r, f"cupom {r['cupom']} esgotado (aviso do canal)")

        # 2) resposta direta a mensagem que gerou o alerta
        if responde_a is not None:
            for r in self.db.ativos_por_mensagem(canal, responde_a, janela):
                registros.setdefault(r["chave"], (r, "o canal respondeu que acabou"))

        # 3) o aviso cita o proprio item ("Nintendo Switch 2 ESGOTADO")
        for r in self.db.ativos_do_canal(canal, janela):
            if r["chave"] in registros:
                continue
            item = next((i for i in self.conf.itens if i.nome == r["item"]), None)
            if item and casa_item(item, texto):
                registros[r["chave"]] = (r, "o canal anunciou o fim desta oferta")

        for registro, motivo in registros.values():
            await self.encerrar(registro, motivo, quando)
        return len(registros)

    async def verificar_fim_por_edicao(
        self, texto: str, canal: str, mensagem_id: int, quando: datetime | None = None
    ) -> int:
        """A mensagem que gerou o alerta foi editada: ainda vale a oferta?

        Esses canais editam quase toda mensagem por rotina (link, ajuste de
        texto), entao a edicao sozinha nao significa nada: comparamos preco,
        cupom e marcadores com o que foi alertado.
        """
        registros = self.db.ativos_por_mensagem(canal, mensagem_id, self.conf.janela_fim_horas)
        if not registros:
            return 0

        promo = await self.extrair(texto)
        encerrados = 0
        for registro in registros:
            motivo = expiracao.motivo_edicao(
                texto, registro["preco"], registro["cupom"], promo
            )
            if motivo:
                await self.encerrar(registro, motivo, quando)
                encerrados += 1
        return encerrados

    async def verificar_fim_por_remocao(
        self, canal: str, mensagens: list[int], quando: datetime | None = None
    ) -> int:
        """Mensagem apagada no canal: a oferta saiu do ar."""
        encerrados = 0
        for mensagem_id in mensagens:
            for registro in self.db.ativos_por_mensagem(
                canal, mensagem_id, self.conf.janela_fim_horas
            ):
                await self.encerrar(registro, "a mensagem foi apagada do canal", quando)
                encerrados += 1
        return encerrados
