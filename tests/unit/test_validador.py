"""Testes do validador: as entradas reais passam e cada violação do contrato é pega."""
import copy
from pathlib import Path

import pytest

from publicador import leitor
from publicador.validador import validar

DADOS = Path(__file__).parents[1] / "dados"
ENTRADA_23 = leitor.ler(DADOS, "2026-10-23")


def _copia():
    return copy.deepcopy(ENTRADA_23)


def _tem(erros, trecho):
    assert any(trecho in m for m in erros), erros[:10]


@pytest.mark.parametrize("data", ["2026-10-23", "2026-10-26", "2026-10-27"])
def test_entradas_reais_passam(data):
    assert validar(leitor.ler(DADOS, data)) == []


def test_status_invalido_em_folha():
    e = _copia()
    e.completa["resumo_acao"]["registros"][0]["cotacao"]["fechamento"]["status"] = "bom"
    _tem(validar(e), "cotacao.fechamento: status 'bom' inválido")


def test_ok_com_valor_nulo():
    e = _copia()
    e.completa["resumo_acao"]["registros"][0]["cotacao"]["fechamento"]["valor"] = None
    _tem(validar(e), "status 'ok' com valor nulo")


def test_valor_nao_nulo_com_indisponivel():
    e = _copia()
    e.completa["resumo_acao"]["registros"][0]["cotacao"]["fechamento"]["status"] = "indisponivel"
    _tem(validar(e), "com status 'indisponivel'")


def test_valor_com_lucro_negativo_e_aceito():
    e = _copia()
    e.completa["resumo_acao"]["registros"][0]["cotacao"]["fechamento"]["status"] = "lucro_negativo"
    assert validar(e) == []


def test_status_de_experimento_nao_vale_fora_do_experimento():
    e = _copia()
    e.completa["resumo_acao"]["registros"][0]["cotacao"]["fechamento"]["status"] = "calibracao_nao_conferida"
    _tem(validar(e), "status 'calibracao_nao_conferida' inválido")


def test_versao_contrato_no_envelope():
    e = _copia()
    e.completa["acoes"]["versao_contrato"] = "2.0"
    _tem(validar(e), "acoes.versao_contrato: '2.0'")


def test_versao_contrato_no_registro():
    e = _copia()
    e.completa["acoes"]["registros"][0]["versao_contrato"] = "2.0"
    _tem(validar(e), "versao_contrato: '2.0'")


def test_chave_do_envelope_diferente():
    e = _copia()
    e.completa["proventos"]["chave"] = ["ticker", "data_com"]
    _tem(validar(e), "proventos.chave")


def test_pacote_com_nome_trocado():
    e = _copia()
    e.completa["acoes"]["pacote"] = "proventos"
    _tem(validar(e), "acoes.pacote: 'proventos'")


def test_chave_duplicada_em_resumo_acao():
    e = _copia()
    regs = e.completa["resumo_acao"]["registros"]
    regs.append(copy.deepcopy(regs[0]))
    _tem(validar(e), "chave duplicada")


def test_serie_com_len_valores_diferente_de_datas():
    e = _copia()
    serie = e.completa["series_diarias"]["registros"][0]
    serie["valores"]["fechamento"] = serie["valores"]["fechamento"][:-1]
    _tem(validar(e), "valores.fechamento: 252 valores para 253 datas")


def test_serie_null_sem_trecho():
    e = _copia()
    serie = next(r for r in e.completa["series_diarias"]["registros"] if not r.get("status"))
    serie["valores"]["fechamento"][5] = None
    _tem(validar(e), "null sem trecho de status")


def test_serie_com_datas_fora_de_ordem():
    e = _copia()
    datas = e.completa["series_diarias"]["registros"][0]["datas"]
    datas[0], datas[1] = datas[1], datas[0]
    _tem(validar(e), "não é posterior")


def test_serie_trecho_com_de_fora_das_datas():
    e = _copia()
    serie = e.completa["series_diarias"]["registros"][0]
    serie["status"]["pl"][0]["de"] = "2000-01-01"
    _tem(validar(e), "'de' '2000-01-01'")


def test_serie_null_coberto_por_trecho_passa():
    e = _copia()
    serie = next(r for r in e.completa["series_diarias"]["registros"] if r.get("status"))
    campo, trechos = next(iter(serie["status"].items()))
    datas = serie["datas"]
    i = datas.index(trechos[0]["de"])
    assert serie["valores"][campo][i] is None
    assert validar(e) == []


def test_contagem_diferente_em_pacotes_enviados():
    e = _copia()
    e.execucao_completa["registros"][0]["pacotes_enviados"][0]["registros"] += 1
    _tem(validar(e), "registros, arquivo tem")


def test_pacote_enviado_sem_arquivo():
    e = _copia()
    e.execucao_completa["registros"][0]["pacotes_enviados"].append(
        {"pacote": "experimento_top", "arquivo": "experimento_top.json", "registros": 1, "removidos": 0}
    )
    _tem(validar(e), "sem arquivo na carga completa")


def test_data_referencia_divergente_entre_pacotes():
    e = _copia()
    e.completa["mercado_dia"]["registros"][0]["data_referencia"] = "2026-10-21"
    _tem(validar(e), "data_referencia divergente")


def test_execucao_com_data_diferente_de_d():
    e = _copia()
    e.execucao["registros"][0]["data_referencia"] = "2026-10-22"
    _tem(validar(e), "esperado '2026-10-23'")


def test_execucao_com_status_talvez():
    e = _copia()
    e.execucao["registros"][0]["status"] = "talvez"
    _tem(validar(e), "status: 'talvez' fora de STATUS_EXECUCAO")


@pytest.mark.parametrize("rodada", [1.5, "1", None])
def test_rodada_nao_inteira(rodada):
    e = _copia()
    e.execucao["registros"][0]["rodada"] = rodada
    _tem(validar(e), "não é inteiro")


def test_experimento_top_com_calibracao_passa():
    e = _copia()
    env = e.execucao_completa
    e.completa["experimento_top"] = {
        "pacote": "experimento_top",
        "versao_contrato": "1.0",
        "chave": ["data_referencia"],
        "gerado_em": env["gerado_em"],
        "dados_ate": env["dados_ate"],
        "registros": [{
            "versao_contrato": "1.0",
            "gerado_em": env["gerado_em"],
            "dados_ate": env["dados_ate"],
            "data_referencia": "2026-10-23",
            "chance": {"valor": None, "status": "calibracao_nao_conferida"},
        }],
        "removidos": [],
    }
    env["registros"][0]["pacotes_enviados"].append(
        {"pacote": "experimento_top", "arquivo": "experimento_top.json", "registros": 1, "removidos": 0}
    )
    assert validar(e) == []


def test_limita_a_200_mensagens():
    e = _copia()
    ruim = copy.deepcopy(e.execucao["registros"][0])
    ruim["status"] = "talvez"
    e.execucao["registros"] = [copy.deepcopy(ruim) for _ in range(300)]
    assert len(validar(e)) == 200


def test_nao_para_no_primeiro_erro():
    e = _copia()
    e.execucao["registros"][0]["status"] = "talvez"
    e.completa["acoes"]["versao_contrato"] = "2.0"
    erros = validar(e)
    assert len(erros) >= 2
