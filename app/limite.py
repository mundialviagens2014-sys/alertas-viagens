"""Limite de tentativas (login errado, link da página do cliente errado), guardado na memória do servidor.

O site roda num processo só (Render), então a contagem em memória vale para todos os pedidos. Ao reiniciar o
servidor a contagem recomeça, o que é aceitável: o objetivo é atrapalhar quem tenta adivinhar senhas ou links
em grande quantidade, não guardar histórico (para isso existe o Registro de acessos)."""
import threading
import time
from collections import deque

_TODOS: list["Limitador"] = []


class Limitador:
    """No máximo `maximo` tentativas por chave (IP, login...) dentro de `janela` segundos."""

    def __init__(self, maximo: int, janela: int):
        self.maximo, self.janela = maximo, janela
        self._tentativas: dict[str, deque] = {}
        self._trava = threading.Lock()
        _TODOS.append(self)

    def _recentes(self, chave: str, agora: float) -> deque | None:
        fila = self._tentativas.get(chave)
        while fila and agora - fila[0] > self.janela:
            fila.popleft()
        return fila

    def excedeu(self, chave: str | None) -> bool:
        with self._trava:
            fila = self._recentes(chave or "?", time.monotonic())
            return bool(fila) and len(fila) >= self.maximo

    def registrar(self, chave: str | None) -> None:
        agora = time.monotonic()
        with self._trava:
            if len(self._tentativas) > 5000:  # não deixa a memória crescer sem fim
                for k in [k for k, f in self._tentativas.items() if not f or agora - f[-1] > self.janela]:
                    del self._tentativas[k]
            fila = self._recentes(chave or "?", agora)
            if fila is None:
                fila = self._tentativas[chave or "?"] = deque()
            fila.append(agora)

    def limpar(self, chave: str | None) -> None:
        with self._trava:
            self._tentativas.pop(chave or "?", None)

    def zerar(self) -> None:
        with self._trava:
            self._tentativas.clear()


def zerar_todos() -> None:
    """Para os testes: começa cada teste sem tentativas contadas."""
    for limitador in _TODOS:
        limitador.zerar()
