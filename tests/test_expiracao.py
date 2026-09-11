"""Testes do aviso de promocao/cupom encerrado."""

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

CANAL = "Sucumba Promo"
PROMO = (
    "Nintendo Switch 2 (Nacional) via Mercado Livre\n"
    "De R$ 3.999 por R$ 3.375 no PIX\n"
    "Cupom `BOLSOCHEIO`\n"
    "https://meli.la/13sTn3c"
)


class NotificadorFake(Notificador):
    """Registra o que teria ido para o Telegram."""

    def __init__(self):
        super().__init__(NotificacaoConf(bot_token="t", chat_id="1", console=False))
        self.enviados: list[str] = []
        self.editados: list[tuple[int, str]] = []
        self.proximo_id = 100

    def enviar_texto(self, texto, responder_a=None):
        self.enviados.append(texto)
        self.proximo_id += 1
        return self.proximo_id

    def editar_texto(self, notificacao_id, texto):
        self.editados.append((notificacao_id, texto))
        return True


class TestFimDaPromocao(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conf = Config(
            telegram=TelegramConf(api_id=1, api_hash="x"),
            notificacao=NotificacaoConf(console=False),
            llm=LLMConf(),
            canais=[],
            itens=[Item("Nintendo Switch 2", ["switch 2"], preco_alvo=3500.0)],
        )
        self.db = Armazenamento(Path(self.tmp.name) / "t.db")
        self.nt = NotificadorFake()
        self.pipe = Pipeline(self.conf, self.db, self.nt, usar_llm=False)
        # alerta inicial: e ele que sera encerrado nos testes
        self.alertas = self.rodar(self.pipe.processar(PROMO, canal=CANAL, mensagem_id=500))
        self.assertEqual(len(self.alertas), 1)
        self.nt.enviados.clear()

    def tearDown(self):
        self.db.fechar()
        self.tmp.cleanup()

    @staticmethod
    def rodar(corrotina):
        return asyncio.run(corrotina)

    def registro(self):
        linhas = self.db.conexao.execute("SELECT * FROM alertas").fetchall()
        self.assertEqual(len(linhas), 1)
        return linhas[0]

    def test_cupom_guardado_no_alerta(self):
        self.assertEqual(self.registro()["cupom"], "BOLSOCHEIO")
        self.assertIsNotNone(self.registro()["notificacao_id"])

    def test_aviso_do_canal_nomeando_o_cupom(self):
        n = self.rodar(
            self.pipe.verificar_fim_por_mensagem(
                "Cupom BOLSOCHEIO no Mercado Livre ESGOTADO", canal=CANAL
            )
        )
        self.assertEqual(n, 1)
        self.assertIsNotNone(self.registro()["expirado_em"])
        # avisou em mensagem nova E carimbou o alerta antigo
        self.assertEqual(len(self.nt.enviados), 1)
        self.assertIn("ACABOU", self.nt.enviados[0])
        self.assertEqual(len(self.nt.editados), 1)
        self.assertIn("ENCERRADA", self.nt.editados[0][1])

    def test_cupom_de_outra_promocao_nao_encerra(self):
        n = self.rodar(
            self.pipe.verificar_fim_por_mensagem("Cupom OUTROCUPOM ESGOTADO", canal=CANAL)
        )
        self.assertEqual(n, 0)
        self.assertIsNone(self.registro()["expirado_em"])

    def test_mensagem_de_cupom_ativo_nao_encerra(self):
        n = self.rodar(
            self.pipe.verificar_fim_por_mensagem(
                "Bora para 15% OFF no Mercado Livre usando BOLSOCHEIO", canal=CANAL
            )
        )
        self.assertEqual(n, 0)

    def test_resposta_a_mensagem_original(self):
        n = self.rodar(
            self.pipe.verificar_fim_por_mensagem("acabou pessoal", canal=CANAL, responde_a=500)
        )
        self.assertEqual(n, 1)

    def test_aviso_citando_o_item(self):
        n = self.rodar(
            self.pipe.verificar_fim_por_mensagem("Nintendo Switch 2 ESGOTADO", canal=CANAL)
        )
        self.assertEqual(n, 1)

    def test_edicao_com_preco_maior_encerra(self):
        editada = PROMO.replace("3.375", "3.899")
        n = self.rodar(self.pipe.verificar_fim_por_edicao(editada, canal=CANAL, mensagem_id=500))
        self.assertEqual(n, 1)
        self.assertIn("subiu", self.registro()["motivo_fim"])

    def test_edicao_sem_o_cupom_encerra(self):
        editada = PROMO.replace("Cupom `BOLSOCHEIO`\n", "")
        n = self.rodar(self.pipe.verificar_fim_por_edicao(editada, canal=CANAL, mensagem_id=500))
        self.assertEqual(n, 1)
        self.assertIn("sumiu", self.registro()["motivo_fim"])

    def test_edicao_de_rotina_nao_encerra(self):
        # o canal edita quase toda mensagem so para ajustar texto/link
        editada = PROMO + "\n\nCompre pelo link acima e ajude o canal!"
        n = self.rodar(self.pipe.verificar_fim_por_edicao(editada, canal=CANAL, mensagem_id=500))
        self.assertEqual(n, 0)
        self.assertIsNone(self.registro()["expirado_em"])
        self.assertEqual(self.nt.enviados, [])

    def test_remocao_da_mensagem_encerra(self):
        n = self.rodar(self.pipe.verificar_fim_por_remocao(CANAL, [500]))
        self.assertEqual(n, 1)
        self.assertIn("apagada", self.registro()["motivo_fim"])

    def test_nao_avisa_duas_vezes(self):
        self.rodar(self.pipe.verificar_fim_por_mensagem("Cupom BOLSOCHEIO ESGOTADO", canal=CANAL))
        self.nt.enviados.clear()
        n = self.rodar(
            self.pipe.verificar_fim_por_mensagem("Cupom BOLSOCHEIO ESGOTADO de novo", canal=CANAL)
        )
        self.assertEqual(n, 0)
        self.assertEqual(self.nt.enviados, [])


if __name__ == "__main__":
    unittest.main()
