"""Testes da recarga automatica do config.yaml."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from monitor.armazenamento import Armazenamento
from monitor.config import carregar
from monitor.listener import vigiar_config
from monitor.notificador import Notificador
from monitor.config import NotificacaoConf
from monitor.pipeline import Pipeline

BASE = """
telegram:
  api_id: 1
  api_hash: "x"
canais:
  - "@um"
itens:
  - nome: "Item A"
    palavras_chave: ["item a"]
"""

COM_ITEM_NOVO = BASE + """  - nome: "Item B"
    palavras_chave: ["item b"]
    preco_alvo: 100.0
"""


class TestRecarga(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.caminho = Path(self.tmp.name) / "config.yaml"
        self.caminho.write_text(BASE, encoding="utf-8")
        self.conf = carregar(self.caminho)
        self.db = Armazenamento(Path(self.tmp.name) / "t.db")
        self.pipe = Pipeline(
            self.conf, self.db, Notificador(NotificacaoConf(), avisar=False), usar_llm=False
        )

    def tearDown(self):
        self.db.fechar()
        self.tmp.cleanup()

    def executar(self, novo_conteudo, ciclos=6):
        async def cenario():
            tarefa = asyncio.create_task(
                vigiar_config(self.caminho, self.conf, self.pipe, intervalo=0.05)
            )
            await asyncio.sleep(0.1)
            if novo_conteudo is not None:
                self.caminho.write_text(novo_conteudo, encoding="utf-8")
            await asyncio.sleep(0.05 * ciclos)
            tarefa.cancel()

        asyncio.run(cenario())

    def test_item_novo_entra_sem_reiniciar(self):
        self.assertEqual(len(self.conf.itens), 1)
        self.executar(COM_ITEM_NOVO)
        self.assertEqual([i.nome for i in self.conf.itens], ["Item A", "Item B"])
        self.assertEqual(self.conf.itens[1].preco_alvo, 100.0)

    def test_config_quebrado_mantem_o_anterior(self):
        self.executar("itens: [[[ isso nao e yaml valido")
        self.assertEqual([i.nome for i in self.conf.itens], ["Item A"])

    def test_config_sem_itens_mantem_o_anterior(self):
        # carregar() rejeita lista vazia; a vigia nao pode zerar o que funciona
        self.executar(BASE.replace('  - nome: "Item A"', "").replace(
            '    palavras_chave: ["item a"]', ""))
        self.assertEqual([i.nome for i in self.conf.itens], ["Item A"])

    def test_sem_mudanca_nao_recarrega(self):
        antes = self.conf.itens
        self.executar(None)
        self.assertIs(self.conf.itens, antes)


if __name__ == "__main__":
    unittest.main()
