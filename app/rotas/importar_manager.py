"""Importar do Manager: envio da planilha, conferência linha a linha e confirmação."""
import re
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from ..config import BASE_DIR
from ..cripto import SenhaIlegivel, cifrar_bytes, decifrar_bytes
from ..database import get_db
from ..models import Usuario
from ..mvo import importar_manager as imp
from ..mvo.contas import formatar_pontos, registrar
from ..permissoes import pode
from ..web import avisar, exigir, ip_de, render, voltar

rotas = APIRouter()
pode_importar = exigir("configurar", "milhas_operar", "custos")

PASTA = BASE_DIR / "importacoes"
TAMANHO_MAX = 5 * 1024 * 1024
VALIDADE_SEGUNDOS = 2 * 60 * 60


def _caminho(token: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{16}\.(xlsx|xlsm|csv|txt)\.mvo", token or ""):
        return None
    caminho = PASTA / token
    return caminho if caminho.exists() else None


def _limpar_antigos() -> None:
    """Arquivos de conferências não confirmadas (podem ter senhas, mesmo cifrados) não ficam guardados."""
    if not PASTA.exists():
        return
    limite = time.time() - VALIDADE_SEGUNDOS
    for arquivo in PASTA.glob("*.mvo"):
        if arquivo.stat().st_mtime < limite:
            arquivo.unlink(missing_ok=True)


# de volta para a lista de cada tipo
VOLTAR = {"pessoas": ("/colaboradores", "Colaboradores"), "contas": ("/contas", "Programas"),
          "fornecedores": ("/fornecedores", "Fornecedores"), "agencias": ("/agencias", "Agências")}
LIMITE_PREVIA = 300  # linhas sem observação mostradas na conferência (a planilha inteira é importada)


@rotas.get("/importar-manager", response_class=HTMLResponse)
def tela(request: Request, tipo: str = "pessoas", usuario: Usuario = Depends(pode_importar)):
    tipo = tipo if tipo in imp.TIPOS else "pessoas"
    return render(request, "mvo/importar_manager.html", usuario, etapa="enviar", tipos=imp.TIPOS,
                  tipo=tipo, modelos=imp.MODELOS, voltar_para=VOLTAR[tipo])


def conferencia(request: Request, db: Session, usuario: Usuario, conteudo: bytes, nome_arquivo: str, tipo: str,
                perfil_padrao: str = "colaborador"):
    """Lê a planilha (reconhecendo o tipo), mostra a conferência e guarda o arquivo para a confirmação."""
    extensao = Path(nome_arquivo or "").suffix.lower().lstrip(".")
    try:
        tipo_lido, titulos, mapa, corpo, trocou = imp.ler_reconhecendo(conteudo, nome_arquivo or "", tipo)
    except Exception as erro:
        avisar(request, f"Não consegui ler a planilha: {erro}", "erro")
        return voltar(f"/importar-manager?tipo={tipo}")
    if trocou:
        avisar(request, f"Esta planilha é de “{imp.TIPOS[tipo_lido]}”: a importação foi feita nesse tipo "
                        f"(você tinha escolhido “{imp.TIPOS[tipo]}”).")
    tipo = tipo_lido
    faltando = [imp.NOMES_CAMPOS[c] for c in imp.OBRIGATORIAS[tipo] if c not in mapa]
    if tipo == "contas" and "nome" not in mapa and "cpf" not in mapa:
        faltando.append("Nome ou CPF do colaborador")
    if faltando:
        avisar(request, "Não encontrei a(s) coluna(s): " + ", ".join(faltando) + ". Colunas da planilha: "
               + ", ".join(t for t in titulos if t) + ".", "erro")
        return voltar(f"/importar-manager?tipo={tipo}")
    perfil_padrao = "cliente" if perfil_padrao == "cliente" else "colaborador"
    itens = imp.analisar(db, tipo, mapa, corpo, perfil_padrao)

    _limpar_antigos()
    PASTA.mkdir(exist_ok=True)
    token = f"{secrets.token_hex(8)}.{extensao}.mvo"
    (PASTA / token).write_bytes(cifrar_bytes(conteudo))
    contagem = {a: sum(1 for i in itens if i.acao == a) for a in ("criar", "atualizar", "ignorar", "erro")}
    destinos = {d: sum(1 for i in itens if i.valida and i.destino == d)
                for d in ("colaborador", "cliente", "conta", "fornecedor", "agencia")}
    pontos = sum(i.dados.get("saldo") or 0 for i in itens if i.valida and not i.dados.get("saldo_ignorado"))
    # planilha grande: a conferência mostra as linhas com observação e uma amostra das outras
    com_aviso = [i for i in itens if i.acao in ("erro", "ignorar") or i.mensagens]
    outras = [i for i in itens if not (i.acao in ("erro", "ignorar") or i.mensagens)]
    mostrar = sorted(com_aviso + outras[:max(0, LIMITE_PREVIA - len(com_aviso))], key=lambda i: i.numero)
    return render(request, "mvo/importar_manager.html", usuario, etapa="conferir", tipos=imp.TIPOS, tipo=tipo,
                  itens=mostrar, ocultos=len(itens) - len(mostrar), token=token, arquivo=nome_arquivo,
                  contagem=contagem, destinos=destinos, pontos=pontos, perfil_padrao=perfil_padrao,
                  voltar_para=VOLTAR[tipo],
                  colunas=[(imp.NOMES_CAMPOS.get(c, c), titulos[i]) for c, i in mapa.items()],
                  sem_coluna=[imp.NOMES_CAMPOS[c] for c, _ in imp.REGRAS[tipo] if c not in mapa],
                  com_senhas=pode(usuario, "senhas"))


@rotas.get("/importar-manager/modelo/{tipo}.xlsx")
def modelo(tipo: str, usuario: Usuario = Depends(pode_importar)):
    if tipo not in imp.TIPOS:
        raise HTTPException(404)
    return Response(imp.planilha_modelo(tipo),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="modelo-mvo-{tipo}.xlsx"'})


@rotas.post("/importar-manager", response_class=HTMLResponse)
async def conferir(request: Request, tipo: str = Form(...), perfil_padrao: str = Form("colaborador"),
                   arquivo: UploadFile = File(...), db: Session = Depends(get_db),
                   usuario: Usuario = Depends(pode_importar)):
    if tipo not in imp.TIPOS:
        avisar(request, "Escolha o tipo de planilha.", "erro")
        return voltar("/importar-manager")
    conteudo = await arquivo.read(TAMANHO_MAX + 1)
    if len(conteudo) > TAMANHO_MAX:
        avisar(request, "Arquivo grande demais (máximo 5 MB).", "erro")
        return voltar(f"/importar-manager?tipo={tipo}")
    return conferencia(request, db, usuario, conteudo, arquivo.filename or "", tipo, perfil_padrao)


@rotas.post("/importar-manager/confirmar")
def confirmar(request: Request, token: str = Form(...), tipo: str = Form(...),
              perfil_padrao: str = Form("colaborador"), db: Session = Depends(get_db),
              usuario: Usuario = Depends(pode_importar)):
    caminho = _caminho(token)
    if not caminho or tipo not in imp.TIPOS:
        avisar(request, "Essa conferência expirou. Envie a planilha de novo.", "erro")
        return voltar("/importar-manager")
    try:
        conteudo = decifrar_bytes(caminho.read_bytes())
    except SenhaIlegivel:
        caminho.unlink(missing_ok=True)
        avisar(request, "Essa conferência expirou. Envie a planilha de novo.", "erro")
        return voltar("/importar-manager")
    extensao = caminho.name.split(".")[1]
    tipo, _, mapa, corpo, _ = imp.ler_reconhecendo(conteudo, f"planilha.{extensao}", tipo)
    itens = imp.analisar(db, tipo, mapa, corpo, "cliente" if perfil_padrao == "cliente" else "colaborador")
    r = imp.aplicar(db, tipo, itens, usuario=usuario.nome, com_senhas=pode(usuario, "senhas"))
    partes = []
    if r["colaboradores"]:
        partes.append(f"{r['colaboradores']} colaborador(es)")
    if r["clientes"]:
        partes.append(f"{r['clientes']} cliente(s)")
    if r["contas"]:
        partes.append(f"{r['contas']} conta(s) com {formatar_pontos(r['pontos'])} pontos de saldo inicial")
    if r["fornecedores"]:
        partes.append(f"{r['fornecedores']} fornecedor(es)")
    if r["agencias"]:
        partes.append(f"{r['agencias']} agência(s)")
    resumo = ", ".join(partes) or "nada"
    registrar(db, usuario, "importacao_manager", None, f"{imp.TIPOS[tipo]}: {resumo} "
              f"({r['criados']} novos, {r['atualizados']} atualizados, {r['erros']} com erro)", ip_de(request))
    db.commit()
    caminho.unlink(missing_ok=True)
    texto = f"Importação concluída: {resumo} ({r['criados']} novos, {r['atualizados']} atualizados)."
    if r["erros"] or r["ignorados"]:
        texto += f" {r['erros'] + r['ignorados']} linha(s) ficaram de fora (erro ou repetidas)."
    avisar(request, texto)
    destino = {"pessoas": "/colaboradores", "contas": "/disponibilidade?zeradas=1", "fornecedores": "/fornecedores",
               "agencias": "/agencias"}[tipo]
    return voltar(destino)
