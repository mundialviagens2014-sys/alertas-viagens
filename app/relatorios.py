"""Relatórios (só para o administrador): os números de vendas, WhatsApp, clientes, promoções, milhas e CPFs.

Tudo é calculado na hora a partir das tabelas; nada é gravado. Os períodos são escolhidos na tela.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import aeroportos
from .models import (
    Cliente,
    ConfirmacaoCadastro,
    EnvioWhatsApp,
    Interesse,
    Match,
    ProgramaMilhas,
    Promocao,
    ImagemPromocao,
    RespostaWhatsApp,
)
from .models_mvo import TIPOS_LANCAMENTO, Colaborador, ContaPrograma, Emissao, Lancamento, PedidoEmissao
from .servicos import MESES_CURTOS, preco_reais

PERIODOS = {
    "mes": "Este mês", "mes_anterior": "Mês passado", "30": "Últimos 30 dias", "90": "Últimos 90 dias",
    "ano": "Este ano", "12m": "Últimos 12 meses", "tudo": "Desde o início", "personalizado": "Período escolhido",
}
NOMES_TIPO = {"passagem": "Passagens", "hotel": "Hotéis", "pacote": "Pacotes", "cruzeiro": "Cruzeiros", "outro": "Outros"}
NOMES_FONTE = {"telegram": "Telegram", "whatsapp": "WhatsApp", "google_flights": "Google Flights", "manual": "Manual",
               "api": "API"}
NOMES_FORMA = {"programa_proprio": "Milhas (conta própria)", "fornecedor": "Fornecedor / balcão", "pagante": "Tarifa paga"}
INTENCOES = {"interesse": "Interessado", "duvida": "Em dúvida", "recusa": "Não quer", "sair": "Pediu para sair",
             "outro": "Outros"}


@dataclass
class Periodo:
    chave: str
    nome: str
    inicio: date
    fim: date  # inclusive

    @property
    def inicio_dt(self) -> datetime:
        return datetime.combine(self.inicio, time.min)

    @property
    def fim_dt(self) -> datetime:
        return datetime.combine(self.fim + timedelta(days=1), time.min)

    @property
    def dias(self) -> int:
        return (self.fim - self.inicio).days + 1


def periodo_de(chave: str, de: date | None = None, ate: date | None = None, hoje: date | None = None) -> Periodo:
    hoje = hoje or date.today()
    if chave == "personalizado" and (de or ate):
        de = de or ate
        ate = ate or hoje
        if ate < de:
            de, ate = ate, de
        return Periodo("personalizado", f"{de:%d/%m/%Y} a {ate:%d/%m/%Y}", de, ate)
    if chave == "mes_anterior":
        fim = hoje.replace(day=1) - timedelta(days=1)
        return Periodo(chave, PERIODOS[chave], fim.replace(day=1), fim)
    if chave == "30":
        return Periodo(chave, PERIODOS[chave], hoje - timedelta(days=29), hoje)
    if chave == "90":
        return Periodo(chave, PERIODOS[chave], hoje - timedelta(days=89), hoje)
    if chave == "ano":
        return Periodo(chave, PERIODOS[chave], hoje.replace(month=1, day=1), hoje)
    if chave == "12m":
        inicio = (hoje.replace(day=1) - timedelta(days=334)).replace(day=1)
        return Periodo(chave, PERIODOS[chave], inicio, hoje)
    if chave == "tudo":
        return Periodo(chave, PERIODOS[chave], date(2000, 1, 1), hoje)
    return Periodo("mes", PERIODOS["mes"], hoje.replace(day=1), hoje)


# ---------------------------------------------------------------- eixos de tempo


def _mes_anterior(d: date) -> date:
    return (d.replace(day=1) - timedelta(days=1)).replace(day=1)


def meses(n: int = 12, hoje: date | None = None) -> list[date]:
    """Os últimos n meses (primeiro dia de cada), do mais antigo ao atual."""
    hoje = hoje or date.today()
    atual = hoje.replace(day=1)
    saida = [atual]
    for _ in range(n - 1):
        atual = _mes_anterior(atual)
        saida.append(atual)
    return list(reversed(saida))


def semanas(n: int = 12, hoje: date | None = None) -> list[date]:
    """As últimas n semanas (segunda-feira de cada), da mais antiga à atual."""
    hoje = hoje or date.today()
    segunda = hoje - timedelta(days=hoje.weekday())
    return [segunda - timedelta(weeks=k) for k in range(n - 1, -1, -1)]


def rotulo_mes(d: date) -> str:
    return f"{MESES_CURTOS[d.month]}/{d.year % 100:02d}"


def rotulo_semana(d: date) -> str:
    return f"{d:%d/%m}"


def _chave_mes(d: date | datetime) -> date:
    return date(d.year, d.month, 1)


def _chave_semana(d: date | datetime) -> date:
    d = d.date() if isinstance(d, datetime) else d
    return d - timedelta(days=d.weekday())


def _por_eixo(eixo: list[date], chave, itens, valor=lambda i: 1) -> list[float]:
    somas: dict[date, float] = defaultdict(float)
    for item in itens:
        somas[chave(item)] += valor(item)
    return [somas.get(k, 0) for k in eixo]


# ---------------------------------------------------------------- vendas (emissões)


def _emissoes(db: Session, inicio: datetime, fim: datetime) -> list[Emissao]:
    return db.scalars(
        select(Emissao).where(Emissao.status == "ativa", Emissao.data_emissao >= inicio, Emissao.data_emissao < fim)
        .options(selectinload(Emissao.agencia))
    ).all()


def _ranking(itens, chave, valor, limite: int = 10) -> list[tuple[str, float]]:
    somas: Counter = Counter()
    for i in itens:
        k = chave(i)
        if k:
            somas[k] += valor(i)
    return somas.most_common(limite)


def vendas(db: Session, p: Periodo) -> dict:
    emissoes = _emissoes(db, p.inicio_dt, p.fim_dt)
    canceladas = db.scalar(select(func.count(Emissao.id)).where(
        Emissao.status == "cancelada", Emissao.data_emissao >= p.inicio_dt, Emissao.data_emissao < p.fim_dt)) or 0
    vendido = round(sum(e.preco_venda or 0 for e in emissoes), 2)
    com_lucro = [e for e in emissoes if e.lucro is not None]
    por_programa = Counter()
    for e in emissoes:
        por_programa[e.programa or NOMES_FORMA.get(e.forma, e.forma)] += 1
    return {
        "qtd": len(emissoes), "passageiros": sum(e.qtd_passageiros or 0 for e in emissoes),
        "vendido": vendido, "cobrado": round(sum(e.total for e in emissoes), 2),
        "custo": round(sum(e.custo or 0 for e in emissoes), 2),
        "lucro": round(sum(e.lucro for e in com_lucro), 2), "com_lucro": len(com_lucro),
        "margem": round(sum(e.lucro for e in com_lucro) / sum(e.preco_venda or 0 for e in com_lucro) * 100, 1)
        if com_lucro and sum(e.preco_venda or 0 for e in com_lucro) else None,
        "ticket": round(vendido / len(emissoes), 2) if emissoes else None,
        "pontos": sum(e.pontos or 0 for e in emissoes if e.forma == "programa_proprio"),
        "canceladas": canceladas,
        "por_programa": [(k, v) for k, v in por_programa.most_common(10)],
        "valor_por_programa": _ranking(emissoes, lambda e: e.programa or NOMES_FORMA.get(e.forma, e.forma),
                                       lambda e: e.preco_venda or 0),
        "por_forma": _ranking(emissoes, lambda e: NOMES_FORMA.get(e.forma, e.forma), lambda e: 1),
        "por_emissor": _ranking(emissoes, lambda e: e.emissor_nome or e.usuario or "–", lambda e: e.preco_venda or 0),
        "qtd_por_emissor": dict(_ranking(emissoes, lambda e: e.emissor_nome or e.usuario or "–", lambda e: 1, 50)),
        "por_companhia": _ranking(emissoes, lambda e: e.companhia, lambda e: 1),
        "por_rota": _ranking(emissoes, lambda e: e.rota, lambda e: 1),
        "por_agencia": _ranking(emissoes, lambda e: e.agencia.nome if e.agencia else "Venda direta", lambda e: e.preco_venda or 0),
        "por_metodo": _ranking(emissoes, lambda e: e.metodo_pagamento or "não informado", lambda e: 1),
    }


def vendas_por_semana(db: Session, n: int = 12, hoje: date | None = None) -> dict:
    eixo = semanas(n, hoje)
    emissoes = _emissoes(db, datetime.combine(eixo[0], time.min), datetime.combine(eixo[-1] + timedelta(days=7), time.min))
    return {"rotulos": [rotulo_semana(d) for d in eixo],
            "qtd": _por_eixo(eixo, lambda e: _chave_semana(e.data_emissao), emissoes),
            "valor": _por_eixo(eixo, lambda e: _chave_semana(e.data_emissao), emissoes, lambda e: e.preco_venda or 0)}


def vendas_por_mes(db: Session, n: int = 12, hoje: date | None = None) -> dict:
    eixo = meses(n, hoje)
    emissoes = _emissoes(db, datetime.combine(eixo[0], time.min), datetime.max.replace(microsecond=0))
    return {"rotulos": [rotulo_mes(d) for d in eixo],
            "qtd": _por_eixo(eixo, lambda e: _chave_mes(e.data_emissao), emissoes),
            "valor": _por_eixo(eixo, lambda e: _chave_mes(e.data_emissao), emissoes, lambda e: e.preco_venda or 0),
            "lucro": _por_eixo(eixo, lambda e: _chave_mes(e.data_emissao), emissoes, lambda e: e.lucro or 0)}


# ---------------------------------------------------------------- WhatsApp (API da Meta)

COBRADAS = ("entregue", "lida")  # a Meta cobra a mensagem entregue; "enviada" ainda pode virar entregue ou falhar


def _preco(conf: dict, campo: str, padrao: float) -> float:
    try:
        return float(str(conf.get(campo, "")).replace(",", "."))
    except ValueError:
        return padrao


def whatsapp(db: Session, p: Periodo, conf: dict, hoje: date | None = None) -> dict:
    preco_promo, preco_conf = _preco(conf, "preco_promocao", 0.35), _preco(conf, "preco_confirmacao", 0.04)
    envios = db.scalars(select(EnvioWhatsApp).where(EnvioWhatsApp.criado_em >= p.inicio_dt,
                                                    EnvioWhatsApp.criado_em < p.fim_dt)).all()
    por_tipo: dict[str, Counter] = defaultdict(Counter)
    for e in envios:
        por_tipo[e.tipo][e.status] += 1
        por_tipo[e.tipo]["total"] += 1

    def cobradas(tipo: str) -> int:
        return sum(por_tipo[tipo][s] for s in COBRADAS)

    def pendentes(tipo: str) -> int:
        return por_tipo[tipo]["enviada"]

    custo_promo = round(cobradas("promocao") * preco_promo, 2)
    custo_conf = round(cobradas("confirmacao") * preco_conf, 2)
    custo_max = round((cobradas("promocao") + pendentes("promocao")) * preco_promo
                      + (cobradas("confirmacao") + pendentes("confirmacao")) * preco_conf, 2)
    respostas = db.scalars(select(RespostaWhatsApp).where(RespostaWhatsApp.recebido_em >= p.inicio_dt,
                                                          RespostaWhatsApp.recebido_em < p.fim_dt)).all()
    matches_enviadas = db.scalar(select(func.count(Match.id)).where(
        Match.status == "enviada", Match.enviado_em >= p.inicio_dt, Match.enviado_em < p.fim_dt)) or 0
    confirmacoes_enviadas = db.scalar(select(func.count(ConfirmacaoCadastro.id)).where(
        ConfirmacaoCadastro.status == "enviada", ConfirmacaoCadastro.enviado_em >= p.inicio_dt,
        ConfirmacaoCadastro.enviado_em < p.fim_dt)) or 0
    intencoes = Counter((r.intencao or "outro") for r in respostas)
    # custo por mês (12 meses), promoções e confirmações separadas
    eixo = meses(12, hoje)
    todos = db.scalars(select(EnvioWhatsApp).where(EnvioWhatsApp.criado_em >= datetime.combine(eixo[0], time.min),
                                                   EnvioWhatsApp.status.in_(COBRADAS))).all()
    por_mes_promo = _por_eixo(eixo, lambda e: _chave_mes(e.criado_em), [e for e in todos if e.tipo == "promocao"],
                              lambda e: preco_promo)
    por_mes_conf = _por_eixo(eixo, lambda e: _chave_mes(e.criado_em), [e for e in todos if e.tipo == "confirmacao"],
                             lambda e: preco_conf)
    entregues_promo = cobradas("promocao")
    return {
        "preco_promocao": preco_promo, "preco_confirmacao": preco_conf,
        "promocoes": dict(por_tipo["promocao"]), "confirmacoes": dict(por_tipo["confirmacao"]),
        "respostas_equipe": por_tipo["resposta"]["total"], "testes": por_tipo["teste"]["total"],
        "cobradas_promocao": entregues_promo, "cobradas_confirmacao": cobradas("confirmacao"),
        "pendentes": pendentes("promocao") + pendentes("confirmacao"),
        "falhas": por_tipo["promocao"]["falhou"] + por_tipo["confirmacao"]["falhou"],
        "custo_promocoes": custo_promo, "custo_confirmacoes": custo_conf, "custo": round(custo_promo + custo_conf, 2),
        "custo_maximo": custo_max,
        "respostas": len(respostas), "descadastros": sum(1 for r in respostas if r.descadastrou),
        "intencoes": [(k, INTENCOES.get(k, k), v) for k, v in intencoes.most_common()],
        "taxa_resposta": round(len(respostas) / entregues_promo * 100, 1) if entregues_promo else None,
        "envios_promocoes_total": matches_enviadas, "envios_promocoes_api": por_tipo["promocao"]["total"],
        "envios_promocoes_manuais": max(matches_enviadas - por_tipo["promocao"]["total"], 0),
        "confirmacoes_total": confirmacoes_enviadas,
        "por_mes": {"rotulos": [rotulo_mes(d) for d in eixo], "promocoes": por_mes_promo, "confirmacoes": por_mes_conf},
        "custo_12m": round(sum(por_mes_promo) + sum(por_mes_conf), 2),
    }


# ---------------------------------------------------------------- clientes


def clientes(db: Session, p: Periodo, hoje: date | None = None) -> dict:
    total = db.scalar(select(func.count(Cliente.id))) or 0
    ativos = db.scalar(select(func.count(Cliente.id)).where(Cliente.ativo.is_(True))) or 0
    autorizados = db.scalar(select(func.count(Cliente.id)).where(Cliente.ativo.is_(True), Cliente.consentimento.is_(True))) or 0
    novos = db.scalar(select(func.count(Cliente.id)).where(Cliente.criado_em >= p.inicio_dt, Cliente.criado_em < p.fim_dt)) or 0
    com_interesse = db.scalar(select(func.count(func.distinct(Interesse.cliente_id))).join(Cliente)
                              .where(Interesse.ativo.is_(True), Cliente.ativo.is_(True))) or 0
    eixo = meses(12, hoje)
    datas = db.scalars(select(Cliente.criado_em).where(Cliente.criado_em >= datetime.combine(eixo[0], time.min))).all()
    interesses = db.execute(select(Interesse.origem, Interesse.destino).join(Cliente)
                            .where(Interesse.ativo.is_(True), Cliente.ativo.is_(True))).all()
    destinos = Counter(aeroportos.nome_curto(d) for _, d in interesses if d)
    origens = Counter(aeroportos.nome_curto(o) for o, _ in interesses if o)
    classes = Counter((c or "sem classificação") for c in db.scalars(select(Cliente.classificacao).where(Cliente.ativo.is_(True))))
    compraram = db.scalar(select(func.count(func.distinct(Emissao.cliente_id))).where(
        Emissao.status == "ativa", Emissao.cliente_id.is_not(None))) or 0
    return {
        "total": total, "ativos": ativos, "inativos": total - ativos, "autorizados": autorizados,
        "sem_autorizacao": ativos - autorizados, "novos": novos, "com_interesse": com_interesse,
        "compraram": compraram, "novos_por_mes": {"rotulos": [rotulo_mes(d) for d in eixo],
                                                  "valores": _por_eixo(eixo, _chave_mes, datas)},
        "top_destinos": destinos.most_common(10), "top_origens": origens.most_common(5),
        "por_classificacao": classes.most_common(8), "interesses_ativos": len(interesses),
    }


# ---------------------------------------------------------------- promoções


def promocoes(db: Session, p: Periodo) -> dict:
    coletadas = db.scalars(select(Promocao).where(Promocao.coletada_em >= p.inicio_dt, Promocao.coletada_em < p.fim_dt)).all()
    por_fonte = Counter(NOMES_FONTE.get(x.fonte, x.fonte) for x in coletadas)
    por_tipo = Counter(x.tipo or "passagem" for x in coletadas)
    matches = db.scalar(select(func.count(Match.id)).where(Match.criado_em >= p.inicio_dt, Match.criado_em < p.fim_dt)) or 0
    enviadas = db.scalar(select(func.count(Match.id)).where(Match.status == "enviada", Match.enviado_em >= p.inicio_dt,
                                                           Match.enviado_em < p.fim_dt)) or 0
    com_interessado = db.scalar(select(func.count(func.distinct(Match.promocao_id))).join(Promocao).where(
        Promocao.coletada_em >= p.inicio_dt, Promocao.coletada_em < p.fim_dt)) or 0
    rotas: Counter = Counter()
    menor: dict[str, float] = {}
    precos = []
    for x in coletadas:
        if x.passagem and x.origem and x.destino:
            rota = f"{x.origem} → {x.destino}"
            rotas[rota] += 1
            valor = preco_reais(x)
            if valor:
                menor[rota] = min(menor.get(rota, valor), valor)
                precos.append(valor)
    return {
        "coletadas": len(coletadas), "ativas": sum(1 for x in coletadas if x.status == "ativa"),
        "descartadas": sum(1 for x in coletadas if x.status == "descartada"),
        "para_revisar": sum(1 for x in coletadas if x.precisa_revisao),
        "com_imagem": db.scalar(select(func.count(ImagemPromocao.id)).join(Promocao).where(
            Promocao.coletada_em >= p.inicio_dt, Promocao.coletada_em < p.fim_dt)) or 0,
        "por_fonte": por_fonte.most_common(), "por_tipo": [(NOMES_TIPO.get(t, t), n) for t, n in por_tipo.most_common()],
        "pacotes": por_tipo["pacote"], "hoteis": por_tipo["hotel"], "cruzeiros": por_tipo["cruzeiro"],
        "matches": matches, "enviadas": enviadas, "com_interessado": com_interessado,
        "taxa_envio": round(enviadas / matches * 100, 1) if matches else None,
        "top_rotas": [(r, n) for r, n in rotas.most_common(10)], "menor_preco": menor,
        "preco_medio": round(sum(precos) / len(precos), 2) if precos else None,
        "em_milhas": sum(1 for x in coletadas if x.milhas and not x.preco),
    }


# ---------------------------------------------------------------- milhas e CPFs


def milhas(db: Session, p: Periodo, hoje: date | None = None) -> dict:
    from .mvo.contas import resumo_por_programa

    estoque = resumo_por_programa(db)
    pontos = sum(g["pontos"] for g in estoque)
    custo = round(sum(g["custo"] for g in estoque), 2)
    lanc = db.scalars(
        select(Lancamento).where(Lancamento.status == "ativo", Lancamento.data_efetivacao >= p.inicio,
                                 Lancamento.data_efetivacao <= p.fim).options(selectinload(Lancamento.conta))
    ).all()
    por_tipo: dict[str, dict] = defaultdict(lambda: {"pontos": 0, "valor": 0.0, "qtd": 0})
    for l in lanc:
        t = por_tipo[l.tipo]
        t["pontos"] += l.pontos or 0
        t["valor"] += l.valor or 0
        t["qtd"] += 1
    compras_pontos = por_tipo["compra"]["pontos"] + por_tipo["clube"]["pontos"]
    compras_valor = round(por_tipo["compra"]["valor"] + por_tipo["clube"]["valor"], 2)
    eixo = meses(12, hoje)
    compras_12m = db.scalars(select(Lancamento).where(
        Lancamento.status == "ativo", Lancamento.tipo.in_(("compra", "clube")), Lancamento.data_efetivacao >= eixo[0])).all()
    return {
        "estoque": estoque, "pontos": pontos, "custo": custo,
        "custo_medio": round(custo / pontos * 1000, 2) if pontos and custo else None,
        "contas": sum(g["contas"] for g in estoque), "com_saldo": sum(g["com_saldo"] for g in estoque),
        "compras_pontos": compras_pontos, "compras_valor": compras_valor,
        "bonus": por_tipo["bonus"]["pontos"], "usados": por_tipo["emissao"]["pontos"] + por_tipo["resgate"]["pontos"],
        "expirados": por_tipo["expiracao"]["pontos"],
        "por_tipo": [(TIPOS_LANCAMENTO.get(t, {}).get("nome", t), d["pontos"]) for t, d in
                     sorted(por_tipo.items(), key=lambda kv: -kv[1]["pontos"])],
        "compras_por_mes": {"rotulos": [rotulo_mes(d) for d in eixo],
                            "valor": _por_eixo(eixo, lambda l: _chave_mes(l.data_efetivacao), compras_12m, lambda l: l.valor or 0),
                            "pontos": _por_eixo(eixo, lambda l: _chave_mes(l.data_efetivacao), compras_12m, lambda l: l.pontos or 0)},
    }


def cpfs(db: Session) -> dict:
    from .mvo.contas import situacoes

    contas = db.scalars(
        select(ContaPrograma).join(Colaborador)
        .where(ContaPrograma.ativa.is_(True), Colaborador.ativo.is_(True), ContaPrograma.uso_pessoal.is_(False))
    ).all()
    cfgs = {x.nome: x for x in db.scalars(select(ProgramaMilhas))}
    sits = situacoes(db, contas, cfgs)
    por_programa: dict[str, dict] = defaultdict(lambda: {"contas": 0, "limite": 0, "usados": 0, "livres": 0, "sem_limite": 0})
    for c in contas:
        s = sits[c.id]
        g = por_programa[c.programa]
        g["contas"] += 1
        g["usados"] += s.cpfs_usados
        if s.cpfs_limite is None:
            g["sem_limite"] += 1
        else:
            g["limite"] += s.cpfs_limite
            g["livres"] += s.cpfs_livres or 0
    linhas = sorted(({"programa": k, **v} for k, v in por_programa.items()), key=lambda g: -g["livres"])
    return {"por_programa": linhas, "usados": sum(g["usados"] for g in linhas), "livres": sum(g["livres"] for g in linhas),
            "limite": sum(g["limite"] for g in linhas), "contas": len(contas)}


# ---------------------------------------------------------------- funil


def funil(db: Session, p: Periodo) -> list[tuple[str, int]]:
    def contar(consulta) -> int:
        return db.scalar(consulta) or 0

    return [
        ("Promoções coletadas", contar(select(func.count(Promocao.id)).where(Promocao.coletada_em >= p.inicio_dt, Promocao.coletada_em < p.fim_dt))),
        ("Clientes com promoção", contar(select(func.count(Match.id)).where(Match.criado_em >= p.inicio_dt, Match.criado_em < p.fim_dt))),
        ("Promoções enviadas", contar(select(func.count(Match.id)).where(Match.status == "enviada", Match.enviado_em >= p.inicio_dt, Match.enviado_em < p.fim_dt))),
        ("Respostas recebidas", contar(select(func.count(RespostaWhatsApp.id)).where(RespostaWhatsApp.recebido_em >= p.inicio_dt, RespostaWhatsApp.recebido_em < p.fim_dt))),
        ("Pedidos de emissão", contar(select(func.count(PedidoEmissao.id)).where(PedidoEmissao.criado_em >= p.inicio_dt, PedidoEmissao.criado_em < p.fim_dt))),
        ("Emissões", contar(select(func.count(Emissao.id)).where(Emissao.status == "ativa", Emissao.data_emissao >= p.inicio_dt, Emissao.data_emissao < p.fim_dt))),
    ]


def tudo(db: Session, p: Periodo, conf: dict, hoje: date | None = None) -> dict:
    return {
        "periodo": p, "vendas": vendas(db, p), "semanas": vendas_por_semana(db, 12, hoje), "meses": vendas_por_mes(db, 12, hoje),
        "whatsapp": whatsapp(db, p, conf, hoje), "clientes": clientes(db, p, hoje), "promocoes": promocoes(db, p),
        "milhas": milhas(db, p, hoje), "cpfs": cpfs(db), "funil": funil(db, p),
    }
