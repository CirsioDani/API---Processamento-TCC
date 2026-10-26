"""Fábrica da aplicação FastAPI: CORS, tratamento de erro do banco e rotas."""
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.repositorio import Repositorio
from api.rotas import criar_roteador
from comum import banco
from comum import config as configuracao
from comum.config import Config

DESCRICAO = """
API **somente de leitura** dos dados do TCC. Não existe rota que grave, altere ou apague nada.

**Chave de acesso:** todas as rotas, exceto `/v1/saude`, exigem a chave no cabeçalho `X-API-Key`.
Clique em **Authorize** no topo desta página e informe a chave.

**Formato das respostas:** cada resposta usa o mesmo envelope dos pacotes do contrato 1.0 (`pacote`,
`versao_contrato`, `chave`, `gerado_em`, `dados_ate`, `registros`, `removidos`), com dois campos a mais:

- `data_publicacao`: a manhã (pasta) da rotina cujos dados aparecem. É o valor usado no parâmetro `data`.
- `data_referencia`: o pregão a que os dados se referem. Exemplo: a publicação de 23/10 traz o pregão de 22/10.

**Ausência:** quando um indicador não existe ou não se aplica, o valor vem como `null`, junto com o `status`
correspondente. Nunca vem como 0.
"""


@contextmanager
def _repositorio_padrao(cfg: Config) -> Iterator[Repositorio]:
    conexao = banco.conectar(cfg, "leitura")
    try:
        yield Repositorio(conexao)
    finally:
        conexao.fechar()


def criar_app(config: Config | None = None, fabrica_repositorio: Callable[[Config], Any] | None = None) -> FastAPI:
    cfg = config if config is not None else configuracao.carregar()
    fabrica = fabrica_repositorio or _repositorio_padrao

    app = FastAPI(
        title="API de dados do TCC",
        version="1.0",
        description=DESCRICAO,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.cors_origens),
        allow_methods=["GET"],
        allow_headers=["X-API-Key"],
    )

    @app.exception_handler(banco.ErroBanco)
    async def _banco_indisponivel(request: Request, erro: banco.ErroBanco) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "banco indisponível"})

    app.include_router(criar_roteador(cfg, fabrica))
    return app
