"""Conexão com o banco. Todo SQL do projeto passa por `Conexao`, que chama a guarda (comum/guarda_sql.py)
antes de executar. Erros do MySQL viram `ErroBanco` com só o código, sem usuário nem senha.
"""
import json
from collections.abc import Callable
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from comum import guarda_sql, tabelas
from comum.config import Config


class ErroBanco(Exception):
    """Falha do banco. A mensagem traz só o código do erro MySQL."""


# Cada SQL executado por Conexao é passado a eles (usado pelos testes de espionagem).
OBSERVADORES: list[Callable[[str], None]] = []


def ler_json(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (str, bytes)):
        return json.loads(v)
    return v


def _codigo(erro: pymysql.MySQLError) -> Any:
    return erro.args[0] if erro.args else "desconhecido"


def _executar_cursor(cursor: Any, sql: str, params: Any) -> None:
    if params is None:
        cursor.execute(sql)
    else:
        cursor.execute(sql, params)


class Conexao:
    def __init__(self, bruta: Any, prefixo: str, somente_leitura: bool):
        self._bruta = bruta
        self.prefixo = prefixo
        self.somente_leitura = somente_leitura

    def t(self, base: str) -> str:
        return tabelas.nome(base, self.prefixo)

    def _verificar_e_avisar(self, sql: str) -> None:
        guarda_sql.verificar(sql, self.prefixo, self.somente_leitura)
        for observador in OBSERVADORES:
            observador(sql)

    def consultar(self, sql: str, params: Any = None) -> list[dict]:
        self._verificar_e_avisar(sql)
        try:
            with self._bruta.cursor() as cursor:
                _executar_cursor(cursor, sql, params)
                return cursor.fetchall()
        except pymysql.MySQLError as erro:
            raise ErroBanco(f"falha ao consultar o banco (código MySQL {_codigo(erro)})") from None

    def executar(self, sql: str, params: Any = None) -> int:
        self._verificar_e_avisar(sql)
        try:
            with self._bruta.cursor() as cursor:
                _executar_cursor(cursor, sql, params)
                return cursor.rowcount
        except pymysql.MySQLError as erro:
            raise ErroBanco(f"falha ao executar no banco (código MySQL {_codigo(erro)})") from None

    def inserir(self, sql: str, params: Any = None) -> int:
        self._verificar_e_avisar(sql)
        try:
            with self._bruta.cursor() as cursor:
                _executar_cursor(cursor, sql, params)
                return cursor.lastrowid
        except pymysql.MySQLError as erro:
            raise ErroBanco(f"falha ao inserir no banco (código MySQL {_codigo(erro)})") from None

    def executar_muitos(self, sql: str, lista_params: Any) -> int:
        self._verificar_e_avisar(sql)
        try:
            with self._bruta.cursor() as cursor:
                cursor.executemany(sql, lista_params)
                return cursor.rowcount
        except pymysql.MySQLError as erro:
            raise ErroBanco(f"falha ao executar em lote no banco (código MySQL {_codigo(erro)})") from None

    def iniciar(self) -> None:
        self._chamar(self._bruta.begin, "iniciar a transação")

    def confirmar(self) -> None:
        self._chamar(self._bruta.commit, "confirmar a transação")

    def desfazer(self) -> None:
        self._chamar(self._bruta.rollback, "desfazer a transação")

    def fechar(self) -> None:
        self._chamar(self._bruta.close, "fechar a conexão")

    def _chamar(self, metodo: Callable[[], None], acao: str) -> None:
        try:
            metodo()
        except pymysql.MySQLError as erro:
            raise ErroBanco(f"falha ao {acao} (código MySQL {_codigo(erro)})") from None

    def apagar_tabelas_de_teste(self) -> None:
        """Único caminho com DROP. Os comandos são montados por guarda_sql.sql_apagar_tabela_de_teste,
        que só aceita o prefixo 'teste_'. Se um deles for recusado, nada é executado."""
        if self.somente_leitura:
            raise guarda_sql.SQLRecusado("conexão de leitura não apaga tabelas")
        comandos = [
            guarda_sql.sql_apagar_tabela_de_teste(self.t(base), self.prefixo)
            for base in tabelas.NOMES_BASE
        ]
        try:
            with self._bruta.cursor() as cursor:
                for sql in comandos:
                    for observador in OBSERVADORES:
                        observador(sql)
                    cursor.execute(sql)
            self._bruta.commit()
        except pymysql.MySQLError as erro:
            raise ErroBanco(f"falha ao apagar as tabelas de teste (código MySQL {_codigo(erro)})") from None

    def __enter__(self) -> "Conexao":
        return self

    def __exit__(self, *excecao: Any) -> None:
        self.fechar()


def _confirmar_banco(conexao: Conexao) -> None:
    banco = conexao.consultar("SELECT DATABASE() AS banco")[0]["banco"]
    if banco != guarda_sql.BANCO_PERMITIDO:
        raise ErroBanco(f"a conexão não está no banco {guarda_sql.BANCO_PERMITIDO!r}")


def conectar(cfg: Config, perfil: str) -> Conexao:
    if perfil == "escrita":
        credencial = cfg.escrita
        somente_leitura = False
        extra: dict[str, Any] = {}
    elif perfil == "leitura":
        credencial = cfg.leitura
        somente_leitura = True
        extra = {"init_command": "SET SESSION TRANSACTION READ ONLY"}
    else:
        raise ValueError(f"perfil de conexão desconhecido: {perfil!r}")

    try:
        bruta = pymysql.connect(
            host=cfg.host,
            port=cfg.porta,
            user=credencial.usuario,
            password=credencial.senha,
            database=cfg.banco,
            charset="utf8mb4",
            autocommit=False,
            cursorclass=DictCursor,
            connect_timeout=10,
            **extra,
        )
    except pymysql.MySQLError as erro:
        raise ErroBanco(f"falha ao conectar ao banco (código MySQL {_codigo(erro)})") from None

    conexao = Conexao(bruta, cfg.prefixo, somente_leitura)
    try:
        _confirmar_banco(conexao)
    except BaseException:
        bruta.close()
        raise
    return conexao
