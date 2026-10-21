// Imagem da promoção com a logo da agência, para colar no WhatsApp.
(function () {
  const AZUL = '#1B468F', AZUL_ESCURO = '#123266', LARANJA = '#FF914D', BRANCO = '#FFFFFF';
  const FONTE = '"Plus Jakarta Sans", system-ui, sans-serif';
  const MONO = '"JetBrains Mono", ui-monospace, monospace';
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

  async function desenhar(d) {
    await document.fonts.ready;
    if (!logo.complete) await new Promise((ok) => { logo.onload = ok; logo.onerror = ok; });
    const L = 1080, c = document.createElement('canvas');
    c.width = L; c.height = L;
    const ctx = c.getContext('2d');

    // fundo azul da marca com uma rota pontilhada decorativa
    ctx.fillStyle = AZUL; ctx.fillRect(0, 0, L, L);
    ctx.strokeStyle = 'rgba(255,255,255,.10)'; ctx.lineWidth = 4; ctx.setLineDash([2, 18]);
    ctx.beginPath(); ctx.moveTo(-40, 760); ctx.bezierCurveTo(300, 520, 760, 980, 1120, 600); ctx.stroke();
    ctx.setLineDash([]);

    // logo
    if (logo.naturalWidth) {
      const w = 420, h = w * logo.naturalHeight / logo.naturalWidth;
      ctx.drawImage(logo, 70, 50, w, h);
    }
    texto(ctx, 'OFERTA ENCONTRADA', L - 70, 132, 26, 800, LARANJA, null, 'right');

    // cartão branco central
    const cx = 70, cy = 270, cw = L - 140, ch = 560;
    ctx.save(); ctx.shadowColor = 'rgba(0,0,0,.25)'; ctx.shadowBlur = 40; ctx.shadowOffsetY = 12;
    arredondado(ctx, cx, cy, cw, ch, 36); ctx.fillStyle = BRANCO; ctx.fill(); ctx.restore();

    // rota
    const meio = L / 2;
    texto(ctx, d.origem, meio - 70, cy + 150, 120, 700, AZUL, MONO, 'right');
    texto(ctx, d.destino, meio + 70, cy + 150, 120, 700, AZUL, MONO, 'left');
    texto(ctx, '✈', meio, cy + 132, 64, 400, LARANJA, null, 'center');
    const cidades = `${d.cidade_origem}  →  ${d.cidade_destino}`;
    texto(ctx, cidades, meio, cy + 205, caber(ctx, cidades, cw - 80, 36, 600), 600, '#556070', null, 'center');

    // linha separadora
    ctx.strokeStyle = '#E3E8F2'; ctx.lineWidth = 3; ctx.setLineDash([14, 12]);
    ctx.beginPath(); ctx.moveTo(cx + 50, cy + 250); ctx.lineTo(cx + cw - 50, cy + 250); ctx.stroke(); ctx.setLineDash([]);

    // valor
    texto(ctx, d.tipo ? `${d.tipo.toUpperCase()} · A PARTIR DE` : 'A PARTIR DE', meio, cy + 315, 26, 800, '#8A94A6', null, 'center');
    texto(ctx, d.valor, meio, cy + 410, caber(ctx, d.valor, cw - 100, 104, 800), 800, LARANJA, null, 'center');

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
      <div class="modal-topo"><b>Imagem da promoção</b><button type="button" class="btn fantasma mini" data-fechar>Fechar ✕</button></div>
      <div class="modal-previa"></div>
      <div class="acoes" style="justify-content:center">
        <button type="button" class="btn primario" data-copiar-img>Copiar imagem</button>
        <a class="btn" data-baixar download="promocao.png">Baixar</a>
      </div>
      <p class="ajuda" style="text-align:center; margin:.8rem 0 0">Copie a imagem, clique em <b>Enviar no WhatsApp</b> e cole na conversa
        (<b>Ctrl + V</b>). Envie a imagem e depois o texto.</p>
    </div>`;
  document.addEventListener('DOMContentLoaded', () => document.body.appendChild(modal));
  modal.addEventListener('click', (e) => { if (e.target === modal || e.target.closest('[data-fechar]')) modal.classList.remove('aberto'); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') modal.classList.remove('aberto'); });

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
    modal.classList.add('aberto');
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
