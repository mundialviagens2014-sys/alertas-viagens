# Origem dos arquivos estáticos

- `logo.png` — logo oficial da Mundial Viagens, fornecido pela agência (fundo azul #1B468F). Não alterar.
- `logo-claro.png` — derivado de `logo.png` em 04/11/2026: o fundo azul foi removido (pixels do azul da marca
  viraram transparentes, bordas desmisturadas em relação ao azul) e as margens vazias cortadas; as marcas (m-globo
  branco com o aviãozinho e "viagens" em laranja) ficaram intactas. Usado na barra lateral azul-noite e no login.
  Gerado por script Python (Pillow) a partir de `logo.png`; para refazer, repita o processo a partir do original.
- `app/marca.svg` — marca do app MVO (18/11/2026): o "m" desenhado com dois arcos de voo, avião laranja e rastro
  tracejado sobre o degradê azul-noite. Desenho próprio do sistema; não substitui o logo oficial da agência.
- `favicon.png`, `icone-180.png`, `app/icone-*.png` — gerados a partir de `app/marca.svg` (desenhado num canvas do
  navegador e salvo em PNG): os "any" com cantos arredondados, os "maskable" e o do iPhone com fundo inteiro.
- `icone.png` — ícone derivado do logo oficial (imagem de compartilhamento da página do cliente).
- `fontes/` — Barlow e Barlow Condensed (Jeremy Tribby), SIL Open Font License 1.1 (`LICENSE-barlow.txt`),
  subconjunto latino em woff2 obtido do pacote npm `@fontsource/barlow` / `@fontsource/barlow-condensed` 5.3.0.
- `fontes/geist-sans-*` — Geist (Vercel), SIL Open Font License 1.1 (`LICENSE-geist.txt`), pacote npm
  `@fontsource/geist-sans` 5.3.0, subconjunto latino, pesos 400/500/600: o texto do sistema desde 10/11/2026.
- `fontes/plus-jakarta-sans-*` — Plus Jakarta Sans (Tokotype), SIL Open Font License 1.1 (`LICENSE-plus-jakarta-sans.txt`),
  pacote npm `@fontsource/plus-jakarta-sans` 5.3.0, subconjunto latino, pesos 600/700: títulos e números grandes.
- Imagem da promoção ("Imagem com a logo", `cartao.js`) — gerada na hora, no navegador da equipe, a partir de `logo.png`,
  dos dados da promoção e das fontes Barlow servidas pelo site; nada vem de fora. Desenha o mesmo arco de voo com o avião
  da macro `arco` (ver DESIGN.md). Não há arquivo gravado no servidor.
- `app/fontes_imagem/*.ttf` — as mesmas Barlow (OFL) de `app/static/fontes/`, convertidas de woff2 para TTF com fontTools,
  para o servidor desenhar a "Imagem com a logo" (`app/imagem_promocao.py`) que vai no cabeçalho do modelo da Meta quando a
  promoção não tem imagem própria. Licença em `app/fontes_imagem/LICENSE-barlow.txt`.
