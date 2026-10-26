"""Testes do planejador (publicador/planejador.py), sem banco.

Os JSON de tests/dados são lidos direto aqui (não passam por publicador/leitor.py).
"""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from comum import series
from publicador.entrada import Entrada
from publicador.operacoes import (
    AjusteRegistrar,
    Estado,
    FotoAtualizar,
    FotoInserir,
    PontoAtualizar,
    PontoInserir,
    PontoRemover,
    VersaoAntecipar,
    VersaoCorrigir,
    VersaoFechar,
    VersaoInserir,
    VersaoReabrir,
)
from publicador.planejador import ErroPublicacao, aplicar_em_memoria, planejar

DADOS = Path(__file__).resolve().parent.parent / "dados"


# ---------------------------------------------------------------- helpers com dados reais

def _ler(caminho: Path) -> dict:
    return json.loads(caminho.read_text(encoding="utf-8"))


def entrada_de(d: str, sem_completa: bool = False) -> Entrada:
    """Entrada montada a partir de tests/dados/saida/<d> e, se existir, tests/dados/saida_completa/<d>."""
    execucao = _ler(DADOS / "saida" / d / "execucao.json")
    pasta = DADOS / "saida_completa" / d
    completa, execucao_completa = None, None
    if pasta.exists() and not sem_completa:
        completa = {}
        for arquivo in sorted(pasta.glob("*.json")):
            envelope = _ler(arquivo)
            if arquivo.stem == "execucao":
                execucao_completa = envelope
            else:
                completa[arquivo.stem] = envelope
    return Entrada(data=d, execucao=execucao, completa=completa, execucao_completa=execucao_completa)


def publicar(entrada: Entrada, estado: Estado):
    plano = planejar(entrada, estado)
    aplicar_em_memoria(estado, plano)
    return plano


def ops(plano, tipo) -> list:
    return [op for op in plano.operacoes if isinstance(op, tipo)]


# ---------------------------------------------------------------- helpers de séries sintéticas

def datas_uteis(n: int) -> list[str]:
    """n datas ISO consecutivas (dias úteis) a partir de 2025-01-01."""
    datas, i = [], 0
    while len(datas) < n:
        d = date(2025, 1, 1) + timedelta(days=i)
        if d.weekday() < 5:
            datas.append(d.isoformat())
        i += 1
    return datas


DATAS = datas_uteis(45)
D_SINTETICA = DATAS[44]


def valores_base(i: int) -> dict:
    return {
        "fechamento": 10.0 + i,
        "indice_retorno_total": 100.0 + 2 * i,
        "volume": 1000.0 + 3 * i,
        "pl": 5.0 + 0.1 * i,
        "pvp": 1.0 + 0.01 * i,
        "dividend_yield": 0.01 + 0.001 * i,
    }


def registro_serie(ticker: str, tipo: str, indices, fn) -> dict:
    """Registro de série no formato do contrato, com as datas DATAS[i] para i em indices."""
    indices = list(indices)
    linhas = [fn(i) for i in indices]
    campos = list(linhas[0].keys())
    return {
        "versao_contrato": "1.0",
        "gerado_em": "2026-10-25T12:00:00-03:00",
        "dados_ate": "2026-10-25T08:00:00-03:00",
        "ticker": ticker,
        "tipo": tipo,
        "nome": ticker,
        "substituir_serie": True,
        "inicio_janela": DATAS[indices[0]],
        "datas": [DATAS[i] for i in indices],
        "valores": {c: [linha[c] for linha in linhas] for c in campos},
        "status": {},
    }


def envelope_series(registros: list) -> dict:
    return {
        "pacote": "series_diarias",
        "versao_contrato": "1.0",
        "chave": ["ticker", "data"],
        "gerado_em": "2026-10-25T12:00:00-03:00",
        "dados_ate": "2026-10-25T08:00:00-03:00",
        "registros": registros,
        "removidos": [],
    }


def execucao_sintetica(d: str) -> dict:
    return {
        "pacote": "execucao",
        "versao_contrato": "1.0",
        "chave": ["data_referencia", "rodada"],
        "gerado_em": "2026-10-25T12:00:00-03:00",
        "dados_ate": "2026-10-25T08:00:00-03:00",
        "registros": [{"versao_contrato": "1.0", "data_referencia": d, "rodada": 1, "status": "ok"}],
        "removidos": [],
    }


def entrada_series(registros: list) -> Entrada:
    return Entrada(
        data=D_SINTETICA,
        execucao=execucao_sintetica(D_SINTETICA),
        completa={"series_diarias": envelope_series(registros)},
    )


def banco_series(registros_base: list) -> Estado:
    """Estado com os pontos das séries (ex.: datas 0..39) já publicados, sem publicações."""
    estado = Estado()
    for reg in registros_base:
        ticker = reg["ticker"]
        estado.catalogo[ticker] = {"tipo": reg["tipo"], "nome": reg["nome"], "campos": list(reg["valores"])}
        estado.pontos[ticker] = {p.data: p for p in series.compacto_para_pontos(reg)}
    return estado


def montar(estado: Estado, ticker: str, janela: str = "max", recusas=None) -> dict:
    catalogo = {"ticker": ticker, **estado.catalogo[ticker]}
    pontos = sorted(estado.pontos[ticker].values(), key=lambda p: p.data)
    meta = {"versao_contrato": "1.0", "gerado_em": "x", "dados_ate": "y"}
    return series.montar_registro(meta, catalogo, pontos, janela, recusas)


# ---------------------------------------------------------------- testes com os dados reais

def test_a_primeira_publicacao_de_23_e_normal():
    estado = Estado()
    plano = planejar(entrada_de("2026-10-23"), estado)

    assert plano.modo == "normal"
    assert plano.status_publicacao == "completa"
    assert plano.data_referencia == "2026-10-22"
    pontos_esperados = sum(
        len(r["datas"]) for r in _ler(DADOS / "saida_completa" / "2026-10-23" / "series_diarias.json")["registros"]
    )
    assert plano.contagens() == {
        "resumo_acao": 3,
        "noticias_dia": 3,
        "mercado_dia": 1,
        "execucao": 1,
        "series_pontos": pontos_esperados,
        "acoes": 3,
        "fundamentos_trimestrais": 24,
        "proventos": 128,
    }
    assert "series_ajustes" not in plano.contagens()


def test_b_republicar_23_e_republicacao_sem_operacoes():
    estado = Estado()
    publicar(entrada_de("2026-10-23"), estado)

    plano = planejar(entrada_de("2026-10-23"), estado)

    assert plano.modo == "republicacao"
    assert plano.contagens() == {}


def test_c_publicar_26_depois_de_23_e_normal_com_emenda_de_pontos():
    estado = Estado()
    publicar(entrada_de("2026-10-23"), estado)

    plano = planejar(entrada_de("2026-10-26"), estado)

    assert plano.modo == "normal"
    assert plano.contagens() == {
        "resumo_acao": 3,
        "noticias_dia": 3,
        "mercado_dia": 1,
        "execucao": 1,
        "series_pontos": 8,
        "series_pontos_atualizados": 1,
        "fundamentos_trimestrais": 1,
        "fundamentos_trimestrais_fechadas": 1,
        "proventos_fechadas": 1,
    }
    atualizados = ops(plano, PontoAtualizar)
    assert len(atualizados) == 1 and atualizados[0].anterior is not None
    assert not ops(plano, AjusteRegistrar)

    aplicar_em_memoria(estado, plano)
    fechadas_vale3 = [
        v
        for chave, lista in estado.versoes["fundamentos_trimestrais"].items()
        if chave[0] == "VALE3"
        for v in lista
        if v.valido_ate == "2026-10-26"
    ]
    assert len(fechadas_vale3) == 1


def test_d_27_so_com_execucao_falhou():
    estado = Estado()
    publicar(entrada_de("2026-10-23"), estado)
    publicar(entrada_de("2026-10-26"), estado)

    plano = planejar(entrada_de("2026-10-27", sem_completa=True), estado)

    assert plano.modo == "so_execucao"
    assert plano.contagens() == {"execucao": 1}
    assert len(ops(plano, FotoInserir)) == 1
    assert ops(plano, FotoInserir)[0].pacote == "execucao"
    assert plano.data_referencia is None

    aplicar_em_memoria(estado, plano)
    assert estado.publicacoes["2026-10-23"] == "completa"
    assert estado.publicacoes["2026-10-26"] == "completa"
    assert estado.publicacoes["2026-10-27"] == "so_execucao"


def test_e_buraco_publicar_26_e_depois_23():
    estado = Estado()
    publicar(entrada_de("2026-10-26"), estado)

    plano = planejar(entrada_de("2026-10-23"), estado)

    assert plano.modo == "buraco"
    assert plano.contagens() == {
        "resumo_acao": 3,
        "noticias_dia": 3,
        "mercado_dia": 1,
        "execucao": 1,
        "series_pontos": 8,
        "fundamentos_trimestrais": 1,
        "proventos": 1,
    }
    assert not ops(plano, PontoAtualizar)
    assert not ops(plano, PontoRemover)
    assert not ops(plano, AjusteRegistrar)
    assert not ops(plano, VersaoFechar)

    antecipar_acoes = [op for op in ops(plano, VersaoAntecipar) if op.pacote == "acoes"]
    assert len(antecipar_acoes) == 3

    insert_fund = [op for op in ops(plano, VersaoInserir) if op.pacote == "fundamentos_trimestrais"]
    assert len(insert_fund) == 1
    assert insert_fund[0].chave["ticker"] == "VALE3"
    assert insert_fund[0].valido_desde == "2026-10-23"
    assert insert_fund[0].valido_ate == "2026-10-26"
    antecipar_fund = [op for op in ops(plano, VersaoAntecipar) if op.pacote == "fundamentos_trimestrais"]
    assert len(antecipar_fund) == 23

    insert_prov = [op for op in ops(plano, VersaoInserir) if op.pacote == "proventos"]
    assert len(insert_prov) == 1
    assert insert_prov[0].chave["ticker"] == "ITUB4"
    assert insert_prov[0].valido_desde == "2026-10-23"
    assert insert_prov[0].valido_ate == "2026-10-26"

    aplicar_em_memoria(estado, plano)
    assert publicar(entrada_de("2026-10-23"), estado).contagens() == {}


def test_f_trava_dos_versionados():
    estado = Estado()
    publicar(entrada_de("2026-10-23"), estado)

    entrada = entrada_de("2026-10-23")
    entrada.completa["proventos"]["registros"] = entrada.completa["proventos"]["registros"][:100]
    with pytest.raises(ErroPublicacao, match="--forcar"):
        planejar(entrada, estado)

    plano = planejar(entrada, estado, forcar=True)
    assert plano.contagens() == {"proventos_fechadas": 28}

    vazia = entrada_de("2026-10-23")
    vazia.completa["proventos"]["registros"] = []
    with pytest.raises(ErroPublicacao):
        planejar(vazia, estado)


def test_f_resumo_com_menos_registros_nao_dispara_trava():
    estado = Estado()
    publicar(entrada_de("2026-10-23"), estado)

    entrada = entrada_de("2026-10-23")
    entrada.completa["resumo_acao"]["registros"] = entrada.completa["resumo_acao"]["registros"][:1]
    plano = planejar(entrada, estado)

    assert plano.modo == "republicacao"
    assert plano.contagens() == {}


def test_g_execucao_com_mesma_chave_e_status_diferente_vira_atualizacao():
    estado = Estado()
    publicar(entrada_de("2026-10-23"), estado)

    entrada = entrada_de("2026-10-23")
    entrada.execucao["registros"][0]["status"] = "parcial"
    plano = planejar(entrada, estado)

    assert plano.contagens() == {"execucao_atualizadas": 1}
    atualizacao = ops(plano, FotoAtualizar)[0]
    assert atualizacao.pacote == "execucao"
    assert atualizacao.chave == {"data_referencia": "2026-10-23", "rodada": 1}


def test_h_republicacao_com_versao_alterada_corrige_no_lugar():
    estado = Estado()
    publicar(entrada_de("2026-10-23"), estado)

    entrada = entrada_de("2026-10-23")
    entrada.completa["acoes"]["registros"][0]["subsetor"] = "Subsetor alterado"
    plano = planejar(entrada, estado)

    assert plano.contagens() == {"acoes_corrigidas": 1}
    assert len(ops(plano, VersaoCorrigir)) == 1
    assert not ops(plano, VersaoFechar)
    assert not [op for op in ops(plano, VersaoInserir) if op.pacote == "acoes"]


def test_h_chave_fechada_no_mesmo_dia_que_reaparece_reabre():
    estado = Estado()
    publicar(entrada_de("2026-10-23"), estado)

    sumida = entrada_de("2026-10-23")
    sumida.completa["proventos"]["registros"].pop(0)
    plano_sumiu = planejar(sumida, estado)
    assert plano_sumiu.contagens() == {"proventos_fechadas": 1}
    aplicar_em_memoria(estado, plano_sumiu)

    plano_voltou = planejar(entrada_de("2026-10-23"), estado)
    assert len(ops(plano_voltou, VersaoReabrir)) == 1
    assert not ops(plano_voltou, VersaoInserir)
    assert not ops(plano_voltou, VersaoFechar)
    assert plano_voltou.contagens() == {}


def test_i_ponto_extra_dentro_da_janela_vira_remocao():
    registros_base = [registro_serie("PETR4", "acao", range(0, 40), valores_base)]
    estado = banco_series(registros_base)
    indices = [i for i in range(10, 45) if i != 20]
    entrada = entrada_series([registro_serie("PETR4", "acao", indices, valores_base)])

    plano = planejar(entrada, estado)

    assert plano.modo == "normal"
    removidos = ops(plano, PontoRemover)
    assert [op.data for op in removidos] == [DATAS[20]]
    assert plano.contagens().get("series_pontos_removidos") == 1

    aplicar_em_memoria(estado, plano)
    assert DATAS[20] not in estado.pontos["PETR4"]


# ---------------------------------------------------------------- séries: os 6 casos da emenda

def test_serie_1_dividendo_nao_muda_antigos():
    estado = banco_series([registro_serie("PETR4", "acao", range(0, 40), valores_base)])
    entrada = entrada_series([registro_serie("PETR4", "acao", range(10, 45), valores_base)])

    plano = planejar(entrada, estado)

    assert not ops(plano, AjusteRegistrar)
    assert not ops(plano, PontoAtualizar)
    assert len(ops(plano, PontoInserir)) == 5


def test_serie_2_desdobramento_emenda_antigos_sem_degrau():
    estado = banco_series([registro_serie("PETR4", "acao", range(0, 40), valores_base)])
    antigos_fech = {DATAS[i]: valores_base(i)["fechamento"] for i in range(40)}

    def desdobramento(i):
        v = valores_base(i)
        if i < 25:
            v["fechamento"] *= 0.5
            v["volume"] *= 2
        return v

    entrada = entrada_series([registro_serie("PETR4", "acao", range(10, 45), desdobramento)])
    plano = planejar(entrada, estado)

    ajustes = ops(plano, AjusteRegistrar)
    assert sorted((a.ajuste.campo, a.ajuste.classificacao) for a in ajustes) == [
        ("fechamento", "evento"),
        ("volume", "evento"),
    ]
    assert all(a.ajuste.aplicado for a in ajustes)

    atualizados = ops(plano, PontoAtualizar)
    antigos_att = [a for a in atualizados if a.ponto.data < DATAS[10]]
    assert len(antigos_att) == 10
    assert all(a.anterior is None for a in antigos_att)
    for a in antigos_att:
        i = DATAS.index(a.ponto.data)
        assert a.ponto.valores["fechamento"] == pytest.approx(valores_base(i)["fechamento"] * 0.5)
        assert a.ponto.valores["volume"] == pytest.approx(valores_base(i)["volume"] * 2)

    janela_att = [a for a in atualizados if DATAS[10] <= a.ponto.data <= DATAS[24]]
    assert len(janela_att) == 15
    assert all(a.anterior is None for a in janela_att)
    assert not [a for a in atualizados if DATAS[25] <= a.ponto.data]

    aplicar_em_memoria(estado, plano)
    registro = montar(estado, "PETR4", "2a")
    fech = registro["valores"]["fechamento"]
    assert fech[9] / fech[10] == pytest.approx(antigos_fech[DATAS[9]] / antigos_fech[DATAS[10]])


def test_serie_6_republicar_depois_da_emenda_nao_aplica_fator_de_novo():
    estado = banco_series([registro_serie("PETR4", "acao", range(0, 40), valores_base)])

    def desdobramento(i):
        v = valores_base(i)
        if i < 25:
            v["fechamento"] *= 0.5
            v["volume"] *= 2
        return v

    entrada = entrada_series([registro_serie("PETR4", "acao", range(10, 45), desdobramento)])
    publicar(entrada, estado)

    plano = planejar(entrada, estado)

    assert plano.modo == "republicacao"
    assert not ops(plano, AjusteRegistrar)
    assert not ops(plano, PontoAtualizar)


def test_serie_3_mudanca_de_ancora_e_uniforme_uma_linha_por_serie():
    estado = banco_series([
        registro_serie("PETR4", "acao", range(0, 40), valores_base),
        registro_serie("VALE3", "acao", range(0, 40), valores_base),
    ])

    def ancora(i):
        v = valores_base(i)
        v["indice_retorno_total"] *= 0.8
        return v

    entrada = entrada_series([
        registro_serie("PETR4", "acao", range(10, 45), ancora),
        registro_serie("VALE3", "acao", range(10, 45), ancora),
    ])
    plano = planejar(entrada, estado)

    ajustes = ops(plano, AjusteRegistrar)
    assert len(ajustes) == 2
    assert sorted(a.ticker for a in ajustes) == ["PETR4", "VALE3"]
    assert all(a.ajuste.campo == "indice_retorno_total" for a in ajustes)
    assert all(a.ajuste.classificacao == "uniforme" for a in ajustes)
    assert all(a.ajuste.aplicado for a in ajustes)


def test_serie_4_fator_absurdo_e_recusado_e_antigos_ficam_indisponiveis():
    estado = banco_series([registro_serie("AZUL3", "acao", range(0, 40), valores_base)])

    def absurdo(i):
        v = valores_base(i)
        v["fechamento"] *= 141012
        return v

    entrada = entrada_series([registro_serie("AZUL3", "acao", range(10, 45), absurdo)])
    plano = planejar(entrada, estado)

    recusados = [a for a in ops(plano, AjusteRegistrar) if a.ajuste.classificacao == "recusado"]
    assert len(recusados) == 1
    assert recusados[0].ajuste.campo == "fechamento"
    assert not recusados[0].ajuste.aplicado
    assert any("AZUL3" in aviso and "recusada" in aviso for aviso in plano.avisos)
    assert not [a for a in ops(plano, PontoAtualizar) if a.ponto.data < DATAS[10]]

    aplicar_em_memoria(estado, plano)
    assert estado.recusas["AZUL3"]["fechamento"] == DATAS[9]

    registro = montar(estado, "AZUL3", "max", estado.recusas["AZUL3"])
    assert registro["valores"]["fechamento"][:10] == [None] * 10
    assert registro["status"]["fechamento"][0] == {"de": DATAS[0], "ate": DATAS[9], "status": "indisponivel"}
    assert estado.pontos["AZUL3"][DATAS[0]].valores["fechamento"] == valores_base(0)["fechamento"]


def test_serie_5_setor_sem_composicao_nova_e_irregular_com_emenda():
    def base_setor(i):
        return {"indice_retorno_total": 100.0 + 2 * i, "n_empresas": 10}

    estado = banco_series([registro_serie("SETOR_SEM:PETR4", "setor_sem_empresa", range(0, 40), base_setor)])

    def composicao_nova(i):
        v = base_setor(i)
        if i <= 20:
            v["indice_retorno_total"] *= 0.9
        else:
            v["indice_retorno_total"] *= 1.0 + 0.01 * (i - 20)
        v["n_empresas"] = 12
        return v

    entrada = entrada_series([registro_serie("SETOR_SEM:PETR4", "setor_sem_empresa", range(10, 45), composicao_nova)])
    plano = planejar(entrada, estado)

    ajustes = ops(plano, AjusteRegistrar)
    assert len(ajustes) == 1
    assert ajustes[0].ajuste.campo == "indice_retorno_total"
    assert ajustes[0].ajuste.classificacao == "irregular"
    assert ajustes[0].ajuste.aplicado

    antigos_att = [a for a in ops(plano, PontoAtualizar) if a.ponto.data < DATAS[10]]
    assert len(antigos_att) == 10
    assert all(a.anterior is None for a in antigos_att)
    for a in antigos_att:
        i = DATAS.index(a.ponto.data)
        assert a.ponto.valores["indice_retorno_total"] == pytest.approx(base_setor(i)["indice_retorno_total"] * 0.9)

    janela_att = [a for a in ops(plano, PontoAtualizar) if DATAS[10] <= a.ponto.data <= DATAS[20]]
    assert janela_att and all(a.anterior is not None for a in janela_att)


