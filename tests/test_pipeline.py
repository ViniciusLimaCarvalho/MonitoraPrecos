"""Testes de deduplicacao e do fluxo completo (sem Telegram)."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from monitor.armazenamento import Armazenamento
from monitor.config import Config, Item, LLMConf, NotificacaoConf, TelegramConf
from monitor.notificador import Notificador
from monitor.pipeline import Pipeline

MENSAGEM = "RTX 4070 Super 12GB\nDe R$ 4.299,00 por R$ 3.399,90\nhttps://kabum.com.br/p/1"


class NotificadorFake(Notificador):
    def __init__(self):
        super().__init__(NotificacaoConf(console=False))
        self.enviados = []

    def alertar(self, alerta, canal, quando):
        self.enviados.append((alerta.item.nome, alerta.promo.preco))
        return True


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conf = Config(
            telegram=TelegramConf(api_id=1, api_hash="x"),
            notificacao=NotificacaoConf(console=False),
            llm=LLMConf(),
            canais=[],
            itens=[Item("RTX 4070", ["rtx 4070"], preco_alvo=3500.0)],
        )
        self.db = Armazenamento(Path(self.tmp.name) / "t.db")
        self.notificador = NotificadorFake()
        self.pipeline = Pipeline(self.conf, self.db, self.notificador, usar_llm=False)

    def tearDown(self):
        self.db.fechar()
        self.tmp.cleanup()

    def processar(self, texto, mensagem_id):
        return asyncio.run(self.pipeline.processar(texto, canal="canal-teste", mensagem_id=mensagem_id))

    def test_alerta_e_disparado_uma_vez(self):
        self.assertEqual(len(self.processar(MENSAGEM, 1)), 1)
        # repost em outra mensagem: mesmo produto e mesmo preco -> nao repete
        self.assertEqual(self.processar(MENSAGEM, 2), [])
        self.assertEqual(len(self.notificador.enviados), 1)

    def test_mesma_mensagem_nao_reprocessa(self):
        self.processar(MENSAGEM, 10)
        self.assertEqual(self.processar(MENSAGEM, 10), [])

    def test_preco_menor_gera_novo_alerta(self):
        self.processar(MENSAGEM, 1)
        mais_barato = MENSAGEM.replace("3.399,90", "2.999,00")
        self.assertEqual(len(self.processar(mais_barato, 2)), 1)
        self.assertEqual(self.notificador.enviados[-1], ("RTX 4070", 2999.0))

    def test_acima_do_alvo_nao_alerta(self):
        caro = MENSAGEM.replace("3.399,90", "3.999,00")
        self.assertEqual(self.processar(caro, 1), [])
        self.assertEqual(self.notificador.enviados, [])

    def test_mensagem_irrelevante(self):
        self.assertEqual(self.processar("Bom dia, grupo!", 1), [])


if __name__ == "__main__":
    unittest.main()


class TestMigracao(unittest.TestCase):
    """Banco criado por versao anterior precisa abrir sem erro."""

    def test_banco_antigo_ganha_colunas_novas(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "antigo.db"
            antigo = sqlite3.connect(caminho)
            antigo.execute(
                "CREATE TABLE alertas (chave TEXT PRIMARY KEY, item TEXT NOT NULL, "
                "produto TEXT, preco REAL, canal TEXT, mensagem_id INTEGER, "
                "link TEXT, criado_em TEXT NOT NULL)"
            )
            antigo.execute(
                "INSERT INTO alertas VALUES ('k','item','p',10.0,'c',1,'l','2026-01-01T00:00:00')"
            )
            antigo.commit()
            antigo.close()

            db = Armazenamento(caminho)
            colunas = {c["name"] for c in db.conexao.execute("PRAGMA table_info(alertas)")}
            self.assertTrue({"cupom", "notificacao_id", "expirado_em", "texto_alerta"} <= colunas)
            self.assertEqual(len(db.ultimos()), 1)  # dado antigo preservado
            db.fechar()


class TestEnvioForcado(TestPipeline):
    """O comando manual 'ultimo' precisa furar as travas de duplicata."""

    def forcar(self, texto, mensagem_id):
        return asyncio.run(
            self.pipeline.processar(
                texto, canal="canal-teste", mensagem_id=mensagem_id, forcar=True
            )
        )

    def test_forcar_reenvia_mesma_promocao(self):
        self.assertEqual(len(self.processar(MENSAGEM, 1)), 1)
        self.assertEqual(self.processar(MENSAGEM, 1), [])  # trava normal
        self.assertEqual(len(self.forcar(MENSAGEM, 1)), 1)  # manual passa
        self.assertEqual(len(self.notificador.enviados), 2)

    def test_outro_item_na_mensagem_ja_vista(self):
        """Caso real: combo citava 2 jogos; o 2o item ficava bloqueado."""
        self.conf.itens = [
            Item("Nintendo Switch 2", ["switch 2"], preco_alvo=3500.0),
            Item("Mario Kart World", ["mario kart world"]),
        ]
        combo = "Nintendo Switch 2 + Mario Kart World por R$ 3.400"
        self.assertEqual(len(self.processar(combo, 7)), 2)  # os dois de uma vez
        self.assertEqual(len(self.forcar(combo, 7)), 2)
