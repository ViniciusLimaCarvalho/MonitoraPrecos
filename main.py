"""Monitor de promocoes do Telegram.

Uso:
    python main.py doctor      valida config, bot e LLM local
    python main.py login       autentica sua conta (primeira vez)
    python main.py canais      lista canais/grupos assinados (para o config)
    python main.py testar      testa a extracao em um texto colado
    python main.py varrer      testa a config no historico dos canais
    python main.py ultimo      manda pelo bot a promocao mais recente de um item
    python main.py resumo      manda o ultimo preco de cada item da lista
    python main.py simular-fim testa o aviso de promocao/cupom encerrado
    python main.py historico   mostra os ultimos alertas
    python main.py run         inicia o monitoramento (padrao)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import sys
from pathlib import Path

from monitor import __version__
from monitor.armazenamento import Armazenamento
from monitor.config import Config, ConfigError, carregar
from monitor.notificador import Notificador, moeda
from monitor.pipeline import Pipeline

RAIZ = Path(__file__).resolve().parent


def configurar_log(verboso: bool) -> None:
    # console do Windows costuma ser cp1252: evita quebrar em emoji/acento
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    # sob pythonw.exe (Agendador de Tarefas) nao existe console: sys.stdout e None
    handlers = [logging.FileHandler(RAIZ / "monitor.log", encoding="utf-8")]
    if sys.stdout is not None:
        handlers.insert(0, logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.DEBUG if verboso else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%d/%m %H:%M:%S",
        handlers=handlers,
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)


def carregar_conf(args, exigir_telegram: bool = True) -> Config:
    try:
        return carregar(args.config, exigir_telegram=exigir_telegram)
    except ConfigError as exc:
        print(f"Erro de configuracao: {exc}", file=sys.stderr)
        raise SystemExit(2)


def sessao_ocupada(exc: BaseException) -> bool:
    """True quando o erro e o monitor segurando o .session (SQLite dono unico)."""
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


def encerrar_cliente(cliente) -> None:
    """Fecha o cliente que ficou pela metade, senao o asyncio loga 'Task destroyed'."""
    try:
        cliente.loop.run_until_complete(cliente.disconnect())
    except Exception:
        pass


def montar_pipeline(conf: Config, notificar: bool = True) -> tuple[Pipeline, Armazenamento]:
    db = Armazenamento(RAIZ / conf.banco, conf.janela_dedup_horas)
    pipeline = Pipeline(conf, db, Notificador(conf.notificacao, avisar=notificar))
    if pipeline.usar_llm:
        from monitor import llm

        if not llm.disponivel(conf.llm):
            logging.warning(
                "LLM local (%s em %s) indisponivel: seguindo apenas com regex.",
                conf.llm.modelo,
                conf.llm.url,
            )
            pipeline.usar_llm = False
    return pipeline, db


# ------------------------------------------------------------------ comandos


def cmd_run(args) -> int:
    from monitor.listener import criar_cliente, rodar

    conf = carregar_conf(args)
    pipeline, db = montar_pipeline(conf)
    db.limpar_antigos()
    cliente = criar_cliente(conf)

    async def principal():
        await cliente.start()
        await rodar(conf, pipeline, cliente, caminho_config=args.config)

    try:
        with cliente:
            cliente.loop.run_until_complete(principal())
    except KeyboardInterrupt:
        print("\nMonitor encerrado.")
    finally:
        db.fechar()
    return 0


def cmd_login(args) -> int:
    from monitor.listener import criar_cliente

    conf = carregar_conf(args)
    if not sys.stdin.isatty():
        print(
            "O login e interativo (telefone, codigo e senha 2FA) e este terminal "
            "nao aceita digitacao. "
            "Abra o PowerShell na pasta do projeto e rode: python main.py login",
            file=sys.stderr,
        )
        return 2
    try:
        with criar_cliente(conf) as cliente:
            eu = cliente.loop.run_until_complete(cliente.get_me())
            usuario = f" (@{eu.username})" if eu.username else ""
            print(f"Autenticado como {eu.first_name}{usuario}. Sessao salva.")
    except EOFError:
        print(
            "Nao foi possivel ler o que voce digitou. Rode o login direto "
            "no PowerShell, fora de qualquer wrapper.",
            file=sys.stderr,
        )
        return 2
    return 0


def cmd_canais(args) -> int:
    from monitor.listener import criar_cliente, listar_dialogos

    conf = carregar_conf(args)
    with criar_cliente(conf) as cliente:
        dialogos = cliente.loop.run_until_complete(listar_dialogos(cliente))
    print(f"\n{len(dialogos)} canais/grupos encontrados:\n")
    print(f"{'TITULO':<45} {'USUARIO':<24} ID")
    print("-" * 92)
    for titulo, usuario, ident in dialogos:
        print(f"{titulo[:44]:<45} {usuario[:23]:<24} {ident}")
    print(
        "\nCopie o @usuario (ou o ID, para canais privados) para a lista "
        "'canais:' do config.yaml."
    )
    return 0


def cmd_testar(args) -> int:
    conf = carregar_conf(args, exigir_telegram=False)
    if args.texto:
        texto = " ".join(args.texto)
    else:
        print("Cole a mensagem de promocao e finalize com Ctrl+Z (Enter) no Windows:\n")
        texto = sys.stdin.read()

    pipeline, db = montar_pipeline(conf, notificar=False)
    try:
        promo = asyncio.run(pipeline.extrair(texto))
        print("\n--- extracao -------------------------------------------")
        print(f"produto ......: {promo.produto}")
        print(f"preco ........: {moeda(promo.preco)}")
        print(f"preco original: {moeda(promo.preco_original) if promo.preco_original else '-'}")
        print(f"loja .........: {promo.loja or '-'}")
        print(f"cupom ........: {promo.cupom or '-'}")
        print(f"link .........: {promo.link or '-'}")
        print(f"origem .......: {promo.origem}")
        if promo.avisos:
            print(f"avisos .......: {'; '.join(promo.avisos)}")

        alertas = asyncio.run(
            pipeline.processar(texto, canal="teste", mensagem_id=None, notificar=False)
        )
        print("\n--- itens de interesse que casariam ---------------------")
        if not alertas:
            print("nenhum (nao dispararia alerta)")
        for a in alertas:
            print(f"  {a.item.nome}  (palavra: {a.palavra}; motivo: {a.motivo})")
    finally:
        db.fechar()
    return 0


def cmd_varrer(args) -> int:
    """Roda a config atual contra o historico dos canais, sem notificar.

    Serve para afinar palavras-chave e precos-alvo antes de deixar rodando.
    """
    from monitor.listener import criar_cliente, nome_do_chat, resolver_canais

    conf = carregar_conf(args)
    pipeline, db = montar_pipeline(conf, notificar=False)
    cliente = criar_cliente(conf)

    async def principal():
        await cliente.start()
        total = achados = 0
        for alvo in await resolver_canais(cliente, conf.canais):
            canal = nome_do_chat(alvo)
            async for msg in cliente.iter_messages(alvo, limit=args.limite):
                texto = msg.message or ""
                if len(texto) < 15:
                    continue
                total += 1
                for a in await pipeline.processar(
                    texto, canal=canal, mensagem_id=msg.id, notificar=False
                ):
                    achados += 1
                    print()
                    print(
                        f"[{msg.date:%d/%m %H:%M}] {a.item.nome} -> "
                        f"{moeda(a.promo.preco)} ({a.motivo})"
                    )
                    print(f"   {a.promo.produto} | {a.promo.loja or '-'} | {canal}")
                    if a.promo.link:
                        print(f"   {a.promo.link}")
        print()
        print(f"=== {total} mensagens varridas, {achados} alertas ===")
        print("(sem deduplicacao: rodando de verdade, o numero seria menor)")

    try:
        with cliente:
            cliente.loop.run_until_complete(principal())
    except BaseException as exc:
        if not sessao_ocupada(exc):
            raise
        encerrar_cliente(cliente)
        print(
            "A sessao do Telegram esta em uso pelo monitor. Pare o monitor "
            "(main.py run) e rode este comando de novo.",
            file=sys.stderr,
        )
        return 2
    finally:
        db.fechar()
    return 0


def cmd_ultimo(args) -> int:
    """Acha a promocao mais recente de um item no historico e manda pelo bot.

    Serve para testar a notificacao de ponta a ponta com dados de verdade.
    """
    from monitor.listener import criar_cliente, nome_do_chat, resolver_canais
    from monitor.config import Item

    conf = carregar_conf(args)
    termo = " ".join(args.termo).strip()
    if termo:
        # prefere o item ja configurado (herda preco-alvo, piso e exclusoes)
        alvo = termo.lower()
        conhecidos = [
            i
            for i in conf.itens
            if alvo in i.nome.lower() or any(alvo in p.lower() for p in i.palavras_chave)
        ]
        conf.itens = conhecidos or [Item(nome=termo, palavras_chave=[termo])]
        print(f"Item: {conf.itens[0].nome}")

    pipeline, db = montar_pipeline(conf)
    cliente = criar_cliente(conf)

    async def principal():
        await cliente.start()
        melhor = {}
        for alvo in await resolver_canais(cliente, conf.canais):
            canal = nome_do_chat(alvo)
            async for msg in cliente.iter_messages(alvo, limit=args.limite):
                texto = msg.message or ""
                if len(texto) < 15:
                    continue
                for a in await pipeline.processar(
                    texto, canal=canal, mensagem_id=msg.id, notificar=False
                ):
                    atual = melhor.get(a.item.nome)
                    if atual is None or msg.date > atual[0]:
                        melhor[a.item.nome] = (msg.date, texto, canal, msg.id)

        if not melhor:
            print("Nenhuma promocao encontrada no historico para esse termo.")
            return

        for nome, (data, texto, canal, mensagem_id) in melhor.items():
            print(f"Enviando o mais recente de {nome} ({data:%d/%m %H:%M}, {canal})...")
            enviados = await pipeline.processar(
                texto,
                canal=canal,
                mensagem_id=mensagem_id,
                quando=data,
                notificar=True,
                forcar=True,
            )
            if not enviados:
                print("  (nao foi possivel enviar; veja o monitor.log)")

    try:
        with cliente:
            cliente.loop.run_until_complete(principal())
    except BaseException as exc:
        if not sessao_ocupada(exc):
            raise
        encerrar_cliente(cliente)
        print(
            "A sessao do Telegram esta em uso pelo monitor. Pare o monitor "
            "(main.py run) e rode este comando de novo.",
            file=sys.stderr,
        )
        return 2
    finally:
        db.fechar()
    return 0


def cmd_resumo(args) -> int:
    """Manda agora o resumo com o ultimo preco de cada item."""
    from datetime import datetime, timezone

    from monitor.listener import ARQUIVO_PEDIDO_RESUMO, criar_cliente, resolver_canais
    from monitor.resumo import montar_texto, ultimos_por_item

    conf = carregar_conf(args)
    pipeline, db = montar_pipeline(conf, notificar=False)
    cliente = criar_cliente(conf)

    async def principal():
        await cliente.start()
        alvos = await resolver_canais(cliente, conf.canais)
        achados = await ultimos_por_item(cliente, alvos, conf, pipeline, args.limite)
        texto = montar_texto(achados, conf, datetime.now(timezone.utc).astimezone())
        enviado = Notificador(conf.notificacao).enviar_texto(texto)
        print(f"{len(achados)} de {len(conf.itens)} itens com preco recente.")
        print("Resumo enviado pelo bot." if enviado else "Bot nao configurado; nada enviado.")

    try:
        with cliente:
            cliente.loop.run_until_complete(principal())
    except BaseException as exc:
        if not sessao_ocupada(exc):
            raise
        encerrar_cliente(cliente)
        pedido = Path(args.config).resolve().parent / ARQUIVO_PEDIDO_RESUMO
        pedido.write_text("", encoding="utf-8")
        print("O monitor esta rodando e e o dono da sessao do Telegram,")
        print("entao quem manda o resumo e ele. Pedido registrado:")
        print("a mensagem chega em alguns segundos (veja o monitor.log).")
    finally:
        db.fechar()
    return 0


def cmd_simular_fim(args) -> int:
    """Processa um texto como se fosse o aviso de fim vindo do canal.

    Usado para testar o fluxo de encerramento sem esperar o cupom acabar.
    """
    import asyncio as _asyncio

    conf = carregar_conf(args, exigir_telegram=False)
    pipeline, db = montar_pipeline(conf)
    texto = " ".join(args.texto)
    try:
        encerrados = _asyncio.run(
            pipeline.verificar_fim_por_mensagem(texto, canal=args.canal, responde_a=args.responde_a)
        )
        print(f"{encerrados} alerta(s) encerrado(s).")
        if not encerrados:
            print("Nada casou. O texto precisa ter marcador de fim (ESGOTADO, acabou, X)")
            print("e citar o cupom guardado, o nome do item, ou responder a mensagem.")
    finally:
        db.fechar()
    return 0


def cmd_historico(args) -> int:
    conf = carregar_conf(args, exigir_telegram=False)
    db = Armazenamento(RAIZ / conf.banco, conf.janela_dedup_horas)
    try:
        linhas = db.ultimos(args.limite)
    finally:
        db.fechar()
    if not linhas:
        print("Nenhum alerta registrado ainda.")
        return 0
    print(f"\nUltimos {len(linhas)} alertas:\n")
    for criado, item, produto, preco, canal, link in linhas:
        print(f"[{criado[:16].replace('T', ' ')}] {item} - {moeda(preco)} ({canal})")
        if produto:
            print(f"    {produto}")
        if link:
            print(f"    {link}")
    return 0


def cmd_doctor(args) -> int:
    conf = carregar_conf(args, exigir_telegram=False)
    ok = True
    print(f"Monitor de Promocoes v{__version__}\n")
    print(f"config .........: {args.config}")
    print(f"itens ..........: {len(conf.itens)} -> {', '.join(i.nome for i in conf.itens[:5])}")
    print(f"canais .........: {len(conf.canais) or 'TODOS (nenhum filtro definido)'}")
    if conf.telegram.api_id and conf.telegram.api_hash:
        print(f"credenciais API : api_id {conf.telegram.api_id}, hash {'*' * 8} (ok)")
    else:
        ok = False
        print("credenciais API : AUSENTES -> preencha telegram.api_id/api_hash "
              "(https://my.telegram.org)")

    sessao = RAIZ / f"{conf.telegram.sessao}.session"
    if sessao.exists():
        print(f"sessao .........: {sessao.name} (ok)")
    else:
        ok = False
        print("sessao .........: ausente -> rode 'python main.py login'")

    notificador = Notificador(conf.notificacao)
    if notificador.ativo:
        enviado = notificador.enviar_texto("✅ Monitor de promocoes: teste de notificacao.")
        print(f"bot notificacao : {'mensagem de teste enviada' if enviado else 'FALHOU'}")
        ok = ok and enviado
    else:
        print("bot notificacao : nao configurado (alertas so no console)")

    if conf.llm.habilitado:
        from monitor import llm

        disp = llm.disponivel(conf.llm)
        print(f"llm local ......: {conf.llm.modelo} em {conf.llm.url} -> "
              f"{'ok' if disp else 'INDISPONIVEL (segue so com regex)'}")
    else:
        print("llm local ......: desligado (somente regex)")

    print("\nResultado:", "pronto para rodar" if ok else "revise os itens acima")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Monitor de promocoes em canais do Telegram.",
    )
    parser.add_argument("--config", default=str(RAIZ / "config.yaml"), help="caminho do config.yaml")
    parser.add_argument("-v", "--verboso", action="store_true", help="log em modo DEBUG")
    sub = parser.add_subparsers(dest="comando")

    sub.add_parser("run", help="inicia o monitoramento (padrao)")
    sub.add_parser("login", help="autentica a conta do Telegram")
    sub.add_parser("canais", help="lista canais/grupos assinados")
    sub.add_parser("doctor", help="valida configuracao e integracoes")

    p_testar = sub.add_parser("testar", help="testa extracao/match em um texto")
    p_testar.add_argument("texto", nargs="*", help="texto; vazio le da entrada padrao")

    p_varrer = sub.add_parser("varrer", help="testa a config no historico dos canais")
    p_varrer.add_argument("-n", "--limite", type=int, default=300,
                          help="mensagens por canal (padrao 300)")

    p_ultimo = sub.add_parser("ultimo", help="manda pelo bot a promocao mais recente de um item")
    p_ultimo.add_argument("termo", nargs="*", help="termo; vazio usa os itens do config")
    p_ultimo.add_argument("-n", "--limite", type=int, default=300)

    p_resumo = sub.add_parser("resumo", help="manda o ultimo preco de cada item pelo bot")
    p_resumo.add_argument("-n", "--limite", type=int, default=120)

    p_fim = sub.add_parser("simular-fim", help="testa o aviso de promocao encerrada")
    p_fim.add_argument("texto", nargs="+", help="texto do aviso, como o canal mandaria")
    p_fim.add_argument("--canal", default="teste", help="canal do alerta a encerrar")
    p_fim.add_argument("--responde-a", type=int, default=None, dest="responde_a")

    p_hist = sub.add_parser("historico", help="ultimos alertas enviados")
    p_hist.add_argument("-n", "--limite", type=int, default=20)

    args = parser.parse_args(argv)
    configurar_log(args.verboso)

    comandos = {
        "run": cmd_run,
        "login": cmd_login,
        "canais": cmd_canais,
        "testar": cmd_testar,
        "varrer": cmd_varrer,
        "ultimo": cmd_ultimo,
        "resumo": cmd_resumo,
        "simular-fim": cmd_simular_fim,
        "doctor": cmd_doctor,
        "historico": cmd_historico,
    }
    return comandos[args.comando or "run"](args)


if __name__ == "__main__":
    raise SystemExit(main())
