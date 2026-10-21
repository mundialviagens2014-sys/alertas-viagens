"""Comandos de linha de comando.

  python -m app.cli criar-usuario            cria um login para a equipe
  python -m app.cli telegram-login           autoriza a conta do Telegram (uma vez)
  python -m app.cli coletar telegram         lê os canais agora
  python -m app.cli coletar google_flights   busca preços agora
  python -m app.cli dados-exemplo            preenche o banco com clientes e promoções de teste
"""
import argparse
import getpass
import json

from sqlalchemy import select

from .database import SessionLocal, init_db
from .models import Usuario
from .servicos import gerar_hash_senha, preparar_banco


def criar_usuario() -> None:
    nome = input("Nome: ").strip()
    login = input("Login: ").strip()
    senha = getpass.getpass("Senha: ")
    if len(senha) < 6:
        raise SystemExit("A senha precisa ter pelo menos 6 caracteres.")
    with SessionLocal() as db:
        if db.scalar(select(Usuario).where(Usuario.login == login)):
            raise SystemExit("Esse login já existe.")
        db.add(Usuario(nome=nome, login=login, senha_hash=gerar_hash_senha(senha)))
        db.commit()
    print(f"Usuário {login} criado.")


def main() -> None:
    p = argparse.ArgumentParser(description="Sistema de alertas de promoções")
    sub = p.add_subparsers(dest="comando", required=True)
    sub.add_parser("criar-usuario")
    sub.add_parser("telegram-login")
    sub.add_parser("telegram-salvar-sessao", help="guarda no banco o login do Telegram feito neste computador")
    c = sub.add_parser("coletar")
    c.add_argument("fonte", choices=["telegram", "google_flights"])
    c.add_argument("--reler", action="store_true", help="lê de novo as últimas 50 mensagens de cada canal")
    sub.add_parser("dados-exemplo")
    cb = sub.add_parser("copiar-banco", help="copia os dados do alertas.db para outro banco (ex.: Supabase)")
    cb.add_argument("destino", help='endereço do banco, entre aspas: "postgresql://..."')
    cb.add_argument("--origem", help="banco de origem (padrão: o alertas.db desta pasta)")
    cb.add_argument("--substituir", action="store_true", help="apaga os dados do destino antes de copiar")
    args = p.parse_args()

    if args.comando == "copiar-banco":
        from .transferir import copiar, origem_padrao

        print("Copiando dados...")
        try:
            r = copiar(args.origem or origem_padrao(), args.destino, args.substituir)
        except Exception as erro:
            raise SystemExit(f"Não foi possível copiar: {erro}")
        print(f"Pronto! {r.get('clientes', 0)} clientes, {r.get('promocoes', 0)} promoções e "
              f"{r.get('matches', 0)} envios copiados.")
        print("Agora coloque esse mesmo endereço em DATABASE_URL no .env e reinicie o site.")
        return

    init_db()
    with SessionLocal() as db:
        preparar_banco(db)

    if args.comando == "criar-usuario":
        criar_usuario()
    elif args.comando == "telegram-login":
        from .collectors import telegram

        telegram.login()
    elif args.comando == "telegram-salvar-sessao":
        from .collectors import telegram

        if telegram.migrar_arquivo_para_banco():
            print("Login do Telegram guardado no banco de dados. O servidor já pode ler os grupos.")
        else:
            print("Não encontrei um login do Telegram neste computador. Rode: python -m app.cli telegram-login")
    elif args.comando == "coletar":
        from .coleta import executar

        print(json.dumps(executar(args.fonte, reler=args.reler), ensure_ascii=False, indent=2))
    elif args.comando == "dados-exemplo":
        from .exemplo import carregar

        print(carregar())


if __name__ == "__main__":
    main()
