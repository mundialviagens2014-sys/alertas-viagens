"""Resumo do administrador: a coluna da direita que aparece nas telas largas (números e gráficos principais).

Os números vêm dos mesmos cálculos dos Relatórios; ficam guardados por alguns minutos para não pesar a cada página."""
from collections import Counter
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import aeroportos, relatorios
from . import whatsapp_api as api
from .models import Cliente, Interesse, Match, Promocao

DIAS = 14
VALIDADE = timedelta(minutes=3)
_cache: dict = {}


def limpar() -> None:
    _cache.clear()


def _por_dia(eixo: list[date], datas) -> list[int]:
    contagem = Counter(d.date() for d in datas if d)
    return [contagem.get(d, 0) for d in eixo]


def resumo(db: Session, hoje: date | None = None) -> dict:
    agora = datetime.now()
    if _cache.get("quando") and agora - _cache["quando"] < VALIDADE and not hoje:
        return _cache["dados"]
    hoje = hoje or date.today()
    eixo = [hoje - timedelta(days=DIAS - 1 - i) for i in range(DIAS)]
    inicio = datetime.combine(eixo[0], time.min)

    lidas = _por_dia(eixo, db.scalars(select(Promocao.coletada_em).where(Promocao.coletada_em >= inicio)))
    enviadas = _por_dia(eixo, db.scalars(select(Match.enviado_em).where(
        Match.status == "enviada", Match.enviado_em >= inicio)))

    mes = relatorios.periodo_de("mes", hoje=hoje)
    vendas = relatorios.vendas(db, mes)
    whats = relatorios.whatsapp(db, mes, api.configuracao(db), hoje)
    por_mes = relatorios.vendas_por_mes(db, 6, hoje)

    ativos = db.scalar(select(func.count(Cliente.id)).where(Cliente.ativo.is_(True))) or 0
    novos = db.scalar(select(func.count(Cliente.id)).where(Cliente.criado_em >= mes.inicio_dt)) or 0
    trechos = db.execute(select(Interesse.origem, Interesse.destino).join(Cliente)
                         .where(Interesse.ativo.is_(True), Cliente.ativo.is_(True))).all()
    origem = Counter(o.upper() for o, _ in trechos if o).most_common(1)
    destinos = Counter(aeroportos.nome_curto(d) for _, d in trechos if d).most_common(5)

    dados = {
        "rotulos": [d.strftime("%d") for d in eixo],   # só o dia: 14 rótulos cabem na coluna
        "lidas": lidas, "enviadas": enviadas, "total_lidas": sum(lidas), "total_enviadas": sum(enviadas),
        "vendas_qtd": vendas["qtd"], "vendido": vendas["vendido"], "lucro": vendas["lucro"],
        "gasto_whatsapp": whats["custo"], "respostas": whats["respostas"],
        "meses": por_mes["rotulos"], "valor_meses": por_mes["valor"],
        "clientes_ativos": ativos, "clientes_novos": novos,
        "origem": origem[0][0] if origem else "", "destinos": destinos,
        "atualizado": agora,
    }
    _cache.update(quando=agora, dados=dados)
    return dados
