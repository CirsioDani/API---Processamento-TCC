# Imagem da API (só GET) para o Railway. Leva só api/ e comum/: nada do publicador, testes, ferramentas ou .env.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements-api.txt .
RUN pip install -r requirements-api.txt

COPY api/ api/
COPY comum/ comum/

RUN useradd --create-home --uid 10001 api
USER api

EXPOSE 8000

# O Railway define PORT; fora dele vale 8000. As variáveis do banco e das chaves vêm do painel, nunca da imagem.
CMD ["sh", "-c", "exec uvicorn api.servidor:app --host 0.0.0.0 --port ${PORT:-8000}"]
