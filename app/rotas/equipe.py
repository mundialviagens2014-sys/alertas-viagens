"""Equipe e acessos: usuários, papéis (níveis de acesso) e registro de acessos."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Usuario
from ..models_mvo import RegistroAcesso
from ..mvo.contas import ACOES_REGISTRO, registrar
from ..permissoes import PAPEIS, PERMISSOES, nome_papel
from ..servicos import gerar_hash_senha
from ..web import avisar, exigir, ip_de, render, voltar

rotas = APIRouter()
pode_usuarios = exigir("usuarios")


def _admins_ativos(db: Session, exceto: int | None = None) -> int:
    consulta = select(func.count(Usuario.id)).where(Usuario.papel == "admin", Usuario.ativo.is_(True))
    if exceto:
        consulta = consulta.where(Usuario.id != exceto)
    return db.scalar(consulta) or 0


@rotas.get("/equipe", response_class=HTMLResponse)
def tela_equipe(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(pode_usuarios)):
    usuarios = db.scalars(select(Usuario).order_by(Usuario.ativo.desc(), Usuario.nome)).all()
    return render(request, "mvo/equipe.html", usuario, usuarios=usuarios, papeis=PAPEIS, permissoes=PERMISSOES)


@rotas.post("/equipe/novo")
def criar_usuario(request: Request, nome: str = Form(...), login: str = Form(...), senha: str = Form(...),
                  papel: str = Form("atendente"), db: Session = Depends(get_db),
                  usuario: Usuario = Depends(pode_usuarios)):
    nome, login = nome.strip(), login.strip()
    if papel not in PAPEIS:
        avisar(request, "Escolha o nível de acesso.", "erro")
    elif len(senha) < 6:
        avisar(request, "A senha precisa ter pelo menos 6 caracteres.", "erro")
    elif not nome or not login:
        avisar(request, "Preencha nome e login.", "erro")
    elif db.scalar(select(Usuario).where(func.lower(Usuario.login) == login.lower())):
        avisar(request, "Esse login já existe.", "erro")
    else:
        novo = Usuario(nome=nome, login=login, senha_hash=gerar_hash_senha(senha), papel=papel)
        db.add(novo)
        db.flush()
        registrar(db, usuario, "usuario_criado", f"usuario:{novo.id}", f"{nome} ({login}) como {nome_papel(papel)}",
                  ip_de(request))
        db.commit()
        avisar(request, f"Acesso criado para {nome} como {nome_papel(papel)}.")
    return voltar("/equipe")


@rotas.post("/equipe/{uid}")
def alterar_usuario(uid: int, request: Request, nome: str = Form(...), papel: str = Form(...),
                    ativo: bool = Form(False), db: Session = Depends(get_db),
                    usuario: Usuario = Depends(pode_usuarios)):
    alvo = db.get(Usuario, uid)
    if not alvo:
        raise HTTPException(404)
    if papel not in PAPEIS:
        avisar(request, "Nível de acesso inválido.", "erro")
        return voltar("/equipe")
    deixa_de_ser_admin = alvo.papel == "admin" and (papel != "admin" or not ativo)
    if deixa_de_ser_admin and not _admins_ativos(db, exceto=alvo.id):
        avisar(request, "O sistema precisa de pelo menos um Administrador ativo.", "erro")
        return voltar("/equipe")
    mudancas = []
    if nome.strip() and nome.strip() != alvo.nome:
        mudancas.append(f"nome: {alvo.nome} → {nome.strip()}")
        alvo.nome = nome.strip()
    if papel != alvo.papel:
        mudancas.append(f"papel: {nome_papel(alvo)} → {nome_papel(papel)}")
        alvo.papel = papel
    if ativo != alvo.ativo:
        mudancas.append("liberado" if ativo else "bloqueado")
        alvo.ativo = ativo
    if mudancas:
        registrar(db, usuario, "usuario_alterado", f"usuario:{alvo.id}", f"{alvo.login}: " + "; ".join(mudancas),
                  ip_de(request))
        db.commit()
        avisar(request, f"Acesso de {alvo.nome} atualizado.")
    return voltar("/equipe")


@rotas.post("/equipe/{uid}/senha")
def redefinir_senha(uid: int, request: Request, senha: str = Form(...), db: Session = Depends(get_db),
                    usuario: Usuario = Depends(pode_usuarios)):
    alvo = db.get(Usuario, uid)
    if not alvo:
        raise HTTPException(404)
    if len(senha) < 6:
        avisar(request, "A senha precisa ter pelo menos 6 caracteres.", "erro")
    else:
        alvo.senha_hash = gerar_hash_senha(senha)
        registrar(db, usuario, "senha_redefinida", f"usuario:{alvo.id}", alvo.login, ip_de(request))
        db.commit()
        avisar(request, f"Nova senha definida para {alvo.nome}. Passe a senha para a pessoa por um canal seguro.")
    return voltar("/equipe")


@rotas.get("/equipe/registro", response_class=HTMLResponse)
def registro_acessos(request: Request, quem: int = 0, acao: str = "", alvo: str = "", limite: int = 300,
                     db: Session = Depends(get_db), usuario: Usuario = Depends(pode_usuarios)):
    consulta = select(RegistroAcesso).order_by(RegistroAcesso.criado_em.desc(), RegistroAcesso.id.desc())
    if quem:
        consulta = consulta.where(RegistroAcesso.usuario_id == quem)
    if acao:
        consulta = consulta.where(RegistroAcesso.acao == acao)
    if alvo.strip():
        consulta = consulta.where(RegistroAcesso.alvo == alvo.strip())
    limite = max(50, min(limite, 2000))
    registros = db.scalars(consulta.limit(limite)).all()
    usuarios = db.scalars(select(Usuario).order_by(Usuario.nome)).all()
    return render(request, "mvo/registro.html", usuario, registros=registros, usuarios=usuarios, quem=quem,
                  acao=acao, alvo=alvo.strip(), limite=limite, acoes=ACOES_REGISTRO)
