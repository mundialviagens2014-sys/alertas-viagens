"""Bilhetes: pedido no MVO, fila do robô, API com token, PDF e atalho na emissão."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models_mvo import ConsultaReserva, Emissao, EmissaoPassageiro
from app.mvo import bilhetes

RESERVA = {
    "situacao": "confirmada",
    "trechos": [{"tipo": "ida", "voos": [
        {"numero": "LA3456", "origem": "FOR", "destino": "GRU", "partida": "2026-12-10T05:30:00",
         "chegada": "2026-12-10T08:55", "cabine": "Economy"},
        {"numero": "LA8084", "origem": "gru", "destino": "LIS", "partida": "2026-12-10T10:20",
         "chegada": "2026-12-10T23:50", "lixo": "x" * 500}]}],
    "passageiros": [{"nome": "Maria Exemplo", "bilhete": "9572100000000", "item_pessoal": 1, "bagagem_mao": 1,
                     "bagagem_despachada": 0, "cpf": "não deve passar"}],
}


@pytest.fixture()
def site(db):
    with TestClient(app) as cliente:
        cliente.post("/login", data={"login": "admin", "senha": "admin123"})
        yield cliente


def _robo(db):
    token = bilhetes.gerar_token(db)
    robo = TestClient(app)
    robo.headers.update({"Authorization": f"Bearer {token}", "X-Robo-Maquina": "PC-RECEPCAO", "X-Robo-Versao": "1.0"})
    return robo


def test_fluxo_completo(db, site):
    db.add(Emissao(forma="pagante", localizador="ABC123", origem="FOR", destino="LIS", companhia="LATAM"))
    db.commit()
    r = site.post("/bilhetes", data={"localizador": " abc-123 ", "companhia": "latam", "sobrenome": "Exemplo",
                                     "origem": "for"})
    assert r.status_code == 200 and "ABC123" in r.text
    c = db.query(ConsultaReserva).one()
    assert (c.localizador, c.companhia, c.origem, c.status) == ("ABC123", "LA", "FOR", "pendente")
    assert c.emissao_id is not None  # achou a emissão do mesmo localizador
    # pedir de novo enquanto está na fila não duplica
    site.post("/bilhetes", data={"localizador": "ABC123", "companhia": "LA", "sobrenome": "Exemplo"})
    assert db.query(ConsultaReserva).count() == 1

    robo = _robo(db)
    assert TestClient(app).post("/api/robo/proximo").status_code == 401
    assert TestClient(app).post("/api/robo/proximo", headers={"Authorization": "Bearer errado"}).status_code == 401
    pedido = robo.post("/api/robo/proximo").json()
    assert pedido["localizador"] == "ABC123" and pedido["companhia"] == "LA" and "latamairlines" in pedido["pagina"]
    assert robo.post("/api/robo/proximo").status_code == 204  # nada mais na fila
    assert "robô ligado" in site.get("/bilhetes").text

    r = robo.post(f"/api/robo/consultas/{pedido['id']}", json={"status": "pronta", "dados": RESERVA})
    assert r.json() == {"ok": True, "status": "pronta"}
    db.expire_all()
    dados = bilhetes.reserva(db.get(ConsultaReserva, pedido["id"]))
    voos = dados["trechos"][0]["voos"]
    assert voos[0]["partida"] == "2026-12-10T05:30" and voos[1]["origem"] == "GRU" and "lixo" not in voos[1]
    assert dados["passageiros"][0]["nome"] == "MARIA EXEMPLO" and "cpf" not in dados["passageiros"][0]

    tela = site.get(f"/bilhetes/{pedido['id']}").text
    assert "LA3456" in tela and "Baixar PDF" in tela
    pdf = site.get(f"/bilhetes/{pedido['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert 'filename="Bilhete_ABC123.pdf"' in pdf.headers["content-disposition"]


def test_robo_sem_reserva_ou_captcha(db, site):
    site.post("/bilhetes", data={"localizador": "XYZ789", "companhia": "G3", "sobrenome": "Silva"})
    site.post("/bilhetes", data={"localizador": "QWE456", "companhia": "AD", "sobrenome": "Souza"})
    robo = _robo(db)
    p1 = robo.post("/api/robo/proximo").json()
    robo.post(f"/api/robo/consultas/{p1['id']}", json={"status": "nao_encontrada", "mensagem": "Reserva não existe"})
    p2 = robo.post("/api/robo/proximo").json()
    robo.post(f"/api/robo/consultas/{p2['id']}", json={"status": "ajuda", "mensagem": "Captcha na página"})
    assert "Reserva não encontrada" in site.get(f"/bilhetes/{p1['id']}").text
    tela = site.get(f"/bilhetes/{p2['id']}").text
    assert "Precisa de uma pessoa" in tela and "Captcha na página" in tela
    assert site.get(f"/bilhetes/{p2['id']}/pdf").status_code == 409
    # tentar de novo volta para a fila
    site.post(f"/bilhetes/{p2['id']}/repetir")
    assert robo.post("/api/robo/proximo").json()["id"] == p2["id"]
    # resposta inválida não estraga a consulta
    r = robo.post(f"/api/robo/consultas/{p2['id']}", json={"status": "pronta", "dados": {"trechos": []}})
    assert r.status_code == 422


def test_pedido_esquecido_volta_para_fila(db, site):
    site.post("/bilhetes", data={"localizador": "ABC123", "companhia": "LA", "sobrenome": "Exemplo"})
    robo = _robo(db)
    p = robo.post("/api/robo/proximo").json()
    c = db.get(ConsultaReserva, p["id"])
    c.iniciado_em = datetime.now() - timedelta(minutes=10)
    db.commit()
    assert robo.post("/api/robo/proximo").json()["id"] == p["id"]


def test_validacao_do_pedido(db, site):
    assert "6 letras" in site.post("/bilhetes", data={"localizador": "AB", "companhia": "LA", "sobrenome": "X1"}).text
    assert "Escolha a companhia" in site.post("/bilhetes", data={"localizador": "ABC123", "companhia": "TAP",
                                                                 "sobrenome": "Silva"}).text
    assert "3 letras" in site.post("/bilhetes", data={"localizador": "ABC123", "companhia": "LA", "sobrenome": "Silva",
                                                      "origem": "FORT"}).text
    assert db.query(ConsultaReserva).count() == 0


def test_token_em_configuracoes(db, site):
    tela = site.get("/configuracoes").text
    assert 'id="robo"' in tela and "Gerar token do robô" in tela
    r = site.post("/configuracoes/robo", data={"acao": "gerar"})
    assert "mvo_robo_" in r.text and "aparece só uma vez" in r.text
    assert "mvo_robo_" not in site.get("/configuracoes").text
    z = site.get("/configuracoes/robo.zip")
    assert z.status_code == 200 and z.content[:2] == b"PK"
    import io
    import zipfile
    nomes = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "robo-mvo/robo_reservas.py" in nomes and "robo-mvo/iniciar.bat" in nomes
    assert not any("robo.json" in n or "capturas" in n for n in nomes)
    site.post("/configuracoes/robo", data={"acao": "desligar"})
    assert not bilhetes.tem_token(db)


def test_atalho_na_emissao(db, site):
    e = Emissao(forma="pagante", localizador="DZUBSZ", origem="GIG", destino="JPA", programa="Smiles")
    db.add(e)
    db.flush()
    db.add(EmissaoPassageiro(emissao_id=e.id, nome="Stefany Costa"))
    db.commit()
    tela = site.get(f"/emissoes/{e.id}").text
    assert "/bilhetes?localizador=DZUBSZ&amp;companhia=G3&amp;sobrenome=Costa&amp;origem=GIG" in tela
    form = site.get("/bilhetes?localizador=DZUBSZ&companhia=G3&sobrenome=Costa&origem=GIG").text
    assert 'value="DZUBSZ"' in form and 'value="Costa"' in form
