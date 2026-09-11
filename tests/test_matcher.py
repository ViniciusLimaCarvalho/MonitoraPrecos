"""Testes do matcher e das regras de preco-alvo."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from monitor.config import Config, Item, LLMConf, NotificacaoConf, TelegramConf
from monitor.extrator import extrair
from monitor.matcher import avaliar


def conf_com(*itens: Item, alertar_sem_preco: bool = True) -> Config:
    return Config(
        telegram=TelegramConf(api_id=1, api_hash="x"),
        notificacao=NotificacaoConf(),
        llm=LLMConf(),
        canais=[],
        itens=list(itens),
        alertar_sem_preco=alertar_sem_preco,
    )


class TestMatcher(unittest.TestCase):
    def test_casa_variacao_sem_espaco(self):
        conf = conf_com(Item("RTX 4070", ["rtx 4070"]))
        alertas = avaliar(extrair("Placa RTX4070 Super por R$ 3.100,00"), conf)
        self.assertEqual(len(alertas), 1)
        self.assertEqual(alertas[0].motivo, "sem preco alvo")

    def test_respeita_preco_alvo(self):
        conf = conf_com(Item("RTX 4070", ["rtx 4070"], preco_alvo=3000.0))
        self.assertEqual(avaliar(extrair("RTX 4070 por R$ 3.399,90"), conf), [])
        alertas = avaliar(extrair("RTX 4070 por R$ 2.899,00"), conf)
        self.assertEqual(alertas[0].motivo, "abaixo do alvo")

    def test_compacto_nao_atravessa_palavras(self):
        """Caso real: 'ocarina' casava dentro de 'feminino Carina Lux'."""
        conf = conf_com(Item("Zelda Ocarina of Time", ["ocarina of time", "ocarina"]))
        tenis = extrair("Tenis Puma Feminino Carina Lux via Mercado Livre por R$ 188")
        self.assertEqual(avaliar(tenis, conf), [])
        jogo = extrair("The Legend of Zelda Ocarina of Time por R$ 188")
        self.assertEqual(len(avaliar(jogo, conf)), 1)

    def test_palavra_de_exclusao(self):
        conf = conf_com(Item("RTX 4070", ["rtx 4070"], excluir=["suporte"]))
        self.assertEqual(avaliar(extrair("Suporte para RTX 4070 R$ 89,90"), conf), [])

    def test_sem_preco_respeita_configuracao(self):
        item = Item("Air Fryer", ["air fryer"], preco_alvo=400.0)
        texto = "Air fryer em oferta relampago, corre!"
        self.assertEqual(len(avaliar(extrair(texto), conf_com(item))), 1)
        self.assertEqual(avaliar(extrair(texto), conf_com(item, alertar_sem_preco=False)), [])

    def test_item_sem_alvo_tambem_ignora_noticia_sem_preco(self):
        item = Item("Ocarina of Time", ["ocarina"])  # sem preco_alvo
        noticia = "Eis o novo Ocarina of Time! Venha assistir a Zelda Direct"
        self.assertEqual(len(avaliar(extrair(noticia), conf_com(item))), 1)
        self.assertEqual(avaliar(extrair(noticia), conf_com(item, alertar_sem_preco=False)), [])

    def test_preco_minimo_separa_console_de_jogo(self):
        conf = conf_com(
            Item("Nintendo Switch 2", ["switch 2"], preco_alvo=3500.0, preco_minimo=1800.0)
        )
        # o console em si
        self.assertEqual(len(avaliar(extrair("Nintendo Switch 2 por R$ 3.299"), conf)), 1)
        # jogo que cita o console no titulo nao pode disparar o alerta do console
        self.assertEqual(avaliar(extrair("Mario Kart World - Switch 2 - R$ 349"), conf), [])

    def test_nao_casa_item_ausente(self):
        conf = conf_com(Item("SSD 1TB", ["ssd 1tb"]))
        self.assertEqual(avaliar(extrair("Teclado mecanico por R$ 199,00"), conf), [])


if __name__ == "__main__":
    unittest.main()
