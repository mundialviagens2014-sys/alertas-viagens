"""Importação de clientes e interesses por planilha (.xlsx ou .csv).

Funciona com a planilha de respostas do Google Forms: as colunas são reconhecidas
pelo texto do cabeçalho (ex.: "Seu WhatsApp com DDD" vira telefone), então a ordem
das colunas e o texto exato das perguntas não importam.
"""
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import aeroportos
from .models import Cliente, Interesse
from .parser import _normalizar, extrair_periodo
from .servicos import ajustar_nome, gerar_matches_para_interesse, normalizar_telefone

# Ordem importa: a primeira regra que bater define a coluna.
# Consentimento vem primeiro porque a pergunta costuma citar "WhatsApp" e "promoções".
REGRAS_COLUNAS: list[tuple[str, tuple[str, ...]]] = [
    ("consentimento", ("autoriz", "consent", "lgpd", "aceita receber", "aceito receber", "concorda", "permite")),
    ("data_resposta", ("carimbo", "timestamp", "data/hora", "enviado em")),
    ("email", ("e-mail", "email")),
    ("telefone", ("whatsapp", "whats", "telefone", "celular", "fone", "zap", "contato")),
    ("destino", ("destino", "para onde", "pra onde", "quer ir", "quer conhecer", "viajar para", "deseja ir")),
    ("origem", ("origem", "saindo", "sair de", "partida", "de onde", "embarque", "cidade", "mora")),
    ("inicio", ("data de ida", "data inicial", "a partir de", "inicio", "ida")),
    ("fim", ("data de volta", "data final", "volta", "fim", "ate quando")),
    ("periodo", ("quando", "periodo", "epoca", "mes", "datas")),
    ("preco_maximo", ("preco", "valor", "orcamento", "quanto", "pagar", "maximo")),
    ("observacoes", ("observ", "coment", "obs", "preferencia", "mais alguma")),
    ("nome", ("nome", "cliente", "passageiro", "chamar", "quem e voce", "quem e vc")),
]

CAMPOS_EXIBICAO = {
    "nome": "Nome", "telefone": "WhatsApp", "email": "E-mail", "origem": "Origem", "destino": "Destino",
    "inicio": "Data de ida", "fim": "Data de volta", "periodo": "Quando quer viajar",
    "preco_maximo": "Preço máximo", "consentimento": "Autoriza promoções", "observacoes": "Observações",
}

_SIM = {"sim", "s", "yes", "y", "true", "1", "x", "autorizo", "concordo", "aceito", "ok", "verdadeiro"}
_QUALQUER = {"", "qualquer", "qualquer lugar", "qualquer destino", "tanto faz", "todos", "nao sei", "aberto"}


@dataclass
class LinhaImportacao:
    numero: int
    nome: str = ""
    telefone: str = ""
    email: str | None = None
    observacoes: str | None = None
    consentimento: bool = False
    interesses: list[dict] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    situacao: str = ""  # novo, atualizar, erro
    cliente_id: int | None = None

    @property
    def valida(self) -> bool:
        return not self.erros


# ---------------------------------------------------------------- leitura do arquivo


def ler_planilha(conteudo: bytes, nome_arquivo: str) -> tuple[list[str], list[list]]:
    """Devolve (cabeçalhos, linhas). Aceita .xlsx e .csv (vírgula, ponto e vírgula ou tab)."""
    nome = nome_arquivo.lower()
    if nome.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
        linhas = [list(r) for r in wb.active.iter_rows(values_only=True)]
        wb.close()
    elif nome.endswith((".csv", ".txt")):
        for codificacao in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                texto = conteudo.decode(codificacao)
                break
            except UnicodeDecodeError:
                continue
        try:
            dialeto = csv.Sniffer().sniff(texto[:5000], delimiters=",;\t")
        except csv.Error:
            dialeto = csv.excel
        linhas = list(csv.reader(io.StringIO(texto), dialeto))
    else:
        raise ValueError("Formato não suportado. Envie um arquivo .xlsx (Excel/Google Planilhas) ou .csv.")

    linhas = [l for l in linhas if any(c not in (None, "") and str(c).strip() for c in l)]
    if not linhas:
        raise ValueError("A planilha está vazia.")
    cabecalhos = [str(c).strip() if c is not None else "" for c in linhas[0]]
    return cabecalhos, linhas[1:]


def _contem(texto: str, chave: str) -> bool:
    # palavras curtas ("ida", "mes", "fim") só valem inteiras: "cidade" não é "ida"
    if len(chave) <= 4:
        return re.search(rf"(?<![a-z]){re.escape(chave)}(?![a-z])", texto) is not None
    return chave in texto


def mapear_colunas(cabecalhos: list[str]) -> dict[str, int]:
    """campo -> índice da coluna, reconhecido pelo texto do cabeçalho."""
    mapa: dict[str, int] = {}
    for indice, cab in enumerate(cabecalhos):
        norm = _normalizar(cab)
        for campo, chaves in REGRAS_COLUNAS:
            if any(_contem(norm, ch) for ch in chaves):
                if campo not in mapa:
                    mapa[campo] = indice
                break
    return mapa


# ---------------------------------------------------------------- interpretação de valores


def _texto(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)  # telefone lido do Excel como número
    return str(valor).strip()


def _data(valor) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = _texto(valor)
    for formato in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(texto[:10], formato).date()
        except ValueError:
            continue
    inicio, _ = extrair_periodo(texto)
    return inicio


PRECO_MAXIMO_PLAUSIVEL = 200_000  # acima disso é erro de digitação ou célula com data


def _intervalo(valor) -> tuple[date | None, date | None]:
    """Resposta de data como intervalo: '15/03' -> (15/03, 15/03); 'março' -> (01/03, 31/03)."""
    if isinstance(valor, datetime):
        return valor.date(), valor.date()
    if isinstance(valor, date):
        return valor, valor
    texto = _texto(valor)
    if not texto:
        return None, None
    for formato in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            d = datetime.strptime(texto[:10], formato).date()
            return d, d
        except ValueError:
            continue
    return extrair_periodo(texto)


def _preco(valor) -> float | None:
    numero = _preco_bruto(valor)
    if numero is None or numero <= 0 or numero > PRECO_MAXIMO_PLAUSIVEL:
        return None
    return numero


def _preco_bruto(valor) -> float | None:
    if isinstance(valor, (datetime, date)):
        return None  # Google Planilhas às vezes transforma a resposta em data
    if isinstance(valor, (int, float)):
        return float(valor) or None
    if re.search(r"\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?", _texto(valor)):
        return None  # "20/10/2026" é data, não preço
    texto = _texto(valor).lower().replace("r$", "").replace(" ", "")
    mil = texto.endswith("mil")
    texto = re.sub(r"[^\d.,]", "", texto)
    if not texto:
        return None
    if "," in texto or re.fullmatch(r"\d{1,3}(\.\d{3})+", texto):
        texto = texto.replace(".", "").replace(",", ".")
    try:
        numero = float(texto)
    except ValueError:
        return None
    return numero * 1000 if mil and numero < 1000 else numero


def _lugares(valor: str) -> list[str]:
    """Separa "Lisboa, Paris e Roma" em ["Lisboa", "Paris", "Roma"]."""
    partes = re.split(r"\s*(?:[,;/|\n]|\be\b|\bou\b)\s*", valor)
    return [p.strip(" .") for p in partes if p.strip(" .")]


def _consentimento(valor) -> bool:
    texto = _normalizar(_texto(valor))
    return texto in _SIM or texto.startswith(("sim", "autorizo", "concordo", "aceito"))


# ---------------------------------------------------------------- montagem das linhas


def interpretar(
    db: Session,
    cabecalhos: list[str],
    linhas: list[list],
    origem_padrao: str | None,
    consentimento_padrao: bool,
) -> tuple[dict[str, int], list[LinhaImportacao]]:
    mapa = mapear_colunas(cabecalhos)
    resultado: list[LinhaImportacao] = []
    vistos: dict[str, LinhaImportacao] = {}

    def celula(linha, campo):
        i = mapa.get(campo)
        return linha[i] if i is not None and i < len(linha) else None

    for n, bruta in enumerate(linhas, start=2):  # linha 1 é o cabeçalho
        item = LinhaImportacao(numero=n)
        item.nome = _texto(celula(bruta, "nome"))
        item.telefone = normalizar_telefone(_texto(celula(bruta, "telefone")))
        item.email = _texto(celula(bruta, "email")) or None
        item.observacoes = _texto(celula(bruta, "observacoes")) or None
        item.consentimento = (
            _consentimento(celula(bruta, "consentimento")) if "consentimento" in mapa else consentimento_padrao
        )

        if not item.nome:
            item.erros.append("sem nome")
        if len(item.telefone) < 10:
            item.erros.append("telefone inválido" if item.telefone else "sem telefone")

        # ----- trecho(s) de interesse
        origens_txt = _lugares(_texto(celula(bruta, "origem")))
        origens = []
        for o in origens_txt:
            cod = aeroportos.resolver(o)
            if cod:
                origens.append(cod)
            else:
                item.avisos.append(f"origem “{o}” não reconhecida")
        if not origens and origem_padrao:
            origens = [origem_padrao]

        destino_txt = _texto(celula(bruta, "destino"))
        destinos: list[str | None] = []
        if _normalizar(destino_txt) in _QUALQUER:
            destinos = [None]
        else:
            for d in _lugares(destino_txt):
                if _normalizar(d) in _QUALQUER:
                    destinos.append(None)
                    continue
                cod = aeroportos.resolver(d)
                if cod:
                    destinos.append(cod)
                else:
                    item.avisos.append(f"destino “{d}” não reconhecido")
            if not destinos:
                destinos = []

        # ida: começo do período; volta: fim do período ("abril" na volta = até 30/04).
        # Só a ida informada: vale o próprio dia ou o mês inteiro, se for só o mês.
        ida_ini, ida_fim = _intervalo(celula(bruta, "inicio"))
        volta_ini, volta_fim = _intervalo(celula(bruta, "fim"))
        inicio, fim = ida_ini or volta_ini, volta_fim or ida_fim
        if _texto(celula(bruta, "inicio")) and not ida_ini:
            item.avisos.append(f"data de ida “{_texto(celula(bruta, 'inicio'))}” não reconhecida")
        if _texto(celula(bruta, "fim")) and not volta_ini:
            item.avisos.append(f"data de volta “{_texto(celula(bruta, 'fim'))}” não reconhecida")
        if not (inicio or fim) and "periodo" in mapa:
            inicio, fim = extrair_periodo(_texto(celula(bruta, "periodo")))
        if inicio and fim and fim < inicio:
            inicio, fim = fim, inicio
        preco_celula = celula(bruta, "preco_maximo")
        preco = _preco(preco_celula)
        if preco is None and _texto(preco_celula):
            item.avisos.append(f"preço “{_texto(preco_celula)}” não reconhecido: cliente entra sem preço máximo")

        if not origens:
            item.avisos.append("sem origem: cliente entra sem trecho de interesse")
        elif not destinos:
            item.avisos.append("nenhum destino reconhecido: cliente entra sem trecho de interesse")
        for o in origens:
            for d in destinos:
                if d and aeroportos.mesmo_lugar(o, d):
                    continue
                item.interesses.append(
                    {"origem": o, "destino": d, "periodo_inicio": inicio, "periodo_fim": fim, "preco_maximo": preco}
                )

        # ----- novo, atualização ou repetido na própria planilha
        if item.erros:
            item.situacao = "erro"
        elif item.telefone in vistos:
            anterior = vistos[item.telefone]
            anterior.interesses.extend(i for i in item.interesses if i not in anterior.interesses)
            anterior.consentimento = anterior.consentimento or item.consentimento
            anterior.avisos.append(f"também aparece na linha {n} (trechos somados)")
            continue
        else:
            existente = db.scalar(select(Cliente).where(Cliente.telefone == item.telefone))
            item.situacao = "atualizar" if existente else "novo"
            item.cliente_id = existente.id if existente else None
            vistos[item.telefone] = item
        resultado.append(item)
    return mapa, resultado


def _mesmo_interesse(i: Interesse, dados: dict) -> bool:
    return (
        i.origem == dados["origem"] and i.destino == dados["destino"]
        and i.periodo_inicio == dados["periodo_inicio"] and i.periodo_fim == dados["periodo_fim"]
    )


def aplicar(db: Session, linhas: list[LinhaImportacao]) -> dict:
    """Grava as linhas válidas. Clientes já cadastrados (mesmo telefone) ganham só os trechos novos."""
    resumo = {"novos": 0, "atualizados": 0, "interesses": 0, "erros": 0, "matches": 0}
    novos_interesses: list[Interesse] = []
    for item in linhas:
        if not item.valida:
            resumo["erros"] += 1
            continue
        cliente = db.scalar(select(Cliente).where(Cliente.telefone == item.telefone))
        if cliente is None:
            cliente = Cliente(nome=ajustar_nome(item.nome), telefone=item.telefone, email=item.email,
                              observacoes=item.observacoes, consentimento=item.consentimento)
            db.add(cliente)
            resumo["novos"] += 1
        else:
            cliente.email = cliente.email or item.email
            cliente.observacoes = cliente.observacoes or item.observacoes
            cliente.consentimento = cliente.consentimento or item.consentimento
            cliente.ativo = True
            resumo["atualizados"] += 1
        for dados in item.interesses:
            if any(_mesmo_interesse(i, dados) for i in cliente.interesses):
                continue
            interesse = Interesse(**dados)
            cliente.interesses.append(interesse)
            novos_interesses.append(interesse)
            resumo["interesses"] += 1
    db.commit()
    for interesse in novos_interesses:  # procura promoções vigentes para os trechos novos
        resumo["matches"] += gerar_matches_para_interesse(db, interesse)
    return resumo


def planilha_modelo() -> bytes:
    """Planilha de exemplo para a equipe preencher ou para usar como base do formulário."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Clientes"
    cab = ["Nome", "WhatsApp", "E-mail", "Origem", "Destino", "Quando quer viajar",
           "Preço máximo", "Autoriza receber promoções?", "Observações"]
    ws.append(cab)
    ws.append(["Maria Oliveira", "(85) 99876-1234", "maria@email.com", "Fortaleza", "Lisboa, Paris",
               "março a junho de 2027", "R$ 3.500", "Sim", "Viaja com o marido"])
    ws.append(["João Pereira", "(88) 99712-3344", "", "FOR", "Orlando", "julho", "4500", "Sim", ""])
    ws.append(["Ana Costa", "85981112222", "", "Fortaleza", "qualquer destino", "dezembro a janeiro", "1.500",
               "Sim", "Prefere voo direto"])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="0E6E6B")
    for col, largura in zip("ABCDEFGHI", (24, 18, 24, 14, 22, 24, 14, 26, 26)):
        ws.column_dimensions[col].width = largura
    ws.freeze_panes = "A2"
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()
