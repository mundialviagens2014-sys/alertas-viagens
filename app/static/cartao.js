// Imagem da promoção com a logo da agência, para colar no WhatsApp.
(function () {
  const AZUL = '#1B468F', AZUL_ESCURO = '#123266', LARANJA = '#FF914D', BRANCO = '#FFFFFF';
  // mesma família do site (servida pelo próprio MVO): Barlow no texto, Barlow Condensed nos códigos de aeroporto
  const FONTE = '"Barlow", system-ui, sans-serif';
  const CONDENSADA = '"Barlow Condensed", "Arial Narrow", "Barlow", sans-serif';
  const logo = new Image();
  logo.src = '/static/logo.png';

  function texto(ctx, t, x, y, tam, peso, cor, fonte, alinhar) {
    ctx.font = `${peso} ${tam}px ${fonte || FONTE}`;
    ctx.fillStyle = cor;
    ctx.textAlign = alinhar || 'left';
    ctx.fillText(t, x, y);
  }

  // diminui a fonte até o texto caber na largura
  function caber(ctx, t, max, tam, peso, fonte) {
    do { ctx.font = `${peso} ${tam}px ${fonte || FONTE}`; tam -= 2; } while (ctx.measureText(t).width > max && tam > 18);
    return tam + 2;
  }

  function arredondado(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }

  // o avião do arco de voo (o mesmo desenho da macro `arco`), em coordenadas de 1 unidade = `escala` px
  function aviao(ctx, x, y, angulo, escala, cor) {
    const P = [[5, 0], [1.5, -1], [-1.5, -4.2], [-3, -4.2], [-1.3, -1], [-4, -.6], [-5.2, -2], [-6.2, -2], [-5.5, 0], [-6.2, 2], [-5.2, 2], [-4, .6], [-1.3, 1], [-3, 4.2], [-1.5, 4.2], [1.5, 1]];
    ctx.save(); ctx.translate(x, y); ctx.rotate(angulo); ctx.scale(escala, escala);
    ctx.beginPath(); P.forEach(([px, py], i) => (i ? ctx.lineTo(px, py) : ctx.moveTo(px, py))); ctx.closePath();
    ctx.fillStyle = cor; ctx.fill(); ctx.restore();
  }

  async function desenhar(d) {
    try { await Promise.all([document.fonts.load('700 120px "Barlow Condensed"'), document.fonts.load('700 40px "Barlow"'), document.fonts.load('600 36px "Barlow"'), document.fonts.load('500 24px "Barlow"')]); } catch (_) { /* sem a fonte, fica a do sistema */ }
    await document.fonts.ready;
    if (!logo.complete) await new Promise((ok) => { logo.onload = ok; logo.onerror = ok; });
    const L = 1080, c = document.createElement('canvas');
    c.width = L; c.height = L;
    const ctx = c.getContext('2d');

    // fundo azul da marca com arcos de rota tênues, como na barra lateral do site
    ctx.fillStyle = AZUL; ctx.fillRect(0, 0, L, L);
    ctx.strokeStyle = 'rgba(255,145,77,.22)'; ctx.lineWidth = 3; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(-40, 820); ctx.quadraticCurveTo(420, 380, 1120, 700); ctx.stroke();
    ctx.strokeStyle = 'rgba(255,145,77,.12)';
    ctx.beginPath(); ctx.moveTo(-40, 960); ctx.quadraticCurveTo(520, 560, 1120, 900); ctx.stroke();

    // logo
    if (logo.naturalWidth) {
      const w = 420, h = w * logo.naturalHeight / logo.naturalWidth;
      ctx.drawImage(logo, 70, 50, w, h);
    }
    texto(ctx, 'OFERTA ENCONTRADA', L - 70, 132, 28, 700, LARANJA, CONDENSADA, 'right');

    // cartão branco central
    const cx = 70, cy = 270, cw = L - 140, ch = 560;
    ctx.save(); ctx.shadowColor = 'rgba(0,0,0,.25)'; ctx.shadowBlur = 40; ctx.shadowOffsetY = 12;
    arredondado(ctx, cx, cy, cw, ch, 36); ctx.fillStyle = BRANCO; ctx.fill(); ctx.restore();

    // rota: os códigos ligados pelo arco de voo com o avião, o gesto do site
    const meio = L / 2;
    texto(ctx, d.origem, meio - 110, cy + 150, 120, 700, AZUL, CONDENSADA, 'right');
    texto(ctx, d.destino, meio + 110, cy + 150, 120, 700, AZUL, CONDENSADA, 'left');
    const ax0 = meio - 92, ax1 = meio + 92, ay = cy + 128, acy = cy + 40;
    ctx.strokeStyle = LARANJA; ctx.lineWidth = 7; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(ax0, ay); ctx.quadraticCurveTo(meio, acy, ax1, ay); ctx.stroke();
    ctx.fillStyle = AZUL;
    ctx.beginPath(); ctx.arc(ax0, ay, 8, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(ax1, ay, 8, 0, Math.PI * 2); ctx.fill();
    // o avião a 52% do caminho (ponto e tangente da curva de Bézier quadrática)
    const t = .52, u = 1 - t;
    const px = u * u * ax0 + 2 * u * t * meio + t * t * ax1, py = u * u * ay + 2 * u * t * acy + t * t * ay;
    const dx = 2 * u * (meio - ax0) + 2 * t * (ax1 - meio), dy = 2 * u * (acy - ay) + 2 * t * (ay - acy);
    aviao(ctx, px, py, Math.atan2(dy, dx), 5.2, AZUL);
    const cidades = `${d.cidade_origem}  →  ${d.cidade_destino}`;
    texto(ctx, cidades, meio, cy + 205, caber(ctx, cidades, cw - 80, 36, 600), 600, '#556070', null, 'center');

    // linha separadora
    ctx.strokeStyle = '#E3E8F2'; ctx.lineWidth = 3; ctx.setLineDash([14, 12]);
    ctx.beginPath(); ctx.moveTo(cx + 50, cy + 250); ctx.lineTo(cx + cw - 50, cy + 250); ctx.stroke(); ctx.setLineDash([]);

    // valor
    texto(ctx, d.tipo ? `${d.tipo.toUpperCase()} · A PARTIR DE` : 'A PARTIR DE', meio, cy + 315, 28, 700, '#5A6782', CONDENSADA, 'center');
    texto(ctx, d.valor, meio, cy + 410, caber(ctx, d.valor, cw - 100, 112, 700, CONDENSADA), 700, LARANJA, CONDENSADA, 'center');

    // datas e companhia
    const info = [d.datas, d.companhia].filter(Boolean).join('  ·  ');
    texto(ctx, info, meio, cy + 490, caber(ctx, info, cw - 80, 34, 600), 600, AZUL_ESCURO, null, 'center');

    // rodapé
    texto(ctx, 'Responda a mensagem e garanta a sua viagem', meio, 935, 38, 700, BRANCO, null, 'center');
    texto(ctx, 'Valores sujeitos a disponibilidade e alteração', meio, 990, 24, 500, 'rgba(255,255,255,.65)', null, 'center');
    return c;
  }

  // janela com a prévia e os botões copiar/baixar
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.innerHTML = `
    <div class="modal-caixa" role="dialog" aria-label="Imagem da promoção">
      <div class="modal-topo"><b>Imagem da promoção</b><button type="button" class="btn fantasma mini" data-fechar>Fechar</button></div>
      <div class="modal-previa"></div>
      <div class="acoes" style="justify-content:center">
        <button type="button" class="btn primario" data-copiar-img>Copiar imagem</button>
        <a class="btn" data-baixar download="promocao.png">Baixar</a>
      </div>
      <p class="ajuda" style="text-align:center; margin:.8rem 0 0">Copie a imagem, clique em <b>Enviar no WhatsApp</b> e cole na conversa
        (<b>Ctrl + V</b>). Envie a imagem e depois o texto.</p>
    </div>`;
  document.addEventListener('DOMContentLoaded', () => document.body.appendChild(modal));

  // fecha com uma animação curta (quem pede menos movimento vê fechar na hora)
  let fechamento = null;
  function fechar() {
    if (!modal.classList.contains('aberto') || modal.classList.contains('fechando')) return;
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) { modal.classList.remove('aberto'); return; }
    modal.classList.add('fechando');
    fechamento = setTimeout(() => modal.classList.remove('aberto', 'fechando'), 170);
  }
  function abrir() {
    clearTimeout(fechamento);
    modal.classList.remove('fechando');
    modal.classList.add('aberto');
  }
  modal.addEventListener('click', (e) => { if (e.target === modal || e.target.closest('[data-fechar]')) fechar(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') fechar(); });

  let atual = null;
  document.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-cartao]');
    if (!b) return;
    const d = JSON.parse(b.dataset.cartao);
    const canvas = await desenhar(d);
    atual = canvas;
    const previa = modal.querySelector('.modal-previa');
    previa.innerHTML = '';
    const img = new Image(); img.src = canvas.toDataURL('image/png'); img.alt = `Promoção ${d.origem} para ${d.destino}`;
    previa.appendChild(img);
    const baixar = modal.querySelector('[data-baixar]');
    baixar.href = img.src; baixar.download = `promocao-${d.origem}-${d.destino}.png`;
    abrir();
  });

  modal.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-copiar-img]');
    if (!b || !atual) return;
    try {
      const blob = await new Promise((ok) => atual.toBlob(ok, 'image/png'));
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
      b.textContent = 'Imagem copiada ✓';
    } catch {
      b.textContent = 'Use o botão Baixar';
    }
    setTimeout(() => (b.textContent = 'Copiar imagem'), 2000);
  });

  window.desenharCartaoPromocao = desenhar; // usado nos testes
})();
