# Sistema de Alertas de Promoções

Lê promoções de passagens (Telegram, Google Flights e mensagens coladas pela equipe),
organiza tudo num banco de dados, cruza com os trechos que cada cliente quer e mostra
num site interno. A equipe revisa e envia pelo WhatsApp com um clique.

```
Fontes ──► Leitura (parser) ──► Banco de dados ──► Matching ──► Site da equipe ──► WhatsApp (manual)
Telegram       trecho, preço,     clientes,          interesse     fila de envio     link wa.me com a
Google Flights datas, companhia   interesses,        x promoção    por interesse     mensagem pronta
Colar texto                       promoções                        histórico
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
| **Fila de envio** | Cada promoção encontrada com os clientes compatíveis. "Enviar no WhatsApp" abre a conversa com a mensagem pronta e registra o envio. "Copiar" e "Pular" também. |
| **Por interesse** | Cada trecho procurado (ex.: FOR → LIS), quantos clientes querem e as promoções vigentes. |
| **Clientes** | Cadastro, busca, trechos de interesse (origem, destino opcional, período, preço máximo), consentimento LGPD e histórico. |
| **Promoções** | Tudo que foi lido, com filtro por fonte, descarte e reativação. **Colar promoção**: cole o texto de um grupo de WhatsApp; o sistema lê e você confere antes de salvar. |
| **Grupos do Telegram** | Grupos lidos pelo sistema: "Ver meus grupos do Telegram" lista os grupos da conta para adicionar com um clique; também dá para pausar, remover e "Ler agora". |
| **Valor das milhas** | R$ por milha de cada programa (Smiles, Azul Fidelidade, LATAM Pass…) e acréscimo fixo opcional. Promoções em milhas aparecem e são enviadas em reais. |
| **Imagem com a logo** | Em cada promoção da fila: gera a arte com a logo da agência para copiar e colar na conversa do WhatsApp. |
| **Histórico** | O que foi enviado, para quem e por quem. |
| **Configurações** | Modelo da mensagem (com prévia), acessos da equipe e troca de senha. |

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
Não há forma oficial de ler grupos de WhatsApp automaticamente, e ferramentas não oficiais podem bloquear o número.
O caminho seguro: a equipe copia a mensagem do grupo e usa **Colar promoção**. Se muitos grupos tiverem versão no
Telegram, prefira segui-los por lá.

### Outros sistemas (API)
```bash
curl -X POST http://localhost:8000/api/promocoes \
  -H "X-API-Key: SUA_CHAVE" -H "Content-Type: application/json" \
  -d '{"texto": "FOR → LIS R$ 2.389 em março/27", "fonte_detalhe": "Robô X"}'
```

## Coleta automática
Com `COLETA_AUTOMATICA=true`, o próprio site lê o Telegram a cada `INTERVALO_TELEGRAM_MIN` minutos e o
Google Flights a cada `INTERVALO_GOOGLE_HORAS` horas. Rode com **um único processo** (`uvicorn` sem `--workers`)
para não duplicar as coletas. Alternativa: deixar desligado e agendar `python -m app.cli coletar ...` no cron.
A fila também tem botões "Ler Telegram agora" e "Buscar no Google Flights".

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
- Hospedagem simples: VPS, Railway ou Render com `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- O arquivo de sessão do Telegram (`telegram_agencia.session`) dá acesso à conta: guarde com cuidado.
- O cadastro de aeroportos/cidades fica em `app/aeroportos.py`.

## Estrutura

```
app/
  main.py            site (rotas e telas)
  parser.py          leitura de texto livre de promoções
  servicos.py        registro, matching, mensagens
  aeroportos.py      aeroportos, apelidos de cidades e agrupamento
  models.py          tabelas do banco
  coleta.py          execução e agendamento das coletas
  collectors/        telegram.py, google_flights.py
  cli.py             comandos de terminal
  templates/, static/
tests/               testes automatizados (pytest)
```

## Próximos passos sugeridos
- Leitura com IA (ex.: Claude API) para mensagens que o parser não entende.
- Formulário público para o próprio cliente cadastrar os trechos de interesse.
- Histórico de preços por trecho e relatórios de conversão (quem respondeu / comprou).
