"""CRM: o quadro (kanban) com a etapa de cada cliente, montado sozinho; arrastar move à mão."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from collections import Counter

from .. import aeroportos, busca, crm, graficos, ia, reengajamento
from .. import whatsapp_api as api
from ..database import get_db
from ..models import Cliente, ConversaAtendimento, Match, Usuario
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


@rotas.post("/crm/conversa-0400")
def conversa_0400(request: Request, cliente_id: int = Form(...), db: Session = Depends(get_db),
                  usuario: Usuario = Depends(pode_alertas)):
    """Abre a conversa com o cliente no WhatsApp do atendimento (0400) e registra no CRM ("Em conversa").

    O 0500 (API) só dispara as promoções e recebe as respostas; a conversa com o cliente segue no 0400, que é o
    aplicativo WhatsApp Business: o link wa.me abre no WhatsApp que estiver aberto no computador ou no celular."""
    from sqlalchemy import select

    from ..servicos import descrever_trecho, link_whatsapp, nome_sobrenome

    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        raise HTTPException(404)
    db.add(ConversaAtendimento(cliente_id=cliente.id, aberta_por=usuario.nome))
    db.commit()
    ultima = db.scalars(select(Match).where(Match.cliente_id == cliente.id, Match.status == "enviada")
                        .order_by(Match.enviado_em.desc()).limit(1)).first()
    primeiro = nome_sobrenome(cliente.nome).split(" ")[0]
    texto = f"Olá, {primeiro}! Aqui é da Mundial Viagens"
    texto += f", sobre a promoção {descrever_trecho(ultima.promocao)} que enviamos." if ultima else "."
    texto += " Como posso te ajudar?"
    return RedirectResponse(link_whatsapp(cliente.telefone, texto), status_code=303)


# ---------------------------------------------------------------- trazer de volta


def _candidato(db: Session, cliente_id: int, de: str = ""):
    if de == "cliente":
        cliente = db.get(Cliente, cliente_id)
        if not cliente:
            raise HTTPException(404)
        return reengajamento.candidato_de(db, cliente)[0]
    alvo = next((c for c in reengajamento.candidatos(db) if c.cliente.id == cliente_id), None)
    if alvo is None:
        raise HTTPException(404, "Esse cliente não está mais na lista (já recebeu mensagem, respondeu ou comprou).")
    return alvo


@rotas.get("/crm/reengajar", response_class=HTMLResponse)
def tela_reengajar(request: Request, motivo: str = "", db: Session = Depends(get_db),
                   usuario: Usuario = Depends(pode_alertas)):
    from ..web import endereco_site

    todos = reengajamento.candidatos(db)
    lista = [c for c in todos if not motivo or c.motivo == motivo]
    site = endereco_site(request)
    return render(request, "reengajar.html", usuario, lista=lista, motivo=motivo, motivos=reengajamento.MOTIVOS,
                  contagem=Counter(c.motivo for c in todos), conf=reengajamento.configuracao(db),
                  variaveis=reengajamento.VARIAVEIS, api_pronta=api.configurada(db),
                  previa=reengajamento.texto_manual(db, lista[0], site) if lista else "")


@rotas.post("/crm/reengajar/configuracoes")
def salvar_reengajar(request: Request, dias_sem_resposta: str = Form(""), dias_sem_compra: str = Form(""),
                     intervalo_dias: str = Form(""), modelo: str = Form(""), variaveis: str = Form(""),
                     texto: str = Form(""), db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    if not pode(usuario, "configurar"):
        raise HTTPException(403)
    reengajamento.salvar_configuracao(db, {
        "dias_sem_resposta": dias_sem_resposta or "30", "dias_sem_compra": dias_sem_compra or "30",
        "intervalo_dias": intervalo_dias or "30", "modelo": modelo, "variaveis": variaveis or "nome, pagina",
        "texto": texto or reengajamento.CAMPOS["texto"]})
    avisar(request, "Configurações de Trazer de volta salvas.")
    return voltar("/crm/reengajar")


@rotas.post("/crm/reengajar/{cliente_id}/whatsapp")
def reengajar_whatsapp(cliente_id: int, request: Request, de: str = Form(""), db: Session = Depends(get_db),
                       usuario: Usuario = Depends(pode_alertas)):
    """Abre o WhatsApp da equipe com a mensagem de retorno pronta e registra (o cliente sai da lista)."""
    from ..servicos import link_whatsapp
    from ..web import endereco_site

    alvo = _candidato(db, cliente_id, de)
    texto = reengajamento.texto_manual(db, alvo, endereco_site(request))
    reengajamento.registrar(db, cliente_id, alvo.motivo, "whatsapp", usuario.nome)
    return RedirectResponse(link_whatsapp(alvo.cliente.telefone, texto), status_code=303)


@rotas.post("/crm/reengajar/{cliente_id}/api")
def reengajar_api(cliente_id: int, request: Request, de: str = Form(""), db: Session = Depends(get_db),
                  usuario: Usuario = Depends(pode_alertas)):
    from ..web import endereco_site

    r = reengajamento.enviar_api(db, _candidato(db, cliente_id, de), endereco_site(request), usuario.nome)
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"ok": r.ok, "motivo": r.motivo}, status_code=200 if r.ok else 400)
    avisar(request, "Mensagem de retorno enviada pelo 0500." if r.ok else f"Não enviou: {r.motivo}", "ok" if r.ok else "erro")
    return voltar(f"/clientes/{cliente_id}" if de == "cliente" else "/crm/reengajar")


@rotas.post("/crm/reengajar/{cliente_id}/pular")
def reengajar_pular(cliente_id: int, request: Request, db: Session = Depends(get_db),
                    usuario: Usuario = Depends(pode_alertas)):
    alvo = _candidato(db, cliente_id)
    reengajamento.registrar(db, cliente_id, alvo.motivo, "pulado", usuario.nome)
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"ok": True})
    return voltar("/crm/reengajar")


@rotas.post("/crm/reengajar/enviar-todos")
def reengajar_todos(request: Request, motivo: str = Form(""), quantos: int = Form(20), db: Session = Depends(get_db),
                    usuario: Usuario = Depends(pode_alertas)):
    """Manda pelo 0500 para os primeiros da lista (até "quantos"), respeitando o limite diário da API."""
    from ..web import endereco_site

    site, enviados, falhas = endereco_site(request), 0, []
    for alvo in [c for c in reengajamento.candidatos(db) if not motivo or c.motivo == motivo][:max(1, min(quantos, 200))]:
        r = reengajamento.enviar_api(db, alvo, site, usuario.nome)
        if r.ok:
            enviados += 1
        else:
            falhas.append(r.motivo)
            if "limite diário" in r.motivo or "não está configurada" in r.motivo or "falta o nome" in r.motivo:
                break
    avisar(request, f"{enviados} mensagem(ns) de retorno enviada(s) pelo 0500.")
    if falhas:
        avisar(request, f"{len(falhas)} não enviada(s): {falhas[0]}", "erro")
    return voltar("/crm/reengajar")
