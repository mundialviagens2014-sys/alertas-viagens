"""Trazer de volta: quem entra na lista e as mensagens de retorno."""
from datetime import datetime, timedelta

import httpx
import pytest

from app import crm, reengajamento
from app import whatsapp_api as api
from app.models import Cliente, ConversaAtendimento, EnvioWhatsApp, Interesse, Match, Promocao, Reengajamento, RespostaWhatsApp
from app.models_mvo import Emissao, PedidoEmissao

AGORA = datetime.now()


def _cliente(db, nome, tel, dias_cadastro=90, **kw):
    c = Cliente(nome=nome, telefone=tel, consentimento=kw.pop("consentimento", True), ativo=kw.pop("ativo", True),
                criado_em=AGORA - timedelta(days=dias_cadastro))
    c.interesses.append(Interesse(origem="FOR", destino="LIS"))
    db.add(c)
    db.flush()
    return c


def _enviada(db, cliente, dias):
    p = Promocao(origem="FOR", destino="LIS", preco=2389, fonte="manual", texto_original="FOR LIS")
    db.add(p)
    db.flush()
    db.add(Match(cliente_id=cliente.id, promocao_id=p.id, status="enviada", enviado_em=AGORA - timedelta(days=dias)))


@pytest.fixture()
def base(db):
    calado = _cliente(db, "Calado Silva", "5585900000001")          # recebe promoções e nunca responde
    _enviada(db, calado, 40)
    respondeu = _cliente(db, "Respondeu Recente", "5585900000002")  # respondeu há 5 dias: fora
    _enviada(db, respondeu, 40)
    db.add(RespostaWhatsApp(cliente_id=respondeu.id, telefone=respondeu.telefone, texto="oi", recebido_em=AGORA - timedelta(days=5)))
    comprou = _cliente(db, "Comprou Antigo", "5585900000003", dias_cadastro=400)  # comprou há 8 meses
    db.add(Emissao(cliente_id=comprou.id, destino="MIA", origem="FOR", data_emissao=AGORA - timedelta(days=240), status="ativa"))
    comprou_recente = _cliente(db, "Comprou Agora", "5585900000004")
    db.add(Emissao(cliente_id=comprou_recente.id, destino="GRU", origem="FOR", data_emissao=AGORA - timedelta(days=20)))
    sem_aut = _cliente(db, "Sem Autorizacao", "5585900000005", consentimento=False)
    _enviada(db, sem_aut, 40)
    pedido = _cliente(db, "Pedido Aberto", "5585900000006")
    _enviada(db, pedido, 40)
    db.add(PedidoEmissao(cliente_id=pedido.id, status="aguardando", criado_em=AGORA - timedelta(days=60)))
    novo = _cliente(db, "Cadastro Novo", "5585900000007", dias_cadastro=3)   # acabou de entrar
    _enviada(db, novo, 2)
    db.commit()
    return {"calado": calado, "comprou": comprou}


def test_quem_entra_na_lista(db, base):
    lista = {c.cliente.nome: c for c in reengajamento.candidatos(db)}
    assert set(lista) == {"Calado Silva", "Comprou Antigo"}
    assert lista["Calado Silva"].motivo == "sem_resposta" and lista["Comprou Antigo"].motivo == "sem_compra"
    assert lista["Comprou Antigo"].ultima_viagem == "Miami" and lista["Comprou Antigo"].dias >= 240

    # mandou pelo WhatsApp (ou pulou): sai da lista até o intervalo passar
    reengajamento.registrar(db, base["calado"].id, "sem_resposta", "whatsapp", "Equipe")
    assert {c.cliente.nome for c in reengajamento.candidatos(db)} == {"Comprou Antigo"}
    depois = AGORA + timedelta(days=31)
    assert "Calado Silva" in {c.cliente.nome for c in reengajamento.candidatos(db, depois)}

    # o CRM mostra a mensagem de retorno
    cartao = {c.cliente.id: c for c in crm.cartoes(db)}[base["calado"].id]
    assert cartao.etapa == "enviada" and "retorno" in cartao.evento


def test_tela_whatsapp_e_api(db, base, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    enviados = []

    def transporte(req: httpx.Request):
        enviados.append(req)
        return httpx.Response(200, json={"messages": [{"id": "wamid.retorno"}]})

    monkeypatch.setattr(api, "configurada", lambda db: True)
    monkeypatch.setattr(api, "configuracao", lambda db: {**api.PADROES, "phone_id": "111", "token": "t", "idioma": "pt_BR",
                                                         "contato": "5588988870400"})
    real = httpx.Client
    monkeypatch.setattr(api.httpx, "Client", lambda *a, **k: real(transport=httpx.MockTransport(transporte)))
    with TestClient(app) as site:
        site.post("/login", data={"login": "admin", "senha": "admin123"})
        tela = site.get("/crm/reengajar").text
        assert "Calado Silva" in tela and "Comprou Antigo" in tela and "Faz um tempinho" in tela
        # WhatsApp da equipe: abre com a mensagem e o link da página
        r = site.post(f"/crm/reengajar/{base['calado'].id}/whatsapp", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("https://api.whatsapp.com/send?phone=5585900000001")
        assert "para-voce" in httpx.URL(r.headers["location"]).params["text"]
        # pelo 0500: precisa do modelo configurado
        assert site.post(f"/crm/reengajar/{base['comprou'].id}/api", headers={"X-Requested-With": "fetch"}).status_code == 400
        site.post("/crm/reengajar/configuracoes", data={"modelo": "retorno_cliente", "variaveis": "nome_cliente=primeiro_nome, link=pagina"})
        r = site.post(f"/crm/reengajar/{base['comprou'].id}/api", headers={"X-Requested-With": "fetch"})
        assert r.json() == {"ok": True, "motivo": ""}
    corpo = enviados[-1].read().decode()
    assert "retorno_cliente" in corpo and '"parameter_name":"nome_cliente"' in corpo.replace(" ", "")
    assert db.query(EnvioWhatsApp).filter_by(tipo="reengajamento").count() == 1
    assert db.query(Reengajamento).count() == 2 and not reengajamento.candidatos(db)
