import pymysql
import pytest

from comum import banco
from comum.banco import Conexao, ErroBanco, ler_json
from comum.guarda_sql import SQLRecusado


class CursorFalso:
    def __init__(self, registro: list):
        self._registro = registro
        self.rowcount = 1
        self.lastrowid = 42

    def __enter__(self):
        return self

    def __exit__(self, *excecao):
        return False

    def execute(self, sql, params=None):
        self._registro.append(("execute", sql, params))

    def executemany(self, sql, lista):
        self._registro.append(("executemany", sql, lista))

    def fetchall(self):
        return []

    def close(self):
        pass


class ConexaoFalsa:
    """No lugar da conexão pymysql: registra tudo o que chegaria ao servidor."""

    def __init__(self, erro_no_execute=None):
        self.execucoes: list = []
        self.operacoes: list[str] = []
        self._erro = erro_no_execute

    def cursor(self):
        if self._erro is not None:
            raise self._erro
        return CursorFalso(self.execucoes)

    def begin(self):
        self.operacoes.append("begin")

    def commit(self):
        self.operacoes.append("commit")

    def rollback(self):
        self.operacoes.append("rollback")

    def close(self):
        self.operacoes.append("close")


@pytest.fixture
def espiao():
    vistos: list[str] = []
    banco.OBSERVADORES.append(vistos.append)
    yield vistos
    banco.OBSERVADORES.clear()


def test_sql_recusado_nunca_chega_ao_cursor(espiao):
    bruta = ConexaoFalsa()
    conexao = Conexao(bruta, "", False)
    with pytest.raises(SQLRecusado):
        conexao.consultar("SELECT * FROM tcc_scraper.x")
    assert bruta.execucoes == []
    assert espiao == []


def test_sql_aceito_chega_ao_cursor_e_avisa_observadores(espiao):
    bruta = ConexaoFalsa()
    conexao = Conexao(bruta, "", False)
    sql = "SELECT ticker FROM acoes WHERE id = %s"
    assert conexao.consultar(sql, (1,)) == []
    assert bruta.execucoes == [("execute", sql, (1,))]
    assert espiao == [sql]


def test_consultar_sem_params_nao_passa_params():
    bruta = ConexaoFalsa()
    Conexao(bruta, "", False).consultar("SELECT DATABASE() AS banco")
    assert bruta.execucoes == [("execute", "SELECT DATABASE() AS banco", None)]


def test_inserir_devolve_lastrowid():
    bruta = ConexaoFalsa()
    conexao = Conexao(bruta, "", False)
    assert conexao.inserir("INSERT INTO acoes (ticker) VALUES (%s)", ("PETR4",)) == 42


def test_executar_muitos_chega_ao_cursor():
    bruta = ConexaoFalsa()
    conexao = Conexao(bruta, "", False)
    sql = "UPDATE acoes SET valido_ate = %s WHERE id = %s"
    assert conexao.executar_muitos(sql, [("2026-01-01", 1)]) == 1
    assert bruta.execucoes == [("executemany", sql, [("2026-01-01", 1)])]


def test_somente_leitura_recusa_insert():
    bruta = ConexaoFalsa()
    conexao = Conexao(bruta, "", True)
    with pytest.raises(SQLRecusado):
        conexao.executar("INSERT INTO acoes (ticker) VALUES (%s)", ("PETR4",))
    assert bruta.execucoes == []


def test_apagar_tabelas_de_teste_recusado_no_prefixo_real(espiao):
    bruta = ConexaoFalsa()
    conexao = Conexao(bruta, "", False)
    with pytest.raises(SQLRecusado):
        conexao.apagar_tabelas_de_teste()
    assert bruta.execucoes == []
    assert bruta.operacoes == []
    assert espiao == []


def test_apagar_tabelas_de_teste_executa_14_drops_e_confirma(espiao):
    bruta = ConexaoFalsa()
    conexao = Conexao(bruta, "teste_", False)
    conexao.apagar_tabelas_de_teste()
    comandos = [registro[1] for registro in bruta.execucoes]
    assert len(comandos) == 14
    assert all(sql.startswith("DROP TABLE IF EXISTS `teste_") for sql in comandos)
    assert espiao == comandos
    assert bruta.operacoes == ["commit"]


def test_erro_mysql_vira_erro_banco_so_com_codigo():
    erro = pymysql.MySQLError(1146, "Table 'x' doesn't exist")
    bruta = ConexaoFalsa(erro_no_execute=erro)
    conexao = Conexao(bruta, "", False)
    with pytest.raises(ErroBanco) as info:
        conexao.consultar("SELECT ticker FROM acoes")
    assert "1146" in str(info.value)
    assert "doesn't exist" not in str(info.value)


def test_ler_json():
    assert ler_json('{"a":1}') == {"a": 1}
    assert ler_json(None) is None
    assert ler_json({"a": 1}) == {"a": 1}


def test_t_aplica_prefixo():
    assert Conexao(ConexaoFalsa(), "teste_", False).t("acoes") == "teste_acoes"
    assert Conexao(ConexaoFalsa(), "", False).t("acoes") == "acoes"
