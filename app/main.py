"""MVO — Mundial Viagens Organização: site da equipe.

Aqui ficam os alertas (clientes, interesses, promoções e envio manual pelo WhatsApp); as páginas de
milhas (colaboradores, contas, emissões...) ficam em app/rotas/.
"""
import logging
import re
import secrets
from collections import OrderedDict, defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware

from . import aeroportos, coleta, formulario, importacao
from .config import BASE_DIR, settings
from .database import SessionLocal, get_db, init_db
from .models import (
    CanalTelegram,
    Cliente,
    Interesse,
    Match,
    ProgramaMilhas,
    Promocao,
    Usuario,
)
from .models_mvo import Emissao
from .mvo.contas import quem_pode_emitir, recalcular_vencidos, registrar
from .rotas import emissoes as rotas_emissoes
from .rotas import equipe as rotas_equipe
from .rotas import importar_manager as rotas_importar
from .rotas import milhas as rotas_milhas
from .parser import PromocaoExtraida, extrair
from .permissoes import pode
from .web import (
    APP_DIR,
    NaoLogado,
    SemPermissao,
    avisar,
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
    limpar_cache_programas,
    milhas_sem_valor,
    preco_reais,
    recalcular_matches,
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
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    init_db()
    with SessionLocal() as db:
        preparar_banco(db)
        recalcular_vencidos(db)  # pontos com data futura que já chegaram
    agendador = coleta.iniciar_agendador() if settings.coleta_automatica else None
    yield
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


app.add_middleware(
    SessionMiddleware, secret_key=settings.secret_key, same_site="lax",
    https_only=settings.cookie_seguro, max_age=60 * 60 * 12,
)
app.add_middleware(AceitaHead)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.include_router(rotas_equipe.rotas)
app.include_router(rotas_milhas.rotas)
app.include_router(rotas_emissoes.rotas)
app.include_router(rotas_importar.rotas)



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


@app.post("/login")
def fazer_login(request: Request, login: str = Form(...), senha: str = Form(...), db: Session = Depends(get_db)):
    usuario = db.scalar(select(Usuario).where(Usuario.login == login.strip()))
    if not usuario or not usuario.ativo or not conferir_senha(senha, usuario.senha_hash):
        registrar(db, usuario, "login_recusado", None, f"login digitado: {login.strip()[:60]}", ip_de(request))
        db.commit()
        avisar(request, "Login ou senha incorretos.", "erro")
        return voltar("/login")
    request.session.clear()
    request.session["usuario_id"] = usuario.id
    usuario.ultimo_acesso = datetime.now()
    registrar(db, usuario, "login", None, None, ip_de(request))
    db.commit()
    return voltar("/" if pode(usuario, "alertas") else "/disponibilidade")


@app.get("/sair")
def sair(request: Request):
    request.session.clear()
    return voltar("/login")


# ---------------------------------------------------------------- fila de envio (página inicial)


@app.get("/", response_class=HTMLResponse)
def fila(request: Request, revisar: int = 0, ver: str = "", db: Session = Depends(get_db),
         usuario: Usuario = Depends(pode_alertas)):
    ver = ver or ("revisar" if revisar else "pendentes")
    limite = datetime.now() - timedelta(days=settings.promo_validade_dias)

    def consulta(status: str):
        return db.scalars(
            select(Match).join(Promocao).join(Cliente)
            .where(Match.status == status, Promocao.status == "ativa", Promocao.coletada_em >= limite,
                   Cliente.ativo.is_(True))
            .options(selectinload(Match.promocao), selectinload(Match.cliente), selectinload(Match.interesse))
            .order_by(Promocao.coletada_em.desc(), Match.id)
        ).all()

    matches = consulta("nova")
    enviadas = consulta("enviada")
    if ver == "enviadas":
        exibir = sorted(enviadas, key=lambda m: m.enviado_em or datetime.min, reverse=True)
    elif ver == "revisar":
        exibir = [m for m in matches if m.promocao.precisa_revisao]
    else:
        exibir = matches

    modelo = template_padrao(db).texto
    grupos: OrderedDict[int, dict] = OrderedDict()
    for m in exibir:
        g = grupos.setdefault(m.promocao_id, {"promo": m.promocao, "itens": []})
        g["itens"].append(_item_envio(modelo, m))

    hoje = datetime.combine(date.today(), datetime.min.time())
    resumo = {
        "pendentes": len(matches),
        "promocoes": len({m.promocao_id for m in matches}),
        "revisar": len({m.promocao_id for m in matches if m.promocao.precisa_revisao}),
        "enviadas_hoje": db.scalar(select(func.count(Match.id)).where(Match.status == "enviada", Match.enviado_em >= hoje)),
        "clientes": db.scalar(select(func.count(Cliente.id)).where(Cliente.ativo.is_(True))),
    }
    resumo["enviadas_vigentes"] = len(enviadas)
    # promoções em milhas: quais contas têm pontos e CPF livre para emitir (a de menor custo primeiro)
    emitir = quem_pode_emitir(db, [g["promo"] for g in grupos.values()]) if pode(usuario, "milhas_ver") else {}
    return render(request, "fila.html", usuario, grupos=list(grupos.values()), resumo=resumo, ver=ver,
                  coletas=coleta.ultima_execucao, tem_telegram=bool(settings.telegram_api_id),
                  tem_google=bool(settings.serpapi_key), emitir=emitir)


def _item_envio(modelo: str, m: Match) -> dict:
    msg = montar_mensagem(modelo, m.cliente, m.promocao)
    return {"match": m, "mensagem": msg, "link": link_whatsapp(m.cliente.telefone, msg)}


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


@app.get("/telegram", response_class=HTMLResponse)
def tela_telegram(request: Request, buscar: int = 0, db: Session = Depends(get_db),
                  usuario: Usuario = Depends(pode_alertas)):
    canais = db.scalars(select(CanalTelegram).order_by(CanalTelegram.ativo.desc(), CanalTelegram.nome)).all()
    limite = datetime.now() - timedelta(days=7)
    contagem = dict(db.execute(
        select(Promocao.fonte_detalhe, func.count(Promocao.id))
        .where(Promocao.fonte == "telegram", Promocao.coletada_em >= limite).group_by(Promocao.fonte_detalhe)
    ).all())
    promos_7d = {c.id: contagem.get(c.nome, 0) + (contagem.get(c.identificador, 0) if c.nome != c.identificador else 0)
                 for c in canais}

    grupos_conta, erro_conta = None, None
    if buscar:
        try:
            cadastrados = {c.identificador for c in canais}
            grupos_conta = [g | {"cadastrado": g["identificador"] in cadastrados} for g in coleta.listar_grupos_telegram()]
        except Exception as erro:
            erro_conta = str(erro)
    return render(request, "telegram.html", usuario, canais=canais, promos_7d=promos_7d,
                  grupos_conta=grupos_conta, erro_conta=erro_conta,
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
    if fonte not in ("telegram", "google_flights"):
        raise HTTPException(404)
    r = coleta.executar(fonte)
    if "erro" in r:
        avisar(request, f"Coleta não concluída: {r['erro']}", "erro")
    else:
        avisar(request, f"Coleta concluída: {r.get('criadas', 0)} promoções novas, {r.get('matches', 0)} clientes compatíveis.")
    return voltar("/")


# ---------------------------------------------------------------- clientes


@app.get("/clientes", response_class=HTMLResponse)
def lista_clientes(request: Request, q: str = "", db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    consulta = select(Cliente).options(selectinload(Cliente.interesses)).order_by(Cliente.nome)
    if q.strip():
        termo = f"%{q.strip()}%"
        digitos = normalizar_telefone(q) if any(c.isdigit() for c in q) else None
        filtros = [Cliente.nome.ilike(termo), Cliente.email.ilike(termo)]
        if digitos:
            filtros.append(Cliente.telefone.contains(digitos.removeprefix("55") if len(digitos) > 4 else digitos))
        consulta = consulta.where(or_(*filtros))
    clientes = db.scalars(consulta).all()
    pendentes = dict(db.execute(
        select(Match.cliente_id, func.count(Match.id)).where(Match.status == "nova").group_by(Match.cliente_id)
    ).all())
    return render(request, "clientes.html", usuario, clientes=clientes, q=q, pendentes=pendentes)


PASTA_IMPORTACOES = BASE_DIR / "importacoes"
TAMANHO_MAX_PLANILHA = 5 * 1024 * 1024


def _arquivo_importacao(token: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{16}\.(xlsx|xlsm|csv|txt)", token):
        return None
    caminho = PASTA_IMPORTACOES / token
    return caminho if caminho.exists() else None


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
    r = importacao.aplicar(db, itens)
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
    return render(request, "formulario.html", usuario, url=ler_estado(db, formulario.CHAVE_URL) or "",
                  script=formulario.script_apps(chave), resumo=formulario.ultimo_resumo(db),
                  automatico=settings.coleta_automatica, intervalo=settings.intervalo_formulario_min)


@app.post("/clientes/formulario")
def salvar_formulario(request: Request, url: str = Form(""), db: Session = Depends(get_db),
                      usuario: Usuario = Depends(pode_configurar)):
    url = url.strip()
    if url:
        erro = formulario.validar_url(url)
        if erro:
            avisar(request, erro, "erro")
            return voltar("/clientes/formulario")
    formulario.configurar(db, url)
    if not url:
        avisar(request, "Formulário desconectado.")
        return voltar("/clientes/formulario")
    return sincronizar_formulario(request, usuario)


@app.post("/clientes/formulario/sincronizar")
def sincronizar_formulario(request: Request, usuario: Usuario = Depends(pode_alertas)):
    r = coleta.executar("formulario")
    if r.get("erro"):
        avisar(request, f"Não consegui ler a planilha: {r['erro']}", "erro")
    elif not r.get("novas"):
        avisar(request, f"Planilha conectada ({r.get('respostas', 0)} respostas). Nenhuma resposta nova desde a última leitura.")
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
    emissoes = []
    if pode(usuario, "milhas_ver"):
        emissoes = db.scalars(
            select(Emissao).where(Emissao.cliente_id == cliente_id).order_by(Emissao.data_emissao.desc()).limit(30)
        ).all()
    return render(request, "cliente.html", usuario, cliente=cliente, emissoes=emissoes,
                  historico=[_item_envio(modelo, m) for m in historico])


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


@app.get("/interesses", response_class=HTMLResponse)
def por_interesse(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    interesses = db.scalars(
        select(Interesse).join(Cliente).where(Interesse.ativo.is_(True), Cliente.ativo.is_(True))
        .options(selectinload(Interesse.cliente))
    ).all()
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
    lista = sorted(trechos.values(), key=lambda t: (-len(t["promos"]), -len(t["interesses"])))
    for t in lista:
        t["promos"] = sorted(t["promos"].values(), key=lambda p: (preco_reais(p) is None, preco_reais(p) or 0))
    return render(request, "interesses.html", usuario, trechos=lista)


# ---------------------------------------------------------------- promoções


@app.get("/promocoes", response_class=HTMLResponse)
def lista_promocoes(request: Request, fonte: str = "", status: str = "ativa",
                    db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    consulta = select(Promocao).options(selectinload(Promocao.matches)).order_by(Promocao.coletada_em.desc()).limit(300)
    if fonte:
        consulta = consulta.where(Promocao.fonte == fonte)
    if status:
        consulta = consulta.where(Promocao.status == status)
    limite = datetime.now() - timedelta(days=settings.promo_validade_dias)
    return render(request, "promocoes.html", usuario, promocoes=db.scalars(consulta).all(),
                  fonte=fonte, status=status, limite=limite)


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
    from .servicos import tabela_programas

    return render(request, "promocao_nova.html", usuario, etapa=etapa, texto=texto, dados=dados,
                  programas={k: list(v) for k, v in tabela_programas().items()},
                  aeroportos_js=_aeroportos_js(), companhias=COMPANHIAS, **extra)


@app.get("/promocoes/nova", response_class=HTMLResponse)
def nova_promocao(request: Request, usuario: Usuario = Depends(pode_alertas)):
    return _tela_promocao(request, usuario, "texto")


@app.post("/promocoes/analisar", response_class=HTMLResponse)
def analisar_promocao(request: Request, texto: str = Form(...), usuario: Usuario = Depends(pode_alertas)):
    return _tela_promocao(request, usuario, "conferir", texto, extrair(texto))


@app.post("/promocoes/nova")
def salvar_promocao(
    request: Request,
    texto: str = Form(""), origem: str = Form(...), destino: str = Form(...), preco: str = Form(""),
    milhas: str = Form(""), inicio: str = Form(""), fim: str = Form(""), datas_texto: str = Form(""),
    companhia: str = Form(""), link: str = Form(""), fonte_detalhe: str = Form(""), programa: str = Form(""),
    tipo_viagem: str = Form(""),
    db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas),
):
    cod_o, cod_d = aeroportos.resolver(origem), aeroportos.resolver(destino)
    m = _numero(milhas)
    dados = PromocaoExtraida(
        origem=cod_o or origem, destino=cod_d or destino, preco=_numero(preco), milhas=int(m) if m else None,
        data_inicio=_data(inicio), data_fim=_data(fim) or _data(inicio),
        companhia=companhia.strip() or None, link=link.strip() or None, confianca=1.0,  # conferida pela equipe
        programa=programa.strip() or None,
        tipo_viagem=tipo_viagem if tipo_viagem in ("ida_volta", "ida") else None,
    )
    erro = None
    if not cod_o or not cod_d:
        erro = "Informe origem e destino válidos (código do aeroporto ou nome da cidade)."
    elif cod_o == cod_d or aeroportos.mesmo_lugar(cod_o, cod_d):
        erro = "Origem e destino estão na mesma cidade."
    elif not dados.preco and not dados.milhas:
        erro = "Informe o valor: preço em reais ou quantidade de milhas."
    elif dados.data_inicio and dados.data_fim and dados.data_fim < dados.data_inicio:
        erro = "A data de volta está antes da data de ida."
    if erro:
        avisar(request, erro, "erro")
        return _tela_promocao(request, usuario, "conferir", texto, dados,
                              datas_texto=datas_texto, fonte_detalhe=fonte_detalhe)

    r = registrar_promocao(db, texto=texto or None, dados=dados, fonte="manual",
                           fonte_detalhe=fonte_detalhe.strip() or f"Cadastrada por {usuario.nome}",
                           datas_texto=datas_texto.strip() or None)
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
def historico(request: Request, dias: int = 7, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    desde = datetime.now() - timedelta(days=dias)
    envios = db.scalars(
        select(Match).where(Match.status == "enviada", Match.enviado_em >= desde)
        .options(selectinload(Match.promocao), selectinload(Match.cliente))
        .order_by(Match.enviado_em.desc())
    ).all()
    por_pessoa: dict[str, int] = defaultdict(int)
    for e in envios:
        por_pessoa[e.enviado_por or "—"] += 1
    return render(request, "historico.html", usuario, envios=envios, dias=dias, por_pessoa=dict(por_pessoa))


# ---------------------------------------------------------------- mensagem e equipe


@app.get("/configuracoes", response_class=HTMLResponse)
def configuracoes(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(usuario_atual)):
    modelo = template_padrao(db)
    exemplo_cliente = Cliente(nome="MARIA DA SILVA OLIVEIRA", telefone="5585999999999")
    exemplo_promo = Promocao(origem="FOR", destino="LIS", preco=2389, companhia="TAP",
                             data_inicio=date(2027, 3, 1), data_fim=date(2027, 6, 30))
    return render(request, "configuracoes.html", usuario, modelo=modelo,
                  previa=montar_mensagem(modelo.texto, exemplo_cliente, exemplo_promo))


def _programas_com_uso(db: Session):
    """Programas com a quantidade de promoções vigentes de cada um (os mais usados primeiro)."""
    uso: dict[str, int] = defaultdict(int)
    for p in promocoes_vigentes(db):
        if p.milhas and p.programa:
            uso[p.programa] += 1
    programas = db.scalars(select(ProgramaMilhas)).all()
    return sorted(((p, uso.get(p.nome, 0)) for p in programas), key=lambda x: (-x[1], x[0].nome))


@app.get("/milhas", response_class=HTMLResponse)
def tela_milhas(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver_precos)):
    sem_valor = sum(1 for p in promocoes_vigentes(db) if milhas_sem_valor(p))
    return render(request, "milhas.html", usuario, programas=_programas_com_uso(db), sem_valor=sem_valor)


@app.post("/milhas")
async def salvar_milhas(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    form = await request.form()
    campos = ("b2c", "b2b", "b2f", "taxa", "cpfs", "janela")
    alterados = []
    for programa in db.scalars(select(ProgramaMilhas)).all():
        if f"b2c_{programa.id}" not in form:
            continue
        antes = (programa.preco_b2c, programa.preco_b2b, programa.preco_b2f, programa.taxa_fixa,
                 programa.limite_cpfs, programa.janela_cpfs)
        # os campos da linha de cada programa terminam com o id dele (ex.: b2c_3)
        _aplicar_precos(programa, {campo: form.get(f"{campo}_{programa.id}") for campo in campos})
        depois = (programa.preco_b2c, programa.preco_b2b, programa.preco_b2f, programa.taxa_fixa,
                  programa.limite_cpfs, programa.janela_cpfs)
        if antes != depois:
            alterados.append(programa.nome)
    novo = (form.get("novo_nome") or "").strip()
    if novo:
        if db.scalar(select(ProgramaMilhas).where(func.lower(ProgramaMilhas.nome) == novo.lower())):
            avisar(request, f"O programa {novo} já existe na lista.", "erro")
        else:
            programa = ProgramaMilhas(nome=novo, personalizado=True)
            _aplicar_precos(programa, {campo: form.get(f"novo_{campo}") for campo in campos})
            db.add(programa)
            alterados.append(novo)
    if alterados:
        registrar(db, usuario, "precos_alterados", None, ", ".join(alterados), ip_de(request))
    db.commit()
    limpar_cache_programas()
    r = recalcular_matches(db)
    texto = "Preços salvos. As promoções foram recalculadas."
    if r["criados"] or r["removidos"]:
        texto += f" Fila de envio: {r['criados']} cliente(s) incluído(s), {r['removidos']} retirado(s) por ficar acima do orçamento."
    avisar(request, texto)
    return voltar("/milhas")


def _aplicar_precos(programa: ProgramaMilhas, linha: dict) -> None:
    """Preços (R$ o milheiro) e limite de CPFs de um programa a partir dos campos do formulário."""
    b2c = ler_numero(linha.get("b2c"))
    if b2c is not None and 0 < b2c < 1:  # digitou o valor por milha (ex.: 0,027): vira o milheiro (27)
        b2c = round(b2c * 1000, 4)
    programa.preco_b2c = b2c
    programa.valor_milha = round(b2c / 1000, 6) if b2c else None  # usado na conversão dos alertas
    programa.preco_b2b = ler_numero(linha.get("b2b"))
    programa.preco_b2f = ler_numero(linha.get("b2f"))
    programa.taxa_fixa = ler_numero(linha.get("taxa"))
    limite = ler_numero(linha.get("cpfs"))
    programa.limite_cpfs = int(limite) if limite and limite > 0 else None
    programa.janela_cpfs = "12m" if linha.get("janela") == "12m" else "ano"


@app.post("/milhas/{programa_id}/excluir")
def excluir_programa(programa_id: int, request: Request, db: Session = Depends(get_db),
                     usuario: Usuario = Depends(pode_configurar)):
    programa = db.get(ProgramaMilhas, programa_id)
    if programa and programa.personalizado:
        db.delete(programa)
        db.commit()
        limpar_cache_programas()
        avisar(request, f"Programa {programa.nome} removido.")
    return voltar("/milhas")


@app.post("/configuracoes/mensagem")
def salvar_modelo(request: Request, texto: str = Form(...), db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    modelo = template_padrao(db)
    modelo.texto = texto.replace("\r\n", "\n")
    db.commit()
    avisar(request, "Modelo de mensagem salvo.")
    return voltar("/configuracoes")


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
        avisar(request, "Senha alterada.")
    return voltar("/configuracoes")


# ---------------------------------------------------------------- API para integrações


class PromocaoEntrada(BaseModel):
    texto: str
    fonte_detalhe: str | None = None
    link_fonte: str | None = None


@app.post("/api/promocoes")
def api_promocao(entrada: PromocaoEntrada, x_api_key: str = Header(""), db: Session = Depends(get_db)):
    """Recebe o texto de uma promoção de outro sistema (ex.: um encaminhador de mensagens)."""
    if not settings.api_key or x_api_key != settings.api_key:
        raise HTTPException(401, "API_KEY inválida ou não configurada.")
    r = registrar_promocao(db, texto=entrada.texto, fonte="api", fonte_detalhe=entrada.fonte_detalhe,
                           link_fonte=entrada.link_fonte)
    return {"resultado": r.motivo, "promocao_id": r.promocao.id if r.promocao else None, "clientes": r.matches}


@app.get("/saude")
def saude():
    return {"ok": True}
