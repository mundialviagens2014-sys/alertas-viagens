"""MVO — Mundial Viagens Organização: site da equipe.

Aqui ficam os alertas (clientes, interesses, promoções e envio manual pelo WhatsApp); as páginas de
milhas (colaboradores, contas, emissões...) ficam em app/rotas/.
"""
import binascii
import hashlib
import hmac
import logging
import os
import re
import secrets
from collections import OrderedDict, defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from itsdangerous import BadData, URLSafeTimedSerializer
from pydantic import BaseModel
from sqlalchemy import and_, case, false, func, or_, select
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware

from . import aeroportos, busca, coleta, formulario, importacao, leitura
from . import colunas as col_
from .exportar import Coluna, exportar, quer_planilha, sim_nao
from .limite import Limitador
from .config import BASE_DIR, settings
from .database import SessionLocal, get_db, init_db
from .models import (
    TIPOS_PROMOCAO,
    CanalTelegram,
    Cliente,
    ConfirmacaoCadastro,
    ImagemPromocao,
    Interesse,
    Match,
    Promocao,
    Usuario,
)
from .models_mvo import Emissao
from .mvo.contas import quem_pode_emitir, recalcular_vencidos, registrar
from .rotas import cadastros as rotas_cadastros
from .rotas import configuracao as rotas_configuracao
from .rotas import emissoes as rotas_emissoes
from .rotas import equipe as rotas_equipe
from .rotas import importar_manager as rotas_importar
from .rotas import manager as rotas_manager
from .rotas import milhas as rotas_milhas
from .rotas import para_voce as rotas_para_voce
from .rotas import pedidos as rotas_pedidos
from .rotas import whatsapp as rotas_whatsapp
from .rotas import whatsapp_api as rotas_whatsapp_api
from .rotas import crm as rotas_crm
from .rotas import relatorios as rotas_relatorios
from .parser import PromocaoExtraida, extrair
from .permissoes import pode
from .web import (
    APP_DIR,
    FONTES,
    NaoLogado,
    SemPermissao,
    avisar,
    chave_de_ip,
    checar_exportar,
    endereco_site,
    exigir,
    exigir_uma,
    ip_de,
    ler_data,
    ler_numero,
    render,
    usuario_atual,
    voltar,
)
from .servicos import (
    ler_estado,
    ajustar_nome,
    conferir_senha,
    formatar_periodo,
    formatar_preco,
    formatar_valor,
    preco_reais,
    gerar_hash_senha,
    gerar_matches_para_interesse,
    gerar_matches_para_promocao,
    interesse_compativel,
    link_whatsapp,
    montar_mensagem,
    normalizar_telefone,
    preparar_banco,
    promocoes_vigentes,
    registrar_promocao,
    template_padrao,
    TEMPLATE_CONFIRMACAO,
    TEMPLATE_PAGINA,
    CHAVE_LINK_NAS_MENSAGENS,
    CHAVE_WHATSAPP_AGENCIA,
    gravar_estado,
    grupos_da_pagina,
    link_nas_mensagens,
    link_pagina,
    montar_confirmacao,
    montar_mensagem_pagina,
    promocoes_da_pagina,
    template_confirmacao,
    template_pergunta,
    TEMPLATE_PERGUNTA,
    template_pagina,
    trechos_da_confirmacao,
    visivel_na_pagina,
    whatsapp_agencia,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


class _EsconderLinkDoCliente(logging.Filter):
    """O registro de pedidos do servidor (logs do Render) não guarda o código da página do cliente."""

    def filter(self, registro: logging.LogRecord) -> bool:
        if isinstance(registro.args, tuple) and any(isinstance(a, str) and ("/para-voce/" in a or "/falar/" in a)
                                                    for a in registro.args):
            registro.args = tuple(re.sub(r"/(para-voce|falar)/[^/?#\s]+", r"/\1/…", a) if isinstance(a, str) else a
                                  for a in registro.args)
        return True


logging.getLogger("uvicorn.access").addFilter(_EsconderLinkDoCliente())


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    init_db()
    with SessionLocal() as db:
        preparar_banco(db)
        recalcular_vencidos(db)  # pontos com data futura que já chegaram
    agendador = coleta.iniciar_agendador() if settings.coleta_automatica else None
    from .collectors import whatsapp as leitor_whatsapp

    leitor_whatsapp.iniciar_se_houver_sessao()  # religa o número leitor do WhatsApp, se já foi pareado
    yield
    leitor_whatsapp.parar()
    if agendador:
        agendador.shutdown(wait=False)


app = FastAPI(title="MVO — Mundial Viagens Organização", lifespan=ciclo_de_vida, docs_url=None, redoc_url=None)


class AceitaHead:
    """Responde a pedidos HEAD como GET (sem corpo). Monitores como o UptimeRobot usam HEAD
    e recebiam 405, marcando o site como fora do ar."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "HEAD":
            scope = dict(scope, method="GET")

            async def sem_corpo(mensagem):
                if mensagem["type"] == "http.response.body":
                    mensagem = dict(mensagem, body=b"")
                await send(mensagem)

            return await self.app(scope, receive, sem_corpo)
        return await self.app(scope, receive, send)


class CabecalhosSeguranca:
    """Cabeçalhos de segurança em todas as respostas: o navegador não "adivinha" o tipo dos arquivos, o site não abre
    dentro de outros sites, o endereço das páginas internas não vai para sites de fora e, no https, o navegador passa
    a usar sempre https. A página do cliente (/para-voce) define os dela, mais rígidos, que têm preferência."""

    PADRAO = ((b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"SAMEORIGIN"),
              (b"referrer-policy", b"same-origin"))

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        https = scope.get("scheme") == "https"
        # arquivos de /static: o navegador guarda e não pergunta de novo ao servidor a cada página.
        # Com ?v=<versão> (CSS e JS) ficam guardados até a próxima versão; fontes e imagens, por uma semana.
        cache = None
        if scope.get("path", "").startswith("/static/"):
            cache = (b"public, max-age=31536000, immutable" if b"v=" in scope.get("query_string", b"")
                     else b"public, max-age=604800")

        async def com_cabecalhos(mensagem):
            if mensagem["type"] == "http.response.start":
                cabecalhos = list(mensagem.get("headers", []))
                existentes = {nome.lower() for nome, _ in cabecalhos}
                if cache and mensagem.get("status") in (200, 304) and b"cache-control" not in existentes:
                    cabecalhos.append((b"cache-control", cache))
                cabecalhos += [(nome, valor) for nome, valor in self.PADRAO if nome not in existentes]
                if https and b"strict-transport-security" not in existentes:
                    cabecalhos.append((b"strict-transport-security", b"max-age=31536000"))
                mensagem = dict(mensagem, headers=cabecalhos)
            await send(mensagem)

        return await self.app(scope, receive, com_cabecalhos)


app.add_middleware(
    SessionMiddleware, secret_key=settings.secret_key, same_site="lax",
    https_only=settings.cookie_seguro, max_age=60 * 60 * 12,
)
app.add_middleware(AceitaHead)
app.add_middleware(CabecalhosSeguranca)
# páginas e arquivos de texto vão compactados (listas grandes chegam bem mais rápido no celular)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.include_router(rotas_equipe.rotas)
app.include_router(rotas_cadastros.rotas)  # antes de milhas: /contas/nova antes de /contas/{conta_id}
app.include_router(rotas_milhas.rotas)
app.include_router(rotas_emissoes.rotas)
app.include_router(rotas_importar.rotas)
app.include_router(rotas_manager.rotas)
app.include_router(rotas_configuracao.rotas)
app.include_router(rotas_pedidos.rotas)
app.include_router(rotas_para_voce.rotas)
app.include_router(rotas_whatsapp.rotas)
app.include_router(rotas_whatsapp_api.rotas)
app.include_router(rotas_crm.rotas)
app.include_router(rotas_relatorios.rotas)



@app.exception_handler(NaoLogado)
async def _redirecionar_login(request: Request, exc: NaoLogado):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(SemPermissao)
async def _sem_permissao(request: Request, exc: SemPermissao):
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"erro": "Você não tem permissão para isso."}, status_code=403)
    with SessionLocal() as db:
        usuario = db.get(Usuario, request.session.get("usuario_id"))
        resposta = render(request, "sem_permissao.html", usuario, faltando=exc.permissoes)
    resposta.status_code = 403
    return resposta


_data = ler_data
_numero = ler_numero

# quem pode usar cada parte do site (ver app/permissoes.py)
pode_alertas = exigir("alertas")
pode_configurar = exigir("configurar")
pode_ver_precos = exigir_uma("milhas_ver", "configurar")


# ---------------------------------------------------------------- login


@app.get("/login", response_class=HTMLResponse)
def tela_login(request: Request):
    return render(request, "login.html")


# O endereço do site agora vai para os clientes (página do cliente), então o login se protege de quem tenta
# adivinhar senhas. Cada tentativa conta antes de a senha ser conferida (pedidos simultâneos não escapam):
# - aparelho que já entrou com aquele login (cookie assinado): 10 tentativas a cada 15 minutos por aparelho; quem
#   ataca de fora não consegue travar a equipe nos aparelhos dela;
# - aparelho novo: 10 tentativas a cada 15 minutos por login, somando todos os aparelhos novos (atacar de vários
#   endereços não dá mais tentativas), e 20 por endereço (IP).
tentativas_aparelho = Limitador(maximo=10, janela=15 * 60)
tentativas_login = Limitador(maximo=10, janela=15 * 60)
tentativas_ip = Limitador(maximo=20, janela=15 * 60)
COOKIE_APARELHO = "mvo_aparelho_"  # + um código por login: o mesmo navegador guarda um para cada login
VALIDADE_APARELHO = 180 * 24 * 3600  # o cookie vence em 180 dias (cada login renova)
_assinatura_aparelho = URLSafeTimedSerializer(settings.secret_key, salt="aparelho-conhecido")
SENHAS_FRACAS = {"admin123", "12345678", "123456789", "senha123", "mundial123", "password"}


def _nome_cookie_aparelho(chave_login: str) -> str:
    return COOKIE_APARELHO + hashlib.sha256(chave_login.encode()).hexdigest()[:12]


def _aparelho_conhecido(request: Request, chave_login: str) -> str | None:
    """Código do aparelho, se ele já entrou no site com este login (cookie assinado e dentro da validade)."""
    try:
        dados = _assinatura_aparelho.loads(request.cookies.get(_nome_cookie_aparelho(chave_login)) or "",
                                           max_age=VALIDADE_APARELHO)
    except BadData:
        return None
    if isinstance(dados, dict) and dados.get("l") == chave_login and isinstance(dados.get("a"), str):
        return dados["a"]
    return None


def senha_fraca(senha: str, login: str = "") -> bool:
    """Senha padrão, comum ou curta: o aviso para trocar aparece em todas as páginas."""
    return len(senha) < 8 or senha.lower() in SENHAS_FRACAS or (bool(login) and senha.lower() == login.strip().lower())


@app.post("/login")
def fazer_login(request: Request, login: str = Form(...), senha: str = Form(...), db: Session = Depends(get_db)):
    chave_login = login.strip().lower()[:60]
    aparelho = _aparelho_conhecido(request, chave_login)
    if aparelho:
        liberado = tentativas_aparelho.tentar(aparelho)
    else:
        liberado = tentativas_ip.tentar(chave_de_ip(request)) and tentativas_login.tentar(chave_login)
    if not liberado:
        avisar(request, "Muitas tentativas erradas. Aguarde 15 minutos e tente de novo.", "erro")
        return voltar("/login")
    usuario = db.scalar(select(Usuario).where(Usuario.login == login.strip()))
    if not usuario or not usuario.ativo or not conferir_senha(senha, usuario.senha_hash):
        registrar(db, usuario, "login_recusado", None, f"login digitado: {login.strip()[:60]}", ip_de(request))
        db.commit()
        avisar(request, "Login ou senha incorretos.", "erro")
        return voltar("/login")
    # entrou: as tentativas deste aparelho (ou deste login, quando o aparelho é novo) voltam a zero
    if aparelho:
        tentativas_aparelho.limpar(aparelho)
    else:
        tentativas_login.limpar(chave_login)
        tentativas_ip.limpar(chave_de_ip(request))
    request.session.clear()
    request.session["usuario_id"] = usuario.id
    if senha_fraca(senha, login):
        request.session["senha_fraca"] = True
    usuario.ultimo_acesso = datetime.now()
    registrar(db, usuario, "login", None, None, ip_de(request))
    db.commit()
    resposta = voltar("/" if pode(usuario, "alertas") else "/disponibilidade")
    resposta.set_cookie(_nome_cookie_aparelho(chave_login),
                        _assinatura_aparelho.dumps({"l": chave_login, "a": aparelho or secrets.token_urlsafe(12)}),
                        max_age=VALIDADE_APARELHO, httponly=True, samesite="lax", secure=settings.cookie_seguro)
    return resposta


@app.get("/sair")
def sair(request: Request):
    request.session.clear()
    return voltar("/login")


# ---------------------------------------------------------------- fila de envio (página inicial)


ORDENS_FILA = {"recentes": "Mais recentes", "preco": "Menor preço", "clientes": "Mais clientes"}


@app.get("/", response_class=HTMLResponse)
def fila(request: Request, revisar: int = 0, ver: str = "", q: str = "", origem: str = "", destino: str = "",
         tipo: str = "", fonte: str = "", ordem: str = "", db: Session = Depends(get_db),
         usuario: Usuario = Depends(pode_alertas)):
    ver = ver or ("revisar" if revisar else "pendentes")
    if ver not in ("pendentes", "revisar", "enviadas", "confirmacoes", "clientes"):
        ver = "pendentes"
    tipo = tipo if tipo in ("reais", "milhas") else ""
    fonte = fonte if fonte in FONTES else ""
    ordem = ordem if ordem in ORDENS_FILA else "recentes"
    limite = datetime.now() - timedelta(days=settings.promo_validade_dias)

    def consulta(status: str):
        return db.scalars(
            select(Match).join(Promocao).join(Cliente)
            .where(Match.status == status, Promocao.status == "ativa", Promocao.coletada_em >= limite,
                   Cliente.ativo.is_(True))
            .options(selectinload(Match.promocao).selectinload(Promocao.imagem),
                     selectinload(Match.cliente).selectinload(Cliente.interesses), selectinload(Match.interesse))
            .order_by(Promocao.coletada_em.desc(), Match.id)
        ).all()

    matches = consulta("nova")
    enviadas = consulta("enviada")
    # confirmações de quem se cadastrou pelo formulário: ficam no topo de "Para enviar"
    pendentes_conf = db.scalars(
        select(ConfirmacaoCadastro).join(Cliente)
        .where(ConfirmacaoCadastro.status == "nova", Cliente.ativo.is_(True))
        .options(selectinload(ConfirmacaoCadastro.cliente).selectinload(Cliente.interesses))
        .order_by(ConfirmacaoCadastro.criado_em, ConfirmacaoCadastro.id)
    ).all()
    de, para, avisos = _lugares_digitados(origem, destino)
    site = endereco_site(request)
    site_msg = site if link_nas_mensagens(db) else ""  # link da página do cliente no fim das mensagens
    confirmacoes = []
    # reais/milhas e fonte são filtros de promoção: com eles, as confirmações de cadastro saem de "Para enviar"
    if (ver == "confirmacoes" or (ver == "pendentes" and not (tipo or fonte))) and pendentes_conf:
        modelo_conf = template_confirmacao(db).texto
        modelo_pergunta = template_pergunta(db).texto
        confirmacoes = [item for item in (_item_confirmacao(modelo_conf, c, site_msg, modelo_pergunta) for c in pendentes_conf)
                        if _confirmacao_casa(item, q, de, para)]
    if ver == "enviadas":
        exibir = sorted(enviadas, key=lambda m: m.enviado_em or datetime.min, reverse=True)
    elif ver == "revisar":
        exibir = [m for m in matches if m.promocao.precisa_revisao]
    elif ver in ("confirmacoes", "clientes"):
        exibir = []
    else:
        exibir = matches
    exibir = [m for m in exibir if _envio_casa(m, q, de, para, tipo, fonte)]

    # "Por cliente": uma mensagem por cliente com o link da página dele, para quem tem promoção nova na página
    novas_por_cliente: dict[int, list[Match]] = defaultdict(list)
    for m in matches:
        if visivel_na_pagina(m):
            novas_por_cliente[m.cliente_id].append(m)
    clientes_link = []
    if ver == "clientes":
        modelo_pagina = template_pagina(db).texto
        na_pagina: dict[int, list[Promocao]] = defaultdict(list)  # o que a página de cada cliente mostra
        for m in sorted(matches + enviadas, key=lambda m: (m.promocao.coletada_em, m.promocao_id), reverse=True):
            if visivel_na_pagina(m):
                na_pagina[m.cliente_id].append(m.promocao)
        for cliente_id, novas in novas_por_cliente.items():
            if any(_envio_casa(m, q, de, para, "", "") for m in novas):
                clientes_link.append(_item_pagina(modelo_pagina, novas[0].cliente, na_pagina[cliente_id], site, novas))
        clientes_link.sort(key=lambda i: (-len(i["novas"]), i["cliente"].nome.lower()))

    modelo = template_padrao(db).texto
    grupos: OrderedDict[int, dict] = OrderedDict()
    for m in exibir:
        g = grupos.setdefault(m.promocao_id, {"promo": m.promocao, "itens": []})
        g["itens"].append(_item_envio(modelo, m, site_msg))
    lista = list(grupos.values())
    if ordem == "preco":  # promoção sem valor em R$ vai para o fim
        lista.sort(key=lambda g: (preco_reais(g["promo"]) is None, preco_reais(g["promo"]) or 0))
    elif ordem == "clientes":
        lista.sort(key=lambda g: -len(g["itens"]))

    hoje = datetime.combine(date.today(), datetime.min.time())
    resumo = {
        "pendentes": len(matches) + len(pendentes_conf),
        "promocoes": len({m.promocao_id for m in matches}),
        "revisar": len({m.promocao_id for m in matches if m.promocao.precisa_revisao}),
        "enviadas_hoje": db.scalar(select(func.count(Match.id)).where(Match.status == "enviada", Match.enviado_em >= hoje)),
        "clientes": db.scalar(select(func.count(Cliente.id)).where(Cliente.ativo.is_(True))),
    }
    resumo["enviadas_vigentes"] = len(enviadas)
    resumo["confirmacoes"] = len(pendentes_conf)
    resumo["por_cliente"] = len(novas_por_cliente)
    # promoções em milhas: quais contas têm pontos e CPF livre para emitir (a de menor custo primeiro)
    emitir = quem_pode_emitir(db, [g["promo"] for g in lista]) if pode(usuario, "milhas_ver") else {}
    for aviso in avisos:
        avisar(request, aviso, "erro")
    filtrando = bool(q.strip() or origem.strip() or destino.strip() or tipo or fonte or ordem != "recentes")
    return render(request, "fila.html", usuario, grupos=lista, resumo=resumo, ver=ver,
                  coletas=coleta.ultima_execucao, tem_telegram=bool(settings.telegram_api_id),
                  tem_google=bool(settings.serpapi_key), tem_site=bool(settings.site_firebase_projeto), emitir=emitir, confirmacoes=confirmacoes,
                  q=q, origem=origem, destino=destino, tipo=tipo, fonte=fonte, ordem=ordem, ordens=ORDENS_FILA,
                  filtrando=filtrando, envios_filtrados=sum(len(g["itens"]) for g in lista), clientes_link=clientes_link)


def _envio_casa(m: Match, q: str, de: set | None, para: set | None, tipo: str, fonte: str) -> bool:
    """O envio da fila passa pelos filtros? (busca pelo cliente ou pela promoção, trecho, reais/milhas, fonte)"""
    p, c = m.promocao, m.cliente
    if de is not None and (p.origem or "").upper() not in de:
        return False
    if para is not None and (p.destino or "").upper() not in para:
        return False
    if (tipo == "reais" and p.preco is None) or (tipo == "milhas" and p.milhas is None):
        return False
    if fonte and p.fonte != fonte:
        return False
    return busca.casa(q, [c.nome, c.email, p.origem, p.destino, p.companhia, p.programa, p.fonte_detalhe, p.titulo,
                          p.lugar, lambda termo: _no_lugar(termo, p.origem, p.destino)], [c.telefone])


def _confirmacao_casa(item: dict, q: str, de: set | None, para: set | None) -> bool:
    """A confirmação de cadastro passa pelos filtros? (busca pelo cliente ou cidade; trecho escolhido no formulário)"""
    trechos, c = item["trechos"], item["cliente"]
    if (de is not None or para is not None) and not any(
            (de is None or (i.origem or "").upper() in de) and (para is None or (i.destino or "").upper() in para)
            for i in trechos):
        return False
    lugares = [x for i in trechos for x in (i.origem, i.destino)]
    return busca.casa(q, [c.nome, c.email, lambda termo: _no_lugar(termo, *lugares)], [c.telefone])


def _item_envio(modelo: str, m: Match, site: str = "") -> dict:
    """Mensagem de uma promoção (com o link da página do cliente no fim, quando `site` vem preenchido)."""
    msg = montar_mensagem(modelo, m.cliente, m.promocao, link_pagina(site, m.cliente))
    return {"match": m, "mensagem": msg, "link": link_whatsapp(m.cliente.telefone, msg)}


def _item_confirmacao(modelo: str, conf: ConfirmacaoCadastro, site: str = "", modelo_pergunta: str = "") -> dict:
    """Confirmação do cadastro; sem nenhum trecho reconhecido, a mensagem pergunta para onde o cliente quer viajar."""
    trechos = trechos_da_confirmacao(conf)
    pergunta = not trechos and bool(modelo_pergunta)
    msg = montar_confirmacao(modelo_pergunta if pergunta else modelo, conf.cliente, trechos,
                             "" if pergunta else link_pagina(site, conf.cliente))
    return {"conf": conf, "cliente": conf.cliente, "trechos": trechos, "mensagem": msg, "pergunta": pergunta,
            "link": link_whatsapp(conf.cliente.telefone, msg)}


def _item_pagina(modelo: str, cliente: Cliente, promos: list[Promocao], site: str, novas: list | None = None) -> dict:
    """Mensagem com o link da página do cliente e o resumo das promoções que estão nela."""
    url = link_pagina(site, cliente)
    msg = montar_mensagem_pagina(modelo, cliente, promos, url)
    trechos = [{"rota": g["nome"], "qtd": len(g["promocoes"]),
                "valor": formatar_valor(g["promocoes"][0])} for g in grupos_da_pagina(promos)]
    return {"cliente": cliente, "novas": novas or [], "promocoes": promos, "trechos": trechos, "url": url,
            "mensagem": msg, "link": link_whatsapp(cliente.telefone, msg),
            "registrar": f"/clientes/{cliente.id}/pagina/enviada"}


def _registrar_confirmacao(request: Request, db: Session, usuario: Usuario, conf: ConfirmacaoCadastro):
    if not conf.cliente.consentimento:
        raise HTTPException(400, "Cliente sem consentimento para receber mensagens.")
    conf.status, conf.enviado_por, conf.enviado_em = "enviada", usuario.nome, datetime.now()
    conf.envios = (conf.envios or 0) + 1
    db.commit()
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"ok": True, "envios": conf.envios})
    avisar(request, f"Confirmação para {conf.cliente.nome} registrada.")
    return voltar(request.headers.get("referer") or "/")


@app.post("/confirmacoes/{conf_id}/enviar")
def enviar_confirmacao(conf_id: int, request: Request, db: Session = Depends(get_db),
                       usuario: Usuario = Depends(pode_alertas)):
    conf = db.get(ConfirmacaoCadastro, conf_id)
    if not conf:
        raise HTTPException(404)
    return _registrar_confirmacao(request, db, usuario, conf)


@app.post("/confirmacoes/{conf_id}/descartar")
def descartar_confirmacao(conf_id: int, request: Request, db: Session = Depends(get_db),
                          usuario: Usuario = Depends(pode_alertas)):
    conf = db.get(ConfirmacaoCadastro, conf_id)
    if not conf:
        raise HTTPException(404)
    conf.status = "descartada"
    db.commit()
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"ok": True})
    return voltar(request.headers.get("referer") or "/")


@app.post("/clientes/{cliente_id}/confirmacao")
def confirmar_cliente(cliente_id: int, request: Request, db: Session = Depends(get_db),
                      usuario: Usuario = Depends(pode_alertas)):
    """Confirmação pela página do cliente (também para quem foi cadastrado pela equipe ou por planilha)."""
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        raise HTTPException(404)
    conf = _confirmacao_do_cliente(cliente)
    if conf is None:
        conf = ConfirmacaoCadastro(cliente=cliente, trechos=None)
        db.add(conf)
    return _registrar_confirmacao(request, db, usuario, conf)


def _confirmacao_do_cliente(cliente: Cliente) -> ConfirmacaoCadastro | None:
    """A que espera envio; se não houver, a última."""
    pendente = next((c for c in cliente.confirmacoes if c.status == "nova"), None)
    return pendente or (cliente.confirmacoes[-1] if cliente.confirmacoes else None)


@app.post("/matches/{match_id}/enviar")
def marcar_enviada(match_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    m = db.get(Match, match_id)
    if not m:
        raise HTTPException(404)
    if not m.cliente.consentimento:
        raise HTTPException(400, "Cliente sem consentimento para receber promoções.")
    # envios feitos antes desta versão não tinham contador: contam como 1
    anteriores = m.envios or (1 if m.enviado_em else 0)
    m.status, m.enviado_por, m.enviado_em = "enviada", usuario.nome, datetime.now()
    m.envios = anteriores + 1
    db.commit()
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"ok": True, "envios": m.envios})
    avisar(request, f"Envio para {m.cliente.nome} registrado.")
    return voltar(request.headers.get("referer") or "/")


@app.post("/matches/{match_id}/descartar")
def descartar_match(match_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    m = db.get(Match, match_id)
    if not m:
        raise HTTPException(404)
    m.status = "descartada"
    db.commit()
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"ok": True})
    return voltar(request.headers.get("referer") or "/")


# ---------------------------------------------------------------- grupos do Telegram


def _normalizar_canal(texto: str) -> str | None:
    """Aceita @usuario, t.me/usuario, link de convite não, id numérico (-100...)."""
    t = texto.strip()
    m = re.match(r"^(?:https?://)?(?:t\.me|telegram\.me)/(?!\+|joinchat)([A-Za-z0-9_]{4,})/?$", t)
    if m:
        return "@" + m.group(1)
    if re.fullmatch(r"@[A-Za-z0-9_]{4,}", t):
        return t
    if re.fullmatch(r"-?\d{5,}", t):
        return t
    return None


SITUACOES_CANAL = {"lendo": "Lendo", "erro": "Com erro", "pausados": "Pausados",
                   "aguardando": "Aguardando a 1ª leitura", "parados": "Sem promoções em 7 dias"}
ORDENS_CANAL = {"nome": "Ordem alfabética", "promocoes": "Mais promoções (7 dias)", "leitura": "Leitura mais recente"}


def _situacao_canal(c: CanalTelegram) -> str:
    if not c.ativo:
        return "pausados"
    if c.ultimo_erro:
        return "erro"
    return "lendo" if c.ultima_leitura else "aguardando"


@app.get("/telegram", response_class=HTMLResponse)
def tela_telegram(request: Request, buscar: int = 0, q: str = "", situacao: str = "", ordem: str = "nome",
                  db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    todos = db.scalars(select(CanalTelegram).order_by(CanalTelegram.ativo.desc(), CanalTelegram.nome)).all()
    limite = datetime.now() - timedelta(days=7)
    contagem = dict(db.execute(
        select(Promocao.fonte_detalhe, func.count(Promocao.id))
        .where(Promocao.fonte == "telegram", Promocao.coletada_em >= limite).group_by(Promocao.fonte_detalhe)
    ).all())
    promos_7d = {c.id: contagem.get(c.nome, 0) + (contagem.get(c.identificador, 0) if c.nome != c.identificador else 0)
                 for c in todos}

    situacao = situacao if situacao in SITUACOES_CANAL else ""
    ordem = ordem if ordem in ORDENS_CANAL else "nome"

    def passa(c: CanalTelegram) -> bool:
        if situacao == "parados":
            ok = c.ativo and not promos_7d.get(c.id)
        else:
            ok = not situacao or _situacao_canal(c) == situacao
        return ok and busca.casa(q, [c.nome, c.identificador])

    canais = [c for c in todos if passa(c)]
    if ordem == "promocoes":
        canais.sort(key=lambda c: -promos_7d.get(c.id, 0))
    elif ordem == "leitura":
        canais.sort(key=lambda c: c.ultima_leitura or datetime.min, reverse=True)

    grupos_conta, erro_conta = None, None
    if buscar:
        try:
            cadastrados = {c.identificador for c in todos}
            grupos_conta = [g | {"cadastrado": g["identificador"] in cadastrados} for g in coleta.listar_grupos_telegram()
                            if busca.casa(q, [g["nome"], g["identificador"]])]
        except Exception as erro:
            erro_conta = str(erro)
    from . import ia

    return render(request, "telegram.html", usuario, canais=canais, total_canais=len(todos), promos_7d=promos_7d,
                  ia_ligada=ia.configurada(), leituras_hoje=ia.leituras_hoje(db), max_leituras=settings.gemini_max_por_dia,
                  leituras_pendentes=leitura.pendentes(db),
                  grupos_conta=grupos_conta, erro_conta=erro_conta, buscar=buscar, q=q, situacao=situacao,
                  situacoes=SITUACOES_CANAL, ordem=ordem, ordens=ORDENS_CANAL,
                  filtrando=bool(q.strip() or situacao or ordem != "nome"),
                  configurado=bool(settings.telegram_api_id and settings.telegram_api_hash),
                  intervalo=settings.intervalo_telegram_min if settings.coleta_automatica else None)


@app.post("/telegram/adicionar")
def adicionar_canal(request: Request, identificador: str = Form(...), nome: str = Form(""),
                    db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    ident = _normalizar_canal(identificador)
    if not ident:
        avisar(request, "Informe o @ do canal, o link t.me/... ou o número do grupo (ex.: -1002079295206). "
                        "Para grupos privados, use “Ver meus grupos do Telegram”.", "erro")
        return voltar("/telegram")
    existente = db.scalar(select(CanalTelegram).where(CanalTelegram.identificador == ident))
    if existente:
        existente.ativo = True
        avisar(request, f"{existente.nome or ident} já estava cadastrado e foi reativado.")
    else:
        db.add(CanalTelegram(identificador=ident, nome=nome.strip() or None))
        avisar(request, f"Grupo {nome.strip() or ident} adicionado. Ele entra na próxima leitura; "
                        f"use “Ler agora” para buscar as últimas mensagens.")
    db.commit()
    return voltar("/telegram")


@app.post("/telegram/{canal_id}/alternar")
def alternar_canal(canal_id: int, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    canal = db.get(CanalTelegram, canal_id)
    if canal:
        canal.ativo = not canal.ativo
        db.commit()
    return voltar("/telegram")


@app.post("/telegram/{canal_id}/excluir")
def excluir_canal(canal_id: int, request: Request, db: Session = Depends(get_db),
                  usuario: Usuario = Depends(pode_configurar)):
    canal = db.get(CanalTelegram, canal_id)
    if canal:
        db.delete(canal)
        db.commit()
        avisar(request, f"{canal.nome or canal.identificador} removido. As promoções já lidas continuam no sistema.")
    return voltar("/telegram")


@app.post("/telegram/{canal_id}/ler")
def ler_canal(canal_id: int, request: Request, usuario: Usuario = Depends(pode_alertas)):
    r = coleta.executar("telegram", reler=True, so_canal=canal_id)
    if r.get("erro") or r.get("erros"):
        avisar(request, f"Não consegui ler o grupo: {r.get('erro') or r['erros'][0]}", "erro")
    else:
        avisar(request, f"Leitura concluída: {r['lidas']} mensagens, {r['criadas']} promoções novas, "
                        f"{r['matches']} clientes compatíveis.")
    return voltar("/telegram")


@app.post("/coletar/{fonte}")
def coletar_agora(fonte: str, request: Request, usuario: Usuario = Depends(pode_alertas)):
    if fonte not in ("telegram", "google_flights", "site"):
        raise HTTPException(404)
    r = coleta.executar(fonte)
    if "erro" in r:
        avisar(request, f"Coleta não concluída: {r['erro']}", "erro")
    elif fonte == "site":
        avisar(request, f"Site da agência: {r.get('no_site', 0)} viagens no site, {r.get('criadas', 0)} novas no MVO "
                        f"({r.get('matches', 0)} clientes interessados), {r.get('atualizadas', 0)} atualizadas, "
                        f"{r.get('retiradas', 0)} retiradas.")
    elif fonte == "google_flights" and r.get("sem_cota"):
        o = r.get("orcamento") or {}
        avisar(request, f"Google Flights: a cota de buscas de hoje já foi usada ({o.get('usadas_hoje', 0)} de "
                        f"{o.get('cota_hoje', 0)}; {o.get('restantes_mes', 0)} restantes no mês). Ajuste em Configurações.", "erro")
    elif fonte == "google_flights":
        o = r.get("orcamento") or {}
        avisar(request, f"Google Flights: {r.get('buscas', 0)} buscas ({r.get('exploradas', 0)} explorar, "
                        f"{r.get('confirmacoes', 0) + r.get('diretas', 0)} conferências), {r.get('criadas', 0)} promoções novas, "
                        f"{r.get('matches', 0)} clientes compatíveis · {o.get('restantes_mes', 0)} buscas restantes no mês.")
    else:
        avisar(request, f"Coleta concluída: {r.get('criadas', 0)} promoções novas, {r.get('matches', 0)} clientes compatíveis.")
    return voltar("/")


# ---------------------------------------------------------------- clientes


def _descrever_interesse(i: Interesse) -> str:
    texto = f"{i.origem} → {i.destino or 'qualquer destino'}"
    if i.periodo_inicio:
        texto += f" · {formatar_periodo(i.periodo_inicio, i.periodo_fim)}"
    if i.preco_maximo:
        texto += f" · até {formatar_preco(i.preco_maximo)}"
    return texto + ("" if i.ativo else " (pausado)")


SITUACOES_CLIENTE = {
    "prontos": "Ativos e com autorização (padrão)", "todos": "Todas as situações",
    "ativos": "Ativos", "inativos": "Inativos", "sem_consentimento": "Sem autorização de WhatsApp",
    "com_fila": "Com promoção na fila", "sem_trecho": "Sem trecho de interesse",
}


def _lugares_digitados(origem: str, destino: str) -> tuple[set | None, set | None, list[str]]:
    """Códigos da cidade digitada em "Saindo de" e em "Indo para" (None = sem filtro; "São Paulo" ou "GRU" valem
    por CGH, GRU, SAO e VCP) e os avisos do que não foi reconhecido."""
    lugares, avisos = [], []
    for texto, nome in ((origem, "Origem"), (destino, "Destino")):
        codigos = None
        if texto.strip():
            codigo = aeroportos.resolver(texto)
            if codigo:
                codigos = set(aeroportos.codigos_do_lugar(codigo))
            else:
                avisos.append(f"{nome} “{texto.strip()}” não reconhecida: o filtro foi ignorado.")
        lugares.append(codigos)
    return lugares[0], lugares[1], avisos


@lru_cache(maxsize=1024)
def _lugar_buscado(termo: str) -> frozenset[str]:
    """Códigos da cidade que a palavra (ou a busca inteira) nomeia: "lisboa" -> LIS; "sao paulo" -> CGH, GRU..."""
    codigo = aeroportos.resolver(termo) if len(termo) >= 3 else None
    return frozenset(aeroportos.codigos_do_lugar(codigo)) if codigo else frozenset()


def _no_lugar(termo: str, *codigos: str | None) -> bool:
    """A palavra da busca é a cidade de algum destes aeroportos?"""
    lugar = _lugar_buscado(termo)
    return bool(lugar) and any(c and c.upper() in lugar for c in codigos)


def _filtro_trecho(origem: str, destino: str):
    """Condição de interesse por origem/destino digitados (código ou cidade) e os avisos do que não foi reconhecido."""
    de, para, avisos = _lugares_digitados(origem, destino)
    condicoes = [coluna.in_(sorted(codigos)) for coluna, codigos in ((Interesse.origem, de), (Interesse.destino, para))
                 if codigos is not None]
    return (and_(*condicoes) if condicoes else None), avisos


@app.get("/clientes", response_class=HTMLResponse)
def lista_clientes(request: Request, q: str = "", situacao: str = "", origem: str = "", destino: str = "",
                   cadastro: str = "", ordem: str = "nome", formato: str = "", db: Session = Depends(get_db),
                   usuario: Usuario = Depends(pode_alertas)):
    consulta = select(Cliente).options(selectinload(Cliente.interesses))
    consulta = busca.aplicar(consulta, q, [Cliente.nome, Cliente.email, Cliente.observacoes, Cliente.classificacao],
                             [Cliente.telefone, Cliente.documento])
    # por padrão, só quem pode receber mensagem (ativo e com autorização): os importados do Manager inativos ou sem
    # autorização ficam em "Todas as situações" e nos filtros próprios. Buscando um nome ou trecho, procura em todos
    # (os ativos e autorizados continuam vindo primeiro).
    situacao = situacao or ("todos" if (q.strip() or origem.strip() or destino.strip()) else "prontos")
    if situacao == "prontos":
        consulta = consulta.where(Cliente.ativo.is_(True), Cliente.consentimento.is_(True))
    elif situacao == "ativos":
        consulta = consulta.where(Cliente.ativo.is_(True))
    elif situacao == "inativos":
        consulta = consulta.where(Cliente.ativo.is_(False))
    elif situacao == "sem_consentimento":
        consulta = consulta.where(Cliente.consentimento.is_not(True))
    elif situacao == "com_fila":
        consulta = consulta.where(Cliente.matches.any(Match.status == "nova"))
    elif situacao == "sem_trecho":
        consulta = consulta.where(~Cliente.interesses.any(Interesse.ativo.is_(True)))
    trecho, avisos = _filtro_trecho(origem, destino)
    if trecho is not None:
        consulta = consulta.where(Cliente.interesses.any(trecho))
    if busca.desde(cadastro):
        consulta = consulta.where(Cliente.criado_em >= busca.desde(cadastro))
    # em "Todas as situações", quem está ativo e autorizado vem primeiro
    prontos_primeiro = case((and_(Cliente.ativo.is_(True), Cliente.consentimento.is_(True)), 0), else_=1)
    consulta = consulta.order_by(prontos_primeiro, Cliente.criado_em.desc() if ordem == "recentes" else Cliente.nome)
    clientes = db.scalars(consulta).all()
    pendentes = dict(db.execute(
        select(Match.cliente_id, func.count(Match.id)).where(Match.status == "nova").group_by(Match.cliente_id)
    ).all())
    if quer_planilha(formato):
        checar_exportar(usuario)
        enviados = dict(db.execute(select(Match.cliente_id, func.count(Match.id)).where(Match.status == "enviada")
                                   .group_by(Match.cliente_id)).all())
        colunas = [
            Coluna("Nome", lambda c: c.nome), Coluna("Telefone", lambda c: col_.telefone(c.telefone)),
            Coluna("E-mail", lambda c: c.email), Coluna("CPF / CNPJ", lambda c: col_.documento(c.documento)),
            Coluna("Nascimento", lambda c: c.data_nascimento, "data"), Coluna("Classificação", lambda c: c.classificacao),
            Coluna("Autorizou WhatsApp", lambda c: sim_nao(c.consentimento)),
            Coluna("Trechos de interesse", lambda c: "; ".join(_descrever_interesse(i) for i in c.interesses)),
            Coluna("Promoções na fila", lambda c: pendentes.get(c.id, 0)),
            Coluna("Promoções enviadas", lambda c: enviados.get(c.id, 0)),
            Coluna("Situação", lambda c: col_.situacao(c.ativo)), Coluna("Observações", lambda c: c.observacoes),
            Coluna("Cadastrado em", lambda c: c.criado_em, "data"),
        ]
        return exportar(request, db, usuario, "Clientes dos alertas", colunas, clientes)
    for aviso in avisos:
        avisar(request, aviso, "erro")
    filtrando = bool(q.strip() or situacao not in ("prontos", "todos") or origem.strip() or destino.strip() or cadastro or ordem != "nome")
    return render(request, "clientes.html", usuario, clientes=clientes, q=q, pendentes=pendentes, situacao=situacao,
                  situacoes=SITUACOES_CLIENTE, origem=origem, destino=destino, cadastro=cadastro, ordem=ordem,
                  filtrando=filtrando)


PASTA_IMPORTACOES = BASE_DIR / "importacoes"
TAMANHO_MAX_PLANILHA = 5 * 1024 * 1024


def _arquivo_importacao(token: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{16}\.(xlsx|xlsm|csv|txt)", token):
        return None
    caminho = PASTA_IMPORTACOES / token
    return caminho if caminho.exists() else None


def _tipo_planilha_manager(conteudo: bytes, nome_arquivo: str) -> str | None:
    """Tipo da lista do Manager, quando a planilha enviada na importação dos alertas é do Manager."""
    from .mvo import importar_manager as imp

    try:
        titulos, _, corpo = imp.ler(conteudo, nome_arquivo, "pessoas")
    except Exception:
        return None
    return imp.detectar_tipo(titulos, corpo)


@app.get("/clientes/importar", response_class=HTMLResponse)
def tela_importar(request: Request, usuario: Usuario = Depends(pode_alertas)):
    return render(request, "importar.html", usuario, etapa="enviar")


@app.get("/clientes/importar/modelo.xlsx")
def baixar_modelo(usuario: Usuario = Depends(pode_alertas)):
    return Response(
        importacao.planilha_modelo(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="modelo-clientes.xlsx"'},
    )


@app.post("/clientes/importar", response_class=HTMLResponse)
async def conferir_importacao(
    request: Request, arquivo: UploadFile = File(...), origem_padrao: str = Form(""),
    consentimento_padrao: bool = Form(False),
    db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas),
):
    conteudo = await arquivo.read(TAMANHO_MAX_PLANILHA + 1)
    if len(conteudo) > TAMANHO_MAX_PLANILHA:
        avisar(request, "Arquivo grande demais (máximo 5 MB).", "erro")
        return voltar("/clientes/importar")
    # lista exportada do Manager (colaboradores e clientes, agências, contas): vai para a importação do Manager
    tipo_manager = _tipo_planilha_manager(conteudo, arquivo.filename or "")
    if tipo_manager:
        if pode(usuario, "configurar", "milhas_operar", "custos"):
            avisar(request, "Esta planilha é do Manager: ela foi lida pela importação do Manager.")
            return rotas_importar.conferencia(request, db, usuario, conteudo, arquivo.filename or "", tipo_manager)
        avisar(request, "Esta planilha é do Manager (colaboradores, clientes ou agências): quem cuida do Manager "
                        "importa em Manager → Importar do Manager.", "erro")
        return voltar("/clientes/importar")
    extensao = Path(arquivo.filename or "").suffix.lower().lstrip(".")
    origem = aeroportos.resolver(origem_padrao) if origem_padrao.strip() else None
    if origem_padrao.strip() and not origem:
        avisar(request, f"Origem padrão não reconhecida: “{origem_padrao}”.", "erro")
        return voltar("/clientes/importar")
    try:
        cabecalhos, linhas = importacao.ler_planilha(conteudo, arquivo.filename or "")
    except Exception as erro:
        avisar(request, f"Não consegui ler a planilha: {erro}", "erro")
        return voltar("/clientes/importar")

    mapa, itens = importacao.interpretar(db, cabecalhos, linhas, origem, consentimento_padrao)
    if "telefone" not in mapa or "nome" not in mapa:
        avisar(request, "Não encontrei as colunas de nome e telefone. Confira os títulos na primeira linha "
                        "(ex.: “Nome” e “WhatsApp”).", "erro")
        return voltar("/clientes/importar")

    PASTA_IMPORTACOES.mkdir(exist_ok=True)
    token = f"{secrets.token_hex(8)}.{extensao}"
    (PASTA_IMPORTACOES / token).write_bytes(conteudo)
    contagem = {s: sum(1 for i in itens if i.situacao == s) for s in ("novo", "atualizar", "erro")}
    contagem["trechos"] = sum(len(i.interesses) for i in itens if i.valida)
    return render(
        request, "importar.html", usuario, etapa="conferir", token=token, itens=itens,
        colunas=[(importacao.CAMPOS_EXIBICAO.get(c, c), cabecalhos[i]) for c, i in mapa.items()
                 if c in importacao.CAMPOS_EXIBICAO],
        sem_coluna=[n for c, n in importacao.CAMPOS_EXIBICAO.items() if c not in mapa],
        contagem=contagem, arquivo=arquivo.filename, origem_padrao=origem or "",
        consentimento_padrao=consentimento_padrao,
    )


@app.post("/clientes/importar/confirmar")
def confirmar_importacao(
    request: Request, token: str = Form(...), origem_padrao: str = Form(""),
    consentimento_padrao: bool = Form(False),
    db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas),
):
    caminho = _arquivo_importacao(token)
    if not caminho:
        avisar(request, "Essa importação expirou. Envie a planilha de novo.", "erro")
        return voltar("/clientes/importar")
    cabecalhos, linhas = importacao.ler_planilha(caminho.read_bytes(), caminho.name)
    _, itens = importacao.interpretar(db, cabecalhos, linhas, aeroportos.resolver(origem_padrao), consentimento_padrao)
    try:
        r = importacao.aplicar(db, itens)
    except Exception as erro:  # noqa: BLE001 - nada é gravado; a equipe vê o motivo em vez de "Internal Server Error"
        db.rollback()
        log.exception("Importação de clientes falhou")
        avisar(request, f"A importação não foi concluída e nada foi gravado: {str(erro).splitlines()[0][:200]}. "
                        "Confira a planilha (células com textos muito longos ou dados misturados) e envie de novo.", "erro")
        return voltar("/clientes/importar")
    caminho.unlink(missing_ok=True)
    texto = (f"Importação concluída: {r['novos']} clientes novos, {r['atualizados']} atualizados, "
             f"{r['interesses']} trechos de interesse adicionados.")
    if r["erros"]:
        texto += f" {r['erros']} linha(s) com erro foram ignoradas."
    if r["matches"]:
        texto += f" {r['matches']} já têm promoção na fila de envio!"
    avisar(request, texto)
    return voltar("/clientes")


@app.get("/clientes/formulario", response_class=HTMLResponse)
def tela_formulario(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    chave = formulario.chave_secreta(db)
    return render(request, "formulario.html", usuario, formularios=formulario.formularios(db),
                  pode_mais=formulario.proximo_numero(db) is not None,
                  script=formulario.script_apps(chave),
                  automatico=settings.coleta_automatica, intervalo=settings.intervalo_formulario_min)


@app.post("/clientes/formulario")
def salvar_formulario(request: Request, url: str = Form(""), nome: str = Form(""), n: str = Form(""),
                      db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    """Conecta um formulário novo (sem n) ou troca o endereço / nome de um já ligado (com n)."""
    url = url.strip()
    numero = int(n) if n.isdigit() else formulario.proximo_numero(db)
    if numero is None:
        avisar(request, f"Já há {formulario.MAXIMO} formulários ligados. Desconecte um para ligar outro.", "erro")
        return voltar("/clientes/formulario")
    if not url:
        formulario.configurar(db, "", numero)
        avisar(request, "Formulário desconectado.")
        return voltar("/clientes/formulario")
    erro = formulario.validar_url(url)
    if erro:
        avisar(request, erro, "erro")
        return voltar("/clientes/formulario")
    repetido = next((f for f in formulario.formularios(db) if f["url"] == url and f["n"] != numero), None)
    if repetido:
        avisar(request, f"Esse endereço já está ligado como “{repetido['nome']}”.", "erro")
        return voltar("/clientes/formulario")
    formulario.configurar(db, url, numero, nome or None)
    return sincronizar_formulario(request, usuario)


@app.post("/clientes/formulario/sincronizar")
def sincronizar_formulario(request: Request, usuario: Usuario = Depends(pode_alertas)):
    r = coleta.executar("formulario")
    if r.get("erro"):
        avisar(request, f"Não consegui ler a planilha: {r['erro']}", "erro")
        return voltar("/clientes/formulario")
    for erro in r.get("erros_formularios", []):
        avisar(request, f"Não consegui ler {erro}", "erro")
    if not r.get("novas"):
        avisar(request, f"Planilhas lidas ({r.get('respostas', 0)} respostas). Nenhuma resposta nova desde a última leitura.")
    else:
        texto = (f"{r['novas']} resposta(s) nova(s): {r['novos']} cliente(s) novo(s), {r['atualizados']} atualizado(s), "
                 f"{r['interesses']} trecho(s) adicionado(s).")
        if r.get("matches"):
            texto += f" {r['matches']} já têm promoção na fila!"
        avisar(request, texto)
        for linha in r.get("linhas_com_erro", []):
            avisar(request, f"Resposta ignorada: {linha}", "erro")
    return voltar("/clientes/formulario")


@app.get("/clientes/novo", response_class=HTMLResponse)
def novo_cliente(request: Request, usuario: Usuario = Depends(pode_alertas)):
    return render(request, "cliente_form.html", usuario, cliente=None)


def _salvar_interesse(request: Request, db: Session, cliente: Cliente, origem: str, destino: str,
                      inicio: str, fim: str, preco_maximo: str, observacao: str) -> bool:
    cod_origem, cod_destino = aeroportos.resolver(origem), aeroportos.resolver(destino)
    if not cod_origem:
        avisar(request, f"Origem não reconhecida: “{origem}”. Use o código do aeroporto (ex.: FOR) ou o nome da cidade.", "erro")
        return False
    if destino.strip() and not cod_destino:
        avisar(request, f"Destino não reconhecido: “{destino}”.", "erro")
        return False
    teto = _numero(preco_maximo)
    if teto is not None and not 0 < teto <= 200_000:
        avisar(request, f"Preço máximo “{preco_maximo}” não parece certo. Use só o valor, ex.: 3.000", "erro")
        return False
    interesse = Interesse(
        cliente=cliente, origem=cod_origem, destino=cod_destino, periodo_inicio=_data(inicio),
        periodo_fim=_data(fim), preco_maximo=teto, observacao=observacao.strip() or None,
    )
    db.add(interesse)
    db.commit()
    qtd = gerar_matches_para_interesse(db, interesse)
    if qtd:
        avisar(request, f"Já existem {qtd} promoção(ões) compatível(is) com esse trecho na fila de envio.")
    return True


@app.post("/clientes/novo")
def criar_cliente(
    request: Request,
    nome: str = Form(...), telefone: str = Form(...), email: str = Form(""), observacoes: str = Form(""),
    consentimento: bool = Form(False),
    origem: str = Form(""), destino: str = Form(""), inicio: str = Form(""), fim: str = Form(""),
    preco_maximo: str = Form(""),
    db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas),
):
    tel = normalizar_telefone(telefone)
    if len(tel) < 10:
        avisar(request, "Telefone inválido. Informe DDD + número.", "erro")
        return voltar("/clientes/novo")
    duplicado = db.scalar(select(Cliente).where(Cliente.telefone == tel))
    if duplicado:
        avisar(request, f"Esse telefone já pertence a {duplicado.nome}. Adicione o interesse no cadastro dele.", "erro")
        return voltar(f"/clientes/{duplicado.id}")
    cliente = Cliente(nome=ajustar_nome(nome), telefone=tel, email=email.strip() or None,
                      observacoes=observacoes.strip() or None, consentimento=consentimento)
    db.add(cliente)
    db.commit()
    if origem.strip():
        _salvar_interesse(request, db, cliente, origem, destino, inicio, fim, preco_maximo, "")
    avisar(request, f"Cliente {cliente.nome} cadastrado.")
    return voltar(f"/clientes/{cliente.id}")


@app.get("/clientes/{cliente_id}", response_class=HTMLResponse)
def ver_cliente(cliente_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        raise HTTPException(404)
    historico = db.scalars(
        select(Match).where(Match.cliente_id == cliente_id).options(selectinload(Match.promocao))
        .order_by(Match.criado_em.desc()).limit(50)
    ).all()
    modelo = template_padrao(db).texto
    site = endereco_site(request)
    site_msg = site if link_nas_mensagens(db) else ""
    emissoes = []
    if pode(usuario, "milhas_ver"):
        emissoes = db.scalars(
            select(Emissao).where(Emissao.cliente_id == cliente_id).order_by(Emissao.data_emissao.desc()).limit(30)
        ).all()
    # confirmação do cadastro: a que espera envio, a última enviada ou, sem nenhuma, uma com todos os trechos
    conf = _confirmacao_do_cliente(cliente)
    trechos = trechos_da_confirmacao(conf) if conf else [i for i in cliente.interesses if i.ativo]
    texto_conf = montar_confirmacao(template_confirmacao(db).texto, cliente, trechos, link_pagina(site_msg, cliente))
    from . import whatsapp_api as api_whats

    conf_api = api_whats.configuracao(db)
    confirmacao = {"conf": conf, "mensagem": texto_conf, "link": link_whatsapp(cliente.telefone, texto_conf),
                   "registrar": f"/confirmacoes/{conf.id}/enviar" if conf else f"/clientes/{cliente.id}/confirmacao",
                   "api": api_whats.configurada(db) and bool(conf_api["modelo_confirmacao"]),
                   "modelo": conf_api["modelo_confirmacao"]}
    # mensagem de retorno (Trazer de volta): o texto pronto, se o cliente está na lista e as últimas tentativas
    from . import reengajamento
    from . import whatsapp_api as api
    from .models import Reengajamento

    alvo, na_lista = reengajamento.candidato_de(db, cliente)
    texto_retorno = reengajamento.texto_manual(db, alvo, site)
    conf_retorno = reengajamento.configuracao(db)
    retorno = {"texto": texto_retorno, "na_lista": na_lista, "candidato": alvo,
               "dias_sem_compra": conf_retorno["dias_sem_compra"], "modelo": conf_retorno["modelo"],
               "api": api.configurada(db) and bool(conf_retorno["modelo"]),
               "tentativas": db.scalars(select(Reengajamento).where(Reengajamento.cliente_id == cliente_id)
                                        .order_by(Reengajamento.quando.desc()).limit(5)).all()}
    # sem nenhum trecho cadastrado: a pergunta de trechos ("de onde sai e para onde quer viajar?")
    pergunta = None
    if not any(i.ativo for i in cliente.interesses):
        texto = montar_confirmacao(template_pergunta(db).texto, cliente, [], "")
        pergunta = {"texto": texto, "link": link_whatsapp(cliente.telefone, texto)}
    if not cliente.token_pagina:   # cliente antigo sem código: ganha o dele para ter a página
        from .models import novo_token

        cliente.token_pagina = novo_token()
        db.commit()
    return render(request, "cliente.html", usuario, cliente=cliente, emissoes=emissoes,
                  historico=[_item_envio(modelo, m, site_msg) for m in historico], confirmacao=confirmacao,
                  retorno=retorno, pergunta=pergunta, link_ligado=bool(site_msg),
                  link_da_pagina=link_pagina(site, cliente))


@app.post("/clientes/{cliente_id}")
def editar_cliente(
    cliente_id: int, request: Request,
    nome: str = Form(...), telefone: str = Form(...), email: str = Form(""), observacoes: str = Form(""),
    consentimento: bool = Form(False), ativo: bool = Form(False),
    db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas),
):
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        raise HTTPException(404)
    tel = normalizar_telefone(telefone)
    outro = db.scalar(select(Cliente).where(Cliente.telefone == tel, Cliente.id != cliente_id))
    if len(tel) < 10 or outro:
        avisar(request, "Telefone inválido ou já cadastrado em outro cliente.", "erro")
        return voltar(f"/clientes/{cliente_id}")
    cliente.nome, cliente.telefone = ajustar_nome(nome), tel
    cliente.email, cliente.observacoes = email.strip() or None, observacoes.strip() or None
    cliente.consentimento, cliente.ativo = consentimento, ativo
    db.commit()
    avisar(request, "Cadastro atualizado.")
    return voltar(f"/clientes/{cliente_id}")


@app.post("/clientes/{cliente_id}/excluir")
def excluir_cliente(cliente_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    cliente = db.get(Cliente, cliente_id)
    if cliente:
        db.delete(cliente)
        db.commit()
        avisar(request, f"Cliente {cliente.nome} excluído.")
    return voltar("/clientes")


@app.post("/clientes/{cliente_id}/interesses")
def adicionar_interesse(
    cliente_id: int, request: Request,
    origem: str = Form(...), destino: str = Form(""), inicio: str = Form(""), fim: str = Form(""),
    preco_maximo: str = Form(""), observacao: str = Form(""),
    db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas),
):
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        raise HTTPException(404)
    if _salvar_interesse(request, db, cliente, origem, destino, inicio, fim, preco_maximo, observacao):
        avisar(request, "Interesse adicionado.")
    return voltar(f"/clientes/{cliente_id}")


@app.post("/interesses/{interesse_id}/alternar")
def alternar_interesse(interesse_id: int, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    i = db.get(Interesse, interesse_id)
    if not i:
        raise HTTPException(404)
    i.ativo = not i.ativo
    db.commit()
    if i.ativo:
        gerar_matches_para_interesse(db, i)
    return voltar(f"/clientes/{i.cliente_id}")


@app.post("/interesses/{interesse_id}/excluir")
def excluir_interesse(interesse_id: int, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    i = db.get(Interesse, interesse_id)
    if not i:
        raise HTTPException(404)
    cliente_id = i.cliente_id
    db.delete(i)
    db.commit()
    return voltar(f"/clientes/{cliente_id}")


# ---------------------------------------------------------------- promoções por interesse


def _interesse_no_lugar(termo: str):
    """Palavra da busca que é uma cidade ou um código: trechos saindo ou chegando lá."""
    codigo = aeroportos.resolver(termo) if len(termo) >= 3 else None
    if not codigo:
        return false()
    codigos = aeroportos.codigos_do_lugar(codigo)
    return or_(Interesse.origem.in_(codigos), Interesse.destino.in_(codigos))


ORDENS_INTERESSE = {"promocoes": "Mais promoções", "clientes": "Mais clientes", "nome": "Ordem alfabética"}


@app.get("/interesses", response_class=HTMLResponse)
def por_interesse(request: Request, q: str = "", origem: str = "", destino: str = "", promos: str = "",
                  ordem: str = "promocoes", formato: str = "", db: Session = Depends(get_db),
                  usuario: Usuario = Depends(pode_alertas)):
    consulta = (select(Interesse).join(Cliente).where(Interesse.ativo.is_(True), Cliente.ativo.is_(True))
                .options(selectinload(Interesse.cliente)))
    consulta = busca.aplicar(consulta, q, [Cliente.nome, Cliente.email, _interesse_no_lugar], [Cliente.telefone])
    trecho, avisos = _filtro_trecho(origem, destino)
    if trecho is not None:
        consulta = consulta.where(trecho)
    interesses = db.scalars(consulta).all()
    vigentes = promocoes_vigentes(db)
    trechos: dict[tuple, dict] = defaultdict(lambda: {"interesses": [], "promos": {}})
    for i in interesses:
        chave = (aeroportos.grupo(i.origem), aeroportos.grupo(i.destino) if i.destino else None)
        t = trechos[chave]
        t["origem"], t["destino"] = chave
        t["interesses"].append(i)
        for p in vigentes:
            if interesse_compativel(i, p):
                t["promos"][p.id] = p
    ordem = ordem if ordem in ORDENS_INTERESSE else "promocoes"
    if ordem == "clientes":
        lista = sorted(trechos.values(), key=lambda t: (-len({i.cliente_id for i in t["interesses"]}), -len(t["promos"])))
    elif ordem == "nome":
        lista = sorted(trechos.values(), key=lambda t: (busca.sem_acento(aeroportos.nome(t["origem"])),
                                                        busca.sem_acento(aeroportos.nome(t["destino"]))))
    else:
        lista = sorted(trechos.values(), key=lambda t: (-len(t["promos"]), -len(t["interesses"])))
    if promos == "com":
        lista = [t for t in lista if t["promos"]]
    elif promos == "sem":
        lista = [t for t in lista if not t["promos"]]
    for t in lista:
        t["promos"] = sorted(t["promos"].values(), key=lambda p: (preco_reais(p) is None, preco_reais(p) or 0))
    if quer_planilha(formato):
        checar_exportar(usuario)
        colunas = [
            Coluna("Origem", lambda t: aeroportos.nome(t["origem"]) if t["origem"] else None),
            Coluna("Destino", lambda t: aeroportos.nome(t["destino"]) if t["destino"] else "Qualquer destino"),
            Coluna("Clientes interessados", lambda t: len({i.cliente_id for i in t["interesses"]})),
            Coluna("Nomes", lambda t: sorted({i.cliente.nome for i in t["interesses"]})),
            Coluna("Promoções vigentes", lambda t: len(t["promos"])),
            Coluna("Menor preço vigente", lambda t: next((preco_reais(p) for p in t["promos"] if preco_reais(p)), None),
                   "dinheiro"),
        ]
        return exportar(request, db, usuario, "Trechos de interesse", colunas, lista)
    for aviso in avisos:
        avisar(request, aviso, "erro")
    filtrando = bool(q.strip() or origem.strip() or destino.strip() or promos or ordem != "promocoes")
    return render(request, "interesses.html", usuario, trechos=lista, q=q, origem=origem, destino=destino,
                  promos=promos, ordem=ordem, ordens=ORDENS_INTERESSE, filtrando=filtrando)


# ---------------------------------------------------------------- promoções


def _promocao_no_lugar(termo: str):
    """Palavra da busca que é uma cidade ou um código ("lisboa", "gru"): promoções saindo ou chegando lá."""
    codigo = aeroportos.resolver(termo) if len(termo) >= 3 else None
    if not codigo:
        return false()
    codigos = aeroportos.codigos_do_lugar(codigo)
    return or_(Promocao.origem.in_(codigos), Promocao.destino.in_(codigos))


@app.get("/promocoes", response_class=HTMLResponse)
def lista_promocoes(request: Request, fonte: str = "", status: str = "ativa", q: str = "", origem: str = "",
                    destino: str = "", tipo: str = "", categoria: str = "", lidas: str = "", interessados: str = "",
                    revisar: str = "", formato: str = "", db: Session = Depends(get_db),
                    usuario: Usuario = Depends(pode_alertas)):
    consulta = (select(Promocao).options(selectinload(Promocao.matches).selectinload(Match.cliente), selectinload(Promocao.imagem))
                .order_by(Promocao.coletada_em.desc()))
    if fonte:
        consulta = consulta.where(Promocao.fonte == fonte)
    categoria = categoria if categoria in TIPOS_PROMOCAO else ""
    if categoria:
        consulta = consulta.where(Promocao.tipo == categoria)
    if status:
        consulta = consulta.where(Promocao.status == status)
    consulta = busca.aplicar(consulta, q, [Promocao.origem, Promocao.destino, Promocao.companhia, Promocao.programa,
                                           Promocao.fonte_detalhe, Promocao.datas_texto, Promocao.texto_original,
                                           Promocao.titulo, Promocao.lugar, Promocao.resumo, _promocao_no_lugar])
    de, para, avisos = _lugares_digitados(origem, destino)
    if de is not None:
        consulta = consulta.where(Promocao.origem.in_(sorted(de)))
    if para is not None:
        consulta = consulta.where(Promocao.destino.in_(sorted(para)))
    if tipo == "reais":
        consulta = consulta.where(Promocao.preco.is_not(None))
    elif tipo == "milhas":
        consulta = consulta.where(Promocao.milhas.is_not(None))
    if busca.desde(lidas):
        consulta = consulta.where(Promocao.coletada_em >= busca.desde(lidas))
    if interessados:
        consulta = consulta.where(Promocao.matches.any())
    if revisar:
        consulta = consulta.where(or_(and_(Promocao.alerta.is_not(None), Promocao.alerta != ""),
                                      Promocao.confianca < settings.confianca_minima))
    if quer_planilha(formato):
        checar_exportar(usuario)
        colunas = [
            Coluna("Lida em", lambda p: p.coletada_em, "datahora"), Coluna("Fonte", lambda p: FONTES.get(p.fonte, p.fonte)),
            Coluna("Detalhe da fonte", lambda p: p.fonte_detalhe), Coluna("Tipo", lambda p: p.nome_tipo),
            Coluna("Origem", lambda p: p.origem), Coluna("Destino", lambda p: p.destino),
            Coluna("Título", lambda p: p.titulo), Coluna("Lugar", lambda p: p.lugar), Coluna("Companhia", lambda p: p.companhia),
            Coluna("Viagem", lambda p: {"ida": "Só ida", "ida_volta": "Ida e volta"}.get(p.tipo_viagem or "", None)),
            Coluna("Datas", lambda p: formatar_periodo(p.data_inicio, p.data_fim, p.datas_texto)),
            Coluna("Preço (R$)", lambda p: p.preco, "dinheiro"), Coluna("Milhas", lambda p: p.milhas, "pontos"),
            Coluna("Programa", lambda p: p.programa), Coluna("Valor em R$", lambda p: preco_reais(p), "dinheiro"),
            Coluna("Clientes compatíveis", lambda p: len(p.matches)),
            Coluna("Enviadas", lambda p: sum(1 for m in p.matches if m.status == "enviada")),
            Coluna("Status", lambda p: {"ativa": "Ativa", "descartada": "Descartada"}.get(p.status, p.status)),
            Coluna("Aviso", lambda p: p.alerta), Coluna("Link", lambda p: p.link or p.link_fonte),
            Coluna("Resumo", lambda p: p.resumo), Coluna("Texto original", lambda p: p.texto_original),
        ]
        return exportar(request, db, usuario, "Promoções", colunas, db.scalars(consulta.limit(20_000)).all())
    consulta = consulta.limit(300)
    limite = datetime.now() - timedelta(days=settings.promo_validade_dias)
    for aviso in avisos:
        avisar(request, aviso, "erro")
    filtrando = bool(q.strip() or origem.strip() or destino.strip() or tipo or categoria or lidas or interessados
                     or revisar)
    return render(request, "promocoes.html", usuario, promocoes=db.scalars(consulta).all(),
                  fonte=fonte, status=status, limite=limite, q=q, origem=origem, destino=destino, tipo=tipo,
                  categoria=categoria, lidas=lidas, interessados=interessados, revisar=revisar, filtrando=filtrando)


def _aeroportos_js() -> dict[str, str]:
    """Mapa usado na prévia da tela de nova promoção: código, apelidos (~nome) e nome curto (CODIGO#)."""
    mapa: dict[str, str] = {}
    for cod in aeroportos.AEROPORTOS:
        mapa[cod] = cod
        mapa[cod + "#"] = aeroportos.nome_curto(cod)
        mapa["~" + aeroportos.sem_acento(aeroportos.nome(cod))] = cod
    for apelido, cod in aeroportos.APELIDOS.items():
        mapa["~" + apelido] = cod
    return mapa


COMPANHIAS = sorted({"Gol", "Azul", "LATAM", "TAP", "Air France", "KLM", "Iberia", "American Airlines", "United",
                     "Delta", "Copa Airlines", "Avianca", "Emirates", "Qatar", "Lufthansa", "Aerolíneas Argentinas",
                     "JetSMART", "Sky Airline", "Air Europa", "ITA Airways", "Turkish", "Ethiopian", "Swiss",
                     "British Airways", "Arajet", "Flybondi", "Air Canada", "Aeromexico"})


def _tela_promocao(request: Request, usuario: Usuario, etapa: str, texto: str = "", dados=None, **extra):
    from . import ia
    from .servicos import tabela_programas

    return render(request, "promocao_nova.html", usuario, etapa=etapa, texto=texto, dados=dados,
                  programas={k: list(v) for k, v in tabela_programas().items()},
                  aeroportos_js=_aeroportos_js(), companhias=COMPANHIAS, ia_ligada=ia.configurada(), **extra)


@app.get("/promocoes/nova", response_class=HTMLResponse)
def nova_promocao(request: Request, usuario: Usuario = Depends(pode_alertas)):
    return _tela_promocao(request, usuario, "texto")


@app.post("/promocoes/analisar", response_class=HTMLResponse)
def analisar_promocao(request: Request, texto: str = Form(...), usuario: Usuario = Depends(pode_alertas)):
    return _tela_promocao(request, usuario, "conferir", texto, extrair(texto))


# imagem enviada em "Nova promoção": fica num arquivo temporário até a equipe salvar a promoção
_RE_TOKEN_IMAGEM = re.compile(r"[0-9a-f]{16}\.jpg")


def _imagem_temporaria(token: str) -> Path | None:
    if not _RE_TOKEN_IMAGEM.fullmatch(token or ""):
        return None
    caminho = PASTA_IMPORTACOES / token
    return caminho if caminho.exists() else None


@app.post("/promocoes/ler-imagem", response_class=HTMLResponse)
async def ler_imagem_promocao(request: Request, imagem: UploadFile = File(...), db: Session = Depends(get_db),
                              usuario: Usuario = Depends(pode_alertas)):
    """Lê a imagem da promoção com a IA e preenche os blocos; a imagem fica guardada para a promoção."""
    from . import ia
    from .servicos import preparar_imagem

    conteudo = await imagem.read()
    if not conteudo or len(conteudo) > leitura.TAMANHO_MAX_IMAGEM:
        avisar(request, "Envie uma imagem JPEG, PNG ou WebP de até 8 MB.", "erro")
        return voltar("/promocoes/nova")
    if not ia.configurada():
        avisar(request, "A leitura de imagens precisa da chave GEMINI_API_KEY nas variáveis do Render.", "erro")
        return voltar("/promocoes/nova")
    conteudo, _ = preparar_imagem(conteudo, imagem.content_type)
    PASTA_IMPORTACOES.mkdir(exist_ok=True)
    token = secrets.token_hex(8) + ".jpg"
    (PASTA_IMPORTACOES / token).write_bytes(conteudo)
    dados = None
    try:
        if not ia.cota_disponivel(db):
            raise ia.CotaExcedida("limite diário de leituras atingido")
        lida = ia.ler(imagem=conteudo, mime="image/jpeg")
        ia.registrar_uso(db)
        if lida.eh_promocao:
            dados = ia.para_extraida(lida)
        else:
            avisar(request, "A IA não reconheceu uma promoção na imagem. Preencha os blocos à mão.", "erro")
    except ia.CotaExcedida:
        avisar(request, "A cota gratuita de leituras por IA acabou por hoje. Preencha os blocos à mão; a imagem fica na promoção.", "erro")
    except Exception as erro:  # falha de rede, resposta estranha
        log.warning("Leitura da imagem falhou: %s", erro)
        avisar(request, "Não consegui ler a imagem agora. Preencha os blocos à mão; a imagem fica na promoção.", "erro")
    return _tela_promocao(request, usuario, "conferir", "", dados, imagem_token=token,
                          datas_texto=dados.datas_texto if dados else "")


@app.get("/promocoes/imagem-temporaria/{token}")
def imagem_temporaria(token: str, usuario: Usuario = Depends(pode_alertas)):
    caminho = _imagem_temporaria(token)
    if not caminho:
        raise HTTPException(404)
    return Response(caminho.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=600"})


@app.post("/promocoes/nova")
def salvar_promocao(
    request: Request,
    texto: str = Form(""), tipo: str = Form("passagem"), origem: str = Form(""), destino: str = Form(""),
    titulo: str = Form(""), lugar: str = Form(""), resumo: str = Form(""), preco: str = Form(""),
    milhas: str = Form(""), inicio: str = Form(""), fim: str = Form(""), datas_texto: str = Form(""),
    companhia: str = Form(""), link: str = Form(""), fonte_detalhe: str = Form(""), programa: str = Form(""),
    tipo_viagem: str = Form(""), imagem_token: str = Form(""),
    db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas),
):
    tipo = tipo if tipo in TIPOS_PROMOCAO else "passagem"
    passagem = tipo == "passagem"
    cod_o, cod_d = aeroportos.resolver(origem), aeroportos.resolver(destino)
    cod_lugar = aeroportos.resolver(lugar) if lugar.strip() else None
    m = _numero(milhas)
    dados = PromocaoExtraida(
        tipo=tipo,
        origem=(cod_o or origem) if passagem else None,
        destino=(cod_d or destino) if passagem else cod_lugar,
        titulo=None if passagem else (titulo.strip() or None),
        lugar=None if passagem else (lugar.strip() or None),
        resumo=resumo.strip() or None,
        preco=_numero(preco), milhas=int(m) if m else None,
        data_inicio=_data(inicio), data_fim=_data(fim) or _data(inicio),
        companhia=companhia.strip() or None, link=link.strip() or None, confianca=1.0,  # conferida pela equipe
        programa=programa.strip() or None,
        tipo_viagem=tipo_viagem if passagem and tipo_viagem in ("ida_volta", "ida") else None,
    )
    erro = None
    if passagem and (not cod_o or not cod_d):
        erro = "Informe origem e destino válidos (código do aeroporto ou nome da cidade)."
    elif passagem and (cod_o == cod_d or aeroportos.mesmo_lugar(cod_o, cod_d)):
        erro = "Origem e destino estão na mesma cidade."
    elif not passagem and not dados.titulo:
        erro = "Dê um nome para a oferta (ex.: o hotel ou o pacote)."
    elif not dados.preco and not dados.milhas:
        erro = "Informe o valor: preço em reais ou quantidade de milhas."
    elif dados.data_inicio and dados.data_fim and dados.data_fim < dados.data_inicio:
        erro = "A data de volta está antes da data de ida." if passagem else "A data final está antes da inicial."
    if erro:
        avisar(request, erro, "erro")
        return _tela_promocao(request, usuario, "conferir", texto, dados, datas_texto=datas_texto,
                              fonte_detalhe=fonte_detalhe, imagem_token=imagem_token if _imagem_temporaria(imagem_token) else "")

    caminho = _imagem_temporaria(imagem_token)
    imagem = caminho.read_bytes() if caminho else None
    r = registrar_promocao(db, texto=texto or None, dados=dados, fonte="manual",
                           fonte_detalhe=fonte_detalhe.strip() or f"Cadastrada por {usuario.nome}",
                           datas_texto=datas_texto.strip() or None, imagem=imagem, mime="image/jpeg")
    if caminho:
        caminho.unlink(missing_ok=True)
    if r.motivo == "duplicada":
        avisar(request, "Essa promoção já foi cadastrada nos últimos dias.", "erro")
    else:
        avisar(request, f"Promoção salva. {r.matches} cliente(s) compatível(is) foram para a fila de envio.")
    return voltar("/" if r.matches else "/promocoes")


@app.post("/promocoes/{promo_id}/descartar")
def descartar_promocao(promo_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    p = db.get(Promocao, promo_id)
    if not p:
        raise HTTPException(404)
    p.status = "descartada"
    for m in p.matches:
        if m.status == "nova":
            m.status = "descartada"
    db.commit()
    avisar(request, "Promoção descartada.")
    return voltar(request.headers.get("referer") or "/")


@app.post("/promocoes/{promo_id}/conferir")
def conferir_promocao(promo_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    p = db.get(Promocao, promo_id)
    if not p:
        raise HTTPException(404)
    p.confianca, p.alerta = 1.0, None
    db.commit()
    avisar(request, "Promoção marcada como conferida.")
    return voltar(request.headers.get("referer") or "/")


@app.post("/promocoes/{promo_id}/reativar")
def reativar_promocao(promo_id: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    p = db.get(Promocao, promo_id)
    if not p:
        raise HTTPException(404)
    p.status = "ativa"
    db.commit()
    gerar_matches_para_promocao(db, p)
    return voltar(request.headers.get("referer") or "/promocoes")


# ---------------------------------------------------------------- histórico


@app.get("/historico", response_class=HTMLResponse)
def historico(request: Request, dias: int = 7, q: str = "", quem: str = "", origem: str = "", destino: str = "",
              tipo: str = "", formato: str = "", db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    desde = datetime.now() - timedelta(days=dias)
    no_periodo = select(Match).join(Cliente).join(Promocao).where(Match.status == "enviada", Match.enviado_em >= desde)
    pessoas = sorted({p for p in db.scalars(no_periodo.with_only_columns(Match.enviado_por).distinct()) if p})
    consulta = busca.aplicar(no_periodo, q, [Cliente.nome, Cliente.email, Promocao.origem, Promocao.destino,
                                             Promocao.companhia, _promocao_no_lugar], [Cliente.telefone])
    if quem:
        consulta = consulta.where(Match.enviado_por == quem)
    de, para, avisos = _lugares_digitados(origem, destino)
    if de is not None:
        consulta = consulta.where(Promocao.origem.in_(sorted(de)))
    if para is not None:
        consulta = consulta.where(Promocao.destino.in_(sorted(para)))
    tipo = tipo if tipo in ("reais", "milhas") else ""
    if tipo:
        consulta = consulta.where((Promocao.preco if tipo == "reais" else Promocao.milhas).is_not(None))
    envios = db.scalars(
        consulta.options(selectinload(Match.promocao), selectinload(Match.cliente)).order_by(Match.enviado_em.desc())
    ).all()
    if quer_planilha(formato):
        checar_exportar(usuario)
        colunas = [
            Coluna("Enviado em", lambda m: m.enviado_em, "datahora"), Coluna("Enviado por", lambda m: m.enviado_por),
            Coluna("Vezes enviada", lambda m: m.envios or 1), Coluna("Cliente", lambda m: m.cliente.nome),
            Coluna("Telefone", lambda m: col_.telefone(m.cliente.telefone)),
            Coluna("Trecho", lambda m: f"{m.promocao.origem} → {m.promocao.destino}"),
            Coluna("Datas", lambda m: formatar_periodo(m.promocao.data_inicio, m.promocao.data_fim, m.promocao.datas_texto)),
            Coluna("Valor em R$", lambda m: preco_reais(m.promocao), "dinheiro"),
            Coluna("Milhas", lambda m: m.promocao.milhas, "pontos"), Coluna("Programa", lambda m: m.promocao.programa),
            Coluna("Companhia", lambda m: m.promocao.companhia),
        ]
        return exportar(request, db, usuario, f"Envios dos últimos {dias} dias", colunas, envios)
    for aviso in avisos:
        avisar(request, aviso, "erro")
    por_pessoa: dict[str, int] = defaultdict(int)
    for e in envios:
        por_pessoa[e.enviado_por or "—"] += 1
    return render(request, "historico.html", usuario, envios=envios, dias=dias, por_pessoa=dict(por_pessoa),
                  q=q, quem=quem, pessoas=pessoas, origem=origem, destino=destino, tipo=tipo,
                  filtrando=bool(q.strip() or quem or origem.strip() or destino.strip() or tipo))


# ---------------------------------------------------------------- mensagem e equipe


@app.get("/configuracoes", response_class=HTMLResponse)
def configuracoes(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(usuario_atual)):
    modelo = template_padrao(db)
    exemplo_cliente = Cliente(nome="MARIA DA SILVA OLIVEIRA", telefone="5585999999999")
    exemplo_promo = Promocao(origem="FOR", destino="LIS", preco=2389, companhia="TAP",
                             data_inicio=date(2027, 3, 1), data_fim=date(2027, 6, 30))
    confirmacao = template_confirmacao(db)
    pergunta = template_pergunta(db)
    exemplo_trechos = [Interesse(origem="FOR", destino=d, periodo_inicio=date(2027, 3, 1), periodo_fim=date(2027, 6, 30),
                                 preco_maximo=3500, ativo=True) for d in ("LIS", "PAR")]
    link_ligado = link_nas_mensagens(db)
    exemplo_link = endereco_site(request) + "/para-voce/xY7kP2qLm9Rt4vBn8sDw3Fh6"  # link de exemplo (não abre)
    exemplo_msg = exemplo_link if link_ligado else ""
    pagina_modelo = template_pagina(db)
    exemplo_promos = [exemplo_promo, Promocao(origem="FOR", destino="LIS", preco=2690, coletada_em=datetime.now()),
                      Promocao(origem="FOR", destino="PAR", preco=3450, coletada_em=datetime.now())]
    from .collectors import google_flights
    return render(request, "configuracoes.html", usuario, modelo=modelo,
                  google=google_flights.orcamento(db) if settings.serpapi_key else None,
                  google_agenda=google_flights.agenda(db) if settings.serpapi_key else None,
                  google_estimativa=google_flights.estimativa(db) if settings.serpapi_key else None,
                  google_ultima=coleta.ultima_execucao.get("google_flights"),
                  previa=montar_mensagem(modelo.texto, exemplo_cliente, exemplo_promo, exemplo_msg),
                  confirmacao=confirmacao,
                  previa_confirmacao=montar_confirmacao(confirmacao.texto, exemplo_cliente, exemplo_trechos, exemplo_msg),
                  pergunta=pergunta, previa_pergunta=montar_confirmacao(pergunta.texto, exemplo_cliente, [], ""),
                  pagina_modelo=pagina_modelo, link_ligado=link_ligado, whatsapp=whatsapp_agencia(db),
                  previa_pagina=montar_mensagem_pagina(pagina_modelo.texto, exemplo_cliente, exemplo_promos, exemplo_link))


@app.get("/milhas")
def tela_milhas(usuario: Usuario = Depends(pode_ver_precos)):
    """Endereço antigo de "Valor das milhas" / "Programas e preços": agora é a Lista de preços."""
    return voltar("/precos")


@app.post("/configuracoes/mensagem")
def salvar_modelo(request: Request, texto: str = Form(...), db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    modelo = template_padrao(db)
    modelo.texto = texto.replace("\r\n", "\n")
    db.commit()
    avisar(request, "Modelo de mensagem salvo.")
    return voltar("/configuracoes")


@app.post("/configuracoes/google-flights")
def salvar_google_flights(request: Request, limite_mes: str = Form(""), auto: str = Form(""), a_cada: str = Form(""),
                          unidade: str = Form(""), por_rodada: str = Form(""), inicio: str = Form(""), fim: str = Form(""),
                          db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    from .collectors import google_flights

    def numero(texto: str, padrao: int | None) -> int | None:
        texto = (texto or "").strip().replace(".", "")
        return padrao if not texto else max(0, int(texto))

    try:
        limite = numero(limite_mes, None)
        intervalo = numero(a_cada, 6) or 1
        rodada = numero(por_rodada, 0)
    except ValueError:
        avisar(request, "Use só números no limite, no intervalo e nas buscas por rodada.", "erro")
        return voltar("/configuracoes#google-flights")
    unidade = unidade if unidade in google_flights.UNIDADES else "horas"
    if intervalo * google_flights.UNIDADES[unidade] < google_flights.MINIMO_MINUTOS:
        avisar(request, "O intervalo mínimo é de 15 minutos.", "erro")
        return voltar("/configuracoes#google-flights")
    if bool(inicio.strip()) != bool(fim.strip()):
        avisar(request, "Preencha o começo e o fim do horário (ou deixe os dois vazios).", "erro")
        return voltar("/configuracoes#google-flights")
    if limite is not None:
        gravar_estado(db, "google_flights:limite_mes", str(limite))
    for chave, valor in (("auto", "1" if auto else "0"), ("a_cada", str(intervalo)), ("unidade", unidade),
                         ("por_rodada", str(rodada or "")), ("inicio", inicio.strip()[:5]), ("fim", fim.strip()[:5])):
        gravar_estado(db, f"google_flights:{chave}", valor)
    est = google_flights.estimativa(db)
    avisar(request, ("Google Flights: coleta automática a cada "
                     f"{intervalo} {unidade}, ~{est['mes']} buscas no mês." if auto else
                     "Google Flights: coleta automática desligada (o botão da fila continua funcionando).")
           + (" A quantidade escolhida passa do limite do mês: as buscas param quando chegar nele." if est["estoura"] else ""),
           "erro" if est["estoura"] else "ok")
    return voltar("/configuracoes#google-flights")


@app.post("/configuracoes/confirmacao")
def salvar_confirmacao(request: Request, texto: str = Form(""), restaurar: str = Form(""),
                       db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    modelo = template_confirmacao(db)
    if restaurar or not texto.strip():
        modelo.texto = TEMPLATE_CONFIRMACAO
        avisar(request, "Mensagem de confirmação voltou ao texto sugerido.")
    else:
        modelo.texto = texto.replace("\r\n", "\n")
        avisar(request, "Mensagem de confirmação salva.")
    db.commit()
    return voltar("/configuracoes#confirmacao")


@app.post("/configuracoes/pergunta")
def salvar_pergunta(request: Request, texto: str = Form(""), restaurar: str = Form(""),
                    db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    modelo = template_pergunta(db)
    if restaurar or not texto.strip():
        modelo.texto = TEMPLATE_PERGUNTA
        avisar(request, "Pergunta de trechos voltou ao texto sugerido.")
    else:
        modelo.texto = texto.replace("\r\n", "\n")
        avisar(request, "Pergunta de trechos salva.")
    db.commit()
    return voltar("/configuracoes#pergunta-trechos")


@app.post("/configuracoes/pagina")
def salvar_pagina_cliente(request: Request, texto: str = Form(""), whatsapp: str = Form(""),
                          link_nas_mensagens: str = Form(""), restaurar: str = Form(""),
                          db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    numero = normalizar_telefone(whatsapp) if whatsapp.strip() else ""
    if numero and not 12 <= len(numero) <= 15:
        avisar(request, "WhatsApp da agência inválido: use DDD e número, ex.: (85) 99999-9999.", "erro")
        return voltar("/configuracoes#pagina-cliente")
    gravar_estado(db, CHAVE_WHATSAPP_AGENCIA, numero)
    gravar_estado(db, CHAVE_LINK_NAS_MENSAGENS, "1" if link_nas_mensagens else "0")
    modelo = template_pagina(db)
    modelo.texto = TEMPLATE_PAGINA if restaurar or not texto.strip() else texto.replace("\r\n", "\n")
    db.commit()
    avisar(request, "Mensagem com o link voltou ao texto sugerido." if restaurar else "Página do cliente: ajustes salvos.")
    return voltar("/configuracoes#pagina-cliente")


@app.post("/configuracoes/senha")
def trocar_senha(request: Request, atual: str = Form(...), nova: str = Form(...),
                 db: Session = Depends(get_db), usuario: Usuario = Depends(usuario_atual)):
    if not conferir_senha(atual, usuario.senha_hash):
        avisar(request, "Senha atual incorreta.", "erro")
    elif len(nova) < 6:
        avisar(request, "A nova senha precisa ter pelo menos 6 caracteres.", "erro")
    else:
        usuario.senha_hash = gerar_hash_senha(nova)
        db.commit()
        if senha_fraca(nova, usuario.login):
            request.session["senha_fraca"] = True
            avisar(request, "Senha alterada, mas ainda é fraca: use pelo menos 8 caracteres, com letras e números.", "erro")
        else:
            request.session.pop("senha_fraca", None)
            avisar(request, "Senha alterada.")
    return voltar("/configuracoes")


# ---------------------------------------------------------------- API para integrações


class PromocaoEntrada(BaseModel):
    texto: str = ""
    fonte_detalhe: str | None = None
    link_fonte: str | None = None
    imagem_base64: str | None = None  # imagem da promoção (JPEG/PNG/WebP), lida pela IA
    mime: str | None = None


@app.post("/api/promocoes")
def api_promocao(entrada: PromocaoEntrada, x_api_key: str = Header(""), db: Session = Depends(get_db)):
    """Recebe uma promoção de outro sistema (ex.: o bot que lê os grupos do WhatsApp): texto e/ou imagem."""
    if not settings.api_key or not hmac.compare_digest(x_api_key.encode(), settings.api_key.encode()):
        raise HTTPException(401, "API_KEY inválida ou não configurada.")
    imagem = None
    if entrada.imagem_base64:
        import base64

        try:
            imagem = base64.b64decode(entrada.imagem_base64, validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(400, "imagem_base64 inválida.")
        if len(imagem) > leitura.TAMANHO_MAX_IMAGEM:
            raise HTTPException(413, "Imagem grande demais (máximo 8 MB).")
    if not entrada.texto.strip() and not imagem:
        raise HTTPException(400, "Envie o texto da promoção, a imagem ou os dois.")
    r = leitura.processar(db, fonte="api", fonte_detalhe=entrada.fonte_detalhe, link_fonte=entrada.link_fonte,
                          texto=entrada.texto, imagem=imagem, mime=entrada.mime)
    return {"resultado": r.motivo, "promocao_id": r.promocao.id if r.promocao else None, "clientes": r.matches}


@app.get("/promocoes/{promo_id}/imagem")
def imagem_promocao(promo_id: int, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    """A imagem original da promoção (para a equipe ver e mandar ao cliente)."""
    img = db.scalar(select(ImagemPromocao).where(ImagemPromocao.promocao_id == promo_id))
    if not img:
        raise HTTPException(404)
    return Response(img.dados, media_type=img.mime, headers={"Cache-Control": "private, max-age=86400",
                                                              "X-Content-Type-Options": "nosniff"})


@app.get("/saude")
def saude():
    # versão publicada (o Render informa o commit), para conferir se a atualização já entrou no ar
    return {"ok": True, "versao": os.getenv("RENDER_GIT_COMMIT", "")[:7] or None}
