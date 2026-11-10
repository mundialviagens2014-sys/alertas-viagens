"""Trazer de volta: clientes que pararam de responder ou de comprar, e a mensagem de retorno para eles.

Quem entra na lista (só clientes ativos, com autorização de WhatsApp e sem pedido de emissão aberto):
- sem_resposta: já recebeu promoção, mas não responde, não chama no 0400 nem pede emissão há N dias (padrão 30);
- sem_compra: já comprou (emissão ativa no Manager) e não compra há N meses (padrão 6), sem contato recente.
Quem recebeu mensagem de retorno (ou foi pulado) espera o intervalo (padrão 30 dias) para voltar à lista.

A mensagem leva o link da página do cliente, com as ofertas atuais dos trechos dele. Sai pelo 0500 (modelo de
marketing aprovado na Meta, ~R$ 0,35) ou pelo WhatsApp da equipe (manual, sem custo)."""
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import aeroportos
from . import whatsapp_api as api
from .config import settings
from .models import Cliente, ConversaAtendimento, EnvioWhatsApp, Match, Reengajamento, RespostaWhatsApp
from .models_mvo import Emissao, PedidoEmissao
from .servicos import gravar_estado, ler_estado, link_pagina, nome_bonito, nome_sobrenome, normalizar_telefone

MOTIVOS = {"sem_resposta": "Sem resposta", "sem_compra": "Já comprou, sem compra há meses"}
CAMPOS = {"dias_sem_resposta": "30", "meses_sem_compra": "6", "intervalo_dias": "30", "modelo": "",
          "variaveis": "nome, pagina",
          "texto": ("Olá, {primeiro_nome}! Faz um tempinho que não conversamos 😊\n\n"
                    "{lembranca}Separamos as ofertas atuais para os trechos que você acompanha: {pagina}\n\n"
                    "Quer que a gente procure algo novo para você? É só responder esta mensagem!")}
VARIAVEIS = {"nome": "nome e sobrenome", "primeiro_nome": "só o primeiro nome", "pagina": "link da página do cliente",
             "agencia": "nome da agência", "ultima_viagem": "destino da última viagem ou do trecho acompanhado",
             "dias": "há quantos dias sem contato", "contato": "link do WhatsApp do atendimento (0400)"}
PREFIXO = "reengajar:"


def configuracao(db: Session) -> dict:
    return {c: (ler_estado(db, PREFIXO + c) or "") or padrao for c, padrao in CAMPOS.items()} | {
        "modelo": ler_estado(db, PREFIXO + "modelo") or ""}


def salvar_configuracao(db: Session, dados: dict) -> None:
    for campo in CAMPOS:
        if campo in dados:
            gravar_estado(db, PREFIXO + campo, str(dados[campo]).strip())


def _inteiro(valor, padrao: int) -> int:
    try:
        return max(1, int(str(valor).strip()))
    except ValueError:
        return padrao


@dataclass
class Candidato:
    cliente: Cliente
    motivo: str
    dias: int                       # dias sem contato (ou desde a última compra)
    ultimo_envio: datetime | None
    ultimo_contato: datetime | None
    ultima_compra: datetime | None
    ultima_viagem: str
    ultima_tentativa: datetime | None


def _maximos(db: Session, coluna_cliente, coluna_data, *filtros) -> dict[int, datetime]:
    linhas = db.execute(select(coluna_cliente, func.max(coluna_data)).where(coluna_cliente.is_not(None), *filtros)
                        .group_by(coluna_cliente)).all()
    return {cid: quando for cid, quando in linhas if quando}


def candidatos(db: Session, agora: datetime | None = None) -> list[Candidato]:
    """A lista de quem trazer de volta, dos que estão há mais tempo sem contato para os mais recentes."""
    agora = agora or datetime.now()
    conf = configuracao(db)
    dias_resposta = _inteiro(conf["dias_sem_resposta"], 30)
    dias_compra = _inteiro(conf["meses_sem_compra"], 6) * 30
    intervalo = _inteiro(conf["intervalo_dias"], 30)

    ultimo_envio = _maximos(db, Match.cliente_id, Match.enviado_em, Match.status == "enviada")
    contatos = [_maximos(db, RespostaWhatsApp.cliente_id, RespostaWhatsApp.recebido_em),
                _maximos(db, ConversaAtendimento.cliente_id, ConversaAtendimento.aberta_em),
                _maximos(db, PedidoEmissao.cliente_id, PedidoEmissao.criado_em)]
    compras = _maximos(db, Emissao.cliente_id, Emissao.data_emissao, Emissao.status == "ativa")
    tentativas = _maximos(db, Reengajamento.cliente_id, Reengajamento.quando)
    pedido_aberto = set(db.scalars(select(PedidoEmissao.cliente_id).where(
        PedidoEmissao.status == "aguardando", PedidoEmissao.cliente_id.is_not(None))))
    destinos_comprados = dict(db.execute(
        select(Emissao.cliente_id, Emissao.destino).where(Emissao.status == "ativa", Emissao.cliente_id.is_not(None))
        .order_by(Emissao.data_emissao)).all())   # o último de cada cliente fica por cima

    clientes = db.scalars(select(Cliente).where(Cliente.ativo.is_(True), Cliente.consentimento.is_(True))).all()
    saida = []
    for c in clientes:
        if c.id in pedido_aberto:
            continue
        tentativa = tentativas.get(c.id)
        if tentativa and tentativa >= agora - timedelta(days=intervalo):
            continue
        compra = compras.get(c.id)
        contato = max([d[c.id] for d in contatos if c.id in d] + ([compra] if compra else []), default=None)
        envio = ultimo_envio.get(c.id)
        motivo = None
        if compra and compra <= agora - timedelta(days=dias_compra) and (
                not contato or contato <= agora - timedelta(days=dias_resposta)):
            motivo, desde = "sem_compra", compra
        elif envio and (c.criado_em or agora) <= agora - timedelta(days=dias_resposta) and (
                not contato or contato <= agora - timedelta(days=dias_resposta)):
            motivo, desde = "sem_resposta", contato or c.criado_em or envio
        if not motivo:
            continue
        viagem = destinos_comprados.get(c.id) or next((i.destino for i in c.interesses if i.ativo and i.destino), "")
        saida.append(Candidato(cliente=c, motivo=motivo, dias=max((agora - desde).days, 0), ultimo_envio=envio,
                               ultimo_contato=contato, ultima_compra=compra,
                               ultima_viagem=aeroportos.nome_curto(viagem) if viagem else "", ultima_tentativa=tentativa))
    return sorted(saida, key=lambda x: -x.dias)


def valores(c: Candidato, site: str, contato: str = "") -> dict:
    primeiro = nome_sobrenome(c.cliente.nome).split(" ")[0]
    lembranca = ""
    if c.motivo == "sem_compra" and c.ultima_viagem:
        lembranca = f"Que tal planejar a próxima viagem? A última foi para {c.ultima_viagem} ✈️\n\n"
    return {"nome": nome_sobrenome(c.cliente.nome), "primeiro_nome": primeiro, "nome_completo": nome_bonito(c.cliente.nome),
            "pagina": link_pagina(site, c.cliente) or site, "agencia": settings.agencia_nome,
            "ultima_viagem": c.ultima_viagem or "o destino que você escolheu", "dias": str(c.dias),
            "contato": f"https://wa.me/{contato}" if contato else "-", "lembranca": lembranca}


def texto_manual(db: Session, c: Candidato, site: str) -> str:
    from .models import novo_token

    if not c.cliente.token_pagina:
        c.cliente.token_pagina = novo_token()
        db.commit()
    v = valores(c, site, api.configuracao(db).get("contato", ""))
    texto = configuracao(db)["texto"]
    for chave, valor in v.items():
        texto = texto.replace("{" + chave + "}", valor)
    return texto


def registrar(db: Session, cliente_id: int, motivo: str, canal: str, por: str, envio_id: int | None = None) -> None:
    db.add(Reengajamento(cliente_id=cliente_id, motivo=motivo if motivo in MOTIVOS else "sem_resposta", canal=canal,
                         por=por, envio_id=envio_id))
    db.commit()


def enviar_api(db: Session, c: Candidato, site: str, quem: str,
               cliente_http: httpx.Client | None = None) -> api.ResultadoEnvio:
    """Manda a mensagem de retorno pelo 0500 com o modelo configurado (marketing)."""
    from .models import novo_token

    conf_api, conf = api.configuracao(db), configuracao(db)
    if not api.configurada(db):
        return api.ResultadoEnvio(False, "a API do WhatsApp não está configurada")
    if not conf["modelo"]:
        return api.ResultadoEnvio(False, "falta o nome do modelo de retorno (aprovado na Meta) nas configurações desta tela")
    motivo = api.pode_enviar(db, conf_api, c.cliente)
    if motivo:
        return api.ResultadoEnvio(False, motivo)
    if not c.cliente.token_pagina:
        c.cliente.token_pagina = novo_token()
    telefone = normalizar_telefone(c.cliente.telefone)
    corpo = api.corpo_modelo(telefone, conf["modelo"], conf_api["idioma"], api._variaveis_do_modelo(conf["variaveis"]),
                             valores(c, site, conf_api.get("contato", "")), None)
    envio = EnvioWhatsApp(cliente_id=c.cliente.id, telefone=telefone, tipo="reengajamento", modelo=conf["modelo"],
                          automatico=False, enviado_por=quem)
    db.add(envio)
    try:
        envio.wamid = api.enviar_bruto(conf_api, corpo, cliente_http)
    except api.FalhaEnvio as erro:
        envio.status, envio.erro = "falhou", str(erro)[:300]
        db.commit()
        return api.ResultadoEnvio(False, str(erro), envio)
    db.flush()
    registrar(db, c.cliente.id, c.motivo, "api", quem, envio.id)
    return api.ResultadoEnvio(True, "", envio)
