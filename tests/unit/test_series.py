"""Testes de comum/series.py: ida e volta do formato compacto, hash, recorte e montagem do registro."""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from comum import series
from comum.series import Ponto

ARQUIVO_REAL = Path(__file__).resolve().parents[1] / "dados" / "saida_completa" / "2026-10-23" / "series_diarias.json"

META = {"versao_contrato": "1.0", "gerado_em": "2026-10-23T10:00:00", "dados_ate": "2026-10-23"}
CATALOGO = {"ticker": "ABEV3", "tipo": "acao", "nome": "Ambev", "campos": ["fechamento", "pl"]}


def _datas(n: int) -> list[str]:
    inicio = date(2025, 1, 1)
    return [(inicio + timedelta(days=i)).isoformat() for i in range(n)]


def _pontos(n: int) -> list[Ponto]:
    return [
        Ponto(data=d, valores={"fechamento": float(i), "pl": float(i) + 0.5}, status={})
        for i, d in enumerate(_datas(n))
    ]


def _serie_sintetica() -> dict:
    return {
        "datas": ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"],
        "valores": {
            "fechamento": [10.0, 10.5, 10.2, 10.8, 11.0, 11.3],
            "pl": [None, None, -3.5, 2.0, None, 4.1],
        },
        "status": {
            "pl": [
                {"de": "2026-01-02", "ate": "2026-01-05", "status": "indisponivel"},
                {"de": "2026-01-06", "ate": "2026-01-06", "status": "lucro_negativo"},
                {"de": "2026-01-08", "ate": "2026-01-08", "status": "indisponivel"},
            ]
        },
    }


def _ida_e_volta(serie: dict) -> tuple[list[str], dict, dict]:
    return series.pontos_para_compacto(series.compacto_para_pontos(serie), list(serie["valores"].keys()))


def test_ida_e_volta_serie_sintetica():
    serie = _serie_sintetica()
    datas, valores, status = _ida_e_volta(serie)
    assert datas == serie["datas"]
    assert valores == serie["valores"]
    assert status == serie["status"]


def test_ida_e_volta_series_reais():
    if not ARQUIVO_REAL.exists():
        pytest.skip(f"arquivo real ainda não existe: {ARQUIVO_REAL}")
    with open(ARQUIVO_REAL, encoding="utf-8") as f:
        doc = json.load(f)
    registros = doc["registros"] if isinstance(doc, dict) else doc
    assert registros, "arquivo real sem séries"
    for serie in registros:
        datas, valores, status = _ida_e_volta(serie)
        assert datas == serie["datas"], serie["ticker"]
        assert valores == serie["valores"], serie["ticker"]
        assert status == serie["status"], serie["ticker"]


def test_hash_ponto_igual_para_pontos_iguais_e_independe_da_ordem_das_chaves():
    a = Ponto("2026-01-02", {"fechamento": 1.0, "pl": None}, {"pl": "indisponivel"})
    b = Ponto("2026-01-02", {"pl": None, "fechamento": 1.0}, {"pl": "indisponivel"})
    assert series.hash_ponto(a) == series.hash_ponto(b)


def test_hash_ponto_muda_com_valor_ou_status():
    base = Ponto("2026-01-02", {"fechamento": 1.0, "pl": None}, {"pl": "indisponivel"})
    valor_mudou = Ponto("2026-01-02", {"fechamento": 1.5, "pl": None}, {"pl": "indisponivel"})
    status_mudou = Ponto("2026-01-02", {"fechamento": 1.0, "pl": None}, {"pl": "historico_insuficiente"})
    assert series.hash_ponto(base) != series.hash_ponto(valor_mudou)
    assert series.hash_ponto(base) != series.hash_ponto(status_mudou)


def test_recortar_janelas():
    pontos = _pontos(300)
    assert len(series.recortar(pontos, "1m")) == 21
    assert series.recortar(pontos, "1m") == pontos[-21:]
    assert series.recortar(pontos, "1a") == pontos[-253:]
    assert series.recortar(pontos, "max") == pontos


def test_recortar_janela_invalida():
    with pytest.raises(ValueError):
        series.recortar(_pontos(10), "7x")


def test_montar_registro_janela_1a_sem_inicio_ultima_completa():
    pontos = _pontos(300)
    reg = series.montar_registro(META, CATALOGO, pontos, "1a")
    assert reg["inicio_janela"] == pontos[47].data
    assert "inicio_ultima_completa" not in reg
    assert len(reg["datas"]) == 253


def test_montar_registro_max_tem_inicio_ultima_completa():
    pontos = _pontos(300)
    reg = series.montar_registro(META, CATALOGO, pontos, "max")
    assert reg["inicio_ultima_completa"] == pontos[47].data
    assert reg["inicio_janela"] == pontos[0].data
    assert len(reg["datas"]) == 300


def test_montar_registro_2a_tem_inicio_ultima_completa():
    pontos = _pontos(300)
    reg = series.montar_registro(META, CATALOGO, pontos, "2a")
    assert reg["inicio_ultima_completa"] == pontos[47].data


def test_montar_registro_recusa_so_no_campo_e_ate_a_data():
    pontos = _pontos(300)
    reg = series.montar_registro(META, CATALOGO, pontos, "max", recusas={"fechamento": pontos[10].data})
    assert reg["valores"]["fechamento"][0:11] == [None] * 11
    assert reg["valores"]["fechamento"][11] == pontos[11].valores["fechamento"]
    assert reg["status"]["fechamento"] == [
        {"de": pontos[0].data, "ate": pontos[10].data, "status": "indisponivel"}
    ]
    assert reg["valores"]["pl"] == [p.valores["pl"] for p in pontos]
    assert "pl" not in reg["status"]


def test_montar_registro_metadados_e_flags():
    pontos = _pontos(300)
    reg = series.montar_registro(META, CATALOGO, pontos, "1a")
    assert reg["substituir_serie"] is True
    assert reg["versao_contrato"] == META["versao_contrato"]
    assert reg["gerado_em"] == META["gerado_em"]
    assert reg["dados_ate"] == META["dados_ate"]
    assert reg["ticker"] == "ABEV3"
    assert reg["tipo"] == "acao"
    assert reg["nome"] == "Ambev"


def test_montar_registro_lista_vazia_e_janela_invalida():
    with pytest.raises(ValueError):
        series.montar_registro(META, CATALOGO, [], "1a")
    with pytest.raises(ValueError):
        series.montar_registro(META, CATALOGO, _pontos(10), "7x")
