"""Leitura de promoções por IA (Gemini): imagens e textos difíceis viram promoções de passagem, hotel, pacote ou
cruzeiro; cota diária e fila de pendências; a imagem fica guardada e aparece na fila e na página do cliente."""
import base64
import io
import json
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import ia, leitura
from app.config import settings
from app.main import app
from app.models import Cliente, ImagemPromocao, Interesse, LeituraPendente, Match, Promocao
from app.servicos import montar_mensagem, template_padrao


def _imagem(cor="red", tamanho=(1600, 1200)) -> bytes:
    saida = io.BytesIO()
    Image.new("RGB", tamanho, cor).save(saida, format="PNG")
    return saida.getvalue()


def _resposta_gemini(dados: dict) -> httpx.Response:
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(dados)}]}}]})


@pytest.fixture()
def gemini(monkeypatch):
    """Finge o Gemini: a próxima resposta vem de `respostas` (lista) ou é um 429 quando `cota_estourada`."""
    monkeypatch.setattr(settings, "gemini_api_key", "chave-teste")
    monkeypatch.setattr(settings, "gemini_max_por_dia", 100)
    monkeypatch.setattr(settings, "gemini_max_por_rodada", 10)
    estado = {"respostas": [], "chamadas": [], "cota_estourada": False}

    def transporte(request: httpx.Request):
        estado["chamadas"].append(json.loads(request.content))
        assert request.headers["x-goog-api-key"] == "chave-teste"
        if estado["cota_estourada"]:
            return httpx.Response(429, json={"error": {"message": "quota exceeded"}})
        return _resposta_gemini(estado["respostas"].pop(0))

    cliente = httpx.Client(transport=httpx.MockTransport(transporte))
    original = ia.ler

    def ler_fingido(texto=None, imagem=None, mime="image/jpeg", cliente_http=None, hoje=None):
        return original(texto=texto, imagem=imagem, mime=mime, cliente_http=cliente, hoje=hoje or date(2026, 11, 4))

    monkeypatch.setattr(ia, "ler", ler_fingido)
    yield estado
    cliente.close()


@pytest.fixture()
def site(db):
    with TestClient(app) as cliente:
        cliente.post("/login", data={"login": "admin", "senha": "admin123"})
        yield cliente


def _clientes(db):
    ana = Cliente(nome="Ana Costa", telefone="5585981112222", consentimento=True)
    ana.interesses.append(Interesse(origem="FOR", destino="LIS"))
    ana.interesses.append(Interesse(origem="FOR", destino="REC", preco_maximo=1500))
    db.add(ana)
    db.commit()
    return ana


HOTEL = {"eh_promocao": True, "tipo": "hotel", "titulo": "Resort Vila Galé com tudo incluído", "destino": "Recife",
         "preco_reais": 2890, "data_inicio": "2027-01-10", "data_fim": "2027-01-15", "datas_texto": "10 a 15 de janeiro",
         "companhia": "Vila Galé", "resumo": "5 diárias all inclusive, em até 10x", "confianca": 0.9}
PASSAGEM = {"eh_promocao": True, "tipo": "passagem", "origem": "Fortaleza", "destino": "Lisboa", "preco_reais": 2389,
            "data_inicio": "2027-03-01", "data_fim": "2027-06-30", "companhia": "TAP", "ida_e_volta": "ida_volta",
            "confianca": 0.95}


def test_interpretar_resposta_do_gemini():
    lida = ia.interpretar(PASSAGEM, hoje=date(2026, 11, 4))
    assert lida.eh_promocao and lida.tipo == "passagem" and lida.preco == 2389 and lida.tipo_viagem == "ida_volta"
    dados = ia.para_extraida(lida)
    assert dados.origem == "FOR" and dados.destino == "LIS" and dados.completa and dados.data_fim == date(2027, 6, 30)
    # ano passado por engano: empurra para o próximo
    lida = ia.interpretar(dict(PASSAGEM, data_inicio="2026-03-01", data_fim="2026-04-30"), hoje=date(2026, 11, 4))
    assert lida.data_inicio == date(2027, 3, 1) and lida.data_fim == date(2027, 4, 30)
    # hotel: título, lugar e destino resolvido para a cidade
    dados = ia.para_extraida(ia.interpretar(HOTEL))
    assert dados.tipo == "hotel" and dados.lugar == "Recife" and dados.destino == "REC" and dados.origem is None
    assert dados.titulo == "Resort Vila Galé com tudo incluído" and dados.completa
    # lixo: campos errados não derrubam
    lida = ia.interpretar({"eh_promocao": "sim", "tipo": "x", "preco_reais": "abc", "data_inicio": 5, "confianca": 7})
    assert lida.eh_promocao and lida.tipo == "passagem" and lida.preco is None and lida.data_inicio is None
    assert lida.confianca == 1.0
    assert ia.vale_a_pena_ler("Bom dia grupo!") is False
    assert ia.vale_a_pena_ler("Pacote Gramado 4 noites com café por R$ 1.890 por pessoa") is True


def test_imagem_vira_promocao_de_hotel_e_casa_pelo_destino(db, gemini):
    ana = _clientes(db)
    gemini["respostas"].append(HOTEL)
    r = leitura.processar(db, fonte="telegram", fonte_detalhe="Grupo Hotéis", link_fonte=None, imagem=_imagem())
    assert r.motivo == "criada" and r.matches == 1
    p = r.promocao
    assert p.tipo == "hotel" and p.titulo.startswith("Resort") and p.lugar == "Recife" and p.destino == "REC"
    assert p.imagem is not None and p.imagem.mime == "image/jpeg"
    assert Image.open(io.BytesIO(p.imagem.dados)).size[0] <= 1280  # reduzida antes de guardar
    assert ia.leituras_hoje(db) == 1
    # o pedido ao Gemini levou a imagem e a instrução, nunca dados de cliente
    pedido = json.dumps(gemini["chamadas"][0])
    assert "inline_data" in pedido and "Ana" not in pedido and "981112222" not in pedido
    # mensagem para a cliente: o trecho vira o nome da oferta e o resumo entra
    msg = montar_mensagem(template_padrao(db).texto, ana, p)
    assert "*Resort Vila Galé com tudo incluído*" in msg and "🏨 Hotel · Recife" in msg
    assert "📝 5 diárias all inclusive" in msg and "→" not in msg.split("Olá")[1].split("💰")[0]
    assert "o destino que você procura" in msg


def test_texto_de_passagem_nao_gasta_ia_e_texto_dificil_gasta(db, gemini):
    _clientes(db)
    r = leitura.processar(db, fonte="telegram", fonte_detalhe="G", link_fonte=None,
                          texto="FOR → LIS R$ 2.389 ida e volta TAP em março/27")
    assert r.motivo == "criada" and gemini["chamadas"] == []
    gemini["respostas"].append(dict(PASSAGEM, destino="Porto", preco_reais=2790))
    r = leitura.processar(db, fonte="telegram", fonte_detalhe="G", link_fonte=None,
                          texto="Olha essa pra quem quer conhecer Portugal: Fortaleza pra cidade do Porto 2.790 reais!!")
    assert r.motivo == "criada" and r.promocao.destino == "OPO" and len(gemini["chamadas"]) == 1
    assert leitura.processar(db, fonte="telegram", fonte_detalhe="G", link_fonte=None, texto="bom dia").motivo == "incompleta"
    assert len(gemini["chamadas"]) == 1


def test_sem_cota_fica_pendente_e_e_lida_depois(db, gemini):
    _clientes(db)
    gemini["cota_estourada"] = True
    orcamento = leitura.Orcamento()
    r = leitura.processar(db, fonte="telegram", fonte_detalhe="G", link_fonte="https://t.me/g/1", imagem=_imagem(),
                          orcamento=orcamento)
    assert r.motivo == "pendente" and orcamento.parou
    r = leitura.processar(db, fonte="telegram", fonte_detalhe="G", link_fonte="https://t.me/g/2", imagem=_imagem("blue"),
                          orcamento=orcamento)
    assert r.motivo == "pendente" and len(gemini["chamadas"]) == 1  # a rodada parou de chamar a IA
    assert leitura.pendentes(db) == 2
    # próxima rodada, com cota: as duas são lidas, da mais antiga para a mais nova
    gemini["cota_estourada"] = False
    gemini["respostas"] += [HOTEL, dict(HOTEL, titulo="Pousada do Sol", preco_reais=990)]
    resumo = leitura.reler_pendentes(db, leitura.Orcamento())
    assert resumo["criadas"] == 2 and leitura.pendentes(db) == 0
    assert [p.titulo for p in db.query(Promocao).order_by(Promocao.id)] == ["Resort Vila Galé com tudo incluído", "Pousada do Sol"]


def test_limite_diario_do_sistema(db, gemini, monkeypatch):
    monkeypatch.setattr(settings, "gemini_max_por_dia", 1)
    gemini["respostas"] += [HOTEL, HOTEL]
    assert leitura.processar(db, fonte="api", fonte_detalhe=None, link_fonte=None, imagem=_imagem()).motivo == "criada"
    r = leitura.processar(db, fonte="api", fonte_detalhe=None, link_fonte=None, imagem=_imagem("blue"))
    assert r.motivo == "pendente" and len(gemini["chamadas"]) == 1


def test_sem_chave_as_imagens_sao_ignoradas(db, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    r = leitura.processar(db, fonte="telegram", fonte_detalhe="G", link_fonte=None, imagem=_imagem())
    assert r.motivo == "sem_ia" and leitura.pendentes(db) == 0


def test_api_recebe_imagem_do_bot(db, gemini, site):
    _clientes(db)
    gemini["respostas"].append(HOTEL)
    r = site.post("/api/promocoes", json={"imagem_base64": base64.b64encode(_imagem()).decode(), "mime": "image/png",
                                           "fonte_detalhe": "WhatsApp · Promoções Nordeste"},
                  headers={"X-API-Key": "chave-teste"})
    assert r.status_code == 200 and r.json()["resultado"] == "criada" and r.json()["clientes"] == 1
    assert db.query(Promocao).one().fonte == "api"
    assert site.post("/api/promocoes", json={"texto": ""}, headers={"X-API-Key": "chave-teste"}).status_code == 400
    assert site.post("/api/promocoes", json={"imagem_base64": "!!!"}, headers={"X-API-Key": "chave-teste"}).status_code == 400


def test_telas_mostram_tipo_e_imagem(db, gemini, site):
    ana = _clientes(db)
    gemini["respostas"].append(HOTEL)
    leitura.processar(db, fonte="telegram", fonte_detalhe="Grupo Hotéis", link_fonte=None, imagem=_imagem())
    promo = db.query(Promocao).one()
    fila = site.get("/").text
    assert "Resort Vila Galé" in fila and f"/promocoes/{promo.id}/imagem" in fila and "Hotel" in fila
    assert "Imagem com a logo" not in fila  # a imagem com a logo é só para passagem
    r = site.get(f"/promocoes/{promo.id}/imagem")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    lista = site.get("/promocoes?categoria=hotel").text
    assert "Resort Vila Galé" in lista
    assert "Resort Vila Galé" not in site.get("/promocoes?categoria=passagem").text.split('<form class="filtros"')[-1]
    assert "Resort Vila Galé" in site.get("/promocoes?q=vila gale").text
    # página do cliente: grupo "Hotel · Recife", imagem pelo link do cliente, e só dele
    with TestClient(app) as visitante:
        html = visitante.get(f"/para-voce/{ana.token_pagina}").text
        assert "🏨 Hotel · Recife" in html and "Resort Vila Galé" in html and "5 diárias" in html
        assert f"/para-voce/{ana.token_pagina}/imagem/{promo.id}" in html
        assert visitante.get(f"/para-voce/{ana.token_pagina}/imagem/{promo.id}").status_code == 200
        assert visitante.get(f"/para-voce/{'x' * 24}/imagem/{promo.id}").status_code == 404
        assert visitante.get(f"/para-voce/{ana.token_pagina}/imagem/9999").status_code == 404
    # página do cliente na equipe e histórico mostram o nome da oferta
    assert "Resort Vila Galé" in site.get(f"/clientes/{ana.id}").text
    assert "Hotel · Recife" in site.get("/?ver=clientes").text


def test_nova_promocao_com_imagem_e_tipo(db, gemini, site, tmp_path, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "PASTA_IMPORTACOES", tmp_path)
    _clientes(db)
    gemini["respostas"].append(HOTEL)
    r = site.post("/promocoes/ler-imagem", files={"imagem": ("promo.png", _imagem(), "image/png")})
    assert r.status_code == 200 and 'value="hotel" checked' in r.text and "Resort Vila Galé" in r.text
    token = r.text.split('name="imagem_token" value="')[1].split('"')[0]
    assert token.endswith(".jpg") and (tmp_path / token).exists()
    assert site.get(f"/promocoes/imagem-temporaria/{token}").status_code == 200
    r = site.post("/promocoes/nova", data={"tipo": "hotel", "titulo": "Resort Vila Galé", "lugar": "Recife",
                                           "resumo": "5 diárias", "preco": "2.890", "inicio": "2027-01-10",
                                           "fim": "2027-01-15", "companhia": "Vila Galé", "imagem_token": token},
                  follow_redirects=False)
    assert r.status_code == 303
    promo = db.query(Promocao).one()
    assert promo.tipo == "hotel" and promo.destino == "REC" and promo.imagem is not None and not (tmp_path / token).exists()
    assert len(promo.matches) == 1
    # sem título não salva
    r = site.post("/promocoes/nova", data={"tipo": "pacote", "preco": "100"})
    assert "Dê um nome para a oferta" in r.text
    # sem a chave, a tela avisa e o envio de imagem é recusado
    monkeypatch.setattr(settings, "gemini_api_key", "")
    assert "GEMINI_API_KEY" in site.get("/promocoes/nova").text
    r = site.post("/promocoes/ler-imagem", files={"imagem": ("promo.png", _imagem(), "image/png")}, follow_redirects=True)
    assert "GEMINI_API_KEY" in r.text


def test_imagens_antigas_sao_apagadas(db, gemini):
    from datetime import datetime, timedelta

    from app.servicos import limpar_imagens_antigas

    gemini["respostas"] += [HOTEL, dict(HOTEL, titulo="Outra", preco_reais=500)]
    leitura.processar(db, fonte="api", fonte_detalhe=None, link_fonte=None, imagem=_imagem())
    leitura.processar(db, fonte="api", fonte_detalhe=None, link_fonte=None, imagem=_imagem("blue"))
    antiga = db.query(Promocao).filter_by(titulo="Outra").one()
    antiga.coletada_em = datetime.now() - timedelta(days=40)
    db.commit()
    assert limpar_imagens_antigas(db) == 1
    assert db.query(ImagemPromocao).count() == 1 and db.query(Promocao).count() == 2


def test_duplicada_de_hotel(db, gemini):
    gemini["respostas"] += [HOTEL, HOTEL]
    assert leitura.processar(db, fonte="api", fonte_detalhe=None, link_fonte=None, imagem=_imagem()).motivo == "criada"
    assert leitura.processar(db, fonte="api", fonte_detalhe=None, link_fonte=None, imagem=_imagem()).motivo == "duplicada"
    assert db.query(LeituraPendente).count() == 0 and db.query(Match).count() == 0
