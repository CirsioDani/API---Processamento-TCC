"""Linha de comando do publicador, sem banco: uso, validação de data e erro de configuração."""
import pytest

from comum import banco, config
from publicador import __main__ as cli


def _nao_pode_conectar(*args, **kwargs):
    raise AssertionError("não deveria chegar à conexão")


def test_sem_argumentos_sai_com_2():
    with pytest.raises(SystemExit) as erro:
        cli.main([])
    assert erro.value.code == 2


def test_data_e_conferir_juntos_saem_com_2(monkeypatch):
    monkeypatch.setattr(config, "carregar", _nao_pode_conectar)
    with pytest.raises(SystemExit) as erro:
        cli.main(["--data", "2026-10-23", "--conferir"])
    assert erro.value.code == 2


def test_forcar_sem_data_sai_com_2(monkeypatch):
    monkeypatch.setattr(config, "carregar", _nao_pode_conectar)
    with pytest.raises(SystemExit) as erro:
        cli.main(["--conferir", "--forcar"])
    assert erro.value.code == 2


def test_data_invalida_sai_com_2_antes_de_qualquer_conexao(monkeypatch):
    chamadas = []
    monkeypatch.setattr(config, "carregar", lambda *a, **k: chamadas.append("carregar"))
    monkeypatch.setattr(banco, "conectar", lambda *a, **k: chamadas.append("conectar"))
    with pytest.raises(SystemExit) as erro:
        cli.main(["--data", "2026-13-01"])
    assert erro.value.code == 2
    assert chamadas == []


def test_data_com_formato_errado_sai_com_2(monkeypatch):
    monkeypatch.setattr(config, "carregar", _nao_pode_conectar)
    with pytest.raises(SystemExit) as erro:
        cli.main(["--data", "23/10/2026"])
    assert erro.value.code == 2


def test_erro_de_configuracao_sai_com_2_e_nao_mostra_senha(monkeypatch, capsys):
    def carregar_falhando(*args, **kwargs):
        raise config.ErroConfig("IP_BANCO não definido")

    monkeypatch.setattr(config, "carregar", carregar_falhando)
    assert cli.main(["--conferir"]) == 2
    saida = capsys.readouterr()
    texto = (saida.out + saida.err).lower()
    assert "ip_banco não definido" in texto
    assert "senha" not in texto
