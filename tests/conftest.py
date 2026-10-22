import os
import tempfile

# Banco temporário só para os testes (precisa vir antes de importar o app)
_pasta = tempfile.mkdtemp()
# TEST_DATABASE_URL permite rodar os testes num PostgreSQL de testes
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_pasta}/teste.db"
os.environ["API_KEY"] = "chave-teste"
os.environ["COLETA_AUTOMATICA"] = "false"

import pytest  # noqa: E402

from app import models_mvo  # noqa: E402,F401  (tabelas do MVO)
from app.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.servicos import limpar_cache_programas, preparar_banco  # noqa: E402


@pytest.fixture()
def db():
    limpar_cache_programas()
    Base.metadata.drop_all(engine)
    init_db()
    sessao = SessionLocal()
    preparar_banco(sessao)
    yield sessao
    sessao.close()
