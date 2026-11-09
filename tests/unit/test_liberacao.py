"""Liberação das recusas SETOR_SEM: lógica pura, sem banco."""
from datetime import date, timedelta

import pytest

from comum import series
from comum.series import Ponto
from publicador.liberacao import ErroLiberacao, Recusa, planejar
from publicador.operacoes import PontoAtualizar

TICKER = "SETOR_SEM:PETR4"
CAMPO = "indice_retorno_total"
TIPOS = {TICKER: "setor_sem_empresa"}
AGORA = "2026-11-10T09:00:00"
MOTIVO = "teste de liberação"
DATAS = [(date(2026, 10, 1) + timedelta(days=i)).isoformat() for i in range(15)]


def _pontos_caso() -> dict[str, dict[str, Ponto]]:
    """Antigos 0..9 com índice 100+2i; janela 10..14 já no nível novo, (100+2i) × 1.26."""
    pontos = {}
    for i in range(10):
        pontos[DATAS[i]] = Ponto(DATAS[i], {CAMPO: 100.0 + 2 * i, "n_empresas": 7}, {})
    for i in range(10, 15):
        pontos[DATAS[i]] = Ponto(DATAS[i], {CAMPO: (100.0 + 2 * i) * 1.26, "n_empresas": 7}, {})
    return {TICKER: pontos}


def _recusa(**mudancas) -> Recusa:
    base = dict(
        id=1, ticker=TICKER, campo=CAMPO, data_publicacao="2026-11-09", fator=1.26, de=DATAS[0], ate=DATAS[9],
        pontos=10, detalhe={"antigos": {"de": DATAS[0], "ate": DATAS[9], "pontos": 10}, "motivo": "recusa original"},
    )
    base.update(mudancas)
    return Recusa(**base)


def test_dez_pontos_antigos_sao_encadeados_pelo_fator():
    pontos = _pontos_caso()
    originais = dict(pontos[TICKER])
    libs, ignoradas = planejar([_recusa()], TIPOS, pontos, MOTIVO, AGORA)

    assert ignoradas == []
    (lib,) = libs
    assert len(lib.operacoes) == 10
    for i, op in enumerate(lib.operacoes):
        assert isinstance(op, PontoAtualizar)
        assert op.ticker == TICKER
        assert op.ponto.data == DATAS[i]
        assert op.ponto.valores[CAMPO] == pytest.approx((100.0 + 2 * i) * 1.26)
        assert op.ponto.valores["n_empresas"] == 7
        assert op.anterior == originais[DATAS[i]]
        assert op.hash == series.hash_ponto(op.ponto)
        assert op.hash_anterior == series.hash_ponto(op.anterior)
        assert op.ruido is False
    assert pontos[TICKER] == originais  # a entrada não é alterada


def test_costura_antes_e_depois_da_liberacao():
    (lib,) = planejar([_recusa()], TIPOS, _pontos_caso(), MOTIVO, AGORA)[0]
    assert lib.costura_antes == pytest.approx(118 / (120 * 1.26))
    assert lib.costura_depois == pytest.approx(118 / 120)
    assert lib.alerta is False


def test_costura_none_sem_ponto_depois_de_ate():
    pontos = _pontos_caso()
    pontos[TICKER] = {d: p for d, p in pontos[TICKER].items() if d <= DATAS[9]}
    (lib,) = planejar([_recusa()], TIPOS, pontos, MOTIVO, AGORA)[0]
    assert lib.costura_antes is None and lib.costura_depois is None


def test_detalhe_novo_preserva_o_original_e_registra_a_liberacao():
    (lib,) = planejar([_recusa()], TIPOS, _pontos_caso(), MOTIVO, AGORA)[0]
    detalhe = lib.detalhe_novo
    assert detalhe["antigos"] == {"de": DATAS[0], "ate": DATAS[9], "pontos": 10}
    assert detalhe["motivo"] == "recusa original"
    liberacao = detalhe["liberacao"]
    assert liberacao["classificacao_anterior"] == "recusado"
    assert liberacao["aplicado_anterior"] is False
    assert liberacao["fator_aplicado"] == 1.26
    assert liberacao["pontos_emendados"] == 10
    assert liberacao["em"] == AGORA
    assert liberacao["motivo"] == MOTIVO
    assert "substituição inteira" in liberacao["regra"]


@pytest.mark.parametrize("tipo", ["acao", "indice"])
def test_tipo_fora_da_lista_e_ignorado(tipo):
    libs, ignoradas = planejar([_recusa()], {TICKER: tipo}, _pontos_caso(), MOTIVO, AGORA)
    assert libs == []
    assert len(ignoradas) == 1
    assert ignoradas[0][0].id == 1
    assert "não é liberável" in ignoradas[0][1]


def test_campo_que_nao_e_proporcional_e_ignorado():
    libs, ignoradas = planejar([_recusa(campo="fechamento")], TIPOS, _pontos_caso(), MOTIVO, AGORA)
    assert libs == []
    assert "fechamento" in ignoradas[0][1]


@pytest.mark.parametrize("fator", [None, 0.0, -1.0])
def test_fator_ausente_ou_nao_positivo_e_ignorado(fator):
    libs, ignoradas = planejar([_recusa(fator=fator)], TIPOS, _pontos_caso(), MOTIVO, AGORA)
    assert libs == []
    assert "fator" in ignoradas[0][1]


def test_sem_pontos_com_valor_no_periodo_e_ignorada():
    libs, ignoradas = planejar([_recusa()], TIPOS, {}, MOTIVO, AGORA)
    assert libs == []
    assert "nenhum ponto" in ignoradas[0][1]


def test_contagem_que_nao_bate_levanta_erro_com_ticker_e_contagens():
    with pytest.raises(ErroLiberacao, match=r"SETOR_SEM:PETR4.*registra 11 pontos.*há 10"):
        planejar([_recusa(pontos=11)], TIPOS, _pontos_caso(), MOTIVO, AGORA)


def test_ponto_sem_valor_no_meio_fica_fora_das_operacoes():
    pontos = _pontos_caso()
    pontos[TICKER][DATAS[4]] = Ponto(DATAS[4], {CAMPO: None, "n_empresas": 7}, {CAMPO: "indisponivel"})
    libs, _ = planejar([_recusa(pontos=9)], TIPOS, pontos, MOTIVO, AGORA)
    (lib,) = libs
    assert [op.ponto.data for op in lib.operacoes] == [d for i, d in enumerate(DATAS[:10]) if i != 4]
    assert len(lib.operacoes) == lib.detalhe_novo["liberacao"]["pontos_emendados"] == 9


@pytest.mark.parametrize("fator, alerta", [(2.5, True), (0.4, True), (2.0, False), (0.5, False), (1.26, False)])
def test_alerta_quando_fator_sai_de_05_a_20(fator, alerta):
    (lib,) = planejar([_recusa(fator=fator)], TIPOS, _pontos_caso(), MOTIVO, AGORA)[0]
    assert lib.alerta is alerta
