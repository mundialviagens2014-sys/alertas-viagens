// Movimento: animações leves para o sistema ficar mais fluido — seções que abrem e fecham deslizando, botão
// mostrando que está esperando o servidor, barra de carregamento no topo e linhas que somem com suavidade.
// Quem ativou "reduzir movimento" no computador ou no celular não vê as animações.
(() => {
  const calmo = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const raiz = document.documentElement;
  const SUAVE = 'cubic-bezier(.2, .75, .25, 1)';

  // ---------- seções que abrem e fecham (<details>) ----------
  function alturaFechada(d) {
    const aberto = d.open;
    d.open = false;
    const altura = d.offsetHeight;
    d.open = aberto;
    return altura;
  }

  function animarSecao(d, abrir) {
    const inicio = d.offsetHeight; // já considera uma animação em andamento
    if (d._mvo) d._mvo.anim.cancel();
    if (abrir) d.open = true;
    const fim = abrir ? d.offsetHeight : alturaFechada(d);
    d.style.overflow = 'hidden';
    d.classList.toggle('mvo-fechando', !abrir);
    const anim = d.animate({ height: [`${inicio}px`, `${fim}px`] }, { duration: 260, easing: SUAVE });
    d._mvo = { anim, abrir };
    for (const filho of d.children) {
      if (filho.tagName !== 'SUMMARY') {
        filho.animate({ opacity: abrir ? [0, 1] : [1, 0] }, { duration: abrir ? 260 : 180, easing: 'ease-out' });
      }
    }
    anim.onfinish = () => {
      if (!d._mvo || d._mvo.anim !== anim) return;
      d._mvo = null;
      if (!abrir) d.open = false;
      d.classList.remove('mvo-fechando');
      d.style.overflow = '';
    };
  }

  document.addEventListener('click', (e) => {
    const resumo = e.target.closest('summary');
    if (!resumo || e.defaultPrevented || calmo()) return;
    const d = resumo.parentElement;
    if (!d || d.tagName !== 'DETAILS' || d.querySelector(':scope > summary') !== resumo || !d.animate) return;
    const interativo = e.target.closest('a, button, input, select, textarea, label');
    if (interativo && resumo.contains(interativo)) return; // link ou botão dentro do título da seção
    e.preventDefault();
    animarSecao(d, d._mvo ? !d._mvo.abrir : !d.open);
  });

  // ---------- esperando o servidor: barra no topo e botão com o círculo girando ----------
  let espera = null;
  let seguranca = null;
  function carregando() {
    clearTimeout(espera);
    clearTimeout(seguranca);
    espera = setTimeout(() => raiz.classList.add('navegando'), 120); // página rápida nem mostra a barra
    seguranca = setTimeout(parou, 60000); // se nada acontecer em 1 minuto, volta tudo ao normal
  }
  function parou() {
    clearTimeout(espera);
    clearTimeout(seguranca);
    raiz.classList.remove('navegando');
    document.querySelectorAll('.btn.carregando').forEach((b) => b.classList.remove('carregando'));
    document.querySelectorAll('form[data-enviando]').forEach((f) => delete f.dataset.enviando);
  }
  // planilhas e outros arquivos baixam sem trocar de página
  const baixaArquivo = (u) => /\.(xlsx|xls|csv|pdf|zip|png|jpe?g)$/i.test(u.pathname) || u.searchParams.has('formato');

  document.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest('a[href]');
    if (!a || (a.target && a.target !== '_self') || a.hasAttribute('download')) return;
    const u = new URL(a.href, location.href);
    if (u.origin !== location.origin || baixaArquivo(u)) return;
    if (u.pathname === location.pathname && u.search === location.search && u.hash) return; // mesma página
    carregando();
  });

  document.addEventListener('submit', (e) => {
    const f = e.target;
    if (e.defaultPrevented || f.hasAttribute('data-rapido') || (f.target && f.target !== '_self')) return;
    if (f.dataset.enviando) { e.preventDefault(); return; } // segundo clique enquanto o primeiro ainda está indo
    const b = e.submitter;
    const destino = new URL(b && b.hasAttribute('formaction') ? b.formAction : f.action, location.href);
    if (baixaArquivo(destino)) return;
    f.dataset.enviando = '1';
    if (b && b.classList.contains('btn')) b.classList.add('carregando');
    carregando();
  });

  // voltar pelo navegador mostra a página guardada: tira a barra e o círculo
  addEventListener('pageshow', (e) => { if (e.persisted) parou(); });

  // ---------- linha que sai da lista (ex.: "Pular" na fila) ----------
  window.mvoSumir = (el) => {
    if (!el) return;
    if (calmo() || !el.animate) { el.remove(); return; }
    if (el.tagName === 'TR') {
      el.animate({ opacity: [1, 0] }, { duration: 200, easing: 'ease-in' }).onfinish = () => el.remove();
      return;
    }
    const cs = getComputedStyle(el);
    el.style.overflow = 'hidden';
    el.animate([
      { height: `${el.offsetHeight}px`, paddingTop: cs.paddingTop, paddingBottom: cs.paddingBottom, opacity: 1 },
      { height: '0px', paddingTop: '0px', paddingBottom: '0px', opacity: 0 },
    ], { duration: 280, easing: 'cubic-bezier(.4, 0, .2, 1)' }).onfinish = () => el.remove();
  };

  // ---------- texto que acabou de mudar (ex.: "Enviada ✓") ----------
  window.mvoDestacar = (el) => {
    if (!el) return;
    el.classList.remove('mvo-novo');
    void el.offsetWidth; // recomeça a animação
    el.classList.add('mvo-novo');
  };
})();
