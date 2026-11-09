"""Página do cliente: as promoções vigentes que combinam com ele, num endereço secreto que vai nas mensagens.

Segurança da página (ela é aberta sem login):
- o endereço tem um código aleatório de 144 bits por cliente (impossível de adivinhar); a equipe pode trocar o
  código a qualquer momento e o link antigo para de funcionar;
- mostra só o primeiro nome, os trechos que o cliente acompanha e as promoções; nada de telefone, e-mail, CPF,
  observações, fonte da promoção, links de terceiros, texto original, custos ou contas;
- só abre para cliente ativo que autorizou receber promoções; link errado, trocado ou de cliente inativo recebe a
  mesma resposta ("link indisponível"), sem dizer se o cliente existe;
- muitas tentativas de link errado vindas do mesmo endereço passam a receber "muitas tentativas" (o link certo
  continua abrindo: num endereço compartilhado, como o das operadoras de celular, quem erra não trava os outros);
- a resposta não pode ser guardada em cache, indexada por buscadores nem aberta dentro de outro site, não passa o
  endereço adiante (Referrer-Policy) e não executa nenhum script (Content-Security-Policy).
"""
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import aeroportos
from ..config import settings
from ..database import get_db
from ..limite import Limitador
from ..models import Cliente, ImagemPromocao, Match, Promocao, Usuario, novo_token
from ..servicos import (
    TIPOS_VIAGEM,
    descrever_trecho,
    formatar_periodo,
    formatar_preco,
    formatar_valor,
    grupos_da_pagina,
    nome_sobrenome,
    periodo_extenso,
    promocoes_da_pagina,
    visivel_na_pagina,
    whatsapp_agencia,
)
from ..web import avisar, chave_de_ip, endereco_site, exigir, templates, voltar

rotas = APIRouter()
pode_alertas = exigir("alertas")

TOKEN_VALIDO = re.compile(r"[A-Za-z0-9_-]{20,64}")
# quem erra o link muitas vezes seguidas (tentando adivinhar) passa a receber "muitas tentativas"
falhas_link = Limitador(maximo=30, janela=15 * 60)

CABECALHOS_PAGINA = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; img-src 'self'; style-src 'self' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
}


def _quando(momento: datetime) -> str:
    dias = (date.today() - momento.date()).days
    return "hoje" if dias <= 0 else "ontem" if dias == 1 else f"{momento:%d/%m}"


def _quero(numero: str, primeiro_nome: str, texto: str) -> str:
    """Botão do WhatsApp da agência com a mensagem pronta."""
    if not numero:
        return ""
    mensagem = f"Olá! Sou {primeiro_nome} e tenho interesse nesta promoção: {texto}" if texto else \
        f"Olá! Sou {primeiro_nome} e vi a minha página de promoções."
    return f"https://wa.me/{numero}?text={quote(mensagem)}"


def _item(p: Promocao, primeiro_nome: str, numero: str, token: str) -> dict:
    valor = formatar_valor(p)
    viagem = TIPOS_VIAGEM.get(p.tipo_viagem or "", "") if p.passagem else ""
    datas = formatar_periodo(p.data_inicio, p.data_fim, p.datas_texto)
    if p.passagem:
        cidades = f"{aeroportos.nome_curto(p.origem)} → {aeroportos.nome_curto(p.destino)}"
        titulo = ""
    else:
        cidades = p.lugar or (aeroportos.nome_curto(p.destino) if p.destino else "")
        titulo = p.titulo or f"{p.nome_tipo}{' em ' + cidades if cidades else ''}"
    partes = [titulo or cidades, valor if valor != "consulte" else "", datas, viagem.lower(), p.companhia or ""]
    return {
        "passagem": p.passagem, "tipo": p.tipo, "nome_tipo": p.nome_tipo, "titulo": titulo,
        "origem": p.origem, "destino": p.destino, "cidades": cidades, "valor": valor, "viagem": viagem,
        "datas": datas, "companhia": p.companhia or "", "resumo": p.resumo or "", "encontrada": _quando(p.coletada_em),
        "imagem": f"/para-voce/{token}/imagem/{p.id}" if p.imagem else "",
        "quero": _quero(numero, primeiro_nome, " · ".join(x for x in partes if x)),
    }


def _resposta(request: Request, contexto: dict, status: int = 200):
    resposta = templates.TemplateResponse(request, "para_voce.html", contexto, status_code=status)
    resposta.headers.update(CABECALHOS_PAGINA)
    return resposta


def _indisponivel(request: Request, status: int):
    return _resposta(request, {"indisponivel": True, "muitas_tentativas": status == 429,
                               "site": endereco_site(request)}, status)


@rotas.get("/para-voce/{token}", response_class=HTMLResponse)
def pagina_do_cliente(token: str, request: Request, db: Session = Depends(get_db)):
    cliente = db.scalar(select(Cliente).where(Cliente.token_pagina == token)) if TOKEN_VALIDO.fullmatch(token) else None
    if cliente is None:  # link errado: conta a tentativa do endereço
        return _indisponivel(request, 404 if falhas_link.tentar(chave_de_ip(request)) else 429)
    if not cliente.ativo or not cliente.consentimento:
        return _indisponivel(request, 404)

    primeiro_nome = nome_sobrenome(cliente.nome).split(" ")[0]
    numero = whatsapp_agencia(db)
    grupos = [{"nome": g["nome"], "emoji": g["emoji"], "tipo": g["tipo"],
               "itens": [_item(p, primeiro_nome, numero, token) for p in g["promocoes"]]}
              for g in grupos_da_pagina(promocoes_da_pagina(db, cliente))]
    trechos = [{"rota": f"{aeroportos.nome_curto(i.origem)} → {aeroportos.nome_curto(i.destino)}",
                "quando": periodo_extenso(i.periodo_inicio, i.periodo_fim),
                "ate": formatar_preco(i.preco_maximo) if i.preco_maximo else ""}
               for i in cliente.interesses if i.ativo]
    return _resposta(request, {
        "indisponivel": False, "primeiro_nome": primeiro_nome, "grupos": grupos, "trechos": trechos,
        "total": sum(len(g["itens"]) for g in grupos), "quero_geral": _quero(numero, primeiro_nome, ""),
        "atualizado": datetime.now(), "site": endereco_site(request),
    })


@rotas.get("/para-voce/{token}/imagem/{promo_id}")
def imagem_da_pagina(token: str, promo_id: int, request: Request, db: Session = Depends(get_db)):
    """A imagem de uma promoção da página do cliente: só com o link certo e só das promoções que ele vê."""
    cliente = db.scalar(select(Cliente).where(Cliente.token_pagina == token)) if TOKEN_VALIDO.fullmatch(token) else None
    if cliente is None or not cliente.ativo or not cliente.consentimento:
        falhas_link.tentar(chave_de_ip(request))
        raise HTTPException(404)
    if promo_id not in {p.id for p in promocoes_da_pagina(db, cliente)}:
        raise HTTPException(404)
    img = db.scalar(select(ImagemPromocao).where(ImagemPromocao.promocao_id == promo_id))
    if not img:
        raise HTTPException(404)
    cabecalhos = dict(CABECALHOS_PAGINA, **{"Cache-Control": "private, max-age=3600"})
    cabecalhos.pop("Content-Security-Policy", None)
    return Response(img.dados, media_type=img.mime, headers=cabecalhos)


# ---------------------------------------------------------------- equipe: link novo e envio do link


@rotas.post("/clientes/{cliente_id}/pagina/novo-link")
def gerar_link_novo(cliente_id: int, request: Request, db: Session = Depends(get_db),
                    usuario: Usuario = Depends(pode_alertas)):
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        raise HTTPException(404)
    cliente.token_pagina = novo_token()
    db.commit()
    avisar(request, "Link novo gerado. O link antigo não abre mais a página.")
    return voltar(f"/clientes/{cliente_id}")


@rotas.post("/clientes/{cliente_id}/pagina/enviada")
def registrar_envio_da_pagina(cliente_id: int, request: Request, db: Session = Depends(get_db),
                              usuario: Usuario = Depends(pode_alertas)):
    """O link da página foi mandado: as promoções novas que estão na página do cliente contam como enviadas."""
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        raise HTTPException(404)
    if not cliente.consentimento:
        raise HTTPException(400, "Cliente sem consentimento para receber promoções.")
    limite = datetime.now() - timedelta(days=settings.promo_validade_dias)
    pendentes = db.scalars(
        select(Match).join(Promocao)
        .where(Match.cliente_id == cliente.id, Match.status == "nova", Promocao.status == "ativa",
               Promocao.coletada_em >= limite)
        .options(selectinload(Match.promocao), selectinload(Match.interesse))
    ).all()
    agora, marcadas = datetime.now(), 0
    for m in pendentes:
        if visivel_na_pagina(m):
            m.status, m.enviado_por, m.enviado_em = "enviada", usuario.nome, agora
            m.envios = (m.envios or 0) + 1
            marcadas += 1
    db.commit()
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"ok": True, "envios": 1, "promocoes": marcadas})
    avisar(request, f"Link da página enviado para {cliente.nome}.")
    return voltar(request.headers.get("referer") or f"/clientes/{cliente_id}")


@rotas.get("/falar/{codigo}")
def falar_com_atendente(codigo: str, request: Request, db: Session = Depends(get_db)):
    """Botão "Falar com um atendente" da mensagem do 0500: avisa o CRM e abre a conversa no WhatsApp do atendimento
    (0400) com a promoção escrita. Código = código da página do cliente + "." + número da promoção."""
    from .. import whatsapp_api as api
    from ..models import ConversaAtendimento

    token, _, promo = codigo.partition(".")
    cliente = db.scalar(select(Cliente).where(Cliente.token_pagina == token)) if TOKEN_VALIDO.fullmatch(token) else None
    if cliente is None:
        return _indisponivel(request, 404 if falhas_link.tentar(chave_de_ip(request)) else 429)
    numero = api.configuracao(db).get("contato") or whatsapp_agencia(db)
    if not numero:
        return _indisponivel(request, 404)
    promocao = db.get(Promocao, int(promo)) if promo.isdigit() else None
    # um registro por cliente a cada 30 minutos (tocar de novo no botão não cria outro)
    recente = db.scalar(select(ConversaAtendimento).where(
        ConversaAtendimento.cliente_id == cliente.id, ConversaAtendimento.origem == "cliente",
        ConversaAtendimento.aberta_em >= datetime.now() - timedelta(minutes=30)))
    if recente is None:
        db.add(ConversaAtendimento(cliente_id=cliente.id, origem="cliente", aberta_por="cliente (botão da mensagem)",
                                   promocao_id=promocao.id if promocao else None))
        db.commit()
    primeiro = nome_sobrenome(cliente.nome).split(" ")[0]
    texto = (f"Olá! Sou {primeiro} e quero falar sobre a promoção {descrever_trecho(promocao)} ({formatar_valor(promocao)})."
             if promocao else f"Olá! Sou {primeiro} e quero falar com um atendente da Mundial Viagens.")
    resposta = RedirectResponse(f"https://wa.me/{numero}?text={quote(texto)}", status_code=303)
    resposta.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"})
    return resposta
