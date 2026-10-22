"""Peças comuns do site: telas (Jinja), usuário logado, permissões, avisos e leitura de formulários."""
import re
from datetime import date, datetime, timedelta

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import aeroportos
from .config import BASE_DIR, settings
from .database import SessionLocal, get_db
from .models import Cliente, Match, Promocao, Usuario
from .models_mvo import FORMAS_EMISSAO, TIPOS_LANCAMENTO
from .mvo.contas import formatar_cpf, formatar_pontos
from .permissoes import PAPEIS, PERMISSOES, nome_papel, papel_de, pode, pode_algum
from .servicos import (
    TIPOS_VIAGEM,
    detalhe_milhas,
    formatar_periodo,
    formatar_preco,
    formatar_telefone,
    formatar_valor,
    milhas_sem_valor,
)

APP_DIR = BASE_DIR / "app"
templates = Jinja2Templates(directory=APP_DIR / "templates")


def _preco_milheiro(valor) -> str:
    if valor is None:
        return ""
    return ("R$ " + f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))


def _dinheiro(valor) -> str:
    """R$ com centavos sempre (para valores financeiros)."""
    if valor is None:
        return "—"
    sinal = "-" if valor < 0 else ""
    return sinal + _preco_milheiro(abs(valor))


def _valor_campo(valor) -> str:
    """Valor para preencher um campo de formulário (números no formato brasileiro)."""
    if valor is None or isinstance(valor, bool):
        return ""
    if isinstance(valor, int):
        return formatar_pontos(valor)
    if isinstance(valor, float):
        if valor.is_integer():
            return formatar_pontos(int(valor))
        return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()[:10]
    return str(valor)


def _reais_redondo(valor) -> str:
    """R$ sem centavos, para totais (ex.: R$ 2.950; negativo: −R$ 120)."""
    if valor is None:
        return "—"
    return ("−" if valor < 0 else "") + formatar_preco(abs(round(valor)))


def _pontos_com_sinal(n: int | None) -> str:
    if n is None:
        return "—"
    return ("+" if n > 0 else "−" if n < 0 else "") + formatar_pontos(abs(n))


templates.env.filters.update(
    campo=_valor_campo,
    sinal=_pontos_com_sinal,
    reais=_reais_redondo,
    preco=formatar_preco,
    dinheiro=_dinheiro,
    milheiro=_preco_milheiro,
    pontos=formatar_pontos,
    cpf=formatar_cpf,
    valor=formatar_valor,
    detalhe_milhas=detalhe_milhas,
    milhas_sem_valor=milhas_sem_valor,
    lugar=aeroportos.nome,
    cidade=aeroportos.nome_curto,
    telefone=formatar_telefone,
    data=lambda d: d.strftime("%d/%m/%Y") if d else "",
    datahora=lambda d: d.strftime("%d/%m %H:%M") if d else "",
    datahora_ano=lambda d: d.strftime("%d/%m/%Y %H:%M") if d else "",
    iso=lambda d: d.isoformat() if d else "",
)


def contar_pendentes() -> int:
    """Clientes aguardando envio (aparece no menu)."""
    limite = datetime.now() - timedelta(days=settings.promo_validade_dias)
    with SessionLocal() as db:
        return db.scalar(
            select(func.count(Match.id)).join(Promocao).join(Cliente)
            .where(Match.status == "nova", Promocao.status == "ativa", Promocao.coletada_em >= limite,
                   Cliente.ativo.is_(True))
        ) or 0


def nomes_programas() -> list[str]:
    from .servicos import tabela_programas

    return sorted(tabela_programas())


def _versao_estaticos() -> str:
    """Muda sempre que um arquivo de estilo/script muda: o navegador não usa versão antiga guardada."""
    pasta = APP_DIR / "static"
    return str(int(max((f.stat().st_mtime for f in pasta.iterdir() if f.is_file()), default=0)))


templates.env.globals.update(
    versao=_versao_estaticos(),
    nomes_programas=nomes_programas,
    contar_pendentes=contar_pendentes,
    agencia=settings.agencia_nome,
    tipos_viagem=TIPOS_VIAGEM,
    tipos_lancamento=TIPOS_LANCAMENTO,
    formas_emissao=FORMAS_EMISSAO,
    papeis=PAPEIS,
    permissoes_texto=PERMISSOES,
    papel_de=papel_de,
    nome_papel=nome_papel,
    pode=pode,
    pode_algum=pode_algum,
    periodo=formatar_periodo,
    hoje=date.today,
    opcoes_aeroportos=aeroportos.opcoes,
    fontes={"telegram": "Telegram", "google_flights": "Google Flights", "manual": "Manual", "api": "API"},
)


# ---------------------------------------------------------------- usuário e permissões


class NaoLogado(Exception):
    pass


class SemPermissao(Exception):
    def __init__(self, permissoes: tuple[str, ...]):
        super().__init__("sem permissão")
        self.permissoes = permissoes


def usuario_atual(request: Request, db: Session = Depends(get_db)) -> Usuario:
    uid = request.session.get("usuario_id")
    usuario = db.get(Usuario, uid) if uid else None
    if not usuario or not usuario.ativo:
        raise NaoLogado()
    return usuario


def exigir(*permissoes: str):
    """Dependência: usuário logado com todas as permissões pedidas (ver app/permissoes.py)."""

    def _dependencia(request: Request, db: Session = Depends(get_db)) -> Usuario:
        usuario = usuario_atual(request, db)
        if not pode(usuario, *permissoes):
            raise SemPermissao(permissoes)
        return usuario

    return _dependencia


def exigir_uma(*permissoes: str):
    """Dependência: usuário logado com pelo menos uma das permissões pedidas."""

    def _dependencia(request: Request, db: Session = Depends(get_db)) -> Usuario:
        usuario = usuario_atual(request, db)
        if not pode_algum(usuario, *permissoes):
            raise SemPermissao(permissoes)
        return usuario

    return _dependencia


def avisar(request: Request, texto: str, tipo: str = "ok") -> None:
    request.session.setdefault("avisos", []).append({"texto": texto, "tipo": tipo})


# ---------------------------------------------------------------- os dois sistemas do MVO

# Cada página pertence a um sistema; as de ajustes (preços, equipe, configurações) servem aos dois e
# mostram o menu do último sistema usado.
MODULOS = {
    "alertas": {"nome": "Alertas", "inicio": "/", "permissao": "alertas"},
    "manager": {"nome": "Manager", "inicio": "/manager", "permissao": "milhas_ver"},
}
PAGINAS_DO_MODULO = {
    "alertas": {"fila", "interesses", "clientes", "cliente", "cliente_form", "importar", "formulario", "promocoes",
                "promocao_nova", "telegram", "historico"},
    "manager": {"disponibilidade", "emissoes", "emissao", "emissao_form", "colaboradores", "colaborador",
                "colaborador_form", "conta", "lancamentos", "lancamento_form", "transferencia", "fornecedores",
                "fornecedor", "importar_manager"},
}


def modulo_da_pagina(request: Request, pagina: str, usuario: Usuario | None) -> str | None:
    if usuario is None:
        return None
    modulo = next((m for m, paginas in PAGINAS_DO_MODULO.items() if pagina in paginas), None)
    if modulo:
        request.session["modulo"] = modulo
    else:
        modulo = request.session.get("modulo")
    if modulo not in MODULOS or not pode(usuario, MODULOS[modulo]["permissao"]):
        modulo = next((m for m, dados in MODULOS.items() if pode(usuario, dados["permissao"])), "alertas")
    return modulo


def render(request: Request, nome: str, usuario: Usuario | None = None, **contexto):
    avisos = request.session.pop("avisos", [])
    pagina = nome.split("/")[-1].split(".")[0]
    return templates.TemplateResponse(
        request, nome,
        {"usuario": usuario, "avisos": avisos, "pagina": pagina, "modulo": modulo_da_pagina(request, pagina, usuario),
         "modulos": MODULOS, **contexto},
    )


def voltar(destino: str) -> RedirectResponse:
    return RedirectResponse(destino, status_code=303)


def ip_de(request: Request) -> str | None:
    """IP de quem fez o pedido (no Render, o uvicorn já lê o IP real do cabeçalho do proxy)."""
    return request.client.host if request.client else None


# ---------------------------------------------------------------- leitura de formulários


def ler_data(valor: str | None) -> date | None:
    if not valor:
        return None
    try:
        return date.fromisoformat(valor.strip())
    except ValueError:
        pass
    for formato in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(valor.strip(), formato).date()
        except ValueError:
            continue
    return None


def ler_numero(valor: str | None) -> float | None:
    if valor is None or not str(valor).strip():
        return None
    limpo = str(valor).replace("R$", "").replace(" ", "")
    if "," in limpo or re.fullmatch(r"-?\d{1,3}(\.\d{3})+", limpo):  # formato brasileiro: 4.500 / 4.500,00
        limpo = limpo.replace(".", "").replace(",", ".")
    try:
        return float(limpo)
    except ValueError:
        return None


def ler_inteiro(valor: str | None) -> int | None:
    numero = ler_numero(valor)
    return int(round(numero)) if numero is not None else None
