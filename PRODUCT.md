# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Atendentes e emissores da Mundial Viagens (agência de viagens em Fortaleza/CE), no computador, o dia inteiro, com a
Fila de envio, os Clientes e o CRM abertos por horas e o WhatsApp ao lado (confirmado). O administrador e o gestor
usam as mesmas telas e mais as Configurações, os Relatórios e o Manager de milhas. Uso pelo celular existe
(a equipe confere a fila e responde clientes fora do balcão), mas é secundário.

## Product Purpose

MVO — Mundial Viagens Organização — é o sistema interno da agência, em um só site com três módulos:
**Alertas** (lê promoções de passagens, hotéis, pacotes e cruzeiros em grupos de WhatsApp e Telegram, no Google
Flights e em imagens lidas por IA; cruza com os trechos que cada cliente quer; a equipe envia pelo WhatsApp com um
clique, pela página de cada cliente ou sozinho pela API da Meta), **Manager** (a operação de milhas: colaboradores que
cedem contas, saldos e custo médio, lançamentos, pedidos de emissão, emissões com passageiros e lucro, disponibilidade
de CPFs, agências, franquias, contratos, compras externas, fornecedores, comissões, programas e preços) e **CRM**
(o funil de cada cliente, movido sozinho pelos eventos do sistema e arrastável à mão). Sucesso é a equipe mandar a
promoção certa para o cliente certo rápido, fechar a emissão sem errar conta ou CPF, e o dono ver vendas, gasto e funil
nos Relatórios.

## Positioning

Um sistema só para toda a operação de uma agência de milhas: a promoção entra sozinha (grupos, Google Flights, IA
lendo imagens), bate com o gosto do cliente, sai pelo WhatsApp e volta como resposta, pedido e emissão — e o mesmo
banco sabe quem pode emitir com a conta de menor custo. Nenhuma ferramenta de mercado junta captação de promoções,
envio pelo WhatsApp, CRM e gestão de milhas com custo médio e limite de CPFs.

## Operating Context

- Rotina: a fila de envio é conferida várias vezes por dia; promoção com cliente interessado vira mensagem no WhatsApp
  (link wa.me ou API); respostas chegam pelo número da API e são respondidas em até 24 h; pedidos e emissões são
  registrados no Manager enquanto o cliente espera no WhatsApp.
- Três números de WhatsApp: 0800 (leitor dos grupos, chip separado), 0400 (atendimento humano no aplicativo) e 0500
  (envio pela API oficial da Meta).
- Telas longas de lista e tabela (fila, clientes, promoções, emissões, lançamentos), formulários de cadastro, um
  quadro kanban (CRM) e relatórios com gráficos.
- Publicado no Render (plano gratuito), banco Supabase; a equipe acessa por mvo.mundialviagens.com.

## Capabilities and Constraints

- Servidor FastAPI + Jinja2; um só arquivo de estilo (`app/static/estilo.css`) e um layout base
  (`app/templates/base.html`) servem todas as telas internas; a página pública do cliente tem o seu
  (`para-voce.css`, fora do escopo deste redesign).
- Sem build de front-end: CSS e JS puros, servidos pelo próprio site; fontes hoje via Google Fonts.
- Deve continuar funcionando sem internet externa além das fontes e sem bibliotecas de terceiros.
- Mantêm-se: estrutura das páginas, textos, rotas, formulários, filtros, atalhos, troca de página animada
  (view transitions), e a acessibilidade atual (foco visível, reduzir movimento respeitado).
- Terminologia fixa: Fila de envio, Por interesse, Promoções, Grupos do WhatsApp, Respostas do WhatsApp, Histórico,
  Funil de vendas, Pedidos de emissão, Emissões, Lançamentos, Disponibilidade, Colaboradores, Programas, Agências,
  Franquias, Contratos, Compras externas, Fornecedores, Relatórios, Equipe e acessos, Configurações.

## Brand Commitments

- Logo oficial em `app/static/logo.png` (fundo azul, "mundial" branco com o m-globo e aviãozinho, "viagens" em
  laranja) — fixo.
- Cores oficiais fixas: azul #1B468F e laranja #FF914D (confirmado pelo usuário). Formas, tipografia, ritmo e
  componentes podem mudar.
- Nome: MVO · Mundial Viagens Organização. Voz: português do Brasil, direto, sem jargão técnico, em frases curtas
  (a mesma voz dos textos atuais das telas).
- Pedido do redesign (binding): organizado, "com a cara da empresa", com mais personalidade do que o visual atual.

## Evidence on Hand

- O sistema completo, rodando, com dados reais da agência em produção; dados de demonstração em
  `/home/claude/demo-ia/servidor_crm.py` para conferir telas.
- Capturas de todas as telas atuais podem ser geradas com Playwright (ver `/home/claude/demo-ia/telas_*.py`).
- Não há material fotográfico da agência nem outros ativos de marca além do logo e das duas cores.

## Product Principles

1. A fila primeiro: o que precisa de ação hoje aparece antes e maior; o resto recua.
2. Uma olhada basta: cada cartão de promoção, cliente ou emissão se lê sem abrir (trecho, valor, data, quem).
3. Sem pegadinha: o que é automático (IA, API, CRM) mostra por que fez o que fez.
4. Dados primeiro, enfeite nunca: densidade de operação, mas com ar para ler o dia todo.
5. A marca aparece onde se olha (logo, azul/laranja nos destaques), não em todo lugar.

## Accessibility & Inclusion

Uso prolongado em tela: contraste de texto conforme WCAG AA, foco visível no teclado, `prefers-reduced-motion`
respeitado (já existe), alvos de toque de 40 px ou mais nos botões principais para o uso ocasional pelo celular.
