"""Leitura do print de uma emissão pela IA: preenche o formulário, não grava nada."""
from fastapi.testclient import TestClient

from app import ia
from app.main import app
from app.models_mvo import Agencia, Emissao

LIDO = {"data_pedido": "2026-11-04", "usuario": "UNIVERSO TURISMO", "tipo_voo": "ida_volta", "tipo_emissao": "SIMPLES",
        "programa": "SMILES", "companhia": "GOL", "origem": "GRU", "destino": "FOR", "data_ida": "2026-12-30",
        "data_volta": "2027-01-15", "pontos": 88500, "valor_passagem": 1460.25, "taxa_embarque": 95.71, "taxa_bagagem": 0,
        "qtd_passageiros": 1, "passageiros": [], "voos": "IDA: G39178 - VOLTA: G31559",
        "observacao": "Programas: SMILES, SMILES - OIFXN106JEZ9R0I"}


def test_ler_print_preenche_os_campos_sem_gravar(db, monkeypatch):
    db.add(Agencia(nome="Universo Turismo"))
    db.commit()
    monkeypatch.setattr(ia, "configurada", lambda: True)
    monkeypatch.setattr(ia, "ler_emissao", lambda imagem, mime="image/jpeg", **k: dict(LIDO))
    with TestClient(app) as site:
        site.post("/login", data={"login": "admin", "senha": "admin123"})
        assert "Ler o print da emissão" in site.get("/emissoes/nova").text
        r = site.post("/emissoes/ler-imagem", files={"imagem": ("print.png", b"\x89PNG falso", "image/png")})
    assert r.status_code == 200
    j = r.json()
    c = j["campos"]
    assert (c["origem"], c["destino"], c["tipo_viagem"], c["pontos"]) == ("GRU", "FOR", "ida_volta", "88500")
    assert c["preco_venda"] == "1460,25" and c["taxa_embarque"] == "95,71" and c["taxa_bagagem"] == ""
    assert c["data_emissao"] == "2026-11-04" and c["programa"] == "SMILES"
    assert "G39178" in c["observacao"] and "SIMPLES" in c["observacao"]
    agencia = db.query(Agencia).one()
    assert j["agencia_id"] == str(agencia.id)   # "UNIVERSO TURISMO" achou "Universo Turismo"
    assert db.query(Emissao).count() == 0     # nada gravado: a equipe confere e registra


def test_ler_print_sem_chave_da_ia_avisa(db, monkeypatch):
    monkeypatch.setattr(ia, "configurada", lambda: False)
    with TestClient(app) as site:
        site.post("/login", data={"login": "admin", "senha": "admin123"})
        r = site.post("/emissoes/ler-imagem", files={"imagem": ("print.png", b"x", "image/png")})
    assert r.status_code == 400 and "GEMINI_API_KEY" in r.json()["erro"]


def test_esquemas_da_ia_nao_tem_opcao_vazia():
    """O Gemini recusa (erro 400) um esquema com opção vazia num enum: foi o que fez a leitura do print falhar."""
    def enums(no):
        if isinstance(no, dict):
            if "enum" in no:
                yield no["enum"]
            for v in no.values():
                yield from enums(v)
    for esquema in (ia.ESQUEMA, ia.ESQUEMA_EMISSAO, ia.ESQUEMA_INTENCAO):
        for lista in enums(esquema):
            assert all(str(x).strip() for x in lista), lista
