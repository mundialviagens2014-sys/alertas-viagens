---
name: MVO · Mundial Viagens Organização
description: Sistema interno da agência desenhado como um mapa de rotas de companhia aérea em operação.
colors:
  azul: "#1b468f"
  azul-escuro: "#12326a"
  noite: "#0b1f4b"
  noite-2: "#132a5e"
  noite-texto: "#dbe5f5"
  noite-texto-fraco: "#afc1e3"
  noite-rotulo: "#8fa6cf"
  azul-claro: "#e4ecf8"
  azul-medio: "#c2d2ea"
  laranja: "#ff914d"
  laranja-texto: "#b8501a"
  laranja-claro: "#ffeedf"
  fundo: "#f2f5f9"
  papel: "#ffffff"
  papel-2: "#f7f9fc"
  tinta: "#13234a"
  tinta-2: "#5a6782"
  linha: "#e0e6ef"
  linha-forte: "#c6d1e0"
  etiqueta-fundo: "#e8edf5"
  zap: "#168a4a"
  zap-escuro: "#0f6b38"
  ok-fundo: "#e2f3e8"
  alerta: "#9a6700"
  alerta-fundo: "#fff4d6"
  erro: "#b42318"
  erro-fundo: "#fde7e4"
typography:
  display:
    fontFamily: "Barlow Condensed, Arial Narrow, Barlow, system-ui, sans-serif"
    fontSize: "1.9rem"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "-0.01em"
    fontFeature: "tnum"
  headline:
    fontFamily: "Plus Jakarta Sans, Geist, system-ui, sans-serif"
    fontSize: "1.6rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Plus Jakarta Sans, Geist, system-ui, sans-serif"
    fontSize: "1.1rem"
    fontWeight: 700
    lineHeight: 1.25
  body:
    fontFamily: "Geist, system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "14.5px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Geist, system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "0.7rem"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "0.08em"
  codigo:
    fontFamily: "Barlow Condensed, Arial Narrow, Barlow, system-ui, sans-serif"
    fontSize: "1.1em"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "0.03em"
    fontFeature: "tnum"
  valor:
    fontFamily: "Barlow Condensed, Arial Narrow, Barlow, system-ui, sans-serif"
    fontSize: "1.7rem"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.005em"
    fontFeature: "tnum"
rounded:
  foco: "4px"
  etiqueta: "5px"
  miniatura: "6px"
  modulo: "7px"
  raio-pequeno: "8px"
  medio: "10px"
  raio: "12px"
  janela: "16px"
  pilula: "99px"
spacing:
  minimo: "4px"
  pequeno: "8px"
  medio: "12px"
  base: "16px"
  cartao: "20px"
  secao: "24px"
  pagina: "28px"
  fim-pagina: "64px"
components:
  button-primary:
    backgroundColor: "{colors.azul}"
    textColor: "{colors.papel}"
    rounded: "{rounded.raio-pequeno}"
    padding: "0.5rem 0.95rem"
    height: "2.4rem"
  button-primary-hover:
    backgroundColor: "{colors.azul-escuro}"
    textColor: "{colors.papel}"
  button-secondary:
    backgroundColor: "{colors.papel}"
    textColor: "{colors.tinta}"
    rounded: "{rounded.raio-pequeno}"
    padding: "0.5rem 0.95rem"
    height: "2.4rem"
  button-secondary-hover:
    backgroundColor: "{colors.papel}"
    textColor: "{colors.azul}"
  button-zap:
    backgroundColor: "{colors.zap}"
    textColor: "{colors.papel}"
    rounded: "{rounded.raio-pequeno}"
    padding: "0.5rem 0.95rem"
    height: "2.4rem"
  button-zap-hover:
    backgroundColor: "{colors.zap-escuro}"
    textColor: "{colors.papel}"
  button-api:
    backgroundColor: "{colors.noite}"
    textColor: "{colors.papel}"
    rounded: "{rounded.raio-pequeno}"
    padding: "0.28rem 0.65rem"
    height: "1.9rem"
  button-api-hover:
    backgroundColor: "{colors.noite-2}"
    textColor: "{colors.papel}"
  button-laranja:
    backgroundColor: "{colors.laranja-claro}"
    textColor: "{colors.laranja-texto}"
    rounded: "{rounded.raio-pequeno}"
    padding: "0.5rem 0.95rem"
    height: "2.4rem"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.tinta-2}"
    rounded: "{rounded.raio-pequeno}"
    padding: "0.5rem 0.5rem"
    height: "2.4rem"
  button-ghost-hover:
    backgroundColor: "rgba(19, 35, 74, 0.06)"
    textColor: "{colors.tinta}"
  button-mini:
    padding: "0.28rem 0.65rem"
    height: "1.9rem"
  input:
    backgroundColor: "{colors.papel}"
    textColor: "{colors.tinta}"
    rounded: "{rounded.raio-pequeno}"
    padding: "0.55rem 0.7rem"
  input-disabled:
    backgroundColor: "{colors.papel-2}"
    textColor: "{colors.tinta-2}"
  card:
    backgroundColor: "{colors.papel}"
    textColor: "{colors.tinta}"
    rounded: "{rounded.raio}"
    padding: "1.25rem"
  card-canhoto:
    backgroundColor: "{colors.papel-2}"
    textColor: "{colors.tinta}"
    padding: "1.1rem 1.25rem 1.2rem"
    width: "340px"
  card-crm:
    backgroundColor: "{colors.papel}"
    textColor: "{colors.tinta}"
    rounded: "{rounded.medio}"
    padding: "0.6rem 0.7rem"
  medidor:
    backgroundColor: "{colors.papel}"
    textColor: "{colors.noite}"
    typography: "{typography.display}"
    padding: "0.8rem 1rem 0.75rem"
  medidor-destaque:
    backgroundColor: "{colors.noite}"
    textColor: "{colors.papel}"
    typography: "{typography.display}"
    padding: "0.8rem 1rem 0.75rem"
  rota-arco:
    textColor: "{colors.azul}"
    typography: "{typography.codigo}"
  valor:
    textColor: "{colors.laranja-texto}"
    typography: "{typography.valor}"
  etiqueta:
    backgroundColor: "{colors.etiqueta-fundo}"
    textColor: "{colors.tinta-2}"
    rounded: "{rounded.etiqueta}"
    padding: "0.12rem 0.5rem"
  chip-trecho:
    backgroundColor: "{colors.azul-claro}"
    textColor: "{colors.azul}"
    rounded: "{rounded.etiqueta}"
    padding: "0.05rem 0.45rem"
  filtro:
    backgroundColor: "{colors.papel}"
    textColor: "{colors.tinta-2}"
    rounded: "{rounded.pilula}"
    padding: "0.35rem 0.8rem"
  filtro-ativo:
    backgroundColor: "{colors.noite}"
    textColor: "{colors.papel}"
    rounded: "{rounded.pilula}"
    padding: "0.35rem 0.8rem"
  nav-item:
    backgroundColor: "transparent"
    textColor: "{colors.noite-texto}"
    rounded: "{rounded.raio-pequeno}"
    padding: "0.5rem 0.75rem 0.5rem 1.15rem"
  nav-item-hover:
    backgroundColor: "rgba(255, 255, 255, 0.07)"
    textColor: "{colors.papel}"
  nav-item-active:
    backgroundColor: "rgba(255, 255, 255, 0.12)"
    textColor: "{colors.papel}"
  modulo:
    backgroundColor: "transparent"
    textColor: "{colors.azul-medio}"
    rounded: "{rounded.modulo}"
    padding: "0.48rem 0.4rem"
  modulo-ativo:
    backgroundColor: "{colors.papel}"
    textColor: "{colors.noite}"
    rounded: "{rounded.modulo}"
    padding: "0.48rem 0.4rem"
  table-head:
    backgroundColor: "{colors.papel-2}"
    textColor: "{colors.tinta-2}"
    typography: "{typography.label}"
    padding: "0.6rem 0.8rem"
  aviso:
    backgroundColor: "{colors.ok-fundo}"
    textColor: "#134d2a"
    rounded: "{rounded.raio-pequeno}"
    padding: "0.7rem 1rem"
  aviso-erro:
    backgroundColor: "{colors.erro-fundo}"
    textColor: "{colors.erro}"
    rounded: "{rounded.raio-pequeno}"
    padding: "0.7rem 1rem"
---

# Design System: MVO · Mundial Viagens Organização

## Overview
## Revisão de 10/11/2026: organização, fontes e movimento

Esta revisão vale por cima das regras abaixo onde elas se contradizem.

- **Fontes:** o texto do sistema passou a ser **Geist** (400/500/600), e títulos (h1, h2, h3), números dos medidores e saldos dos programas passaram a ser **Plus Jakarta Sans** (600/700, -0.02em, com um pouco mais de espaço entre palavras). A **Barlow Condensed** continua só nos códigos de aeroporto, chips de trecho, valores das promoções e nomes de programa. A Barlow comum segue declarada porque a imagem da promoção (`cartao.js`, `imagem_promocao.py`) é desenhada com ela.
- **Cabeçalho da página:** o `.topo` de cada página (filho direto de `.conteudo`) termina num filete de 1 px, e as ações ficam alinhadas pela base do subtítulo.
- **Medidores:** cada número virou um cartão pequeno com 12 px de espaço entre eles. O rótulo fica em cima e o algarismo embaixo, alinhado pela base em toda a faixa. O destaque é azul-noite com um brilho laranja tênue no canto.
- **Profundidade:** a Regra da Borda Única foi suavizada. Cartões, tabelas, medidores, campos e botões ganham `--sombra-xs` (1 px, 5%). O que é clicável (cartão do CRM, medidor-link, cartão de programa, bilhete) ganha `--sombra-sm`/`--sombra-md` ao passar o mouse. Modal e login continuam sendo os únicos que flutuam de verdade.
- **Cores de destaque:** a pílula de filtro ativa passou a ser azul da marca (antes, azul-noite). Os nomes na primeira coluna das tabelas ficam em tinta (peso 600) e só ficam azuis ao passar o mouse. Etiquetas de situação (ok, revisar, off, futuro, entrada, saída, intenção) viraram pílulas com um ponto da cor do estado. O botão primário e o do WhatsApp ganharam um brilho interno fino.
- **Tabelas:** os cabeçalhos estão em caixa normal (0.76rem/600, sem caixa-alta), as células são centralizadas na vertical e as linhas ficaram um pouco mais altas.
- **Campos:** o foco é a borda azul com halo `--anel` (box-shadow de 3 px a 16%), não mais o contorno. O contorno transparente continua para o modo de alto contraste.
- **Passo a passo (`ol.passos`):** cada passo tem um número em círculo, uma linha ligando até o próximo, um título curto (`<strong>`) e o detalhe embaixo. Dentro do passo, `<b>` é o nome de um botão ou menu da tela e aparece como uma tecla. `.obs` é a observação em letra menor. O último passo, o que leva ao formulário logo abaixo, leva `class="final"` (círculo cheio). Para listas curtas, use `.passos.compacto`.
- **Explicações:** `ul.pontos` é a lista de "Como funciona", com um losango azul por item e um filete entre os itens. Um `p.ajuda` solto no cartão (não a dica de um campo) vira nota: fundo claro, filete azul à esquerda e ícone "i". Também dá para usar `p.nota` em qualquer lugar.
- **Filtros (todas as telas):** `form.filtros` é um painel branco. Na primeira linha ficam os campos (o de busca, `type=search` ou `name=q`, é o maior; as listas têm no máximo 270 px). No rodapé ficam as caixas de marcar como chips, a contagem, "Limpar" (um `<a>` sem classe) e o botão de buscar em azul à direita. Sem nada para o rodapé, o botão sobe para a linha dos campos. Abas de escolha única ficam em `.segmentos`, um trilho cinza com a aba ativa em branco. Quando há abas em cima de um formulário, os dois ficam juntos em `.painel-filtros` (Fila, Histórico, Pedidos, Promoções).
- **Celular (até 700 px):** o subtítulo da página mostra no máximo 3 linhas e as ações do topo ficam em duas colunas. Os números do dia viram uma faixa que desliza para o lado, com encaixe. Toda `table.tabela` com cabeçalho vira cartões: o script do `base.html` copia o título de cada coluna para `data-rotulo`, e cada célula aparece como "rótulo … valor". Células vazias somem. A primeira coluna é o título do cartão. Para manter uma tabela como tabela, use a classe `sem-cartoes`.
- **Movimento (sempre atrás de `prefers-reduced-motion: no-preference`):** ao chegar, os blocos do conteúdo sobem 6 px e aparecem em sequência (0,36 s, até 0,18 s de atraso). Os medidores e os cartões de programa entram um depois do outro, e os avisos aparecem deslizando. Ao passar o mouse, medidores-link e cartões de programa sobem 2 px e o cartão do CRM sobe 1 px. Pílulas de filtro e botões encolhem levemente ao toque. Nada se move por causa da rolagem.


**Creative North Star: "Mapa de Rotas em Operação"**

O MVO é a sala de operação de uma companhia aérea pequena. À esquerda, o céu: uma barra lateral azul-noite com arcos de rota tênues desenhados ao fundo, como o verso de um mapa de bordo. À direita, o quadro de partidas: um fundo cinza-azulado frio onde a equipe lê fila, clientes, CRM e Manager o dia inteiro. Cada trecho é desenhado, não escrito — dois códigos de aeroporto em Barlow Condensed ligados por um arco laranja com o avião no ápice. Esse gesto único atravessa o sistema inteiro (fila, promoções, cliente, interesses, histórico, CRM, pedidos, emissões) e, nos Relatórios e no CRM, vira o leque de rotas que sai de Fortaleza.

A densidade é de operação, com ar para ler: corpo em 15 px, tabelas com algarismos tabulares, cartões brancos de uma borda só sobre o fundo frio, sem sombra. A cor é disciplinada. O azul da marca é o azul do que age: botão primário, links, foco, códigos de aeroporto. O laranja é raro e por isso visível: arcos de voo, o ponto do item ativo no menu, os contadores e o valor da promoção (em sua versão legível sobre branco). Os estados ok, alerta e erro têm cores próprias e fixas, que nunca disputam com a marca. O sistema recusa de propósito o painel administrativo cinza-com-um-azul e o cartão-bilhete picotado do visual anterior.

O movimento é pouco e tem propósito: o arco se desenha uma vez ao chegar na tela, o avião desliza um pouco pela rota ao passar o mouse, o destaque do menu viaja até o item novo na troca de página. Quem pediu "reduzir movimento" não vê nada disso. Os controles são os da web (botões, campos, selects, details), não há biblioteca de terceiros nem build, e as fontes são servidas pelo próprio site. A página pública do cliente (`app/templates/para_voce.html` e `app/static/para-voce.css`) fica fora deste sistema e está excluída do detector em `.impeccable/config.json`.

**Key Characteristics:**
- Um gesto de assinatura: o arco de voo entre códigos de aeroporto (macro `arco`), com o leque de rotas como sua forma agregada.
- Uma família tipográfica, Barlow: Condensed em caixa-alta para códigos, números grandes e totais; Barlow para todo o resto.
- Céu azul-noite na barra lateral e no login; quadro de partidas claro e frio na área de trabalho.
- Cartões de borda única, sem sombra; profundidade por tom (fundo → papel-2 → papel), não por elevação.
- Laranja raro (arcos, ponto ativo, contadores, valores); azul para ação; estados fixos em verde, âmbar e vermelho.
- Densidade de operação: 15 px de corpo, tabelas compactas, algarismos tabulares, faixa de medidores emendados que encolhem em vez de cortar.
- Movimento com ease-out forte (120–300 ms), sempre atrás de `prefers-reduced-motion: no-preference`.

## Colors

Um céu azul-noite, um papel frio e dois sinais: o azul que age e o laranja que marca a rota.

### Primary
- **Azul da marca** (#1b468f): a cor da ação — botão primário, links, caret dos campos, contorno de foco no teclado, códigos de aeroporto, `accent-color` das caixas, cor principal dos gráficos, borda do balão com resposta nova e da coluna do funil que recebe um cartão. Fixo por compromisso de marca.
- **Azul profundo** (#12326a): o azul da marca em hover/pressionado (botão primário), o texto de `code` e os rótulos de valor nos gráficos. Hover sempre escurece, nunca clareia.
- **Azul-noite** (#0b1f4b): o céu — fundo da barra lateral e da tela de login, botão "Pela API", faixa de nome dos programas, etiqueta "admin", títulos (h1/h2), cabeçalho das colunas do funil e `theme-color`. No build também marca a posição atual: o medidor em destaque e a pílula de filtro ativa (o contrato previa azul da marca para seleção; o build entregou a noite e assim foi aprovado — ver a linha de notas do documentador, não uma regra).
- **Azul-noite 2** (#132a5e): relevo dentro do céu — hover do botão "Pela API".
- **Texto sobre a noite** (#dbe5f5), **texto fraco sobre a noite** (#afc1e3) e **rótulo de seção** (#8fa6cf): os três tons de texto da barra lateral — itens do menu, rodapé com nome do usuário, e as seções em caixa-alta.
- **Azul-claro** (#e4ecf8): fundo de seleção suave — chips de trecho, `code`, halo de foco dos campos (3 px), etiquetas "gestor" e "futuro", coluna do funil recebendo um cartão.
- **Azul-médio** (#c2d2ea): texto secundário sobre a noite — pílula de módulos inativa, a legenda do medidor em destaque e a constante `AZUL_MEDIO` dos gráficos.

### Secondary
- **Laranja de rota** (#ff914d): só onde a rota passa — a linha do arco e os pontos de pouso, o ponto do item ativo no menu (com halo a 22%), a barra sob o módulo ativo, os contadores do menu, a barra fina de progresso no topo enquanto a página carrega, os arcos ao fundo da barra lateral (a 10–22% de opacidade) e as cores de seleção de texto. Fixo por compromisso de marca. Nunca é cor de texto sobre fundo claro.
- **Laranja legível** (#b8501a): o laranja como texto sobre branco (contraste 5:1) — o valor da promoção, o valor do cartão do CRM, a etiqueta "à mão", o papel "emissor", o botão laranja ("Imagem com a logo").
- **Laranja-claro** (#ffeedf): fundo do botão laranja e das etiquetas em laranja ("saída", "emissor", "à mão").

### Tertiary
- **Verde WhatsApp** (#168a4a) e **verde escuro** (#0f6b38): o botão "Enviar no WhatsApp" (a ação primária da fila) e seu hover; o verde também assina o ícone de confirmação (`.icone.ok`) e a etiqueta "interessado"; o verde escuro, o status "Enviada ✓", as etiquetas "ok"/"entrada" e os ganhos ("mais").
- **Verde-fundo** (#e2f3e8): avisos de sucesso, etiquetas "ok"/"entrada"/"interessado", balões da equipe nas respostas do WhatsApp.
- **Âmbar** (#9a6700) sobre **âmbar-fundo** (#fff4d6): alerta — "revisar antes de enviar", programa sem preço, etiqueta "em dúvida".
- **Vermelho** (#b42318) sobre **vermelho-fundo** (#fde7e4): erro, perdas ("menos"), botão "perigo", etiquetas "não quer"/"pediu para sair", aviso de senha fraca; a mesma constante `VERMELHO` nos gráficos.

### Neutral
- **Fundo** (#eef2f8): o quadro de partidas — fundo da área de trabalho, cinza-azulado frio.
- **Papel** (#ffffff): cartões, tabelas, campos, medidores, pílula do módulo ativo.
- **Papel 2** (#f5f8fc): o segundo neutro — cabeçalhos de tabela, a coluna da promoção (canhoto), hover de medidor-link, campos desabilitados, segredos do cofre, balões do cliente.
- **Tinta** (#13234a): texto corrido e rótulos de campos marcados.
- **Tinta 2** (#5a6782): texto secundário, subtítulos, rótulos em caixa-alta, cabeçalhos de tabela, botão fantasma, dica de rolagem, eixos e legendas dos gráficos (`CINZA`).
- **Linha** (#d9e1ee): a borda única de cartões, tabelas, medidores e divisórias; a grade dos gráficos (`LINHA`).
- **Linha forte** (#b9c7dd): borda de botões e campos, pílulas de filtro, borda tracejada do estado vazio, barra de rolagem.
- **Fundo de etiqueta** (#e8edf5): etiquetas neutras ("atendente", "off", tipo "outro") e chips desligados.

Os gráficos (`app/graficos.py`) usam os mesmos valores das variáveis: barras em azul da marca (e laranja ou verde quando a série pede), grade em linha, eixos e legendas em tinta 2, totais em azul profundo; as roscas por categoria usam, nesta ordem, azul, laranja, verde e depois #7c5cbf, #0f8b8d, #c2410c, #9a6700, #475569, #be185d, #2563eb; o leque de rotas é laranja com espessura (1.2–5 unidades) e opacidade (45–100%) proporcionais à quantidade, pontos de chegada em azul e a origem em Barlow Condensed.

### Named Rules
**A Regra do Laranja Raro.** O laranja puro entra em poucos lugares: arcos e pontos de rota, o ponto do item ativo no menu e a barra sob o módulo ativo, os contadores do menu e a barra de progresso da navegação. Sobre fundo claro, texto laranja é sempre o laranja legível (#b8501a). Se uma tela nova pede laranja em outro lugar, a resposta é não.

**A Regra do Azul Que Age.** Botão primário, links, códigos de aeroporto, caret, contorno de foco e o halo de seleção dos campos são azul da marca; o hover escurece para o azul profundo. Azul nunca é cor decorativa de fundo fora do azul-claro de seleção.

**A Regra dos Estados Fixos.** ok = verde, alerta = âmbar, erro = vermelho, cada um com o seu fundo claro. Nem azul nem laranja indicam estado; nenhuma tela inventa uma quarta cor de estado.

## Typography

**Display Font:** Barlow Condensed 500/600/700 (com Arial Narrow, Barlow, system-ui)
**Body Font:** Barlow 400/500/600/700 (com system-ui, -apple-system, Segoe UI, Roboto)
**Label/Mono Font:** monoespaçada do sistema (`--mono`: ui-monospace, SFMono-Regular, Menlo, Consolas) só para `code` e os segredos do cofre — nada baixado

**Character:** Uma família só, servida pelo próprio site (`app/static/fontes/`, subconjunto latino em woff2, SIL OFL). A Condensed em caixa-alta dá a voz do painel de partidas aos códigos de aeroporto e aos números grandes; a Barlow regular, levemente industrial, mantém o texto corrido legível por horas. O contraste do sistema vem da largura, não de uma segunda família.

### Hierarchy
- **Display** (700, 1.9rem, line-height 1, -0.01em, tabular): os números do dia nos medidores (`.numero b`) e o trecho grande no canhoto da promoção (`.rota-arco.grande`). Cada medidor é um contêiner (`container-type: inline-size`): o algarismo cai para 1.6rem em células de até 190 px e 1.4rem até 150 px e, se ainda não couber, quebra no espaço ("R$ | 117.160") — nunca é cortado com reticências; no celular, 1.7rem. Variantes menores da mesma voz: saldo dos programas (1.6rem) e valor da promoção (ver Valor).
- **Headline** (700, 1.75rem, 1.15, -0.01em, azul-noite): o título da página (h1), um por tela, com `text-wrap: balance` e o subtítulo cinza (`.sub`, tinta 2, máx. 72ch) logo abaixo.
- **Title** (700, 1.1rem, 1.25, azul-noite): h2 dos cartões e seções; o título de promoção não-aérea usa 1.12rem; cabeçalho das colunas do funil e do leque no CRM, 0.95rem.
- **Body** (400, 15px, 1.5, tinta): texto corrido. Secundário em 0.85rem (`.pequeno`), 0.82rem e 0.8rem; células de tabela e botões em 0.9rem com peso 600 nos botões; dica de rolagem em 0.74rem.
- **Label** (600, 0.7rem, +0.08em, CAIXA-ALTA, tinta 2): cabeçalhos de tabela, rótulos dos detalhes da promoção (0.07em, em linha com o valor), títulos de sub-bloco ("Quem pode emitir"), rótulos do cofre; seções do menu em 0.74rem/+0.12em; etiquetas em 0.7rem/+0.06em.
- **Código** (Condensed 700, 1.1em do contexto, +0.03em, azul): os códigos de aeroporto do arco; `.codigo` (1.08em, +0.04em) para localizadores e trechos incompletos; chips de trecho em Condensed 600 0.9rem; a origem do leque em 17 unidades.
- **Valor** (Condensed 700, 1.7rem, 1.1, -0.005em, tabular, laranja legível): o preço da promoção, à direita do trecho no canhoto; no cartão do CRM, 1.1rem.

### Named Rules
**A Regra da Condensada.** Barlow Condensed aparece só em caixa-alta de códigos de aeroporto, números grandes (medidores, valores, saldos, totais) e nomes de programa. Nunca em texto corrido, títulos de página, rótulos ou botões.

**A Regra dos Algarismos Tabulares.** Todo número em coluna, medidor, contador ou soma usa `font-variant-numeric: tabular-nums`; colunas numéricas (`.num`) alinham à direita e não quebram.

**A Regra do Rótulo Miúdo.** Caixa-alta pequena (0.7–0.74rem, +0.06 a +0.12em, peso 600) só como rótulo de campo, cabeçalho de tabela, seção do menu, sub-bloco ou etiqueta. Nunca como olho acima de um título: h1 e h2 vêm sozinhos, com o subtítulo cinza embaixo.

## Layout

A tela é uma grade de duas colunas: barra lateral fixa de 248 px (`.app { grid-template-columns: 248px 1fr }`) e a área de trabalho. A barra é `sticky`, ocupa 100vh e rola por dentro; o menu rola sozinho até o item ativo quando é comprido. A área de trabalho (`.conteudo`) tem padding de 1.75rem no topo, `clamp(1rem, 3vw, 2.5rem)` nas laterais e 4rem embaixo, e largura máxima de 1240 px; cada coluna da grade tem `min-width: 0` para que tabelas largas rolem dentro de `.rolagem` em vez de alargar a página.

Toda página abre com `.topo`: h1 e subtítulo à esquerda, as ações (`.acoes`, gap 0.5rem) à direita, com quebra automática e 1.25rem de margem abaixo. Depois vêm, nesta ordem quando existem: a faixa de medidores (`.numeros`, 1.5rem abaixo), os filtros em uma linha (`.filtros`, gap 0.5rem, 1rem abaixo) e a lista — cartões `.bilhete`, tabela `.tabela` ou quadro `.quadro`.

Ritmo de espaço em múltiplos de 4 px: 4 (ajustes finos), 8 (gap entre botões e filtros, entre itens do menu 2 px), 12 (quadro do CRM, cartões de programa), 16 (campos de formulário, grade de relatórios), 20 (padding e distância entre cartões; gap de `.grade-2`), 24 (abaixo dos medidores), 28 (topo da página), 64 (fim da página). Grades: `.grade-2` em `minmax(0, 1.1fr) minmax(0, 1fr)`; `.grade-3` em `auto-fit minmax(300px, 1fr)`; `.grade-2-igual` em `auto-fit minmax(420px, 1fr)`; `.campos` em `auto-fit minmax(180px, 1fr)`; `.programas-painel` em `auto-fill minmax(210px, 1fr)`; o canhoto da promoção tem 340 px fixos; o quadro do CRM corre em colunas de `minmax(250px, 1fr)` com rolagem horizontal; o topo do CRM (`.crm-topo`) põe a faixa de cinco medidores ao lado do leque (`minmax(0, 1fr) minmax(0, 420px)`).

A faixa de medidores é `auto-fit minmax(160px, 1fr)` por padrão; faixas com muitos medidores declaram a contagem (`.numeros.colunas-4` nos Relatórios, cinco colunas no topo do CRM) para a última linha não ficar com células vazias.

Densidade de operação: 15 px de corpo, células de tabela com 0.6rem × 0.8rem (compacta: 0.35rem × 0.5rem), medidores com 0.8rem × 1rem, cartão do CRM com 0.6rem × 0.7rem.

Dois pontos de quebra. O principal, em 900 px: a barra lateral vira uma faixa no topo (sem os arcos ao fundo), o logo encolhe para 140 px, o menu fica atrás do botão "Menu" (`.abrir-menu`, `aria-expanded`) enquanto a pílula de módulos continua à vista; o ponto laranja do item ativo some; o bilhete, as grades (`.grade-2`, `.grade-3`, `.grade-2-igual`) e os destinatários empilham em uma coluna; todas as faixas de medidores viram duas colunas (o último, se ímpar, ocupa a linha inteira). O secundário, em 1200 px, só para o topo do CRM: o leque desce para baixo da faixa de medidores. Acima de 901 px a barra lateral, o indicador do menu e a pílula ativa recebem `view-transition-name` para ficarem parados (ou deslizarem) na troca de página.

### Named Rules
**A Regra da Faixa de Medidores.** Os números do dia ficam em uma única faixa (`.numeros`): células emendadas por filetes de 1 px dentro de uma borda só, o primeiro medidor em azul-noite (`.destaque`) com o algarismo em Condensed. Faixas com mais de três medidores declaram as colunas (`.colunas-4`, `.colunas-5`); o algarismo encolhe por consulta de contêiner e quebra no espaço, mas nunca é cortado. Nunca em cartões soltos com sombra.

## Elevation & Depth

O sistema é plano: cartões, tabelas, medidores, campos e botões têm uma borda de 1 px (linha ou linha forte) e nenhuma sombra — "sem sombra fantasma" é um comentário literal do código. A profundidade vem do tom: fundo (#eef2f8) → papel 2 (#f5f8fc) → papel (#fff) na área de trabalho; azul-noite → azul-noite 2 e véus brancos (7% hover, 12% ativo, 28% de preto no poço da pílula de módulos) na barra lateral. Só o que flutua sobre a página ganha sombra, sempre difusa, larga e com deslocamento negativo de espalhamento: a janela modal e o cartão de login. O ponto laranja do item ativo tem um halo de 3 px a 22% — um anel, não uma sombra. Uma tabela que transborda não ganha sombra na borda: sua borda direita some em degradê (máscara de 32 px) e uma dica em texto avisa que há mais colunas.

### Shadow Vocabulary
- **Janela modal** (`box-shadow: 0 24px 64px -16px rgba(0,0,0,.45)`): a caixa da imagem da promoção, sobre um véu `rgba(11,31,75,.62)`; raio 16 px.
- **Cartão de login** (`box-shadow: 0 30px 60px -20px rgba(0,0,0,.55)`): o único cartão sem borda, flutuando sobre o céu azul-noite.
- **Halo do item ativo** (`box-shadow: 0 0 0 3px rgba(255,145,77,.22)`): o anel ao redor do ponto laranja no menu (e no indicador que desliza).
- **Sombra de flutuação** (`--sombra: 0 1px 2px rgba(11,31,75,.06), 0 10px 24px -12px rgba(11,31,75,.18)`): declarada para cartões que flutuam; hoje consumida apenas pela página pública do cliente, nunca por uma tela interna.

### Named Rules
**A Regra da Borda Única.** Superfície de trabalho = uma borda de 1 px, sem sombra, sem borda dupla, sem gradiente. Sombra só em modal e login. Hover de cartão clicável muda a borda (para azul ou linha forte) ou o fundo (para papel 2), nunca eleva.

## Shapes

Cantos suavemente arredondados, em escala curta e fixa: 12 px para cartões, tabelas, faixa de medidores, bilhete e colunas do funil; 8 px para botões, campos, itens do menu, avisos, imagem da promoção e cofre; 10 px para o poço da pílula de módulos, o cartão do CRM e os balões de conversa; 7 px para cada módulo dentro do poço; 5 px para etiquetas e chips; 6 px para miniaturas e o texto original; 16 px para a janela modal; 99 px para pílulas (filtros, contadores, papel do usuário); 4 px para `code` e o contorno de foco. Círculos para pontos: 6 px no item ativo do menu, 8 px no cabeçalho das colunas do funil (na cor da etapa), 2.2 de raio nos pousos do arco.

Bordas são únicas e de 1 px. Tracejado tem dois significados: borda `1px dashed linha-forte` para o estado vazio e `outline: 2px dashed azul` para a coluna do funil que vai receber um cartão arrastado. Os balões do WhatsApp têm um canto de 4 px que aponta para quem fala (superior esquerdo no cliente, superior direito na equipe).

A geometria de assinatura é o arco de voo: uma só curva quadrática, `M2 18 Q32 -6 62 18` numa caixa de 64 × 22, traço laranja de 2 px (2.5 no grande) com pontas redondas, dois pontos de pouso e a silhueta do avião em azul posicionada a 52% do caminho (`offset-path: var(--arco)`), virada para a direção do voo. Ida e volta ganha a linha de retorno por baixo, tracejada `3 3` a 55%. A mesma curva, em escala, fecha o leque de rotas dos Relatórios e do CRM e os arcos hairline (1.3 px) ao fundo da barra lateral e do login.

Ícones são SVG de um traço só (`.icone`: 1em, `stroke: currentColor`, 1.8 de espessura, pontas e junções redondas, alinhado a -0.12em): três traços do menu, olho, cadeado e o círculo com check (`.icone.ok`, em verde). Nunca emoji.

### Named Rules
**A Regra do Arco.** Todo trecho com os dois códigos é desenhado pela macro `arco(origem, destino)` — `grande=true` no canhoto, `ida_volta=true` quando há retorno. O texto "FOR → MCO" é só o fallback para trechos incompletos (`rota_emissao`) e a linha das cidades por extenso embaixo do arco.

## Components

### Buttons
Firmes e baixos: borda única, rótulo em peso 600, resposta imediata ao toque.
- **Shape:** cantos de 8 px; padding 0.5rem × 0.95rem; altura mínima 2.4rem (38 px); 0.9rem/600; `white-space: nowrap`; ícone de 1em com gap 0.4rem.
- **Padrão (`.btn`):** papel com borda linha forte e texto tinta; hover: borda e texto em azul da marca.
- **Primário (`.primario`):** azul da marca com texto branco; hover azul profundo. Um por `.topo` ("+ Colar promoção", "+ Registrar emissão", "Entrar").
- **WhatsApp (`.zap`):** verde (#168a4a) com texto branco; hover verde escuro. A ação primária de cada destinatário na fila; desabilitado quando falta consentimento.
- **API (`.api`):** azul-noite com texto branco; hover azul-noite 2; enquanto envia, opacidade 55% e cursor `wait`. Sempre `.mini`, ao lado do botão verde.
- **Laranja (`.laranja`):** laranja-claro com borda #ffc9a3 e texto laranja legível; hover #ffe0c8 com borda laranja. Só para "Imagem com a logo".
- **Perigo (`.perigo`):** padrão com texto vermelho; hover borda vermelha. **Fantasma (`.fantasma`):** sem fundo nem borda, texto tinta 2, padding lateral 0.5rem; hover tinta sobre `rgba(19,35,74,.06)` — "Pular", "Descartar", "Fechar".
- **Mini (`.mini`):** 0.28rem × 0.65rem, 0.8rem, altura mínima 1.9rem — ações dentro de tabelas, cartões e ao lado do botão principal.
- **Hover / Focus:** cores em 150 ms; `:active` encolhe para `scale(.97)` em 120 ms; foco de teclado com contorno de 2 px em azul (laranja sobre a noite) e offset 2 px.
- **Carregando (`.carregando`):** o texto fica transparente e um anel de 1.1em na cor do rótulo (`--giro`) gira em 0.6 s; o botão não muda de tamanho. **Desabilitado:** opacidade 45%, `cursor: not-allowed`.

### Chips
Rótulos pequenos em caixa-alta que classificam sem gritar.
- **Etiqueta (`.etiqueta`):** 0.7rem/600, +0.06em, caixa-alta, 5 px, padding 0.12rem × 0.5rem; neutra em fundo de etiqueta com tinta 2. Variantes por fonte (telegram #dfeefb/#1d5f99, google_flights #e7f0e1/#3c6b1f, whatsapp #e3f5e9/#0f6b38), por tipo (hotel #fdf0d5/#8a5a00, pacote #ede7fb/#4b2a9a, cruzeiro #dff3f7/#0b5c70, outro = neutra), por status (revisar = âmbar, ok/entrada = verde, off = neutra com #6b7893, saída = laranja-claro/laranja legível, futuro = azul-claro/azul), por papel (admin = azul-noite/branco, gestor = azul-claro/azul, emissor = laranja-claro/laranja legível, atendente = neutra) e por intenção no CRM (interesse verde, dúvida âmbar, recusa/sair vermelho, outro = fundo/tinta 2).
- **Chip de trecho (`.chip`):** Condensed 600 0.9rem, +0.03em, azul sobre azul-claro, 5 px, padding 0.05rem × 0.45rem — os interesses do cliente ("FOR→LIS"); `.off` em fundo de etiqueta, #7b879d e riscado.
- **Contador do menu (`.contador`):** pílula laranja com texto azul-noite, 0.72rem/700, tabular.

### Cards / Containers
Papel branco, uma borda, canto de 12 px — nada flutua.
- **Corner Style:** 12 px (`--raio`); 10 px no cartão do CRM.
- **Background:** papel; papel 2 na coluna da promoção (canhoto), nos cabeçalhos de tabela e nos balões do cliente.
- **Shadow Strategy:** nenhuma (ver Elevation & Depth); hover de cartão clicável muda a borda para azul (`.programa-cartao`) ou linha forte (`.cartao-crm`), ou o fundo para papel 2 (`a.numero`).
- **Border:** 1 px linha.
- **Internal Padding:** 1.25rem (`.cartao`); 1.1rem × 1.25rem × 1.2rem no canhoto; 0.6rem × 0.7rem no cartão do CRM; margem de 1.25rem entre cartões.
- **Bilhete (`.bilhete`):** o cartão da promoção na fila — duas colunas (340 px + 1fr) com uma divisória de 1 px. À esquerda, o canhoto em papel 2: a cabeça (`.cabeca-canhoto`) com o arco grande e as cidades à esquerda e o valor à direita (alinhado à direita, sem quebra); a imagem, se houver; os detalhes (`.detalhes`) como pares rótulo miúdo + valor em linha, que fluem e quebram (gap 0.25rem × 0.9rem; `.largo` ocupa a linha toda); o aviso de revisão em âmbar; a linha da fonte (`.linha-fonte`) com a etiqueta da fonte e o "Texto original" na mesma linha (aberto, ele ocupa a largura toda sobre papel); e as ações mini. À direita, o cabeçalho "N cliente(s) interessado(s)" em rótulo miúdo e uma linha por destinatário (nome 600, info 0.82rem, status "Enviada ✓" em verde escuro, botões alinhados à direita). No celular empilha.
- **Faixa de medidores (`.numeros` / `.numero`):** ver A Regra da Faixa de Medidores; o algarismo em display (encolhendo por contêiner), a legenda em 0.8rem tinta 2, a nota em 0.74rem; `.destaque` em azul-noite com legenda azul-médio; `a.numero` é clicável.
- **Cartão de programa (`.programa-cartao`):** faixa superior azul-noite com o nome em Condensed 600 0.95rem caixa-alta +0.1em; saldo em Condensed 700 1.6rem; linhas em 0.8rem.
- **Cartão do CRM (`.cartao-crm`):** arrastável (`cursor: grab`, 50% de opacidade enquanto arrasta); nome 600 com o valor em Condensed laranja legível à direita; o trecho com o arco; evento em 0.78rem; rodapé com data, etiqueta de intenção e links em azul 600; a marca "à mão" no canto superior direito.
- **Estado vazio (`.vazio`):** papel com borda tracejada linha forte, 12 px, centrado, padding 3rem × 1rem; título 1.1rem azul-noite e uma frase com o próximo passo.
- **Aviso (`.aviso`):** 8 px, padding 0.7rem × 1rem; sucesso em verde-fundo com borda #bfe3cb e texto #134d2a; `.erro` em vermelho-fundo com borda #f3c0b8 e texto vermelho.
- **Balões (`.balao`):** 12 px, máx. 640 px, 0.92rem; cliente em papel 2 com borda linha (azul quando é nova resposta) e canto superior esquerdo de 4 px; equipe em verde-fundo com canto superior direito de 4 px; a hora em 0.72rem.
- **Janela modal (`.modal-caixa`):** 16 px, `min(520px, 100%)`, padding 1rem × 1.25rem × 1.25rem, sobre o véu azul-noite a 62%.

### Inputs / Fields
Campos de web comuns, com um halo azul-claro no foco.
- **Style:** borda 1 px linha forte, 8 px, padding 0.55rem × 0.7rem, papel, texto tinta, `caret-color` azul; placeholder #6f7d99; largura 100% (auto dentro de `.filtros`); textarea com 120 px mínimos e redimensionável na vertical; `select` nativo; `accent-color` azul nas caixas e rádios.
- **Label:** 600, 0.85rem, azul-noite, 0.3rem acima; `.ajuda` em 0.8rem tinta 2 abaixo; `.campo-check` alinha caixa e texto com gap 0.5rem.
- **Focus:** borda azul + contorno de 3 px azul-claro sem offset, que acende em 150 ms (parte de transparente fora do modo de alto contraste).
- **Disabled:** papel 2 com texto tinta 2 e `cursor: not-allowed`.
- **Filtros:** selects e caixas dentro de `form.filtros` aplicam a busca ao mudar; o campo de busca é `type=search`; origem/destino usam a `datalist` de aeroportos.

### Navigation
O céu do mapa: azul-noite, arcos tênues, um ponto laranja onde a rota está agora.
- **Barra lateral (`.lateral`):** 248 px, azul-noite com os arcos SVG ao fundo (laranja a 10–22%, dois pontos de cidade), padding 1.1rem × 0.9rem × 1.25rem, coluna com gap 1.1rem; o logo claro (`logo-claro.png`, 200 px) com a legenda "MVO · Organização" em 0.72rem caixa-alta +0.1em e uma divisória branca a 12%.
- **Pílula de módulos (`.modulos`):** poço preto a 28% com 10 px e 3 px de respiro; cada módulo 600 0.86rem +0.02em em azul-médio, 7 px; hover branco sobre branco a 7%; ativo em papel com texto azul-noite e uma barra laranja de 20 × 3 px embaixo. Visível também no celular.
- **Menu (`.menu a`):** 500, texto sobre a noite, 8 px, padding 0.5rem × 0.75rem × 0.5rem × 1.15rem, gap 2 px; hover branco a 7%; ativo 600 branco sobre branco a 12% com o ponto laranja de 6 px e halo à esquerda. Seções em rótulo de seção 0.74rem caixa-alta +0.12em; grupo "Configuração" é um `details` com chevron desenhado em borda que gira 90°; contadores em pílula laranja.
- **Indicador:** o script envolve o item ativo num `.indicador` com `view-transition-name`, que desliza até o item novo em 300 ms (`--rapido`) na troca de página; a barra lateral fica parada.
- **Rodapé:** nome em 0.82rem texto fraco, papel do usuário em pílula branca a 12% caixa-alta 0.72rem, "Sair" em branco; `margin-top: auto`, no fim da coluna que rola.
- **Celular (≤ 900 px):** faixa no topo sem arcos, logo 140 px, botão "Menu" com ícone de três traços (`.icone`), menu em linha quando aberto, pílula de módulos sempre à vista.
- **Foco:** contorno laranja de 2 px dentro da barra lateral e do login (azul no resto).

### Tables
O quadro de partidas: uma borda, cabeçalho em rótulo miúdo, algarismos alinhados.
- **Style:** `.tabela` em papel, borda linha, 12 px, `border-collapse`; th em papel 2 com rótulo miúdo (0.7rem, +0.08em, tinta 2) sem quebra; td 0.9rem, padding 0.6rem × 0.8rem, divisórias de 1 px, alinhamento ao topo; hover de linha #f8fafd em 120 ms; `.num` à direita, tabular, sem quebra; `.codigo` para localizadores; linhas canceladas a 50% com valores riscados; `.tabela-compacta` com 0.35rem × 0.5rem e 0.85rem; sempre dentro de `.rolagem`.
- **Transbordo:** quando a tabela não cabe, `base.html` marca `.rolagem.transborda` — a borda direita some em degradê (`mask-image` até 20% nos últimos 32 px) — e insere a dica `.dica-rolagem` ("Deslize para o lado para ver as outras colunas →", 0.74rem, tinta 2) logo abaixo.

### Filters
- **Pílulas (`.filtros a`):** 99 px, borda linha forte, papel, tinta 2, 600 0.85rem, padding 0.35rem × 0.8rem; ativo em azul-noite com texto branco; hover borda e texto azul. Campos e selects na mesma linha com largura própria; "Limpar" como link.

### Arco de rota (assinatura)
Dois códigos em Condensed azul ligados pelo arco laranja com o avião no ápice (`.rota-arco`, macro `arco`). Tamanho herdado (1.1em; `.grande` 1.9rem com +0.04em no canhoto). Ao entrar na tela o traço se desenha em 550 ms (`stroke-dasharray: 80`, `--rapido`, uma vez); ao passar o mouse no bilhete, na linha da tabela, no cartão do CRM ou no link, o avião desliza de 52% para 72% do caminho em 200 ms. `b.qualquer` ("qualquer destino") em peso 500, sem caixa-alta, tinta 2. Em `prefers-reduced-motion: reduce` o arco aparece pronto e o avião fica parado.

### Leque de rotas (assinatura)
`graficos.leque(origem, itens)`: desenhado em 380 unidades de largura (26 por linha) para continuar legível encolhido a ~320 px no celular; quem o mostra maior limita a largura por CSS (`.grafico.leque { max-width: 440px }`, o tamanho desenhado × 1.15). A origem à esquerda (ponto azul de raio 6 com contorno branco e o código em Condensed 17, +0.04em) e um arco laranja até cada destino (ponto azul de raio 3.5; rótulo 600 em 14, azul profundo; a quantidade em 13.5, tinta 2, encostada na borda direita, com singular e plural), com espessura de 1.2 a 5 e opacidade de 45 a 100% proporcionais à quantidade. Vive em um `.cartao` ("Rotas em andamento" no CRM, "Destinos mais pedidos" nos Relatórios). Como todos os gráficos, é SVG desenhado no servidor em Barlow, com `<title>` em cada marca.

### Imagem da promoção (raster gerado)
`app/static/cartao.js` desenha no navegador, em canvas 1080×1080, a imagem que a equipe cola no WhatsApp: fundo azul da marca com dois arcos de rota tênues em laranja (22% e 12%), a logo oficial, cartão branco de cantos 36 com os códigos em Barlow Condensed 700 (120 px) ligados pelo mesmo arco laranja (traço 7) com o avião da macro `arco` a 52% do caminho, cidades em Barlow 600, "A PARTIR DE" e o valor em Condensed laranja, datas e companhia em Barlow 600, rodapé em branco sobre o azul. As fontes são as servidas pelo site (`document.fonts.load` antes de desenhar). Origem registrada em `app/static/PROVENIENCIA.md`.

### Charts
SVG do servidor (`app/graficos.py`), sem script nem biblioteca, em `.grafico` (100% de largura, Barlow). Barras com `rx` 3 (horizontais 4, funil 5), grade em linha, eixo e legendas em tinta 2 (10.5–11.5), totais em azul profundo 700, rosca com traço de 22 e o total no centro, funil em azul com opacidade decrescente por etapa. Estado vazio: "Sem dados no período" em tinta 2.

## Do's and Don'ts

### Do:
- **Do** desenhar todo trecho com os dois códigos pela macro `arco(origem, destino)` — `grande=true` no canhoto, `ida_volta=true` quando há retorno — e deixar a linha das cidades por extenso (0.82rem, tinta 2) embaixo.
- **Do** abrir cada tela com `.topo` (h1 + `.sub` + ações), um único `.btn.primario` por topo, e os números do dia em `.numeros` com o primeiro medidor em `.destaque` — declarando `.colunas-4` ou `.colunas-5` quando há mais de três.
- **Do** deixar o algarismo do medidor encolher pela consulta de contêiner (1.9 → 1.6 → 1.4rem) e quebrar no espaço quando ainda não couber; nunca cortá-lo com reticências.
- **Do** usar `.btn.zap` (verde #168a4a) para "Enviar no WhatsApp" e `.btn.api.mini` (azul-noite) para "Pela API", nessa ordem, com "Copiar" e "Pular" como `.mini` e `.fantasma` ao lado.
- **Do** manter cartões em papel com uma borda de 1 px linha e 12 px de canto, e papel 2 só em cabeçalhos de tabela, barras de ferramentas e na coluna da promoção.
- **Do** escrever números em Barlow Condensed 700 com `tabular-nums` nos medidores, valores e saldos, e alinhar colunas numéricas à direita com `.num`; tabelas largas sempre dentro de `.rolagem`, que ganha o degradê e a dica quando transborda.
- **Do** usar o laranja legível (#b8501a) para qualquer texto laranja sobre fundo claro; o laranja puro (#ff914d) fica para traços, pontos e pílulas sobre a noite e para a barra de progresso da navegação (3 px no topo, um indicador, não um acento de cartão).
- **Do** desenhar ícones em SVG de um traço só (`.icone`: 1em, `stroke: currentColor`, 1.8 de espessura, pontas redondas; `.icone.ok` em verde para confirmação) e usar os caracteres tipográficos do sistema (✓, →, ↗, ✕, ✱, —) apenas como texto.
- **Do** servir Barlow e Barlow Condensed de `/static/fontes/` com `font-display: swap`, pré-carregando Barlow 400 e Barlow Condensed 700; a monoespaçada é a do sistema.
- **Do** colocar toda animação atrás de `prefers-reduced-motion: no-preference`, com `--rapido` (`cubic-bezier(.23, 1, .32, 1)`) entre 120 e 300 ms para respostas; o arco se desenha em 550 ms uma vez, ao chegar.
- **Do** manter alvos de 2.4rem nos botões de ação e os dois pontos de quebra (900 px geral; 1200 px só para o topo do CRM), onde a barra lateral vira faixa no topo e o menu fica atrás de "Menu".

### Don't:
- **Don't** pôr sombra em cartões, tabelas, medidores, botões ou campos; sombra só na janela modal e no cartão de login, sempre difusa. Nunca sombras duras deslocadas.
- **Don't** usar emoji ou pictogramas como ícone em botões, células ou títulos; o sistema tem `.icone` em SVG (menu, olho, cadeado, check).
- **Don't** colocar um olho (eyebrow/kicker) em caixa-alta acima de h1 ou h2; o rótulo miúdo é só de campo, tabela, seção do menu, sub-bloco ou etiqueta.
- **Don't** usar o laranja puro (#ff914d) como cor de texto sobre branco ou papel 2, nem espalhar laranja em fundos, bordas ou títulos.
- **Don't** usar Barlow Condensed em texto corrido, botões, rótulos ou títulos de página, nem fontes do sistema como fonte de título.
- **Don't** usar azul ou laranja para indicar ok, alerta ou erro, nem inventar uma quarta cor de estado; os pares verde/âmbar/vermelho com seus fundos são fixos.
- **Don't** carregar fontes externas (Google Fonts), bibliotecas de terceiros ou um build de front-end; CSS e JS puros servidos pelo próprio site.
- **Don't** criar raios fora da escala (4, 5, 6, 7, 8, 10, 12, 16, 99 px), bordas duplas, gradientes de superfície ou cartões com borda colorida de lado.
- **Don't** escrever "FOR → MCO" em texto quando os dois códigos existem; o texto com seta é só o fallback de trecho incompleto.
- **Don't** cortar números com reticências nem deixar uma tabela larga alargar a página; o medidor encolhe e quebra, a tabela rola dentro de `.rolagem`.
- **Don't** trocar os controles da web por componentes próprios: select, details, checkbox e `confirm()` são os nativos, estilizados pelo sistema.

## Tema escuro (18/11/2026)

- Escolha no rodapé do menu: **Claro / Escuro / Automático** (segue o aparelho). Fica guardada no navegador
  (`localStorage["mvo-tema"]`), por pessoa e por aparelho; o padrão é Claro.
- Um script curto no topo do `base.html` põe `data-tema="claro|escuro"` no `<html>` antes de a página aparecer
  (sem piscar) e acompanha a troca do aparelho, outras abas e páginas pré-carregadas.
- Todo o tema está no fim do `estilo.css`, em `:root[data-tema="escuro"]` (tokens) e `[data-tema="escuro"] …`
  (cores fixas que não eram tokens). Texto em azul-noite usa `--forte`; links e textos azuis usam `--azul-texto`.
  `--azul`, `--noite` etc. continuam sendo cores de fundo.
- A barra lateral e o login continuam azul-noite nos dois temas (é a marca). Conversas usam as cores do WhatsApp escuro.
- Gráficos do servidor (`app/graficos.py`) trocam de cor por seletor de atributo do SVG; uma cor nova lá precisa da
  regra correspondente no bloco do tema escuro.
- Cor nova em CSS: use token. Se precisar de cor fixa clara (fundo de destaque, etiqueta), acrescente a versão escura
  no bloco do tema escuro.
