"""Esqueci minha senha: link de redefinição por e-mail, e a configuração do envio de e-mail.

Cuidados:
- a resposta é sempre a mesma, exista ou não o login/e-mail (quem tenta não descobre quem tem acesso);
- o link vale 1 hora e uma vez só; o banco guarda só o hash do código;
- o endereço do link vem do SITE_URL (não do pedido), para ninguém forjar um link apontando para outro site;
- limite de pedidos por endereço (IP) e por login, para ninguém encher a caixa de e-mail de alguém.
"""
import hashlib
import secrets
from datetime import datetime, timedelta
from html import escape

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from .. import email_envio
from ..config import settings
from ..database import get_db
from ..limite import Limitador
from ..models import RedefinicaoSenha, Usuario
from ..mvo.contas import registrar
from ..servicos import gerar_hash_senha
from ..web import avisar, chave_de_ip, exigir, ip_de, render, usuario_atual, voltar

rotas = APIRouter()
pode_configurar = exigir("configurar")

VALIDADE = timedelta(hours=1)
MINIMO_SENHA = 8
pedidos_ip = Limitador(maximo=5, janela=15 * 60)
pedidos_conta = Limitador(maximo=3, janela=60 * 60)
codigos_errados = Limitador(maximo=20, janela=15 * 60)

RESPOSTA_PADRAO = ("Se esse login ou e-mail tiver acesso ao MVO com e-mail cadastrado, enviamos um link para criar "
                   "uma senha nova. Ele vale por 1 hora. Confira também a caixa de spam.")


def _hash(codigo: str) -> str:
    return hashlib.sha256(codigo.encode()).hexdigest()


def _endereco_site(request: Request) -> str:
    return settings.site_url or str(request.base_url).rstrip("/")


def _pedido_valido(db: Session, codigo: str) -> RedefinicaoSenha | None:
    if not codigo or len(codigo) > 100:
        return None
    pedido = db.scalar(select(RedefinicaoSenha).where(RedefinicaoSenha.codigo_hash == _hash(codigo)))
    if not pedido or pedido.usado_em or pedido.expira_em < datetime.now():
        return None
    usuario = db.get(Usuario, pedido.usuario_id)
    return pedido if usuario and usuario.ativo else None


def _email_do_link(usuario: Usuario, link: str) -> tuple[str, str, str]:
    assunto = "MVO · Criar uma senha nova"
    nome = escape(usuario.nome.split()[0] if usuario.nome else "")
    texto = (f"Olá, {usuario.nome}!\n\nRecebemos um pedido para criar uma senha nova no MVO para o login "
             f"\"{usuario.login}\".\n\nPara criar a senha, abra o link abaixo (vale por 1 hora e só uma vez):\n{link}\n\n"
             "Se não foi você, ignore este e-mail: a sua senha atual continua valendo.\n\nMVO · "
             f"{settings.agencia_nome}")
    html = f"""<!doctype html><html lang="pt-BR"><body style="margin:0;padding:24px;background:#f2f5f9;font-family:Arial,Helvetica,sans-serif;color:#13234a">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="480" cellpadding="0" cellspacing="0" style="max-width:480px;width:100%;background:#ffffff;border-radius:14px;border:1px solid #e0e6ef">
<tr><td style="background:#0b1f4b;border-radius:14px 14px 0 0;padding:18px 24px;color:#ffffff;font-size:18px;font-weight:bold">MVO <span style="color:#ff914d">·</span> <span style="font-weight:normal;color:#afc1e3;font-size:14px">{escape(settings.agencia_nome)}</span></td></tr>
<tr><td style="padding:24px">
<p style="margin:0 0 12px;font-size:17px;font-weight:bold">Olá, {nome}!</p>
<p style="margin:0 0 16px;font-size:15px;line-height:1.5">Recebemos um pedido para criar uma senha nova no MVO para o login <b>{escape(usuario.login)}</b>.</p>
<p style="margin:0 0 20px"><a href="{escape(link)}" style="display:inline-block;background:#1b468f;color:#ffffff;text-decoration:none;font-weight:bold;font-size:15px;padding:12px 22px;border-radius:9px">Criar senha nova</a></p>
<p style="margin:0 0 16px;font-size:13px;line-height:1.5;color:#5a6782">O link vale por <b>1 hora</b> e funciona uma vez só. Se o botão não abrir, copie este endereço no navegador:<br><span style="word-break:break-all;color:#1b468f">{escape(link)}</span></p>
<p style="margin:0;font-size:13px;line-height:1.5;color:#5a6782">Se não foi você, ignore este e-mail: a sua senha atual continua valendo.</p>
</td></tr></table></td></tr></table></body></html>"""
    return assunto, html, texto


# ---------------------------------------------------------------- esqueci minha senha


@rotas.get("/esqueci-senha", response_class=HTMLResponse)
def tela_esqueci(request: Request):
    return render(request, "esqueci_senha.html")


@rotas.post("/esqueci-senha")
def pedir_link(request: Request, login: str = Form(...), db: Session = Depends(get_db)):
    digitado = login.strip()[:160]
    if not digitado:
        avisar(request, "Digite o seu login ou e-mail.", "erro")
        return voltar("/esqueci-senha")
    if not pedidos_ip.tentar(chave_de_ip(request)) or not pedidos_conta.tentar(digitado.lower()):
        avisar(request, "Muitos pedidos seguidos. Aguarde um pouco e tente de novo.", "erro")
        return voltar("/esqueci-senha")
    usuario = db.scalar(select(Usuario).where(or_(func.lower(Usuario.login) == digitado.lower(),
                                                  func.lower(Usuario.email) == digitado.lower())).limit(1))
    if usuario and usuario.ativo and usuario.email:
        # um link por vez: os anteriores deixam de valer
        db.execute(update(RedefinicaoSenha).where(RedefinicaoSenha.usuario_id == usuario.id,
                                                  RedefinicaoSenha.usado_em.is_(None)).values(usado_em=datetime.now()))
        codigo = secrets.token_urlsafe(32)
        db.add(RedefinicaoSenha(usuario_id=usuario.id, codigo_hash=_hash(codigo), expira_em=datetime.now() + VALIDADE))
        assunto, html, texto = _email_do_link(usuario, f"{_endereco_site(request)}/redefinir-senha/{codigo}")
        try:
            email_envio.enviar(db, usuario.email, usuario.nome, assunto, html, texto)
            registrar(db, usuario, "senha_link_enviado", f"usuario:{usuario.id}", None, ip_de(request))
        except email_envio.EmailNaoEnviado as e:
            registrar(db, usuario, "senha_link_falhou", f"usuario:{usuario.id}", str(e)[:250], ip_de(request))
        db.commit()
    avisar(request, RESPOSTA_PADRAO)
    return voltar("/esqueci-senha?enviado=1")


# ---------------------------------------------------------------- criar a senha nova pelo link


@rotas.get("/redefinir-senha/{codigo}", response_class=HTMLResponse)
def tela_redefinir(codigo: str, request: Request, db: Session = Depends(get_db)):
    if not codigos_errados.excedeu(chave_de_ip(request)) and _pedido_valido(db, codigo):
        return render(request, "redefinir_senha.html", codigo=codigo, minimo=MINIMO_SENHA)
    codigos_errados.tentar(chave_de_ip(request))
    return render(request, "redefinir_senha.html", codigo=None, minimo=MINIMO_SENHA)


@rotas.post("/redefinir-senha/{codigo}")
def redefinir(codigo: str, request: Request, nova: str = Form(...), confirmar: str = Form(...),
              db: Session = Depends(get_db)):
    pedido = None if codigos_errados.excedeu(chave_de_ip(request)) else _pedido_valido(db, codigo)
    if not pedido:
        codigos_errados.tentar(chave_de_ip(request))
        return render(request, "redefinir_senha.html", codigo=None, minimo=MINIMO_SENHA)
    usuario = db.get(Usuario, pedido.usuario_id)
    erro = None
    if len(nova) < MINIMO_SENHA:
        erro = f"A senha precisa ter pelo menos {MINIMO_SENHA} caracteres."
    elif nova != confirmar:
        erro = "As duas senhas não são iguais."
    elif nova.lower() in {usuario.login.lower(), "12345678", "123456789", "senha123", "mundial123", "password"}:
        erro = "Essa senha é fácil de adivinhar. Use letras e números."
    if erro:
        avisar(request, erro, "erro")
        return voltar(f"/redefinir-senha/{codigo}")
    usuario.senha_hash = gerar_hash_senha(nova)
    pedido.usado_em = datetime.now()
    registrar(db, usuario, "senha_redefinida_email", f"usuario:{usuario.id}", usuario.login, ip_de(request))
    db.commit()
    avisar(request, "Senha nova criada. Entre com ela.")
    return voltar("/login")


# ---------------------------------------------------------------- e-mail de cada pessoa


def email_valido(texto: str) -> str | None:
    """E-mail limpo, "" para apagar, ou None se não parece e-mail."""
    texto = (texto or "").strip().lower()[:160]
    if not texto:
        return ""
    if " " in texto or texto.count("@") != 1 or "." not in texto.split("@")[1] or texto.startswith("@"):
        return None
    return texto


@rotas.post("/configuracoes/meu-email")
def salvar_meu_email(request: Request, email: str = Form(""), db: Session = Depends(get_db),
                     usuario: Usuario = Depends(usuario_atual)):
    limpo = email_valido(email)
    if limpo is None:
        avisar(request, "Esse e-mail não parece certo.", "erro")
    else:
        usuario.email = limpo or None
        db.commit()
        avisar(request, "E-mail salvo." if limpo else "E-mail apagado.")
    return voltar("/configuracoes")


# ---------------------------------------------------------------- Configurações → E-mail


@rotas.post("/configuracoes/email")
def salvar_email(request: Request, chave: str = Form(""), remetente: str = Form(""), nome: str = Form(""),
                 teste: str = Form(""), apagar: str = Form(""), db: Session = Depends(get_db),
                 usuario: Usuario = Depends(pode_configurar)):
    if apagar:
        email_envio.apagar_chave(db)
        registrar(db, usuario, "configuracao", None, "E-mail: chave da Brevo apagada", ip_de(request))
        db.commit()
        avisar(request, "Chave da Brevo apagada. O \"Esqueci minha senha\" fica sem enviar até colocar outra.")
        return voltar("/configuracoes#email")
    if remetente.strip() and "@" not in remetente:
        avisar(request, "O remetente precisa ser um e-mail.", "erro")
        return voltar("/configuracoes#email")
    email_envio.salvar(db, chave, remetente, nome)
    registrar(db, usuario, "configuracao", None, "E-mail (Brevo) configurado", ip_de(request))
    db.commit()
    if teste.strip():
        try:
            email_envio.enviar(db, teste.strip(), usuario.nome, "MVO · Teste de e-mail",
                               "<p>Funcionou! O MVO já consegue mandar o link de <b>Esqueci minha senha</b>.</p>",
                               "Funcionou! O MVO já consegue mandar o link de Esqueci minha senha.")
            avisar(request, f"Salvo. E-mail de teste enviado para {teste.strip()}: confira a caixa de entrada (e o spam).")
        except email_envio.EmailNaoEnviado as e:
            avisar(request, f"Salvo, mas o teste não foi enviado: {e}", "erro")
    else:
        avisar(request, "E-mail: ajustes salvos.")
    return voltar("/configuracoes#email")
