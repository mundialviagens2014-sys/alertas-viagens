"""Bilhetes: pedir a consulta da reserva, acompanhar o robô, conferir e baixar o PDF; e a API do robô."""
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import Usuario
from ..models_mvo import COMPANHIAS_BILHETE, ConsultaReserva
from ..mvo import bilhetes
from ..mvo.bilhete_pdf import gerar_pdf
from ..mvo.contas import registrar
from ..servicos import whatsapp_agencia
from ..web import avisar, exigir, ip_de, render, voltar

rotas = APIRouter()
pode_ver = exigir("milhas_ver")
pode_operar = exigir("milhas_ver", "milhas_operar")
pode_configurar = exigir("configurar")


def _consulta(db: Session, cid: int) -> ConsultaReserva:
    c = db.get(ConsultaReserva, cid)
    if not c:
        raise HTTPException(404)
    return c


def _contato(db: Session) -> str | None:
    numero = whatsapp_agencia(db)
    if len(numero) >= 12 and numero.startswith("55"):
        n = numero[2:]
        return f"WhatsApp ({n[:2]}) {n[2:-4]}-{n[-4:]}"
    return f"WhatsApp +{numero}" if numero else None


def _arquivo(c: ConsultaReserva) -> str:
    return f"Bilhete_{c.localizador}.pdf"


# ---------------------------------------------------------------- telas


@rotas.get("/bilhetes", response_class=HTMLResponse)
def tela(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    consultas = db.scalars(select(ConsultaReserva).options(selectinload(ConsultaReserva.emissao))
                           .order_by(ConsultaReserva.criado_em.desc()).limit(60)).all()
    q = request.query_params
    return render(request, "mvo/bilhetes.html", usuario, consultas=consultas, companhias=COMPANHIAS_BILHETE,
                  robo=bilhetes.situacao_robo(db),
                  previa={k: q.get(k, "") for k in ("localizador", "companhia", "sobrenome", "origem")})


@rotas.post("/bilhetes")
def pedir(request: Request, localizador: str = Form(""), companhia: str = Form(""), sobrenome: str = Form(""),
          origem: str = Form(""), db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    try:
        c = bilhetes.pedir(db, localizador, companhia, sobrenome, origem, usuario)
    except bilhetes.PedidoInvalido as e:
        avisar(request, str(e), "erro")
        return voltar("/bilhetes")
    registrar(db, usuario, "bilhete_pedido", f"consulta:{c.id}", f"{c.companhia} {c.localizador}", ip_de(request))
    db.commit()
    return voltar(f"/bilhetes/{c.id}")


@rotas.get("/bilhetes/{cid}", response_class=HTMLResponse)
def ver(cid: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    c = _consulta(db, cid)
    return render(request, "mvo/bilhete.html", usuario, c=c, r=bilhetes.reserva(c), robo=bilhetes.situacao_robo(db),
                  pagina_oficial=bilhetes.PAGINA_RESERVA.get(c.companhia), arquivo=_arquivo(c))


@rotas.get("/bilhetes/{cid}/estado")
def estado(cid: int, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    """A tela pergunta enquanto o robô trabalha; quando muda, ela recarrega."""
    c = _consulta(db, cid)
    robo = bilhetes.situacao_robo(db)
    return {"status": c.status, "nome": c.nome_status, "robo_online": robo["online"]}


@rotas.post("/bilhetes/{cid}/repetir")
def repetir(cid: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    bilhetes.repetir(db, _consulta(db, cid))
    avisar(request, "Pedido voltou para a fila do robô.")
    return voltar(f"/bilhetes/{cid}")


async def _ler_arquivo(request: Request, db: Session, arquivo: UploadFile | None, texto: str) -> dict:
    """Print/PDF/texto da reserva → leitura da IA. Levanta PedidoInvalido com a mensagem para a equipe."""
    from .. import ia
    from ..leitura import TAMANHO_MAX_IMAGEM
    from ..servicos import preparar_imagem

    conteudo = await arquivo.read() if arquivo is not None and arquivo.filename else b""
    mime = (arquivo.content_type or "") if arquivo is not None else ""
    if not conteudo and not texto.strip():
        raise bilhetes.PedidoInvalido("Escolha o print ou o PDF da reserva (ou cole o texto do e-mail).")
    if conteudo and (len(conteudo) > TAMANHO_MAX_IMAGEM or mime not in bilhetes.TIPOS_PRINT):
        raise bilhetes.PedidoInvalido("Envie uma imagem (JPEG, PNG, WebP) ou um PDF de até 8 MB.")
    if not ia.configurada():
        raise bilhetes.PedidoInvalido("A leitura do print precisa da chave GEMINI_API_KEY nas variáveis do Render.")
    if not ia.cota_disponivel(db):
        raise bilhetes.PedidoInvalido("A cota gratuita de leituras por IA acabou por hoje. Tente amanhã.")
    if conteudo and mime.startswith("image/"):
        conteudo, mime = preparar_imagem(conteudo, mime)
    try:
        lido = bilhetes.ler_print(conteudo or None, mime, texto.strip() or None)
        ia.registrar_uso(db)
    except ia.CotaExcedida:
        raise bilhetes.PedidoInvalido("A cota gratuita de leituras por IA acabou por hoje. Tente amanhã.") from None
    except Exception as erro:  # rede, resposta estranha da IA
        raise bilhetes.PedidoInvalido(f"Não consegui ler agora ({type(erro).__name__}). Tente de novo.") from None
    if not lido:
        raise bilhetes.PedidoInvalido("O arquivo não parece uma reserva de voo.")
    return lido


@rotas.post("/bilhetes/print")
async def criar_pelo_print(request: Request, arquivo: UploadFile | None = File(None), texto: str = Form(""),
                           companhia: str = Form(""), db: Session = Depends(get_db),
                           usuario: Usuario = Depends(pode_operar)):
    """Primeira tela: só o print da reserva → bilhete pronto, sem passar pelo robô."""
    try:
        lido = await _ler_arquivo(request, db, arquivo, texto)
        c = bilhetes.criar_pelo_print(db, lido, companhia, usuario)
    except bilhetes.PedidoInvalido as e:
        avisar(request, str(e), "erro")
        return voltar("/bilhetes")
    registrar(db, usuario, "bilhete_print", f"consulta:{c.id}", f"{c.companhia} {c.localizador}", ip_de(request))
    db.commit()
    avisar(request, "Bilhete gerado pelo print. Confira voos e horários antes de enviar ao cliente.")
    return voltar(f"/bilhetes/{c.id}")


@rotas.post("/bilhetes/{cid}/ler-print")
async def ler_print(cid: int, request: Request, arquivo: UploadFile | None = File(None), texto: str = Form(""),
                    db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    """Print da reserva na tela do bilhete, para quando o site da companhia barra o robô (LATAM e GOL pedem captcha)."""
    c = _consulta(db, cid)
    try:
        bilhetes.aplicar_print(db, c, await _ler_arquivo(request, db, arquivo, texto))
    except bilhetes.PedidoInvalido as e:
        avisar(request, str(e), "erro")
        return voltar(f"/bilhetes/{cid}")
    registrar(db, usuario, "bilhete_print", f"consulta:{c.id}", f"{c.companhia} {c.localizador}", ip_de(request))
    db.commit()
    avisar(request, "Reserva lida do print. Confira voos e horários antes de enviar ao cliente.")
    return voltar(f"/bilhetes/{cid}")


@rotas.post("/bilhetes/{cid}/excluir")
def excluir(cid: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_operar)):
    c = _consulta(db, cid)
    registrar(db, usuario, "bilhete_excluido", f"consulta:{c.id}", f"{c.companhia} {c.localizador}", ip_de(request))
    db.delete(c)
    db.commit()
    avisar(request, "Consulta excluída.")
    return voltar("/bilhetes")


@rotas.get("/bilhetes/{cid}/pdf")
def pdf(cid: int, request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_ver)):
    c = _consulta(db, cid)
    r = bilhetes.reserva(c)
    if c.status != "pronta" or not r:
        raise HTTPException(409, "A reserva ainda não foi lida.")
    conteudo = gerar_pdf(c.localizador, c.companhia, r, contato=_contato(db))
    registrar(db, usuario, "bilhete_baixado", f"consulta:{c.id}", f"{c.companhia} {c.localizador}", ip_de(request))
    db.commit()
    modo = "inline" if request.query_params.get("ver") else "attachment"
    return Response(conteudo, media_type="application/pdf",
                    headers={"Content-Disposition": f'{modo}; filename="{_arquivo(c)}"', "Cache-Control": "no-store"})


# ---------------------------------------------------------------- Configurações → Robô de reservas


@rotas.post("/configuracoes/robo")
def configurar_robo(request: Request, acao: str = Form(...), db: Session = Depends(get_db),
                    usuario: Usuario = Depends(pode_configurar)):
    if acao == "gerar":
        token = bilhetes.gerar_token(db)
        registrar(db, usuario, "configuracao", None, "Robô de reservas: token novo", ip_de(request))
        db.commit()
        # aparece uma vez só, nesta resposta
        robo = bilhetes.situacao_robo(db)
        return render(request, "mvo/robo_token.html", usuario, token=token, robo=robo)
    if acao == "desligar":
        bilhetes.desligar_token(db)
        registrar(db, usuario, "configuracao", None, "Robô de reservas: token apagado", ip_de(request))
        db.commit()
        avisar(request, "Token apagado: o robô para de receber pedidos até você gerar outro.")
    return voltar("/configuracoes#robo")


@rotas.get("/configuracoes/robo.zip")
def baixar_robo(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_configurar)):
    """A pasta do robô pronta para copiar no computador da agência (sem token, perfil ou capturas)."""
    pasta = Path(__file__).resolve().parent.parent.parent / "robo"
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for arquivo in sorted(pasta.iterdir()):
            if arquivo.is_file() and arquivo.suffix in (".py", ".bat", ".txt", ".md"):
                z.write(arquivo, f"robo-mvo/{arquivo.name}")
    registrar(db, usuario, "configuracao", None, "Baixou o robô de reservas", ip_de(request))
    db.commit()
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="robo-mvo.zip"'})


# ---------------------------------------------------------------- API do robô (token, sem login)


def _robo(request: Request, db: Session = Depends(get_db)) -> Session:
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    if not bilhetes.token_valido(db, token):
        raise HTTPException(401, "Token do robô inválido.")
    bilhetes.marcar_visto(db, request.headers.get("x-robo-maquina"), request.headers.get("x-robo-versao"))
    db.commit()
    return db


@rotas.post("/api/robo/proximo")
def api_proximo(db: Session = Depends(_robo)):
    c = bilhetes.proximo(db)
    if not c:
        return Response(status_code=204)
    return bilhetes.pedido_para_robo(c)


@rotas.post("/api/robo/consultas/{cid}")
async def api_resultado(cid: int, request: Request, db: Session = Depends(_robo)):
    c = db.get(ConsultaReserva, cid)
    if not c:
        raise HTTPException(404)
    try:
        corpo = await request.json()
    except ValueError:
        raise HTTPException(400, "Mande JSON.") from None
    if not isinstance(corpo, dict):
        raise HTTPException(400, "Mande um objeto JSON.")
    try:
        bilhetes.registrar_resultado(db, c, corpo)
    except bilhetes.PedidoInvalido as e:
        return JSONResponse({"ok": False, "erro": str(e)}, status_code=422)
    return {"ok": True, "status": c.status}


@rotas.post("/api/robo/fila")
def api_fila(db: Session = Depends(_robo)):
    """O GitHub pergunta antes de ligar o Chrome: se não há pedido, a rodada termina em segundos."""
    return {"pendentes": bilhetes.pendentes(db)}


@rotas.post("/api/robo/ping")
def api_ping(db: Session = Depends(_robo)):
    return {"ok": True, "agora": datetime.now().isoformat(timespec="seconds")}

