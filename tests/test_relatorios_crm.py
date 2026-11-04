"""Relatórios do administrador (indicadores, gráficos e dados brutos) e CRM (funil automático com arrastar à mão)."""
import io
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import crm, relatorios, whatsapp_api as api
from app.main import app
from app.models import (
    Cliente, ConfirmacaoCadastro, EnvioWhatsApp, EtapaCRM, Interesse, Match, RespostaWhatsApp, Usuario,
)
from app.models_mvo import Emissao, PedidoEmissao
from app.servicos import gerar_hash_senha, registrar_promocao


@pytest.fixture()
def site(db):
    with TestClient(app) as cliente:
        cliente.post("/login", data={"login": "admin", "senha": "admin123"})
        yield cliente


@pytest.fixture()
def atendente(db):
    db.add(Usuario(nome="Bia", login="bia", senha_hash=gerar_hash_senha("bia12345"), papel="atendente"))
    db.commit()
    with TestClient(app) as cliente:
        cliente.post("/login", data={"login": "bia", "senha": "bia12345"})
        yield cliente


def _cenario(db):
    ana = Cliente(nome="Ana Costa", telefone="5585981112222", consentimento=True)
    ana.interesses.append(Interesse(origem="FOR", destino="LIS"))
    beto = Cliente(nome="Beto Lima", telefone="5585981113333", consentimento=True)
    beto.interesses.append(Interesse(origem="FOR", destino="LIS"))
    db.add_all([ana, beto])
    db.commit()
    registrar_promocao(db, texto="FOR → LIS R$ 2.389 ida e volta TAP em março/27", fonte="telegram")
    agora = datetime.now()
    for m in db.query(Match):
        m.status, m.enviado_em, m.enviado_por, m.envios = "enviada", agora - timedelta(days=2), "Ana Laura", 1
    db.add_all([
        Emissao(forma="pagante", preco_venda=2300, taxa_embarque=100, outros_custos=1800, cliente_id=ana.id,
                cliente_nome=ana.nome, data_emissao=agora - timedelta(days=1), programa="LATAM Pass", companhia="TAP",
                origem="FOR", destino="LIS", emissor_nome="Zoe", qtd_passageiros=2),
        Emissao(forma="programa_proprio", preco_venda=1500, custo_milhas=900, data_emissao=agora - timedelta(days=40),
                programa="Smiles", emissor_nome="Zoe"),
        EnvioWhatsApp(cliente_id=ana.id, telefone=ana.telefone, tipo="promocao", modelo="promo", status="lida", wamid="w1"),
        EnvioWhatsApp(cliente_id=beto.id, telefone=beto.telefone, tipo="promocao", modelo="promo", status="entregue", wamid="w2"),
        EnvioWhatsApp(cliente_id=beto.id, telefone=beto.telefone, tipo="promocao", modelo="promo", status="falhou", wamid="w3"),
        EnvioWhatsApp(cliente_id=ana.id, telefone=ana.telefone, tipo="confirmacao", modelo="conf", status="entregue", wamid="w4"),
        RespostaWhatsApp(cliente_id=beto.id, telefone=beto.telefone, texto="Tem para abril?", wamid="r1", intencao="duvida"),
    ])
    db.commit()
    return ana, beto


# ---------------------------------------------------------------- relatórios


def test_relatorios_so_para_administrador(db, site, atendente):
    assert site.get("/relatorios").status_code == 200
    assert "/relatorios" in site.get("/configuracoes").text
    r = atendente.get("/relatorios", follow_redirects=False)
    assert r.status_code in (302, 303, 403)
    assert "/relatorios" not in atendente.get("/configuracoes").text
    assert atendente.get("/relatorios/dados-brutos.xlsx", follow_redirects=False).status_code in (302, 303, 403)


def test_numeros_dos_relatorios(db, site):
    api.salvar_configuracao(db, {"preco_promocao": "0,50", "preco_confirmacao": "0.10"})
    _cenario(db)
    p = relatorios.periodo_de("90")
    v = relatorios.vendas(db, p)
    assert v["qtd"] == 2 and v["vendido"] == 3800 and v["passageiros"] == 3
    assert v["custo"] == 2700 and v["lucro"] == 1100 and v["ticket"] == 1900
    assert dict(v["por_programa"]) == {"LATAM Pass": 1, "Smiles": 1}
    w = relatorios.whatsapp(db, p, api.configuracao(db))
    assert w["preco_promocao"] == 0.5 and w["cobradas_promocao"] == 2 and w["custo_promocoes"] == 1.0
    assert w["cobradas_confirmacao"] == 1 and w["custo_confirmacoes"] == 0.1 and w["custo"] == 1.1 and w["falhas"] == 1
    assert w["respostas"] == 1 and w["taxa_resposta"] == 50.0
    assert w["envios_promocoes_total"] == 2
    c = relatorios.clientes(db, p)
    assert c["total"] == 2 and c["novos"] == 2 and c["top_destinos"][0] == ("Lisboa", 2) and c["compraram"] == 1
    pr = relatorios.promocoes(db, p)
    assert pr["coletadas"] == 1 and pr["por_fonte"] == [("Telegram", 1)] and pr["enviadas"] == 2 and pr["matches"] == 2
    f = dict(relatorios.funil(db, p))
    assert f["Promoções coletadas"] == 1 and f["Promoções enviadas"] == 2 and f["Emissões"] == 2
    semanas = relatorios.vendas_por_semana(db)
    assert len(semanas["rotulos"]) == 12 and sum(semanas["qtd"]) >= 1
    meses = relatorios.vendas_por_mes(db)
    assert len(meses["rotulos"]) == 12 and sum(meses["valor"]) == 3800

    tela = site.get("/relatorios?periodo=90").text
    assert "R$ 3.800" in tela and "<svg" in tela and "Dados brutos" in tela
    assert "R$ 1,10" in tela  # gasto com WhatsApp
    assert site.get("/relatorios?de=2026-01-01&ate=2026-01-31").status_code == 200
    assert site.get("/relatorios?periodo=tudo").status_code == 200


def test_periodos():
    hoje = date(2026, 11, 4)
    assert relatorios.periodo_de("mes", hoje=hoje).inicio == date(2026, 11, 1)
    assert relatorios.periodo_de("mes_anterior", hoje=hoje).fim == date(2026, 10, 31)
    assert relatorios.periodo_de("30", hoje=hoje).dias == 30
    assert relatorios.periodo_de("12m", hoje=hoje).inicio == date(2025, 12, 1)
    p = relatorios.periodo_de("personalizado", date(2026, 3, 10), date(2026, 2, 1), hoje=hoje)
    assert (p.inicio, p.fim) == (date(2026, 2, 1), date(2026, 3, 10))
    assert [relatorios.rotulo_mes(d) for d in relatorios.meses(3, hoje)] == ["set/26", "out/26", "nov/26"]
    assert relatorios.semanas(2, hoje)[-1] == date(2026, 11, 2)


def test_dados_brutos_sem_segredos(db, site):
    from openpyxl import load_workbook

    _cenario(db)
    r = site.get("/relatorios/dados-brutos.xlsx")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    wb = load_workbook(io.BytesIO(r.content), read_only=True)
    abas = wb.sheetnames
    assert abas[0] == "Leia-me" and "clientes" in abas and "emissoes" in abas and "usuarios" in abas
    assert "arquivos_sessao" not in abas and "estado_coletor" not in abas
    cabecalho = [c.value for c in next(wb["usuarios"].iter_rows(max_row=1))]
    assert "login" in cabecalho and "senha_hash" not in cabecalho
    cabecalho = [c.value for c in next(wb["clientes"].iter_rows(max_row=1))]
    assert "telefone" in cabecalho and "token_pagina" not in cabecalho
    cabecalho = [c.value for c in next(wb["contas_programa"].iter_rows(max_row=1))]
    assert "saldo" in cabecalho and not any("cripto" in c for c in cabecalho)
    linhas = list(wb["emissoes"].iter_rows(values_only=True))
    assert len(linhas) == 3  # cabeçalho + 2 emissões
    cabecalho = [c.value for c in next(wb["imagens_promocao"].iter_rows(max_row=1))]
    assert "dados" not in cabecalho
    from app.models_mvo import RegistroAcesso

    registro = db.query(RegistroAcesso).filter(RegistroAcesso.acao == "exportacao").one()
    assert "Dados brutos" in registro.detalhe


def test_precos_por_mensagem_na_configuracao(db, site):
    site.post("/configuracoes/whatsapp", data={"phone_id": "1", "preco_promocao": "0,42", "preco_confirmacao": "abc",
                                              "max_por_dia": "200", "max_por_cliente_dia": "3"})
    conf = api.configuracao(db)
    assert conf["preco_promocao"] == "0.42" and conf["preco_confirmacao"] == "0.04"
    assert 'value="0,42"' in site.get("/configuracoes/whatsapp").text


# ---------------------------------------------------------------- CRM


def test_regras_de_intencao():
    f = crm.classificar_por_regras
    assert f("SAIR") == "sair" and f("não quero mais receber mensagens") == "sair"
    assert f("Não tenho interesse, obrigado") == "recusa" and f("muito caro") == "recusa"
    assert f("Quero! Pode reservar") == "interesse"
    assert f("Tem para abril? Quanto fica parcelado?") == "duvida" and f("vou pensar") == "duvida"
    assert f("Oi") == "outro" and f("[image]") == "outro" and f("") is None
    assert f("Quero saber se tem para abril?") is None  # interesse e dúvida juntos: a IA decide


def test_classificar_pendentes_com_ia_falsa(db, monkeypatch):
    from app import ia

    monkeypatch.setattr(ia.settings, "gemini_api_key", "x")
    monkeypatch.setattr(ia, "classificar_intencao", lambda texto, cliente_http=None: "interesse")
    ana = Cliente(nome="Ana", telefone="5585981112222", consentimento=True)
    db.add(ana)
    db.commit()
    db.add_all([RespostaWhatsApp(cliente_id=ana.id, telefone=ana.telefone, texto="vou pensar", wamid="a"),
                RespostaWhatsApp(cliente_id=ana.id, telefone=ana.telefone, texto="Quero saber se tem para abril?", wamid="b")])
    db.commit()
    resumo = crm.classificar_pendentes(db, limite=5)
    assert resumo == {"regras": 1, "ia": 1, "pendentes": 0}
    por_texto = {r.texto: (r.intencao, r.intencao_por) for r in db.query(RespostaWhatsApp)}
    assert por_texto["vou pensar"] == ("duvida", "regras") and por_texto["Quero saber se tem para abril?"] == ("interesse", "ia")
    assert ia.leituras_hoje(db) == 1


def _cliente(db, nome, telefone):
    c = Cliente(nome=nome, telefone=telefone, consentimento=True, criado_em=datetime.now() - timedelta(days=10))
    c.interesses.append(Interesse(origem="FOR", destino="LIS"))
    db.add(c)
    db.commit()
    return c


def test_etapas_automaticas(db):
    agora = datetime.now()
    novo = _cliente(db, "Novo Cadastro", "5585980000001")
    enviado = _cliente(db, "Recebeu Promo", "5585980000002")
    respondeu = _cliente(db, "Respondeu Interesse", "5585980000003")
    duvida = _cliente(db, "Em Duvida", "5585980000004")
    conversa = _cliente(db, "Em Conversa", "5585980000005")
    pedido = _cliente(db, "Com Pedido", "5585980000006")
    ganho = _cliente(db, "Comprou", "5585980000007")
    perdido = _cliente(db, "Pediu Sair", "5585980000008")
    antigo = _cliente(db, "Antigo Sem Nada", "5585980000009")
    antigo.criado_em = agora - timedelta(days=400)
    registrar_promocao(db, texto="FOR → LIS R$ 2.389 ida e volta TAP em março/27", fonte="manual")
    for m in db.query(Match):
        if m.cliente_id != novo.id and m.cliente_id != antigo.id:
            m.status, m.enviado_em, m.enviado_por = "enviada", agora - timedelta(days=3), "Ana"
    db.add_all([
        RespostaWhatsApp(cliente_id=respondeu.id, telefone=respondeu.telefone, texto="Quero!", wamid="r1",
                         intencao="interesse", recebido_em=agora - timedelta(days=2)),
        RespostaWhatsApp(cliente_id=duvida.id, telefone=duvida.telefone, texto="tem para abril?", wamid="r2",
                         intencao="duvida", recebido_em=agora - timedelta(days=2)),
        RespostaWhatsApp(cliente_id=conversa.id, telefone=conversa.telefone, texto="oi", wamid="r3", intencao="outro",
                         recebido_em=agora - timedelta(days=2)),
        EnvioWhatsApp(cliente_id=conversa.id, telefone=conversa.telefone, tipo="resposta", status="entregue", wamid="e1",
                      enviado_por="Ana", criado_em=agora - timedelta(days=1)),
        RespostaWhatsApp(cliente_id=perdido.id, telefone=perdido.telefone, texto="SAIR", wamid="r4", intencao="sair",
                         descadastrou=True, recebido_em=agora - timedelta(days=1)),
        PedidoEmissao(cliente_id=pedido.id, status="aguardando", origem="FOR", destino="LIS", preco_combinado=2100,
                      criado_em=agora - timedelta(days=1)),
        Emissao(forma="pagante", preco_venda=2300, cliente_id=ganho.id, cliente_nome=ganho.nome,
                data_emissao=agora - timedelta(hours=5), origem="FOR", destino="LIS", emissor_nome="Zoe"),
    ])
    db.commit()
    por_nome = {c.cliente.nome: c for c in crm.cartoes(db, dias=90)}
    assert "Antigo Sem Nada" not in por_nome
    assert por_nome["Novo Cadastro"].etapa == "cadastrado"
    assert por_nome["Recebeu Promo"].etapa == "enviada" and por_nome["Recebeu Promo"].valor == 2389
    assert por_nome["Recebeu Promo"].trecho == "Fortaleza → Lisboa" and por_nome["Recebeu Promo"].responsavel == "Ana"
    assert por_nome["Respondeu Interesse"].etapa == "respondeu" and por_nome["Respondeu Interesse"].intencao == "interesse"
    assert por_nome["Em Duvida"].etapa == "duvida"
    assert por_nome["Em Conversa"].etapa == "conversa"
    assert por_nome["Com Pedido"].etapa == "pedido" and por_nome["Com Pedido"].valor == 2100
    assert por_nome["Comprou"].etapa == "ganho" and por_nome["Comprou"].valor == 2300
    assert por_nome["Pediu Sair"].etapa == "perdido"
    colunas = {c["chave"]: c for c in crm.colunas(list(por_nome.values()))}
    assert colunas["ganho"]["qtd"] == 1 and colunas["ganho"]["valor"] == 2300
    r = crm.resumo(list(por_nome.values()))
    assert r["ganhos"] == 1 and r["perdidos"] == 1 and r["abertos"] == 5

    # mover à mão vale até o próximo acontecimento
    crm.mover(db, enviado.id, "duvida", "Ana", "ligou pedindo mais datas")
    cartao = {c.cliente.id: c for c in crm.cartoes(db, dias=90)}[enviado.id]
    assert cartao.etapa == "duvida" and cartao.manual and "ligou" in cartao.evento
    db.add(RespostaWhatsApp(cliente_id=enviado.id, telefone=enviado.telefone, texto="Quero", wamid="r9",
                            intencao="interesse", recebido_em=datetime.now() + timedelta(seconds=1)))
    db.commit()
    cartao = {c.cliente.id: c for c in crm.cartoes(db, dias=90)}[enviado.id]
    assert cartao.etapa == "respondeu" and not cartao.manual
    assert crm.voltar_ao_automatico(db, enviado.id) and not crm.voltar_ao_automatico(db, enviado.id)

    # pedido aberto segura o cliente mesmo com promoção nova enviada depois
    m = db.query(Match).filter(Match.cliente_id == pedido.id).one()
    m.enviado_em = datetime.now()
    db.commit()
    assert {c.cliente.id: c for c in crm.cartoes(db, dias=90)}[pedido.id].etapa == "pedido"
    # cliente inativo vai para perdido
    conversa.ativo = False
    db.commit()
    assert {c.cliente.id: c for c in crm.cartoes(db, dias=90)}[conversa.id].etapa == "perdido"


def test_tela_do_crm_e_mover(db, site, atendente):
    ana = _cliente(db, "Ana Costa", "5585981112222")
    registrar_promocao(db, texto="FOR → LIS R$ 2.389 ida e volta TAP em março/27", fonte="manual")
    for m in db.query(Match):
        m.status, m.enviado_em, m.enviado_por = "enviada", datetime.now(), "Ana Laura"
    db.commit()
    tela = site.get("/crm").text
    assert "Funil de vendas" in tela and 'data-etapa="enviada"' in tela and "Ana Costa" in tela
    assert 'href="/crm"' in tela and ">CRM<" in tela  # pílula Alertas | Manager | CRM
    assert "Fortaleza → Lisboa" in tela
    assert 'class="cartao-crm"' not in site.get("/crm?q=zzz").text
    assert "Ana Costa" in site.get("/crm?q=ana&etapa=enviada&dias=30").text

    r = site.post("/crm/mover", data={"cliente_id": ana.id, "etapa": "pedido", "motivo": "fechou por telefone"},
                  headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200 and r.json()["nome"] == "Pedido de emissão"
    assert db.query(EtapaCRM).one().motivo == "fechou por telefone"
    assert site.post("/crm/mover", data={"cliente_id": ana.id, "etapa": "inexistente"}).status_code == 400
    assert "à mão" in site.get("/crm").text
    r = site.post("/crm/automatico", data={"cliente_id": ana.id}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200 and db.query(EtapaCRM).count() == 0
    # atendente usa o CRM (é parte dos alertas)
    assert atendente.get("/crm").status_code == 200


def test_webhook_classifica_pelas_regras(db, site):
    import hashlib, hmac, json

    api.salvar_configuracao(db, {"token": "t", "phone_id": "1", "app_secret": "s"})
    ana = _cliente(db, "Ana", "5585981112222")
    corpo = json.dumps({"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "1"}, "messages": [
        {"from": "5585981112222", "id": "wamid.x", "type": "text", "timestamp": str(int(datetime.now().timestamp())),
         "text": {"body": "Tem para abril?"}}]}}]}]}).encode()
    assinatura = "sha256=" + hmac.new(b"s", corpo, hashlib.sha256).hexdigest()
    assert site.post("/webhooks/whatsapp", content=corpo, headers={"content-type": "application/json",
                                                                  "X-Hub-Signature-256": assinatura}).status_code == 200
    r = db.query(RespostaWhatsApp).one()
    assert r.intencao == "duvida" and r.intencao_por == "regras"
    assert {c.cliente.id: c for c in crm.cartoes(db)}[ana.id].etapa == "duvida"
