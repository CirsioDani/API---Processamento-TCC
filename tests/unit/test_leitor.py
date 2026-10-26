"""Testes do leitor: lê as pastas de dados de teste, confere hashes, completa e erros."""
import hashlib
from pathlib import Path

import pytest

from publicador import leitor
from publicador.leitor import ErroEntrada

DADOS = Path(__file__).parents[1] / "dados"
PACOTES_COMPLETA = {
    "acoes", "mercado_dia", "resumo_acao", "series_diarias",
    "fundamentos_trimestrais", "proventos", "noticias_dia",
}


def test_ler_com_completa_2026_10_23():
    e = leitor.ler(DADOS, "2026-10-23")
    assert e.data == "2026-10-23"
    assert e.completa is not None
    assert set(e.completa) == PACOTES_COMPLETA
    assert e.execucao_completa is not None
    assert e.execucao["registros"][0]["data_referencia"] == "2026-10-23"
    assert e.execucao_completa["registros"][0]["data_referencia"] == "2026-10-23"


def test_hashes_com_chaves_relativas_e_sha256_dos_bytes():
    e = leitor.ler(DADOS, "2026-10-23")
    esperadas = {"saida/2026-10-23/execucao.json"}
    esperadas |= {f"saida_completa/2026-10-23/{p}.json" for p in PACOTES_COMPLETA | {"execucao"}}
    assert set(e.hashes_arquivos) == esperadas
    assert len(e.hashes_arquivos) == 9
    for chave, valor in e.hashes_arquivos.items():
        assert "\\" not in chave
        bruto = (DADOS / chave).read_bytes()
        assert valor == hashlib.sha256(bruto).hexdigest()


def test_pastas_sao_absolutas():
    e = leitor.ler(DADOS, "2026-10-23")
    assert Path(e.pasta_saida).is_absolute()
    assert Path(e.pasta_completa).is_absolute()
    assert Path(e.pasta_saida) == (DADOS / "saida" / "2026-10-23").resolve()


def test_ler_sem_completa_2026_10_27():
    e = leitor.ler(DADOS, "2026-10-27")
    assert e.completa is None
    assert e.execucao_completa is None
    assert e.pasta_completa is None
    assert e.execucao["registros"][0]["status"] == "falhou"
    assert set(e.hashes_arquivos) == {"saida/2026-10-27/execucao.json"}


def test_ler_2026_10_26_tem_completa():
    e = leitor.ler(DADOS, "2026-10-26")
    assert set(e.completa) == PACOTES_COMPLETA


def test_data_sem_pasta_nada_para_publicar():
    with pytest.raises(ErroEntrada, match="nada para publicar"):
        leitor.ler(DADOS, "2026-10-30")


@pytest.mark.parametrize("data", ["23/10/2026", "2026-13-01", "2026-02-30", "20261023", ""])
def test_data_malformada(data):
    with pytest.raises(ErroEntrada):
        leitor.ler(DADOS, data)
