"""Ponto de entrada para o uvicorn: python -m uvicorn api.servidor:app --host 127.0.0.1 --port 8000"""
from api.main import criar_app

app = criar_app()
