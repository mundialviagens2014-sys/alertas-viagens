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


def test_nuvem_fila_e_chamada_do_github(db, site, monkeypatch):
    from app.config import settings
    chamadas = []

    class Resp:
        status_code = 204
        text = ""

    def falso_post(url, timeout, headers, json):
        chamadas.append((url, headers["Authorization"], json))
        return Resp()

    monkeypatch.setattr(bilhetes.httpx, "post", falso_post)
    monkeypatch.setattr(settings, "github_token_robo", "gh-teste")
    monkeypatch.setattr(bilhetes.threading, "Thread", lambda target, daemon: type("T", (), {"start": staticmethod(target)}))
    site.post("/bilhetes", data={"localizador": "ABC123", "companhia": "AD", "sobrenome": "Lima", "origem": "FOR"})
    assert chamadas and chamadas[0][0].endswith("/repos/mundialviagens2014-sys/alertas-viagens/dispatches")
    assert chamadas[0][1] == "Bearer gh-teste" and chamadas[0][2] == {"event_type": "bilhete"}
    robo = _robo(db)
    robo.headers["X-Robo-Maquina"] = "GitHub Actions"
    assert robo.post("/api/robo/fila").json() == {"pendentes": 1}
    assert "robô na nuvem" in site.get("/bilhetes").text


# ---------------------------------------------------------------- bilhete pelo print (LATAM barra o robô)

LIDO_PRINT = {
    "e_reserva": True, "localizador": "QWE123",
    "trechos": [{"tipo": "ida", "voos": [
        {"numero": "LA3456", "origem": "FOR", "destino": "GRU", "partida": "2026-12-10T05:30",
         "chegada": "2026-12-10T08:55", "cabine": "Economy"}]},
        {"tipo": "volta", "voos": [
            {"numero": "LA3457", "origem": "GRU", "destino": "FOR", "partida": "2026-12-20T22:10",
             "chegada": "2026-12-21T01:30"}]}],
    "passageiros": [{"nome": "EXEMPLO/MARIA", "bilhete": "9572100000000"}],
}


def test_print_gera_o_bilhete_quando_o_robo_e_barrado(db, site, monkeypatch):
    from app import ia
    monkeypatch.setattr(ia, "configurada", lambda: True)
    recebido = {}

    def falso_gemini(partes, esquema, **k):
        recebido["mime"] = partes[-1].get("inline_data", {}).get("mime_type")
        return {k2: v for k2, v in LIDO_PRINT.items()} | {"passageiros": [dict(p) for p in LIDO_PRINT["passageiros"]]}

    monkeypatch.setattr(ia, "gerar_json", falso_gemini)
    site.post("/bilhetes", data={"localizador": "QWE123", "companhia": "LA", "sobrenome": "Exemplo"})
    c = db.query(ConsultaReserva).one()
    tela = site.get(f"/bilhetes/{c.id}").text
    assert "Gerar pelo print da reserva" in tela   # LATAM: já aparece enquanto espera o robô
    # o robô é barrado pelo captcha
    robo = _robo(db)
    assert robo.post("/api/robo/proximo").json()["id"] == c.id
    robo.post(f"/api/robo/consultas/{c.id}", json={"status": "ajuda", "mensagem": "O site pediu verificação (captcha)."})
    db.expire_all()
    assert c.status == "ajuda" and "Gerar pelo print" in site.get(f"/bilhetes/{c.id}").text

    r = site.post(f"/bilhetes/{c.id}/ler-print", files={"arquivo": ("reserva.pdf", b"%PDF-1.4 falso", "application/pdf")})
    assert "Reserva lida do print" in r.text and recebido["mime"] == "application/pdf"
    db.expire_all()
    dados = bilhetes.reserva(c)
    assert c.status == "pronta" and dados["fonte"] == "print"
    assert [t["tipo"] for t in dados["trechos"]] == ["ida", "volta"]
    assert dados["passageiros"][0]["nome"] == "MARIA EXEMPLO"   # SOBRENOME/NOME virou nome normal
    assert "Lido do print da reserva" in r.text
    pdf = site.get(f"/bilhetes/{c.id}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    # o robô atrasado não estraga o que veio do print
    robo.post(f"/api/robo/consultas/{c.id}", json={"status": "erro", "mensagem": "atrasado"})
    db.expire_all()
    assert c.status == "pronta" and bilhetes.reserva(c)["fonte"] == "print"


def test_print_que_nao_e_reserva_ou_arquivo_errado(db, site, monkeypatch):
    from app import ia
    monkeypatch.setattr(ia, "configurada", lambda: True)
    monkeypatch.setattr(ia, "gerar_json", lambda partes, esquema, **k: {"e_reserva": False, "trechos": []})
    site.post("/bilhetes", data={"localizador": "QWE123", "companhia": "LA", "sobrenome": "Exemplo"})
    c = db.query(ConsultaReserva).one()
    r = site.post(f"/bilhetes/{c.id}/ler-print", files={"arquivo": ("foto.png", b"\x89PNG falso", "image/png")})
    assert "não parece uma reserva" in r.text
    r = site.post(f"/bilhetes/{c.id}/ler-print", files={"arquivo": ("x.exe", b"MZ", "application/octet-stream")})
    assert "PDF de até 8 MB" in r.text
    db.expire_all()
    assert c.status == "pendente"


def test_esquema_do_print_aceito_pelo_gemini():
    def enums(no):
        if isinstance(no, dict):
            if "enum" in no:
                yield no["enum"]
            for v in no.values():
                yield from enums(v)
    assert all(all(str(x).strip() for x in lista) for lista in enums(bilhetes.ESQUEMA_PRINT))


def test_print_na_primeira_tela_gera_o_bilhete_sem_o_robo(db, site, monkeypatch):
    from app import ia
    monkeypatch.setattr(ia, "configurada", lambda: True)
    chamadas = []
    monkeypatch.setattr(bilhetes, "chamar_robo_nuvem", lambda: chamadas.append(1))
    lido = dict(LIDO_PRINT, localizador="qwe-123", companhia="")   # companhia sai do número do voo (LA3456)
    monkeypatch.setattr(ia, "gerar_json", lambda partes, esquema, **k: {**lido, "passageiros": [dict(p) for p in lido["passageiros"]]})
    assert "Gerar pelo print da reserva" in site.get("/bilhetes").text
    r = site.post("/bilhetes/print", files={"arquivo": ("reserva.png", b"\x89PNG falso", "image/png")})
    assert "Bilhete gerado pelo print" in r.text
    c = db.query(ConsultaReserva).one()
    assert (c.localizador, c.companhia, c.sobrenome, c.origem, c.status) == ("QWE123", "LA", "Exemplo", "FOR", "pronta")
    assert chamadas == []   # não foi para a fila do robô
    # outro print da mesma reserva atualiza o mesmo bilhete
    site.post("/bilhetes/print", files={"arquivo": ("reserva.png", b"\x89PNG falso", "image/png")})
    assert db.query(ConsultaReserva).count() == 1


def test_print_sem_localizador_na_primeira_tela(db, site, monkeypatch):
    from app import ia
    monkeypatch.setattr(ia, "configurada", lambda: True)
    monkeypatch.setattr(ia, "gerar_json", lambda partes, esquema, **k: dict(LIDO_PRINT, localizador="1272100000000"))
    r = site.post("/bilhetes/print", files={"arquivo": ("reserva.png", b"\x89PNG falso", "image/png")})
    assert "Não achei o localizador" in r.text
    assert db.query(ConsultaReserva).count() == 0
