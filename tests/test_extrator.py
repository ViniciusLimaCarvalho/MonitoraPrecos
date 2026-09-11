"""Testes do extrator (rodar com: python -m unittest discover -s tests)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from monitor.extrator import extrair, extrair_loja, parse_preco


class TestParsePreco(unittest.TestCase):
    def test_formatos_brasileiros(self):
        self.assertEqual(parse_preco("1.234,56"), 1234.56)
        self.assertEqual(parse_preco("99,90"), 99.90)
        self.assertEqual(parse_preco("1.999"), 1999.0)
        self.assertEqual(parse_preco("3499"), 3499.0)
        self.assertEqual(parse_preco("19.99"), 19.99)

    def test_invalidos(self):
        self.assertIsNone(parse_preco(""))
        self.assertIsNone(parse_preco("abc"))
        self.assertIsNone(parse_preco("0"))


class TestExtrair(unittest.TestCase):
    def test_de_por_com_cupom(self):
        p = extrair(
            "PLACA DE VIDEO RTX 4070 SUPER\n"
            "De R$ 4.299,00 por R$ 3.399,90 no PIX\n"
            "Cupom: TECH50\nhttps://www.kabum.com.br/produto/123"
        )
        self.assertEqual(p.preco, 3399.90)
        self.assertEqual(p.preco_original, 4299.00)
        self.assertEqual(p.cupom, "TECH50")
        self.assertEqual(p.loja, "KaBuM!")
        self.assertIn("RTX 4070", p.produto)
        self.assertTrue(p.completa)

    def test_ignora_valor_da_parcela(self):
        p = extrair("Fone JBL 510BT\n12x de R$ 24,90 (R$ 249,00 a vista)")
        self.assertEqual(p.preco, 249.00)

    def test_so_parcelado_usa_o_total(self):
        p = extrair("Notebook Ideapad\n10x de R$ 250,00 sem juros")
        self.assertEqual(p.preco, 2500.00)

    def test_mensagem_sem_preco(self):
        p = extrair("Promocao relampago, corre! https://loja.com/x")
        self.assertIsNone(p.preco)
        self.assertFalse(p.completa)


class TestLoja(unittest.TestCase):
    """Formato real dos canais monitorados: "Produto via Loja"."""

    def test_nome_da_loja_com_espaco(self):
        self.assertEqual(extrair_loja("Camera via Mercado Livre", None), "Mercado Livre")
        self.assertEqual(extrair_loja("Cadeira na Casas Bahia", None), "Casas Bahia")

    def test_link_encurtado(self):
        self.assertEqual(extrair_loja("Tenis", "https://meli.la/1abc"), "Mercado Livre")
        self.assertEqual(extrair_loja("Livro", "https://amzn.to/xyz"), "Amazon")

    def test_marca_nao_vence_a_loja(self):
        self.assertEqual(
            extrair_loja("Monitor Gamer Samsung 24 via Mercado Livre", None), "Mercado Livre"
        )


if __name__ == "__main__":
    unittest.main()
