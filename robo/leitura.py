"""Leitura da reserva a partir das respostas JSON que o site da companhia recebe ao abrir "Minhas viagens".

Em vez de ler a tela (que muda toda hora), o robô escuta as respostas JSON que o próprio site busca para montar a
página e procura nelas o que parece um voo (aeroporto de saída e chegada + horários) e o que parece um passageiro
(nome e sobrenome). Funciona com formatos diferentes de site; quando uma companhia tiver um formato que isto não
entende, o modo "aprender" guarda as respostas para ajustarmos.
"""
import re
from datetime import datetime, timedelta

IATA = re.compile(r"^[A-Z]{3}$")

CHAVES_SAIDA = ("departure", "origin", "departureairport", "originairport", "from", "boardpoint", "partida", "origem",
                "departurestation", "originstation", "embarque")
CHAVES_CHEGADA = ("arrival", "destination", "arrivalairport", "destinationairport", "to", "offpoint", "chegada",
                  "destino", "arrivalstation", "destinationstation", "desembarque")
CHAVES_CODIGO = ("iatacode", "airportcode", "locationcode", "code", "iata", "station", "airport", "codigo", "id")
CHAVES_HORA = ("localdatetime", "datetimelocal", "scheduleddatetime", "datetime", "scheduled", "localtime", "date",
               "time", "at", "datahora", "data", "hora", "estimated", "std", "sta")
CHAVES_NUMERO = ("flightdesignator", "flightnumber", "marketingflightnumber", "operatingflightnumber", "number", "numero", "flightno",
                 "flight")
CHAVES_CIA = ("marketingairlinecode", "marketingcarrier", "carriercode", "airlinecode", "carrier", "airline",
              "marketingairline", "companhia")
CHAVES_OPERADORA = ("operatingairlinename", "operatingcarriername", "operatingairline", "operatedby", "operatingcarrier")
CHAVES_CABINE = ("cabin", "cabinclass", "cabine", "brandname", "farefamily", "fare")

PRIMEIRO_NOME = ("firstname", "givenname", "first", "nome", "primeironome", "given")
SOBRENOME = ("lastname", "surname", "familyname", "last", "sobrenome", "family")
CHAVES_BILHETE = ("ticketnumber", "eticketnumber", "eticket", "documentnumber", "ticket", "bilhete", "numerobilhete")


def _chaves(d: dict) -> dict:
    return {re.sub(r"[^a-z]", "", str(k).lower()): v for k, v in d.items()}


def _primeiro(d: dict, nomes) -> object:
    k = _chaves(d)
    for n in nomes:
        if n in k and k[n] not in (None, "", [], {}):
            return k[n]
    return None


def _codigo(valor) -> str | None:
    if isinstance(valor, str):
        v = valor.strip().upper()
        return v if IATA.match(v) else None
    if isinstance(valor, dict):
        for n in CHAVES_CODIGO:
            c = _codigo(_chaves(valor).get(n))
            if c:
                return c
        for v in valor.values():
            if isinstance(v, dict):
                c = _codigo(_primeiro(v, CHAVES_CODIGO))
                if c:
                    return c
    return None


def _hora(valor) -> datetime | None:
    if isinstance(valor, str):
        texto = valor.strip()
        texto = re.sub(r"([+-]\d\d:?\d\d|Z)$", "", texto)   # hora local do aeroporto: ignora o fuso
        for formato in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                        "%d/%m/%Y %H:%M", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                return datetime.strptime(texto[:26], formato)
            except ValueError:
                continue
        return None
    if isinstance(valor, dict):
        for n in CHAVES_HORA:
            h = _hora(_chaves(valor).get(n))
            if h:
                return h
        k = _chaves(valor)   # data e hora separadas
        if isinstance(k.get("date"), str) and isinstance(k.get("time"), str):
            return _hora(f"{k['date'][:10]}T{k['time'][:5]}")
    return None


def _lado(seg: dict, chaves) -> tuple[str | None, datetime | None]:
    k = _chaves(seg)
    for n in chaves:
        if n in k:
            alvo = k[n]
            codigo = _codigo(alvo)
            hora = _hora(alvo)
            if codigo:
                if not hora:  # ex.: "departureAirport": "FOR" + "departureDateTime": "..."
                    hora = _hora(k.get(n.replace("airport", "") + "datetime")) or _hora(k.get(n + "datetime")) \
                        or _hora(k.get(n.replace("airport", "").replace("station", "") + "date"))
                return codigo, hora
    return None, None


def _horas_soltas(seg: dict, lado: str) -> datetime | None:
    k = _chaves(seg)
    for nome in (f"{lado}datetime", f"{lado}datetimelocal", f"{lado}localdatetime", f"{lado}time", f"{lado}date",
                 "std" if lado == "departure" else "sta"):
        h = _hora(k.get(nome))
        if h:
            return h
    return None


def _voo(seg: dict) -> dict | None:
    origem, partida = _lado(seg, CHAVES_SAIDA)
    destino, chegada = _lado(seg, CHAVES_CHEGADA)
    partida = partida or _horas_soltas(seg, "departure")
    chegada = chegada or _horas_soltas(seg, "arrival")
    if not (origem and destino and partida) or origem == destino:
        return None
    numero = _primeiro(seg, CHAVES_NUMERO)
    cia = _primeiro(seg, CHAVES_CIA)
    if isinstance(numero, dict):   # {"carrierCode": "LA", "flightNumber": "3456"}
        cia = cia or _primeiro(numero, ("carriercode", "airlinecode", "carrier", "marketingcarrier"))
        numero = _primeiro(numero, ("flightnumber", "number", "numero")) or None
    if isinstance(cia, dict):
        cia = _primeiro(cia, ("code", "iatacode", "iata"))
    numero = str(numero).strip() if numero not in (None, "") else ""
    sigla = cia.strip().upper() if isinstance(cia, str) and len(cia.strip()) == 2 else ""
    if numero.isdigit() and sigla:   # "3456" + "LA" → "LA3456"
        numero = sigla + numero
    operadora = _primeiro(seg, CHAVES_OPERADORA)
    if isinstance(operadora, dict):
        operadora = _primeiro(operadora, ("name", "nome", "code"))
    cabine = _primeiro(seg, CHAVES_CABINE)
    if isinstance(cabine, dict):
        cabine = _primeiro(cabine, ("name", "nome", "code"))
    return {"numero": numero.upper().replace(" ", "") or None, "origem": origem, "destino": destino,
            "partida": partida.strftime("%Y-%m-%dT%H:%M"),
            "chegada": chegada.strftime("%Y-%m-%dT%H:%M") if chegada else None,
            "operado_por": str(operadora)[:60] if isinstance(operadora, str) else None,
            "cabine": str(cabine)[:30] if isinstance(cabine, str) else None}


def _passageiro(d: dict) -> dict | None:
    k = _chaves(d)
    nome, sobrenome = _primeiro(d, PRIMEIRO_NOME), _primeiro(d, SOBRENOME)
    if isinstance(k.get("name"), dict):
        nome = nome or _primeiro(k["name"], PRIMEIRO_NOME)
        sobrenome = sobrenome or _primeiro(k["name"], SOBRENOME)
    if not (isinstance(nome, str) and isinstance(sobrenome, str)):
        return None
    if not (1 < len(nome) < 60 and 1 < len(sobrenome) < 60) or any(ch.isdigit() for ch in nome + sobrenome):
        return None
    bilhete = _primeiro(d, CHAVES_BILHETE)
    if isinstance(bilhete, (dict, list)):
        bilhete = None
    if not bilhete:   # o número do bilhete às vezes vem dentro de "tickets": [{"ticketNumber": ...}]
        for valor in d.values():
            for item in (valor if isinstance(valor, list) else [valor]):
                if isinstance(item, dict):
                    achado = _primeiro(item, CHAVES_BILHETE)
                    if isinstance(achado, (str, int)):
                        bilhete = achado
                        break
            if bilhete:
                break
    bilhete = re.sub(r"\D", "", str(bilhete)) if bilhete else ""
    return {"nome": f"{nome.strip()} {sobrenome.strip()}".upper(), "bilhete": bilhete if len(bilhete) >= 10 else None}


def _andar(obj, voos: list, pessoas: list, profundidade: int = 0) -> None:
    if profundidade > 40:
        return
    if isinstance(obj, dict):
        v = _voo(obj)
        if v:
            voos.append(v)
        p = _passageiro(obj)
        if p:
            pessoas.append(p)
        for valor in obj.values():
            _andar(valor, voos, pessoas, profundidade + 1)
    elif isinstance(obj, list):
        for item in obj:
            _andar(item, voos, pessoas, profundidade + 1)


def montar_reserva(respostas: list, origem_pedida: str | None = None) -> dict | None:
    """Junta voos e passageiros achados em todas as respostas. Devolve None se não achou nenhum voo."""
    voos, pessoas = [], []
    for r in respostas:
        _andar(r, voos, pessoas)
    unicos = {}
    for v in voos:
        chave = (v["origem"], v["destino"], v["partida"])
        atual = unicos.get(chave)
        # fica com a versão mais completa do mesmo voo (algumas respostas trazem menos campos)
        if atual is None or sum(1 for x in v.values() if x) > sum(1 for x in atual.values() if x):
            unicos[chave] = v
    lista = sorted(unicos.values(), key=lambda v: v["partida"])
    if not lista:
        return None
    # trechos: voos seguidos (sai de onde o anterior chegou, até 24h depois) formam um trecho com conexão
    trechos, atual = [], []
    for v in lista:
        if atual:
            anterior = atual[-1]
            chegou = datetime.fromisoformat(anterior["chegada"] or anterior["partida"])
            sai = datetime.fromisoformat(v["partida"])
            if v["origem"] == anterior["destino"] and timedelta(0) <= sai - chegou <= timedelta(hours=24):
                atual.append(v)
                continue
            trechos.append(atual)
        atual = [v]
    trechos.append(atual)
    if origem_pedida:   # começa pelo trecho que sai da origem informada
        for i, t in enumerate(trechos):
            if t[0]["origem"] == origem_pedida.upper():
                trechos = trechos[i:] + trechos[:i]
                break
    nomes, passageiros = set(), []
    for p in pessoas:
        if p["nome"] not in nomes:
            nomes.add(p["nome"])
            passageiros.append(p)
        elif p["bilhete"]:
            next(x for x in passageiros if x["nome"] == p["nome"])["bilhete"] = p["bilhete"]
    return {"situacao": "confirmada",
            "trechos": [{"tipo": "ida" if i == 0 else "volta", "voos": t} for i, t in enumerate(trechos)],
            "passageiros": passageiros[:9]}
