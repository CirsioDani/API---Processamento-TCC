"""Testes de integração: publicador + API contra o banco real, só nas tabelas teste_*.

Pulados sem PROC_API_TESTES_COM_BANCO=1 (ver conftest.py). Quem roda no banco é a sessão principal.
"""
import dataclasses
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pymysql
import pytest

from api.repositorio import sql_recusas
from comum import banco, guarda_sql, series
from comum.series import Ponto
from ferramentas import conferir_api
from publicador import gravador, liberacao
from publicador.emenda import Ajuste
from publicador.operacoes import AjusteRegistrar, CatalogoGravar, PontoInserir
from tests.integracao.conftest import (
    DIR_DADOS,
    ClienteAPI,
    contagens,
    ler_completa,
    ler_saida,
    ordenados,
    publicar,
)

pytestmark = pytest.mark.integracao

D23, D26, D27 = "2026-10-23", "2026-10-26", "2026-10-27"
TICKERS = ("PETR4", "VALE3", "ITUB4")
PREFIXO = "teste_"
TABELAS_14 = (
    "teste_publicacoes", "teste_correcoes", "teste_resumo_acao", "teste_noticias_dia", "teste_mercado_dia",
    "teste_execucao", "teste_experimento_top", "teste_experimento_historico", "teste_series_catalogo",
    "teste_series_pontos", "teste_series_ajustes", "teste_acoes", "teste_fundamentos_trimestrais",
    "teste_proventos",
)


# ---------------------------------------------------------------------------------------------- helpers locais

def _registro(d: str, pacote: str, ticker: str | None = None) -> dict:
    registros = ler_completa(d, pacote)["registros"]
    if ticker is None:
        assert len(registros) == 1
        return registros[0]
    return next(r for r in registros if r["ticker"] == ticker)


def _do_ticker(d: str, pacote: str, ticker: str) -> list[dict]:
    return [r for r in ler_completa(d, pacote)["registros"] if r["ticker"] == ticker]


def _sem_meta(registros: list[dict]) -> list[dict]:
    """Versionados: uma versão que não mudou mantém o gerado_em/dados_ate do dia em que nasceu (o hash ignora os
    metadados). Por isso a comparação com o arquivo de um dia posterior é pelo conteúdo."""
    return [{k: v for k, v in r.items() if k not in ("versao_contrato", "gerado_em", "dados_ate")} for r in registros]


def _valor(registro: dict, campo: str):
    """Indicador do resumo é {valor, status, ...}; quando é valor puro, devolve o próprio valor."""
    valor = registro[campo]
    return valor["valor"] if isinstance(valor, dict) else valor


def _confere_serie(api: ClienteAPI, ticker: str, arquivo: dict) -> None:
    registro = api.get(f"/v1/series_diarias/{ticker}").json()["registros"][0]
    for campo in ("datas", "valores", "status", "inicio_janela", "ticker", "tipo", "nome"):
        assert registro[campo] == arquivo[campo], (ticker, campo)


def _series_do_arquivo(d: str) -> dict[str, dict]:
    return {r["ticker"]: r for r in ler_completa(d, "series_diarias")["registros"]}


def _pontos(conn) -> list[tuple[str, dict]]:
    """(data, valores) da série TSTE3 em ordem, sem os removidos."""
    linhas = conn.consultar(
        f"SELECT data, valores FROM {conn.t('series_pontos')} WHERE ticker = %s AND removido_em IS NULL "
        "ORDER BY data",
        ("TSTE3",),
    )
    return [(linha["data"].isoformat(), banco.ler_json(linha["valores"])) for linha in linhas]


def _ajustes(conn) -> list[dict]:
    return conn.consultar(
        f"SELECT campo, data_publicacao, fator, de, ate, pontos, origem, classificacao, aplicado "
        f"FROM {conn.t('series_ajustes')} WHERE ticker = %s ORDER BY campo, data_publicacao",
        ("TSTE3",),
    )


_NOME_EM_SQL = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?([`\w.]+)", re.IGNORECASE
)
_UPSERT = re.compile(r"ON\s+DUPLICATE\s+KEY\s+UPDATE", re.IGNORECASE)


def _tabelas_citadas(sql: str) -> list[str]:
    """Nomes que o SQL põe depois de FROM/JOIN/INTO/UPDATE/TABLE. O 'UPDATE' do upsert (ON DUPLICATE KEY
    UPDATE col = ...) não é tabela e sai antes da busca. Subconsultas '(' não casam."""
    limpo = _UPSERT.sub(" ", sql)
    return [m.group(1).strip("`") for m in _NOME_EM_SQL.finditer(limpo)]


def _tabelas_do_banco(cfg) -> list[str]:
    leitura = banco.conectar(cfg, "leitura")
    try:
        with leitura._bruta.cursor() as cursor:
            cursor.execute("SHOW TABLES")
            return [list(linha.values())[0] for linha in cursor.fetchall()]
    finally:
        leitura.fechar()


# ------------------------------------------------------------------------------------------- 1. esquema

def test_criar_tabelas_idempotente(conexao):
    gravador.criar_tabelas(conexao)
    contagem = contagens(conexao)
    assert len(contagem) == 14
    assert all(nome.startswith(PREFIXO) for nome in contagem)
    assert set(contagem.values()) == {0}
    assert set(contagem) == set(TABELAS_14)


# ------------------------------------------------------------------------------------- 2. publicar 23 e API

def test_publicar_23_e_api_devolve_os_arquivos(conexao, api):
    publicar(conexao, DIR_DADOS, D23)
    series23 = _series_do_arquivo(D23)

    esperado = {
        "teste_publicacoes": 1,
        "teste_correcoes": 0,
        "teste_resumo_acao": 3,
        "teste_noticias_dia": 3,
        "teste_mercado_dia": 1,
        "teste_execucao": 1,
        "teste_experimento_top": 0,
        "teste_experimento_historico": 0,
        "teste_series_catalogo": 8,
        "teste_series_pontos": sum(len(r["datas"]) for r in series23.values()),
        "teste_series_ajustes": 0,
        "teste_acoes": 3,
        "teste_fundamentos_trimestrais": 24,
        "teste_proventos": 128,
    }
    assert contagens(conexao) == esperado

    for t in TICKERS:
        for rota in ("resumo_acao", "noticias_dia"):
            corpo = api.get(f"/v1/{rota}/{t}").json()
            assert corpo["registros"][0] == _registro(D23, rota, t), (rota, t)
            assert corpo["data_publicacao"] == D23
            assert corpo["data_referencia"] == "2026-10-22"

    corpo = api.get("/v1/mercado_dia").json()
    assert corpo["registros"] == ler_completa(D23, "mercado_dia")["registros"]
    assert corpo["data_publicacao"] == D23 and corpo["data_referencia"] == "2026-10-22"

    acoes = api.get("/v1/acoes").json()["registros"]
    assert ordenados(acoes, ["ticker"]) == ordenados(ler_completa(D23, "acoes")["registros"], ["ticker"])

    for t in TICKERS:
        fund = api.get(f"/v1/fundamentos_trimestrais/{t}").json()["registros"]
        assert ordenados(fund, ["data_fim_periodo"]) == ordenados(
            _do_ticker(D23, "fundamentos_trimestrais", t), ["data_fim_periodo"])
        prov = api.get(f"/v1/proventos/{t}").json()["registros"]
        assert ordenados(prov, ["data_com", "tipo"]) == ordenados(
            _do_ticker(D23, "proventos", t), ["data_com", "tipo"])

    for ticker in series23:
        _confere_serie(api, ticker, series23[ticker])

    corpo = api.get("/v1/series_diarias/PETR4").json()
    for campo in ("versao_contrato", "chave", "gerado_em", "dados_ate"):
        assert corpo[campo] == ler_completa(D23, "series_diarias")[campo], campo

    ultima = api.get("/v1/execucao/ultima").json()
    assert ultima["registros"] == [ler_saida(D23)["registros"][-1]]
    assert ultima["data_publicacao"] == D23


# --------------------------------------------------------------------------- 3. segundo dia acumula historico

def test_segundo_dia_acumula_historico(conexao, api):
    plano23 = publicar(conexao, DIR_DADOS, D23)
    publicar(conexao, DIR_DADOS, D26)
    assert plano23.modo == "normal"

    series23 = _series_do_arquivo(D23)
    series26 = _series_do_arquivo(D26)
    soma23 = sum(len(r["datas"]) for r in series23.values())

    contagem = contagens(conexao)
    assert contagem["teste_resumo_acao"] == 6
    assert contagem["teste_noticias_dia"] == 6
    assert contagem["teste_mercado_dia"] == 2
    assert contagem["teste_execucao"] == 2
    assert contagem["teste_series_pontos"] == soma23 + 8
    assert contagem["teste_correcoes"] == 1
    assert contagem["teste_fundamentos_trimestrais"] == 25
    assert contagem["teste_proventos"] == 128
    assert contagem["teste_publicacoes"] == 2

    # resumo: sem data = 26; ?data=23 = 23 (com a data_referencia dele)
    atual = api.get("/v1/resumo_acao/PETR4").json()
    assert atual["registros"][0] == _registro(D26, "resumo_acao", "PETR4")
    assert atual["data_publicacao"] == D26
    antigo = api.get("/v1/resumo_acao/PETR4", params={"data": D23}).json()
    assert antigo["registros"][0] == _registro(D23, "resumo_acao", "PETR4")
    assert antigo["data_referencia"] == "2026-10-22"

    historico = api.get("/v1/resumo_acao/PETR4/historico", params={"campo": "pl"}).json()
    assert historico["datas"] == ["2026-10-22", "2026-10-23"]
    assert historico["valores"] == [
        _valor(_registro(D23, "resumo_acao", "PETR4"), "pl"),
        _valor(_registro(D26, "resumo_acao", "PETR4"), "pl"),
    ]

    fund_atual = ordenados(api.get("/v1/fundamentos_trimestrais/VALE3").json()["registros"], ["data_fim_periodo"])
    assert _sem_meta(fund_atual) == _sem_meta(
        ordenados(_do_ticker(D26, "fundamentos_trimestrais", "VALE3"), ["data_fim_periodo"]))
    fund_antigo = ordenados(
        api.get("/v1/fundamentos_trimestrais/VALE3", params={"data": D23}).json()["registros"],
        ["data_fim_periodo"])
    assert fund_antigo == ordenados(_do_ticker(D23, "fundamentos_trimestrais", "VALE3"), ["data_fim_periodo"])

    prov_atual = api.get("/v1/proventos/ITUB4").json()["registros"]
    prov_antigo = api.get("/v1/proventos/ITUB4", params={"data": D23}).json()["registros"]
    assert len(prov_atual) == len(prov_antigo) - 1

    serie_1a = api.get("/v1/series_diarias/PETR4").json()["registros"][0]
    arq26 = series26["PETR4"]
    for campo in ("datas", "valores", "status"):
        assert serie_1a[campo] == arq26[campo], campo

    serie_max = api.get("/v1/series_diarias/PETR4", params={"janela": "max"}).json()["registros"][0]
    assert len(serie_max["datas"]) == len(series23["PETR4"]["datas"]) + 1
    assert serie_max["inicio_ultima_completa"] == arq26["inicio_janela"]


# ------------------------------------------------------------------------------------- 4. republicar não duplica

def test_republicar_nao_duplica(conexao):
    publicar(conexao, DIR_DADOS, D23)
    publicar(conexao, DIR_DADOS, D26)
    c1 = contagens(conexao)

    plano = publicar(conexao, DIR_DADOS, D26)
    assert plano.modo == "republicacao"
    assert plano.contagens() == {}
    assert contagens(conexao) == c1

    plano = publicar(conexao, DIR_DADOS, D23)
    assert plano.modo == "buraco"
    assert plano.contagens() == {}
    assert contagens(conexao) == c1

    linhas = conexao.consultar(
        f"SELECT vezes_publicada FROM {conexao.t('publicacoes')} WHERE data_publicacao = %s", (D26,))
    assert linhas[0]["vezes_publicada"] == 2


# ----------------------------------------------------------------------------- 5. dia que falhou grava só execução

def test_dia_que_falhou_grava_so_execucao(conexao, api):
    publicar(conexao, DIR_DADOS, D23)
    publicar(conexao, DIR_DADOS, D26)
    antes = contagens(conexao)

    plano = publicar(conexao, DIR_DADOS, D27)
    assert plano.modo == "so_execucao"

    depois = contagens(conexao)
    diferencas = {nome: depois[nome] - antes[nome] for nome in antes if depois[nome] != antes[nome]}
    # A publicação de 27 cria a sua própria linha em publicacoes (ela aparece na contagem, como na 23 e na 26).
    assert diferencas == {"teste_execucao": 1, "teste_publicacoes": 1}

    ultima = api.get("/v1/execucao/ultima").json()
    assert ultima["registros"][0]["status"] == "falhou"
    assert ultima["data_referencia"] == D27

    assert api.get("/v1/resumo_acao/PETR4").json()["registros"][0] == _registro(D26, "resumo_acao", "PETR4")


# -------------------------------------------------------------------------------- 6. preencher buraco

def test_preencher_buraco_nao_mexe_nos_dias_seguintes(conexao, api):
    publicar(conexao, DIR_DADOS, D26)

    def respostas():
        return {
            "resumo": api.get("/v1/resumo_acao/PETR4").json(),
            "acoes": api.get("/v1/acoes").json(),
            "fundamentos": api.get("/v1/fundamentos_trimestrais/VALE3").json(),
            "proventos": api.get("/v1/proventos/ITUB4").json(),
            "serie": api.get("/v1/series_diarias/PETR4").json(),
        }

    antes = respostas()
    plano = publicar(conexao, DIR_DADOS, D23)
    assert plano.modo == "buraco"
    assert respostas() == antes

    resumo23 = api.get("/v1/resumo_acao/PETR4", params={"data": D23}).json()["registros"][0]
    assert resumo23 == _registro(D23, "resumo_acao", "PETR4")

    serie_max = api.get("/v1/series_diarias/PETR4", params={"janela": "max"}).json()["registros"][0]
    assert len(serie_max["datas"]) == len(_series_do_arquivo(D26)["PETR4"]["datas"]) + 1


# ----------------------------------------------------------------------------------- 7. transação única

def test_transacao_unica(conexao, monkeypatch):
    def falha(*_args, **_kwargs):
        raise RuntimeError("falha injetada")

    monkeypatch.setattr(gravador, "comando_publicacao", falha)
    with pytest.raises(RuntimeError, match="falha injetada"):
        publicar(conexao, DIR_DADOS, D23)
    assert set(contagens(conexao).values()) == {0}


# ---------------------------------------------------------------------------- 8. conexão da API é read only

def test_conexao_da_api_e_read_only(cfg, conexao):
    # O fixture conexao garante que as tabelas teste_* existem antes do INSERT de teste.
    leitura = banco.conectar(cfg, "leitura")
    try:
        with pytest.raises(guarda_sql.SQLRecusado):
            leitura.executar(
                "INSERT INTO teste_publicacoes (data_publicacao, status, hashes_arquivos, contagens_novas, inicio) "
                "VALUES ('2030-01-01','completa','{}','{}', NOW())"
            )
        # Passando por baixo da guarda de propósito: o próprio MySQL precisa recusar.
        with pytest.raises((pymysql.err.OperationalError, pymysql.err.InternalError)) as erro:
            with leitura._bruta.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO teste_publicacoes (data_publicacao, status, hashes_arquivos, contagens_novas, inicio) "
                    "VALUES ('2030-01-01','completa','{}','{}', NOW())"
                )
        assert erro.value.args[0] == 1792
    finally:
        leitura.fechar()


# -------------------------------------------------------------------------- 9. modo teste nunca toca tabela real

def test_modo_teste_nunca_toca_tabela_real(conexao, cfg):
    antes = {nome for nome in _tabelas_do_banco(cfg) if not nome.startswith(PREFIXO)}

    sqls: list[str] = []

    def espiao(sql: str) -> None:
        sqls.append(sql)

    banco.OBSERVADORES.append(espiao)
    try:
        publicar(conexao, DIR_DADOS, D23)
        publicar(conexao, DIR_DADOS, D26)
        publicar(conexao, DIR_DADOS, D27)
        api = ClienteAPI(cfg)
        api.get("/v1/resumo_acao/PETR4")
        api.get("/v1/resumo_acao/PETR4", params={"data": D23})
        api.get("/v1/series_diarias/PETR4", params={"janela": "max"})
        api.get("/v1/acoes")
        api.get("/v1/resumo_acao/PETR4/historico", params={"campo": "pl"})
    finally:
        banco.OBSERVADORES.remove(espiao)

    assert sqls, "o espião não viu nenhum SQL"
    for sql in sqls:
        for nome in _tabelas_citadas(sql):
            assert nome.startswith(PREFIXO), f"tabela real citada: {nome!r} em {sql[:120]!r}"

    depois = {nome for nome in _tabelas_do_banco(cfg) if not nome.startswith(PREFIXO)}
    assert depois == antes


# ----------------------------------------------------------------- 10. emenda ponta a ponta (dados sintéticos)

GERADO = "2030-01-10T12:00:00-03:00"
DADOS_ATE = "2030-01-10T08:00:00-03:00"
D1, D2, D3 = "2030-01-10", "2030-01-11", "2030-01-12"
CAMPOS_SERIE = ("fechamento", "indice_retorno_total", "volume")


def _dias_uteis(inicio: date, n: int) -> list[date]:
    datas, dia = [], inicio
    while len(datas) < n:
        if dia.weekday() < 5:
            datas.append(dia)
        dia += timedelta(days=1)
    return datas


DATAS = _dias_uteis(date(2029, 11, 1), 45)  # índices 0..44; o dia 1 usa 0..39, o dia 2 e o 3 usam 10..44


def _base(i: int) -> dict:
    """Valores 'verdadeiros' da data de índice i, antes de qualquer evento."""
    return {"fechamento": 10.0 + i, "indice_retorno_total": 100.0 + i, "volume": 1000.0 + 10.0 * i}


def _valores(i: int, dia: int) -> dict:
    """Dia 1: tudo original. Dia 2: desdobramento 1:2 na data de índice 25, então 10..24 vêm com fechamento
    ×0.5 e volume ×2. Dia 3: o mesmo do dia 2, com fechamento ×141012 em toda a janela."""
    base = _base(i)
    if dia == 1:
        return base
    dividido = 10 <= i <= 24
    valores = {
        "fechamento": base["fechamento"] * (0.5 if dividido else 1.0),
        "indice_retorno_total": base["indice_retorno_total"],
        "volume": base["volume"] * (2.0 if dividido else 1.0),
    }
    if dia == 3:
        valores["fechamento"] *= 141012
    return valores


def _envelope(pacote: str, registros: list, chave: list[str]) -> dict:
    return {
        "pacote": pacote,
        "versao_contrato": "1.0",
        "chave": chave,
        "gerado_em": GERADO,
        "dados_ate": DADOS_ATE,
        "registros": registros,
        "removidos": [],
    }


def _escrever(raiz: Path, d: str, indices: range | list[int], dia: int,
              alterar: dict[int, dict] | None = None) -> None:
    """Escreve saida/<d>/execucao.json e saida_completa/<d>/{series_diarias,mercado_dia,execucao}.json.

    `indices` pode ser uma lista (pula datas do meio). `alterar` troca campos de datas específicas (índice -> campos).
    """
    datas = [DATAS[i].isoformat() for i in indices]

    def valores_do_dia(i: int) -> dict:
        return {**_valores(i, dia), **(alterar or {}).get(i, {})}

    serie = {
        "versao_contrato": "1.0",
        "gerado_em": GERADO,
        "dados_ate": DADOS_ATE,
        "ticker": "TSTE3",
        "tipo": "acao",
        "nome": "Teste Emenda",
        "substituir_serie": True,
        "inicio_janela": datas[0],
        "datas": datas,
        "valores": {campo: [valores_do_dia(i)[campo] for i in indices] for campo in CAMPOS_SERIE},
        "status": {},
    }
    mercado = {
        "versao_contrato": "1.0",
        "gerado_em": GERADO,
        "dados_ate": DADOS_ATE,
        "data_referencia": d,
        "IBOV": {"fechamento": {"valor": 100000.0, "status": "ok"}, "unidade": "pontos"},
    }
    execucao = {
        "versao_contrato": "1.0",
        "gerado_em": GERADO,
        "dados_ate": DADOS_ATE,
        "data_referencia": d,
        "rodada": 1,
        "status": "ok",
        "pacotes_enviados": [
            {"pacote": "series_diarias", "arquivo": "series_diarias.json", "registros": 1, "removidos": 0},
            {"pacote": "mercado_dia", "arquivo": "mercado_dia.json", "registros": 1, "removidos": 0},
        ],
    }
    chave_execucao = ["data_referencia", "rodada"]
    saida = raiz / "saida" / d
    completa = raiz / "saida_completa" / d
    saida.mkdir(parents=True)
    completa.mkdir(parents=True)
    (saida / "execucao.json").write_text(
        json.dumps(_envelope("execucao", [execucao], chave_execucao), ensure_ascii=False), encoding="utf-8")
    (completa / "series_diarias.json").write_text(
        json.dumps(_envelope("series_diarias", [serie], ["ticker", "data"]), ensure_ascii=False), encoding="utf-8")
    (completa / "mercado_dia.json").write_text(
        json.dumps(_envelope("mercado_dia", [mercado], ["data_referencia"]), ensure_ascii=False), encoding="utf-8")
    (completa / "execucao.json").write_text(
        json.dumps(_envelope("execucao", [execucao], chave_execucao), ensure_ascii=False), encoding="utf-8")


def test_emenda_ponta_a_ponta(conexao, cfg, tmp_path):
    api = ClienteAPI(dataclasses.replace(cfg, dir_processamento=tmp_path))

    # Dia 1: 40 datas, sem evento.
    _escrever(tmp_path, D1, range(0, 40), 1)
    publicar(conexao, tmp_path, D1)

    # Dia 2: 45 datas (5 novas), com o desdobramento: a emenda deve ajustar os 10 pontos antigos.
    _escrever(tmp_path, D2, range(10, 45), 2)
    publicar(conexao, tmp_path, D2)

    ajustes = _ajustes(conexao)
    assert [(a["campo"], a["classificacao"], a["aplicado"]) for a in ajustes] == [
        ("fechamento", "evento", 1),
        ("volume", "evento", 1),
    ]
    pontos = _pontos(conexao)
    assert len(pontos) == 45
    for i in range(10):
        assert pontos[i][1]["fechamento"] == pytest.approx(_base(i)["fechamento"] * 0.5)

    janela = api.get("/v1/series_diarias/TSTE3", params={"janela": "max"}).json()["registros"][0]
    fechamento = janela["valores"]["fechamento"]
    assert len(fechamento) == 45
    # Sem degrau na emenda: a razão entre os pontos 9 e 10 é a original, não a original × 2.
    assert fechamento[9] / fechamento[10] == pytest.approx(_base(9)["fechamento"] / _base(10)["fechamento"], rel=1e-6)
    assert contagens(conexao)["teste_correcoes"] == 0

    # Republicar o dia 2 (antes do dia 3) não aplica o fator de novo.
    ajustes_antes = _ajustes(conexao)
    pontos_antes = _pontos(conexao)
    plano = publicar(conexao, tmp_path, D2)
    assert plano.modo == "republicacao"
    assert _ajustes(conexao) == ajustes_antes
    assert _pontos(conexao) == pontos_antes

    # Dia 3: fechamento de toda a janela ×141012 → fator fora de [0,01; 100] → recusado, antigos intactos.
    _escrever(tmp_path, D3, range(10, 45), 3)
    publicar(conexao, tmp_path, D3)

    ajustes = _ajustes(conexao)
    assert len(ajustes) == 3
    recusados = [(a["campo"], a["aplicado"]) for a in ajustes if a["classificacao"] == "recusado"]
    assert recusados == [("fechamento", 0)]

    pontos_depois = _pontos(conexao)
    assert pontos_depois[:10] == pontos_antes[:10]

    janela = api.get("/v1/series_diarias/TSTE3", params={"janela": "max"}).json()["registros"][0]
    fechamento = janela["valores"]["fechamento"]
    assert fechamento[:10] == [None] * 10
    assert fechamento[10] is not None
    trechos = janela["status"]["fechamento"]
    assert any(
        t["status"] == "indisponivel" and t["de"] == DATAS[0].isoformat() and t["ate"] == DATAS[9].isoformat()
        for t in trechos
    ), trechos


def test_ponto_removido_volta_sem_chave_duplicada(conexao, tmp_path):
    # Dia 1: janela inteira (45 datas).
    _escrever(tmp_path, D1, range(0, 45), 1)
    publicar(conexao, tmp_path, D1)

    # Dia 2: a mesma janela sem a data de índice 20: o ponto vira removido (removido_em = D2).
    data_meio = DATAS[20].isoformat()
    _escrever(tmp_path, D2, [i for i in range(0, 45) if i != 20], 1)
    publicar(conexao, tmp_path, D2)
    linha = conexao.consultar(
        f"SELECT removido_em FROM {conexao.t('series_pontos')} WHERE ticker = %s AND data = %s",
        ("TSTE3", data_meio),
    )
    assert str(linha[0]["removido_em"]) == D2
    assert len(_pontos(conexao)) == 44

    # Dia 3: a data volta, com fechamento diferente do removido. Reabre a linha (antes: chave duplicada).
    _escrever(tmp_path, D3, range(0, 45), 1, alterar={20: {"fechamento": 99.0}})
    plano = publicar(conexao, tmp_path, D3)
    assert plano.contagens().get("series_pontos_reabertos") == 1
    assert "series_pontos" not in plano.contagens()

    linha = conexao.consultar(
        f"SELECT removido_em, valores, hash FROM {conexao.t('series_pontos')} WHERE ticker = %s AND data = %s",
        ("TSTE3", data_meio),
    )
    assert linha[0]["removido_em"] is None
    assert banco.ler_json(linha[0]["valores"])["fechamento"] == 99.0
    assert dict(_pontos(conexao))[data_meio]["fechamento"] == 99.0

    # A remoção e a volta ficam em correcoes: a remoção com hash_novo zerado, a volta com hash_anterior zerado.
    zeros = "0" * 64
    correcoes = [
        c for c in conexao.consultar(
            f"SELECT chave, hash_anterior, hash_novo FROM {conexao.t('correcoes')} WHERE tabela = %s",
            ("series_pontos",),
        )
        if banco.ler_json(c["chave"]) == {"ticker": "TSTE3", "data": data_meio}
    ]
    remocoes = [c for c in correcoes if c["hash_novo"] == zeros]
    voltas = [c for c in correcoes if c["hash_anterior"] == zeros]
    assert len(correcoes) == 2
    assert len(remocoes) == 1 and remocoes[0]["hash_anterior"] != zeros
    assert len(voltas) == 1 and voltas[0]["hash_novo"] == linha[0]["hash"]

    # Republicar o dia 3: nenhuma operação de ponto e as tabelas com as mesmas contagens.
    antes = contagens(conexao)
    plano_de_novo = publicar(conexao, tmp_path, D3)
    assert plano_de_novo.modo == "republicacao"
    assert contagens(conexao) == antes
    for chave in ("series_pontos", "series_pontos_atualizados", "series_pontos_removidos", "series_pontos_reabertos"):
        assert chave not in plano_de_novo.contagens()


# ------------------------------------------------------ 11. experimento_top e experimento_historico (sintéticos)

TICKERS_TOP = ("TSTA3", "TSTB4", "TSTC3", "TSTD4", "TSTF3")


def _experimento_top(d: str, pregao_base: str, tickers: tuple[str, ...]) -> dict:
    """experimento_top no formato da seção 8.3 item 8 do contrato (valores ilustrativos)."""
    ranking = []
    for posicao, ticker in enumerate(tickers, start=1):
        ranking.append({
            "posicao": posicao,
            "ticker": ticker,
            "chance": {"valor": None, "referencia_acaso": None, "exibir": False,
                       "status": "calibracao_nao_conferida"},
            "motivos": [
                {"fator": "noticias_dia_tom", "valor": 0.42, "efeito": "ajudou",
                 "texto": f"Notícias do último pregão e da noite com tom positivo (posição {posicao})"},
                {"fator": "ret_5d", "valor": -0.031, "efeito": "ajudou",
                 "texto": "Caiu 3,1% nos últimos 5 pregões (abaixo da média das ações)"},
            ],
            "noticias_usadas": [f"n{posicao:06d}"],
        })
    return {
        "versao_contrato": "1.0",
        "gerado_em": GERADO,
        "dados_ate": DADOS_ATE,
        "data_referencia": d,
        "pregao_previsto": d,
        "pregao_base": pregao_base,
        "valido_para_historico": True,
        "modelo_versao": "xgb-2030-02",
        "modelo_provisorio": False,
        "universo": "composicao_atual",
        "n_candidatas": 84,
        "selo": "em_avaliacao",
        "titulo": "5 ações com maior chance de superar o Ibovespa (experimento)",
        "aviso_fixo": "Não constitui recomendação de investimento.",
        "avisos": [],
        "ranking": ranking,
    }


def _experimento_historico(d: str) -> dict:
    """experimento_historico sem dias fechados: dia null e taxa de acerto sem histórico suficiente."""
    return {
        "versao_contrato": "1.0",
        "gerado_em": GERADO,
        "dados_ate": DADOS_ATE,
        "data_referencia": d,
        "dia": None,
        "resumo": {
            "periodo_inicio": None,
            "periodo_fim": None,
            "n_dias": 0,
            "taxa_acerto": {"valor": None, "status": "historico_insuficiente"},
            "nota": "Histórico calculado com as ações que compõem o Ibovespa atualmente. Ainda não há dias fechados.",
        },
    }


def _escrever_experimento(raiz: Path, d: str, pregao: str, top: dict, historico: dict) -> None:
    """Escreve saida/<d>/execucao.json e saida_completa/<d>/{mercado_dia,experimento_top,experimento_historico,
    execucao}.json, com pacotes_enviados batendo com os arquivos da completa."""
    mercado = {
        "versao_contrato": "1.0",
        "gerado_em": GERADO,
        "dados_ate": DADOS_ATE,
        "data_referencia": pregao,
        "IBOV": {"fechamento": {"valor": 100000.0, "status": "ok"}, "unidade": "pontos"},
    }
    enviados = [
        {"pacote": "mercado_dia", "arquivo": "mercado_dia.json", "registros": 1, "removidos": 0},
        {"pacote": "experimento_top", "arquivo": "experimento_top.json", "registros": 1, "removidos": 0},
        {"pacote": "experimento_historico", "arquivo": "experimento_historico.json", "registros": 1, "removidos": 0},
    ]
    execucao = {
        "versao_contrato": "1.0",
        "gerado_em": GERADO,
        "dados_ate": DADOS_ATE,
        "data_referencia": d,
        "rodada": 1,
        "status": "ok",
        "pacotes_enviados": enviados,
    }
    chave_execucao = ["data_referencia", "rodada"]
    saida = raiz / "saida" / d
    completa = raiz / "saida_completa" / d
    saida.mkdir(parents=True)
    completa.mkdir(parents=True)
    (saida / "execucao.json").write_text(
        json.dumps(_envelope("execucao", [execucao], chave_execucao), ensure_ascii=False), encoding="utf-8")
    (completa / "mercado_dia.json").write_text(
        json.dumps(_envelope("mercado_dia", [mercado], ["data_referencia"]), ensure_ascii=False), encoding="utf-8")
    (completa / "experimento_top.json").write_text(
        json.dumps(_envelope("experimento_top", [top], ["data_referencia"]), ensure_ascii=False), encoding="utf-8")
    (completa / "experimento_historico.json").write_text(
        json.dumps(_envelope("experimento_historico", [historico], ["data_referencia"]), ensure_ascii=False),
        encoding="utf-8")
    (completa / "execucao.json").write_text(
        json.dumps(_envelope("execucao", [execucao], chave_execucao), ensure_ascii=False), encoding="utf-8")


def test_experimento_top_sintetico(conexao, cfg, tmp_path):
    api = ClienteAPI(dataclasses.replace(cfg, dir_processamento=tmp_path))
    d1, d2 = "2030-02-04", "2030-02-05"  # D: dia da rotina
    p1, p2 = "2030-02-01", "2030-02-04"  # pregão anterior a cada D (data_referencia do mercado_dia)
    top1 = _experimento_top(d1, p1, TICKERS_TOP)
    top2 = _experimento_top(d2, p2, TICKERS_TOP[::-1])  # ranking diferente, para o ?data= distinguir os dias
    hist1, hist2 = _experimento_historico(d1), _experimento_historico(d2)
    _escrever_experimento(tmp_path, d1, p1, top1, hist1)
    _escrever_experimento(tmp_path, d2, p2, top2, hist2)

    publicar(conexao, tmp_path, d1)
    publicar(conexao, tmp_path, d2)

    contagem = contagens(conexao)
    assert contagem["teste_publicacoes"] == 2
    assert contagem["teste_mercado_dia"] == 2
    assert contagem["teste_experimento_top"] == 2
    assert contagem["teste_experimento_historico"] == 2
    assert contagem["teste_execucao"] == 2

    atual = api.get("/v1/experimento/top").json()
    assert atual["registros"] == [top2]
    assert atual["data_publicacao"] == d2
    assert atual["data_referencia"] == d2
    assert atual["registros"][0]["ranking"][0]["chance"]["valor"] is None
    antigo = api.get("/v1/experimento/top", params={"data": d1}).json()
    assert antigo["registros"] == [top1]
    assert antigo["data_publicacao"] == d1
    assert antigo["data_referencia"] == d1

    assert api.get("/v1/experimento/historico").json()["registros"] == [hist2]
    assert api.get("/v1/experimento/historico", params={"data": d1}).json()["registros"] == [hist1]
    assert api.get("/v1/experimento_top").json()["registros"] == [top2]

    plano = publicar(conexao, tmp_path, d2)
    assert plano.modo == "republicacao"
    assert contagens(conexao) == contagem


# ------------------------------------------------------------------------------------- 9. conferir_api contra a API

def test_conferir_api_contra_banco(conexao, cfg, api, capsys):
    """A conferência (só leitura) bate com a API real nas publicações 23 e 26.

    Depois de 26, conferir 23 acusa UMA diferença, de propósito: com ?data= as séries saem com as correções
    posteriores (regra do plano), e 26 corrigiu o volume de um ponto antigo de PETR4. O resto mostra 23 como era.
    """

    def buscar(caminho, params):
        resposta = api.get(caminho, params=params)
        return resposta.status_code, resposta.json()

    publicar(conexao, DIR_DADOS, D23)
    assert conferir_api.conferir(buscar, DIR_DADOS, D23) == 0

    publicar(conexao, DIR_DADOS, D26)
    capsys.readouterr()
    assert conferir_api.conferir(buscar, DIR_DADOS, D23) == 1
    saida = capsys.readouterr().out
    assert "series_diarias[PETR4].valores.volume" in saida
    assert len([linha for linha in saida.splitlines() if "≠" in linha]) == 1  # só o ponto corrigido
    assert conferir_api.conferir(buscar, DIR_DADOS, D26) == 0


# ------------------------------------------------ 12. liberação de recusa SETOR_SEM (sintético, só teste_*)

TICKER_SETOR = "SETOR_SEM:TSTS3"
MOTIVO_TESTE = "teste de integração"
AGORA_TESTE = "2030-01-13T09:00:00"


def test_liberacao_de_recusa_setor_sem(conexao):
    t = conexao.t
    # índices 0..9: antigos (100+2i); 10..14: janela já no nível novo, (100+2i) × 1.26
    pontos = [
        Ponto(DATAS[i].isoformat(),
              {"indice_retorno_total": (100.0 + 2 * i) * (1.26 if i >= 10 else 1.0), "n_empresas": 7}, {})
        for i in range(15)
    ]
    operacoes = [CatalogoGravar(TICKER_SETOR, "setor_sem_empresa", "Setor sem empresa TSTS3",
                                ["indice_retorno_total", "n_empresas"])]
    operacoes += [PontoInserir(TICKER_SETOR, p, series.hash_ponto(p)) for p in pontos]
    operacoes.append(AjusteRegistrar(TICKER_SETOR, Ajuste(
        campo="indice_retorno_total", fator=1.26, de=DATAS[0].isoformat(), ate=DATAS[9].isoformat(), pontos=10,
        origem="emenda", classificacao="recusado", aplicado=False,
        detalhe={"antigos": {"de": DATAS[0].isoformat(), "ate": DATAS[9].isoformat(), "pontos": 10},
                 "motivo": "recusa de teste"},
    )))
    for op in operacoes:
        for sql, params in gravador.comandos_da_operacao(op, D3, t):
            conexao.executar(sql, params)

    # a API esconde os pontos até 'ate' enquanto a recusa existir
    sql_rec = sql_recusas(t("series_ajustes"))
    recusas_api = conexao.consultar(sql_rec, (TICKER_SETOR, "2030-12-31"))
    assert len(recusas_api) == 1 and recusas_api[0]["ate"] == DATAS[9]

    correcoes_antes = contagens(conexao)["teste_correcoes"]
    recusas, tipos, pontos_banco = gravador.carregar_recusas(conexao, D3)
    assert len(recusas) == 1 and recusas[0].ticker == TICKER_SETOR and recusas[0].pontos == 10
    libs, ignoradas = liberacao.planejar(recusas, tipos, pontos_banco, MOTIVO_TESTE, AGORA_TESTE)
    assert ignoradas == [] and len(libs) == 1 and len(libs[0].operacoes) == 10
    gravador.gravar_liberacoes(conexao, libs)

    assert conexao.consultar(sql_rec, (TICKER_SETOR, "2030-12-31")) == []
    linhas = conexao.consultar(
        f"SELECT data, valores FROM {t('series_pontos')} WHERE ticker = %s AND removido_em IS NULL ORDER BY data",
        (TICKER_SETOR,),
    )
    assert len(linhas) == 15
    for i, linha in enumerate(linhas):
        assert banco.ler_json(linha["valores"])["indice_retorno_total"] == pytest.approx((100.0 + 2 * i) * 1.26)
    assert contagens(conexao)["teste_correcoes"] - correcoes_antes == 10

    ajustes = conexao.consultar(
        f"SELECT classificacao, aplicado, detalhe FROM {t('series_ajustes')} WHERE ticker = %s", (TICKER_SETOR,))
    assert [(a["classificacao"], a["aplicado"]) for a in ajustes] == [("irregular", 1)]
    detalhe = banco.ler_json(ajustes[0]["detalhe"])
    assert detalhe["liberacao"]["classificacao_anterior"] == "recusado"
    assert detalhe["liberacao"]["pontos_emendados"] == 10
    assert detalhe["antigos"] == {"de": DATAS[0].isoformat(), "ate": DATAS[9].isoformat(), "pontos": 10}

    # repetir não faz nada: a recusa já não é 'recusado'
    antes_de_novo = contagens(conexao)
    recusas2, tipos2, pontos2 = gravador.carregar_recusas(conexao, D3)
    assert recusas2 == []
    libs2, ignoradas2 = liberacao.planejar(recusas2, tipos2, pontos2, MOTIVO_TESTE, AGORA_TESTE)
    assert libs2 == [] and ignoradas2 == []
    gravador.gravar_liberacoes(conexao, libs2)
    assert contagens(conexao) == antes_de_novo
