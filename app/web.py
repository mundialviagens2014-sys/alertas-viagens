"""Peças comuns do site: telas (Jinja), usuário logado, permissões, avisos e leitura de formulários."""
import ipaddress
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
from .models import TIPOS_PROMOCAO, Cliente, ConfirmacaoCadastro, Match, Promocao, Usuario
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
    """Mensagens aguardando envio: promoções e confirmações de cadastro (aparece no menu)."""
    limite = datetime.now() - timedelta(days=settings.promo_validade_dias)
    with SessionLocal() as db:
        promocoes = db.scalar(
            select(func.count(Match.id)).join(Promocao).join(Cliente)
            .where(Match.status == "nova", Promocao.status == "ativa", Promocao.coletada_em >= limite,
                   Cliente.ativo.is_(True))
        ) or 0
        confirmacoes = db.scalar(
            select(func.count(ConfirmacaoCadastro.id)).join(Cliente)
            .where(ConfirmacaoCadastro.status == "nova", Cliente.ativo.is_(True))
        ) or 0
        return promocoes + confirmacoes


def api_whatsapp_pronta() -> bool:
    """Se a API oficial do WhatsApp (envio automático) está configurada: mostra os botões "pela API" e as respostas."""
    from . import whatsapp_api

    with SessionLocal() as db:
        return whatsapp_api.configurada(db)


def respostas_whatsapp() -> int:
    """Respostas de clientes pela API ainda não lidas (aparece no menu)."""
    from . import whatsapp_api

    with SessionLocal() as db:
        return whatsapp_api.respostas_nao_lidas(db)


def pedidos_abertos() -> int:
    """Pedidos de emissão aguardando (aparece no menu do Manager)."""
    from .models_mvo import PedidoEmissao

    with SessionLocal() as db:
        return db.scalar(select(func.count(PedidoEmissao.id)).where(PedidoEmissao.status == "aguardando")) or 0


def classificacoes() -> list[str]:
    """Nomes de classificação para sugerir nos cadastros: as cadastradas em Configuração e as já usadas."""
    from .models_mvo import Classificacao, Colaborador

    with SessionLocal() as db:
        nomes = set(db.scalars(select(Classificacao.nome).where(Classificacao.ativo.is_not(False))))
        nomes |= set(db.scalars(select(Cliente.classificacao).where(Cliente.classificacao.is_not(None)).distinct()))
        nomes |= set(db.scalars(select(Colaborador.classificacao).where(Colaborador.classificacao.is_not(None)).distinct()))
    return sorted((n for n in nomes if n), key=str.lower)


def consolidadores() -> list[str]:
    """Consolidadores cadastrados (ativos) e os já usados nos colaboradores."""
    from .models_mvo import Colaborador, Consolidador

    with SessionLocal() as db:
        nomes = set(db.scalars(select(Consolidador.nome).where(Consolidador.ativo.is_not(False))))
        nomes |= set(db.scalars(select(Colaborador.consolidador).where(Colaborador.consolidador.is_not(None)).distinct()))
    return sorted((n for n in nomes if n), key=str.lower)


def cores_classificacoes() -> dict[str, str]:
    """Cor (hexadecimal) de cada classificação cadastrada, para as etiquetas."""
    from .models_mvo import CORES, Classificacao

    with SessionLocal() as db:
        return {c.nome.lower(): CORES.get(c.cor or "", CORES["azul"]) for c in db.scalars(select(Classificacao))}


def nomes_programas() -> list[str]:
    from .servicos import tabela_programas

    return sorted(tabela_programas())


def _versao_estaticos() -> str:
    """Muda sempre que um arquivo de estilo/script muda: o navegador não usa versão antiga guardada."""
    pasta = APP_DIR / "static"
    return str(int(max((f.stat().st_mtime for f in pasta.iterdir() if f.is_file()), default=0)))


from .busca import PERIODOS  # noqa: E402
from .exportar import link_planilha  # noqa: E402

FONTES = {"telegram": "Telegram", "whatsapp": "WhatsApp", "google_flights": "Google Flights", "manual": "Manual",
          "api": "API"}


def url_filtro(request: Request, **mudancas) -> str:
    """Endereço da mesma lista com alguns filtros trocados; os outros filtros continuam valendo."""
    from urllib.parse import urlencode

    params = [(k, v) for k, v in request.query_params.multi_items() if k not in mudancas and k != "formato"]
    params += [(k, v) for k, v in mudancas.items() if v not in (None, "")]
    return request.url.path + ("?" + urlencode(params) if params else "")


templates.env.globals.update(
    link_planilha=link_planilha,
    url_filtro=url_filtro,
    periodos=PERIODOS,
    versao=_versao_estaticos(),
    nomes_programas=nomes_programas,
    classificacoes=classificacoes,
    cores_classificacoes=cores_classificacoes,
    consolidadores=consolidadores,
    contar_pendentes=contar_pendentes,
    pedidos_abertos=pedidos_abertos,
    api_whatsapp_pronta=api_whatsapp_pronta,
    respostas_whatsapp=respostas_whatsapp,
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
    fontes=FONTES,
    tipos_promocao=TIPOS_PROMOCAO,
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


def checar_exportar(usuario: Usuario) -> None:
    """Exportar listas em planilha é só para quem tem a permissão (Administrador e Gestor)."""
    if not pode(usuario, "exportar"):
        raise SemPermissao(("exportar",))


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
                "promocao_nova", "telegram", "whatsapp", "whatsapp_respostas", "historico"},
    "manager": {"painel", "disponibilidade", "emissoes", "emissao", "emissao_form", "colaboradores", "colaborador",
                "colaborador_form", "conta", "contas", "conta_form", "lancamentos", "lancamento_form", "transferencia", "fornecedores",
                "fornecedor", "importar_manager", "clientes_manager", "cliente_manager", "cliente_manager_form",
                "comissoes", "pedidos", "pedido_form", "agencias", "agencia", "agencia_form", "contratos",
                "compras_externas", "compra_externa_form", "programas", "programa_tipo", "markups-programa",
                "markups-companhia", "grupos-produto", "classificacoes", "consolidadores", "franquias"},
}
# Grupos do menu do Manager que abrem e fecham (ficam abertos quando a página atual está neles).
GRUPO_CONFIGURACAO = {"programas", "programa_tipo", "precos", "markups-programa", "markups-companhia", "grupos-produto",
                      "classificacoes", "consolidadores", "importar_manager"}


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
    """Mostra o modelo. A página (para o menu) é o nome do arquivo, ou `pagina=` quando um modelo serve a várias."""
    avisos = request.session.pop("avisos", [])
    pagina = contexto.pop("pagina", None) or nome.split("/")[-1].split(".")[0]
    return templates.TemplateResponse(
        request, nome,
        {"usuario": usuario, "avisos": avisos, "pagina": pagina, "modulo": modulo_da_pagina(request, pagina, usuario),
         "modulos": MODULOS, "grupo_configuracao": GRUPO_CONFIGURACAO, **contexto},
    )


def voltar(destino: str) -> RedirectResponse:
    return RedirectResponse(destino, status_code=303)


def destino_seguro(valor, padrao: str) -> str:
    """Endereço de volta vindo do formulário: só caminhos deste site (nunca outro endereço)."""
    destino = valor if isinstance(valor, str) else ""
    if destino.startswith("/") and not destino.startswith("//") and not any(c in destino for c in "\\\r\n"):
        return destino
    return padrao


def ip_de(request: Request) -> str | None:
    """IP de quem fez o pedido. O Render fica atrás da Cloudflare, que sempre reescreve o CF-Connecting-IP com o IP
    real; sem esse cabeçalho, vale o IP que o uvicorn leu do X-Forwarded-For (que quem pede consegue inventar)."""
    for candidato in (request.headers.get("cf-connecting-ip"), request.client.host if request.client else None):
        try:
            return str(ipaddress.ip_address((candidato or "").strip()))
        except ValueError:
            continue
    return None


def chave_de_ip(request: Request) -> str:
    """Chave dos limites de tentativa: o IP; no IPv6, a rede /64 (os endereços de um mesmo assinante)."""
    ip = ip_de(request)
    if not ip:
        return "?"
    if ipaddress.ip_address(ip).version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return ip


def endereco_site(request: Request) -> str:
    """Endereço do site para os links que vão nas mensagens: SITE_URL, se configurado; senão, o endereço usado pela
    equipe (https://... no Render, que informa o https ao uvicorn)."""
    return (settings.site_url or str(request.base_url)).rstrip("/")


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


def ler_texto(valor, limite: int | None = None) -> str | None:
    """Texto do formulário sem espaços sobrando; vazio vira None."""
    texto = " ".join(valor.split()) if isinstance(valor, str) else ""
    if limite:
        texto = texto[:limite]
    return texto or None


CAMPOS_ENDERECO = ("cep", "endereco", "numero", "complemento", "bairro", "cidade", "estado")


def ler_endereco(form) -> dict:
    """CEP, endereço, número, complemento, bairro, cidade e UF de um formulário."""
    cep = re.sub(r"\D", "", form.get("cep") or "")[:8]
    return {
        "cep": f"{cep[:5]}-{cep[5:]}" if len(cep) == 8 else (cep or None),
        "endereco": ler_texto(form.get("endereco"), 200), "numero": ler_texto(form.get("numero"), 20),
        "complemento": ler_texto(form.get("complemento"), 80), "bairro": ler_texto(form.get("bairro"), 80),
        "cidade": ler_texto(form.get("cidade"), 80), "estado": (ler_texto(form.get("estado")) or "").upper()[:2] or None,
    }
