from datetime import date

import pytest

from app.parser import extrair

HOJE = date(2026, 10, 19)


@pytest.mark.parametrize("texto, origem, destino, preco", [
    ("🔥 FORTALEZA ➜ LISBOA por R$ 2.389 ida e volta! TAP", "FOR", "LIS", 2389),
    ("GRU → MIA a partir de R$1.899,90 com a LATAM", "GRU", "MIA", 1899.90),
    ("Promo de Natal: voos para Orlando saindo de Recife por 2.950 reais", "REC", "MCO", 2950),
    ("Para Miami, de São Paulo: R$ 2.100 em julho", "SAO", "MIA", 2100),
    ("Voos de Natal para Lisboa R$ 3.200", "NAT", "LIS", 3200),
    ("Passagens para Paris saindo de São Paulo por R$ 3 mil", "SAO", "PAR", 3000),
    ("FOR x GRU R$ 389 o trecho", "FOR", "GRU", 389),
])
def test_trecho_e_preco(texto, origem, destino, preco):
    p = extrair(texto, HOJE)
    assert (p.origem, p.destino) == (origem, destino)
    assert p.preco == pytest.approx(preco)


def test_milhas_e_datas_especificas():
    p = extrair("Smiles: Fortaleza x Natal por 6.500 milhas, datas 10/02 a 20/02", HOJE)
    assert p.milhas == 6500 and p.preco is None
    assert (p.data_inicio, p.data_fim) == (date(2027, 2, 10), date(2027, 2, 20))


def test_meses_viram_periodo_e_prazo_de_compra_e_ignorado():
    p = extrair("GRU → MIA R$ 1.899, saídas em novembro e dezembro. Compre até 25/10!", HOJE)
    assert (p.data_inicio, p.data_fim) == (date(2026, 11, 1), date(2026, 12, 31))


def test_mes_com_ano_e_link_nao_vira_cidade():
    p = extrair("https://site.com/promo-lisboa Fortaleza para Madri R$ 2.900 em 03/2027", HOJE)
    assert (p.origem, p.destino) == ("FOR", "MAD")
    assert (p.data_inicio, p.data_fim) == (date(2027, 3, 1), date(2027, 3, 31))
    assert p.link == "https://site.com/promo-lisboa"


def test_palavras_comuns_nao_viram_cidade_nem_data():
    p = extrair("Hotel com vista para o mar em Salvador, R$ 300 a diária", HOJE)
    assert p.destino is None and p.data_inicio is None
    assert not p.completa


def test_confianca():
    completa = extrair("FOR → LIS R$ 2.389 em março/27 com TAP https://x.com/a", HOJE)
    incompleta = extrair("Promoção imperdível para Lisboa!", HOJE)
    assert completa.confianca == 1.0
    assert incompleta.confianca < 0.5


ALERTA_VALE_VOOS = """✅ Azul Fidelidade
📍 Origem: {origem}
📍 Destino: {destino}
✈️ Companhia Aérea: Azul
📆 Data da Cotação: 17/10/2026
💺 Classe: Econômica

👉🏻 Trecho ida e volta 24.000 milhas.

Data de Ida:

Março 2027: 21, 22, 23

Data de Volta:

Abril 2027: 09, 10"""


@pytest.mark.parametrize("origem_txt, destino_txt, origem, destino", [
    ("NATAL", "RECIFE", "NAT", "REC"),
    ("SÃO PAULO (GRU)", "LISBOA (LIS)", "GRU", "LIS"),
    ("RIO DE JANEIRO", "PORTO", "RIO", "OPO"),
])
def test_alerta_com_campos_rotulados(origem_txt, destino_txt, origem, destino):
    p = extrair(ALERTA_VALE_VOOS.format(origem=origem_txt, destino=destino_txt), HOJE)
    assert (p.origem, p.destino) == (origem, destino)
    assert p.milhas == 24000 and p.companhia == "Azul"
    # data da cotação é ignorada; ida 21/03 e volta 10/04
    assert (p.data_inicio, p.data_fim) == (date(2027, 3, 21), date(2027, 4, 10))



@pytest.mark.parametrize("texto, tipo", [
    ("👉🏻 Trecho ida e volta 24.000 milhas.", "ida_volta"),
    ("GRU → MIA R$ 1.899 ida/volta", "ida_volta"),
    ("FOR-LIS R$ 2.389 i/v", "ida_volta"),
    ("Data de Ida: março\nData de Volta: abril", "ida_volta"),
    ("Fortaleza x Natal por 6.500 milhas o trecho", "ida"),
    ("Somente ida por R$ 299", "ida"),
    ("Recife x Natal só ida", "ida"),
    ("Voos para Orlando por 2.950 reais", None),
])
def test_ida_e_volta_ou_so_ida(texto, tipo):
    assert extrair(texto, HOJE).tipo_viagem == tipo
