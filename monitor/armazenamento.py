"""SQLite simples para evitar alertas duplicados e guardar o historico."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .texto import normalizar

_TABELAS = """
CREATE TABLE IF NOT EXISTS alertas (
    chave      TEXT PRIMARY KEY,
    item       TEXT NOT NULL,
    produto    TEXT,
    preco      REAL,
    canal      TEXT,
    mensagem_id INTEGER,
    link       TEXT,
    criado_em  TEXT NOT NULL,
    cupom      TEXT,
    notificacao_id INTEGER,
    expirado_em TEXT,
    motivo_fim TEXT,
    texto_alerta TEXT
);
CREATE TABLE IF NOT EXISTS mensagens_vistas (
    canal       TEXT NOT NULL,
    mensagem_id INTEGER NOT NULL,
    visto_em    TEXT NOT NULL,
    PRIMARY KEY (canal, mensagem_id)
);
"""

# Criados depois da migracao: um indice pode citar coluna que ainda nao existe
# em bancos de versoes anteriores.
_INDICES = """
CREATE INDEX IF NOT EXISTS idx_alertas_item ON alertas(item, criado_em);
CREATE INDEX IF NOT EXISTS idx_alertas_origem ON alertas(canal, mensagem_id);
CREATE INDEX IF NOT EXISTS idx_alertas_cupom ON alertas(cupom);
"""


def _agora() -> datetime:
    return datetime.now(timezone.utc)


class Armazenamento:
    def __init__(self, caminho: str | Path, janela_horas: int = 72) -> None:
        caminho = Path(caminho)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        self.conexao = sqlite3.connect(caminho)
        self.conexao.row_factory = sqlite3.Row
        self.conexao.executescript(_TABELAS)
        self._migrar()
        self.conexao.executescript(_INDICES)
        self.conexao.commit()
        self.janela_horas = janela_horas

    def _migrar(self) -> None:
        """Adiciona colunas novas em bancos criados por versoes anteriores."""
        existentes = {c["name"] for c in self.conexao.execute("PRAGMA table_info(alertas)")}
        for coluna, tipo in (
            ("cupom", "TEXT"),
            ("notificacao_id", "INTEGER"),
            ("expirado_em", "TEXT"),
            ("motivo_fim", "TEXT"),
            ("texto_alerta", "TEXT"),
        ):
            if coluna not in existentes:
                self.conexao.execute(f"ALTER TABLE alertas ADD COLUMN {coluna} {tipo}")

    def fechar(self) -> None:
        self.conexao.close()

    @staticmethod
    def chave(item: str, produto: str | None, preco: float | None, texto: str) -> str:
        """Mesma promocao = mesmo item + produto + preco.

        O preco entra na chave de proposito: se o mesmo produto voltar mais
        barato, isso e uma promocao nova e merece um novo alerta.
        """
        base = "|".join(
            [
                normalizar(item),
                normalizar(produto or "")[:80] or normalizar(texto)[:80],
                f"{preco:.2f}" if preco is not None else "?",
            ]
        )
        return hashlib.sha256(base.encode("utf-8")).hexdigest()

    def ja_alertado(self, chave: str) -> bool:
        limite = (_agora() - timedelta(hours=self.janela_horas)).isoformat()
        cur = self.conexao.execute(
            "SELECT 1 FROM alertas WHERE chave = ? AND criado_em >= ?", (chave, limite)
        )
        return cur.fetchone() is not None

    def registrar(
        self,
        chave: str,
        item: str,
        produto: str | None,
        preco: float | None,
        canal: str,
        mensagem_id: int | None,
        link: str | None,
        cupom: str | None = None,
        notificacao_id: int | None = None,
        texto_alerta: str | None = None,
    ) -> None:
        self.conexao.execute(
            "INSERT OR REPLACE INTO alertas "
            "(chave, item, produto, preco, canal, mensagem_id, link, criado_em, "
            " cupom, notificacao_id, texto_alerta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chave,
                item,
                produto,
                preco,
                canal,
                mensagem_id,
                link,
                _agora().isoformat(),
                (cupom or None),
                notificacao_id,
                texto_alerta,
            ),
        )
        self.conexao.commit()

    def mensagem_nova(self, canal: str, mensagem_id: int | None) -> bool:
        """Evita reprocessar a mesma mensagem (reconexao, edicao, repost)."""
        if mensagem_id is None:
            return True
        try:
            self.conexao.execute(
                "INSERT INTO mensagens_vistas (canal, mensagem_id, visto_em) VALUES (?, ?, ?)",
                (canal, mensagem_id, _agora().isoformat()),
            )
            self.conexao.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def ultimos(self, limite: int = 20) -> list[tuple]:
        cur = self.conexao.execute(
            "SELECT criado_em, item, produto, preco, canal, link "
            "FROM alertas ORDER BY criado_em DESC LIMIT ?",
            (limite,),
        )
        return cur.fetchall()

    def limpar_antigos(self, dias: int = 30) -> int:
        """Remove registros antigos para o banco nao crescer sem limite."""
        limite = (_agora() - timedelta(days=dias)).isoformat()
        cur = self.conexao.execute("DELETE FROM mensagens_vistas WHERE visto_em < ?", (limite,))
        self.conexao.commit()
        return cur.rowcount

    # ------------------------------------------------- promocoes ainda ativas

    def _ativos(self, sql: str, parametros: tuple, janela_horas: int) -> list[sqlite3.Row]:
        limite = (_agora() - timedelta(hours=janela_horas)).isoformat()
        return self.conexao.execute(
            "SELECT * FROM alertas WHERE expirado_em IS NULL AND criado_em >= ? " + sql,
            (limite, *parametros),
        ).fetchall()

    def ativos_por_mensagem(self, canal: str, mensagem_id: int, janela_horas: int = 168):
        """Alertas gerados por uma mensagem especifica (para edicao/remocao)."""
        return self._ativos("AND canal = ? AND mensagem_id = ?", (canal, mensagem_id), janela_horas)

    def ativos_por_cupom(self, cupons: set[str], janela_horas: int = 168):
        """Alertas cujo cupom foi citado em uma mensagem de encerramento."""
        if not cupons:
            return []
        marcadores = ",".join("?" * len(cupons))
        return self._ativos(
            f"AND cupom IS NOT NULL AND UPPER(cupom) IN ({marcadores})",
            tuple(sorted(cupons)),
            janela_horas,
        )

    def ativos_do_canal(self, canal: str, janela_horas: int = 168):
        return self._ativos("AND canal = ?", (canal,), janela_horas)

    def marcar_expirado(self, chave: str, motivo: str) -> None:
        self.conexao.execute(
            "UPDATE alertas SET expirado_em = ?, motivo_fim = ? WHERE chave = ?",
            (_agora().isoformat(), motivo, chave),
        )
        self.conexao.commit()
