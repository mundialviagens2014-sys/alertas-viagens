"""CRM: o quadro (kanban) com a etapa de cada cliente, montado sozinho; arrastar move à mão."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from collections import Counter

from .. import aeroportos, busca, crm, graficos, ia
from .. import whatsapp_api as api
from ..database import get_db
from ..models import Cliente, Usuario
from ..permissoes import pode
from ..web import avisar, exigir, render, voltar

rotas = APIRouter()
pode_alertas = exigir("alertas")
DIAS = (30, 90, 365)


@rotas.get("/crm", response_class=HTMLResponse)
def quadro(request: Request, dias: int = 90, q: str = "", etapa: str = "", db: Session = Depends(get_db),
           usuario: Usuario = Depends(pode_alertas)):
    dias = dias if dias in DIAS else 90
    if ia.configurada():
        crm.classificar_pendentes(db, limite=2)  # respostas novas ganham intenção (poucas por vez: a tela não espera)
    todos = crm.cartoes(db, dias=dias)
    if q:
        todos = [c for c in todos if busca.casa(q, [c.cliente.nome, c.cliente.telefone, c.trecho, c.responsavel or "",
                                                     c.ultima_resposta or ""])]
    colunas = crm.colunas(todos)
    if etapa in crm.ETAPAS:
        colunas = [c for c in colunas if c["chave"] == etapa]
    # o leque de rotas: para onde vão os clientes em andamento, a partir da origem mais comum
    abertos = [c for c in todos if c.origem and c.destino and c.etapa in ("enviada", "respondeu", "conversa", "duvida", "pedido")]
    origens = Counter(c.origem for c in abertos)
    origem = origens.most_common(1)[0][0] if origens else None
    destinos = Counter(aeroportos.nome_curto(c.destino) for c in abertos if c.origem == origem)
    leque = graficos.leque(origem, destinos.most_common(7), maximo=7, sufixo=" clientes") if origem else None
    return render(request, "crm.html", usuario, colunas=colunas, etapas=crm.ETAPAS, dias=dias, q=q, etapa=etapa,
                  resumo=crm.resumo(todos, crm.vendas_no_periodo(db, dias)), api_pronta=api.configurada(db), opcoes_dias=DIAS,
                  pode_operar=pode(usuario, "milhas_operar"), leque=leque, leque_origem=origem,
                  leque_qtd=sum(destinos.values()))


@rotas.post("/crm/mover")
def mover(request: Request, cliente_id: int = Form(...), etapa: str = Form(...), motivo: str = Form(""),
          db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    if etapa not in crm.ETAPAS or not db.get(Cliente, cliente_id):
        raise HTTPException(400)
    crm.mover(db, cliente_id, etapa, usuario.nome, motivo.strip()[:255])
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"ok": True, "etapa": etapa, "nome": crm.ETAPAS[etapa]["nome"]})
    avisar(request, f"Cliente movido para “{crm.ETAPAS[etapa]['nome']}”.")
    return voltar("/crm")


@rotas.post("/crm/automatico")
def automatico(request: Request, cliente_id: int = Form(...), db: Session = Depends(get_db),
               usuario: Usuario = Depends(pode_alertas)):
    crm.voltar_ao_automatico(db, cliente_id)
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"ok": True})
    avisar(request, "O cartão voltou a ser movido pelo sistema.")
    return voltar("/crm")
