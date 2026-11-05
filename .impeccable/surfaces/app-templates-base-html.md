---
version: 1
slug: "app-templates-base-html"
primary_target: "app/templates/base.html"
related_targets: ["app/static/estilo.css","app/templates/_macros.html"]
---

# MVO — sistema interno (todas as telas da equipe)

Escopo: o shell (barra lateral, troca de módulo, menu, topo das páginas) e o sistema de componentes de `app/static/estilo.css` + `app/templates/base.html`, aplicado a todas as telas internas (Alertas, Manager, CRM, Configurações, Relatórios, login). A página pública do cliente (/para-voce) fica fora. Modo: Operate.

Público e tarefa: atendentes e emissores no computador o dia inteiro — fila de envio, clientes, CRM e Manager abertos por horas; administrador nos Relatórios. Tarefas: conferir a fila e enviar; achar o cliente; registrar pedido e emissão; mover o funil.

Restrições: logo, azul #1B468F e laranja #FF914D fixos; navegação lateral, controles web padrão, rotas, textos e filtros intocados; troca de página com view transitions preservada; sem bibliotecas de terceiros; fontes servidas pelo próprio site.

## Direction contract

THESIS: o MVO é um mapa de rotas de companhia aérea em operação — cada trecho é uma linha de voo desenhada, não um texto; o sistema recusa o painel admin cinza-com-um-azul e o cartão-bilhete picotado do visual anterior.

OWN-WORLD: barra lateral azul-noite (#0B1F4B) com arcos de rota tênues ao fundo; fundo de trabalho cinza-azulado frio; cartões brancos de borda única (sem sombra fantasma); azul da marca para ações primárias, seleção e links; laranja só nos arcos de rota, no marcador do item ativo e nos valores em destaque; estados ok/alerta/erro fixos. Uma família, Barlow: Barlow Condensed em caixa-alta para códigos de aeroporto, números grandes e totais; Barlow para tudo o mais, algarismos tabulares nas tabelas. Sem emoji como ícone; ícones em SVG de um traço só.

STORY: quem abre vê a fila como um quadro de partidas: trecho, valor, data, cliente, e o botão de enviar; entende que o sistema já cruzou promoção e cliente; age em um clique e vê o status mudar no lugar.

FIRST VIEWPORT: barra lateral azul-noite à esquerda (logo, pílula Alertas | Manager | CRM, menu com o item ativo marcado por um ponto laranja e fundo claro); à direita o título da página, os números do dia em uma faixa de cinco medidores com algarismos condensados, os filtros em uma linha, e a fila: cada promoção como um cartão com o trecho em códigos condensados ligados por um arco laranja com o avião no ápice, o valor em laranja condensado, os detalhes em duas colunas e, à direita, os clientes com o botão verde de WhatsApp e o azul "Pela API". Ação primária: "Enviar no WhatsApp" em cada cliente.

FORM: mapa de rotas de companhia aérea — candidato 1 da lista ordenada (escolhido pelo usuário sobre o sorteado, candidato 6). Gesto de assinatura: o arco de voo entre códigos em todo trecho (fila, promoções, cliente, interesses, histórico, CRM) e o leque de rotas saindo de FOR nos Relatórios e no CRM; motion: o arco se desenha (stroke) ao entrar e o avião desliza ao passar o mouse, 200 ms, ease-out forte. seed: 4e5530d1ffb1cf78

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
