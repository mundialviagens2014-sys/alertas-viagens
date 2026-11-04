"""Tela "Grupos do WhatsApp": ligar o número leitor (QR code ou código), escolher os grupos e acompanhar."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import busca, ia, leitura
from ..collectors import whatsapp
from ..config import settings
from ..database import get_db
from ..models import GrupoWhatsApp, Promocao, Usuario
from ..mvo.contas import registrar
from ..servicos import normalizar_telefone
from ..web import avisar, exigir, ip_de, render, voltar

rotas = APIRouter()
pode_alertas = exigir("alertas")
pode_configurar = exigir("configurar")

SITUACOES = {"desligado": "Desligado", "conectando": "Conectando…", "aguardando_qr": "Aguardando o QR code",
             "conectado": "Conectado", "desconectado": "Desconectado", "erro": "Com erro"}


def _resumo_grupos(db: Session) -> dict[str, int]:
    """Promoções que vieram de cada grupo nos últimos 7 dias (pelo nome do grupo na fonte)."""
    from datetime import datetime, timedelta

    limite = datetime.now() - timedelta(days=7)
    return dict(db.execute(
        select(Promocao.fonte_detalhe, func.count(Promocao.id))
        .where(Promocao.fonte == "whatsapp", Promocao.coletada_em >= limite).group_by(Promocao.fonte_detalhe)
    ).all())


@rotas.get("/whatsapp", response_class=HTMLResponse)
def tela_whatsapp(request: Request, q: str = "", ver: str = "", db: Session = Depends(get_db),
                  usuario: Usuario = Depends(pode_alertas)):
    todos = db.scalars(select(GrupoWhatsApp).order_by(GrupoWhatsApp.ativo.desc(), GrupoWhatsApp.nome)).all()
    ver = ver if ver in ("lidos", "nao_lidos") else ""
    grupos = [g for g in todos if busca.casa(q, [g.nome, g.jid]) and (
        not ver or (g.ativo if ver == "lidos" else not g.ativo))]
    promos_7d = _resumo_grupos(db)
    e = whatsapp.estado
    return render(request, "whatsapp.html", usuario, grupos=grupos, total=len(todos), q=q, ver=ver,
                  filtrando=bool(q.strip() or ver), promos_7d=promos_7d, estado=e, situacao=SITUACOES.get(e.status, e.status),
                  ligado=settings.whatsapp_leitura, ia_ligada=ia.configurada(), leituras_hoje=ia.leituras_hoje(db),
                  max_leituras=settings.gemini_max_por_dia, pendentes=leitura.pendentes(db),
                  sessao_guardada=whatsapp.sessao_guardada(), atualizar=e.status in ("conectando", "aguardando_qr"))


@rotas.get("/whatsapp/qr.png")
def qr_whatsapp(usuario: Usuario = Depends(pode_alertas)):
    png = whatsapp.qr_png()
    if not png:
        raise HTTPException(404)
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


@rotas.get("/whatsapp/estado")
def estado_whatsapp(usuario: Usuario = Depends(pode_alertas)):
    e = whatsapp.estado
    return JSONResponse({"status": e.status, "situacao": SITUACOES.get(e.status, e.status), "tem_qr": bool(e.qr),
                         "numero": e.numero, "erro": e.erro, "codigo": e.codigo_pareamento})


@rotas.post("/whatsapp/conectar")
def conectar_whatsapp(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    if not settings.whatsapp_leitura:
        avisar(request, "O leitor do WhatsApp está desligado (WHATSAPP_LEITURA=false).", "erro")
        return voltar("/whatsapp")
    if whatsapp.estado.status == "conectado":
        avisar(request, "O número já está conectado. Para trocar de número, desconecte primeiro.", "erro")
        return voltar("/whatsapp")
    whatsapp.parar()
    whatsapp.iniciar(novo=not whatsapp.sessao_guardada())
    registrar(db, usuario, "whatsapp_conectar", None, "ligou o leitor de grupos do WhatsApp", ip_de(request))
    db.commit()
    avisar(request, "Ligando… o QR code aparece em alguns segundos. No celular do número leitor: WhatsApp → "
                    "Aparelhos conectados → Conectar aparelho.")
    return voltar("/whatsapp")


@rotas.post("/whatsapp/parear")
def parear_whatsapp(request: Request, telefone: str = Form(...), usuario: Usuario = Depends(pode_configurar)):
    numero = normalizar_telefone(telefone)
    if not 12 <= len(numero) <= 15:
        avisar(request, "Informe o número do chip leitor com DDD, ex.: (85) 99999-9999.", "erro")
        return voltar("/whatsapp")
    try:
        whatsapp.pedir_codigo_pareamento(numero)
    except Exception as erro:
        avisar(request, f"Não consegui gerar o código: {erro}", "erro")
    return voltar("/whatsapp")


@rotas.post("/whatsapp/desconectar")
def desconectar_whatsapp(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    whatsapp.desconectar()
    registrar(db, usuario, "whatsapp_desconectar", None, "desconectou o número leitor do WhatsApp", ip_de(request))
    db.commit()
    avisar(request, "Número desconectado. Para ligar de novo, clique em “Conectar” e escaneie o QR code.")
    return voltar("/whatsapp")


@rotas.post("/whatsapp/grupos/atualizar")
def atualizar_grupos(request: Request, usuario: Usuario = Depends(pode_configurar)):
    try:
        novos = whatsapp.sincronizar_grupos()
        avisar(request, f"Lista atualizada: {novos} grupo(s) novo(s)." if novos else "Lista atualizada.")
    except Exception as erro:
        avisar(request, f"Não consegui listar os grupos: {erro}", "erro")
    return voltar("/whatsapp")


@rotas.post("/whatsapp/grupos/{grupo_id}/alternar")
def alternar_grupo(grupo_id: int, request: Request, db: Session = Depends(get_db),
                   usuario: Usuario = Depends(pode_configurar)):
    grupo = db.get(GrupoWhatsApp, grupo_id)
    if not grupo:
        raise HTTPException(404)
    grupo.ativo = not grupo.ativo
    db.commit()
    avisar(request, f"“{grupo.nome or grupo.jid}”: {'as mensagens passam a ser lidas' if grupo.ativo else 'deixou de ser lido'}.")
    return voltar("/whatsapp")


@rotas.post("/whatsapp/grupos/{grupo_id}/excluir")
def excluir_grupo(grupo_id: int, request: Request, db: Session = Depends(get_db),
                  usuario: Usuario = Depends(pode_configurar)):
    grupo = db.get(GrupoWhatsApp, grupo_id)
    if grupo:
        db.delete(grupo)
        db.commit()
    return voltar("/whatsapp")
