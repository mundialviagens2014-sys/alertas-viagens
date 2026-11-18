"""MVO como aplicativo (PWA): manifesto, service worker e página "sem internet".

Com isso o navegador oferece "Instalar app" (Android, Chrome/Edge no computador) e o iPhone aceita
"Adicionar à Tela de Início": o MVO abre em tela cheia, com ícone próprio, sem barra de endereço.
Nada aqui exige login, porque o navegador busca estes três endereços antes de existir uma sessão.

O service worker é de propósito simples: guarda só arquivos de /static (estilo, fontes, ícones) e a página
"sem internet". As páginas do sistema sempre vêm do servidor e nunca ficam guardadas no aparelho,
porque têm dados de clientes.
"""
import json

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

from ..config import settings
from ..web import _versao_estaticos

rotas = APIRouter()

AZUL_NOITE = "#0b1f4b"

FONTES_ESSENCIAIS = (
    "geist-sans-latin-400-normal.woff2",
    "geist-sans-latin-500-normal.woff2",
    "geist-sans-latin-600-normal.woff2",
    "plus-jakarta-sans-latin-700-normal.woff2",
    "barlow-condensed-latin-700-normal.woff2",
)


def manifesto() -> dict:
    icones = [{"src": f"/static/app/icone-{t}.png", "sizes": f"{t}x{t}", "type": "image/png", "purpose": "any"}
              for t in (192, 512)]
    icones += [{"src": f"/static/app/icone-maskable-{t}.png", "sizes": f"{t}x{t}", "type": "image/png",
                "purpose": "maskable"} for t in (192, 512)]
    atalho = lambda nome, url: {"name": nome, "short_name": nome, "url": url,  # noqa: E731
                                "icons": [{"src": "/static/app/icone-192.png", "sizes": "192x192"}]}
    return {
        "id": "/",
        "name": f"MVO · {settings.agencia_nome}",
        "short_name": "MVO",
        "description": "Alertas de promoções, Manager de milhas, CRM e relatórios da agência.",
        "lang": "pt-BR",
        "dir": "ltr",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": AZUL_NOITE,
        "theme_color": AZUL_NOITE,
        "categories": ["business", "productivity", "travel"],
        "icons": icones,
        # tocar e segurar o ícone (Android) ou clicar com o botão direito (computador)
        "shortcuts": [atalho("Promoções na fila", "/"), atalho("Funil de vendas", "/crm"),
                      atalho("Respostas do WhatsApp", "/whatsapp/respostas")],
    }


@rotas.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return Response(json.dumps(manifesto(), ensure_ascii=False), media_type="application/manifest+json",
                    headers={"Cache-Control": "public, max-age=3600"})


SERVICE_WORKER = r"""// MVO — service worker (gerado em app/rotas/aplicativo.py)
const VERSAO = "__VERSAO__";
const CACHE = "mvo-" + VERSAO;
const ESSENCIAIS = __ESSENCIAIS__;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ESSENCIAIS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    for (const nome of await caches.keys()) if (nome !== CACHE) await caches.delete(nome);  // versão antiga
    if (self.registration.navigationPreload) await self.registration.navigationPreload.enable();
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  // páginas: sempre do servidor (têm dados de clientes, nunca ficam guardadas); sem internet, a página de aviso
  if (req.mode === "navigate") {
    e.respondWith((async () => {
      try {
        const adiantada = await e.preloadResponse;
        return adiantada || await fetch(req);
      } catch (erro) {
        return (await caches.match("/offline")) || Response.error();
      }
    })());
    return;
  }

  // estilo, fontes, ícones: do aparelho primeiro (abre na hora); o que faltar vem do servidor e fica guardado
  if (url.pathname.startsWith("/static/")) {
    e.respondWith((async () => {
      const guardado = await caches.match(req);
      if (guardado) return guardado;
      const resposta = await fetch(req);
      if (resposta.ok && resposta.type === "basic") {
        const copia = resposta.clone();
        caches.open(CACHE).then((c) => c.put(req, copia));
      }
      return resposta;
    })());
  }
});
"""


@rotas.get("/sw.js", include_in_schema=False)
def service_worker():
    v = _versao_estaticos()
    essenciais = ["/offline", f"/static/estilo.css?v={v}", f"/static/movimento.js?v={v}",
                  f"/static/logo-claro.png?v={v}", "/static/app/icone-192.png", "/static/favicon.png"]
    essenciais += [f"/static/fontes/{f}" for f in FONTES_ESSENCIAIS]
    corpo = SERVICE_WORKER.replace("__VERSAO__", v).replace("__ESSENCIAIS__", json.dumps(essenciais))
    # sem cache: o navegador confere a cada abertura se há versão nova do MVO
    return Response(corpo, media_type="text/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


OFFLINE = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0b1f4b">
<title>Sem internet · MVO</title>
<style>
  html, body { height: 100%; margin: 0; }
  body { display: grid; place-items: center; padding: 1.5rem; box-sizing: border-box; background: #0b1f4b; color: #dbe5f5;
         font: 400 1rem/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; text-align: center; }
  main { max-width: 22rem; }
  img { width: 72px; height: 72px; border-radius: 18px; }
  h1 { margin: 1rem 0 .4rem; font-size: 1.3rem; color: #fff; }
  p { margin: 0 0 1.4rem; color: #afc1e3; }
  button { font-family: inherit; font-weight: 600; font-size: .95rem; line-height: 1; padding: .8rem 1.4rem; border: 0; border-radius: 10px; background: #ff914d; color: #0b1f4b;
           cursor: pointer; }
  button:active { transform: scale(.97); }
</style>
</head>
<body>
<main>
  <img src="/static/app/icone-192.png" alt="">
  <h1>Sem conexão com a internet</h1>
  <p>O MVO precisa de internet para mostrar promoções, clientes e conversas. Assim que a conexão voltar, a página abre sozinha.</p>
  <button type="button" onclick="location.reload()">Tentar de novo</button>
</main>
<script>addEventListener('online', () => location.reload());</script>
</body>
</html>
"""


@rotas.get("/offline", include_in_schema=False, response_class=HTMLResponse)
def offline():
    return HTMLResponse(OFFLINE)
