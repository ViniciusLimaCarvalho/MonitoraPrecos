"""Listener do Telegram via Telethon (conta de usuario, canais ja assinados)."""

from __future__ import annotations

import logging

import asyncio
from pathlib import Path

from telethon import TelegramClient, events

from .config import Config, ConfigError, carregar
from .pipeline import Pipeline

log = logging.getLogger(__name__)

# arquivo-pedido: como `main.py resumo` fala com o monitor ja no ar
ARQUIVO_PEDIDO_RESUMO = ".pedido_resumo"


def criar_cliente(conf: Config) -> TelegramClient:
    return TelegramClient(conf.telegram.sessao, conf.telegram.api_id, conf.telegram.api_hash)


def nome_do_chat(chat) -> str:
    for atributo in ("title", "username", "first_name"):
        valor = getattr(chat, atributo, None)
        if valor:
            return str(valor)
    return str(getattr(chat, "id", "desconhecido"))


async def listar_dialogos(cliente: TelegramClient) -> list[tuple[str, str, int]]:
    """(titulo, @usuario ou '-', id) de canais e grupos assinados."""
    itens: list[tuple[str, str, int]] = []
    async for dialogo in cliente.iter_dialogs():
        if dialogo.is_user:
            continue
        entidade = dialogo.entity
        usuario = getattr(entidade, "username", None)
        itens.append((dialogo.name or "-", f"@{usuario}" if usuario else "-", dialogo.id))
    return itens


async def _por_titulo(cliente: TelegramClient, alvo: str):
    """Ultimo recurso: acha o dialogo cujo nome contem o texto informado."""
    alvo = alvo.lower()
    async for dialogo in cliente.iter_dialogs():
        if not dialogo.is_user and alvo in (dialogo.name or "").lower():
            return dialogo.entity
    return None


async def resolver_canais(cliente: TelegramClient, canais: list[str]) -> list:
    """Aceita @usuario, link t.me, id numerico ou parte do titulo."""
    entidades = []
    for bruto in canais:
        alvo: str | int = bruto.strip()
        if isinstance(alvo, str):
            if alvo.startswith(("https://t.me/", "http://t.me/", "t.me/")):
                alvo = "@" + alvo.rstrip("/").rsplit("/", 1)[-1]
            if alvo.lstrip("-").isdigit():
                alvo = int(alvo)
        try:
            entidade = await cliente.get_entity(alvo)
        except Exception as exc:
            log.debug("get_entity falhou para %s: %s", bruto, exc)
            entidade = await _por_titulo(cliente, str(bruto))
        if entidade is None:
            log.error(
                "Canal nao encontrado: %s. Confira em 'python main.py canais' "
                "se voce esta inscrito e se o identificador esta certo.",
                bruto,
            )
            continue
        entidades.append(entidade)
        log.info("Monitorando: %s", nome_do_chat(entidade))
    return entidades


async def enviar_resumo(conf: Config, pipeline: Pipeline, cliente: TelegramClient, alvos) -> None:
    """Manda, uma vez, o ultimo preco visto de cada item da lista."""
    from datetime import datetime, timezone

    from .resumo import montar_texto, ultimos_por_item

    try:
        achados = await ultimos_por_item(
            cliente, alvos or [], conf, pipeline, conf.notificacao.resumo_mensagens
        )
        texto = montar_texto(achados, conf, datetime.now(timezone.utc).astimezone())
        await asyncio.to_thread(pipeline.notificador.enviar_texto, texto)
        log.info("Resumo enviado (%d de %d itens com preco recente).",
                 len(achados), len(conf.itens))
    except Exception:
        log.exception("Falha ao montar o resumo")


async def vigiar_pedidos(pasta, conf: Config, pipeline: Pipeline, cliente: TelegramClient,
                         alvos, intervalo: int = 5) -> None:
    """Atende `python main.py resumo` enquanto o monitor esta no ar.

    O .session e um SQLite de dono unico: um segundo processo nao consegue
    abrir a mesma conta enquanto o monitor roda. Entao o comando avulso so
    deixa um arquivo-pedido na pasta e quem monta e manda o resumo e este
    processo, que ja esta conectado.
    """
    pedido = Path(pasta) / ARQUIVO_PEDIDO_RESUMO
    while True:
        await asyncio.sleep(intervalo)
        try:
            if not pedido.exists():
                continue
            pedido.unlink()
        except OSError:
            continue
        log.info("Pedido de resumo recebido; montando...")
        await enviar_resumo(conf, pipeline, cliente, alvos)


async def rodar(conf: Config, pipeline: Pipeline, cliente: TelegramClient,
                caminho_config=None) -> None:
    """Escuta novas mensagens ate ser interrompido (Ctrl+C)."""
    if conf.canais:
        alvos = await resolver_canais(cliente, conf.canais)
        if not alvos:
            raise SystemExit(
                "Nenhum canal da lista pode ser resolvido. Rode 'python main.py canais'."
            )
    else:
        alvos = None
        log.warning("Nenhum canal em 'canais': ouvindo TODOS os chats da sua conta.")

    @cliente.on(events.NewMessage(chats=alvos))
    async def _ao_receber(evento):
        texto = evento.message.message or ""
        canal = nome_do_chat(await evento.get_chat())
        if conf.log_mensagens:
            log.debug("[%s] %s", canal, texto[:120].replace("\n", " "))
        try:
            await pipeline.processar(
                texto,
                canal=canal,
                mensagem_id=evento.message.id,
                quando=evento.message.date,
            )
            # a mesma mensagem pode anunciar o fim de outra oferta
            responde_a = getattr(evento.message.reply_to, "reply_to_msg_id", None)
            await pipeline.verificar_fim_por_mensagem(
                texto, canal=canal, responde_a=responde_a, quando=evento.message.date
            )
        except Exception:
            # um erro em uma mensagem nunca pode derrubar o monitor
            log.exception("Erro ao processar mensagem de %s", canal)

    @cliente.on(events.MessageEdited(chats=alvos))
    async def _ao_editar(evento):
        canal = nome_do_chat(await evento.get_chat())
        try:
            await pipeline.verificar_fim_por_edicao(
                evento.message.message or "",
                canal=canal,
                mensagem_id=evento.message.id,
                quando=evento.message.edit_date or evento.message.date,
            )
        except Exception:
            log.exception("Erro ao processar edicao em %s", canal)

    @cliente.on(events.MessageDeleted(chats=alvos))
    async def _ao_apagar(evento):
        try:
            chat = await evento.get_chat()
            canal = nome_do_chat(chat) if chat else ""
        except Exception:
            canal = ""
        if not canal:
            return
        try:
            await pipeline.verificar_fim_por_remocao(canal, list(evento.deleted_ids))
        except Exception:
            log.exception("Erro ao processar remocao em %s", canal)

    eu = await cliente.get_me()
    log.info(
        "Monitor ativo como %s | %d item(ns) | %s",
        getattr(eu, "username", None) or getattr(eu, "first_name", "?"),
        len(conf.itens),
        "LLM local ligado" if pipeline.usar_llm else "somente regex",
    )
    log.info("Aguardando promocoes... (Ctrl+C para sair)")

    # em segundo plano: ja escutando enquanto o resumo e montado
    if conf.notificacao.resumo_ao_iniciar and alvos:
        asyncio.create_task(enviar_resumo(conf, pipeline, cliente, alvos))
    if caminho_config:
        asyncio.create_task(vigiar_config(caminho_config, conf, pipeline))
        pasta = Path(caminho_config).resolve().parent
        pedido = pasta / ARQUIVO_PEDIDO_RESUMO
        pedido.unlink(missing_ok=True)  # descarta pedido velho de outra execucao
        asyncio.create_task(vigiar_pedidos(pasta, conf, pipeline, cliente, alvos))

    await cliente.run_until_disconnected()


async def vigiar_config(caminho, conf: Config, pipeline: Pipeline, intervalo: int = 30) -> None:
    """Recarrega itens e ajustes quando o config.yaml muda, sem reiniciar.

    A lista de canais NAO e recarregada: os handlers ja foram registrados nas
    entidades resolvidas no start, entao mudar canais ainda pede reinicio.
    """
    caminho = Path(caminho)
    try:
        assinatura = caminho.stat().st_mtime
    except OSError:
        return

    while True:
        await asyncio.sleep(intervalo)
        try:
            atual = caminho.stat().st_mtime
            if atual == assinatura:
                continue
            assinatura = atual
            novo = carregar(caminho)
        except ConfigError as exc:
            log.error("config.yaml invalido, mantendo o anterior: %s", exc)
            continue
        except Exception as exc:
            log.error("Nao consegui reler o config.yaml: %s", exc)
            continue

        if set(novo.canais) != set(conf.canais):
            log.warning("A lista de canais mudou: reinicie o monitor para valer.")

        conf.itens = novo.itens
        conf.alertar_sem_preco = novo.alertar_sem_preco
        conf.janela_dedup_horas = novo.janela_dedup_horas
        conf.janela_fim_horas = novo.janela_fim_horas
        conf.log_mensagens = novo.log_mensagens
        pipeline.db.janela_horas = novo.janela_dedup_horas
        log.info("config.yaml recarregado: %d item(ns) agora.", len(conf.itens))
