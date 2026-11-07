"""Gera no servidor a "Imagem com a logo" de uma promoção (o mesmo desenho do botão da fila, em cartao.js).

Usada quando o modelo da Meta tem cabeçalho de imagem e a promoção não tem imagem própria (ex.: Google Flights):
a Meta exige uma imagem em todo envio desse modelo. Fontes Barlow em app/fontes_imagem (OFL, as mesmas do site)."""
import io
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import aeroportos
from .models import Promocao

PASTA = Path(__file__).parent
AZUL, AZUL_ESCURO, LARANJA, BRANCO = (27, 70, 143), (18, 50, 102), (255, 145, 77), (255, 255, 255)
CINZA, CINZA_LINHA = (90, 103, 130), (227, 232, 242)
L = 1080
AVIAO = [(5, 0), (1.5, -1), (-1.5, -4.2), (-3, -4.2), (-1.3, -1), (-4, -.6), (-5.2, -2), (-6.2, -2), (-5.5, 0),
         (-6.2, 2), (-5.2, 2), (-4, .6), (-1.3, 1), (-3, 4.2), (-1.5, 4.2), (1.5, 1)]


@lru_cache(maxsize=32)
def _fonte(nome: str, tamanho: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(PASTA / "fontes_imagem" / f"{nome}.ttf"), tamanho)
    except OSError:
        return ImageFont.load_default(tamanho)


def _caber(d: ImageDraw.ImageDraw, texto: str, largura: int, tamanho: int, nome: str) -> ImageFont.FreeTypeFont:
    while tamanho > 18 and d.textlength(texto, font=_fonte(nome, tamanho)) > largura:
        tamanho -= 2
    return _fonte(nome, tamanho)


def _curva(p0, p1, p2, passos=60):
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
             (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]) for t in (i / passos for i in range(passos + 1))]


def _linha_grossa(d: ImageDraw.ImageDraw, pontos, cor, largura: int) -> None:
    d.line(pontos, fill=cor, width=largura, joint="curve")
    r = largura / 2
    for x, y in (pontos[0], pontos[-1]):
        d.ellipse((x - r, y - r, x + r, y + r), fill=cor)


def gerar(p: Promocao) -> bytes:
    """JPEG 1080×1080: fundo azul com arcos de rota, logo, cartão branco com o trecho no arco de voo e o valor."""
    from .servicos import TIPOS_VIAGEM, formatar_periodo, formatar_valor

    escala = 2   # desenha em 2x e reduz: bordas lisas
    img = Image.new("RGB", (L * escala, L * escala), AZUL)
    d = ImageDraw.Draw(img)
    s = lambda v: v * escala  # noqa: E731

    # arcos de rota tênues no fundo (laranja sobre o azul)
    fundo = Image.new("RGBA", img.size, (0, 0, 0, 0))
    df = ImageDraw.Draw(fundo)
    for (a, b, c), alfa in ((((-40, 820), (420, 380), (1120, 700)), 56), (((-40, 960), (520, 560), (1120, 900)), 31)):
        df.line([(s(x), s(y)) for x, y in _curva(a, b, c)], fill=LARANJA + (alfa,), width=s(3), joint="curve")
    img.paste(fundo, (0, 0), fundo)
    d = ImageDraw.Draw(img)

    # logo e "oferta encontrada"
    try:
        logo = Image.open(PASTA / "static" / "logo.png").convert("RGBA")
        w = s(420)
        logo = logo.resize((w, round(w * logo.height / logo.width)), Image.LANCZOS)
        img.paste(logo, (s(70), s(50)), logo)
    except OSError:
        pass
    d.text((s(L - 70), s(132)), "OFERTA ENCONTRADA", font=_fonte("barlow-condensed-700", s(28)), fill=LARANJA, anchor="rs")

    # cartão branco com sombra suave
    cx, cy, cw, ch = 70, 270, L - 140, 560
    sombra = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sombra).rounded_rectangle((s(cx), s(cy + 12), s(cx + cw), s(cy + ch + 12)), s(36), fill=(0, 0, 0, 64))
    sombra = sombra.filter(ImageFilter.GaussianBlur(s(20)))
    img.paste(sombra, (0, 0), sombra)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((s(cx), s(cy), s(cx + cw), s(cy + ch)), s(36), fill=BRANCO)

    meio = L / 2
    origem, destino = (p.origem or "").upper(), (p.destino or "").upper()
    if p.passagem and origem and destino:
        codigo = _fonte("barlow-condensed-700", s(120))
        d.text((s(meio - 110), s(cy + 150)), origem, font=codigo, fill=AZUL, anchor="rs")
        d.text((s(meio + 110), s(cy + 150)), destino, font=codigo, fill=AZUL, anchor="ls")
        a0, a1, ay, acy = (meio - 92, cy + 128), (meio + 92, cy + 128), cy + 128, cy + 40
        _linha_grossa(d, [(s(x), s(y)) for x, y in _curva(a0, (meio, acy), a1)], LARANJA, s(7))
        for x, y in (a0, a1):
            d.ellipse((s(x - 8), s(y - 8), s(x + 8), s(y + 8)), fill=AZUL)
        # avião a 52% do caminho, na direção da curva
        t, u = .52, .48
        px, py = u * u * a0[0] + 2 * u * t * meio + t * t * a1[0], u * u * ay + 2 * u * t * acy + t * t * ay
        ang = math.atan2(2 * u * (acy - ay) + 2 * t * (ay - acy), 2 * u * (meio - a0[0]) + 2 * t * (a1[0] - meio))
        e = 5.2
        d.polygon([(s(px + e * (x * math.cos(ang) - y * math.sin(ang))), s(py + e * (x * math.sin(ang) + y * math.cos(ang))))
                   for x, y in AVIAO], fill=AZUL)
        cidades = f"{aeroportos.nome_curto(origem)}  –  {aeroportos.nome_curto(destino)}"   # a fonte não tem a seta
    else:
        titulo = p.titulo or p.lugar or (p.destino and aeroportos.nome_curto(p.destino)) or "Promoção"
        d.text((s(meio), s(cy + 150)), titulo, font=_caber(d, titulo, s(cw - 80), s(84), "barlow-condensed-700"),
               fill=AZUL, anchor="ms")
        cidades = p.lugar or ""
    if cidades:
        d.text((s(meio), s(cy + 205)), cidades, font=_caber(d, cidades, s(cw - 80), s(36), "barlow-600"), fill=CINZA, anchor="ms")

    # linha tracejada
    x = cx + 50
    while x < cx + cw - 50:
        d.line((s(x), s(cy + 250), s(min(x + 14, cx + cw - 50)), s(cy + 250)), fill=CINZA_LINHA, width=s(3))
        x += 26

    tipo = TIPOS_VIAGEM.get(p.tipo_viagem or "", "")
    rotulo = f"{tipo.upper()} · A PARTIR DE" if tipo else "A PARTIR DE"
    d.text((s(meio), s(cy + 315)), rotulo, font=_fonte("barlow-condensed-700", s(28)), fill=CINZA, anchor="ms")
    valor = formatar_valor(p)
    d.text((s(meio), s(cy + 410)), valor, font=_caber(d, valor, s(cw - 100), s(112), "barlow-condensed-700"),
           fill=LARANJA, anchor="ms")
    info = "  ·  ".join(x for x in (formatar_periodo(p.data_inicio, p.data_fim, p.datas_texto), p.companhia) if x)
    if info:
        d.text((s(meio), s(cy + 490)), info, font=_caber(d, info, s(cw - 80), s(34), "barlow-600"), fill=AZUL_ESCURO, anchor="ms")

    d.text((s(meio), s(935)), "Responda a mensagem e garanta a sua viagem", font=_fonte("barlow-700", s(38)), fill=BRANCO, anchor="ms")
    d.text((s(meio), s(990)), "Valores sujeitos a disponibilidade e alteração", font=_fonte("barlow-500", s(24)),
           fill=(200, 210, 228), anchor="ms")

    saida = io.BytesIO()
    img.resize((L, L), Image.LANCZOS).save(saida, "JPEG", quality=88, optimize=True)
    return saida.getvalue()
