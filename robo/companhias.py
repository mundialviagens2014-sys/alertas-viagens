"""Como o robô consulta a reserva em cada companhia.

Cada companhia tem PAGINA (o "Minhas viagens") e consultar(pagina, pedido, respostas):
- abre a página, preenche localizador e sobrenome e manda buscar;
- espera o site carregar a reserva (as respostas JSON caem em `respostas`, que o leitura.py interpreta);
- devolve None quando deu certo, ou um resultado pronto quando não deu
  ({"status": "ajuda" | "nao_encontrada" | "erro", "mensagem": ...}).

Os campos são achados por "pistas" (nome, id, placeholder, rótulo), não por posição fixa, para aguentar pequenas
mudanças no site. Quando uma companhia mudar muito, rode o modo aprender e ajuste as pistas daqui.
"""
import re

# sinais de verificação anti-robô: o robô NÃO tenta passar por elas, só avisa que precisa de uma pessoa
SINAIS_VERIFICACAO = ("captcha", "não sou um robô", "nao sou um robo", "verify you are human", "are you a robot",
                      "access denied", "acesso negado", "request unsuccessful", "pardon our interruption",
                      "unusual traffic", "tráfego incomum", "px-captcha", "challenge")
SINAIS_NAO_ENCONTRADA = ("não encontramos", "nao encontramos", "não foi possível encontrar", "reserva não encontrada",
                         "verifique os dados", "dados inválidos", "código de reserva inválido", "não localizamos",
                         "we couldn't find", "booking not found")

PISTAS_LOCALIZADOR = re.compile(r"reserva|localizador|c[oó]digo|booking|pnr|record|order|confirmation", re.I)
PISTAS_SOBRENOME = re.compile(r"sobrenome|last.?name|surname|family", re.I)
PISTAS_BOTAO = re.compile(r"^\s*(buscar|pesquisar|consultar|continuar|encontrar|procurar|acessar|ver reserva|"
                          r"buscar reserva|encontrar viagem|search|find|continue|entrar)\s*$", re.I)


def verificacao_na_tela(pagina) -> bool:
    try:
        texto = pagina.inner_text("body", timeout=3000).lower()
    except Exception:  # noqa: BLE001
        texto = ""
    if any(s in texto for s in SINAIS_VERIFICACAO[:7]):
        return True
    for frame in pagina.frames:
        url = (frame.url or "").lower()
        if any(s in url for s in ("recaptcha", "hcaptcha", "captcha", "challenges.cloudflare", "perimeterx")):
            return True
    return False


def nao_encontrada(pagina) -> bool:
    try:
        texto = pagina.inner_text("body", timeout=3000).lower()
    except Exception:  # noqa: BLE001
        return False
    return any(s in texto for s in SINAIS_NAO_ENCONTRADA)


def _campo(pagina, pistas: re.Pattern):
    """O primeiro campo de texto visível cujo nome/id/placeholder/rótulo combina com as pistas."""
    for campo in pagina.locator("input:visible").all():
        try:
            tipo = (campo.get_attribute("type") or "text").lower()
            if tipo not in ("text", "search", "") or not campo.is_editable():
                continue
            descricao = " ".join(filter(None, [campo.get_attribute(a) for a in
                                               ("name", "id", "placeholder", "aria-label", "data-testid",
                                                "formcontrolname", "autocomplete")]))
            id_ = campo.get_attribute("id")
            if id_:
                rotulo = pagina.locator(f'label[for="{id_}"]')
                if rotulo.count():
                    descricao += " " + rotulo.first.inner_text()
            if pistas.search(descricao):
                return campo
        except Exception:  # noqa: BLE001
            continue
    return None


def _aceitar_cookies(pagina) -> None:
    """Fecha o aviso de cookies escolhendo o mínimo (só os necessários), quando houver essa opção."""
    for texto in ("Rejeitar", "Recusar", "Apenas necessários", "Somente necessários", "Rechazar", "Reject all"):
        botao = pagina.get_by_role("button", name=re.compile(texto, re.I))
        try:
            if botao.count() and botao.first.is_visible():
                botao.first.click(timeout=2000)
                return
        except Exception:  # noqa: BLE001
            pass


class Companhia:
    PAGINA = ""
    ESPERA_MS = 25000

    def consultar(self, pagina, pedido: dict, respostas: list) -> dict | None:
        pagina.goto(self.PAGINA, wait_until="domcontentloaded", timeout=60000)
        pagina.wait_for_timeout(2500)
        _aceitar_cookies(pagina)
        if verificacao_na_tela(pagina):
            return {"status": "ajuda", "mensagem": "O site pediu verificação (captcha) antes da busca."}
        self.abrir_busca(pagina)
        loc, sob = _campo(pagina, PISTAS_LOCALIZADOR), _campo(pagina, PISTAS_SOBRENOME)
        if not loc or not sob:
            return {"status": "erro", "mensagem": "Não achei os campos de localizador e sobrenome na página "
                                                  "(o site pode ter mudado)."}
        loc.click()
        loc.fill("")
        loc.press_sequentially(pedido["localizador"], delay=90)
        sob.click()
        sob.fill("")
        sob.press_sequentially(pedido["sobrenome"], delay=90)
        antes = len(respostas)
        botao = pagina.get_by_role("button", name=PISTAS_BOTAO)
        if botao.count():
            botao.first.click()
        else:
            sob.press("Enter")
        try:
            pagina.wait_for_load_state("networkidle", timeout=self.ESPERA_MS)
        except Exception:  # noqa: BLE001  sites que nunca ficam "parados": segue com o que chegou
            pass
        pagina.wait_for_timeout(3000)
        if verificacao_na_tela(pagina):
            return {"status": "ajuda", "mensagem": "O site pediu verificação (captcha) depois da busca."}
        if len(respostas) == antes and nao_encontrada(pagina):
            return {"status": "nao_encontrada", "mensagem": "A companhia não encontrou a reserva com esse "
                                                            "localizador e sobrenome."}
        self.depois_da_busca(pagina)
        return None

    def abrir_busca(self, pagina) -> None:
        """Algumas páginas escondem o formulário atrás de uma aba/botão; cada companhia ajusta aqui."""

    def depois_da_busca(self, pagina) -> None:
        """Algumas páginas só carregam os detalhes (passageiros, bagagem) ao abrir a viagem."""
        for texto in ("Ver detalhes", "Detalhes da viagem", "Ver viagem", "Gerenciar", "Ver reserva"):
            alvo = pagina.get_by_text(re.compile(texto, re.I))
            try:
                if alvo.count() and alvo.first.is_visible():
                    alvo.first.click(timeout=3000)
                    pagina.wait_for_load_state("networkidle", timeout=15000)
                    return
            except Exception:  # noqa: BLE001
                continue


class Latam(Companhia):
    PAGINA = "https://www.latamairlines.com/br/pt/minhas-viagens"


class Gol(Companhia):
    PAGINA = "https://b2c.voegol.com.br/minhas-viagens/encontrar-viagem"


class Azul(Companhia):
    PAGINA = "https://www.voeazul.com.br/br/pt/home/minhas-viagens"


ADAPTADORES = {"LA": Latam(), "G3": Gol(), "AD": Azul()}
