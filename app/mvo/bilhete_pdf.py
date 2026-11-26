"""PDF do bilhete (comprovante de reserva) com a marca da agência, a partir da reserva lida pelo robô.

É um comprovante da AGÊNCIA: leva o nome da companhia em texto e o link oficial de "Minhas viagens" (e um QR Code
para ele), mas não a logo da companhia, para não parecer um documento emitido por ela.
"""
from datetime import datetime
from io import BytesIO
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from .. import aeroportos
from ..config import settings
from .bilhetes import PAGINA_RESERVA, nome_companhia

NOITE, AZUL, LARANJA = HexColor("#0b1f4b"), HexColor("#1b468f"), HexColor("#f26b1d")
TINTA, TINTA_2, LINHA, PAPEL_2 = HexColor("#13234a"), HexColor("#5a6782"), HexColor("#e0e6ef"), HexColor("#f7f9fc")
VERDE, VERDE_FUNDO = HexColor("#0f6b38"), HexColor("#e3f5e9")
LOGO = Path(__file__).resolve().parent.parent / "static" / "logo.png"

SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]
L, A = A4
M = 36  # margem


def _dt(texto: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(texto) if texto else None
    except ValueError:
        return None


def _dia_extenso(d: datetime | None) -> str:
    return f"{SEMANA[d.weekday()]}, {d.day} de {MESES[d.month - 1]}" if d else ""


def _duracao(inicio: datetime | None, fim: datetime | None) -> str:
    if not inicio or not fim or fim <= inicio:
        return ""
    minutos = int((fim - inicio).total_seconds() // 60)
    h, m = divmod(minutos, 60)
    return f"{h}h{m:02d}" if h else f"{m}min"


def _espaco(c: Canvas, texto: str, x: float, y: float, fonte: str, tam: float, espaco: float = 1.2, direita=False):
    """Texto com letras espaçadas (títulos em caixa alta)."""
    c.setFont(fonte, tam)
    largura = c.stringWidth(texto, fonte, tam) + espaco * max(len(texto) - 1, 0)
    if direita:
        x -= largura
    c.saveState()   # o espaçamento das letras não pode vazar para o resto da página
    obj = c.beginText(x, y)
    obj.setFont(fonte, tam)
    obj.setCharSpace(espaco)
    obj.textOut(texto)
    c.drawText(obj)
    c.restoreState()
    return largura


def _cortar(c: Canvas, texto: str, fonte: str, tam: float, largura: float) -> str:
    if c.stringWidth(texto, fonte, tam) <= largura:
        return texto
    while texto and c.stringWidth(texto + "…", fonte, tam) > largura:
        texto = texto[:-1]
    return texto.rstrip() + "…"


def _aviao(c: Canvas, x: float, y: float, cor=TINTA_2, tam: float = 9):
    """Avião pequeno desenhado (setinha com asas), sem depender de fonte de ícones."""
    c.saveState()
    c.setFillColor(cor)
    c.setStrokeColor(cor)
    p = c.beginPath()
    s = tam / 9
    p.moveTo(x - 4.5 * s, y)
    p.lineTo(x + 4.5 * s, y)
    p.lineTo(x + 2.5 * s, y + 1 * s)
    p.lineTo(x - 4.5 * s, y + 1 * s)
    p.close()
    c.drawPath(p, fill=1, stroke=0)
    asa = c.beginPath()
    asa.moveTo(x - 0.5 * s, y + 1 * s)
    asa.lineTo(x + 1.2 * s, y + 4.2 * s)
    asa.lineTo(x + 2.4 * s, y + 4.2 * s)
    asa.lineTo(x + 1.8 * s, y + 1 * s)
    asa.close()
    c.drawPath(asa, fill=1, stroke=0)
    asa2 = c.beginPath()
    asa2.moveTo(x - 0.5 * s, y)
    asa2.lineTo(x + 1.2 * s, y - 3.2 * s)
    asa2.lineTo(x + 2.4 * s, y - 3.2 * s)
    asa2.lineTo(x + 1.8 * s, y)
    asa2.close()
    c.drawPath(asa2, fill=1, stroke=0)
    c.restoreState()


def _qr(c: Canvas, url: str, x: float, y: float, tam: float) -> None:
    w = QrCodeWidget(url, barLevel="M")
    x0, y0, x1, y1 = w.getBounds()
    d = Drawing(tam, tam, transform=[tam / (x1 - x0), 0, 0, tam / (y1 - y0), 0, 0])
    d.add(w)
    renderPDF.draw(d, c, x, y)


def gerar_pdf(localizador: str, companhia: str, reserva: dict, agencia: str | None = None,
              contato: str | None = None, emitido_em: datetime | None = None) -> bytes:
    agencia = agencia or settings.agencia_nome
    emitido_em = emitido_em or datetime.now()
    cia = nome_companhia(companhia)
    pagina = PAGINA_RESERVA.get(companhia, "")
    buf = BytesIO()
    c = Canvas(buf, pagesize=A4)
    c.setTitle(f"Bilhete {localizador} · {cia}")
    c.setAuthor(agencia)
    c.setSubject("Comprovante de reserva")

    # ---------------- cabeçalho: marca da agência | localizador + QR da página oficial
    topo = A - M
    if LOGO.exists():
        c.drawImage(ImageReader(str(LOGO)), M, topo - 44, width=104, height=44, mask="auto")
    c.setFillColor(TINTA)
    c.setFont("Helvetica-Bold", 13.5)
    c.drawString(M + 116, topo - 20, "COMPROVANTE DE RESERVA")
    c.setFillColor(TINTA_2)
    c.setFont("Helvetica", 8.5)
    c.drawString(M + 116, topo - 34, _cortar(c, f"Voo {cia} · emitido por {agencia}", "Helvetica", 8.5, 200))

    qr_tam = 64
    qr_x = L - M - qr_tam
    caixa_l, caixa_a = 118, 64
    caixa_x = qr_x - 10 - caixa_l
    c.setStrokeColor(LINHA)
    c.setFillColor(white)
    c.roundRect(caixa_x, topo - caixa_a, caixa_l, caixa_a, 10, stroke=1, fill=1)
    c.setFillColor(TINTA_2)
    _espaco(c, "LOCALIZADOR", caixa_x + caixa_l / 2 - 29, topo - 20, "Helvetica-Bold", 7, 1.6)
    c.setFillColor(TINTA)
    c.setFont("Helvetica-Bold", 23)
    c.drawCentredString(caixa_x + caixa_l / 2, topo - 50, localizador)
    if pagina:
        _qr(c, pagina, qr_x, topo - qr_tam, qr_tam)
        c.linkURL(pagina, (qr_x, topo - qr_tam, qr_x + qr_tam, topo), relative=0)
        c.setFillColor(AZUL)
        c.setFont("Helvetica-Bold", 7.5)
        texto = f"Conferir no site da {cia} ›"
        c.drawRightString(L - M, topo - qr_tam - 12, texto)
        lw = c.stringWidth(texto, "Helvetica-Bold", 7.5)
        c.linkURL(pagina, (L - M - lw, topo - qr_tam - 15, L - M, topo - qr_tam - 4), relative=0)

    y = topo - qr_tam - 30
    c.setStrokeColor(LINHA)
    c.line(M, y, L - M, y)

    # ---------------- itinerário
    y -= 26
    c.setFillColor(NOITE)
    c.rect(M, y - 2, 3, 14, stroke=0, fill=1)
    _espaco(c, "ITINERÁRIO DO VOO", M + 12, y, "Helvetica-Bold", 10.5, 2.2)
    c.setFillColor(TINTA_2)
    c.setFont("Helvetica-Oblique", 7.5)
    c.drawRightString(L - M, y + 1, f"Reserva {reserva.get('situacao') or 'confirmada'} na {cia}".upper())
    y -= 14

    for trecho in reserva.get("trechos") or []:
        voos = trecho.get("voos") or []
        if not voos:
            continue
        altura_voo = 58
        conexoes = len(voos) - 1
        altura = 34 + len(voos) * altura_voo + conexoes * 20 + 8
        if y - altura < 150:
            c.showPage()
            y = A - M
        x0, largura = M, L - 2 * M
        # cartão
        c.setFillColor(white)
        c.setStrokeColor(LINHA)
        c.roundRect(x0, y - altura, largura, altura, 12, stroke=1, fill=1)
        # faixa escura do topo
        c.setFillColor(NOITE)
        c.roundRect(x0, y - 34, largura, 34, 12, stroke=0, fill=1)
        c.rect(x0, y - 34, largura, 14, stroke=0, fill=1)
        _aviao(c, x0 + 22, y - 17, white, 11)
        c.setFillColor(white)
        titulo = "TRECHO DE IDA" if trecho.get("tipo") != "volta" else "TRECHO DE VOLTA"
        _espaco(c, titulo, x0 + 38, y - 21, "Helvetica-Bold", 8.5, 1.6)
        primeiro = _dt(voos[0].get("partida"))
        rotulo = "VOO DIRETO" if not conexoes else f"{conexoes} CONEX{'ÃO' if conexoes == 1 else 'ÕES'}"
        c.setFont("Helvetica-Bold", 7.5)
        pill_l = c.stringWidth(rotulo, "Helvetica-Bold", 7.5) + 22
        c.setFillColor(HexColor("#2a3a63"))
        c.roundRect(x0 + largura - 14 - pill_l, y - 25, pill_l, 16, 8, stroke=0, fill=1)
        c.setFillColor(white)
        c.drawCentredString(x0 + largura - 14 - pill_l / 2, y - 20, rotulo)
        _espaco(c, _dia_extenso(primeiro).upper(), x0 + largura - 28 - pill_l, y - 21, "Helvetica-Bold", 8, 1.2,
                direita=True)

        yy = y - 34
        for n, voo in enumerate(voos):
            if n:
                anterior = _dt(voos[n - 1].get("chegada"))
                espera = _duracao(anterior, _dt(voo.get("partida")))
                c.setFillColor(PAPEL_2)
                c.rect(x0 + 1, yy - 20, largura - 2, 20, stroke=0, fill=1)
                c.setFillColor(TINTA_2)
                c.setFont("Helvetica-Bold", 7.5)
                cidade = aeroportos.nome_curto(voo.get("origem")) or voo.get("origem")
                c.drawCentredString(x0 + largura / 2, yy - 13,
                                    f"CONEXÃO EM {cidade.upper()} ({voo.get('origem')})" + (f" · ESPERA DE {espera}" if espera else ""))
                yy -= 20
            partida, chegada = _dt(voo.get("partida")), _dt(voo.get("chegada"))
            base = yy - 34
            # horários
            c.setFillColor(TINTA)
            c.setFont("Helvetica-Bold", 21)
            c.drawString(x0 + 18, base, partida.strftime("%H:%M") if partida else "--:--")
            c.drawRightString(x0 + largura - 18, base, chegada.strftime("%H:%M") if chegada else "--:--")
            c.setFillColor(TINTA_2)
            c.setFont("Helvetica-Bold", 7)
            if partida:
                c.drawString(x0 + 18, base - 11, partida.strftime("%d/%m/%Y"))
            if chegada:
                c.drawRightString(x0 + largura - 18, base - 11, chegada.strftime("%d/%m/%Y"))
            # aeroportos
            for codigo, cx in ((voo.get("origem"), x0 + largura * 0.30), (voo.get("destino"), x0 + largura * 0.70)):
                c.setFillColor(TINTA)
                c.setFont("Helvetica-Bold", 16)
                c.drawCentredString(cx, base + 1, codigo or "")
                c.setFillColor(TINTA_2)
                c.setFont("Helvetica", 7)
                c.drawCentredString(cx, base - 10, _cortar(c, aeroportos.nome_curto(codigo) or "", "Helvetica", 7, 90))
            # linha do meio: avião, número do voo e duração
            meio = x0 + largura / 2
            c.setStrokeColor(LINHA)
            c.line(meio - 46, base + 7, meio - 12, base + 7)
            c.line(meio + 12, base + 7, meio + 46, base + 7)
            _aviao(c, meio, base + 7, TINTA, 10)
            c.setFillColor(TINTA_2)
            _espaco(c, (voo.get("numero") or "").upper(), meio - c.stringWidth((voo.get("numero") or "").upper(),
                    "Helvetica-Bold", 7) / 2 - 3, base - 6, "Helvetica-Bold", 7, 1.2)
            extra = " · ".join(x for x in (_duracao(partida, chegada), voo.get("cabine")) if x)
            if extra:
                c.setFont("Helvetica", 6.5)
                c.drawCentredString(meio, base - 16, extra)
            if voo.get("operado_por") and cia.upper() not in voo["operado_por"].upper():
                c.setFont("Helvetica-Oblique", 6.5)
                c.drawCentredString(meio, base - 25, f"Operado por {voo['operado_por']}")
            yy -= altura_voo
        y = y - altura - 22

    # ---------------- passageiros
    passageiros = reserva.get("passageiros") or []
    if y < 170:
        c.showPage()
        y = A - M
    c.setFillColor(NOITE)
    c.rect(M, y - 2, 3, 14, stroke=0, fill=1)
    _espaco(c, f"PASSAGEIROS ({len(passageiros)})", M + 12, y, "Helvetica-Bold", 10.5, 2.2)
    y -= 14
    for p in passageiros:
        alto = 62
        if y - alto < 120:
            c.showPage()
            y = A - M
        c.setFillColor(PAPEL_2)
        c.setStrokeColor(LINHA)
        c.roundRect(M, y - alto, L - 2 * M, alto, 12, stroke=1, fill=1)
        c.setFillColor(TINTA)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(M + 16, y - 22, _cortar(c, p.get("nome") or "", "Helvetica-Bold", 11, 330))
        if p.get("bilhete"):
            c.setFillColor(TINTA_2)
            c.setFont("Helvetica", 7.5)
            c.drawString(M + 16, y - 33, f"Bilhete nº {p['bilhete']}")
        # selo
        c.setFillColor(VERDE_FUNDO)
        c.roundRect(L - M - 88, y - 27, 74, 15, 7, stroke=0, fill=1)
        c.setFillColor(VERDE)
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(L - M - 51, y - 22.5, "CONFIRMADO")
        c.setStrokeColor(LINHA)
        c.line(M + 16, y - 39, L - M - 16, y - 39)
        bagagem = []
        if p.get("item_pessoal") is not None:
            bagagem.append(f"{p['item_pessoal']} ITEM PESSOAL (MOCHILA)")
        if p.get("bagagem_mao") is not None:
            bagagem.append(f"{p['bagagem_mao']} BAGAGEM DE MÃO")
        if p.get("bagagem_despachada") is not None:
            n = p["bagagem_despachada"]
            bagagem.append(f"{n} {'BAGAGEM DESPACHADA' if n == 1 else 'BAGAGENS DESPACHADAS'}")
        c.setFillColor(TINTA)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(M + 16, y - 53, "   ·   ".join(bagagem) if bagagem else "Bagagem: confira no site da companhia")
        y -= alto + 10

    # ---------------- aviso
    y -= 4
    aviso = [f"Faça o check-in pelo site ou aplicativo da {cia}. No embarque, apresente documento oficial com foto.",
             "Horários, portão e bagagem podem mudar: confira sempre a reserva no site da companhia (QR Code acima)."]
    c.setFillColor(PAPEL_2)
    c.setStrokeColor(LINHA)
    c.roundRect(M, y - 40, L - 2 * M, 40, 10, stroke=1, fill=1)
    c.setFillColor(TINTA_2)
    c.setFont("Helvetica-Bold", 7.2)
    c.drawString(M + 14, y - 16, aviso[0].upper())
    c.drawString(M + 14, y - 28, aviso[1].upper())
    y -= 62

    # ---------------- rodapé
    c.setFillColor(TINTA_2)
    _espaco(c, "EMITIDO POR", M, y, "Helvetica-Bold", 7, 1.6)
    c.setFillColor(TINTA)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(M + 66, y - 1, agencia)
    if contato:
        c.setFillColor(TINTA_2)
        c.setFont("Helvetica", 8)
        c.drawString(M, y - 14, f"Dúvidas: {contato}")
    c.setFillColor(TINTA_2)
    _espaco(c, "DATA DE EMISSÃO", L - M, y + 6, "Helvetica-Bold", 7, 1.6, direita=True)
    c.setFillColor(TINTA)
    c.setFont("Helvetica-Bold", 9)
    c.drawRightString(L - M, y - 8, emitido_em.strftime("%d/%m/%Y %H:%M"))
    c.setFillColor(LARANJA)
    c.rect(0, 0, L, 4, stroke=0, fill=1)

    c.showPage()
    c.save()
    return buf.getvalue()
