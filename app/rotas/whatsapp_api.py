"""WhatsApp automático (API oficial da Meta): configuração, envio pela fila, webhook e respostas dos clientes."""
import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import whatsapp_api as api
from ..config import settings
from ..database import get_db
from ..models import Cliente, ConfirmacaoCadastro, EnvioWhatsApp, Match, RespostaWhatsApp, Usuario
from ..mvo.contas import registrar
from ..servicos import normalizar_telefone
from ..web import avisar, endereco_site, exigir, ip_de, render, voltar

log = logging.getLogger(__name__)
rotas = APIRouter()
pode_alertas = exigir("alertas")
pode_configurar = exigir("configurar")


# ---------------------------------------------------------------- configuração


@rotas.get("/configuracoes/whatsapp", response_class=HTMLResponse)
def tela_api(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    conf = api.configuracao(db)
    ultimos = db.scalars(select(EnvioWhatsApp).order_by(EnvioWhatsApp.id.desc()).limit(15)).all()
    return render(request, "whatsapp_api.html", usuario, conf=conf, tem_token=bool(conf["token"]),
                  tem_segredo=bool(conf["app_secret"]), configurada=api.configurada(db), variaveis=api.VARIAVEIS,
                  enviados_hoje=api.enviados_hoje(db), ultimos=ultimos, site=endereco_site(request),
                  webhook=endereco_site(request) + "/webhooks/whatsapp", site_falar=endereco_site(request) + api.CAMINHO_FALAR, pagina="configuracoes",
                  mes=_contagem_mes(db), agendador_ligado=settings.coleta_automatica, site_definido=bool(settings.site_url),
                  intervalo=settings.intervalo_whatsapp_api_min)


def _contagem_mes(db: Session) -> int:
    inicio = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(select(func.count(EnvioWhatsApp.id)).where(
        EnvioWhatsApp.criado_em >= inicio, EnvioWhatsApp.status != "falhou",
        EnvioWhatsApp.tipo.in_(("promocao", "confirmacao")))) or 0


@rotas.post("/configuracoes/whatsapp")
def salvar_api(request: Request, token: str = Form(""), phone_id: str = Form(""), waba_id: str = Form(""),
               verify_token: str = Form(""), app_secret: str = Form(""), modelo_promocao: str = Form(""),
               modelo_confirmacao: str = Form(""), idioma: str = Form("pt_BR"), variaveis_promocao: str = Form(""),
               variaveis_confirmacao: str = Form(""), automatico: str = Form(""), max_por_dia: str = Form("200"),
               max_por_cliente_dia: str = Form("3"), contato: str = Form(""), preco_promocao: str = Form(""),
               preco_confirmacao: str = Form(""), botao_atendimento: str = Form(""), imagem_cabecalho: str = Form(""),
               db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    dados = {
        "token": token.strip(), "phone_id": phone_id.strip(), "waba_id": waba_id.strip(),
        "verify_token": verify_token.strip(), "app_secret": app_secret.strip(),
        "modelo_promocao": modelo_promocao.strip(), "modelo_confirmacao": modelo_confirmacao.strip(),
        "idioma": idioma.strip() or "pt_BR", "variaveis_promocao": variaveis_promocao.strip(),
        "variaveis_confirmacao": variaveis_confirmacao.strip(), "automatico": "1" if automatico else "0",
        "max_por_dia": str(api._inteiro(max_por_dia, 200)), "max_por_cliente_dia": str(api._inteiro(max_por_cliente_dia, 3)),
        "contato": normalizar_telefone(contato) if contato.strip() else "",
        "preco_promocao": preco_promocao.strip() or api.PADROES["preco_promocao"],
        "preco_confirmacao": preco_confirmacao.strip() or api.PADROES["preco_confirmacao"],
        "botao_atendimento": "1" if botao_atendimento else "0",
        "imagem_cabecalho": "1" if imagem_cabecalho else "0",
    }
    desconhecidas = [v for v in api._variaveis(dados["variaveis_promocao"] + "," + dados["variaveis_confirmacao"])
                     if v not in api.VARIAVEIS]
    if desconhecidas:
        avisar(request, "Variável desconhecida: " + ", ".join(desconhecidas), "erro")
        return voltar("/configuracoes/whatsapp")
    api.salvar_configuracao(db, dados)
    registrar(db, usuario, "whatsapp_api", None, "envio automático " + ("ligado" if automatico else "desligado"), ip_de(request))
    db.commit()
    avisar(request, "Configuração do WhatsApp automático salva." + (" O envio automático está LIGADO." if automatico else ""))
    return voltar("/configuracoes/whatsapp")


@rotas.post("/configuracoes/whatsapp/apagar")
def apagar_api(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    api.apagar_segredos(db)
    registrar(db, usuario, "whatsapp_api", None, "token e segredo apagados; envio automático desligado", ip_de(request))
    db.commit()
    avisar(request, "Token e segredo do app apagados; o envio automático foi desligado. Cole um token novo quando quiser religar.")
    return voltar("/configuracoes/whatsapp")


@rotas.post("/configuracoes/whatsapp/teste")
def testar_api(request: Request, telefone: str = Form(...), db: Session = Depends(get_db),
               usuario: Usuario = Depends(pode_configurar)):
    r = api.enviar_teste(db, telefone)
    if r.ok and r.motivo == "numero_real":
        avisar(request, "Token e número conferidos: a Meta reconheceu o número da agência. (O modelo hello_world só sai do "
                        "número de teste; com um modelo seu aprovado, teste pela Fila de envio → “Pela API”.)")
    elif r.ok:
        avisar(request, "Mensagem de teste enviada (modelo hello_world). Confira no celular.")
    else:
        avisar(request, f"O teste falhou: {r.motivo}", "erro")
    return voltar("/configuracoes/whatsapp")


# ---------------------------------------------------------------- envio pela fila (botões)


def _tentar(envio, db: Session) -> api.ResultadoEnvio:
    """Um erro inesperado no envio vira motivo na tela (e fica no log), em vez de "erro 500"."""
    try:
        return envio()
    except Exception as erro:  # noqa: BLE001
        log.exception("Falha inesperada no envio pela API do WhatsApp")
        db.rollback()
        return api.ResultadoEnvio(False, f"erro interno ({type(erro).__name__}): {str(erro)[:200]}")


@rotas.post("/matches/{match_id}/enviar-api")
def enviar_match_api(match_id: int, request: Request, db: Session = Depends(get_db),
                     usuario: Usuario = Depends(pode_alertas)):
    m = db.get(Match, match_id)
    if not m:
        raise HTTPException(404)
    r = _tentar(lambda: api.enviar_promocao(db, m, endereco_site(request), usuario.nome), db)
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"ok": r.ok, "motivo": r.motivo, "envios": m.envios or 0}, status_code=200 if r.ok else 400)
    avisar(request, f"Promoção enviada pela API para {m.cliente.nome}." if r.ok else f"Não enviou: {r.motivo}",
           "ok" if r.ok else "erro")
    return voltar("/")


@rotas.post("/confirmacoes/{conf_id}/enviar-api")
def enviar_confirmacao_api(conf_id: int, request: Request, db: Session = Depends(get_db),
                           usuario: Usuario = Depends(pode_alertas)):
    c = db.get(ConfirmacaoCadastro, conf_id)
    if not c:
        raise HTTPException(404)
    r = _tentar(lambda: api.enviar_confirmacao(db, c, endereco_site(request), usuario.nome), db)
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"ok": r.ok, "motivo": r.motivo, "envios": c.envios or 0}, status_code=200 if r.ok else 400)
    avisar(request, "Confirmação enviada pela API." if r.ok else f"Não enviou: {r.motivo}", "ok" if r.ok else "erro")
    return voltar("/")


@rotas.post("/whatsapp-api/enviar-pendentes")
def enviar_pendentes_agora(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    if not api.automatico_ligado(db):
        avisar(request, "Ligue o envio automático em Configurações → WhatsApp automático.", "erro")
        return voltar("/")
    resumo = api.enviar_pendentes(db, endereco_site(request))
    avisar(request, f"Enviadas pela API: {resumo['promocoes']} promoções e {resumo['confirmacoes']} confirmações"
                    + (f"; {resumo['falhas']} falharam" if resumo["falhas"] else "")
                    + (f"; {resumo['pulados']} ficaram para depois" if resumo["pulados"] else "") + ".")
    return voltar("/")


# ---------------------------------------------------------------- webhook da Meta


@rotas.get("/webhooks/whatsapp")
def verificar_webhook(request: Request, db: Session = Depends(get_db)):
    """A Meta chama uma vez, ao salvar o webhook no painel: devolve o desafio se o token de verificação bater."""
    p = request.query_params
    conf = api.configuracao(db)
    if p.get("hub.mode") == "subscribe" and conf["verify_token"] and p.get("hub.verify_token") == conf["verify_token"]:
        return PlainTextResponse(p.get("hub.challenge") or "")
    raise HTTPException(403)


@rotas.post("/webhooks/whatsapp")
async def receber_webhook(request: Request, db: Session = Depends(get_db)):
    if not api.configurada(db):
        raise HTTPException(404)
    corpo = await request.body()
    if len(corpo) > 1_000_000:
        raise HTTPException(413)
    conf = api.configuracao(db)
    if not api.assinatura_valida(conf, corpo, request.headers.get("x-hub-signature-256")):
        raise HTTPException(403)
    try:
        dados = json.loads(corpo or b"{}")
    except ValueError:
        raise HTTPException(400)
    if not isinstance(dados, dict):
        raise HTTPException(400)
    try:
        api.tratar_webhook(db, dados, conf["phone_id"])
    except Exception:  # a Meta reenvia se não receber 200; um erro nosso não pode travar a fila dela
        log.exception("Falha ao tratar webhook do WhatsApp")
    return PlainTextResponse("ok")


# ---------------------------------------------------------------- respostas dos clientes


ROTULOS_ENVIO = {"promocao": "Promoção enviada", "confirmacao": "Confirmação de cadastro",
                 "reengajamento": "Mensagem de retorno", "teste": "Mensagem de teste"}


def _descrever_envio(e: EnvioWhatsApp, m: Match | None = None) -> str:
    """Texto do balão de uma mensagem que saiu pelo 0500 (modelo aprovado ou resposta livre da equipe)."""
    from ..servicos import descrever_trecho

    if e.tipo == "resposta":
        return e.texto or "Resposta da equipe"
    rotulo = ROTULOS_ENVIO.get(e.tipo, "Mensagem enviada")
    if m is not None:
        rotulo += f": {descrever_trecho(m.promocao)}"
    return rotulo + (f" · modelo {e.modelo}" if e.modelo else "")


@rotas.get("/whatsapp/respostas", response_class=HTMLResponse)
def respostas(request: Request, dias: int = 7, c: str = "", db: Session = Depends(get_db), usuario: Usuario = Depends(pode_alertas)):
    dias = dias if dias in (1, 7, 30) else 7
    desde = datetime.now() - timedelta(days=dias)
    itens = db.scalars(
        select(RespostaWhatsApp).where(RespostaWhatsApp.recebido_em >= desde)
        .order_by(RespostaWhatsApp.recebido_em.desc()).limit(300)
    ).all()
    clientes = {c.id: c for c in db.scalars(select(Cliente).where(Cliente.id.in_({r.cliente_id for r in itens if r.cliente_id})))} if itens else {}
    conversas: dict[str, dict] = {}
    for r in itens:
        conv = conversas.setdefault(r.telefone, {"telefone": r.telefone, "cliente": clientes.get(r.cliente_id), "nome": r.nome,
                                                 "mensagens": [], "nao_lidas": 0, "ultima": r.recebido_em})
        conv["mensagens"].append({"de": "cliente", "texto": r.texto, "quando": r.recebido_em, "lida": r.lida, "saiu": r.descadastrou})
        conv["nao_lidas"] += 0 if r.lida else 1
    # o que saiu pelo 0500 para esses telefones (promoção, confirmação, retorno, respostas da equipe): o contexto da conversa
    if conversas:
        enviados = db.scalars(select(EnvioWhatsApp).where(EnvioWhatsApp.telefone.in_(list(conversas)),
                                                          EnvioWhatsApp.criado_em >= desde - timedelta(days=7))
                              .order_by(EnvioWhatsApp.criado_em)).all()
        matches = {m.id: m for m in db.scalars(select(Match).where(Match.id.in_({e.match_id for e in enviados if e.match_id})))}
        for e in enviados:
            m = matches.get(e.match_id) if e.tipo == "promocao" else None
            conversas[e.telefone]["mensagens"].append({"de": "equipe", "texto": _descrever_envio(e, m), "quando": e.criado_em,
                                                       "status": e.status, "modelo": e.tipo != "resposta", "por": e.enviado_por})
    for conv in conversas.values():
        conv["mensagens"].sort(key=lambda m: m["quando"])
        conv["janela_aberta"] = datetime.now() - conv["ultima"] < timedelta(hours=24)
        conv["ultima_msg"] = conv["mensagens"][-1] if conv["mensagens"] else None
        conv["saiu"] = any(m.get("saiu") for m in conv["mensagens"])
    lista = sorted(conversas.values(), key=lambda x: x["ultima"], reverse=True)
    escolhida = normalizar_telefone(c) if c else ""
    if escolhida not in conversas:
        escolhida = ""
    return render(request, "whatsapp_respostas.html", usuario, conversas=lista, dias=dias, escolhida=escolhida,
                  configurada=api.configurada(db), hoje=datetime.now().date())


@rotas.post("/whatsapp/respostas/lidas")
def marcar_lidas(request: Request, telefone: str = Form(""), db: Session = Depends(get_db),
                 usuario: Usuario = Depends(pode_alertas)):
    consulta = select(RespostaWhatsApp).where(RespostaWhatsApp.lida.is_(False))
    if telefone:
        consulta = consulta.where(RespostaWhatsApp.telefone == normalizar_telefone(telefone))
    for r in db.scalars(consulta):
        r.lida = True
    db.commit()
    if request.headers.get("x-requested-with") == "fetch":
        return HTMLResponse("ok")
    return voltar(f"/whatsapp/respostas?c={normalizar_telefone(telefone)}" if telefone else "/whatsapp/respostas")


@rotas.post("/whatsapp/respostas/responder")
def responder(request: Request, telefone: str = Form(...), texto: str = Form(...), db: Session = Depends(get_db),
              usuario: Usuario = Depends(pode_alertas)):
    numero = normalizar_telefone(telefone)
    if not texto.strip():
        avisar(request, "Escreva a resposta.", "erro")
        return voltar(f"/whatsapp/respostas?c={numero}")
    r = api.enviar_texto(db, numero, texto.strip(), usuario.nome, cliente=api.cliente_pelo_telefone(db, numero))
    if r.ok:
        for resp in db.scalars(select(RespostaWhatsApp).where(RespostaWhatsApp.telefone == numero, RespostaWhatsApp.lida.is_(False))):
            resp.lida = True
        db.commit()
        avisar(request, "Resposta enviada.")
    else:
        avisar(request, f"Não enviou: {r.motivo}", "erro")
    return voltar(f"/whatsapp/respostas?c={numero}")
