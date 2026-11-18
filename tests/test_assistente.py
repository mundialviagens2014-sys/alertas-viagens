"""Pergunte ao MVO: entender a pergunta por regras e responder com os números do sistema (só leitura)."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import assistente
from app.main import app
from app.models import Cliente, Interesse, Promocao


@pytest.mark.parametrize("pergunta, consulta, extra", [
    ("Ok Google, quantos clientes estão em conversa no MVO?", "funil_etapa", {"etapa": "conversa"}),
    ("quantos clientes aprovados", "funil_etapa", {"etapa": "ganho"}),
    ("quantas promoções para enviar", "fila", {}),
    ("vendas do mês passado", "vendas", {"periodo": "mes_anterior"}),
    ("promoção mais barata para Lisboa", "promocao_barata", {"destino": "LIS"}),
    ("pedidos aguardando emissão", "pedidos", {}),
    ("quantos clientes querem ir para Paris", "interessados", {"destino": "PAR"}),
    ("como está o funil", "funil_resumo", {}),
    ("telefone do Carlos", "cliente", {"nome": "carlos"}),
])
def test_entende_por_regras(pergunta, consulta, extra):
    p = assistente.entender_por_regras(pergunta)
    assert p is not None and p.consulta == consulta
    for chave, valor in extra.items():
        assert getattr(p, chave) == valor


def test_pergunta_sem_sentido_nao_vira_consulta():
    assert assistente.entender_por_regras("qual a capital da França") is None


@pytest.fixture()
def site(db):
    with TestClient(app) as cliente:
        cliente.post("/login", data={"login": "admin", "senha": "admin123"})
        yield cliente


def test_responde_pelo_site_com_os_dados(site, db):
    c = Cliente(nome="João Pereira", telefone="5585999990000", consentimento=True)
    c.interesses.append(Interesse(origem="FOR", destino="LIS"))
    db.add(c)
    db.add(Promocao(fonte="manual", origem="FOR", destino="LIS", preco=2100, companhia="TAP", coletada_em=datetime.now()))
    db.add(Promocao(fonte="manual", origem="FOR", destino="LIS", preco=2600, coletada_em=datetime.now()))
    db.commit()

    j = site.post("/assistente/perguntar", data={"pergunta": "promoção mais barata para Lisboa"}).json()
    assert "2.100" in j["texto"] and "TAP" in j["texto"] and j["link"] == "/promocoes"

    j = site.post("/assistente/perguntar", data={"pergunta": "quantos clientes querem ir para Lisboa"}).json()
    assert j["texto"].startswith("1 cliente quer") and j["detalhes"] == ["João Pereira"]

    j = site.post("/assistente/perguntar", data={"pergunta": "dados do cliente joao"}).json()   # sem acento acha
    assert j["texto"].startswith("João Pereira") and j["link"].startswith("/clientes/")


def test_assistente_exige_login(db):
    with TestClient(app) as anonimo:
        r = anonimo.post("/assistente/perguntar", data={"pergunta": "vendas"}, follow_redirects=False)
        assert r.status_code in (303, 401, 403)
