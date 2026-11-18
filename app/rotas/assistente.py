"""Pergunte ao MVO: a rota que o microfone do app chama (ver app/assistente.py)."""
from fastapi import APIRouter, Depends, Form
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .. import assistente
from ..database import get_db
from ..models import Usuario
from ..web import exigir_uma

rotas = APIRouter()


@rotas.post("/assistente/perguntar")
def perguntar(pergunta: str = Form(""), db: Session = Depends(get_db),
              usuario: Usuario = Depends(exigir_uma("alertas", "milhas_ver"))):
    resposta = assistente.perguntar(pergunta[:400], db, usuario)
    return JSONResponse(resposta.json(), headers={"Cache-Control": "no-store"})
