"""Fixtures e helpers dos testes de integração (banco real, só tabelas teste_*).

Sem PROC_API_TESTES_COM_BANCO=1, os testes deste pacote são pulados pelo hook abaixo e nenhuma conexão é aberta.
Os helpers ficam aqui; o test_integracao.py importa o que precisa de tests.integracao.conftest.
"""
import dataclasses
import json
import os
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import criar_app
from comum import banco
from comum import config as configuracao
from publicador import gravador, leitor, planejador, validador
from publicador.operacoes import Plano

PASTA = Path(__file__).resolve().parent
DIR_DADOS = configuracao.RAIZ / "tests" / "dados"
CHAVE_API = "chave-integracao"
CABECALHOS = {"X-API-Key": CHAVE_API}
BANCO_HABILITADO = os.environ.get("PROC_API_TESTES_COM_BANCO") == "1"


def pytest_collection_modifyitems(items):
    """Sem o banco liberado, pula tudo que estiver nesta pasta (e só nela)."""
    if BANCO_HABILITADO:
        return
    for item in items:
        if PASTA in Path(str(item.path)).resolve().parents:
            item.add_marker(pytest.mark.skip(reason="integração: defina PROC_API_TESTES_COM_BANCO=1"))


@pytest.fixture(scope="session")
def cfg():
    base = configuracao.carregar()
    cfg = dataclasses.replace(
        base,
        prefixo="teste_",
        dir_processamento=configuracao.RAIZ / "tests" / "dados",
        chaves_api=(CHAVE_API,),
        cors_origens=(),
    )
    assert cfg.prefixo == "teste_"
    return cfg


@pytest.fixture
def conexao(cfg):
    conn = banco.conectar(cfg, "escrita")
    conn.apagar_tabelas_de_teste()
    gravador.criar_tabelas(conn)
    try:
        yield conn
    finally:
        conn.apagar_tabelas_de_teste()
        conn.fechar()


class ClienteAPI:
    """TestClient com o cabeçalho X-API-Key já aplicado em todo GET."""

    def __init__(self, cfg):
        self._cliente = TestClient(criar_app(config=cfg))

    def get(self, caminho, params=None):
        return self._cliente.get(caminho, params=params, headers=CABECALHOS)


@pytest.fixture
def api(cfg):
    return ClienteAPI(cfg)


def publicar(conn, dir_proc, data, forcar=False) -> Plano:
    entrada = leitor.ler(dir_proc, data)
    erros = validador.validar(entrada)
    assert erros == [], erros[:5]
    estado = gravador.carregar_estado(conn, entrada)
    plano = planejador.planejar(entrada, estado, forcar)
    gravador.gravar(conn, entrada, plano, datetime.now())
    return plano


def contagens(conn) -> dict[str, int]:
    return gravador.contar_tabelas(conn)


def ler_completa(d: str, pacote: str):
    """Envelope de tests/dados/saida_completa/<d>/<pacote>.json."""
    return json.loads((DIR_DADOS / "saida_completa" / d / f"{pacote}.json").read_text(encoding="utf-8"))


def ler_saida(d: str, nome: str = "execucao"):
    """Envelope de tests/dados/saida/<d>/<nome>.json."""
    return json.loads((DIR_DADOS / "saida" / d / f"{nome}.json").read_text(encoding="utf-8"))


def ordenados(registros: list, chave: list[str]) -> list:
    """Cópia da lista ordenada pela chave natural: compara listas sem depender da ordem de chegada."""
    return sorted(registros, key=lambda r: tuple(r[campo] for campo in chave))
