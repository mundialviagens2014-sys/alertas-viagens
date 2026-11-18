"""Pergunte ao MVO: o assistente de voz (e texto) do app.

A pessoa pergunta em português ("quantos clientes estão em conversa?") e o sistema responde com os números do
próprio MVO. Funciona em duas etapas:

1. Entender a pergunta: primeiro por regras simples (sem custo e sem internet); se as regras não entenderem e o
   Gemini estiver configurado, o Gemini escolhe qual consulta fazer. Ele recebe só a frase da pessoa e a lista de
   consultas possíveis; nenhum dado de cliente vai para o Gemini.
2. Responder: a consulta roda aqui, com as mesmas funções das telas (os números batem com o que se vê no sistema).

Só leitura: o assistente não envia, não apaga e não muda nada, e respeita as permissões de quem pergunta.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import aeroportos, crm, ia, relatorios
from .config import settings
from .models import Cliente, ConfirmacaoCadastro, Interesse, Match, Promocao
from .models_mvo import PedidoEmissao
from .permissoes import pode
from .servicos import preco_reais

log = logging.getLogger(__name__)

CONSULTAS = {
    "funil_etapa": "quantos clientes estão numa etapa do funil do CRM (e quem são)",
    "funil_resumo": "o resumo do funil: quantos clientes em cada etapa",
    "fila": "quantos envios de promoção estão pendentes na fila",
    "clientes": "quantos clientes ativos existem e quantos são novos no período",
    "interessados": "quantos clientes querem viajar para um destino (ou saindo de uma origem)",
    "promocoes": "quantas promoções foram encontradas no período (pode filtrar por destino)",
    "promocao_barata": "a promoção vigente mais barata (pode filtrar por destino ou origem)",
    "respostas": "quantas respostas de clientes no WhatsApp ainda não foram lidas",
    "pedidos": "quantos pedidos de emissão estão aguardando",
    "vendas": "quantas emissões (vendas) no período, valor vendido e lucro",
    "cliente": "os dados de um cliente pelo nome: telefone, trechos de interesse e etapa no funil",
}
PERIODOS = {"hoje": "hoje", "7": "nos últimos 7 dias", "mes": "este mês", "mes_anterior": "no mês passado",
            "30": "nos últimos 30 dias", "90": "nos últimos 90 dias", "ano": "este ano", "tudo": "desde o início"}
PERMISSAO = {"pedidos": "milhas_ver", "vendas": "milhas_ver"}   # o resto é dos Alertas/CRM

SUGESTOES = ["Quantos clientes estão em conversa?", "Quantas promoções para enviar?", "Vendas deste mês",
             "Promoção mais barata para Lisboa", "Quem respondeu no WhatsApp?", "Pedidos aguardando emissão"]


@dataclass
class Pedido:
    consulta: str
    etapa: str | None = None
    periodo: str | None = None
    origem: str | None = None
    destino: str | None = None
    nome: str | None = None


@dataclass
class Resposta:
    texto: str
    detalhes: list[str] = field(default_factory=list)
    link: str | None = None
    como: str = "regras"

    def json(self) -> dict:
        return {"texto": self.texto, "detalhes": self.detalhes, "link": self.link, "como": self.como}


# ---------------------------------------------------------------- entender a pergunta (regras)

ETAPA_POR_PALAVRA = [
    (r"cadastrad", "cadastrado"), (r"promo\w* enviad|receberam (a )?promo", "enviada"), (r"respond", "respondeu"),
    (r"conversa", "conversa"), (r"duvida", "duvida"), (r"pedido de emiss", "pedido"),
    (r"venda conclu|conclu|ganh|fechad|aprovad", "ganho"), (r"perdid", "perdido"),
]


def _limpo(texto: str) -> str:
    t = aeroportos.sem_acento((texto or "").lower())
    t = re.sub(r"^\s*(ok|ei|e ai|oi)?\s*(google|gemini|mvo)[,\s]*", "", t)
    return re.sub(r"\s+", " ", t).strip(" ?!.")


def _periodo(t: str) -> str | None:
    if "hoje" in t:
        return "hoje"
    if re.search(r"semana|7 dias|sete dias", t):
        return "7"
    if re.search(r"mes passado|ultimo mes|mes anterior", t):
        return "mes_anterior"
    if re.search(r"(deste|desse|neste|nesse|do|este|esse) mes|no mes|mensal", t):
        return "mes"
    if "90 dias" in t or "tres meses" in t or "trimestre" in t:
        return "90"
    if "30 dias" in t:
        return "30"
    if re.search(r"(deste|desse|neste|nesse|este|esse|do) ano", t):
        return "ano"
    return None


def _lugar(t: str, depois: str) -> str | None:
    """Código do aeroporto/cidade que vem depois de "para", "pra", "de" etc. (tenta 3, 2 e 1 palavras)."""
    m = re.search(rf"\b(?:{depois})\s+(.+)$", t)
    if not m:
        return None
    palavras = re.sub(r"[^a-z0-9 ]", " ", m.group(1)).split()
    for n in (3, 2, 1):
        if len(palavras) >= n:
            codigo = aeroportos.resolver(" ".join(palavras[:n]))
            if codigo:
                return codigo
    return None


def entender_por_regras(pergunta: str) -> Pedido | None:
    t = _limpo(pergunta)
    if not t:
        return None
    periodo = _periodo(t)
    destino = _lugar(t, "para|pra|pro|a|em|destino")
    origem = _lugar(t, "saindo de|de|partindo de|origem")
    if re.search(r"pedido", t) and re.search(r"aguard|abert|pendent|emitir|falta", t):
        return Pedido("pedidos")
    if re.search(r"\b(vend|fatur|lucr|emiss|emitid)", t) and not re.search(r"cliente.*(etapa|funil)", t):
        return Pedido("vendas", periodo=periodo or "mes")
    if re.search(r"respost|mensag|respond\w* no whats|nao lid", t) and re.search(r"whats|nao lid|nova|quem", t):
        return Pedido("respostas")
    if re.search(r"\bfila\b|para enviar|pra enviar|pendent|envios", t):
        return Pedido("fila")
    if re.search(r"mais barat|menor preco|melhor preco|mais em conta", t):
        return Pedido("promocao_barata", destino=destino, origem=origem if not destino else None)
    if re.search(r"\bpromo", t):
        return Pedido("promocoes", periodo=periodo or "hoje", destino=destino)
    if re.search(r"interess|querem (ir|viajar)|procuram", t) and (destino or origem):
        return Pedido("interessados", destino=destino, origem=origem)
    for padrao, etapa in ETAPA_POR_PALAVRA:
        if re.search(padrao, t):
            return Pedido("funil_etapa", etapa=etapa)
    if "funil" in t or "crm" in t:
        return Pedido("funil_resumo")
    if re.search(r"quantos clientes|clientes (ativos|cadastrados|novos)|total de clientes", t):
        return Pedido("clientes", periodo=periodo or "mes")
    m = re.search(r"(?:dados|telefone|contato|numero|whatsapp|ficha)\s+d[oa]\s+(?:cliente\s+)?([a-z][a-z ]{2,40})$"
                  r"|(?:^|\s)(?:cliente|quem e)\s+([a-z][a-z ]{2,40})$", t)
    if m:
        return Pedido("cliente", nome=(m.group(1) or m.group(2)).strip())
    return None


# ---------------------------------------------------------------- entender a pergunta (Gemini)

ESQUEMA = {
    "type": "OBJECT",
    "properties": {
        "consulta": {"type": "STRING", "enum": [*CONSULTAS, "nenhuma"]},
        "etapa": {"type": "STRING", "enum": [*crm.ETAPAS, "nenhuma"]},
        "periodo": {"type": "STRING", "enum": [*PERIODOS, "nenhum"]},
        "origem": {"type": "STRING", "description": "cidade de saída citada, se houver"},
        "destino": {"type": "STRING", "description": "cidade de destino citada, se houver"},
        "nome": {"type": "STRING", "description": "nome do cliente citado, se a pergunta for sobre um cliente"},
    },
    "required": ["consulta"],
}


def _instrucoes() -> str:
    consultas = "\n".join(f"- {k}: {v}" for k, v in CONSULTAS.items())
    etapas = "\n".join(f"- {k}: {v['nome']} ({v['ajuda']})" for k, v in crm.ETAPAS.items())
    return ("Você interpreta perguntas da equipe de uma agência de viagens sobre o sistema interno dela (MVO). "
            "Escolha a consulta que responde à pergunta e preencha os filtros citados. Se nenhuma servir, use "
            f"\"nenhuma\".\nConsultas:\n{consultas}\nEtapas do funil do CRM:\n{etapas}\n"
            "\"Aprovados\", \"fechados\" ou \"que compraram\" = etapa ganho. Períodos: hoje, 7 (semana), mes, "
            "mes_anterior, 30, 90, ano, tudo.\nPergunta:\n")


def entender_pela_ia(pergunta: str, db: Session) -> Pedido | None:
    if not ia.cota_disponivel(db):
        return None
    try:
        dados = ia.gerar_json([{"text": _instrucoes() + pergunta.strip()[:400]}], ESQUEMA, timeout=20, temperatura=0.0)
        ia.registrar_uso(db)
    except Exception as erro:   # sem internet, cota, chave: a resposta diz que não entendeu
        log.warning("Assistente: Gemini não interpretou a pergunta: %s", erro)
        return None
    if not isinstance(dados, dict) or dados.get("consulta") not in CONSULTAS:
        return None
    vazio = lambda v: None if not v or v in ("nenhuma", "nenhum") else v  # noqa: E731
    return Pedido(dados["consulta"], etapa=vazio(dados.get("etapa")), periodo=vazio(dados.get("periodo")),
                  origem=aeroportos.resolver(dados.get("origem") or "") if dados.get("origem") else None,
                  destino=aeroportos.resolver(dados.get("destino") or "") if dados.get("destino") else None,
                  nome=vazio(dados.get("nome")))


# ---------------------------------------------------------------- responder (só leitura)

def _n(qtd: int, singular: str, plural: str) -> str:
    return f"{qtd} {singular if qtd == 1 else plural}"


def _reais(v: float | None) -> str:
    return "R$ " + f"{v or 0:,.0f}".replace(",", ".")


def _periodo_rel(chave: str) -> relatorios.Periodo:
    hoje = date.today()
    if chave == "hoje":
        return relatorios.periodo_de("personalizado", de=hoje, ate=hoje)
    if chave == "7":
        return relatorios.periodo_de("personalizado", de=hoje - timedelta(days=6), ate=hoje)
    return relatorios.periodo_de(chave if chave in relatorios.PERIODOS else "mes")


def _nomes(cartoes) -> list[str]:
    return [c.cliente.nome + (f" · {c.trecho}" if c.trecho else "") for c in cartoes[:5]]


def executar(p: Pedido, db: Session, usuario) -> Resposta:
    if not pode(usuario, PERMISSAO.get(p.consulta, "alertas")):
        return Resposta("Você não tem acesso a essa informação no MVO.")

    if p.consulta in ("funil_etapa", "funil_resumo"):
        cartoes = crm.cartoes(db, dias=90)
        if p.consulta == "funil_etapa" and p.etapa in crm.ETAPAS:
            da_etapa = [c for c in cartoes if c.etapa == p.etapa]
            nome = crm.ETAPAS[p.etapa]["nome"]
            valor = sum(c.valor or 0 for c in da_etapa)
            texto = (f"{_n(len(da_etapa), 'cliente está', 'clientes estão')} na etapa {nome}"
                     + (f", somando {_reais(valor)}" if valor else "") + ".")
            if not da_etapa:
                texto = f"Nenhum cliente na etapa {nome} agora."
            return Resposta(texto, _nomes(da_etapa), "/crm")
        r = crm.resumo(cartoes, crm.vendas_no_periodo(db, 90))
        por_etapa = [f"{v['nome']}: {sum(1 for c in cartoes if c.etapa == k)}" for k, v in crm.ETAPAS.items()]
        return Resposta(f"{_n(r['abertos'], 'cliente', 'clientes')} em andamento no funil, "
                        f"{r['aguardando_agencia']} esperando resposta da agência e {r['ganhos']} vendas em 90 dias.",
                        por_etapa, "/crm")

    if p.consulta == "fila":
        limite = datetime.now() - timedelta(days=settings.promo_validade_dias)
        base = (select(Match).join(Promocao).join(Cliente)
                .where(Match.status == "nova", Promocao.status == "ativa", Promocao.coletada_em >= limite,
                       Cliente.ativo.is_(True)))
        sub = base.subquery()
        envios = db.scalar(select(func.count()).select_from(sub)) or 0
        promos = db.scalar(select(func.count(func.distinct(sub.c.promocao_id)))) or 0
        confirmacoes = db.scalar(select(func.count(ConfirmacaoCadastro.id)).join(Cliente).where(
            ConfirmacaoCadastro.status == "nova", Cliente.ativo.is_(True))) or 0
        texto = (f"{_n(envios, 'envio pendente', 'envios pendentes')} em {_n(promos, 'promoção', 'promoções')}"
                 + (f", e {_n(confirmacoes, 'confirmação', 'confirmações')} de cadastro" if confirmacoes else "") + ".")
        return Resposta(texto if envios or confirmacoes else "A fila está vazia: nada para enviar agora.", [], "/")

    if p.consulta == "clientes":
        per = _periodo_rel(p.periodo or "mes")
        ativos = db.scalar(select(func.count(Cliente.id)).where(Cliente.ativo.is_(True))) or 0
        novos = db.scalar(select(func.count(Cliente.id)).where(Cliente.criado_em >= per.inicio_dt,
                                                              Cliente.criado_em < per.fim_dt)) or 0
        return Resposta(f"{_n(ativos, 'cliente ativo', 'clientes ativos')}; "
                        f"{_n(novos, 'novo', 'novos')} {PERIODOS.get(p.periodo or 'mes', '')}.", [], "/clientes")

    if p.consulta == "interessados":
        q = select(Cliente.nome, Interesse.origem, Interesse.destino).join(Cliente).where(
            Interesse.ativo.is_(True), Cliente.ativo.is_(True))
        linhas = db.execute(q).all()
        if p.destino:
            linhas = [l for l in linhas if l.destino and aeroportos.mesmo_lugar(l.destino, p.destino)]
        if p.origem:
            linhas = [l for l in linhas if l.origem and aeroportos.mesmo_lugar(l.origem, p.origem)]
        lugar = " e ".join(x for x in (f"saindo de {aeroportos.nome_curto(p.origem)}" if p.origem else "",
                                       f"para {aeroportos.nome_curto(p.destino)}" if p.destino else "") if x)
        nomes = sorted({l.nome for l in linhas})
        return Resposta(f"{_n(len(nomes), 'cliente quer', 'clientes querem')} viajar {lugar}.", nomes[:5], "/interesses")

    if p.consulta in ("promocoes", "promocao_barata"):
        limite = datetime.now() - timedelta(days=settings.promo_validade_dias)
        q = select(Promocao).where(Promocao.status == "ativa")
        if p.consulta == "promocoes":
            per = _periodo_rel(p.periodo or "hoje")
            q = q.where(Promocao.coletada_em >= per.inicio_dt, Promocao.coletada_em < per.fim_dt)
        else:
            q = q.where(Promocao.coletada_em >= limite)
        promos = db.scalars(q).all()
        if p.destino:
            promos = [x for x in promos if x.destino and aeroportos.mesmo_lugar(x.destino, p.destino)]
        if p.origem:
            promos = [x for x in promos if x.origem and aeroportos.mesmo_lugar(x.origem, p.origem)]
        para = f" para {aeroportos.nome_curto(p.destino)}" if p.destino else ""
        if p.consulta == "promocoes":
            return Resposta(f"{_n(len(promos), 'promoção encontrada', 'promoções encontradas')}{para} "
                            f"{PERIODOS.get(p.periodo or 'hoje', '')}.", [], "/promocoes")
        com_preco = sorted((x for x in promos if preco_reais(x)), key=preco_reais)
        if not com_preco:
            return Resposta(f"Nenhuma promoção vigente{para} com preço em reais.", [], "/promocoes")
        b = com_preco[0]
        trecho = f"{aeroportos.nome_curto(b.origem)} → {aeroportos.nome_curto(b.destino)}" if b.origem else (b.titulo or "")
        detalhes = [f"{aeroportos.nome_curto(x.origem)} → {aeroportos.nome_curto(x.destino)}: {_reais(preco_reais(x))}"
                    for x in com_preco[1:4] if x.origem]
        return Resposta(f"A mais barata{para} é {trecho} por {_reais(preco_reais(b))}"
                        + (f", pela {b.companhia}" if b.companhia else "") + ".", detalhes, "/promocoes")

    if p.consulta == "respostas":
        from . import whatsapp_api
        n = whatsapp_api.respostas_nao_lidas(db) if whatsapp_api.configurada(db) else 0
        return Resposta(f"{_n(n, 'resposta nova', 'respostas novas')} no WhatsApp." if n
                        else "Nenhuma resposta nova no WhatsApp.", [], "/whatsapp/respostas")

    if p.consulta == "pedidos":
        pedidos = db.scalars(select(PedidoEmissao).where(PedidoEmissao.status == "aguardando")
                             .order_by(PedidoEmissao.criado_em)).all()
        detalhes = [f"{x.cliente_nome or x.requisitante_nome or 'Pedido'} · {x.origem or ''}→{x.destino or ''}".strip(" ·→")
                    for x in pedidos[:5]] if pedidos and hasattr(PedidoEmissao, "cliente_nome") else []
        return Resposta(f"{_n(len(pedidos), 'pedido aguardando', 'pedidos aguardando')} emissão." if pedidos
                        else "Nenhum pedido de emissão aguardando.", detalhes, "/pedidos")

    if p.consulta == "vendas":
        chave = p.periodo or "mes"
        v = relatorios.vendas(db, _periodo_rel(chave))
        texto = f"{_n(v['qtd'], 'emissão', 'emissões')} {PERIODOS.get(chave, '')}, {_reais(v['vendido'])} vendidos"
        if pode(usuario, "custos") and v.get("com_lucro"):
            texto += f" e {_reais(v['lucro'])} de lucro"
        return Resposta(texto + ".", [], "/emissoes")

    if p.consulta == "cliente" and p.nome:
        # sem acento e sem diferença de maiúsculas ("joao" acha "João")
        termo = aeroportos.sem_acento(p.nome.strip().lower())
        ids = [i for i, nome in db.execute(select(Cliente.id, Cliente.nome)).all()
               if termo in aeroportos.sem_acento((nome or "").lower())][:5]
        achados = [db.get(Cliente, i) for i in ids]
        if not achados:
            return Resposta(f"Não achei cliente com o nome {p.nome}.", [], "/clientes")
        c = achados[0]
        etapa = next((crm.ETAPAS[x.etapa]["nome"] for x in crm.cartoes(db, dias=90) if x.cliente.id == c.id), None)
        trechos = ", ".join(f"{i.origem or '?'}→{i.destino or 'qualquer lugar'}" for i in c.interesses if i.ativo) \
            if hasattr(c, "interesses") else ""
        detalhes = [x for x in (f"Telefone: {c.telefone}" if c.telefone else "", f"Quer: {trechos}" if trechos else "",
                                f"Funil: {etapa}" if etapa else "") if x]
        if len(achados) > 1:
            detalhes.append("Outros com esse nome: " + ", ".join(x.nome for x in achados[1:]))
        return Resposta(f"{c.nome}{'' if c.ativo else ' (inativo)'}.", detalhes, f"/clientes/{c.id}")

    return Resposta("Não entendi a pergunta.")


def perguntar(pergunta: str, db: Session, usuario) -> Resposta:
    pergunta = (pergunta or "").strip()
    if not pergunta:
        return Resposta("Faça uma pergunta sobre o MVO.", SUGESTOES[:3])
    pedido, como = entender_por_regras(pergunta), "regras"
    if pedido is None and ia.configurada():
        pedido, como = entender_pela_ia(pergunta, db), "ia"
    if pedido is None:
        return Resposta("Não entendi. Tente perguntar de outro jeito, por exemplo:", SUGESTOES[:4])
    resposta = executar(pedido, db, usuario)
    resposta.como = como
    return resposta
