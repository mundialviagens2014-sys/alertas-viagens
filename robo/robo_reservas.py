"""Robô de reservas do MVO.

Roda num computador da agência (Windows ou Mac). Pergunta ao MVO se há pedido de bilhete, abre o site da companhia
(LATAM, GOL ou Azul) no Chrome deste computador, consulta a reserva com localizador + sobrenome, lê os voos e
passageiros e devolve para o MVO, que gera o bilhete em PDF.

Uso:
    python robo_reservas.py --configurar     # endereço do MVO e token (Configurações → Robô de reservas)
    python robo_reservas.py                  # liga o robô (deixe a janela aberta)
    python robo_reservas.py --aprender LA ABC123 SOBRENOME
        # abre o site, você consulta na mão, e o robô guarda o que o site recebeu (pasta capturas/) para ajustar
        # a leitura daquela companhia. ATENÇÃO: o arquivo tem os dados da reserva; trate como documento do cliente.

Regras que o robô segue:
- se o site pedir captcha / "confirme que você não é um robô" / bloquear o acesso, ele NÃO tenta passar:
  devolve "Precisa de uma pessoa" para o MVO e segue para o próximo pedido;
- espera alguns segundos entre consultas, para não sobrecarregar o site;
- não guarda senha de ninguém: só localizador e sobrenome, que vêm do MVO.
"""
import argparse
import json
import platform
import random
import sys
import time
from pathlib import Path

import httpx

from companhias import ADAPTADORES
from leitura import montar_reserva

VERSAO = "1.0"
PASTA = Path(__file__).resolve().parent
CONFIG = PASTA / "robo.json"
PERFIL = PASTA / "perfil-chrome"      # cookies e preferências do Chrome do robô (fica só neste computador)
CAPTURAS = PASTA / "capturas"
INTERVALO = 5                          # segundos entre perguntas ao MVO quando a fila está vazia
PAUSA_ENTRE_CONSULTAS = (8, 15)        # segundos, sorteado: não martela o site da companhia


def ler_config() -> dict:
    if not CONFIG.exists():
        sys.exit("Rode primeiro:  python robo_reservas.py --configurar")
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def configurar() -> None:
    print("Configuração do robô de reservas do MVO\n")
    url = input("Endereço do MVO (ex.: https://mvo.mundialviagens.com): ").strip().rstrip("/")
    token = input("Token do robô (MVO → Configurações → Robô de reservas): ").strip()
    visivel = input("Mostrar a janela do Chrome enquanto consulta? [S/n]: ").strip().lower() != "n"
    CONFIG.write_text(json.dumps({"mvo": url, "token": token, "visivel": visivel}, indent=2), encoding="utf-8")
    try:
        r = cliente_mvo({"mvo": url, "token": token}).post("/api/robo/ping")
        r.raise_for_status()
        print("\nPronto! O MVO respondeu. Agora abra iniciar.bat (ou: python robo_reservas.py).")
    except httpx.HTTPError as e:
        print(f"\nSalvei, mas o MVO não aceitou ({e}). Confira o endereço e gere outro token se precisar.")


def cliente_mvo(conf: dict) -> httpx.Client:
    return httpx.Client(base_url=conf["mvo"], timeout=30, headers={
        "Authorization": f"Bearer {conf['token']}", "X-Robo-Versao": VERSAO,
        "X-Robo-Maquina": platform.node()[:60] or "robo"})


def abrir_navegador(pw, visivel: bool):
    PERFIL.mkdir(exist_ok=True)
    opcoes = {"headless": not visivel, "locale": "pt-BR", "timezone_id": "America/Fortaleza",
              "viewport": {"width": 1280, "height": 900}}
    try:   # o Chrome instalado no computador (o mesmo que vocês usam)
        return pw.chromium.launch_persistent_context(str(PERFIL), channel="chrome", **opcoes)
    except Exception:  # noqa: BLE001  sem Chrome instalado: o Chromium que o Playwright baixou
        return pw.chromium.launch_persistent_context(str(PERFIL), **opcoes)


def consultar(contexto, pedido: dict) -> dict:
    """Uma consulta: devolve o corpo que vai para o MVO ({"status": ..., "dados"/"mensagem": ...})."""
    adaptador = ADAPTADORES.get(pedido["companhia"])
    if not adaptador:
        return {"status": "erro", "mensagem": f"Companhia {pedido['companhia']} sem leitura no robô."}
    pagina = contexto.new_page()
    respostas: list = []

    def guardar(resp):
        try:
            if "json" in (resp.headers.get("content-type") or "") and resp.request.resource_type in ("xhr", "fetch"):
                respostas.append(resp.json())
        except Exception:  # noqa: BLE001  resposta vazia ou cortada: ignora
            pass

    pagina.on("response", guardar)
    try:
        resultado = adaptador.consultar(pagina, pedido, respostas)
        if resultado:   # o adaptador já decidiu (captcha, não encontrada...)
            return resultado
        reserva = montar_reserva(respostas, pedido.get("origem"))
        if not reserva:
            salvar_captura(pedido, respostas, pagina)
            return {"status": "erro", "mensagem": "Abri a reserva mas não consegui ler os voos. Guardei uma "
                                                  "captura no computador do robô para ajustarmos a leitura."}
        if not reserva["passageiros"]:
            reserva["passageiros"] = [{"nome": pedido["sobrenome"].upper()}]
        return {"status": "pronta", "dados": reserva}
    except Exception as e:  # noqa: BLE001
        return {"status": "erro", "mensagem": f"{type(e).__name__}: {str(e)[:300]}"}
    finally:
        pagina.close()


def salvar_captura(pedido: dict, respostas: list, pagina=None) -> Path:
    CAPTURAS.mkdir(exist_ok=True)
    arquivo = CAPTURAS / f"{pedido['companhia']}_{pedido['localizador']}_{time.strftime('%Y%m%d-%H%M%S')}.json"
    texto = ""
    try:
        texto = pagina.inner_text("body")[:20000] if pagina else ""
    except Exception:  # noqa: BLE001
        pass
    arquivo.write_text(json.dumps({"pedido": pedido, "url": pagina.url if pagina else None, "texto_da_tela": texto,
                                   "respostas": respostas}, ensure_ascii=False, indent=1), encoding="utf-8")
    return arquivo


def ligar() -> None:
    from playwright.sync_api import sync_playwright

    conf = ler_config()
    mvo = cliente_mvo(conf)
    print(f"Robô de reservas {VERSAO} ligado. Deixe esta janela aberta. (Ctrl+C para desligar)")
    with sync_playwright() as pw:
        contexto = abrir_navegador(pw, conf.get("visivel", True))
        try:
            while True:
                try:
                    r = mvo.post("/api/robo/proximo")
                    if r.status_code == 401:
                        print("O MVO recusou o token. Gere outro em Configurações e rode configurar.bat.")
                        time.sleep(60)
                        continue
                    r.raise_for_status()
                except httpx.HTTPError as e:
                    print(f"Sem resposta do MVO ({e}). Tentando de novo em 30s.")
                    time.sleep(30)
                    continue
                if r.status_code == 204:
                    time.sleep(INTERVALO)
                    continue
                pedido = r.json()
                print(f"→ {pedido['companhia']} {pedido['localizador']} ({pedido['sobrenome']})… ", end="", flush=True)
                corpo = consultar(contexto, pedido)
                print(corpo["status"], corpo.get("mensagem") or "")
                try:
                    mvo.post(f"/api/robo/consultas/{pedido['id']}", json=corpo).raise_for_status()
                except httpx.HTTPError as e:
                    print(f"   não consegui devolver ao MVO: {e}")
                time.sleep(random.uniform(*PAUSA_ENTRE_CONSULTAS))
        except KeyboardInterrupt:
            print("\nRobô desligado.")
        finally:
            contexto.close()


def aprender(companhia: str, localizador: str, sobrenome: str) -> None:
    """Abre a página da companhia; você consulta na mão; o robô guarda tudo o que o site recebeu."""
    from playwright.sync_api import sync_playwright

    pedido = {"companhia": companhia.upper(), "localizador": localizador.upper(), "sobrenome": sobrenome,
              "origem": None}
    adaptador = ADAPTADORES.get(pedido["companhia"])
    with sync_playwright() as pw:
        contexto = abrir_navegador(pw, True)
        pagina = contexto.new_page()
        respostas: list = []
        pagina.on("response", lambda r: _guardar_aprendendo(r, respostas))
        pagina.goto(adaptador.PAGINA if adaptador else "about:blank")
        input("Consulte a reserva na janela do Chrome. Quando os voos aparecerem, volte aqui e aperte Enter… ")
        arquivo = salvar_captura(pedido, respostas, pagina)
        reserva = montar_reserva(respostas)
        print(f"\nCaptura salva em: {arquivo}")
        print("Leitura automática:", json.dumps(reserva, ensure_ascii=False, indent=1) if reserva else "nada encontrado")
        contexto.close()


def _guardar_aprendendo(resp, respostas: list) -> None:
    try:
        if "json" in (resp.headers.get("content-type") or ""):
            respostas.append({"url": resp.url, "corpo": resp.json()})
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    args = argparse.ArgumentParser(description="Robô de reservas do MVO")
    args.add_argument("--configurar", action="store_true")
    args.add_argument("--aprender", nargs=3, metavar=("COMPANHIA", "LOCALIZADOR", "SOBRENOME"))
    a = args.parse_args()
    if a.configurar:
        configurar()
    elif a.aprender:
        aprender(*a.aprender)
    else:
        ligar()
