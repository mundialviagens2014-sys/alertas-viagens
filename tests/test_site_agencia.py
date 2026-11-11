"""Leitura das viagens do site da agência (Firestore público) → promoções do MVO. Só leitura."""
import base64
from datetime import date
from io import BytesIO

import httpx
from PIL import Image

from app.collectors import site_agencia
from app.models import Cliente, Interesse, Match, Promocao

HOJE = date(2026, 11, 11)


def _jpeg() -> str:
    saida = BytesIO()
    Image.new("RGB", (40, 30), (20, 60, 140)).save(saida, format="JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(saida.getvalue()).decode()


def _doc(doc_id, **campos):
    def val(v):
        if isinstance(v, bool):
            return {"booleanValue": v}
        if isinstance(v, int):
            return {"integerValue": str(v)}
        return {"stringValue": v}
    return {"name": f"projects/mundial-viagens/databases/(default)/documents/promocoes/{doc_id}",
            "fields": {k: val(v) for k, v in campos.items()}}


def _site(viagens, pedidos):
    def responder(pedido: httpx.Request):
        pedidos.append(pedido)
        assert pedido.method == "GET"          # o MVO nunca escreve no site
        if pedido.url.path.endswith("/promocoes"):
            return httpx.Response(200, json={"documents": viagens()})
        return httpx.Response(200, json={"fields": {"fotos": {"arrayValue": {"values": [{"stringValue": _jpeg()}]}}}})
    return httpx.Client(transport=httpx.MockTransport(responder))


def test_viagem_do_site_vira_promocao_com_foto_e_clientes(db):
    ana = Cliente(nome="Ana Lima", telefone="5585988887777", consentimento=True)
    ana.interesses.append(Interesse(origem="FOR", destino="LIS"))
    db.add(ana)
    db.commit()
    viagens = [_doc("abc123", categoria="pacote", lugar="Lisboa e Porto", descricao="9 noites · Traslados inclusos",
                    dataSaida="2027-03-10", dataVolta="2027-03-19", exibirAte="2027-03-10", preco=8640, parcelas=10),
               _doc("velha", categoria="grupo", lugar="Gramado", dataSaida="2026-10-01", exibirAte="2026-10-01", preco=3000),
               _doc("semvalor", categoria="peregrinacao", lugar="Terra Santa", dataSaida="2027-05-01", preco="A definir")]
    pedidos = []
    with _site(lambda: viagens, pedidos) as http:
        r = site_agencia.coletar(db, hoje=HOJE, cliente_http=http)
    assert (r["no_site"], r["criadas"], r["matches"]) == (2, 2, 1)   # a de outubro já passou
    p = db.query(Promocao).filter_by(link_fonte="https://mundialviagens.com/promo.html?id=abc123").one()
    assert (p.fonte, p.tipo, p.origem, p.destino, p.preco) == ("site", "pacote", "FOR", "LIS", 8640.0)
    assert (p.data_inicio, p.data_fim, p.titulo) == (date(2027, 3, 10), date(2027, 3, 19), "Pacote: Lisboa e Porto")
    assert "em até 10x" in p.resumo and p.imagem is not None
    assert db.query(Match).filter_by(promocao_id=p.id, cliente_id=ana.id).count() == 1
    terra = db.query(Promocao).filter_by(lugar="Terra Santa").one()
    assert terra.preco is None and "A definir" in terra.resumo
    # a lista pede só os campos de texto (as fotos vêm depois, uma por viagem nova)
    assert "mask.fieldPaths=lugar" in str(pedidos[0].url) and "fotos" not in str(pedidos[0].url)

    # mudou o valor no site: o MVO atualiza; tirou do site: sai da fila do MVO
    viagens[0] = _doc("abc123", categoria="pacote", lugar="Lisboa e Porto", dataSaida="2027-03-10",
                      dataVolta="2027-03-19", exibirAte="2027-03-10", preco=7990)
    del viagens[2]
    with _site(lambda: viagens, []) as http:
        r = site_agencia.coletar(db, hoje=HOJE, cliente_http=http)
    assert (r["criadas"], r["atualizadas"], r["retiradas"]) == (0, 1, 1)
    db.expire_all()
    assert db.get(Promocao, p.id).preco == 7990.0 and terra.status == "descartada"


def test_valores_do_site():
    assert site_agencia._preco(8640) == 8640.0
    assert site_agencia._preco("R$ 8.640,00") == 8640.0
    assert site_agencia._preco("A definir") is None
    assert site_agencia._destino("Lisboa e Porto") == "LIS"
    assert site_agencia._destino("Curitiba-PR") == "CWB"
    assert site_agencia._destino("Maragogi - AL") == "MCZ"
    assert site_agencia._destino("Temporada de Inverno em Gramado") == "POA"
    assert site_agencia._destino("Lugar que não existe") is None
