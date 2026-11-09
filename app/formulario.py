"""Sincronização automática com a planilha de respostas do Google Forms.

O sistema roda no computador da agência, então é ele quem busca a planilha no Google
de tempos em tempos. Duas formas de ligar:

1. Recomendada (privada): um pequeno script colado na planilha (Extensões → Apps Script)
   publica as respostas num endereço que só responde com a chave secreta da agência.
   A tela "Clientes → Formulário automático" mostra o script já com a chave.
2. Alternativa: link de compartilhamento da planilha ("qualquer pessoa com o link").
   Mais simples, mas quem tiver o link vê os telefones dos clientes.

Só respostas novas são importadas (pelo "Carimbo de data/hora" do Forms). Clientes já
cadastrados (mesmo telefone) só ganham os trechos novos.

Vários formulários: cada um tem um número (1, 2, 3...) e guarda o próprio endereço e o controle das respostas
já lidas. O formulário 1 usa as chaves de antes ("formulario:url"...); os outros, "formulario2:url" etc. Todos
usam o mesmo script (a mesma chave secreta).
"""
import json
import re
import secrets
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from . import importacao
from .servicos import gravar_estado, ler_estado

CHAVE_URL = "formulario:url"
CHAVE_SEGREDO = "formulario:chave"
CHAVE_CARIMBO = "formulario:ultimo_carimbo"
CHAVE_LINHAS = "formulario:linhas_lidas"
CHAVE_RESUMO = "formulario:ultimo_resumo"
CHAVE_LISTA = "formularios:numeros"   # números dos formulários além do 1 (ex.: "2,3")
MAXIMO = 10


def _chave(n: int, nome: str) -> str:
    """Chave de estado do formulário n: o 1 usa as de antes (formulario:url), os outros formulario2:url..."""
    return f"formulario:{nome}" if n == 1 else f"formulario{n}:{nome}"


def numeros(db: Session) -> list[int]:
    extras = [int(x) for x in (ler_estado(db, CHAVE_LISTA) or "").split(",") if x.strip().isdigit()]
    return [1] + sorted(set(x for x in extras if x > 1))


def formularios(db: Session) -> list[dict]:
    """Formulários com endereço salvo: número, nome, endereço e o resumo da última leitura."""
    saida = []
    for n in numeros(db):
        url = ler_estado(db, _chave(n, "url"))
        if url:
            saida.append({"n": n, "nome": ler_estado(db, _chave(n, "nome")) or f"Formulário {n}", "url": url,
                          "resumo": ultimo_resumo(db, n)})
    return saida


def configurado(db: Session) -> bool:
    return any(ler_estado(db, _chave(n, "url")) for n in numeros(db))

FORMATOS_CARIMBO = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


class FormularioNaoConfigurado(RuntimeError):
    pass


def chave_secreta(db: Session) -> str:
    chave = ler_estado(db, CHAVE_SEGREDO)
    if not chave:
        chave = secrets.token_urlsafe(24)
        gravar_estado(db, CHAVE_SEGREDO, chave)
    return chave


def script_apps(chave: str) -> str:
    """Código para colar em Extensões → Apps Script da planilha de respostas."""
    return f"""const CHAVE = '{chave}';

function doGet(e) {{
  if (!e || !e.parameter || e.parameter.chave !== CHAVE) {{
    return ContentService.createTextOutput('acesso negado');
  }}
  const planilha = SpreadsheetApp.getActiveSpreadsheet();
  const abas = planilha.getSheets();
  // a aba de respostas do Forms começa com "Carimbo de data/hora" (ou "Timestamp")
  const aba = abas.find(a => /^(carimbo|timestamp)/i.test(String(a.getRange(1, 1).getDisplayValue()).trim()))
    || abas.find(a => /^(respostas|form responses)/i.test(a.getName())) || abas[0];
  const dados = aba.getDataRange().getDisplayValues();
  const csv = dados.map(l => l.map(c => '"' + String(c).replace(/"/g, '""') + '"').join(',')).join('\\n');
  return ContentService.createTextOutput(csv).setMimeType(ContentService.MimeType.CSV);
}}
"""


def endereco_download(url: str, chave: str) -> str:
    """Transforma o endereço colado pela agência no endereço que devolve a planilha em CSV."""
    url = url.strip()
    m = re.match(r"https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)", url)
    if m:  # link de compartilhamento da planilha
        gid = re.search(r"[#&?]gid=(\d+)", url)
        return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv" + (f"&gid={gid.group(1)}" if gid else "")
    if "script.google.com/macros/" in url:  # app da Web do Apps Script
        separador = "&" if "?" in url else "?"
        return f"{url}{separador}chave={chave}" if "chave=" not in url else url
    return url


def validar_url(url: str) -> str | None:
    """Mensagem de erro, ou None se o endereço parece certo."""
    url = url.strip()
    if re.match(r"https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec", url):
        return None
    if re.match(r"https://docs\.google\.com/spreadsheets/d/[A-Za-z0-9_-]+", url):
        return None
    if "script.google.com/macros" in url and url.rstrip("/").endswith("/dev"):
        return "Esse é o endereço de teste (termina em /dev). Use o endereço do app da Web, que termina em /exec."
    if "docs.google.com/forms" in url:
        return "Esse é o link do formulário. Cole o endereço da planilha de respostas ou do script."
    return "Endereço não reconhecido. Cole o endereço do app da Web (termina em /exec) ou o link da planilha."


def baixar(url: str, chave: str, cliente_http: httpx.Client | None = None) -> bytes:
    proprio = cliente_http is None
    cliente_http = cliente_http or httpx.Client(follow_redirects=True, timeout=60)
    try:
        r = cliente_http.get(endereco_download(url, chave))
    except httpx.HTTPError as erro:
        raise RuntimeError(f"Não consegui acessar a planilha: {erro}") from erro
    finally:
        if proprio:
            cliente_http.close()
    corpo = r.content
    inicio = corpo[:300].decode("utf-8", "ignore").lower()
    if "acesso negado" in inicio:
        raise RuntimeError("A planilha recusou a chave. Copie o script de novo nesta tela e reimplante.")
    if r.status_code in (401, 403) or "<html" in inicio or "accounts.google.com" in str(r.url):
        raise RuntimeError("O Google pediu login. No script, a implantação precisa estar com acesso “Qualquer pessoa”; "
                           "no link da planilha, o compartilhamento precisa ser “Qualquer pessoa com o link”.")
    if r.status_code >= 400:
        raise RuntimeError(f"O Google respondeu com erro {r.status_code}.")
    return corpo


def _carimbo(valor) -> datetime | None:
    if isinstance(valor, datetime):
        return valor
    texto = str(valor or "").strip()
    for formato in FORMATOS_CARIMBO:
        try:
            return datetime.strptime(texto, formato)
        except ValueError:
            continue
    return None


SOMAR = ("respostas", "novas", "novos", "atualizados", "interesses", "erros", "matches", "confirmacoes")


def sincronizar(db: Session, cliente_http: httpx.Client | None = None) -> dict:
    """Busca as planilhas de todos os formulários ligados e importa as respostas novas. Devolve a soma de todos e,
    em "formularios", o resumo de cada um (um formulário com erro não impede os outros)."""
    lista = formularios(db)
    if not lista:
        raise FormularioNaoConfigurado("Formulário automático não configurado (Clientes → Formulário automático).")
    total = {k: 0 for k in SOMAR} | {"linhas_com_erro": [], "formularios": []}
    erros = []
    for f in lista:
        try:
            r = sincronizar_um(db, f["n"], cliente_http)
        except RuntimeError as erro:
            erros.append(f"{f['nome']}: {erro}")
            gravar_estado(db, _chave(f["n"], "ultimo_resumo"), json.dumps(
                {"erro": str(erro), "em": datetime.now().strftime("%d/%m %H:%M")}, ensure_ascii=False))
            total["formularios"].append({"n": f["n"], "nome": f["nome"], "erro": str(erro)})
            continue
        for k in SOMAR:
            total[k] += r.get(k, 0)
        total["linhas_com_erro"] += [f"{f['nome']} · {x}" if len(lista) > 1 else x for x in r["linhas_com_erro"]]
        total["formularios"].append({"n": f["n"], "nome": f["nome"], **r})
    if erros and len(erros) == len(lista):
        raise RuntimeError(" | ".join(erros) if len(erros) > 1 else erros[0].split(": ", 1)[1])
    total["erros_formularios"] = erros
    total["em"] = datetime.now().strftime("%d/%m %H:%M")
    return total


def sincronizar_um(db: Session, n: int, cliente_http: httpx.Client | None = None) -> dict:
    """Busca a planilha do formulário n e importa as respostas que ainda não foram importadas."""
    url = ler_estado(db, _chave(n, "url"))
    if not url:
        raise FormularioNaoConfigurado(f"Formulário {n} não configurado.")
    conteudo = baixar(url, chave_secreta(db), cliente_http)
    cabecalhos, linhas = importacao.ler_planilha(conteudo, "respostas.csv")
    mapa = importacao.mapear_colunas(cabecalhos)
    if "telefone" not in mapa or "nome" not in mapa:
        faltam = " e ".join(n for c, n in (("nome", "nome"), ("telefone", "WhatsApp")) if c not in mapa)
        recebidas = ", ".join(f"“{c[:40]}”" for c in cabecalhos[:8] if c) or "nenhuma (a aba está vazia)"
        raise RuntimeError(f"Não encontrei a coluna de {faltam}. Colunas recebidas da planilha: {recebidas}.")

    # quais respostas são novas: pelo carimbo de data/hora; sem ele, pela quantidade de linhas
    i_carimbo = mapa.get("data_resposta")
    carimbos = [_carimbo(l[i_carimbo]) if i_carimbo is not None and i_carimbo < len(l) else None for l in linhas]
    ultimo_txt = ler_estado(db, _chave(n, "ultimo_carimbo"))
    ultimo = datetime.fromisoformat(ultimo_txt) if ultimo_txt else None
    if carimbos and all(carimbos):
        novas = [l for l, c in zip(linhas, carimbos) if ultimo is None or c > ultimo]
        novo_marco = max(carimbos)
    else:
        ja_lidas = int(ler_estado(db, _chave(n, "linhas_lidas")) or 0)
        novas = linhas[ja_lidas:] if ja_lidas <= len(linhas) else linhas
        novo_marco = None

    resumo = {"respostas": len(linhas), "novas": len(novas), "novos": 0, "atualizados": 0,
              "interesses": 0, "erros": 0, "matches": 0, "confirmacoes": 0, "linhas_com_erro": []}
    if novas:
        _, itens = importacao.interpretar(db, cabecalhos, novas, origem_padrao=None, consentimento_padrao=False)
        # formulário que serve a outra coisa também (ex.: sorteio): quem não escolheu destino nem autorizou promoções
        # pelo WhatsApp não vira cliente dos alertas
        fora = [i for i in itens if i.valida and not i.interesses and not i.consentimento]
        itens = [i for i in itens if i not in fora]
        resumo["sem_interesse"] = len(fora)
        # cada cadastro novo ganha a mensagem de confirmação na fila de envio
        resumo.update(importacao.aplicar(db, itens, confirmar=True))
        resumo["linhas_com_erro"] = [f"{i.nome or 'sem nome'}: {', '.join(i.erros)}" for i in itens if i.erros][:10]

    if novo_marco:
        gravar_estado(db, _chave(n, "ultimo_carimbo"), novo_marco.isoformat())
    gravar_estado(db, _chave(n, "linhas_lidas"), str(len(linhas)))
    resumo["em"] = datetime.now().strftime("%d/%m %H:%M")
    gravar_estado(db, _chave(n, "ultimo_resumo"), json.dumps(resumo, ensure_ascii=False))
    return resumo


def ultimo_resumo(db: Session, n: int = 1) -> dict | None:
    bruto = ler_estado(db, _chave(n, "ultimo_resumo"))
    return json.loads(bruto) if bruto else None


def proximo_numero(db: Session) -> int | None:
    """Número para um formulário novo: o 1 se estiver livre, senão o próximo (None se já há o máximo)."""
    livres = [n for n in numeros(db) if not ler_estado(db, _chave(n, "url"))]
    if livres:
        return livres[0]
    usados = numeros(db)
    return None if len(usados) >= MAXIMO else max(usados) + 1


def configurar(db: Session, url: str | None, n: int = 1, nome: str | None = None) -> None:
    """Salva (ou remove) o endereço do formulário n. Trocar de planilha reinicia o controle de respostas lidas."""
    anterior = ler_estado(db, _chave(n, "url"))
    gravar_estado(db, _chave(n, "url"), (url or "").strip())
    if nome is not None:
        gravar_estado(db, _chave(n, "nome"), nome.strip()[:60])
    if (url or "").strip() != (anterior or ""):
        gravar_estado(db, _chave(n, "ultimo_carimbo"), "")
        gravar_estado(db, _chave(n, "linhas_lidas"), "0")
        gravar_estado(db, _chave(n, "ultimo_resumo"), "")
    if n > 1:
        extras = set(numeros(db)) - {1}
        extras = extras | {n} if (url or "").strip() else extras - {n}
        gravar_estado(db, CHAVE_LISTA, ",".join(str(x) for x in sorted(extras)))
