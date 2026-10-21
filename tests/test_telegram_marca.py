from fastapi.testclient import TestClient

from app import coleta
from app.config import settings
from app.main import _normalizar_canal, app
from app.models import CanalTelegram, Cliente, Promocao, TemplateMensagem
from app.servicos import (
    TEMPLATE_ANTERIOR,
    TEMPLATE_PADRAO,
    montar_mensagem,
    nome_bonito,
    nome_sobrenome,
    preparar_banco,
)


def test_nome_e_sobrenome():
    assert nome_sobrenome("Maria da Silva Oliveira") == "Maria Oliveira"
    assert nome_sobrenome("MARIA DA SILVA OLIVEIRA") == "Maria Oliveira"
    assert nome_sobrenome("joão pereira") == "João Pereira"
    assert nome_sobrenome("Ana") == "Ana"
    assert nome_bonito("ana-luiza DOS santos") == "Ana-Luiza dos Santos"


def test_mensagem_padrao_tem_nome_sobrenome_e_valor_em_reais(db):
    c = Cliente(nome="EDUARDO DE SOUZA LIMA", telefone="5588988870800")
    p = Promocao(origem="NAT", destino="REC", preco=728, companhia="Azul")
    msg = montar_mensagem(TEMPLATE_PADRAO, c, p)
    assert msg.startswith("Olá, Eduardo Lima! ✈️")
    assert "*Natal → Recife*" in msg and "💰 *R$ 728*" in msg and "Agência de Viagens" in msg


def test_modelo_antigo_nao_editado_e_atualizado(db):
    t = db.query(TemplateMensagem).one()
    t.texto = TEMPLATE_ANTERIOR
    db.commit()
    preparar_banco(db)
    db.refresh(t)
    assert t.texto == TEMPLATE_PADRAO

    t.texto = "Meu texto próprio {nome}"
    db.commit()
    preparar_banco(db)
    db.refresh(t)
    assert t.texto == "Meu texto próprio {nome}"  # personalizado pela agência: não mexe


def test_grupos_do_env_viram_cadastro_no_site(db, monkeypatch):
    monkeypatch.setattr(settings, "telegram_canais", ["-1002079295206", "-1002115138954"])
    preparar_banco(db)
    assert {c.identificador for c in db.query(CanalTelegram)} == {"-1002079295206", "-1002115138954"}
    preparar_banco(db)  # não duplica
    assert db.query(CanalTelegram).count() == 2


def test_formatos_aceitos_para_adicionar_grupo():
    assert _normalizar_canal("https://t.me/valevoos") == "@valevoos"
    assert _normalizar_canal("t.me/valevoos/") == "@valevoos"
    assert _normalizar_canal("@valevoos") == "@valevoos"
    assert _normalizar_canal("-1002079295206") == "-1002079295206"
    assert _normalizar_canal("https://t.me/+AbCdEf123") is None  # convite: precisa entrar pelo app
    assert _normalizar_canal("Vale Voos") is None


def test_tela_de_grupos(db, monkeypatch):
    monkeypatch.setattr(settings, "telegram_api_id", "123")
    monkeypatch.setattr(settings, "telegram_api_hash", "abc")
    monkeypatch.setattr(coleta, "listar_grupos_telegram", lambda: [
        {"identificador": "-1002079295206", "nome": "Alertas Nacionais 🇧🇷✈️ - Vale Voos", "membros": 2206},
        {"identificador": "-1002146300553", "nome": "Alertas Lucrativos 🚨💰 - C.A.L", "membros": 900},
    ])
    db.add(CanalTelegram(identificador="-1002079295206", nome="Alertas Nacionais 🇧🇷✈️ - Vale Voos"))
    db.commit()
    with TestClient(app) as site:
        site.post("/login", data={"login": "admin", "senha": "admin123"})
        tela = site.get("/telegram?buscar=1")
        assert "já está sendo lido" in tela.text and "Alertas Lucrativos" in tela.text

        r = site.post("/telegram/adicionar", data={"identificador": "-1002146300553", "nome": "Alertas Lucrativos 🚨💰 - C.A.L"})
        assert "adicionado" in r.text
        novo = db.query(CanalTelegram).filter_by(identificador="-1002146300553").one()

        site.post(f"/telegram/{novo.id}/alternar")
        db.refresh(novo)
        assert not novo.ativo

        ruim = site.post("/telegram/adicionar", data={"identificador": "Vale Voos"})
        assert "Informe o @ do canal" in ruim.text

        site.post(f"/telegram/{novo.id}/excluir")
        assert db.query(CanalTelegram).count() == 1


def test_nomes_em_maiusculas_sao_corrigidos(db):
    from app.servicos import ajustar_nome

    assert ajustar_nome("  EDUARDO   DE SOUZA LIMA ") == "Eduardo de Souza Lima"
    assert ajustar_nome("McDonald Silva") == "McDonald Silva"  # nome já digitado com cuidado: mantém
    db.add(Cliente(nome="ANA COSTA", telefone="5585900000000"))
    db.commit()
    preparar_banco(db)
    assert db.query(Cliente).one().nome == "Ana Costa"


def test_login_do_telegram_vai_do_arquivo_para_o_banco(db, tmp_path, monkeypatch):
    from telethon.sessions import SQLiteSession, StringSession

    from app.collectors import telegram

    caminho = str(tmp_path / "telegram_agencia")
    arquivo = SQLiteSession(caminho)
    arquivo.set_dc(4, "149.154.167.91", 443)
    arquivo.auth_key = __import__("telethon.crypto", fromlist=["AuthKey"]).AuthKey(b"x" * 256)
    arquivo.save()
    arquivo.close()

    monkeypatch.setattr(settings, "telegram_sessao", caminho)
    monkeypatch.setattr(settings, "telegram_api_id", "123")
    monkeypatch.setattr(settings, "telegram_api_hash", "abc")
    assert telegram.sessao_salva() is None
    assert telegram.migrar_arquivo_para_banco() is True

    texto = telegram.sessao_salva()
    recuperada = StringSession(texto)
    assert recuperada.dc_id == 4 and recuperada.auth_key.key == b"x" * 256

    # no servidor não existe o arquivo: o cliente usa o login do banco
    monkeypatch.setattr(settings, "telegram_sessao", str(tmp_path / "nao_existe"))
    cliente = telegram._cliente()
    assert isinstance(cliente.session, StringSession) and cliente.session.auth_key.key == b"x" * 256
