"""Gráficos em SVG desenhados pelo servidor (sem biblioteca externa, funcionam sem internet e sem script).

Cada função devolve o SVG pronto para o modelo (Markup). Formatos dos números: "int", "dinheiro", "pontos", "pct".
"""
from markupsafe import Markup, escape

AZUL = "#1b468f"
AZUL_ESCURO = "#12326a"
AZUL_MEDIO = "#c2d2ea"   # = --azul-medio
LARANJA = "#ff914d"
VERDE = "#168a4a"
VERMELHO = "#b42318"     # = --erro
CINZA = "#5a6782"
LINHA = "#d9e1ee"        # = --linha
PALETA = [AZUL, LARANJA, VERDE, "#7c5cbf", "#0f8b8d", "#c2410c", "#9a6700", "#475569", "#be185d", "#2563eb"]


def formatar(valor: float | int | None, formato: str = "int") -> str:
    if valor is None:
        return "–"
    if formato == "dinheiro":
        if abs(valor) >= 1_000_000:
            return f"R$ {valor / 1_000_000:.1f} mi".replace(".", ",")
        if abs(valor) >= 10_000:
            return f"R$ {valor / 1000:.0f} mil"
        if abs(valor) < 100 and valor != int(valor):
            return "R$ " + f"{valor:.2f}".replace(".", ",")
        return "R$ " + f"{valor:,.0f}".replace(",", ".")
    if formato == "pct":
        return f"{valor:.0f}%"
    if formato == "pontos" and abs(valor) >= 1_000_000:
        return f"{valor / 1_000_000:.1f} mi".replace(".", ",")
    return f"{valor:,.0f}".replace(",", ".")


def _teto(maximo: float) -> float:
    """Topo "redondo" da escala (ex.: 7 → 8, 23 → 25, 870 → 1000)."""
    if maximo <= 0:
        return 1
    import math

    ordem = 10 ** math.floor(math.log10(maximo))
    for mult in (1, 2, 2.5, 4, 5, 8, 10):
        if maximo <= mult * ordem:
            return mult * ordem
    return 10 * ordem


def _vazio(largura: int, altura: int, texto: str = "Sem dados no período") -> Markup:
    return Markup(
        f'<svg viewBox="0 0 {largura} {altura}" class="grafico" role="img"><text x="{largura / 2}" y="{altura / 2}" '
        f'text-anchor="middle" fill="{CINZA}" font-size="13">{escape(texto)}</text></svg>'
    )


def barras(itens: list[tuple[str, float]], *, formato: str = "int", cor: str = AZUL, largura: int = 560,
           altura: int = 230, titulo: str = "") -> Markup:
    """Barras verticais: [(rótulo, valor), ...]."""
    if not itens or all((v or 0) <= 0 for _, v in itens):
        return _vazio(largura, altura)
    return barras_empilhadas([r for r, _ in itens], [{"nome": titulo, "cor": cor, "valores": [v or 0 for _, v in itens]}],
                             formato=formato, largura=largura, altura=altura, legenda=False)


def barras_empilhadas(categorias: list[str], series: list[dict], *, formato: str = "int", largura: int = 560,
                      altura: int = 230, legenda: bool = True) -> Markup:
    """Barras empilhadas: series = [{"nome", "cor", "valores": [...]}] (uma lista por série, na ordem das categorias)."""
    n = len(categorias)
    if n == 0 or not series:
        return _vazio(largura, altura)
    totais = [sum((s["valores"][i] or 0) for s in series) for i in range(n)]
    if max(totais) <= 0:
        return _vazio(largura, altura)
    esq, dir_, topo, base = 54, 12, 30 if legenda else 22, 34
    teto = _teto(max(totais))
    divisoes = int(teto) if formato == "int" and teto < 4 else 4
    area_l, area_a = largura - esq - dir_, altura - topo - base
    passo = area_l / n
    larg = passo * (0.62 if n <= 16 else 0.8)
    partes = [f'<svg viewBox="0 0 {largura} {altura}" class="grafico" role="img">']
    # grade e eixo
    for k in range(divisoes + 1):
        y = topo + area_a - area_a * k / divisoes
        partes.append(f'<line x1="{esq}" y1="{y:.1f}" x2="{largura - dir_}" y2="{y:.1f}" stroke="{LINHA}" stroke-width="1"/>')
        partes.append(f'<text x="{esq - 6}" y="{y + 4:.1f}" text-anchor="end" font-size="10.5" fill="{CINZA}">'
                      f'{escape(formatar(teto * k / divisoes, formato))}</text>')
    # barras
    cada = 1 if n <= 14 else (2 if n <= 28 else max(1, round(n / 12)))
    for i, rotulo in enumerate(categorias):
        x = esq + passo * i + (passo - larg) / 2
        y_atual = topo + area_a
        for s in series:
            v = s["valores"][i] or 0
            if v <= 0:
                continue
            h = area_a * v / teto
            y_atual -= h
            partes.append(f'<rect x="{x:.1f}" y="{y_atual:.1f}" width="{larg:.1f}" height="{h:.1f}" rx="3" fill="{s["cor"]}">'
                          f'<title>{escape(rotulo)} · {escape(s["nome"] or "")}: {escape(formatar(v, formato))}</title></rect>')
        if totais[i] > 0 and passo >= 40:  # com barras finas o rótulo não cabe: fica a escala e o título ao passar o mouse
            desloca = 11 if (passo < 64 and i % 2) else 0  # barras vizinhas: rótulos em alturas alternadas, sem se cobrir
            partes.append(f'<text x="{x + larg / 2:.1f}" y="{y_atual - 4 - desloca:.1f}" text-anchor="middle" font-size="10.5" '
                          f'font-weight="700" fill="{AZUL_ESCURO}">{escape(formatar(totais[i], formato))}</text>')
        if i % cada == 0:
            partes.append(f'<text x="{x + larg / 2:.1f}" y="{altura - base + 16}" text-anchor="middle" font-size="10.5" '
                          f'fill="{CINZA}">{escape(rotulo)}</text>')
    partes.append(f'<line x1="{esq}" y1="{topo + area_a:.1f}" x2="{largura - dir_}" y2="{topo + area_a:.1f}" stroke="{CINZA}" stroke-width="1"/>')
    if legenda:
        x = esq
        for s in series:
            partes.append(f'<rect x="{x}" y="6" width="11" height="11" rx="2" fill="{s["cor"]}"/>')
            partes.append(f'<text x="{x + 15}" y="16" font-size="11" fill="{CINZA}">{escape(s["nome"])}</text>')
            x += 15 + 7 * len(s["nome"]) + 18
    partes.append("</svg>")
    return Markup("".join(partes))


def barras_horizontais(itens: list[tuple[str, float]], *, formato: str = "int", cor: str = AZUL, largura: int = 380,
                       maximo: int = 10) -> Markup:
    """Ranking: [(rótulo, valor), ...] já em ordem; uma barra por linha."""
    itens = [(r, v or 0) for r, v in itens[:maximo]]
    if not itens or max(v for _, v in itens) <= 0:
        return _vazio(largura, 80)
    linha_h, esq, dir_ = 26, 140, 64
    altura = linha_h * len(itens) + 8
    teto = max(v for _, v in itens)
    area_l = largura - esq - dir_
    partes = [f'<svg viewBox="0 0 {largura} {altura}" class="grafico" role="img">']
    for i, (rotulo, v) in enumerate(itens):
        y = 4 + i * linha_h
        w = area_l * v / teto if teto else 0
        texto = rotulo if len(rotulo) <= 21 else rotulo[:20] + "…"
        partes.append(f'<text x="{esq - 8}" y="{y + 16}" text-anchor="end" font-size="11.5" fill="{AZUL_ESCURO}">'
                      f'<title>{escape(rotulo)}</title>{escape(texto)}</text>')
        partes.append(f'<rect x="{esq}" y="{y + 4}" width="{max(w, 2):.1f}" height="{linha_h - 9}" rx="4" fill="{cor}">'
                      f'<title>{escape(rotulo)}: {escape(formatar(v, formato))}</title></rect>')
        partes.append(f'<text x="{esq + max(w, 2) + 6:.1f}" y="{y + 16}" font-size="11" font-weight="700" fill="{AZUL_ESCURO}">'
                      f'{escape(formatar(v, formato))}</text>')
    partes.append("</svg>")
    return Markup("".join(partes))


def rosca(itens: list[tuple[str, float]], *, formato: str = "int", largura: int = 360, cores: list[str] | None = None) -> Markup:
    """Rosca com legenda à direita: [(rótulo, valor), ...]."""
    import math

    itens = [(r, v or 0) for r, v in itens if (v or 0) > 0]
    total = sum(v for _, v in itens)
    if not itens or total <= 0:
        return _vazio(largura, 160)
    cores = cores or PALETA
    altura = max(160, 22 * len(itens) + 20)
    cx, cy, raio, esp = 80, altura / 2, 62, 22
    partes = [f'<svg viewBox="0 0 {largura} {altura}" class="grafico" role="img">']
    ang = -math.pi / 2
    for i, (rotulo, v) in enumerate(itens):
        frac = v / total
        fim = ang + 2 * math.pi * frac
        cor = cores[i % len(cores)]
        if frac >= 0.9999:
            partes.append(f'<circle cx="{cx}" cy="{cy:.1f}" r="{raio}" fill="none" stroke="{cor}" stroke-width="{esp}"/>')
        else:
            x1, y1 = cx + raio * math.cos(ang), cy + raio * math.sin(ang)
            x2, y2 = cx + raio * math.cos(fim), cy + raio * math.sin(fim)
            grande = 1 if frac > 0.5 else 0
            partes.append(f'<path d="M {x1:.2f} {y1:.2f} A {raio} {raio} 0 {grande} 1 {x2:.2f} {y2:.2f}" fill="none" '
                          f'stroke="{cor}" stroke-width="{esp}"><title>{escape(rotulo)}: {escape(formatar(v, formato))} '
                          f'({frac * 100:.0f}%)</title></path>')
        ang = fim
        y = 16 + i * 22
        partes.append(f'<rect x="{cx + raio + 36}" y="{y - 10}" width="11" height="11" rx="2" fill="{cor}"/>')
        texto = rotulo if len(rotulo) <= 22 else rotulo[:21] + "…"
        partes.append(f'<text x="{cx + raio + 52}" y="{y}" font-size="11.5" fill="{AZUL_ESCURO}">{escape(texto)} '
                      f'<tspan fill="{CINZA}">· {escape(formatar(v, formato))} ({frac * 100:.0f}%)</tspan></text>')
    partes.append(f'<text x="{cx}" y="{cy + 5:.1f}" text-anchor="middle" font-size="15" font-weight="800" fill="{AZUL_ESCURO}">'
                  f'{escape(formatar(total, formato))}</text>')
    partes.append("</svg>")
    return Markup("".join(partes))


def funil(etapas: list[tuple[str, float]], *, largura: int = 760, cor: str = AZUL) -> Markup:
    """Funil: cada etapa com a quantidade e o % em relação à primeira (a barra é proporcional ao maior valor)."""
    if not etapas or max((v or 0) for _, v in etapas) <= 0:
        return _vazio(largura, 80)
    linha_h, esq, dir_ = 30, 190, 130
    altura = linha_h * len(etapas) + 8
    base = etapas[0][1] or max((v or 0) for _, v in etapas)
    maior = max((v or 0) for _, v in etapas)
    area_l = largura - esq - dir_
    partes = [f'<svg viewBox="0 0 {largura} {altura}" class="grafico" role="img">']
    anterior = None
    for i, (rotulo, v) in enumerate(etapas):
        v = v or 0
        y = 4 + i * linha_h
        w = area_l * v / maior
        pct = v / base * 100
        conv = f" · {v / anterior * 100:.0f}% da etapa anterior" if anterior else ""
        partes.append(f'<text x="{esq - 8}" y="{y + 19}" text-anchor="end" font-size="11.5" fill="{AZUL_ESCURO}">{escape(rotulo)}</text>')
        partes.append(f'<rect x="{esq + (area_l - w) / 2:.1f}" y="{y + 5}" width="{max(w, 2):.1f}" height="{linha_h - 10}" rx="5" '
                      f'fill="{cor}" fill-opacity="{0.35 + 0.65 * (1 - i / max(1, len(etapas) - 1)):.2f}">'
                      f'<title>{escape(rotulo)}: {escape(formatar(v))}{escape(conv)}</title></rect>')
        partes.append(f'<text x="{esq + area_l + 8}" y="{y + 19}" font-size="11.5" font-weight="700" fill="{AZUL_ESCURO}">'
                      f'{escape(formatar(v))} <tspan fill="{CINZA}" font-weight="400">({pct:.0f}%)</tspan></text>')
        anterior = v or None
    partes.append("</svg>")
    return Markup("".join(partes))


def leque(origem: str, itens: list[tuple[str, float]], *, largura: int = 380, maximo: int = 10, formato: str = "int",
          sufixo: str = "", sufixo_um: str | None = None) -> Markup:
    """Mapa de rotas: de uma origem, à esquerda, um arco até cada destino, à direita; a espessura é a quantidade.

    Desenhado em 380 unidades para que, encolhido até ~320 px (celular), os rótulos ainda fiquem com 11 px ou mais;
    quem o mostra maior limita a largura por CSS (.grafico.leque)."""
    itens = [(r, v or 0) for r, v in itens[:maximo] if (v or 0) > 0]
    if not itens:
        return _vazio(largura, 120)
    if sufixo_um is None:   # " clientes" vira " cliente" quando é 1
        sufixo_um = sufixo[:-1] if sufixo.endswith("s") else sufixo
    quantidade = lambda v: f"{formatar(v, formato)}{sufixo_um if v == 1 else sufixo}"  # noqa: E731
    linha_h = 26
    altura = max(120, linha_h * len(itens) + 24)
    x0, y0 = 58, altura / 2
    x1 = largura - 204                      # os rótulos ocupam a faixa da direita; a quantidade fica encostada na borda
    maior = max(v for _, v in itens)
    partes = [f'<svg viewBox="0 0 {largura} {altura}" class="grafico leque" role="img">']
    for i, (rotulo, v) in enumerate(itens):
        y1 = 16 + i * linha_h
        esp = 1.2 + 3.8 * (v / maior)
        cx = (x0 + x1) / 2
        cy = min(y0, y1) - abs(y1 - y0) * .25 - 10
        partes.append(f'<path d="M{x0} {y0} Q{cx:.1f} {cy:.1f} {x1} {y1}" fill="none" stroke="{LARANJA}" stroke-opacity="{0.45 + 0.55 * v / maior:.2f}" '
                      f'stroke-width="{esp:.1f}" stroke-linecap="round"><title>{escape(origem)} → {escape(rotulo)}: {escape(quantidade(v))}</title></path>')
    for i, (rotulo, v) in enumerate(itens):
        y1 = 16 + i * linha_h
        partes.append(f'<circle cx="{x1}" cy="{y1}" r="3.5" fill="{AZUL}"/>')
        texto = rotulo if len(rotulo) <= 16 else rotulo[:15] + "…"
        partes.append(f'<text x="{x1 + 10}" y="{y1 + 5}" font-size="14" fill="{AZUL_ESCURO}" font-weight="600">{escape(texto)}</text>')
        partes.append(f'<text x="{largura - 6}" y="{y1 + 5}" font-size="13.5" fill="{CINZA}" text-anchor="end">{escape(quantidade(v))}</text>')
    partes.append(f'<circle cx="{x0}" cy="{y0:.1f}" r="6" fill="{AZUL}" stroke="#fff" stroke-width="2"/>')
    if len(origem) <= 4:    # código de aeroporto: à esquerda do ponto, na condensada
        partes.append(f'<text x="{x0 - 12}" y="{y0 + 6:.1f}" text-anchor="end" font-size="17" font-weight="700" fill="{AZUL_ESCURO}" '
                      f'letter-spacing=".04em" style="font-family: var(--condensada)">{escape(origem)}</text>')
    else:                   # nome por extenso: embaixo do ponto, para não sair pela borda
        partes.append(f'<text x="{x0}" y="{y0 + 22:.1f}" text-anchor="middle" font-size="13" font-weight="600" fill="{AZUL_ESCURO}">'
                      f'{escape(origem if len(origem) <= 14 else origem[:13] + "…")}</text>')
    partes.append("</svg>")
    return Markup("".join(partes))
