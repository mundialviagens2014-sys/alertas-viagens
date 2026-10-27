"""Aeroportos conhecidos, apelidos de cidades e agrupamento por região metropolitana.

Cada código (aeroporto ou código de cidade) aponta para (nome exibido, grupo).
Dois códigos do mesmo grupo são considerados o mesmo lugar no matching
(ex.: GRU, CGH e VCP atendem São Paulo).
Para incluir um aeroporto, adicione uma linha em AEROPORTOS e, se quiser,
apelidos em APELIDOS.
"""
import unicodedata

AEROPORTOS: dict[str, tuple[str, str]] = {
    # --- Brasil ---
    "SAO": ("São Paulo (todos)", "SAO"),
    "GRU": ("São Paulo – Guarulhos", "SAO"),
    "CGH": ("São Paulo – Congonhas", "SAO"),
    "VCP": ("Campinas – Viracopos", "SAO"),
    "RIO": ("Rio de Janeiro (todos)", "RIO"),
    "GIG": ("Rio de Janeiro – Galeão", "RIO"),
    "SDU": ("Rio de Janeiro – Santos Dumont", "RIO"),
    "BHZ": ("Belo Horizonte (todos)", "BHZ"),
    "CNF": ("Belo Horizonte – Confins", "BHZ"),
    "PLU": ("Belo Horizonte – Pampulha", "BHZ"),
    "BSB": ("Brasília", "BSB"),
    "SSA": ("Salvador", "SSA"),
    "FOR": ("Fortaleza", "FOR"),
    "REC": ("Recife", "REC"),
    "NAT": ("Natal", "NAT"),
    "MCZ": ("Maceió", "MCZ"),
    "JPA": ("João Pessoa", "JPA"),
    "AJU": ("Aracaju", "AJU"),
    "SLZ": ("São Luís", "SLZ"),
    "THE": ("Teresina", "THE"),
    "JDO": ("Juazeiro do Norte", "JDO"),
    "JJD": ("Jericoacoara", "JJD"),
    "PNZ": ("Petrolina", "PNZ"),
    "IOS": ("Ilhéus", "IOS"),
    "BPS": ("Porto Seguro", "BPS"),
    "FEN": ("Fernando de Noronha", "FEN"),
    "IMP": ("Imperatriz", "IMP"),
    "BEL": ("Belém", "BEL"),
    "MAO": ("Manaus", "MAO"),
    "STM": ("Santarém", "STM"),
    "MCP": ("Macapá", "MCP"),
    "BVB": ("Boa Vista", "BVB"),
    "PVH": ("Porto Velho", "PVH"),
    "RBR": ("Rio Branco", "RBR"),
    "PMW": ("Palmas", "PMW"),
    "POA": ("Porto Alegre", "POA"),
    "CWB": ("Curitiba", "CWB"),
    "FLN": ("Florianópolis", "FLN"),
    "NVT": ("Navegantes", "NVT"),
    "IGU": ("Foz do Iguaçu", "IGU"),
    "LDB": ("Londrina", "LDB"),
    "MGF": ("Maringá", "MGF"),
    "GYN": ("Goiânia", "GYN"),
    "CGB": ("Cuiabá", "CGB"),
    "CGR": ("Campo Grande", "CGR"),
    "VIX": ("Vitória", "VIX"),
    "UDI": ("Uberlândia", "UDI"),
    "RAO": ("Ribeirão Preto", "RAO"),
    "SJP": ("São José do Rio Preto", "SJP"),
    # --- América do Sul, Central e Caribe ---
    "BUE": ("Buenos Aires (todos)", "BUE"),
    "EZE": ("Buenos Aires – Ezeiza", "BUE"),
    "AEP": ("Buenos Aires – Aeroparque", "BUE"),
    "BRC": ("Bariloche", "BRC"),
    "MDZ": ("Mendoza", "MDZ"),
    "SCL": ("Santiago", "SCL"),
    "MVD": ("Montevidéu", "MVD"),
    "PDP": ("Punta del Este", "PDP"),
    "ASU": ("Assunção", "ASU"),
    "LIM": ("Lima", "LIM"),
    "CUZ": ("Cusco", "CUZ"),
    "BOG": ("Bogotá", "BOG"),
    "MDE": ("Medellín", "MDE"),
    "CTG": ("Cartagena", "CTG"),
    "ADZ": ("San Andrés", "ADZ"),
    "UIO": ("Quito", "UIO"),
    "PTY": ("Cidade do Panamá", "PTY"),
    "MEX": ("Cidade do México", "MEX"),
    "CUN": ("Cancún", "CUN"),
    "PUJ": ("Punta Cana", "PUJ"),
    "HAV": ("Havana", "HAV"),
    "AUA": ("Aruba", "AUA"),
    "CUR": ("Curaçao", "CUR"),
    # --- América do Norte ---
    "NYC": ("Nova York (todos)", "NYC"),
    "JFK": ("Nova York – JFK", "NYC"),
    "EWR": ("Nova York – Newark", "NYC"),
    "LGA": ("Nova York – LaGuardia", "NYC"),
    "MIA": ("Miami", "MIA"),
    "FLL": ("Fort Lauderdale", "FLL"),
    "MCO": ("Orlando", "MCO"),
    "ATL": ("Atlanta", "ATL"),
    "BOS": ("Boston", "BOS"),
    "ORD": ("Chicago", "ORD"),
    "WAS": ("Washington (todos)", "WAS"),
    "IAD": ("Washington – Dulles", "WAS"),
    "IAH": ("Houston", "IAH"),
    "DFW": ("Dallas", "DFW"),
    "LAS": ("Las Vegas", "LAS"),
    "LAX": ("Los Angeles", "LAX"),
    "SFO": ("San Francisco", "SFO"),
    "YYZ": ("Toronto", "YYZ"),
    "YUL": ("Montreal", "YUL"),
    # --- Europa ---
    "LIS": ("Lisboa", "LIS"),
    "OPO": ("Porto", "OPO"),
    "FAO": ("Faro", "FAO"),
    "MAD": ("Madri", "MAD"),
    "BCN": ("Barcelona", "BCN"),
    "PAR": ("Paris (todos)", "PAR"),
    "CDG": ("Paris – Charles de Gaulle", "PAR"),
    "ORY": ("Paris – Orly", "PAR"),
    "LON": ("Londres (todos)", "LON"),
    "LHR": ("Londres – Heathrow", "LON"),
    "LGW": ("Londres – Gatwick", "LON"),
    "ROM": ("Roma (todos)", "ROM"),
    "FCO": ("Roma – Fiumicino", "ROM"),
    "MIL": ("Milão (todos)", "MIL"),
    "MXP": ("Milão – Malpensa", "MIL"),
    "VCE": ("Veneza", "VCE"),
    "AMS": ("Amsterdã", "AMS"),
    "BRU": ("Bruxelas", "BRU"),
    "FRA": ("Frankfurt", "FRA"),
    "MUC": ("Munique", "MUC"),
    "BER": ("Berlim", "BER"),
    "ZRH": ("Zurique", "ZRH"),
    "GVA": ("Genebra", "GVA"),
    "VIE": ("Viena", "VIE"),
    "PRG": ("Praga", "PRG"),
    "DUB": ("Dublin", "DUB"),
    "ATH": ("Atenas", "ATH"),
    "IST": ("Istambul", "IST"),
    # --- Oriente Médio, África, Ásia, Oceania ---
    "DXB": ("Dubai", "DXB"),
    "DOH": ("Doha", "DOH"),
    "TLV": ("Tel Aviv", "TLV"),
    "CAI": ("Cairo", "CAI"),
    "JNB": ("Joanesburgo", "JNB"),
    "CPT": ("Cidade do Cabo", "CPT"),
    "LAD": ("Luanda", "LAD"),
    "TYO": ("Tóquio (todos)", "TYO"),
    "NRT": ("Tóquio – Narita", "TYO"),
    "HND": ("Tóquio – Haneda", "TYO"),
    "ICN": ("Seul", "ICN"),
    "PEK": ("Pequim", "PEK"),
    "BKK": ("Bangkok", "BKK"),
    "SIN": ("Singapura", "SIN"),
    "MLE": ("Maldivas", "MLE"),
    "SYD": ("Sydney", "SYD"),
}

# Apelidos (sem acento, minúsculos) -> código
APELIDOS: dict[str, str] = {
    "sao paulo": "SAO", "sampa": "SAO", "sp": "SAO", "guarulhos": "GRU", "congonhas": "CGH",
    "campinas": "VCP", "viracopos": "VCP",
    "rio de janeiro": "RIO", "rio": "RIO", "galeao": "GIG", "santos dumont": "SDU",
    "belo horizonte": "BHZ", "bh": "BHZ", "confins": "CNF",
    "brasilia": "BSB", "salvador": "SSA", "fortaleza": "FOR", "recife": "REC", "natal": "NAT",
    "maceio": "MCZ", "joao pessoa": "JPA", "aracaju": "AJU", "sao luis": "SLZ", "teresina": "THE",
    "juazeiro do norte": "JDO", "jericoacoara": "JJD", "jeri": "JJD", "petrolina": "PNZ",
    "ilheus": "IOS", "porto seguro": "BPS", "fernando de noronha": "FEN", "noronha": "FEN",
    "imperatriz": "IMP", "belem": "BEL", "manaus": "MAO", "santarem": "STM", "macapa": "MCP",
    "boa vista": "BVB", "porto velho": "PVH", "rio branco": "RBR", "palmas": "PMW",
    "porto alegre": "POA", "curitiba": "CWB", "florianopolis": "FLN", "floripa": "FLN",
    "navegantes": "NVT", "foz do iguacu": "IGU", "londrina": "LDB", "maringa": "MGF",
    "goiania": "GYN", "cuiaba": "CGB", "campo grande": "CGR", "vitoria": "VIX",
    "uberlandia": "UDI", "ribeirao preto": "RAO", "sao jose do rio preto": "SJP",
    "buenos aires": "BUE", "bariloche": "BRC", "mendoza": "MDZ", "santiago": "SCL",
    "santiago do chile": "SCL", "montevideu": "MVD", "punta del este": "PDP", "assuncao": "ASU",
    "lima": "LIM", "cusco": "CUZ", "bogota": "BOG", "medellin": "MDE", "cartagena": "CTG",
    "san andres": "ADZ", "quito": "UIO", "panama": "PTY", "cidade do panama": "PTY",
    "cidade do mexico": "MEX", "cancun": "CUN", "punta cana": "PUJ", "havana": "HAV",
    "aruba": "AUA", "curacao": "CUR",
    "nova york": "NYC", "nova iorque": "NYC", "new york": "NYC", "ny": "NYC", "miami": "MIA",
    "fort lauderdale": "FLL", "orlando": "MCO", "atlanta": "ATL", "boston": "BOS",
    "chicago": "ORD", "washington": "WAS", "houston": "IAH", "dallas": "DFW",
    "las vegas": "LAS", "los angeles": "LAX", "san francisco": "SFO", "toronto": "YYZ",
    "montreal": "YUL",
    "lisboa": "LIS", "porto": "OPO", "faro": "FAO", "madri": "MAD", "madrid": "MAD",
    "barcelona": "BCN", "paris": "PAR", "londres": "LON", "london": "LON", "roma": "ROM",
    "milao": "MIL", "veneza": "VCE", "amsterda": "AMS", "amsterdam": "AMS", "bruxelas": "BRU",
    "frankfurt": "FRA", "munique": "MUC", "berlim": "BER", "zurique": "ZRH", "genebra": "GVA",
    "viena": "VIE", "praga": "PRG", "dublin": "DUB", "atenas": "ATH", "istambul": "IST",
    "dubai": "DXB", "doha": "DOH", "tel aviv": "TLV", "cairo": "CAI", "joanesburgo": "JNB",
    "cidade do cabo": "CPT", "luanda": "LAD", "toquio": "TYO", "tokyo": "TYO", "seul": "ICN",
    "pequim": "PEK", "bangkok": "BKK", "singapura": "SIN", "maldivas": "MLE", "sydney": "SYD",
}

# Códigos de cidade que também são palavras comuns em maiúsculas; não são lidos
# como sigla solta no texto (ex.: "R$ 2 MIL"). Continuam válidos no cadastro.
SIGLAS_AMBIGUAS = {"MIL", "PAR", "ROM", "WAS", "SIN"}


def sem_acento(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c)).lower()


def grupo(codigo: str | None) -> str | None:
    if not codigo:
        return None
    info = AEROPORTOS.get(codigo.upper())
    return info[1] if info else codigo.upper()


def codigos_do_lugar(codigo: str | None) -> list[str]:
    """Todos os códigos da mesma cidade: 'SAO' -> ['CGH', 'GRU', 'SAO', 'VCP']; 'LIS' -> ['LIS']."""
    if not codigo:
        return []
    g = grupo(codigo)
    return sorted({c for c, (_, gr) in AEROPORTOS.items() if gr == g} | {codigo.upper(), g})


def mesmo_lugar(a: str | None, b: str | None) -> bool:
    """True se os dois códigos atendem a mesma cidade (ou se um é o código da cidade do outro)."""
    return bool(a and b) and grupo(a) == grupo(b)


def nome(codigo: str | None) -> str:
    if not codigo:
        return "Qualquer destino"
    info = AEROPORTOS.get(codigo.upper())
    return info[0] if info else codigo.upper()


def nome_curto(codigo: str | None) -> str:
    """Nome da cidade sem o detalhe do aeroporto (para mensagens ao cliente)."""
    if not codigo:
        return "qualquer destino"
    return nome(codigo).split(" – ")[0].replace(" (todos)", "")


def resolver(entrada: str | None) -> str | None:
    """Converte o que a equipe digitou (código ou nome da cidade) em código. None se desconhecido."""
    if not entrada or not entrada.strip():
        return None
    bruto = entrada.strip()
    if bruto.upper() in AEROPORTOS:
        return bruto.upper()
    chave = sem_acento(bruto)
    if chave in APELIDOS:
        return APELIDOS[chave]
    for codigo, (exibicao, _) in AEROPORTOS.items():
        if sem_acento(exibicao) == chave:
            return codigo
    return None


def opcoes() -> list[tuple[str, str]]:
    """Lista (código, nome) para os campos de seleção do site, ordenada por nome."""
    return sorted(((c, f"{n} ({c})") for c, (n, _) in AEROPORTOS.items()), key=lambda x: sem_acento(x[1]))
