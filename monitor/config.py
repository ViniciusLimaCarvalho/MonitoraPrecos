"""Leitura e validação do config.yaml (+ variáveis de ambiente e .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(Exception):
    """Erro de configuração legível para o usuário."""


@dataclass
class Item:
    nome: str
    palavras_chave: list[str]
    preco_alvo: float | None = None
    preco_minimo: float | None = None
    categoria: str | None = None
    excluir: list[str] = field(default_factory=list)


@dataclass
class TelegramConf:
    api_id: int
    api_hash: str
    sessao: str = "sessao_monitor"


@dataclass
class NotificacaoConf:
    bot_token: str = ""
    chat_id: str = ""
    console: bool = True
    timeout: int = 45      # api.telegram.org fica lento em algumas redes
    tentativas: int = 3
    resumo_ao_iniciar: bool = True
    resumo_mensagens: int = 120


@dataclass
class LLMConf:
    habilitado: bool = False
    url: str = "http://localhost:11434"
    modelo: str = "qwen2.5:7b"
    timeout: int = 30


@dataclass
class Config:
    telegram: TelegramConf
    notificacao: NotificacaoConf
    llm: LLMConf
    canais: list[str]
    itens: list[Item]
    banco: str = "dados/alertas.db"
    janela_dedup_horas: int = 72
    janela_fim_horas: int = 72
    alertar_sem_preco: bool = True
    log_mensagens: bool = False


def carregar_dotenv(caminho: Path) -> None:
    """Carrega um .env simples (CHAVE=valor) sem sobrescrever o ambiente real."""
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        os.environ.setdefault(chave.strip(), valor.strip().strip("'\""))


def _env(nome: str, atual):
    """Variável de ambiente tem prioridade sobre o valor do YAML."""
    valor = os.environ.get(nome)
    return valor if valor not in (None, "") else atual


def _preco(valor, contexto: str) -> float | None:
    if valor in (None, "", "null"):
        return None
    try:
        return float(str(valor).replace(",", "."))
    except ValueError as exc:
        raise ConfigError(f"preço inválido em {contexto}: {valor!r}") from exc


def carregar(caminho: str | Path = "config.yaml", exigir_telegram: bool = True) -> Config:
    caminho = Path(caminho)
    if not caminho.exists():
        raise ConfigError(
            f"arquivo {caminho} não encontrado. "
            "Copie config.example.yaml para config.yaml e preencha seus dados."
        )

    carregar_dotenv(caminho.parent / ".env")

    dados = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    if not isinstance(dados, dict):
        raise ConfigError(f"{caminho} não contém um mapeamento YAML válido.")

    bruto_tg = dados.get("telegram") or {}
    api_id = _env("TELEGRAM_API_ID", bruto_tg.get("api_id"))
    api_hash = _env("TELEGRAM_API_HASH", bruto_tg.get("api_hash"))
    if not api_id or not api_hash:
        if exigir_telegram:
            raise ConfigError(
                "telegram.api_id e telegram.api_hash são obrigatórios "
                "(ou as variáveis TELEGRAM_API_ID / TELEGRAM_API_HASH). "
                "Obtenha os dois gratuitamente em https://my.telegram.org > "
                "API development tools."
            )
        # comandos offline (testar/historico/doctor) rodam sem credenciais
        api_id, api_hash = 0, ""
    try:
        api_id = int(api_id)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"telegram.api_id deve ser um número inteiro, recebi {api_id!r}") from exc

    telegram = TelegramConf(
        api_id=api_id,
        api_hash=str(api_hash),
        sessao=str(bruto_tg.get("sessao") or "sessao_monitor"),
    )

    bruto_nt = dados.get("notificacao") or {}
    notificacao = NotificacaoConf(
        bot_token=str(_env("TELEGRAM_BOT_TOKEN", bruto_nt.get("bot_token")) or ""),
        chat_id=str(_env("TELEGRAM_CHAT_ID", bruto_nt.get("chat_id")) or ""),
        console=bool(bruto_nt.get("console", True)),
        timeout=int(bruto_nt.get("timeout") or 45),
        tentativas=int(bruto_nt.get("tentativas") or 3),
        resumo_ao_iniciar=bool(bruto_nt.get("resumo_ao_iniciar", True)),
        resumo_mensagens=int(bruto_nt.get("resumo_mensagens") or 120),
    )

    bruto_llm = dados.get("llm") or {}
    llm = LLMConf(
        habilitado=bool(bruto_llm.get("habilitado", False)),
        url=str(bruto_llm.get("url") or "http://localhost:11434").rstrip("/"),
        modelo=str(bruto_llm.get("modelo") or "qwen2.5:7b"),
        timeout=int(bruto_llm.get("timeout") or 30),
    )

    canais = [str(c).strip() for c in (dados.get("canais") or []) if str(c).strip()]

    itens: list[Item] = []
    for i, bruto in enumerate(dados.get("itens") or [], start=1):
        if not isinstance(bruto, dict):
            raise ConfigError(f"itens[{i}] deve ser um mapeamento com 'nome' e 'palavras_chave'.")
        nome = str(bruto.get("nome") or "").strip()
        if not nome:
            raise ConfigError(f"itens[{i}] está sem 'nome'.")
        palavras = [str(p).strip() for p in (bruto.get("palavras_chave") or []) if str(p).strip()]
        if not palavras:
            palavras = [nome]
        itens.append(
            Item(
                nome=nome,
                palavras_chave=palavras,
                preco_alvo=_preco(bruto.get("preco_alvo"), f"itens[{i}] ({nome})"),
                preco_minimo=_preco(bruto.get("preco_minimo"), f"itens[{i}] ({nome})"),
                categoria=(str(bruto["categoria"]) if bruto.get("categoria") else None),
                excluir=[str(p).strip() for p in (bruto.get("excluir") or []) if str(p).strip()],
            )
        )

    if not itens:
        raise ConfigError("nenhum item em 'itens'. Adicione ao menos um produto para monitorar.")

    return Config(
        telegram=telegram,
        notificacao=notificacao,
        llm=llm,
        canais=canais,
        itens=itens,
        banco=str(dados.get("banco") or "dados/alertas.db"),
        janela_dedup_horas=int(dados.get("janela_dedup_horas", 72)),
        janela_fim_horas=int(dados.get("janela_fim_horas", 72)),
        alertar_sem_preco=bool(dados.get("alertar_sem_preco", True)),
        log_mensagens=bool(dados.get("log_mensagens", False)),
    )
