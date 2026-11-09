"""Envio de mensagens pela API oficial do WhatsApp (Meta Cloud API), com o número da agência que fala com os
clientes.

O que a equipe preenche em Configurações → WhatsApp automático: o token permanente, o ID do número (Phone number
ID), o ID da conta (WABA ID), o token de verificação do webhook e, opcionalmente, o segredo do app (para conferir
a assinatura dos webhooks). Tudo fica no banco, cifrado com a mesma chave dos logins das contas de milhas.

Regras da Meta que o código respeita:
- fora da janela de 24 h depois da última mensagem do cliente, só vai mensagem de modelo aprovado (template);
  promoção e confirmação de cadastro são modelos; a resposta da equipe dentro da janela é texto livre;
- cada mensagem de marketing é cobrada; o sistema conta os envios e respeita um limite por dia e por cliente;
- quem responde SAIR (ou parecido) deixa de receber: o cadastro perde a autorização.
"""
import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import cripto, crm
from .config import settings
from .models import (
    Cliente,
    ConfirmacaoCadastro,
    EnvioWhatsApp,
    EstadoColetor,
    ImagemPromocao,
    Match,
    Promocao,
    RespostaWhatsApp,
)
from .servicos import (
    preco_reais,
    descrever_trecho,
    formatar_periodo,
    formatar_valor,
    gravar_estado,
    ler_estado,
    link_pagina,
    nome_sobrenome,
    normalizar_telefone,
    periodo_extenso,
    texto_trechos,
    trechos_da_confirmacao,
)

log = logging.getLogger(__name__)
GRAPH = "https://graph.facebook.com/v21.0"
PREFIXO = "whatsapp_api:"
CIFRADOS = ("token", "app_secret")
CAMPOS = ("token", "phone_id", "waba_id", "verify_token", "app_secret", "modelo_promocao", "modelo_confirmacao",
          "idioma", "variaveis_promocao", "variaveis_confirmacao", "automatico", "max_por_dia", "max_por_cliente_dia",
          "contato", "preco_promocao", "preco_confirmacao", "botao_atendimento")
PADROES = {"idioma": "pt_BR", "variaveis_promocao": "nome, trecho, valor, datas, pagina, contato",
           "variaveis_confirmacao": "nome, trechos, pagina", "automatico": "0", "max_por_dia": "200",
           "max_por_cliente_dia": "3", "preco_promocao": "0.35", "preco_confirmacao": "0.04",
           "botao_atendimento": "0"}
VARIAVEIS = {
    "nome": "primeiro nome e sobrenome do cliente", "primeiro_nome": "só o primeiro nome",
    "trecho": "Fortaleza → Lisboa, ou o nome da oferta", "origem": "código da origem (FOR)", "destino": "código do destino (GIG)",
    "valor": "R$ 2.389 ou 24.000 milhas", "valor_numero": "só o número em reais (2.389), para modelo que já escreve R$",
    "datas": "as datas",
    "companhia": "companhia ou operadora", "resumo": "o que inclui", "pagina": "link da página do cliente",
    "contato": "WhatsApp da agência para responder (Configurações → Página do cliente)", "agencia": "nome da agência",
    "trechos": "os trechos escolhidos no cadastro (confirmação)", "destinos": "os destinos escolhidos (confirmação)",
}
RE_SAIR = re.compile(r"^\s*(sair|parar|pare|stop|cancelar|descadastrar|remover|n[aã]o quero( mais)?|unsubscribe)\b", re.I)
MEDIA_VALIDADE = timedelta(days=28)


class APINaoConfigurada(RuntimeError):
    pass


class FalhaEnvio(RuntimeError):
    pass


# ---------------------------------------------------------------- configuração


def configuracao(db: Session) -> dict[str, str]:
    """Tudo o que foi preenchido em Configurações (uma consulta só), com os padrões no que ficou em branco."""
    guardado = dict(db.execute(
        select(EstadoColetor.chave, EstadoColetor.valor).where(EstadoColetor.chave.in_([PREFIXO + c for c in CAMPOS]))
    ).all())
    conf = {}
    for campo in CAMPOS:
        valor = guardado.get(PREFIXO + campo) or ""
        if campo in CIFRADOS and valor:
            try:
                valor = cripto.decifrar(valor)
            except cripto.SenhaIlegivel:
                valor = ""
        conf[campo] = valor or PADROES.get(campo, "")
    return conf


def salvar_configuracao(db: Session, dados: dict[str, str]) -> None:
    for campo in CAMPOS:
        if campo not in dados:
            continue
        valor = (dados.get(campo) or "").strip()
        if campo == "contato" and valor:
            valor = normalizar_telefone(valor)
        if campo.startswith("preco_") and valor:
            valor = _decimal(valor, PADROES[campo])
        if campo in CIFRADOS:
            if not valor:  # campo em branco na tela: mantém o que já estava (o token nunca é mostrado)
                continue
            valor = cripto.cifrar(valor) or ""
        gravar_estado(db, PREFIXO + campo, valor)


def _decimal(valor: str, padrao: str) -> str:
    """Preço em reais digitado como 0,35 ou 0.35 → "0.35"."""
    try:
        return f"{max(0.0, float(valor.replace(',', '.'))):.4f}".rstrip("0").rstrip(".") or "0"
    except ValueError:
        return padrao


def apagar_segredos(db: Session) -> None:
    """Tira o token e o segredo do app do banco e desliga o envio automático (token vazado, troca de número...)."""
    for campo in CIFRADOS:
        gravar_estado(db, PREFIXO + campo, "")
    gravar_estado(db, PREFIXO + "automatico", "0")


def configurada(db: Session) -> bool:
    """Token e ID do número preenchidos (sem decifrar: é chamada em toda página, para mostrar os botões)."""
    chaves = {PREFIXO + "token", PREFIXO + "phone_id"}
    achados = {c for c, v in db.execute(select(EstadoColetor.chave, EstadoColetor.valor)
                                        .where(EstadoColetor.chave.in_(chaves))).all() if v}
    return achados == chaves


def automatico_ligado(db: Session) -> bool:
    return configurada(db) and ler_estado(db, PREFIXO + "automatico") == "1"


# ---------------------------------------------------------------- chamadas à Meta


def _cabecalhos(conf: dict) -> dict:
    return {"Authorization": f"Bearer {conf['token']}"}


def _erro_meta(r: httpx.Response) -> str:
    try:
        e = r.json().get("error", {})
        return f"{e.get('message', r.text[:200])} (código {e.get('code', r.status_code)})"
    except ValueError:
        return f"HTTP {r.status_code}: {r.text[:200]}"


def enviar_bruto(conf: dict, corpo: dict, cliente_http: httpx.Client | None = None) -> str:
    """Manda a mensagem e devolve o id (wamid). Levanta FalhaEnvio com o motivo da Meta."""
    proprio = cliente_http is None
    cliente_http = cliente_http or httpx.Client(timeout=30)
    try:
        r = cliente_http.post(f"{GRAPH}/{conf['phone_id']}/messages", json={"messaging_product": "whatsapp", **corpo},
                              headers=_cabecalhos(conf))
    except httpx.HTTPError as erro:
        raise FalhaEnvio(f"sem resposta da Meta: {erro}") from erro
    finally:
        if proprio:
            cliente_http.close()
    if r.status_code >= 400:
        raise FalhaEnvio(_erro_meta(r))
    try:
        return r.json()["messages"][0]["id"]
    except (KeyError, IndexError, ValueError):
        return ""


def enviar_imagem_para_meta(db: Session, conf: dict, imagem: ImagemPromocao,
                            cliente_http: httpx.Client | None = None, guardar: bool = True) -> str | None:
    """Sobe a imagem da promoção para a Meta (uma vez; o id vale cerca de 30 dias) e devolve o media id."""
    if imagem.meta_media_id and imagem.meta_media_em and datetime.now() - imagem.meta_media_em < MEDIA_VALIDADE:
        return imagem.meta_media_id
    proprio = cliente_http is None
    cliente_http = cliente_http or httpx.Client(timeout=60)
    try:
        r = cliente_http.post(f"{GRAPH}/{conf['phone_id']}/media", headers=_cabecalhos(conf),
                              data={"messaging_product": "whatsapp", "type": imagem.mime},
                              files={"file": ("promocao.jpg", imagem.dados, imagem.mime)})
    except httpx.HTTPError as erro:
        log.warning("Imagem não subiu para a Meta: %s", erro)
        return None
    finally:
        if proprio:
            cliente_http.close()
    if r.status_code >= 400:
        log.warning("Imagem recusada pela Meta: %s", _erro_meta(r))
        return None
    media_id = (r.json() or {}).get("id")
    if media_id and guardar:
        imagem.meta_media_id, imagem.meta_media_em = media_id, datetime.now()
        db.commit()
    return media_id


CAMINHO_FALAR = "/falar/"


def sufixo_botao_atendimento(m: Match) -> str | None:
    """Final do link do botão "Falar com um atendente": o código da página do cliente e a promoção."""
    from .models import novo_token

    if not m.cliente.token_pagina:
        m.cliente.token_pagina = novo_token()
    return f"{m.cliente.token_pagina}.{m.promocao_id}"


def _modelo_com_imagem(db: Session, modelo: str) -> bool | None:
    """O que já se sabe do modelo: True = tem cabeçalho de imagem, False = não tem, None = ainda não se sabe."""
    valor = db.scalar(select(EstadoColetor.valor).where(EstadoColetor.chave == f"{PREFIXO}cabecalho:{modelo}"))
    return {"imagem": True, "nenhum": False}.get(valor or "")


def _guardar_modelo_com_imagem(db: Session, modelo: str, imagem: bool) -> None:
    chave, valor = f"{PREFIXO}cabecalho:{modelo}", "imagem" if imagem else "nenhum"
    item = db.get(EstadoColetor, chave)
    if item:
        item.valor = valor
    else:
        db.add(EstadoColetor(chave=chave, valor=valor))


def _imagem_do_envio(db: Session, conf: dict, promo: Promocao, cliente_http: httpx.Client | None) -> str | None:
    """Media id da imagem da promoção; sem imagem própria, sobe a "Imagem com a logo" gerada no servidor."""
    if promo.imagem:
        return enviar_imagem_para_meta(db, conf, promo.imagem, cliente_http)
    from . import imagem_promocao

    gerada = ImagemPromocao(dados=imagem_promocao.gerar(promo), mime="image/jpeg")
    return enviar_imagem_para_meta(db, conf, gerada, cliente_http, guardar=False)


# ---------------------------------------------------------------- variáveis dos modelos


def _variaveis(texto: str) -> list[str]:
    """Nomes das variáveis do MVO usadas (aceita "nome_na_meta=variavel")."""
    return [nosso for _, nosso in _variaveis_do_modelo(texto)]


def valores_promocao(db: Session, cliente: Cliente, promo: Promocao, site: str, conf: dict) -> dict[str, str]:
    from .servicos import whatsapp_agencia

    contato = conf.get("contato") or whatsapp_agencia(db)
    return {
        "nome": nome_sobrenome(cliente.nome), "primeiro_nome": nome_sobrenome(cliente.nome).split(" ")[0],
        "trecho": descrever_trecho(promo), "valor": formatar_valor(promo),
        "origem": (promo.origem or "").upper() or "-", "destino": (promo.destino or promo.lugar or "").upper() or "-",
        "valor_numero": _numero_reais(preco_reais(promo)),
        "datas": formatar_periodo(promo.data_inicio, promo.data_fim, promo.datas_texto),
        "companhia": promo.companhia or "-", "resumo": promo.resumo or "-", "pagina": link_pagina(site, cliente) or "-",
        "contato": f"https://wa.me/{contato}" if contato else "-", "agencia": settings.agencia_nome,
    }


def valores_confirmacao(db: Session, cliente: Cliente, conf_cadastro: ConfirmacaoCadastro, site: str, conf: dict) -> dict:
    from .servicos import _destinos, _juntar, whatsapp_agencia

    trechos = trechos_da_confirmacao(conf_cadastro)
    contato = conf.get("contato") or whatsapp_agencia(db)
    primeiro = trechos[0] if trechos else None
    return {
        "nome": nome_sobrenome(cliente.nome), "primeiro_nome": nome_sobrenome(cliente.nome).split(" ")[0],
        "trechos": texto_trechos(trechos).replace("*", "").replace("\n", " · ") or "os destinos escolhidos",
        "destinos": _juntar(_destinos(trechos)) or "os destinos escolhidos",
        "datas": periodo_extenso(primeiro.periodo_inicio, primeiro.periodo_fim) if primeiro else "as datas escolhidas",
        "pagina": link_pagina(site, cliente) or "-", "contato": f"https://wa.me/{contato}" if contato else "-",
        "agencia": settings.agencia_nome,
    }


def _numero_reais(valor: float | None) -> str:
    if not valor:
        return "-"
    return f"{valor:,.0f}".replace(",", ".") if float(valor).is_integer() or valor >= 100 else f"{valor:.2f}".replace(".", ",")


def _variaveis_do_modelo(texto: str) -> list[tuple[str | None, str]]:
    """Lê a lista de variáveis de Configurações. "nome, trecho" = modelo com {{1}}, {{2}} (na ordem);
    "nome_cliente=nome, origem=origem" = modelo com variáveis com nome na Meta ({{nome_cliente}} ← nome do cliente)."""
    pares = []
    for item in texto.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            na_meta, nosso = (x.strip() for x in item.split("=", 1))
            pares.append((na_meta, nosso.lower()))
        else:
            pares.append((None, item.lower()))
    return pares


def _limpar(valor: str) -> str:
    """A Meta não aceita quebras de linha nem muitos espaços nos parâmetros."""
    return re.sub(r"\s+", " ", str(valor or "-")).strip()[:1000] or "-"


def corpo_modelo(telefone: str, modelo: str, idioma: str, ordem: list[str], valores: dict, media_id: str | None,
                 botao: str | None = None) -> dict:
    componentes = []
    if media_id:
        componentes.append({"type": "header", "parameters": [{"type": "image", "image": {"id": media_id}}]})
    if ordem:
        parametros = []
        for item in ordem:
            na_meta, nosso = item if isinstance(item, tuple) else (None, item)
            parametro = {"type": "text", "text": _limpar(valores.get(nosso, "-"))}
            if na_meta:
                parametro["parameter_name"] = na_meta
            parametros.append(parametro)
        componentes.append({"type": "body", "parameters": parametros})
    if botao:   # botão de URL com final variável: https://<site>/falar/{{1}} → leva ao 0400 e avisa o CRM
        componentes.append({"type": "button", "sub_type": "url", "index": "0",
                            "parameters": [{"type": "text", "text": botao}]})
    return {"to": telefone, "type": "template",
            "template": {"name": modelo, "language": {"code": idioma or "pt_BR"}, "components": componentes}}


# ---------------------------------------------------------------- limites


def enviados_hoje(db: Session, cliente_id: int | None = None) -> int:
    hoje = datetime.combine(datetime.now().date(), datetime.min.time())
    consulta = select(func.count(EnvioWhatsApp.id)).where(EnvioWhatsApp.criado_em >= hoje,
                                                           EnvioWhatsApp.status != "falhou",
                                                           EnvioWhatsApp.tipo.in_(("promocao", "confirmacao")))
    if cliente_id:
        consulta = consulta.where(EnvioWhatsApp.cliente_id == cliente_id)
    return db.scalar(consulta) or 0


def _inteiro(valor: str, padrao: int) -> int:
    try:
        return max(0, int(valor))
    except (TypeError, ValueError):
        return padrao


def pode_enviar(db: Session, conf: dict, cliente: Cliente) -> str | None:
    """Motivo para não mandar agora (None = pode)."""
    if not cliente.consentimento:
        return "cliente sem autorização"
    if not cliente.ativo:
        return "cliente inativo"
    if enviados_hoje(db) >= _inteiro(conf["max_por_dia"], 200):
        return "limite diário de envios atingido"
    if enviados_hoje(db, cliente.id) >= _inteiro(conf["max_por_cliente_dia"], 3):
        return "este cliente já recebeu o máximo de hoje"
    return None


# ---------------------------------------------------------------- envios


@dataclass
class ResultadoEnvio:
    ok: bool
    motivo: str = ""
    envio: EnvioWhatsApp | None = None


def enviar_promocao(db: Session, m: Match, site: str, quem: str, automatico: bool = False,
                    cliente_http: httpx.Client | None = None) -> ResultadoEnvio:
    conf = configuracao(db)
    if not configurada(db):
        return ResultadoEnvio(False, "API do WhatsApp não configurada")
    if not conf["modelo_promocao"]:
        return ResultadoEnvio(False, "modelo de promoção não informado em Configurações")
    motivo = pode_enviar(db, conf, m.cliente)
    if motivo:
        return ResultadoEnvio(False, motivo)
    telefone = normalizar_telefone(m.cliente.telefone)
    valores = valores_promocao(db, m.cliente, m.promocao, site, conf)
    ordem = _variaveis_do_modelo(conf["variaveis_promocao"])
    # modelo com cabeçalho de imagem: a Meta exige imagem em todo envio. Sem imagem própria (ex.: Google Flights),
    # vai a "Imagem com a logo" gerada aqui. Se o modelo não tiver cabeçalho, a Meta responde 132012 e repetimos sem.
    botao = sufixo_botao_atendimento(m) if conf.get("botao_atendimento") == "1" else None
    com_imagem = _modelo_com_imagem(db, conf["modelo_promocao"])
    media_id = _imagem_do_envio(db, conf, m.promocao, cliente_http) if com_imagem is not False else None
    envio = EnvioWhatsApp(cliente_id=m.cliente_id, match_id=m.id, telefone=telefone,
                          tipo="promocao", modelo=conf["modelo_promocao"], automatico=automatico, enviado_por=quem)
    db.add(envio)
    try:
        try:
            envio.wamid = enviar_bruto(conf, corpo_modelo(telefone, conf["modelo_promocao"], conf["idioma"], ordem, valores,
                                                          media_id, botao), cliente_http)
        except FalhaEnvio as erro:
            if "132012" not in str(erro):
                raise
            # formato do cabeçalho não bateu: tenta do outro jeito (com imagem ↔ sem imagem) e guarda o que funcionou
            tentou_com_imagem = media_id is not None
            media_id = None if tentou_com_imagem else _imagem_do_envio(db, conf, m.promocao, cliente_http)
            if not tentou_com_imagem and media_id is None:   # a imagem não subiu: repetir seria igual
                raise
            envio.wamid = enviar_bruto(conf, corpo_modelo(telefone, conf["modelo_promocao"], conf["idioma"], ordem, valores,
                                                          media_id, botao), cliente_http)
        _guardar_modelo_com_imagem(db, conf["modelo_promocao"], media_id is not None)
    except FalhaEnvio as erro:
        envio.status, envio.erro = "falhou", str(erro)[:300]
        db.commit()
        return ResultadoEnvio(False, str(erro), envio)
    m.status, m.enviado_por, m.enviado_em = "enviada", quem, datetime.now()
    m.envios = (m.envios or 0) + 1
    db.commit()
    return ResultadoEnvio(True, "", envio)


def enviar_confirmacao(db: Session, c: ConfirmacaoCadastro, site: str, quem: str, automatico: bool = False,
                       cliente_http: httpx.Client | None = None) -> ResultadoEnvio:
    conf = configuracao(db)
    if not configurada(db):
        return ResultadoEnvio(False, "API do WhatsApp não configurada")
    if not conf["modelo_confirmacao"]:
        return ResultadoEnvio(False, "modelo de confirmação não informado em Configurações")
    motivo = pode_enviar(db, conf, c.cliente)
    if motivo:
        return ResultadoEnvio(False, motivo)
    corpo = corpo_modelo(normalizar_telefone(c.cliente.telefone), conf["modelo_confirmacao"], conf["idioma"],
                         _variaveis_do_modelo(conf["variaveis_confirmacao"]), valores_confirmacao(db, c.cliente, c, site, conf), None)
    envio = EnvioWhatsApp(cliente_id=c.cliente_id, confirmacao_id=c.id, telefone=normalizar_telefone(c.cliente.telefone),
                          tipo="confirmacao", modelo=conf["modelo_confirmacao"], automatico=automatico, enviado_por=quem)
    db.add(envio)
    try:
        envio.wamid = enviar_bruto(conf, corpo, cliente_http)
    except FalhaEnvio as erro:
        envio.status, envio.erro = "falhou", str(erro)[:300]
        db.commit()
        return ResultadoEnvio(False, str(erro), envio)
    c.status, c.enviado_por, c.enviado_em = "enviada", quem, datetime.now()
    c.envios = (c.envios or 0) + 1
    db.commit()
    return ResultadoEnvio(True, "", envio)


def enviar_texto(db: Session, telefone: str, texto: str, quem: str, cliente: Cliente | None = None,
                 cliente_http: httpx.Client | None = None) -> ResultadoEnvio:
    """Resposta da equipe (texto livre): só chega se o cliente escreveu nas últimas 24 h (regra da Meta)."""
    conf = configuracao(db)
    if not configurada(db):
        return ResultadoEnvio(False, "API do WhatsApp não configurada")
    telefone = normalizar_telefone(telefone)
    envio = EnvioWhatsApp(cliente_id=cliente.id if cliente else None, telefone=telefone, tipo="resposta", enviado_por=quem)
    db.add(envio)
    try:
        envio.wamid = enviar_bruto(conf, {"to": telefone, "type": "text", "text": {"body": texto[:4000],
                                                                                      "preview_url": True}}, cliente_http)
    except FalhaEnvio as erro:
        envio.status, envio.erro = "falhou", str(erro)[:300]
        db.commit()
        return ResultadoEnvio(False, str(erro), envio)
    db.commit()
    return ResultadoEnvio(True, "", envio)


def enviar_teste(db: Session, telefone: str, cliente_http: httpx.Client | None = None) -> ResultadoEnvio:
    """Manda o modelo hello_world (que a Meta já aprova em todas as contas) para conferir token e número."""
    conf = configuracao(db)
    if not configurada(db):
        return ResultadoEnvio(False, "Preencha o token e o ID do número primeiro.")
    telefone = normalizar_telefone(telefone)
    envio = EnvioWhatsApp(telefone=telefone, tipo="teste", modelo="hello_world", enviado_por="teste")
    db.add(envio)
    try:
        envio.wamid = enviar_bruto(conf, corpo_modelo(telefone, "hello_world", "en_US", [], {}, None), cliente_http)
    except FalhaEnvio as erro:
        if "131058" in str(erro):  # número real: a Meta só manda hello_world do número de teste; token e número ok
            envio.status, envio.erro = "falhou", "número real reconhecido pela Meta (hello_world só sai do número de teste)"
            db.commit()
            return ResultadoEnvio(True, "numero_real", envio)
        envio.status, envio.erro = "falhou", str(erro)[:300]
        db.commit()
        return ResultadoEnvio(False, str(erro), envio)
    db.commit()
    return ResultadoEnvio(True, "", envio)


def enviar_pendentes(db: Session, site: str, cliente_http: httpx.Client | None = None, limite: int = 50) -> dict:
    """Modo automático: manda as promoções novas (conferidas) e as confirmações de cadastro que estão na fila."""
    resumo = {"promocoes": 0, "confirmacoes": 0, "pulados": 0, "falhas": 0, "motivos": {}}
    if not automatico_ligado(db):
        return resumo
    validade = datetime.now() - timedelta(days=settings.promo_validade_dias)
    # sem modelo de confirmação em Configurações, a confirmação não vai pela API (fica para o envio manual, grátis)
    confirmacoes = db.scalars(
        select(ConfirmacaoCadastro).join(Cliente)
        .where(ConfirmacaoCadastro.status == "nova", Cliente.ativo.is_(True), Cliente.consentimento.is_(True))
        .order_by(ConfirmacaoCadastro.id)
    ).all() if configuracao(db)["modelo_confirmacao"] else []
    for c in confirmacoes[:limite]:
        r = enviar_confirmacao(db, c, site, "Automático (API)", automatico=True, cliente_http=cliente_http)
        _contar(resumo, "confirmacoes", r)
    matches = db.scalars(
        select(Match).join(Promocao).join(Cliente)
        .where(Match.status == "nova", Promocao.status == "ativa", Promocao.coletada_em >= validade,
               Cliente.ativo.is_(True), Cliente.consentimento.is_(True))
        .order_by(Promocao.coletada_em, Match.id)
    ).all()
    for m in matches[:limite]:
        if m.promocao.precisa_revisao:  # a equipe confere antes
            continue
        r = enviar_promocao(db, m, site, "Automático (API)", automatico=True, cliente_http=cliente_http)
        _contar(resumo, "promocoes", r)
        if r.motivo == "limite diário de envios atingido":
            break
    return resumo


def _contar(resumo: dict, chave: str, r: ResultadoEnvio) -> None:
    if r.ok:
        resumo[chave] += 1
    elif r.envio is not None and r.envio.status == "falhou":
        resumo["falhas"] += 1
        resumo["motivos"][r.motivo] = resumo["motivos"].get(r.motivo, 0) + 1
    else:
        resumo["pulados"] += 1
        resumo["motivos"][r.motivo] = resumo["motivos"].get(r.motivo, 0) + 1


# ---------------------------------------------------------------- webhook (o que a Meta manda para o site)


def assinatura_valida(conf: dict, corpo: bytes, cabecalho: str | None) -> bool:
    """Confere X-Hub-Signature-256 quando o segredo do app está configurado; sem segredo, aceita."""
    if not conf.get("app_secret"):
        return True
    if not cabecalho or not cabecalho.startswith("sha256="):
        return False
    esperado = hmac.new(conf["app_secret"].encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecalho[len("sha256="):])


def tratar_webhook(db: Session, dados: dict, phone_id: str = "") -> dict:
    """Atualiza o status dos envios e guarda as respostas dos clientes (SAIR tira a autorização)."""
    resumo = {"status": 0, "respostas": 0, "descadastros": 0, "ignorados": 0}
    for entrada in dados.get("entry") or []:
        for mudanca in entrada.get("changes") or []:
            valor = mudanca.get("value") or {}
            if not isinstance(valor, dict):
                continue
            de_numero = (valor.get("metadata") or {}).get("phone_number_id")
            if phone_id and de_numero and str(de_numero) != str(phone_id):
                resumo["ignorados"] += 1  # aviso de outro número (ou forjado): não é deste sistema
                continue
            for st in valor.get("statuses") or []:
                envio = db.scalar(select(EnvioWhatsApp).where(EnvioWhatsApp.wamid == st.get("id")))
                if not envio:
                    continue
                novo = {"sent": "enviada", "delivered": "entregue", "read": "lida", "failed": "falhou"}.get(st.get("status"))
                if novo and (novo == "falhou" or _ordem(novo) >= _ordem(envio.status)):
                    envio.status = novo
                    envio.atualizado_em = datetime.now()
                    if novo == "falhou":
                        erros = st.get("errors") or [{}]
                        envio.erro = f"{erros[0].get('title', '')} {erros[0].get('message', '')}".strip()[:300] or "falhou"
                    resumo["status"] += 1
            nomes = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in valor.get("contacts") or []}
            for msg in valor.get("messages") or []:
                telefone = normalizar_telefone(msg.get("from") or "")
                if not telefone:
                    continue
                if db.scalar(select(RespostaWhatsApp.id).where(RespostaWhatsApp.wamid == msg.get("id"))):
                    continue  # a Meta reenvia webhooks: não duplica
                texto = _texto_da_mensagem(msg)
                cliente = cliente_pelo_telefone(db, telefone)
                resposta = RespostaWhatsApp(cliente_id=cliente.id if cliente else None, telefone=telefone,
                                            nome=nomes.get(msg.get("from")), texto=texto, wamid=msg.get("id"),
                                            recebido_em=_quando(msg.get("timestamp")))
                intencao = crm.classificar_por_regras(texto)
                if intencao:
                    resposta.intencao, resposta.intencao_por = intencao, "regras"
                if texto and (RE_SAIR.match(texto) or intencao == "sair"):
                    resposta.intencao, resposta.intencao_por = "sair", "regras"
                    resposta.descadastrou = True
                    resumo["descadastros"] += 1
                    if cliente:
                        cliente.consentimento = False
                        cliente.observacoes = ((cliente.observacoes or "") + f"\nPediu para não receber mensagens pelo WhatsApp em {datetime.now():%d/%m/%Y}.").strip()
                db.add(resposta)
                resumo["respostas"] += 1
    db.commit()
    return resumo


def variantes_telefone(telefone: str) -> list[str]:
    """O mesmo celular brasileiro com e sem o nono dígito: a Meta às vezes manda o número antigo (sem o 9)."""
    t = normalizar_telefone(telefone)
    variantes = [t]
    if t.startswith("55") and len(t) == 13 and t[4] == "9":
        variantes.append(t[:4] + t[5:])
    elif t.startswith("55") and len(t) == 12:
        variantes.append(t[:4] + "9" + t[4:])
    return variantes


def cliente_pelo_telefone(db: Session, telefone: str) -> Cliente | None:
    variantes = variantes_telefone(telefone)
    clientes = db.scalars(select(Cliente).where(Cliente.telefone.in_(variantes)).order_by(Cliente.ativo.desc(), Cliente.id)).all()
    return next((c for c in clientes if c.telefone == variantes[0]), clientes[0] if clientes else None)


def _ordem(status: str) -> int:
    return {"enviada": 1, "entregue": 2, "lida": 3, "falhou": 9}.get(status, 0)


def _texto_da_mensagem(msg: dict) -> str:
    return _texto_bruto(msg)[:4000]


def _texto_bruto(msg: dict) -> str:
    tipo = msg.get("type")
    if tipo == "text":
        return str((msg.get("text") or {}).get("body") or "")
    if tipo == "button":
        return (msg.get("button") or {}).get("text") or ""
    if tipo == "interactive":
        inter = msg.get("interactive") or {}
        return (inter.get("button_reply") or inter.get("list_reply") or {}).get("title") or ""
    if tipo in ("image", "video", "audio", "document", "sticker"):
        legenda = (msg.get(tipo) or {}).get("caption")
        return f"[{tipo}] {legenda}".strip() if legenda else f"[{tipo}]"
    return f"[{tipo}]" if tipo else ""


def _quando(timestamp) -> datetime:
    try:
        return datetime.fromtimestamp(int(timestamp))
    except (TypeError, ValueError):
        return datetime.now()


def respostas_nao_lidas(db: Session) -> int:
    return db.scalar(select(func.count(RespostaWhatsApp.id)).where(RespostaWhatsApp.lida.is_(False))) or 0
