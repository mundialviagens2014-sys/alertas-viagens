// MVO no celular — o comportamento do app (a moldura está em templates/_app_movel.html e o visual em app-movel.css).
// Folhas que sobem e descem com o dedo, título que aparece na barra ao rolar, filtros numa folha, números que contam,
// stories, deslizar para enviar/pular na fila, funil com uma coluna por tela e "segure para mover", e puxar para
// atualizar no app instalado. No computador (tela larga) nada disto roda.
(() => {
  const movel = matchMedia('(max-width: 900px)');
  if (!movel.matches || !document.querySelector('.am-abas')) return;

  const raiz = document.documentElement;
  const calmo = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const vibrar = (ms = 8) => { try { if (navigator.vibrate) navigator.vibrate(ms); } catch (e) {} };
  const ehApp = matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  const chegada = raiz.dataset.amChegada || 'aba';

  // ---------- barra do topo: título pequeno aparece quando o título grande sai da tela ----------
  const topo = document.querySelector('[data-am-topo]');
  const h1 = document.querySelector('.conteudo h1');
  const tituloBarra = topo.querySelector('[data-am-titulo]');
  if (h1) {
    tituloBarra.textContent = h1.textContent.trim();
    new IntersectionObserver(([e]) => topo.classList.toggle('com-titulo', !e.isIntersecting && e.boundingClientRect.top < 80),
      { rootMargin: '-60px 0px 0px 0px' }).observe(h1);
  } else {
    tituloBarra.textContent = (document.title.split('·')[0] || '').trim();
    topo.classList.add('com-titulo');
  }
  const marcarRolagem = () => topo.classList.toggle('rolou', scrollY > 6);
  addEventListener('scroll', marcarRolagem, { passive: true }); marcarRolagem();

  // voltar: volta no histórico (com a animação de voltar); se a tela foi aberta direto, vai para o início do módulo
  document.querySelector('[data-am-voltar]')?.addEventListener('click', () => {
    vibrar();
    try { sessionStorage.setItem('am-voltar', '1'); } catch (e) {}
    const veioDaqui = document.referrer && new URL(document.referrer).origin === location.origin;
    if (veioDaqui && history.length > 1) history.back();
    else location.href = { manager: '/manager', crm: '/crm' }[document.body.dataset.modulo] || '/';
  });

  // links "← Voltar para ..." do cabeçalho: no app, o botão voltar da barra do topo faz isso
  document.querySelectorAll('.conteudo > .topo a').forEach((a) => { if (/^\s*←/.test(a.textContent)) a.hidden = true; });
  document.querySelectorAll('.conteudo > .topo > div:last-child:not(:first-child)').forEach((d) => { if (![...d.children].some((c) => !c.hidden)) d.hidden = true; });

  // ---------- abas: o brilho do vidro fica em cima da aba escolhida ----------
  const abas = document.querySelector('.am-abas');
  const posicionarLuz = () => {
    const ativa = abas.querySelector('.am-aba.ativa'); if (!ativa) return;
    const a = abas.getBoundingClientRect(), b = ativa.getBoundingClientRect();
    abas.style.setProperty('--am-luz-x', ((b.left + b.width / 2 - a.left) / a.width * 100).toFixed(1) + '%');
  };
  posicionarLuz(); addEventListener('resize', posicionarLuz);
  abas.addEventListener('pointerdown', (e) => { if (e.target.closest('.am-aba')) vibrar(6); });

  // ---------- folhas (Criar, Mais, Filtros, Mover) ----------
  const abrirFolha = (folha) => {
    if (!folha || folha.open) return;
    folha.style.removeProperty('--am-arrasto');
    folha.showModal(); raiz.classList.add('am-folha-aberta'); vibrar(10);
    document.querySelectorAll(`[data-am-abrir="${folha.id}"]`).forEach((b) => b.setAttribute('aria-expanded', 'true'));
  };
  const fecharFolha = (folha) => { if (folha && folha.open) folha.close(); };
  const aoFechar = (folha) => folha.addEventListener('close', () => {
    if (!document.querySelector('dialog.am-folha[open]')) raiz.classList.remove('am-folha-aberta');
    document.querySelectorAll(`[data-am-abrir="${folha.id}"]`).forEach((b) => b.setAttribute('aria-expanded', 'false'));
    setTimeout(() => folha.style.removeProperty('--am-arrasto'), 520);
  });
  // tocar fora da folha fecha; arrastar a alça (ou o topo da folha) para baixo também
  const prepararFolha = (folha) => {
    aoFechar(folha);
    folha.setAttribute('autofocus', '');   // ao abrir, o foco fica na folha (e não no primeiro botão, com o anel de foco)
    folha.tabIndex = -1;
    folha.addEventListener('click', (e) => {
      if (e.target !== folha) return;
      const r = folha.getBoundingClientRect();
      if (e.clientY < r.top) fecharFolha(folha);
    });
    let y0 = null, t0 = 0, dy = 0;
    folha.addEventListener('pointerdown', (e) => {
      const naAlca = e.target.closest('[data-am-alca]') || (folha.scrollTop <= 0 && e.clientY - folha.getBoundingClientRect().top < 64);
      if (!naAlca || e.target.closest('a, button, input, select, textarea, label')) return;
      y0 = e.clientY; t0 = performance.now(); dy = 0; folha.classList.add('arrastando'); folha.setPointerCapture(e.pointerId);
    });
    folha.addEventListener('pointermove', (e) => {
      if (y0 === null) return;
      dy = Math.max(0, e.clientY - y0); if (e.clientY < y0) dy = -Math.sqrt(y0 - e.clientY) * 2;   // para cima: resiste
      folha.style.setProperty('--am-arrasto', dy + 'px');
    });
    const soltar = () => {
      if (y0 === null) return;
      const rapido = dy / Math.max(1, performance.now() - t0) > .6;
      folha.classList.remove('arrastando'); y0 = null;
      if (dy > 110 || (rapido && dy > 30)) { folha.style.setProperty('--am-arrasto', '100%'); setTimeout(() => fecharFolha(folha), calmo() ? 0 : 260); }
      else folha.style.removeProperty('--am-arrasto');
    };
    folha.addEventListener('pointerup', soltar); folha.addEventListener('pointercancel', soltar);
  };
  document.querySelectorAll('dialog.am-folha').forEach(prepararFolha);
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-am-abrir]'); if (!b) return;
    const folha = document.getElementById(b.dataset.amAbrir);
    if (folha && folha.open) fecharFolha(folha); else { document.querySelectorAll('dialog.am-folha[open]').forEach(fecharFolha); abrirFolha(folha); }
  });
  // escolher um link dentro da folha: ela desce enquanto a próxima tela carrega
  document.addEventListener('click', (e) => {
    const a = e.target.closest('dialog.am-folha a[href]'); if (!a) return;
    const folha = a.closest('dialog'); setTimeout(() => fecharFolha(folha), 60);
  });
  // ao voltar para a tela pelo histórico, nenhuma folha fica aberta
  addEventListener('pageshow', (e) => { if (e.persisted) document.querySelectorAll('dialog[open]').forEach((d) => d.close()); raiz.classList.remove('am-folha-aberta'); });

  // ---------- Mais: as seções vêm do menu lateral (mesmas permissões e contadores) ----------
  const lista = document.querySelector('[data-am-menu]'), menu = document.getElementById('menu-principal');
  if (lista && menu) {
    const copia = menu.cloneNode(true);
    copia.querySelectorAll('.indicador, script').forEach((x) => x.remove());
    copia.querySelectorAll('details').forEach((d) => { d.open = true; });
    lista.append(...copia.childNodes);
  }
  // diálogos que moravam no menu lateral (que fica escondido no celular) passam para a página
  document.querySelectorAll('.lateral dialog').forEach((d) => document.body.append(d));
  // tema: claro, escuro ou automático, com um círculo que se abre a partir do botão
  const botoesTema = document.querySelectorAll('[data-am-tema]');
  const marcarTema = () => botoesTema.forEach((b) => b.setAttribute('aria-pressed', b.dataset.amTema === raiz.dataset.escolhaTema));
  marcarTema();
  botoesTema.forEach((b) => b.addEventListener('click', (e) => {
    try { localStorage.setItem('mvo-tema', b.dataset.amTema); } catch (x) {}
    vibrar();
    const aplicar = () => { window.mvoAplicarTema && window.mvoAplicarTema(); marcarTema(); };
    if (!document.startViewTransition || calmo()) { aplicar(); return; }
    raiz.classList.add('am-trocando-tema');
    const t = document.startViewTransition(aplicar);
    t.ready.then(() => {
      const raio = Math.hypot(Math.max(e.clientX, innerWidth - e.clientX), Math.max(e.clientY, innerHeight - e.clientY));
      raiz.animate({ clipPath: [`circle(0px at ${e.clientX}px ${e.clientY}px)`, `circle(${raio}px at ${e.clientX}px ${e.clientY}px)`] },
        { duration: 650, easing: 'cubic-bezier(.32, .72, 0, 1)', pseudoElement: '::view-transition-new(root)' });
    });
    t.finished.finally(() => raiz.classList.remove('am-trocando-tema'));
  }));
  // instalar o app: usa o mesmo botão do menu lateral, que já sabe quando dá para instalar
  const instalarOriginal = document.querySelector('[data-instalar]'), instalar = document.querySelector('[data-am-instalar]');
  if (instalarOriginal && instalar) {
    const espelhar = () => { instalar.hidden = instalarOriginal.hidden; };
    espelhar(); new MutationObserver(espelhar).observe(instalarOriginal, { attributes: true, attributeFilter: ['hidden'] });
    instalar.addEventListener('click', () => { fecharFolha(instalar.closest('dialog')); instalarOriginal.click(); });
  }

  // ---------- filtros das listas: a busca fica à vista, o resto vai para a folha "Filtros" ----------
  const lupa = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>';
  const iconeFiltros = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M7 12h10M10 18h4"/></svg>';
  document.querySelectorAll('form.filtros').forEach((form, i) => {
    const campos = [...form.children].filter((c) => !c.matches('input[type=hidden], .segmentos, button, .btn, a, span, datalist, small'));
    const busca = campos.find((c) => c.matches('input[type=search], input[name=q]'));
    const resto = campos.filter((c) => c !== busca);
    if (!resto.length) return;   // só a busca: fica como está
    form.classList.add('am-filtros');
    const folha = document.createElement('dialog');
    folha.className = 'am-folha am-folha-filtros'; folha.id = 'am-filtros-' + i;
    folha.innerHTML = '<div class="am-alca" data-am-alca aria-hidden="true"></div><h2>Filtros</h2><div class="am-campos"></div>'
      + '<div class="am-aplicar"><button type="button" class="btn" data-am-limpar>Limpar</button><button class="btn primario">Mostrar resultados</button></div>';
    const lugar = folha.querySelector('.am-campos');
    resto.forEach((c) => { c.removeAttribute('style'); lugar.append(c); });
    // a folha fica dentro do formulário: os campos continuam sendo enviados com a busca
    form.append(folha); prepararFolha(folha);
    // dentro da folha, escolher uma opção não busca na hora (no computador busca): espera "Mostrar resultados"
    folha.addEventListener('change', (e) => { e.stopPropagation(); contar(); });
    folha.querySelector('[data-am-limpar]').addEventListener('click', () => {
      lugar.querySelectorAll('input:not([type=hidden])').forEach((c) => { if (c.type === 'checkbox') c.checked = false; else c.value = ''; });
      lugar.querySelectorAll('select').forEach((s) => { s.selectedIndex = 0; });
      contar();
    });
    const linha = document.createElement('div'); linha.className = 'am-busca';
    const botao = document.createElement('button');
    botao.type = 'button'; botao.className = 'am-botao-filtros'; botao.dataset.amAbrir = folha.id; botao.innerHTML = iconeFiltros + 'Filtros';
    if (busca) { busca.removeAttribute('style'); busca.before(linha); linha.append(busca); linha.insertAdjacentHTML('afterbegin', lupa); linha.after(botao); }
    else form.prepend(botao);
    const contar = () => {
      let n = 0;
      lugar.querySelectorAll('input:not([type=hidden])').forEach((c) => { if (c.type === 'checkbox' ? c.checked : c.value.trim()) n++; });
      lugar.querySelectorAll('select').forEach((s) => { if (s.selectedIndex > 0) n++; });
      botao.dataset.qtd = n;
    };
    contar();
    // o botão "Buscar" do computador sobra: a busca vai com Enter no teclado ou com "Mostrar resultados"
    form.querySelectorAll(':scope > button:not(.am-botao-filtros), :scope > .btn').forEach((b) => { b.hidden = true; });
  });

  // ---------- números: contam do zero quando a tela abre ----------
  if (!calmo() && chegada !== 'filtro') {
    document.querySelectorAll('.numero b').forEach((b) => {
      const texto = b.textContent.trim();
      const m = texto.match(/^(\D*?)(\d{1,3}(?:\.\d{3})*|\d+)(,\d+)?(\D*)$/);
      if (!m) return;
      const alvo = Number(m[2].replace(/\./g, '')); if (!alvo) return;
      const decimais = m[3] ? m[3].length - 1 : 0, total = alvo + (decimais ? Number('0' + m[3].replace(',', '.')) : 0);
      const fmt = (v) => m[1] + v.toLocaleString('pt-BR', { minimumFractionDigits: decimais, maximumFractionDigits: decimais }) + m[4];
      const inicio = performance.now(), dur = 900;
      const passo = (t) => {
        const p = Math.min(1, (t - inicio) / dur), suave = 1 - Math.pow(2, -10 * p);
        b.textContent = p < 1 ? fmt(decimais ? total * suave : Math.round(total * suave)) : texto;
        if (p < 1) requestAnimationFrame(passo);
      };
      requestAnimationFrame(passo);
    });
  }

  // ---------- cascata: os cartões entram um depois do outro ----------
  if (!calmo() && (chegada === 'aba' || chegada === 'filtro')) {
    document.querySelectorAll('.conteudo .numero, .conteudo .am-story, .conteudo .bilhete, .conteudo > .cartao, .conteudo .tabela.cartoes-moveis tbody tr, .conteudo .cartao-crm')
      .forEach((el, i) => { if (i < 14) { el.style.setProperty('--am-i', i); el.classList.add('am-cascata'); } });
  }

  // ---------- stories da fila: o anel gira enquanto a fonte é lida ----------
  document.addEventListener('submit', (e) => {
    const f = e.target.closest('[data-am-story]'); if (!f) return;
    f.querySelector('.am-story')?.classList.add('carregando'); vibrar(12);
  });

  // ---------- fila: deslizar o cliente para a direita envia no WhatsApp; para a esquerda, pula ----------
  document.querySelectorAll('.destinatario').forEach((linha) => {
    const enviar = linha.querySelector('a[data-registrar]:not([disabled])');
    const pular = linha.querySelector('form[data-rapido]');
    if (!enviar && !pular) return;
    const caixa = document.createElement('div'); caixa.className = 'am-deslize';
    caixa.innerHTML = '<div class="am-deslize-fundo" aria-hidden="true"><span class="enviar">WhatsApp →</span><span class="pular">← Pular</span></div>';
    linha.before(caixa); caixa.append(linha);
    let x0 = null, y0 = 0, dx = 0, decidido = false, horizontal = false;
    linha.addEventListener('pointerdown', (e) => {
      if (e.pointerType === 'mouse' || e.target.closest('a, button, input, textarea, select')) return;
      x0 = e.clientX; y0 = e.clientY; dx = 0; decidido = false; horizontal = false;
    });
    linha.addEventListener('pointermove', (e) => {
      if (x0 === null) return;
      const ax = e.clientX - x0, ay = e.clientY - y0;
      if (!decidido && (Math.abs(ax) > 8 || Math.abs(ay) > 8)) {
        decidido = true; horizontal = Math.abs(ax) > Math.abs(ay) * 1.3;
        if (horizontal) { caixa.classList.add('arrastando'); linha.setPointerCapture(e.pointerId); }
      }
      if (!horizontal) return;
      dx = ax; if ((dx > 0 && !enviar) || (dx < 0 && !pular)) dx /= 4;
      caixa.dataset.lado = dx > 0 ? 'direita' : 'esquerda';
      linha.style.transform = `translateX(${dx}px)`;
      const passou = Math.abs(dx) > 110;
      if (passou !== caixa.classList.contains('passou')) { caixa.classList.toggle('passou', passou); if (passou) vibrar(14); }
    });
    const soltar = () => {
      if (x0 === null) return;
      x0 = null; caixa.classList.remove('arrastando');
      if (horizontal && dx > 110 && enviar) {
        linha.style.transform = ''; enviar.click();
      } else if (horizontal && dx < -110 && pular) {
        linha.style.transform = 'translateX(-110%)';
        setTimeout(() => pular.requestSubmit ? pular.requestSubmit() : pular.submit(), 220);
      } else linha.style.transform = '';
      if (!linha.style.transform) setTimeout(() => { delete caixa.dataset.lado; }, 450);
    };
    linha.addEventListener('pointerup', soltar); linha.addEventListener('pointercancel', soltar);
  });

  // ---------- CRM: uma coluna por tela, chips das etapas e "segure o cartão para mover" ----------
  const quadro = document.getElementById('quadro');
  if (quadro) {
    const colunas = [...quadro.querySelectorAll('.coluna-crm')];
    const nomeEtapa = (c) => (c.querySelector('header h2, header h3, header b, header strong')?.textContent || c.dataset.etapa).trim();
    const barra = document.createElement('div'); barra.className = 'am-etapas'; barra.setAttribute('role', 'tablist');
    const qtd = (c) => c.querySelectorAll('.cartao-crm').length;
    colunas.forEach((c, i) => {
      const b = document.createElement('button'); b.type = 'button'; b.style.setProperty('--cor', getComputedStyle(c).getPropertyValue('--cor'));
      b.innerHTML = `${nomeEtapa(c)} <b>${qtd(c)}</b>`;
      b.addEventListener('click', () => { vibrar(); quadro.scrollTo({ left: c.offsetLeft - quadro.offsetLeft - parseFloat(getComputedStyle(quadro).paddingLeft), behavior: calmo() ? 'auto' : 'smooth' }); });
      barra.append(b);
    });
    quadro.before(barra);
    const marcar = () => {
      const i = Math.round(quadro.scrollLeft / Math.max(1, colunas[1] ? colunas[1].offsetLeft - colunas[0].offsetLeft : quadro.clientWidth));
      barra.querySelectorAll('button').forEach((b, j) => b.classList.toggle('ativo', j === i));
      const ativo = barra.children[i]; if (ativo) barra.scrollTo({ left: ativo.offsetLeft - 16, behavior: calmo() ? 'auto' : 'smooth' });
    };
    quadro.addEventListener('scroll', () => { clearTimeout(quadro._t); quadro._t = setTimeout(marcar, 60); }, { passive: true });
    marcar();
    const contarTudo = () => colunas.forEach((c, i) => {
      const n = qtd(c); barra.children[i].querySelector('b').textContent = n;
      const q = c.querySelector('[data-qtd]'); if (q) q.textContent = n;
      const vazia = c.querySelector('.vazia');
      if (n && vazia) vazia.remove(); if (!n && !vazia) c.querySelector('.cartoes').insertAdjacentHTML('beforeend', '<div class="vazia">Ninguém aqui</div>');
    });

    // folha "Mover para"
    const folha = document.createElement('dialog'); folha.className = 'am-folha'; folha.id = 'am-mover';
    folha.innerHTML = '<div class="am-alca" data-am-alca aria-hidden="true"></div><h2>Mover para</h2><div class="am-mover-lista"></div>'
      + '<div class="am-mover-motivo" hidden><label for="am-motivo">Motivo da perda (opcional)</label><input id="am-motivo" type="text">'
      + '<button type="button" class="btn primario" style="width:100%;margin-top:10px" data-am-confirmar-perda>Mover para Perdido</button></div>';
    document.body.append(folha); prepararFolha(folha);
    const listaMover = folha.querySelector('.am-mover-lista'), motivo = folha.querySelector('.am-mover-motivo');
    let cartaoEscolhido = null;
    colunas.forEach((c) => {
      const b = document.createElement('button'); b.type = 'button'; b.dataset.etapa = c.dataset.etapa; b.textContent = nomeEtapa(c);
      b.style.setProperty('--cor', getComputedStyle(c).getPropertyValue('--cor')); listaMover.append(b);
    });
    const mover = async (destino, textoMotivo = '') => {
      const cartao = cartaoEscolhido; if (!cartao) return;
      const col = colunas.find((c) => c.dataset.etapa === destino);
      fecharFolha(folha);
      const trocar = () => {
        col.querySelector('.cartoes').prepend(cartao);
        if (!cartao.querySelector('.mao')) cartao.insertAdjacentHTML('afterbegin', '<span class="mao">à mão</span>');
        contarTudo();
      };
      if (document.startViewTransition && !calmo()) { cartao.style.viewTransitionName = 'am-cartao-movido'; await document.startViewTransition(trocar).finished; cartao.style.viewTransitionName = ''; }
      else trocar();
      barra.children[colunas.indexOf(col)].click();
      cartao.classList.add('am-chegou-agora'); setTimeout(() => cartao.classList.remove('am-chegou-agora'), 800);
      try {
        const r = await fetch('/crm/mover', { method: 'POST', headers: { 'X-Requested-With': 'fetch' },
          body: new URLSearchParams({ cliente_id: cartao.dataset.cliente, etapa: destino, motivo: textoMotivo }) });
        if (!r.ok) throw new Error();
      } catch (x) { alert('Não foi possível mover. Recarregue a página.'); }
    };
    listaMover.addEventListener('click', (e) => {
      const b = e.target.closest('button'); if (!b || b.getAttribute('aria-current') === 'true') return;
      vibrar();
      if (b.dataset.etapa === 'perdido') { motivo.hidden = false; folha.querySelector('#am-motivo').focus(); return; }
      mover(b.dataset.etapa);
    });
    folha.querySelector('[data-am-confirmar-perda]').addEventListener('click', () => mover('perdido', folha.querySelector('#am-motivo').value));
    // segurar o cartão por meio segundo abre "Mover para"
    let espera = null, inicio = null;
    quadro.addEventListener('pointerdown', (e) => {
      const cartao = e.target.closest('.cartao-crm'); if (!cartao || e.target.closest('a, button, form')) return;
      inicio = { x: e.clientX, y: e.clientY };
      cartao.classList.add('am-segurando');
      espera = setTimeout(() => {
        cartao.classList.remove('am-segurando'); vibrar(20);
        cartaoEscolhido = cartao; motivo.hidden = true; folha.querySelector('#am-motivo').value = '';
        const atual = cartao.closest('.coluna-crm').dataset.etapa;
        listaMover.querySelectorAll('button').forEach((b) => b.setAttribute('aria-current', b.dataset.etapa === atual));
        abrirFolha(folha);
      }, 480);
    });
    const cancelar = (e) => {
      if (!espera) return;
      if (e && e.type === 'pointermove' && inicio && Math.hypot(e.clientX - inicio.x, e.clientY - inicio.y) < 10) return;
      clearTimeout(espera); espera = null; quadro.querySelectorAll('.am-segurando').forEach((c) => c.classList.remove('am-segurando'));
    };
    ['pointerup', 'pointercancel', 'pointermove'].forEach((t) => quadro.addEventListener(t, cancelar, { passive: true }));
    quadro.addEventListener('scroll', () => cancelar(), { passive: true });
    quadro.addEventListener('contextmenu', (e) => { if (e.target.closest('.cartao-crm')) e.preventDefault(); });
    quadro.querySelectorAll('.cartao-crm').forEach((c) => c.setAttribute('draggable', 'false'));
  }

  // ---------- Pergunte ao MVO: fala (ou digita), o MVO responde por escrito e em voz alta ----------
  const folhaP = document.getElementById('am-perguntar');
  if (folhaP) {
    const orbe = folhaP.querySelector('[data-am-ouvir]'), estado = folhaP.querySelector('[data-am-estado]');
    const transcricao = folhaP.querySelector('[data-am-transcricao]'), caixa = folhaP.querySelector('[data-am-resposta]');
    const form = folhaP.querySelector('[data-am-digitar]'), campo = form.querySelector('input');
    const Reconhecer = window.SpeechRecognition || window.webkitSpeechRecognition;
    const dicaInicial = estado.textContent;
    let rec = null, ouvindo = false;
    const calar = () => { try { speechSynthesis.cancel(); } catch (e) {} };
    const falar = (texto) => {
      if (!('speechSynthesis' in window)) return;
      calar();
      const fala = new SpeechSynthesisUtterance(texto.replace(/R\$\s?/g, '').replace(/→/g, ' para '));
      fala.lang = 'pt-BR'; fala.rate = 1.05;
      const voz = speechSynthesis.getVoices().find((v) => v.lang === 'pt-BR' || v.lang === 'pt_BR');
      if (voz) fala.voice = voz;
      speechSynthesis.speak(fala);
    };
    const perguntar = async (texto) => {
      texto = (texto || '').trim(); if (!texto) return;
      transcricao.textContent = '“' + texto + '”';
      estado.textContent = 'Consultando o MVO…'; caixa.hidden = true; orbe.classList.add('pensando');
      try {
        const r = await fetch('/assistente/perguntar', { method: 'POST', headers: { 'X-Requested-With': 'fetch' },
          body: new URLSearchParams({ pergunta: texto }) });
        if (!r.ok) throw new Error(r.status);
        const j = await r.json();
        caixa.replaceChildren();
        const b = document.createElement('b'); b.textContent = j.texto; caixa.append(b);
        if (j.detalhes && j.detalhes.length) {
          const ul = document.createElement('ul');
          j.detalhes.forEach((d) => { const li = document.createElement('li'); li.textContent = d; ul.append(li); });
          caixa.append(ul);
        }
        if (j.link) { const a = document.createElement('a'); a.href = j.link; a.textContent = 'Abrir no MVO →'; caixa.append(a); }
        caixa.hidden = false; estado.textContent = ''; vibrar(12);
        falar(j.texto + (j.detalhes && j.detalhes.length && j.detalhes.length <= 3 && j.link !== '/crm' ? '. ' + j.detalhes.join('. ') : ''));
      } catch (e) {
        estado.textContent = 'Não consegui consultar agora. Confira a internet e tente de novo.';
      }
      orbe.classList.remove('pensando');
    };
    orbe.addEventListener('click', () => {
      if (!Reconhecer) { estado.textContent = 'Este navegador não reconhece voz. Digite a pergunta abaixo.'; campo.focus(); return; }
      if (ouvindo && rec) { rec.stop(); return; }
      calar();
      rec = new Reconhecer(); rec.lang = 'pt-BR'; rec.interimResults = true; rec.maxAlternatives = 1;
      let final = '';
      rec.onstart = () => { ouvindo = true; orbe.classList.add('ouvindo'); estado.textContent = 'Ouvindo…'; transcricao.textContent = ''; caixa.hidden = true; vibrar(10); };
      rec.onresult = (e) => {
        let parcial = ''; final = '';
        for (const r of e.results) { if (r.isFinal) final += r[0].transcript; else parcial += r[0].transcript; }
        transcricao.textContent = final || parcial;
      };
      rec.onerror = (e) => {
        estado.textContent = { 'not-allowed': 'Permita o microfone para o MVO (cadeado ao lado do endereço → Permissões).',
          'service-not-allowed': 'Permita o microfone para o MVO nas configurações do Chrome.',
          'no-speech': 'Não ouvi nada. Toque e fale de novo.', 'network': 'O reconhecimento de voz precisa de internet.' }[e.error]
          || 'Não consegui ouvir. Tente de novo ou digite.';
      };
      rec.onend = () => { ouvindo = false; orbe.classList.remove('ouvindo'); if (final) perguntar(final); };
      try { rec.start(); } catch (e) { estado.textContent = 'Não consegui ligar o microfone. Tente de novo.'; }
    });
    folhaP.addEventListener('click', (e) => { const b = e.target.closest('[data-am-sugestao]'); if (b) perguntar(b.textContent); });
    form.addEventListener('submit', (e) => { e.preventDefault(); campo.blur(); perguntar(campo.value); campo.value = ''; });
    folhaP.addEventListener('close', () => { if (rec) try { rec.abort(); } catch (e) {} calar(); });
    folhaP.addEventListener('close', () => { estado.textContent = dicaInicial; });
    // atalho do ícone (segurar o ícone do MVO → "Perguntar ao MVO") abre direto aqui
    if (new URLSearchParams(location.search).has('perguntar')) {
      abrirFolha(folhaP); history.replaceState(null, '', location.pathname);
    }
  }

  // ---------- puxar para atualizar (só no app instalado; no navegador ele já existe) ----------
  if (ehApp) {
    const indicador = document.querySelector('[data-am-puxar]');
    let y0 = null, puxado = 0, atualizando = false;
    addEventListener('touchstart', (e) => {
      if (atualizando || scrollY > 0 || document.querySelector('dialog[open]') || e.touches.length > 1) return;
      y0 = e.touches[0].clientY; indicador.classList.add('ativo'); indicador.classList.remove('solto');
    }, { passive: true });
    addEventListener('touchmove', (e) => {
      if (y0 === null) return;
      const d = e.touches[0].clientY - y0;
      if (d <= 0) { puxado = 0; indicador.style.setProperty('--am-puxado', 0); return; }
      const novo = Math.min(1, d / 160);
      if (novo >= 1 && puxado < 1) vibrar(12);
      puxado = novo; indicador.style.setProperty('--am-puxado', puxado.toFixed(3));
    }, { passive: true });
    addEventListener('touchend', () => {
      if (y0 === null) return;
      y0 = null; indicador.classList.add('solto');
      if (puxado >= 1) { atualizando = true; indicador.classList.add('atualizando'); setTimeout(() => location.reload(), 350); }
      else { indicador.style.setProperty('--am-puxado', 0); setTimeout(() => indicador.classList.remove('ativo'), 450); }
      puxado = 0;
    });
  }
})();
