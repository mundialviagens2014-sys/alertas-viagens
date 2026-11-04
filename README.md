# MVO — Mundial Viagens Organização

Um só site com três sistemas, trocados pela chave **Alertas | Manager | CRM** no topo do menu:

- **Alertas**: lê promoções (grupos do WhatsApp e do Telegram, Google Flights, mensagens e imagens coladas
  pela equipe; imagens, hotéis, pacotes e cruzeiros passam pela IA), cruza com os trechos que cada cliente quer
  e envia pelo WhatsApp: com um clique, pelo link da página de cada cliente ou sozinho, pela API oficial da Meta.
- **Manager** (substitui o Manager de milhas): colaboradores que cedem as contas, contas nos programas com
  saldo e custo médio do milheiro, lançamentos (compras, bônus, transferências), pedidos de emissão, emissões
  com passageiros e lucro, disponibilidade (quem tem pontos e CPF livre), clientes, agências, franquias,
  contratos, compras externas, fornecedores, comissões por emissor e a configuração dos programas
  (tipos de programa, lista de preços B2B/B2C/B2F, markups, grupos, classificações e consolidadores).

- **CRM**: o funil de vendas, um cartão por cliente, que anda sozinho conforme o que o sistema registra
  (promoção enviada, resposta do cliente lida pela IA, pedido de emissão, emissão) e que a equipe também arrasta à mão.
- **Relatórios** (só o administrador, botão em Configurações): vendas por semana e por mês, gasto com o WhatsApp,
  clientes, promoções, milhas, CPFs e funil, com gráficos, e a planilha com os dados brutos de todas as tabelas.

Os sistemas se conversam: na fila de alertas, cada promoção em milhas mostra **quem pode emitir** (a conta de
menor custo primeiro) e a margem; “Registrar emissão” leva a promoção e o cliente para o formulário de emissão.

```
Fontes ──────────► Leitura ──────────► Banco de dados ──► Matching ──► Site da equipe ──► WhatsApp
Grupos do WhatsApp  regras (texto de     clientes,          interesse     fila de envio     manual (link wa.me),
Grupos do Telegram  passagem) ou IA      interesses,        x promoção    por interesse     página do cliente ou
Google Flights      (Gemini: imagens,    promoções,         clientes      histórico         automático (API da
Formulário / colar  hotel, pacote...)    imagens                          respostas         Meta, modelo aprovado)
```

## Como rodar

Requer Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # e ajuste o que precisar

python -m app.cli dados-exemplo    # opcional: clientes e promoções de teste
uvicorn app.main:app --reload
```

Abra http://localhost:8000 e entre com `admin` / `admin123` (troque em Configurações).

Testes: `pytest`

## O que o site faz

| Tela | Para quê |
|---|---|
| **Fila de envio** | Cada promoção encontrada com os clientes compatíveis. "Enviar no WhatsApp" abre a conversa com a mensagem pronta e registra o envio; "Pela API" manda pelo número da agência na API da Meta (quando configurada). "Copiar" e "Pular" também. Abas "Por cliente" (uma mensagem com o link da página do cliente) e "Confirmações de cadastro". |
| **Por interesse** | Cada trecho procurado (ex.: FOR → LIS), quantos clientes querem e as promoções vigentes. |
| **Clientes** | Cadastro, busca, trechos de interesse (origem, destino opcional, período, preço máximo), consentimento LGPD e histórico. |
| **Promoções** | Tudo que foi lido, com filtro por fonte, descarte e reativação. **Colar promoção**: cole o texto de um grupo de WhatsApp; o sistema lê e você confere antes de salvar. |
| **Grupos do Telegram** | Grupos lidos pelo sistema: "Ver meus grupos do Telegram" lista os grupos da conta para adicionar com um clique; também dá para pausar, remover e "Ler agora". |
| **Grupos do WhatsApp** | O número leitor (um chip só para isso) conecta por QR code ou código; escolha quais grupos são lidos. Cada mensagem ou imagem que chega vira promoção (imagens pela IA). |
| **Respostas do WhatsApp** | O que os clientes responderam às mensagens da API da Meta; dentro de 24 h a equipe responde daqui. Quem manda SAIR perde a autorização sozinho. |
| **Lista de preços** | Preço do milheiro de cada programa por tipo de negócio: B2C (cliente final; o B2C geral converte as promoções em milhas dos alertas em reais), B2B (agências) e B2F (franquias), geral ou especial por agência. |
| **Imagem com a logo** | Em cada promoção da fila: gera a arte com a logo da agência para copiar e colar na conversa do WhatsApp. |
| **Histórico** | O que foi enviado, para quem e por quem. |
| **Configurações** | Modelos das mensagens (com prévia), página do cliente, WhatsApp automático (API da Meta) e troca de senha. |

### CRM

| Tela | Para quê |
|---|---|
| **Funil de vendas** | Colunas: Cadastrado · Promoção enviada · Respondeu · Em conversa · Em dúvida · Pedido de emissão · Venda concluída · Perdido. A coluna é decidida pelo evento mais recente de cada cliente; as respostas recebidas pela API da Meta ganham uma intenção (interessado, dúvida, não quer, sair) pelas regras e pela IA. Arrastar um cartão grava uma etapa manual, que vale até o próximo evento. Cada coluna mostra a quantidade e a soma dos valores. |

### Relatórios (administrador)

Configurações → **Relatórios**: período (mês, mês passado, 30/90 dias, ano, 12 meses, tudo ou datas), vendas (emissões:
quantidade, valor, custo, lucro, ticket, por programa/emissor/companhia/agência, por semana e por mês), WhatsApp (mensagens
entregues × preço por mensagem definido em Configurações → WhatsApp automático, separado para promoção e confirmação;
respostas e intenções), funil, clientes, promoções por fonte e tipo, milhas (estoque, compras, custo médio) e CPFs por
programa. **Dados brutos**: Excel com uma aba por tabela (sem senhas, logins/tokens cifrados, token da Meta, código da
página do cliente, sessão do WhatsApp e imagens); o download fica no registro de acessos.

### Manager (milhas)

| Tela | Para quê |
|---|---|
| **Início** | Pedidos aguardando, avisos (contratos vencendo, compras a receber), pontos por programa e o mês. |
| **Pedidos de emissão** | O que agências e clientes pediram, com o tempo de espera. “Emitir” abre a emissão já preenchida (trecho, passageiros, agência, preço combinado); ao registrar, o pedido fica como emitido. |
| **Emissões** | Registro com conta usada, passageiros (CPF), agência compradora, venda (com o preço sugerido pela lista de preços + markup), taxas, custo e lucro. Desconta os pontos e ocupa os CPFs; cancelar devolve. Filtro por mês e por emissor. |
| **Lançamentos** | Compras, clubes, bônus, resgates, expirações, ajustes e transferências (com bônus). Data futura = pontos a creditar. |
| **Disponibilidade** | Contas com pontos e CPFs livres, da de menor custo para a de maior; filtro por programa, pontos e passageiros; margem por milheiro; preços B2B/B2C/B2F. |
| **Comissões** | Por emissor e por mês: emissões, venda, lucro e a comissão (% do lucro ou da venda); planilha para baixar. |
| **Clientes** | Quem compra as passagens, com as emissões de cada um. |
| **Colaboradores** | Quem cede as contas (documentos, endereço, classificação, consolidador), as contas e os contratos de cada um. |
| **Programas** | Todas as contas: saldo disponível e pessoal, valor pelo custo médio, emissões e o login (com os dados do titular, fica registrado). |
| **Agências / Franquias** | Agências parceiras (tipo de negócio B2B/B2F, franquia, contatos, endereço), com emissões, pedidos e preços especiais. |
| **Contratos** | Contratos com os colaboradores: tipo, vigência, valor de resgate e aviso de vencimento. |
| **Compras externas** | Pontos comprados de fornecedores: pagamento, vencimento e, ao marcar “pontos recebidos”, a compra entra na conta com o custo. |
| **Fornecedores** | Quem vende pontos ou emissões, com o histórico de compras. |
| **Configuração** | Tipos de programa (login, controle de saldo, limite de CPFs por ano ou 12 meses, cancelamento devolve CPF, exige descrição…), lista de preços, markups de programa e de companhia, grupos de produtos, classificações (com cor) e consolidadores. |
| **Importar do Manager** | Planilhas do Manager: pessoas (fidelizados e clientes, com documentos e endereço), contas com saldo/custo/login/token, fornecedores e agências. Confere antes de gravar. |
| **Equipe e acessos** | Usuários com nível de acesso (Administrador, Gestor, Emissor, Atendente) e o registro de acessos. |

**Custo médio do milheiro**: média ponderada das entradas com custo (compras, clubes, transferências, saldo
inicial). Bônus entram sem custo e baixam a média. Saídas levam o custo médio do momento. Tudo é recalculado a
partir dos lançamentos em ordem de data, então um lançamento esquecido pode ser incluído depois com a data certa.

**Níveis de acesso**: Administrador (tudo), Gestor (tudo menos usuários), Emissor (alertas, disponibilidade,
emissões e logins das contas; não vê custos nem lucro), Atendente (só alertas). Ver login e senha de uma conta
fica registrado.

**Exportar planilha**: as listas dos dois sistemas têm o botão “Exportar planilha” (Excel), com os mesmos filtros
da tela e todas as linhas (não só as que aparecem). Só Administrador e Gestor exportam, e fica no registro de
acessos. Custos só saem para quem vê custos; logins e senhas das contas nunca vão para a planilha.

**Excluir**: colaborador, conta, cliente, agência, fornecedor, pedido, contrato, compra externa, acesso da equipe e
os itens da configuração podem ser excluídos (com confirmação, registrado). O que tem histórico que precisa ficar
não é apagado e a tela explica o porquê: conta com emissão ativa ou transferência com outra conta (cancele antes ou
desative), fornecedor com compras, agência com emissões. Emissão só é excluída depois de cancelada.

### Regras importantes
- **Matching**: mesma cidade de origem e destino (GRU, CGH e VCP contam como São Paulo), preço até o máximo do cliente e período que se cruza com o da promoção. Destino vazio = qualquer destino.
- **Revisão**: promoções lidas com pouca confiança ou com preço mais de 50% abaixo da média do trecho (possível erro tarifário) aparecem marcadas "Revisar antes de enviar".
- **Duplicadas**: a mesma promoção postada em vários canais nos últimos 3 dias é registrada uma vez só.
- **Validade**: promoções saem da fila depois de `PROMO_VALIDADE_DIAS` (padrão 5).
- **Consentimento**: cliente sem autorização registrada não pode receber envio.
- Ao cadastrar um interesse novo, o sistema já procura nas promoções vigentes.

## Fontes

### Telegram
1. Em https://my.telegram.org → *API development tools*, crie um app e copie `api_id` e `api_hash` para o `.env`.
2. Use uma conta da agência que participe dos canais/grupos de promoções.
3. Rode uma vez: `python -m app.cli telegram-login` (pede o telefone e o código). Ele lista os canais que a conta segue.
4. Coloque os canais em `TELEGRAM_CANAIS` (ex.: `@canal1,@canal2,-1001234567890`).
5. Teste: `python -m app.cli coletar telegram`

Na primeira leitura de cada canal, pega as últimas 50 mensagens; depois, só as novas.

### Google Flights
O Google Flights não tem API oficial. O sistema usa a **SerpApi** (plano pago, com cota gratuita para teste).
Coloque a chave em `SERPAPI_KEY`. Para cada trecho de interesse com destino definido, consulta 3 datas
de ida (só ida) dentro do período dos clientes e registra a promoção quando o menor preço cabe no
orçamento de algum cliente ou o Google classifica como preço baixo. `GOOGLE_FLIGHTS_MAX_BUSCAS` limita o consumo
por rodada; os trechos são revezados.

Teste: `python -m app.cli coletar google_flights`

### Grupos de WhatsApp
Não há forma oficial de ler grupos de WhatsApp. O sistema usa a biblioteca `neonize` (funciona como o WhatsApp Web),
por isso **use um chip separado** ("número 1"), só para ler: a Meta pode bloquear esse número. Nunca ligue aqui o
número que fala com os clientes. Em **Grupos do WhatsApp**, conecte pelo QR code (ou código) e marque os grupos a ler.
A sessão fica guardada no banco e religa sozinha depois de cada publicação. `WHATSAPP_LEITURA=false` desliga.

### Leitura por IA (Gemini)
Mensagens de passagem em texto são lidas pelas regras. Imagens e promoções de hotel, pacote e cruzeiro passam pelo
Gemini (Google AI Studio, nível gratuito): `GEMINI_API_KEY` no Render. `GEMINI_MAX_POR_DIA` (150) e
`GEMINI_MAX_POR_RODADA` (40) limitam o uso; o que passar da cota fica esperando e é relido depois. As imagens ficam
guardadas `IMAGENS_DIAS` dias (30) para a fila e a página do cliente.

### Outros sistemas (API)
```bash
curl -X POST http://localhost:8000/api/promocoes \
  -H "X-API-Key: SUA_CHAVE" -H "Content-Type: application/json" \
  -d '{"texto": "FOR → LIS R$ 2.389 em março/27", "fonte_detalhe": "Robô X"}'
```

## Envio automático pelo WhatsApp (API oficial da Meta)
O número da agência que fala com os clientes ("número 2") entra na API da Meta (WhatsApp Business Platform).
Tudo é preenchido em **Configurações → WhatsApp automático**, nunca em variável de ambiente: ID do número,
ID da conta, token permanente (fica cifrado no banco e nunca volta para a tela), token de verificação e segredo
do app para o webhook, nomes dos modelos aprovados (um de Marketing para promoção, com cabeçalho de imagem opcional;
um de Utilidade para a confirmação de cadastro) e a ordem das variáveis de cada modelo.

- Webhook: `https://SEU-SITE/webhooks/whatsapp` (GET para a verificação da Meta; POST com assinatura
  `X-Hub-Signature-256` conferida quando o segredo do app está preenchido). Atualiza o status dos envios
  (entregue/lida/falhou) e guarda as respostas dos clientes.
- Com **envio automático** ligado, a cada `INTERVALO_WHATSAPP_API_MIN` minutos (5) o site manda as promoções novas da
  fila (menos as "para conferir") e as confirmações de cadastro, respeitando o máximo por dia e por cliente.
  Precisa de `COLETA_AUTOMATICA=true` e `SITE_URL` (o link da página do cliente vai na mensagem).
- Cada mensagem de marketing é cobrada pela Meta; os limites evitam surpresa. Quem responde SAIR deixa de receber.

## Coleta automática
Com `COLETA_AUTOMATICA=true`, o próprio site lê o Telegram a cada `INTERVALO_TELEGRAM_MIN` minutos, o
Google Flights a cada `INTERVALO_GOOGLE_HORAS` horas, o formulário a cada `INTERVALO_FORMULARIO_MIN` e faz o envio
automático pelo WhatsApp a cada `INTERVALO_WHATSAPP_API_MIN`. Rode com **um único processo** (`uvicorn` sem
`--workers`) para não duplicar as coletas. Alternativa: deixar desligado e agendar `python -m app.cli coletar ...`
no cron. A fila também tem botões "Ler Telegram agora" e "Buscar no Google Flights".

## Publicação gratuita (Supabase + Render + UptimeRobot)

1. **Banco:** Supabase → Connect → Connection String → Session pooler. Coloque em `DATABASE_URL` no `.env` e rode
   `python -m app.cli copiar-banco "ENDEREÇO"` para levar os dados do computador.
2. **Telegram:** com o `.env` já apontando para o Supabase, rode `python -m app.cli telegram-salvar-sessao`
   (o login passa a ficar no banco, e o servidor usa o mesmo).
3. **Código:** envie esta pasta para um repositório **privado** no GitHub (o `.gitignore` já deixa de fora `.env`,
   banco local e sessão do Telegram).
4. **Render:** New → Blueprint → escolha o repositório. O `render.yaml` já configura tudo; preencha `DATABASE_URL`,
   `TELEGRAM_API_ID` e `TELEGRAM_API_HASH` quando pedir.
5. **UptimeRobot:** monitor HTTP(s) para `https://SEU-SITE.onrender.com/saude` a cada 5 minutos (mantém o site acordado
   e as leituras automáticas rodando).
6. Depois de publicado, **não deixe o sistema ligado no computador ao mesmo tempo**: o mesmo login do Telegram em dois
   lugares pode ser desconectado pelo Telegram.

## Produção
- Use PostgreSQL: instale `psycopg[binary]` e defina `DATABASE_URL`.
- Defina `SECRET_KEY` aleatória, troque a senha do admin e use HTTPS (`COOKIE_SEGURO=true`).
- `CHAVE_SENHAS`: chave da criptografia dos logins e senhas das contas de milhas (no Render é gerada sozinha).
  **Não troque depois**: as senhas guardadas deixariam de ser legíveis.
- Hospedagem simples: VPS, Railway ou Render com `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- O arquivo de sessão do Telegram (`telegram_agencia.session`) dá acesso à conta: guarde com cuidado.
- O cadastro de aeroportos/cidades fica em `app/aeroportos.py`.

## Estrutura

```
app/
  main.py            site: alertas (rotas e telas)
  rotas/             site: Manager (milhas, emissões, pedidos, cadastros, configuração, equipe, importação)
  mvo/               regras das milhas: saldo, custo médio, CPFs, emissões, importação
  web.py             peças comuns do site (telas, permissões, avisos)
  permissoes.py      níveis de acesso
  cripto.py          criptografia dos logins/senhas das contas
  models_mvo.py      tabelas do Manager
  parser.py          leitura de texto livre de promoções
  servicos.py        registro, matching, mensagens
  aeroportos.py      aeroportos, apelidos de cidades e agrupamento
  models.py          tabelas dos alertas
  coleta.py          execução e agendamento das coletas e do envio automático
  collectors/        telegram.py, google_flights.py, whatsapp.py (número leitor)
  ia.py, leitura.py  leitura por IA (Gemini) e fila do que ainda não foi lido
  whatsapp_api.py    envio pela API da Meta, webhook e respostas
  rotas/whatsapp*.py telas Grupos do WhatsApp, WhatsApp automático e Respostas
  crm.py, rotas/crm.py          funil de vendas (etapas automáticas, intenção das respostas)
  relatorios.py, graficos.py    números dos relatórios e gráficos em SVG
  dados_brutos.py, rotas/relatorios.py  relatórios do administrador e planilha com todas as tabelas
  cli.py             comandos de terminal
  templates/, static/
tests/               testes automatizados (pytest)
```

## Próximos passos sugeridos
- Manager: clubes com crédito mensal automático, consignações, ofertas e conciliação com o extrato.
- Importar lançamentos e emissões antigos do Manager (formato a definir com as planilhas exportadas).
- Histórico de preços por trecho e relatórios de conversão (quem respondeu / comprou).
