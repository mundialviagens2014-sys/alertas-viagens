"""Relatórios do administrador: indicadores e gráficos de vendas, WhatsApp, clientes, promoções, milhas e CPFs,
e a planilha com os dados brutos de todas as tabelas."""
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from .. import dados_brutos, graficos, relatorios
from .. import whatsapp_api as api
from ..database import get_db
from ..exportar import XLSX
from ..models import Usuario
from ..mvo.contas import registrar
from ..web import exigir_admin, ip_de, ler_data, render

rotas = APIRouter()
so_admin = exigir_admin()
CORES_INTENCAO = {"interesse": graficos.VERDE, "duvida": "#9a6700", "recusa": graficos.VERMELHO, "sair": "#475569",
                  "outro": graficos.AZUL_MEDIO}


@rotas.get("/relatorios", response_class=HTMLResponse)
def tela(request: Request, periodo: str = "mes", de: str = "", ate: str = "", db: Session = Depends(get_db),
         usuario: Usuario = Depends(so_admin)):
    data_de, data_ate = ler_data(de), ler_data(ate)
    if data_de or data_ate:
        periodo = "personalizado"
    p = relatorios.periodo_de(periodo if periodo in relatorios.PERIODOS else "mes", data_de, data_ate)
    conf = api.configuracao(db)
    r = relatorios.tudo(db, p, conf)
    v, w, c, pr, m = r["vendas"], r["whatsapp"], r["clientes"], r["promocoes"], r["milhas"]
    g = {
        "vendas_semana_qtd": graficos.barras(list(zip(r["semanas"]["rotulos"], r["semanas"]["qtd"])), formato="int"),
        "vendas_semana_valor": graficos.barras(list(zip(r["semanas"]["rotulos"], r["semanas"]["valor"])), formato="dinheiro",
                                               cor=graficos.LARANJA),
        "vendas_mes": graficos.barras_empilhadas(r["meses"]["rotulos"], [
            {"nome": "Vendido (R$)", "cor": graficos.AZUL, "valores": r["meses"]["valor"]}], formato="dinheiro", legenda=False),
        "vendas_mes_qtd": graficos.barras(list(zip(r["meses"]["rotulos"], r["meses"]["qtd"])), formato="int", cor=graficos.VERDE),
        "lucro_mes": graficos.barras(list(zip(r["meses"]["rotulos"], r["meses"]["lucro"])), formato="dinheiro", cor=graficos.VERDE),
        "whatsapp_mes": graficos.barras_empilhadas(w["por_mes"]["rotulos"], [
            {"nome": "Promoções", "cor": graficos.AZUL, "valores": w["por_mes"]["promocoes"]},
            {"nome": "Confirmações", "cor": graficos.LARANJA, "valores": w["por_mes"]["confirmacoes"]}], formato="dinheiro"),
        "whatsapp_status": graficos.rosca([("Entregues", w["promocoes"].get("entregue", 0)), ("Lidas", w["promocoes"].get("lida", 0)),
                                           ("Enviadas (sem confirmação)", w["promocoes"].get("enviada", 0)),
                                           ("Falharam", w["promocoes"].get("falhou", 0))],
                                          cores=[graficos.AZUL, graficos.VERDE, graficos.AZUL_MEDIO, graficos.VERMELHO]),
        "intencoes": graficos.rosca([(nome, n) for _, nome, n in w["intencoes"]],
                                    cores=[CORES_INTENCAO.get(chave, graficos.AZUL_MEDIO) for chave, _, _ in w["intencoes"]]),
        "clientes_mes": graficos.barras(list(zip(c["novos_por_mes"]["rotulos"], c["novos_por_mes"]["valores"])), formato="int"),
        "destinos": graficos.leque(c["origem_principal"] or "—", c["top_destinos"],
                                   sufixo=" clientes"),
        "promo_fonte": graficos.rosca(pr["por_fonte"]),
        "promo_tipo": graficos.rosca(pr["por_tipo"], cores=[graficos.AZUL, "#7c5cbf", graficos.LARANJA, "#0f8b8d", "#475569"]),
        "rotas": graficos.barras_horizontais(pr["top_rotas"]),
        "programas": graficos.barras_horizontais(v["por_programa"]),
        "programas_valor": graficos.barras_horizontais(v["valor_por_programa"], formato="dinheiro", cor=graficos.LARANJA),
        "emissores": graficos.barras_horizontais(v["por_emissor"], formato="dinheiro", cor=graficos.VERDE),
        "companhias": graficos.barras_horizontais(v["por_companhia"]),
        "agencias": graficos.barras_horizontais(v["por_agencia"], formato="dinheiro"),
        "milhas_estoque": graficos.barras_horizontais([(x["programa"], x["pontos"]) for x in m["estoque"]], formato="pontos"),
        "compras_mes": graficos.barras(list(zip(m["compras_por_mes"]["rotulos"], m["compras_por_mes"]["valor"])), formato="dinheiro",
                                       cor="#7c5cbf"),
        "funil": graficos.funil(r["funil"]),
    }
    return render(request, "relatorios.html", usuario, r=r, g=g, periodos=relatorios.PERIODOS, periodo=p,
                  de=data_de.isoformat() if data_de else "", ate=data_ate.isoformat() if data_ate else "",
                  pagina="configuracoes", hoje=date.today(), formatar=graficos.formatar)


@rotas.get("/relatorios/dados-brutos.xlsx")
def dados(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(so_admin)):
    conteudo, resumo = dados_brutos.gerar(db)
    registrar(db, usuario, "exportacao", None,
              f"Dados brutos: {len(resumo)} tabelas, {sum(n for _, n in resumo)} linhas", ip_de(request))
    db.commit()
    nome = f"mvo-dados-brutos-{date.today():%Y-%m-%d}.xlsx"
    return Response(conteudo, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{nome}"',
                                                        "Cache-Control": "no-store"})
