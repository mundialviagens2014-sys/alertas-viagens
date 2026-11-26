# Robô de reservas do MVO

Programa que roda num computador da agência e atende os pedidos da tela **Bilhetes** do MVO: abre o site da
LATAM, GOL ou Azul no Chrome deste computador, consulta a reserva com localizador + sobrenome, lê voos e
passageiros e devolve para o MVO gerar o bilhete em PDF.

Roda aqui (e não no servidor do MVO) porque os sites das companhias bloqueiam acessos vindos de servidores na
nuvem e porque o Chrome não cabe no plano gratuito do Render.

## Instalar (uma vez)

1. Instale o **Python 3** em python.org (no instalador, marque **Add python.exe to PATH**).
2. Copie esta pasta `robo` para o computador (ex.: `C:\robo-mvo`).
3. Dê dois cliques em **instalar.bat**.
4. No MVO, vá em **Configurações → Robô de reservas → Gerar token do robô** e copie o token.
5. Dê dois cliques em **configurar.bat**: cole o endereço do MVO e o token.
6. Dê dois cliques em **iniciar.bat** e deixe a janela aberta. Em Bilhetes aparece **robô ligado**.

Para ligar sozinho com o Windows: atalho do `iniciar.bat` em `shell:startup` (Win+R → `shell:startup`).

## O que ele faz e o que não faz

- Só consulta reservas pedidas no MVO, uma de cada vez, com pausa entre elas.
- Se o site pedir captcha, "confirme que você não é um robô" ou bloquear o acesso, ele **não tenta passar**:
  marca o pedido como "Precisa de uma pessoa" e segue.
- Não guarda senha de ninguém. O token do MVO fica em `robo.json`, só neste computador.

## Quando uma companhia mudar o site

Rode **aprender.bat**, informe companhia, localizador e sobrenome, consulte a reserva na janela que abrir e
aperte Enter. O robô guarda em `capturas/` o que o site recebeu, para ajustar a leitura em `companhias.py` /
`leitura.py`. O arquivo tem os dados da reserva: trate como documento do cliente e apague depois.
