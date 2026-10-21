"""Leitura de textos de promoções (Telegram, mensagens coladas etc.).

Transforma um texto livre como
    "🔥 FORTALEZA ➜ LISBOA por R$ 2.389 ida e volta! Datas de março a junho/27 – TAP"
em dados estruturados: origem, destino, preço, período, companhia e link.
"""
import calendar
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from .aeroportos import AEROPORTOS, APELIDOS, SIGLAS_AMBIGUAS, grupo
from .aeroportos import resolver as aeroportos_resolver


@dataclass
class PromocaoExtraida:
    origem: str | None = None
    destino: str | None = None
    preco: float | None = None
    milhas: int | None = None
    data_inicio: date | None = None
    data_fim: date | None = None
    companhia: str | None = None
    link: str | None = None
    confianca: float = 0.0
    programa: str | None = None  # programa de milhas (Smiles, Azul Fidelidade...)
    tipo_viagem: str | None = None  # "ida_volta", "ida" ou None (não informado)

    @property
    def completa(self) -> bool:
        return bool(self.origem and self.destino and self.origem != self.destino)


def _normalizar(texto: str) -> str:
    """Minúsculo e sem acento, mantendo exatamente o mesmo tamanho do texto original."""
    saida = []
    for c in texto:
        base = "".join(x for x in unicodedata.normalize("NFKD", c) if not unicodedata.combining(x))
        saida.append(base.lower() if len(base) == 1 else c.lower()[:1] or " ")
    return "".join(saida)


# ---------------------------------------------------------------- trecho

# Apelidos que também são palavras comuns ("Natal" = festa, "Rio" = rio...):
# só valem quando aparecem junto de um conector de rota.
_APELIDOS_AMBIGUOS = {"natal", "rio", "porto", "vitoria", "palmas", "lima", "faro", "panama", "santiago", "sp", "ny", "bh", "jeri"}
_RE_APELIDOS = re.compile(
    r"(?<![a-z0-9])(" + "|".join(re.escape(a) for a in sorted(APELIDOS, key=len, reverse=True)) + r")(?![a-z0-9])"
)
_RE_SIGLA = re.compile(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])")
_CONECTOR_ANTES = re.compile(r"(?:\bpara|\bpra|\bate|\bx|->|→|➜|➡|✈|-|–|/|(?:saindo|partindo|voos?)\s+de)\s*$")
_CONECTOR_DEPOIS = re.compile(r"^\s*(?:x\b|->|→|➜|➡|✈|-|–|/|para\b|pra\b)")
_MARCA_ORIGEM = re.compile(r"(?:saindo|partindo|saida|saidas|origem|embarque|embarcando)\s*(?:de|da|do|em|:)?\s*$")
_MARCA_DESTINO = re.compile(r"(?:\bpara|\bpra|destino:?|->|→|➜|➡|✈|\bx)\s*(?:o|a|os|as)?\s*$")


def _achar_locais(texto: str, norm: str) -> list[tuple[int, str, bool, bool]]:
    """Lista (posição, código, origem_explícita, destino_explícito) na ordem do texto."""
    achados: list[tuple[int, int, str]] = []
    for m in _RE_SIGLA.finditer(texto):
        cod = m.group(1)
        if cod in AEROPORTOS and cod not in SIGLAS_AMBIGUAS:
            achados.append((m.start(), m.end(), cod))
    for m in _RE_APELIDOS.finditer(norm):
        apelido = m.group(1)
        if any(ini < m.end() and m.start() < fim for ini, fim, _ in achados):
            continue  # já coberto por uma sigla
        if apelido in _APELIDOS_AMBIGUOS:
            antes, depois = norm[max(0, m.start() - 8): m.start()], norm[m.end(): m.end() + 6]
            if not (_CONECTOR_ANTES.search(antes) or _CONECTOR_DEPOIS.search(depois)):
                continue
        achados.append((m.start(), m.end(), APELIDOS[apelido]))
    achados.sort()
    return [
        (
            ini,
            cod,
            bool(_MARCA_ORIGEM.search(norm[max(0, ini - 25): ini])),
            bool(_MARCA_DESTINO.search(norm[max(0, ini - 12): ini])),
        )
        for ini, _, cod in achados
    ]


_RE_ROTULO = re.compile(r"(?m)^[^\w\n]*(origem|saindo de|partida|destino|chegada)\s*:\s*(.+)$")


def _trecho_rotulado(texto: str, norm: str) -> tuple[str | None, str | None]:
    """Formato de alerta com campos ("📍 Origem: NATAL" / "📍 Destino: RECIFE")."""
    achados: dict[str, str] = {}
    for m in _RE_ROTULO.finditer(norm):
        tipo = "origem" if m.group(1) in ("origem", "saindo de", "partida") else "destino"
        if tipo in achados:
            continue
        ini, fim = m.span(2)
        valor_txt, valor_norm = texto[ini:fim], norm[ini:fim]
        cod = aeroportos_resolver(valor_txt.strip(" .!*_"))
        if not cod:
            # "SÃO PAULO (GRU)", "Fortaleza - CE": procura sigla ou cidade dentro do valor;
            # aqui o rótulo já diz que é um lugar, então apelidos ambíguos ("Natal") valem
            siglas = [s for s in _RE_SIGLA.findall(valor_txt) if s in AEROPORTOS]
            apelidos = [APELIDOS[a] for a in _RE_APELIDOS.findall(valor_norm)]
            cod = (siglas or apelidos or [None])[0]
        if cod:
            achados[tipo] = cod
    return achados.get("origem"), achados.get("destino")


def _definir_trecho(locais: list[tuple[int, str, bool, bool]]) -> tuple[str | None, str | None]:
    if not locais:
        return None, None
    explicita = [cod for _, cod, origem, _ in locais if origem]
    sem_marca = [cod for _, cod, _, dest in locais if not dest]
    if explicita:
        origem = explicita[0]
    elif locais[0][3] and sem_marca:  # "Para Miami, de São Paulo": o primeiro lugar é o destino
        origem = sem_marca[0]
    else:
        origem = locais[0][1]
    candidatos = [(dest, cod) for _, cod, orig, dest in locais if grupo(cod) != grupo(origem) and not orig]
    if not candidatos:
        return origem, None
    marcados = [cod for dest, cod in candidatos if dest]
    return origem, (marcados[0] if marcados else candidatos[0][1])


# ---------------------------------------------------------------- preço e milhas

_RE_PRECO = re.compile(r"r\$\s*(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)(\s*mil\b)?")
_RE_REAIS = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?\s*reais\b")
_RE_MILHAS = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+|\d+(?:,\d+)?)\s*(mil|k)?\s*(?:milhas|pontos)\b")


def _numero_br(valor: str) -> float:
    return float(valor.replace(".", "").replace(",", "."))


def _extrair_preco(norm: str) -> float | None:
    valores = []
    for m in _RE_PRECO.finditer(norm):
        v = _numero_br(m.group(1)) * (1000 if m.group(2) else 1)
        valores.append(v)
    for m in _RE_REAIS.finditer(norm):
        valores.append(_numero_br(m.group(1)))
    valores = [v for v in valores if v >= 50]  # ignora valores de taxas/parcelas muito baixas
    return min(valores) if valores else None


def _extrair_milhas(norm: str) -> int | None:
    valores = []
    for m in _RE_MILHAS.finditer(norm):
        bruto, mult = m.group(1), m.group(2)
        v = float(bruto.replace(",", ".")) * 1000 if mult else _numero_br(bruto)
        valores.append(int(v))
    valores = [v for v in valores if v >= 1000]
    return min(valores) if valores else None


# ---------------------------------------------------------------- datas

_MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7,
    "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8,
    "set": 9, "out": 10, "nov": 11, "dez": 12,
}
_ABREV_AMBIGUAS = {"mar", "set", "out"}  # "vista para o mar", "set de fotos", "out" em inglês
_RE_MES = re.compile(
    r"(?<![a-z])(" + "|".join(sorted(_MESES, key=len, reverse=True)) + r")(?![a-z])\.?"
    r"(\s*/\s*\d{4}(?!\d)|\s*/\s*\d{2}(?!\d)|\s+de\s+20\d{2}(?!\d)|\s+20\d{2}(?!\d))?"
)
_RE_MES_ANO = re.compile(r"(?<![\d/])(\d{1,2})/(20\d{2})(?![\d/])")
_RE_DATA = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?(?![\d/])")
_RE_DIA_ANTES = re.compile(r"(?<!\d)(\d{1,2})\s*(?:de\s*)?$")
_RE_NAO_E_VIAGEM = re.compile(r"(compr|valid|emit|emiss|reserv|ate hoje|expira|encerra|postad|cotac|pesquis|consulta)")
# "Março 2027: 21, 22, 23" / "Abril/27: 09 e 10" -> dias exatos dentro do mês
_RE_MES_DIAS = re.compile(
    r"(?<![a-z])(" + "|".join(sorted(_MESES, key=len, reverse=True)) + r")(?![a-z])\.?"
    r"\s*(?:de\s*|/\s*)?(\d{4}|\d{2})?\s*:\s*((?:\d{1,2}(?:\s*(?:,|e|;|-)\s*|\s+)?)+)"
)


def _ano(mes: int, ano_txt: str | None, hoje: date) -> int:
    if ano_txt:
        ano = int(re.sub(r"\D", "", ano_txt))
        return ano + 2000 if ano < 100 else ano
    return hoje.year if mes >= hoje.month else hoje.year + 1


def _extrair_datas(norm: str, hoje: date) -> tuple[date | None, date | None]:
    intervalos: list[tuple[date, date]] = []
    ocupado: list[tuple[int, int]] = []

    for m in _RE_MES_DIAS.finditer(norm):
        mes = _MESES[m.group(1)]
        ano = _ano(mes, m.group(2), hoje)
        ultimo = calendar.monthrange(ano, mes)[1]
        dias = [int(d) for d in re.findall(r"\d{1,2}", m.group(3)) if 1 <= int(d) <= ultimo]
        if not dias or _RE_NAO_E_VIAGEM.search(norm[max(0, m.start() - 20): m.start()]):
            continue
        intervalos.append((date(ano, mes, min(dias)), date(ano, mes, max(dias))))
        ocupado.append((m.start(), m.end()))

    for m in _RE_MES_ANO.finditer(norm):  # "03/2027" = março inteiro
        mes, ano = int(m.group(1)), int(m.group(2))
        if not 1 <= mes <= 12 or _RE_NAO_E_VIAGEM.search(norm[max(0, m.start() - 20): m.start()]):
            continue
        intervalos.append((date(ano, mes, 1), date(ano, mes, calendar.monthrange(ano, mes)[1])))
        ocupado.append((m.start(), m.end()))

    for m in _RE_DATA.finditer(norm):
        if any(a <= m.start() < b for a, b in ocupado):
            continue
        dia, mes = int(m.group(1)), int(m.group(2))
        if not (1 <= dia <= 31 and 1 <= mes <= 12):
            continue
        if _RE_NAO_E_VIAGEM.search(norm[max(0, m.start() - 20): m.start()]):
            continue
        ano = _ano(mes, m.group(3), hoje)
        dia = min(dia, calendar.monthrange(ano, mes)[1])
        d = date(ano, mes, dia)
        intervalos.append((d, d))
        ocupado.append((m.start(), m.end()))

    for m in _RE_MES.finditer(norm):
        if any(a <= m.start() < b for a, b in ocupado):
            continue
        palavra, ano_txt = m.group(1), m.group(2)
        if palavra in _ABREV_AMBIGUAS and not ano_txt:
            continue
        if _RE_NAO_E_VIAGEM.search(norm[max(0, m.start() - 20): m.start()]):
            continue
        mes = _MESES[palavra]
        ano = _ano(mes, ano_txt, hoje)
        ultimo = calendar.monthrange(ano, mes)[1]
        dia = _RE_DIA_ANTES.search(norm[max(0, m.start() - 6): m.start()])
        if dia and 1 <= int(dia.group(1)) <= ultimo:
            d = date(ano, mes, int(dia.group(1)))
            intervalos.append((d, d))
        else:
            intervalos.append((date(ano, mes, 1), date(ano, mes, ultimo)))

    if not intervalos:
        return None, None
    return min(i for i, _ in intervalos), max(f for _, f in intervalos)


_RE_IDA_VOLTA = re.compile(
    r"ida\s*(?:e|/|&|\+)\s*volta|\bida-volta\b|(?<![a-z])i\s*/\s*v(?![a-z])|round\s*-?\s*trip"
    r"|datas?\s+(?:de|da)\s+volta|(?<![a-z])volta\s*:|(?<![a-z])retorno\s*:"
)
_RE_SO_IDA = re.compile(
    r"(?:so|somente|apenas)\s+(?:a\s+|de\s+)?ida(?![a-z])|ida\s+simples|one\s*-?\s*way|trecho\s+(?:unico|simples)"
    r"|(?:por|o|cada|ao)\s+trecho(?![a-z])|(?<![a-z])(?:so|somente)\s+trecho"
)


def detectar_tipo_viagem(texto: str | None) -> str | None:
    """'ida_volta', 'ida' ou None. "Ida e volta" vence quando o texto cita os dois."""
    norm = _normalizar(texto or "")
    if _RE_IDA_VOLTA.search(norm):
        return "ida_volta"
    if _RE_SO_IDA.search(norm):
        return "ida"
    return None


def extrair_periodo(texto: str, hoje: date | None = None) -> tuple[date | None, date | None]:
    """Período de viagem escrito livremente (ex.: "julho", "dezembro a fevereiro", "10/01 a 20/01")."""
    norm = _normalizar(texto or "")
    # numa resposta de formulário "mar", "set" e "out" são meses, não palavras soltas
    norm = re.sub(r"(?<![a-z])(mar|set|out)(?![a-z])", lambda m: {"mar": "marco", "set": "setembro", "out": "outubro"}[m.group(1)], norm)
    return _extrair_datas(norm, hoje or date.today())


# ---------------------------------------------------------------- companhia e link

_COMPANHIAS = [
    "LATAM", "TAP", "Air France", "KLM", "Iberia", "American Airlines", "United", "Delta",
    "Copa Airlines", "Copa", "Avianca", "Emirates", "Qatar", "Lufthansa", "Aerolineas Argentinas",
    "JetSMART", "Sky Airline", "Air Europa", "ITA Airways", "Turkish", "Ethiopian", "Swiss",
    "British Airways", "Arajet", "Flybondi", "Edelweiss", "Air Canada", "Aeromexico", "Iberojet",
]
_RE_COMPANHIA = re.compile(
    r"(?<![a-z])(" + "|".join(re.escape(_normalizar(c)) for c in _COMPANHIAS) + r")(?![a-z])"
)
# "Gol" e "Azul" também são palavras comuns: só contam com inicial maiúscula.
_RE_COMPANHIA_MAIUSC = re.compile(r"(?<![A-Za-z])(GOL|Gol|AZUL|Azul)(?![A-Za-z])")
_RE_LINK = re.compile(r"https?://[^\s<>\"'）)]+")


def _extrair_companhia(texto: str, norm: str) -> str | None:
    achados = []
    m = _RE_COMPANHIA.search(norm)
    if m:
        alvo = m.group(1)
        nome = next(c for c in _COMPANHIAS if _normalizar(c) == alvo)
        achados.append((m.start(), nome))
    m = _RE_COMPANHIA_MAIUSC.search(texto)
    if m:
        achados.append((m.start(), m.group(1).capitalize()))
    return min(achados)[1] if achados else None


def _extrair_link(texto: str) -> str | None:
    m = _RE_LINK.search(texto)
    return m.group(0).rstrip(".,;:!?*_") if m else None


# ---------------------------------------------------------------- função principal


def extrair(texto: str, hoje: date | None = None) -> PromocaoExtraida:
    hoje = hoje or date.today()
    texto = texto or ""
    norm = _normalizar(texto)

    # Links não entram na busca de cidades/datas (ex.: ".../promo-lisboa-03-2027")
    sem_link, norm_sem_link = texto, norm
    for m in _RE_LINK.finditer(texto):
        vazio = " " * (m.end() - m.start())
        sem_link = sem_link[: m.start()] + vazio + sem_link[m.end():]
        norm_sem_link = norm_sem_link[: m.start()] + vazio + norm_sem_link[m.end():]
    texto_original, texto, norm = texto, sem_link, norm_sem_link

    origem, destino = _trecho_rotulado(texto, norm)
    if not (origem and destino):
        livre_origem, livre_destino = _definir_trecho(_achar_locais(texto, norm))
        origem = origem or livre_origem
        destino = destino or (livre_destino if livre_destino and grupo(livre_destino) != grupo(origem) else None)
    # "Rio de Janeiro" não é o mês de janeiro
    norm_datas = re.sub(r"rio de janeiro", lambda m: " " * len(m.group()), norm)
    inicio, fim = _extrair_datas(norm_datas, hoje)
    p = PromocaoExtraida(
        origem=origem,
        destino=destino,
        preco=_extrair_preco(norm),
        milhas=_extrair_milhas(norm),
        data_inicio=inicio,
        data_fim=fim,
        companhia=_extrair_companhia(texto, norm),
        link=_extrair_link(texto_original),
        tipo_viagem=detectar_tipo_viagem(texto),
    )
    if p.milhas:
        from .programas import detectar_programa

        p.programa = detectar_programa(texto, p.companhia)

    conf = 0.0
    if p.completa:
        conf += 0.4
    elif p.origem or p.destino:
        conf += 0.1
    if p.preco or p.milhas:
        conf += 0.3
    if p.data_inicio:
        conf += 0.15
    if p.link:
        conf += 0.1
    if p.companhia:
        conf += 0.05
    p.confianca = round(min(conf, 1.0), 2)
    return p
