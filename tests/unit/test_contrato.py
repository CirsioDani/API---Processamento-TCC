"""Testes de comum/contrato.py: hash, chave e json canônico do contrato 1.0."""
import pytest

from comum import contrato


def _registro() -> dict:
    return {
        "versao_contrato": "1.0",
        "gerado_em": "2026-10-23T10:00:00",
        "dados_ate": "2026-10-23",
        "ticker": "ABEV3",
        "valores": [1, 2, None],
    }


def test_hash_registro_ignora_metadados():
    base = _registro()
    mudado = dict(base, gerado_em="2026-10-24T08:00:00", dados_ate="2026-10-24", versao_contrato="9.9")
    assert contrato.hash_registro(base) == contrato.hash_registro(mudado)


def test_hash_registro_muda_com_valor():
    base = _registro()
    mudado = dict(base, valores=[1, 2, 3])
    assert contrato.hash_registro(base) != contrato.hash_registro(mudado)


def test_hash_registro_independe_da_ordem_das_chaves():
    base = _registro()
    invertido = dict(reversed(list(base.items())))
    assert list(invertido) != list(base)
    assert contrato.hash_registro(base) == contrato.hash_registro(invertido)


def test_chave_de_resumo_acao():
    registro = {"data_referencia": "2026-10-23", "ticker": "ABEV3", "outro": 1}
    assert contrato.chave_de("resumo_acao", registro) == ("2026-10-23", "ABEV3")


def test_json_canonico_recusa_nan():
    with pytest.raises(ValueError):
        contrato.json_canonico({"x": float("nan")})


def test_pacotes_do_contrato():
    esperado = {
        "acoes": (("ticker",), "versionada"),
        "mercado_dia": (("data_referencia",), "foto"),
        "resumo_acao": (("data_referencia", "ticker"), "foto"),
        "series_diarias": (("ticker", "data"), "serie"),
        "fundamentos_trimestrais": (("ticker", "data_fim_periodo"), "versionada"),
        "proventos": (("ticker", "tipo", "data_com"), "versionada"),
        "noticias_dia": (("data_referencia", "ticker"), "foto"),
        "experimento_top": (("data_referencia",), "foto"),
        "experimento_historico": (("data_referencia",), "foto"),
        "execucao": (("data_referencia", "rodada"), "foto"),
    }
    assert set(contrato.PACOTES) == set(esperado)
    assert len(contrato.PACOTES) == 10
    for nome, (chave, familia) in esperado.items():
        assert contrato.PACOTES[nome].nome == nome
        assert contrato.PACOTES[nome].chave == chave
        assert contrato.PACOTES[nome].familia == familia
