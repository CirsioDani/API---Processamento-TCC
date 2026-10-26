from pathlib import Path

import pytest

from comum import guarda_sql
from comum.guarda_sql import SQLRecusado, sql_apagar_tabela_de_teste, verificar

RAIZ = Path(__file__).resolve().parents[2]

RECUSADOS = [
    "SELECT * FROM tcc_scraper.x",
    "SELECT * FROM `tcc_scraper`.`x`",
    "SELECT * FROM outro.acoes",
    "SELECT 'tcc_scraper'",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM mysql.user",
    "USE tcc_scraper",
    "DROP DATABASE tcc_processador",
    "DROP TABLE acoes",
    "GRANT ALL ON acoes TO x",
    "DELETE FROM acoes",
    "TRUNCATE acoes",
    "ALTER TABLE acoes ADD x INT",
    "SELECT 1; DROP TABLE acoes",
    "SELECT 1 /*!50000 , 2 */",
    "SELECT ticker FROM acoes a WHERE a.ticker = %s",
    "SELECT * FROM tabela_estranha",
    "SELECT * FROM acoes, publicacoes",
    'SELECT "x"',
    "SELECT 'a\\' , 1",
    "SELECT 1 INTO OUTFILE '/tmp/x'",
    "SELECT @@version",
    "REPLACE INTO acoes VALUES (1)",
    "SET SESSION TRANSACTION READ ONLY",
    "CREATE TABLE acoes (x INT)",
    "CREATE DATABASE x",
    "",
]

ACEITOS = [
    "SELECT registro FROM resumo_acao WHERE ticker = %s ORDER BY data_publicacao DESC LIMIT 1",
    "INSERT INTO series_pontos (ticker, data) VALUES (%s, %s) ON DUPLICATE KEY UPDATE hash = VALUES(hash)",
    "UPDATE acoes SET valido_ate = %s WHERE id = %s",
    "SELECT JSON_EXTRACT(registro, %s) AS v FROM resumo_acao",
    "SELECT ticker FROM tcc_processador.acoes",
    "SELECT DATABASE() AS banco",
    "CREATE INDEX ix_x ON acoes (ticker)",
    "SELECT COUNT(*) AS n FROM publicacoes",
]


def _comandos_do_esquema(prefixo: str) -> list[str]:
    texto = (RAIZ / "sql" / "esquema.sql").read_text(encoding="utf-8")
    linhas = [linha for linha in texto.splitlines() if not linha.startswith("--")]
    partes = "\n".join(linhas).split(";")
    return [parte.strip().replace("{p}", prefixo) for parte in partes if parte.strip()]


@pytest.mark.parametrize("sql", RECUSADOS)
def test_recusa(sql):
    with pytest.raises(SQLRecusado):
        verificar(sql, "")


@pytest.mark.parametrize("sql", ACEITOS)
def test_aceita_com_prefixo_vazio(sql):
    verificar(sql, "")


@pytest.mark.parametrize("prefixo", ["", "teste_"])
def test_esquema_passa_na_guarda(prefixo):
    comandos = _comandos_do_esquema(prefixo)
    assert len(comandos) == 14
    for sql in comandos:
        verificar(sql, prefixo)


def test_modo_teste_recusa_tabela_real_sem_prefixo():
    with pytest.raises(SQLRecusado):
        verificar("SELECT * FROM acoes", "teste_")


def test_modo_teste_recusa_junto_de_tabela_real():
    with pytest.raises(SQLRecusado):
        verificar("SELECT * FROM teste_acoes JOIN acoes", "teste_")


def test_modo_teste_aceita_tabela_teste():
    verificar("SELECT * FROM teste_acoes", "teste_")


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO acoes (ticker, valido_desde, hash, registro) VALUES (%s, %s, %s, %s)",
        "UPDATE acoes SET valido_ate = %s WHERE id = %s",
        "CREATE TABLE IF NOT EXISTS acoes (x INT)",
    ],
)
def test_somente_leitura_recusa_escrita(sql):
    with pytest.raises(SQLRecusado):
        verificar(sql, "", somente_leitura=True)


def test_somente_leitura_aceita_select():
    verificar("SELECT ticker FROM acoes WHERE id = %s", "", somente_leitura=True)


def test_sql_apagar_tabela_de_teste_monta_drop():
    assert sql_apagar_tabela_de_teste("teste_acoes", "teste_") == "DROP TABLE IF EXISTS `teste_acoes`"


@pytest.mark.parametrize(
    "nome, prefixo",
    [("acoes", "teste_"), ("teste_acoes", ""), ("teste_xyz", "teste_")],
)
def test_sql_apagar_tabela_de_teste_recusa(nome, prefixo):
    with pytest.raises(SQLRecusado):
        sql_apagar_tabela_de_teste(nome, prefixo)


def test_prefixo_invalido_levanta_value_error():
    with pytest.raises(ValueError):
        verificar("SELECT 1", "outro_")


def test_banco_permitido_e_tcc_processador():
    assert guarda_sql.BANCO_PERMITIDO == "tcc_processador"
