"""CRM: o funil de cada cliente, montado sozinho a partir do que o sistema já registra.

A etapa de um cliente é decidida pelo evento mais recente dele: cadastro, promoção enviada, resposta pelo
WhatsApp (e a intenção dela), resposta da equipe, pedido de emissão, emissão. Arrastar o cartão à mão grava uma
etapa manual, que vale até acontecer um evento mais novo. Nada disso cria tabelas de "negócios": o cartão é o
cliente, e o que ele mostra vem das tabelas que já existem.
"""
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import ia
from .models import (Cliente, ConfirmacaoCadastro, ConversaAtendimento, EnvioWhatsApp, EtapaCRM, Match, Reengajamento,
                     RespostaWhatsApp)
from .models_mvo import Emissao, PedidoEmissao
from .servicos import descrever_trecho, preco_reais

log = logging.getLogger(__name__)

ETAPAS: dict[str, dict] = {
    "cadastrado": {"nome": "Cadastrado", "ajuda": "Cliente novo ou confirmado, ainda sem promoção enviada", "cor": "#5a6782"},
    "enviada": {"nome": "Promoção enviada", "ajuda": "Recebeu promoção pelo 0500 (disparo); aguardando o cliente", "cor": "#1b468f"},
    "respondeu": {"nome": "Respondeu", "ajuda": "O cliente respondeu no 0500 ou chamou no 0400 pelo botão; a agência responde", "cor": "#ff914d"},
    "conversa": {"nome": "Em conversa", "ajuda": "Conversa com a equipe no 0400 (atendimento)", "cor": "#0f8b8d"},
    "duvida": {"nome": "Em dúvida", "ajuda": "Fez perguntas ou disse que vai pensar (resposta no 0500 ou marcado à mão)", "cor": "#9a6700"},
    "pedido": {"nome": "Pedido de emissão", "ajuda": "Pedido aberto no Manager, esperando emitir", "cor": "#7c5cbf"},
    "ganho": {"nome": "Venda concluída", "ajuda": "Emissão registrada no Manager", "cor": "#168a4a"},
    "perdido": {"nome": "Perdido", "ajuda": "Não quer, pediu para sair ou cadastro inativo", "cor": "#b3261e"},
}
# intenção da resposta → etapa
ETAPA_DA_INTENCAO = {"interesse": "respondeu", "duvida": "duvida", "recusa": "perdido", "sair": "perdido", "outro": "respondeu"}

# ---------------------------------------------------------------- intenção das respostas

RE_SAIR = re.compile(r"^\s*(sair|parar|pare|stop|cancelar|descadastrar|remover|unsubscribe)\b|n[aã]o (quero|mande|manda|envie|envia)( mais)? (mensage|receber|promo)", re.I)
RE_RECUSA = re.compile(r"\b(n[aã]o (quero|tenho interesse|preciso|vou|me interessa|posso agora)|sem interesse|muito caro|"
                       r"caro demais|desisti|deixa pra l[aá]|n[aã]o[, ]+obrigad[oa]|agora n[aã]o|outra hora)\b", re.I)
RE_INTERESSE = re.compile(r"\b(quero|queremos|pode reservar|pode emitir|vamos fechar|fechar|fechado|me manda|manda mais|"
                          r"como fa[cç]o|como fa[cç]emos|reserva|tenho interesse|interesse|gostei|perfeito|bora|"
                          r"vou querer|me interessa)\b", re.I)
RE_DUVIDA = re.compile(r"\?|\b(quanto|tem (para|pra)|outra data|outro dia|outras datas|parcel|inclui|taxa|bagagem|"
                       r"hor[aá]rio|escala|conex[aã]o|vou pensar|vou ver|depois|talvez|ser[aá] que|d[uú]vida|"
                       r"pode ser|e se|qual)\b", re.I)
RE_OUTRO = re.compile(r"^\s*(oi|ol[aá]|ok|okay|blz|beleza|obrigad[oa]|valeu|bom dia|boa tarde|boa noite|👍|🙏|❤️|😊|k+)[\s!.]*$", re.I)


def classificar_por_regras(texto: str | None) -> str | None:
    """Intenção pelas palavras; None quando as regras não decidem (aí a IA olha)."""
    t = (texto or "").strip()
    if not t or t.startswith("["):  # imagem, áudio, figurinha sem legenda
        return "outro" if t else None
    if RE_SAIR.search(t):
        return "sair"
    if RE_RECUSA.search(t):
        return "recusa"
    if RE_OUTRO.match(t):
        return "outro"
    interesse, duvida = bool(RE_INTERESSE.search(t)), bool(RE_DUVIDA.search(t))
    if interesse and not duvida:
        return "interesse"
    if duvida and not interesse:
        return "duvida"
    return None


def classificar_pendentes(db: Session, limite: int = 10, usar_ia: bool = True, cliente_http=None) -> dict:
    """Dá intenção às respostas que ainda não têm: regras primeiro; o que sobrar vai para a IA (dentro da cota)."""
    resumo = {"regras": 0, "ia": 0, "pendentes": 0}
    pendentes = db.scalars(
        select(RespostaWhatsApp).where(RespostaWhatsApp.intencao.is_(None)).order_by(RespostaWhatsApp.id).limit(200)
    ).all()
    para_ia = []
    for r in pendentes:
        intencao = classificar_por_regras(r.texto)
        if intencao:
            r.intencao, r.intencao_por = intencao, "regras"
            resumo["regras"] += 1
        else:
            para_ia.append(r)
    db.commit()
    for r in para_ia:
        if not usar_ia or not ia.cota_disponivel(db) or resumo["ia"] >= limite:
            resumo["pendentes"] += 1
            continue
        try:
            intencao = ia.classificar_intencao(r.texto or "", cliente_http=cliente_http)
            ia.registrar_uso(db)
        except ia.CotaExcedida:
            resumo["pendentes"] += len(para_ia) - resumo["ia"]
            break
        except Exception as erro:  # noqa: BLE001 - a resposta fica para a próxima rodada
            log.warning("IA não classificou a resposta %s: %s", r.id, erro)
            resumo["pendentes"] += 1
            continue
        r.intencao, r.intencao_por = intencao or "outro", "ia"
        resumo["ia"] += 1
    db.commit()
    return resumo


# ---------------------------------------------------------------- o funil


@dataclass
class Cartao:
    cliente: Cliente
    etapa: str
    quando: datetime
    evento: str
    valor: float | None = None
    trecho: str = ""
    origem: str | None = None  # códigos do trecho da última promoção (passagem), para o arco de voo no cartão
    destino: str | None = None
    responsavel: str | None = None
    manual: bool = False
    intencao: str | None = None
    ultima_resposta: str | None = None
    nao_lidas: int = 0
    link_pedido: int | None = None
    link_emissao: int | None = None
    canal: str = ""   # "0400" quando a última coisa foi a conversa no número de atendimento
    detalhes: dict = field(default_factory=dict)


def _ultimo(itens, chave_cliente, chave_tempo) -> dict:
    saida: dict = {}
    for item in itens:
        cid = chave_cliente(item)
        if cid is None:
            continue
        atual = saida.get(cid)
        if atual is None or chave_tempo(item) > chave_tempo(atual):
            saida[cid] = item
    return saida


def cartoes(db: Session, dias: int = 90, agora: datetime | None = None) -> list[Cartao]:
    agora = agora or datetime.now()
    desde = agora - timedelta(days=dias)

    envios = db.scalars(
        select(Match).where(Match.status == "enviada", Match.enviado_em >= desde)
        .options(selectinload(Match.promocao))
    ).all()
    ultimo_envio = _ultimo(envios, lambda m: m.cliente_id, lambda m: m.enviado_em or desde)
    confirmacoes = db.scalars(select(ConfirmacaoCadastro).where(ConfirmacaoCadastro.status == "enviada",
                                                                ConfirmacaoCadastro.enviado_em >= desde)).all()
    ultima_confirmacao = _ultimo(confirmacoes, lambda c: c.cliente_id, lambda c: c.enviado_em or desde)
    respostas = db.scalars(select(RespostaWhatsApp).where(RespostaWhatsApp.recebido_em >= desde,
                                                          RespostaWhatsApp.cliente_id.is_not(None))).all()
    ultima_resposta = _ultimo(respostas, lambda r: r.cliente_id, lambda r: r.recebido_em)
    nao_lidas: dict[int, int] = defaultdict(int)
    for r in respostas:
        if not r.lida:
            nao_lidas[r.cliente_id] += 1
    respostas_equipe = db.scalars(select(EnvioWhatsApp).where(
        EnvioWhatsApp.tipo == "resposta", EnvioWhatsApp.status != "falhou", EnvioWhatsApp.criado_em >= desde,
        EnvioWhatsApp.cliente_id.is_not(None))).all()
    ultima_equipe = _ultimo(respostas_equipe, lambda e: e.cliente_id, lambda e: e.criado_em)
    retornos = db.scalars(select(Reengajamento).where(Reengajamento.quando >= desde, Reengajamento.canal != "pulado")).all()
    ultimo_retorno = _ultimo(retornos, lambda r: r.cliente_id, lambda r: r.quando)
    conversas_0400 = db.scalars(select(ConversaAtendimento).where(ConversaAtendimento.aberta_em >= desde)).all()
    ultima_0400 = _ultimo(conversas_0400, lambda c: c.cliente_id, lambda c: c.aberta_em)
    pedidos = db.scalars(select(PedidoEmissao).where(
        PedidoEmissao.cliente_id.is_not(None),
        (PedidoEmissao.status == "aguardando") | (PedidoEmissao.criado_em >= desde))).all()
    ultimo_pedido = _ultimo(pedidos, lambda p: p.cliente_id, lambda p: p.atendido_em or p.criado_em)
    emissoes = db.scalars(select(Emissao).where(Emissao.status == "ativa", Emissao.cliente_id.is_not(None),
                                                Emissao.data_emissao >= desde)).all()
    ultima_emissao = _ultimo(emissoes, lambda e: e.cliente_id, lambda e: e.data_emissao)
    manuais = {m.cliente_id: m for m in db.scalars(select(EtapaCRM))}

    ids = (set(ultimo_envio) | set(ultima_confirmacao) | set(ultima_resposta) | set(ultima_equipe) | set(ultima_0400) | set(ultimo_retorno) | set(ultimo_pedido)
           | set(ultima_emissao) | {cid for cid, m in manuais.items() if m.definido_em >= desde})
    novos = db.scalars(select(Cliente).where(Cliente.criado_em >= desde, Cliente.ativo.is_(True))).all()
    clientes = {c.id: c for c in novos}
    faltam = [i for i in ids if i not in clientes]
    for lote in range(0, len(faltam), 500):
        for c in db.scalars(select(Cliente).where(Cliente.id.in_(faltam[lote:lote + 500]))):
            clientes[c.id] = c

    saida: list[Cartao] = []
    for cid, cliente in clientes.items():
        eventos: list[tuple[datetime, str, object]] = []
        if cliente.criado_em and cliente.criado_em >= desde:
            eventos.append((cliente.criado_em, "cadastro", None))
        if cid in ultima_confirmacao:
            c = ultima_confirmacao[cid]
            eventos.append((c.enviado_em, "confirmacao", c))
        if cid in ultimo_envio:
            m = ultimo_envio[cid]
            eventos.append((m.enviado_em, "envio", m))
        if cid in ultima_resposta:
            r = ultima_resposta[cid]
            eventos.append((r.recebido_em, "resposta", r))
        if cid in ultima_equipe:
            e = ultima_equipe[cid]
            eventos.append((e.criado_em, "equipe", e))
        if cid in ultima_0400:
            v = ultima_0400[cid]
            eventos.append((v.aberta_em, "atendimento", v))
        if cid in ultimo_retorno:
            r = ultimo_retorno[cid]
            eventos.append((r.quando, "retorno", r))
        if cid in ultimo_pedido:
            p = ultimo_pedido[cid]
            eventos.append((p.atendido_em or p.criado_em, "pedido", p))
        if cid in ultima_emissao:
            e = ultima_emissao[cid]
            eventos.append((e.data_emissao, "emissao", e))
        if not eventos:
            continue
        eventos.sort(key=lambda ev: ev[0])
        quando, tipo, obj = eventos[-1]
        cartao = _montar(cliente, quando, tipo, obj, ultimo_envio.get(cid), ultimo_pedido.get(cid), ultima_resposta.get(cid))
        cartao.nao_lidas = nao_lidas.get(cid, 0)
        manual = manuais.get(cid)
        if manual and manual.definido_em >= quando:
            cartao.etapa, cartao.manual = manual.etapa, True
            cartao.quando = manual.definido_em
            cartao.evento = f"Movido à mão por {manual.definido_por or 'equipe'}" + (f": {manual.motivo}" if manual.motivo else "")
        elif not cliente.ativo:
            cartao.etapa, cartao.evento = "perdido", "Cadastro inativo"
        if cartao.etapa not in ETAPAS:
            cartao.etapa = "cadastrado"
        saida.append(cartao)
    saida.sort(key=lambda c: c.quando, reverse=True)
    return saida


def _montar(cliente: Cliente, quando: datetime, tipo: str, obj, envio: Match | None, pedido: PedidoEmissao | None,
            resposta: RespostaWhatsApp | None) -> Cartao:
    cartao = Cartao(cliente=cliente, etapa="cadastrado", quando=quando, evento="Cadastrado")
    if envio is not None:
        promo = envio.promocao
        cartao.trecho = descrever_trecho(promo)
        if promo.passagem and promo.origem and promo.destino:
            cartao.origem, cartao.destino = promo.origem, promo.destino
        cartao.valor = preco_reais(promo)
        cartao.responsavel = envio.enviado_por
    if resposta is not None:
        cartao.ultima_resposta = (resposta.texto or "")[:160]
        cartao.intencao = resposta.intencao
    # pedido aberto segura o cliente nessa etapa, mesmo com envio ou resposta mais nova (só emissão ou mão tiram)
    if pedido is not None and pedido.status == "aguardando" and tipo != "emissao":
        tipo, obj = "pedido", pedido
    if tipo == "emissao":
        cartao.etapa = "ganho"
        cartao.valor = obj.preco_venda or obj.total
        cartao.evento = f"Emissão {obj.rota or ''} · {quando:%d/%m}".strip()
        cartao.responsavel = obj.emissor_nome or obj.usuario
        cartao.link_emissao = obj.id
    elif tipo == "pedido":
        p = obj
        if p.status == "aguardando":
            cartao.etapa = "pedido"
            cartao.evento = f"Pedido de emissão aguardando · {p.origem or '?'} → {p.destino or '?'}"
        elif p.status == "cancelado":
            cartao.etapa = "perdido"
            cartao.evento = "Pedido de emissão cancelado"
        else:
            cartao.etapa = "ganho"
            cartao.evento = "Pedido emitido"
        cartao.valor = p.preco_combinado or cartao.valor
        cartao.link_pedido = p.id
    elif tipo == "resposta":
        cartao.etapa = ETAPA_DA_INTENCAO.get(obj.intencao or "outro", "respondeu")
        if obj.descadastrou:
            cartao.etapa = "perdido"
        cartao.evento = "Respondeu no 0500" + (f": “{(obj.texto or '')[:90]}”" if obj.texto else "")
    elif tipo == "equipe":
        cartao.etapa = "conversa"
        cartao.evento = f"A equipe respondeu pelo 0500 ({obj.enviado_por or 'MVO'})"
    elif tipo == "retorno":
        cartao.etapa = "enviada"
        cartao.evento = ("Mensagem de retorno " + ("pelo 0500" if obj.canal == "api" else "pelo WhatsApp")
                         + f" ({obj.por or 'equipe'})")
    elif tipo == "atendimento" and obj.origem == "cliente":
        # o cliente tocou em "Falar com um atendente": está esperando a agência no 0400
        cartao.etapa = "respondeu"
        cartao.canal = "0400"
        cartao.evento = "Chamou no 0400 pelo botão da mensagem"
        cartao.intencao = cartao.intencao or "interesse"
    elif tipo == "atendimento":
        cartao.etapa = "conversa"
        cartao.canal = "0400"
        cartao.evento = f"Conversa no 0400 ({obj.aberta_por or 'equipe'})"
        cartao.responsavel = obj.aberta_por or cartao.responsavel
    elif tipo == "envio":
        cartao.etapa = "enviada"
        cartao.evento = f"Promoção enviada pelo 0500 por {obj.enviado_por or 'equipe'}"
    elif tipo == "confirmacao":
        cartao.etapa = "cadastrado"
        cartao.evento = "Cadastro confirmado pelo WhatsApp"
    if not cliente.consentimento and cartao.etapa in ("cadastrado", "enviada", "respondeu", "conversa", "duvida") \
            and resposta is not None and resposta.descadastrou:
        cartao.etapa, cartao.evento = "perdido", "Pediu para não receber mensagens"
    return cartao


def colunas(cartoes_: list[Cartao]) -> list[dict]:
    """As colunas do quadro, com cartões, contagem e soma dos valores."""
    por_etapa: dict[str, list[Cartao]] = {e: [] for e in ETAPAS}
    for c in cartoes_:
        por_etapa.setdefault(c.etapa, []).append(c)
    return [{"chave": chave, **dados, "cartoes": por_etapa[chave], "qtd": len(por_etapa[chave]),
             "valor": round(sum(c.valor or 0 for c in por_etapa[chave]), 2)} for chave, dados in ETAPAS.items()]


def mover(db: Session, cliente_id: int, etapa: str, quem: str | None, motivo: str | None = None) -> EtapaCRM:
    if etapa not in ETAPAS:
        raise ValueError("etapa desconhecida")
    item = db.get(EtapaCRM, cliente_id)
    if item is None:
        item = EtapaCRM(cliente_id=cliente_id)
        db.add(item)
    item.etapa, item.definido_em, item.definido_por, item.motivo = etapa, datetime.now(), quem, (motivo or None)
    db.commit()
    return item


def voltar_ao_automatico(db: Session, cliente_id: int) -> bool:
    item = db.get(EtapaCRM, cliente_id)
    if item is None:
        return False
    db.delete(item)
    db.commit()
    return True


def vendas_no_periodo(db: Session, dias: int = 90, agora: datetime | None = None) -> dict:
    """Emissões de clientes no período (a venda fica contada mesmo que o cartão já tenha seguido para outra etapa)."""
    desde = (agora or datetime.now()) - timedelta(days=dias)
    linhas = db.execute(select(Emissao.cliente_id, Emissao.preco_venda).where(
        Emissao.status == "ativa", Emissao.cliente_id.is_not(None), Emissao.data_emissao >= desde)).all()
    return {"qtd": len(linhas), "valor": round(sum(v or 0 for _, v in linhas), 2), "clientes": len({c for c, _ in linhas})}


def resumo(cartoes_: list[Cartao], vendas: dict | None = None) -> dict:
    total = len(cartoes_)
    ganhos = [c for c in cartoes_ if c.etapa == "ganho"]
    vendas = vendas or {"qtd": len(ganhos), "valor": round(sum(c.valor or 0 for c in ganhos), 2), "clientes": len(ganhos)}
    abertos = [c for c in cartoes_ if c.etapa in ("enviada", "respondeu", "conversa", "duvida", "pedido")]
    return {"total": total, "ganhos": vendas["qtd"], "valor_ganho": vendas["valor"],
            "abertos": len(abertos), "valor_aberto": round(sum(c.valor or 0 for c in abertos), 2),
            "aguardando_agencia": sum(1 for c in cartoes_ if c.etapa in ("respondeu", "duvida")),
            "perdidos": sum(1 for c in cartoes_ if c.etapa == "perdido"),
            "taxa": round(vendas["clientes"] / total * 100, 1) if total else None}
