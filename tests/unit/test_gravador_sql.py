"""SQL do gravador sem banco: cada comando passa na guarda, os params batem com os %s, e o fluxo de gravar
(iniciar, operações, confirmar; erro -> desfazer) é testado com uma Conexao falsa."""
import re
from datetime import date, datetime

import pytest

from comum import guarda_sql, tabelas
from comum.series import Ponto
from publicador import gravador
from publicador.emenda import Ajuste
from publicador.entrada import Entrada
from publicador.operacoes import (
    AjusteRegistrar,
    CatalogoGravar,
    Estado,
    FotoAtualizar,
    FotoInserir,
    Plano,
    PontoAtualizar,
    PontoInserir,
    PontoRemover,
    VersaoAntecipar,
    VersaoCorrigir,
    VersaoFechar,
    VersaoInserir,
    VersaoReabrir,
)

PREFIXOS = ["", "teste_"]
DATA = "2026-10-23"
INICIO = datetime(2026, 10, 24, 8, 0, 0)
HASH = "a" * 64
HASH_NOVO = "b" * 64


def _t(prefixo):
    return lambda base: prefixo + base


def _ponto(data="2026-10-22", fechamento=10.5):
    return Ponto(data=data, valores={"fechamento": fechamento, "volume": None}, status={"volume": "indisponivel"})


def _ajuste(classificacao="uniforme", aplicado=True):
    return Ajuste(campo="fechamento", fator=2.0, de="2026-01-02", ate="2026-03-03", pontos=40,
                  origem="emenda", classificacao=classificacao, aplicado=aplicado,
                  detalhe={"antigos": {"de": "2026-01-02", "ate": "2026-03-03", "pontos": 40}})


def _operacoes_sinteticas():
    """Um exemplo de cada tipo de operação, com os campos que o planejador produz."""
    return [
        FotoInserir("resumo_acao", {"data_referencia": "2026-10-22", "ticker": "PETR4"}, HASH, {"pl": 5}),
        FotoInserir("mercado_dia", {"data_referencia": "2026-10-22"}, HASH, {"ibov": 1}),
        FotoInserir("execucao", {"data_referencia": DATA, "rodada": 1}, HASH, {"status": "ok"}),
        FotoAtualizar("resumo_acao", {"data_referencia": "2026-10-22", "ticker": "PETR4"},
                      HASH_NOVO, {"pl": 6}, HASH, {"pl": 5}),
        FotoAtualizar("execucao", {"data_referencia": DATA, "rodada": 1},
                      HASH_NOVO, {"status": "parcial"}, HASH, {"status": "ok"}),
        PontoInserir("PETR4", _ponto(), HASH),
        PontoAtualizar("PETR4", _ponto(fechamento=11.0), HASH_NOVO, _ponto(), HASH),
        PontoAtualizar("PETR4", _ponto(data="2026-10-21", fechamento=9.0), HASH_NOVO, None, HASH),
        PontoRemover("PETR4", "2026-10-20", _ponto(data="2026-10-20"), HASH),
        CatalogoGravar("PETR4", "acao", "PETROBRAS PN", ["fechamento", "volume"]),
        AjusteRegistrar("PETR4", _ajuste()),
        AjusteRegistrar("PETR4", _ajuste(classificacao="recusado", aplicado=False)),
        VersaoInserir("acoes", {"ticker": "PETR4"}, DATA, None, HASH, {"nome": "PETR4"}),
        VersaoInserir("fundamentos_trimestrais", {"ticker": "PETR4", "data_fim_periodo": "2026-06-30"},
                      DATA, "2026-10-30", HASH, {"lucro": 1}),
        VersaoInserir("proventos", {"ticker": "PETR4", "tipo": "JCP", "data_com": "2026-09-01"},
                      DATA, None, HASH, {"valor": 0.3}),
        VersaoFechar("acoes", 7, {"ticker": "PETR4"}, DATA),
        VersaoCorrigir("acoes", 8, {"ticker": "PETR4"}, HASH_NOVO, {"nome": "PETR4 PN"}, HASH, {"nome": "PETR4"}),
        VersaoReabrir("proventos", 9, {"ticker": "PETR4", "tipo": "JCP", "data_com": "2026-09-01"}),
        VersaoAntecipar("acoes", 10, {"ticker": "VALE3"}, "2026-09-30"),
    ]


def _tamanho_esperado(sql):
    return sql.count("%s")


@pytest.mark.parametrize("prefixo", PREFIXOS)
def test_esquema_tem_14_comandos_todos_aceitos_pela_guarda(prefixo):
    comandos = gravador.comandos_do_esquema(prefixo)
    assert len(comandos) == 14
    assert all(c.startswith("CREATE TABLE IF NOT EXISTS") for c in comandos)
    for comando in comandos:
        guarda_sql.verificar(comando, prefixo)


def test_esquema_sem_placeholder_e_sem_comentario():
    for prefixo in PREFIXOS:
        for comando in gravador.comandos_do_esquema(prefixo):
            assert "{p}" not in comando
            assert not comando.lstrip().startswith("--")


@pytest.mark.parametrize("prefixo", PREFIXOS)
def test_cada_tipo_de_operacao_passa_na_guarda_e_tem_params_do_tamanho_certo(prefixo):
    t = _t(prefixo)
    for op in _operacoes_sinteticas():
        comandos = gravador.comandos_da_operacao(op, DATA, t)
        assert comandos, f"sem SQL para {type(op).__name__}"
        for sql, params in comandos:
            guarda_sql.verificar(sql, prefixo)
            assert isinstance(params, tuple)
            assert _tamanho_esperado(sql) == len(params), (type(op).__name__, sql)


def test_todos_os_tipos_de_operacao_estao_cobertos():
    tipos = {type(op) for op in _operacoes_sinteticas()}
    assert tipos == {FotoInserir, FotoAtualizar, PontoInserir, PontoAtualizar, PontoRemover, CatalogoGravar,
                     AjusteRegistrar, VersaoInserir, VersaoFechar, VersaoCorrigir, VersaoReabrir, VersaoAntecipar}


def test_correcao_de_ponto_removido_usa_zeros_e_chave_ticker_data():
    comandos = gravador.comandos_da_operacao(PontoRemover("PETR4", "2026-10-20", _ponto(data="2026-10-20"), HASH),
                                             DATA, _t(""))
    sql, params = comandos[1]
    assert sql.startswith("INSERT INTO correcoes")
    assert params[0] == "series_pontos"
    assert params[1] == '{"ticker": "PETR4", "data": "2026-10-20"}'
    assert params[2] == DATA
    assert params[5] == "0" * 64


def test_atualizar_ponto_sem_anterior_nao_gera_correcao():
    comandos = gravador.comandos_da_operacao(PontoAtualizar("PETR4", _ponto(), HASH_NOVO, None, HASH),
                                             DATA, _t(""))
    assert len(comandos) == 1
    assert comandos[0][0].startswith("UPDATE series_pontos SET valores = %s, status = %s, hash = %s")


def test_ponto_inserir_grava_json_sem_escapar_acento():
    op = PontoInserir("PETR4", Ponto(data="2026-10-22", valores={"fechamento": 1.0}, status={"volume": "nao_se_aplica"}),
                      HASH)
    (sql, params), = gravador.comandos_da_operacao(op, DATA, _t("teste_"))
    assert sql == ("INSERT INTO teste_series_pontos (ticker, data, valores, status, hash, data_publicacao) "
                   "VALUES (%s, %s, %s, %s, %s, %s)")
    assert params == ("PETR4", "2026-10-22", '{"fechamento": 1.0}', '{"volume": "nao_se_aplica"}', HASH, DATA)


def test_execucao_grava_status_como_coluna():
    op = FotoInserir("execucao", {"data_referencia": DATA, "rodada": 2}, HASH, {"status": "falhou"})
    (sql, params), = gravador.comandos_da_operacao(op, DATA, _t(""))
    assert "(data_referencia, rodada, status, data_publicacao, hash, registro)" in sql
    assert params[2] == "falhou"


def test_upsert_publicacoes_passa_na_guarda_e_status_e_a_primeira_atribuicao():
    for prefixo in PREFIXOS:
        sql, params = gravador.comando_publicacao(
            _t(prefixo),
            data=DATA, status="completa", data_referencia="2026-10-22", pasta_completa="C:/x",
            pasta_saida="C:/y", hashes_arquivos={"saida/2026-10-23/execucao.json": HASH},
            contagens_novas={"resumo_acao": 2}, execucao_status="ok", erros=None,
            metadados={"mercado_dia": {"versao_contrato": "1.0"}}, inicio=INICIO, fim=INICIO)
        guarda_sql.verificar(sql, prefixo)
        assert _tamanho_esperado(sql) == len(params) == 12
        atualizacao = sql.split("ON DUPLICATE KEY UPDATE ", 1)[1]
        primeira = atualizacao.split(",", 1)[0].strip()
        assert primeira.startswith("status = ")
        assert "vezes_publicada = vezes_publicada + 1" in sql


def test_upsert_publicacoes_com_metadados_vazio_vira_null():
    _, params = gravador.comando_publicacao(
        _t(""), data=DATA, status="so_execucao", data_referencia=None, pasta_completa=None, pasta_saida="",
        hashes_arquivos={}, contagens_novas={}, execucao_status=None, erros=None, metadados={},
        inicio=INICIO, fim=INICIO)
    assert params[9] is None


def test_insert_em_lote_ate_1000_por_chamada():
    """2500 pontos novos viram 3 chamadas de executar_muitos (1000, 1000, 500)."""
    plano = Plano(data=DATA, modo="normal", status_publicacao="completa", data_referencia="2026-10-22")
    plano.operacoes = [PontoInserir("PETR4", _ponto(data=f"2026-01-{i:05d}"), HASH) for i in range(2500)]
    conexao = ConexaoFalsa()
    gravador.gravar(conexao, _entrada(), plano, INICIO)
    tamanhos = [n for (tipo, sql, n) in conexao.muitos if "series_pontos" in sql]
    assert tamanhos == [1000, 1000, 500]


# ---------------------------------------------------------------------------------------------------------------
# Conexão falsa e fluxo de gravar / carregar_estado
# ---------------------------------------------------------------------------------------------------------------

class ConexaoFalsa:
    """Imita o que gravador usa de Conexao. Registra as chamadas; devolve linhas pré-programadas por tabela base.

    `linhas`: nome base -> lista de dicts (o que o SELECT devolveria).
    `falhar_em`: se o SQL de um executar contiver este texto, levanta RuntimeError.
    """

    def __init__(self, linhas=None, falhar_em=None):
        self.prefixo = "teste_"
        self.linhas = linhas or {}
        self.falhar_em = falhar_em
        self.chamadas = []
        self.consultas = []
        self.executados = []
        self.muitos = []

    def t(self, base):
        return tabelas.nome(base, self.prefixo)

    def consultar(self, sql, params=None):
        self.chamadas.append("consultar")
        self.consultas.append(sql)
        guarda_sql.verificar(sql, self.prefixo, somente_leitura=True)
        casamento = re.search(r"FROM teste_(\w+)", sql)
        return [dict(linha) for linha in self.linhas.get(casamento.group(1), [])]

    def _checar_falha(self, sql):
        if self.falhar_em and self.falhar_em in sql:
            raise RuntimeError("falha simulada")

    def executar(self, sql, params=None):
        guarda_sql.verificar(sql, self.prefixo)
        self._checar_falha(sql)
        self.chamadas.append("executar")
        self.executados.append((sql, params))
        return 1

    def executar_muitos(self, sql, lista):
        guarda_sql.verificar(sql, self.prefixo)
        self._checar_falha(sql)
        self.chamadas.append("executar_muitos")
        self.muitos.append(("executar_muitos", sql, len(lista)))
        return len(lista)

    def iniciar(self):
        self.chamadas.append("iniciar")

    def confirmar(self):
        self.chamadas.append("confirmar")

    def desfazer(self):
        self.chamadas.append("desfazer")


def _entrada():
    return Entrada(
        data=DATA,
        execucao={"pacote": "execucao", "registros": [{"data_referencia": DATA, "rodada": 1, "status": "ok"}]},
        completa=None,
        hashes_arquivos={"saida/2026-10-23/execucao.json": HASH},
        pasta_saida="C:/saida/2026-10-23",
    )


def _plano_com_operacoes():
    plano = Plano(data=DATA, modo="normal", status_publicacao="completa", data_referencia="2026-10-22",
                  metadados={"mercado_dia": {"versao_contrato": "1.0"}})
    plano.operacoes = [
        FotoInserir("mercado_dia", {"data_referencia": "2026-10-22"}, HASH, {"ibov": 1}),
        FotoInserir("execucao", {"data_referencia": DATA, "rodada": 1}, HASH, {"status": "ok"}),
        CatalogoGravar("PETR4", "acao", "PETROBRAS PN", ["fechamento"]),
        PontoInserir("PETR4", _ponto(), HASH),
    ]
    return plano


def test_gravar_inicia_executa_operacoes_e_confirma():
    conexao = ConexaoFalsa()
    gravador.gravar(conexao, _entrada(), _plano_com_operacoes(), INICIO)
    assert conexao.chamadas[0] == "iniciar"
    assert conexao.chamadas[-1] == "confirmar"
    assert "desfazer" not in conexao.chamadas
    sqls = [sql for sql, _ in conexao.executados] + [sql for _, sql, _ in conexao.muitos]
    assert any(sql.startswith("INSERT INTO teste_mercado_dia") for sql in sqls)
    assert any("series_catalogo" in sql for sql in sqls)
    # o upsert de publicacoes é o último executar (não é INSERT em lote)
    ultimo_sql = conexao.executados[-1][0]
    assert ultimo_sql.startswith("INSERT INTO teste_publicacoes")
    assert "ON DUPLICATE KEY UPDATE status = IF" in ultimo_sql


def test_gravar_com_erro_desfaz_e_relanca_sem_confirmar():
    conexao = ConexaoFalsa(falhar_em="series_pontos")
    with pytest.raises(RuntimeError, match="falha simulada"):
        gravador.gravar(conexao, _entrada(), _plano_com_operacoes(), INICIO)
    assert conexao.chamadas[0] == "iniciar"
    assert "desfazer" in conexao.chamadas
    assert "confirmar" not in conexao.chamadas


def test_gravar_com_erro_no_upsert_final_tambem_desfaz():
    conexao = ConexaoFalsa(falhar_em="INTO teste_publicacoes")
    with pytest.raises(RuntimeError):
        gravador.gravar(conexao, _entrada(), _plano_com_operacoes(), INICIO)
    assert conexao.chamadas[-1] == "desfazer"


def test_registrar_rejeicao_grava_so_publicacoes_como_rejeitada():
    conexao = ConexaoFalsa()
    gravador.registrar_rejeicao(conexao, _entrada(), ["resumo_acao[PETR4].pl: status 'ok' com valor nulo"], INICIO)
    assert conexao.chamadas == ["iniciar", "executar", "confirmar"]
    sql, params = conexao.executados[0]
    assert sql.startswith("INSERT INTO teste_publicacoes")
    assert params[1] == "rejeitada"
    assert params[8] == '["resumo_acao[PETR4].pl: status \'ok\' com valor nulo"]'


def test_carregar_estado_monta_chaves_pontos_e_versoes():
    linhas = {
        "publicacoes": [{"data_publicacao": date(2026, 10, 22), "status": "completa"}],
        "mercado_dia": [{"data_referencia": date(2026, 10, 22), "hash": HASH,
                         "registro": '{"ibov": 1}'}],
        "execucao": [{"data_referencia": date(2026, 10, 23), "rodada": 1,
                      "hash": HASH, "registro": '{"status": "ok"}'}],
        "series_pontos": [{"ticker": "PETR4", "data": date(2026, 10, 22),
                           "valores": '{"fechamento": 10.5, "volume": null}',
                           "status": '{"volume": "indisponivel"}'}],
        "series_catalogo": [{"ticker": "PETR4", "tipo": "acao", "nome": "PETROBRAS PN",
                             "campos": '["fechamento", "volume"]'}],
        "acoes": [{"ticker": "PETR4", "id": 7, "valido_desde": date(2026, 10, 1),
                   "valido_ate": None, "hash": HASH, "registro": '{"nome": "PETR4"}'}],
    }
    entrada = Entrada(
        data=DATA,
        execucao={"pacote": "execucao", "registros": [{"data_referencia": DATA, "rodada": 1, "status": "ok"}]},
        completa={
            "mercado_dia": {"registros": [{"data_referencia": "2026-10-22", "ibov": 1}]},
            "series_diarias": {"registros": [{"ticker": "PETR4"}]},
        },
    )
    conexao = ConexaoFalsa(linhas)
    estado = gravador.carregar_estado(conexao, entrada)

    assert isinstance(estado, Estado)
    assert estado.publicacoes == {"2026-10-22": "completa"}
    assert estado.fotos["mercado_dia"] == {("2026-10-22",): (HASH, {"ibov": 1})}
    assert estado.fotos["execucao"] == {("2026-10-23", 1): (HASH, {"status": "ok"})}
    assert estado.pontos["PETR4"]["2026-10-22"] == Ponto("2026-10-22", {"fechamento": 10.5, "volume": None},
                                                         {"volume": "indisponivel"})
    assert estado.catalogo["PETR4"] == {"tipo": "acao", "nome": "PETROBRAS PN", "campos": ["fechamento", "volume"]}
    versao = estado.versoes["acoes"][("PETR4",)][0]
    assert (versao.id, versao.valido_desde, versao.valido_ate, versao.hash) == (7, "2026-10-01", None, HASH)
    assert versao.registro == {"nome": "PETR4"}
    assert estado.recusas == {}


def test_carregar_estado_consulta_pontos_so_nao_removidos_e_fotos_por_data():
    entrada = Entrada(
        data=DATA,
        execucao={"pacote": "execucao", "registros": [{"data_referencia": DATA, "rodada": 1, "status": "ok"}]},
        completa={"series_diarias": {"registros": [{"ticker": "PETR4"}]}},
    )
    conexao = ConexaoFalsa()
    gravador.carregar_estado(conexao, entrada)
    sql_pontos = next(sql for sql in conexao.consultas if "FROM teste_series_pontos" in sql)
    assert "removido_em IS NULL" in sql_pontos
    assert "ticker IN (%s)" in sql_pontos
    sql_execucao = next(sql for sql in conexao.consultas if "FROM teste_execucao" in sql)
    assert "WHERE data_referencia IN (%s)" in sql_execucao
    assert "FROM teste_publicacoes" in conexao.consultas[0]
