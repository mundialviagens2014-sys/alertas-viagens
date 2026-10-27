"""Limite de tentativas (login errado, link da página do cliente errado), guardado na memória do servidor.

O site roda num processo só (Render), então a contagem em memória vale para todos os pedidos. Ao reiniciar o
servidor a contagem recomeça, o que é aceitável: o objetivo é atrapalhar quem tenta adivinhar senhas ou links
em grande quantidade, não guardar histórico (para isso existe o Registro de acessos).

A memória é limitada: no máximo `max_chaves` chaves e chaves de até 100 caracteres, mesmo que quem ataca invente
uma chave diferente a cada pedido. Quando enche, sai primeiro uma chave antiga que não está no limite: inventar
chaves novas não apaga a contagem de quem já está bloqueado."""
import threading
import time
from collections import OrderedDict, deque

_TODOS: list["Limitador"] = []


class Limitador:
    """No máximo `maximo` tentativas por chave (IP, login...) dentro de `janela` segundos."""

    def __init__(self, maximo: int, janela: int, max_chaves: int = 10_000):
        self.maximo, self.janela, self.max_chaves = maximo, janela, max_chaves
        self._tentativas: OrderedDict[str, deque] = OrderedDict()
        self._trava = threading.Lock()
        _TODOS.append(self)

    @staticmethod
    def _chave(chave: str | None) -> str:
        return (chave or "?")[:100]

    def _recentes(self, chave: str, agora: float) -> deque | None:
        fila = self._tentativas.get(chave)
        while fila and agora - fila[0] > self.janela:
            fila.popleft()
        return fila

    def excedeu(self, chave: str | None) -> bool:
        """Já chegou ao limite? (só consulta; não conta tentativa)"""
        with self._trava:
            fila = self._recentes(self._chave(chave), time.monotonic())
            return bool(fila) and len(fila) >= self.maximo

    def tentar(self, chave: str | None) -> bool:
        """Conta uma tentativa, se ainda couber no limite, e diz se ela pode seguir. Consultar e contar de uma vez
        só (com a trava) impede que muitos pedidos simultâneos passem todos antes de algum ser contado."""
        chave, agora = self._chave(chave), time.monotonic()
        with self._trava:
            fila = self._recentes(chave, agora)
            if fila is not None and len(fila) >= self.maximo:
                self._tentativas.move_to_end(chave)  # bloqueada continua entre as mais recentes
                return False
            if fila is None:
                fila = self._tentativas[chave] = deque()
            fila.append(agora)
            self._tentativas.move_to_end(chave)
            while len(self._tentativas) > self.max_chaves:  # memória limitada
                self._liberar_espaco(agora)
            return True

    def _liberar_espaco(self, agora: float) -> None:
        """Tira uma chave: de preferência uma antiga sem tentativa recente ou abaixo do limite (olha só as 50 mais
        antigas, para não percorrer tudo a cada pedido); se todas estiverem no limite, a mais antiga."""
        for i, chave in enumerate(self._tentativas):
            if i >= 50:
                break
            fila = self._recentes(chave, agora)
            if not fila or len(fila) < self.maximo:
                del self._tentativas[chave]
                return
        self._tentativas.popitem(last=False)

    def limpar(self, chave: str | None) -> None:
        with self._trava:
            self._tentativas.pop(self._chave(chave), None)

    def zerar(self) -> None:
        with self._trava:
            self._tentativas.clear()

    def __len__(self) -> int:
        return len(self._tentativas)


def zerar_todos() -> None:
    """Para os testes: começa cada teste sem tentativas contadas."""
    for limitador in _TODOS:
        limitador.zerar()
