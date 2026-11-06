"""Equipe e acessos: usuários, papéis (níveis de acesso) e registro de acessos."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import busca
from ..database import get_db
from ..exportar import Coluna, exportar, quer_planilha, sim_nao
from ..models import Usuario
from ..models_mvo import RegistroAcesso
from ..mvo.comissoes import descrever_regra
from ..mvo.contas import ACOES_REGISTRO, registrar
from ..permissoes import PAPEIS, PERMISSOES, nome_papel
from ..servicos import gerar_hash_senha
from ..web import avisar, checar_exportar, exigir, ip_de, render, voltar

rotas = APIRouter()
pode_usuarios = exigir("usuarios")


def _admins_ativos(db: Session, exceto: int | None = None) -> int:
    consulta = select(func.count(Usuario.id)).where(Usuario.papel == "admin", Usuario.ativo.is_(True))
    if exceto:
        consulta = consulta.where(Usuario.id != exceto)
    return db.scalar(consulta) or 0


@rotas.get("/equipe", response_class=HTMLResponse)
def tela_equipe(request: Request, formato: str = "", db: Session = Depends(get_db),
                usuario: Usuario = Depends(pode_usuarios)):
    usuarios = db.scalars(select(Usuario).order_by(Usuario.ativo.desc(), Usuario.nome)).all()
    if quer_planilha(formato):
        checar_exportar(usuario)
        colunas = [
            Coluna("Nome", lambda u: u.nome), Coluna("Login", lambda u: u.login), Coluna("Nível", lambda u: nome_papel(u)),
            Coluna("Ativo", lambda u: sim_nao(u.ativo)), Coluna("Último acesso", lambda u: u.ultimo_acesso, "datahora"),
            Coluna("Comissão (% ou R$ por passagem)", lambda u: u.comissao_pct),
            Coluna("Regra da comissão", lambda u: descrever_regra(u.comissao_pct, u.comissao_base or "lucro") or "sem comissão"),
        ]
        return exportar(request, db, usuario, "Equipe", colunas, usuarios)
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


@rotas.post("/equipe/{uid}/excluir")
def excluir_usuario(uid: int, request: Request, db: Session = Depends(get_db),
                    usuario: Usuario = Depends(pode_usuarios)):
    alvo = db.get(Usuario, uid)
    if not alvo:
        raise HTTPException(404)
    if alvo.id == usuario.id:
        avisar(request, "Você não pode excluir o seu próprio acesso.", "erro")
        return voltar("/equipe")
    if alvo.papel == "admin" and alvo.ativo and not _admins_ativos(db, exceto=alvo.id):
        avisar(request, "O sistema precisa de pelo menos um Administrador ativo.", "erro")
        return voltar("/equipe")
    nome, login = alvo.nome, alvo.login
    db.delete(alvo)  # emissões e envios guardam o nome de quem fez; o registro de acessos também
    registrar(db, usuario, "exclusao", f"usuario:{uid}", f"Acesso de {nome} ({login})", ip_de(request))
    db.commit()
    avisar(request, f"Acesso de {nome} excluído. O que essa pessoa registrou continua com o nome dela.")
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
def registro_acessos(request: Request, quem: int = 0, acao: str = "", alvo: str = "", q: str = "", periodo: str = "",
                     limite: int = 300, formato: str = "", db: Session = Depends(get_db),
                     usuario: Usuario = Depends(pode_usuarios)):
    consulta = select(RegistroAcesso).order_by(RegistroAcesso.criado_em.desc(), RegistroAcesso.id.desc())
    if quem:
        consulta = consulta.where(RegistroAcesso.usuario_id == quem)
    if acao:
        consulta = consulta.where(RegistroAcesso.acao == acao)
    if alvo.strip():
        consulta = consulta.where(RegistroAcesso.alvo == alvo.strip())
    consulta = busca.aplicar(consulta, q, [RegistroAcesso.detalhe, RegistroAcesso.alvo, RegistroAcesso.usuario_nome,
                                           RegistroAcesso.ip])
    if busca.desde(periodo):
        consulta = consulta.where(RegistroAcesso.criado_em >= busca.desde(periodo))
    if quer_planilha(formato):
        checar_exportar(usuario)
        colunas = [Coluna("Data e hora", lambda r: r.criado_em, "datahora"), Coluna("Usuário", lambda r: r.usuario_nome),
                   Coluna("Ação", lambda r: ACOES_REGISTRO.get(r.acao, r.acao)), Coluna("Alvo", lambda r: r.alvo),
                   Coluna("Detalhe", lambda r: r.detalhe), Coluna("IP", lambda r: r.ip)]
        return exportar(request, db, usuario, "Registro de acessos", colunas, db.scalars(consulta.limit(100_000)).all())
    limite = max(50, min(limite, 2000))
    registros = db.scalars(consulta.limit(limite)).all()
    usuarios = db.scalars(select(Usuario).order_by(Usuario.nome)).all()
    return render(request, "mvo/registro.html", usuario, registros=registros, usuarios=usuarios, quem=quem,
                  acao=acao, alvo=alvo.strip(), limite=limite, acoes=ACOES_REGISTRO, q=q, periodo=periodo)
