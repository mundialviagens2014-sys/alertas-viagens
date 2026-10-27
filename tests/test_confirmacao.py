"""Confirmação de cadastro: quem responde o formulário ganha uma mensagem pronta na fila de envio
("obrigado pelo cadastro, vamos ficar de olho nas promoções de março a abril para Lisboa e Paris")."""
from datetime import date
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app import formulario
from app.main import app
from app.models import Cliente, ConfirmacaoCadastro, Interesse, Usuario
from app.servicos import (
    TEMPLATE_CONFIRMACAO,
    gerar_hash_senha,
    montar_confirmacao,
    periodo_extenso,
    template_confirmacao,
    trechos_da_confirmacao,
)
from app.web import contar_pendentes
from tests.test_formulario import CAB, JOAO, MARIA, URL, _google

FETCH = {"X-Requested-With": "fetch"}
MARIA_ROMA = '"21/10/2026 08:00:00","Maria da Silva Oliveira","(85) 99876-1234","Fortaleza","Roma","Sim","Sim","","maio",""'
MARIA_MADRI = '"22/10/2026 08:00:00","Maria da Silva Oliveira","(85) 99876-1234","Fortaleza","Madri","Sim","Sim","","",""'
SEM_AUTORIZAR = '"20/10/2026 11:00:00","Carla Souza","85 98888-7777","Fortaleza","Natal","Não","Não","","",""'


@pytest.fixture()
def site(db):
    with TestClient(app) as cliente:
        cliente.post("/login", data={"login": "admin", "senha": "admin123"})
        yield cliente


def _sincronizar(db, linhas):
    chave = formulario.chave_secreta(db)
    if not formulario.ler_estado(db, formulario.CHAVE_URL):
        formulario.configurar(db, URL)
    with _google([CAB, *linhas], chave) as http:
        return formulario.sincronizar(db, http)


def _mensagem(db, conf):
    return montar_confirmacao(template_confirmacao(db).texto, conf.cliente, trechos_da_confirmacao(conf))


def test_periodo_escrito_para_o_cliente():
    assert periodo_extenso(None, None) == "qualquer data"
    assert periodo_extenso(date(2027, 3, 1), date(2027, 3, 31)) == "março de 2027"
    assert periodo_extenso(date(2027, 3, 1), date(2027, 6, 30)) == "março a junho de 2027"
    assert periodo_extenso(date(2026, 12, 1), date(2027, 1, 31)) == "dezembro de 2026 a janeiro de 2027"
    assert periodo_extenso(date(2027, 3, 15), date(2027, 3, 15)) == "15/03/2027"
    assert periodo_extenso(date(2027, 3, 15), date(2027, 3, 22)) == "15/03 a 22/03/2027"
    assert periodo_extenso(date(2026, 12, 20), date(2027, 1, 5)) == "20/12/2026 a 05/01/2027"
    assert periodo_extenso(None, date(2027, 6, 30)) == "até junho de 2027"
    assert periodo_extenso(date(2027, 3, 15), None) == "a partir de 15/03/2027"


def test_formulario_prepara_confirmacao_com_destinos_e_datas(db):
    r = _sincronizar(db, [MARIA, JOAO])
    assert (r["novos"], r["confirmacoes"]) == (2, 2)
    maria = db.query(Cliente).filter_by(telefone="5585998761234").one()
    conf = maria.confirmacoes[0]
    assert conf.status == "nova" and conf.trechos is None
    msg = _mensagem(db, conf)
    assert msg.startswith("Olá, Maria Oliveira! 😊")
    assert "Obrigado pelo seu cadastro na" in msg
    assert "✈️ *Fortaleza → Lisboa e Paris*\n📅 março a abril de 2027" in msg
    joao = db.query(Cliente).filter_by(telefone="5588997123344").one()
    assert "✈️ *Fortaleza → Orlando*\n📅 qualquer data" in _mensagem(db, joao.confirmacoes[0])

    # a mesma planilha lida de novo não cria outra confirmação
    assert _sincronizar(db, [MARIA, JOAO])["confirmacoes"] == 0
    assert db.query(ConfirmacaoCadastro).count() == 2

    # Maria responde de novo antes da equipe enviar: o trecho novo entra na mesma confirmação
    assert _sincronizar(db, [MARIA, JOAO, MARIA_ROMA])["confirmacoes"] == 1
    db.refresh(maria)
    assert len(maria.confirmacoes) == 1
    assert "Roma" in _mensagem(db, maria.confirmacoes[0])

    # depois de enviada, uma resposta com destino novo gera outra confirmação, só com o trecho novo
    maria.confirmacoes[0].status = "enviada"
    db.commit()
    assert _sincronizar(db, [MARIA, JOAO, MARIA_ROMA, MARIA_MADRI])["confirmacoes"] == 1
    db.refresh(maria)
    nova = maria.confirmacoes[-1]
    assert nova.status == "nova" and nova.trechos
    msg = _mensagem(db, nova)
    assert "Madri" in msg and "Lisboa" not in msg and "Roma" not in msg


def test_importacao_pela_planilha_nao_prepara_confirmacao(site, db):
    conteudo = "\n".join([CAB, MARIA]).encode()
    r = site.post("/clientes/importar", files={"arquivo": ("respostas.csv", conteudo, "text/csv")})
    token = r.text.split('name="token" value="')[1].split('"')[0]
    site.post("/clientes/importar/confirmar", data={"token": token})
    assert db.query(Cliente).count() == 1
    assert db.query(ConfirmacaoCadastro).count() == 0


def test_fila_envia_e_pula_confirmacao(site, db):
    _sincronizar(db, [MARIA, JOAO, SEM_AUTORIZAR])
    assert contar_pendentes() == 3  # o número do menu inclui as confirmações

    fila = site.get("/").text
    assert "Novos cadastros pelo formulário" in fila
    assert "Maria da Silva Oliveira" in fila and "FOR → LIS (março/2027 a abril/2027)" in fila
    link = fila.split('href="https://wa.me/5585998761234?text=')[1].split('"')[0]
    assert "Fortaleza → Lisboa e Paris" in unquote(link)
    assert "sem consentimento" in fila  # Carla não autorizou: o botão fica desligado

    maria = db.query(Cliente).filter_by(telefone="5585998761234").one()
    joao = db.query(Cliente).filter_by(telefone="5588997123344").one()
    carla = db.query(Cliente).filter_by(telefone="5585988887777").one()
    assert site.post(f"/confirmacoes/{maria.confirmacoes[0].id}/enviar", headers=FETCH).json() == {"ok": True, "envios": 1}
    assert site.post(f"/confirmacoes/{joao.confirmacoes[0].id}/descartar", headers=FETCH).json() == {"ok": True}
    assert site.post(f"/confirmacoes/{carla.confirmacoes[0].id}/enviar", headers=FETCH).status_code == 400
    db.expire_all()
    enviada = db.get(ConfirmacaoCadastro, maria.confirmacoes[0].id)
    assert (enviada.status, enviada.enviado_por, enviada.envios) == ("enviada", "Administrador", 1)
    assert db.get(ConfirmacaoCadastro, joao.confirmacoes[0].id).status == "descartada"

    fila = site.get("/").text
    assert "Maria da Silva Oliveira" not in fila and "João Pereira" not in fila and "Carla Souza" in fila
    assert contar_pendentes() == 1

    # na página do cliente: enviada, com quem mandou e o botão de reenviar
    tela = site.get(f"/clientes/{maria.id}").text
    assert "Confirmação do cadastro" in tela and "Enviar de novo" in tela and "Administrador" in tela
    assert site.post(f"/confirmacoes/{enviada.id}/enviar", headers=FETCH).json()["envios"] == 2


def test_confirmacao_pela_pagina_do_cliente(site, db):
    c = Cliente(nome="Eduardo Lima", telefone="5588998613236", consentimento=True)
    c.interesses.append(Interesse(origem="FOR", destino="RIO", periodo_inicio=date(2026, 12, 1),
                                  periodo_fim=date(2026, 12, 31)))
    db.add(c)
    db.commit()
    tela = site.get(f"/clientes/{c.id}").text
    assert f'data-registrar="/clientes/{c.id}/confirmacao"' in tela
    assert "Fortaleza → Rio de Janeiro" in tela and "dezembro de 2026" in tela

    assert site.post(f"/clientes/{c.id}/confirmacao", headers=FETCH).json()["envios"] == 1
    assert site.post(f"/clientes/{c.id}/confirmacao", headers=FETCH).json()["envios"] == 2  # a mesma, sem duplicar
    db.expire_all()
    assert db.query(ConfirmacaoCadastro).filter_by(cliente_id=c.id).count() == 1

    # excluir o cliente apaga as confirmações dele
    site.post(f"/clientes/{c.id}/excluir")
    db.expire_all()
    assert db.query(ConfirmacaoCadastro).count() == 0


def test_texto_da_confirmacao_em_configuracoes(site, db):
    tela = site.get("/configuracoes").text
    assert "Confirmação do cadastro" in tela and "Fortaleza → Lisboa e Paris" in tela and "março a junho de 2027" in tela

    site.post("/configuracoes/confirmacao", data={"texto": "Oi, {primeiro_nome}! Vamos buscar as promoções de {datas} "
                                                           "para {destinos}. Equipe {agencia}"})
    tela = site.get("/configuracoes").text
    assert "Oi, Maria! Vamos buscar as promoções de março a junho de 2027 para Lisboa e Paris." in tela

    site.post("/configuracoes/confirmacao", data={"texto": "qualquer", "restaurar": "1"})
    db.expire_all()
    assert template_confirmacao(db).texto == TEMPLATE_CONFIRMACAO

    # atendente não muda o texto
    db.add(Usuario(nome="Atendente", login="atendente", senha_hash=gerar_hash_senha("senha123"), papel="atendente"))
    db.commit()
    with TestClient(app) as atendente:
        atendente.post("/login", data={"login": "atendente", "senha": "senha123"})
        assert atendente.post("/configuracoes/confirmacao", data={"texto": "x"}).status_code == 403
