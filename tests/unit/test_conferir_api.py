"""Testes do ferramentas/conferir_api.py com o repositório falso de test_api (sem banco, sem rede).

A função real de HTTP só é exercitada contra uma porta fechada (conexão recusada), sem sair da máquina.
"""
import json
from pathlib import Path

from comum import contrato
from ferramentas import conferir_api as cf
from tests.unit.test_api import CHAVE, PUB, REF, criar_cliente, criar_repositorio

DADOS = Path(__file__).resolve().parents[1] / "dados"
DATA = PUB  # pasta 2026-10-23 (pregão 2026-10-22)
PACOTES_REMONTADOS = (
    "acoes", "resumo_acao", "noticias_dia", "series_diarias",
    "fundamentos_trimestrais", "proventos", "mercado_dia", "execucao",
)


def _buscar_do(cliente):
    """buscar(caminho, params) -> (status, json) em cima do TestClient da API falsa."""
    def buscar(caminho, params):
        resposta = cliente.get(caminho, params=params, headers=CHAVE)
        return resposta.status_code, resposta.json()
    return buscar


def _repo_como_o_publicador():
    """Repositório falso igual ao que o publicador grava: a execução vem de saida/ (entrada.execucao).

    O criar_repositorio de test_api carrega a execução de saida_completa, que tem outro conteúdo; aqui
    alinha-se à fonte da publicação para a conferência dar OK no dia de teste.
    """
    repo = criar_repositorio()
    execucao = json.loads((DADOS / "saida" / DATA / "execucao.json").read_text(encoding="utf-8"))
    repo.linhas_execucao = [{"registro": r, "data_publicacao": PUB} for r in execucao["registros"]]
    return repo


def _conferir_repo(repo, capsys, remontar=None):
    codigo = cf.conferir(_buscar_do(criar_cliente(repo)), DADOS, DATA, remontar)
    return codigo, capsys.readouterr().out


def _numeros_da_linha(saida: str, rotulo: str) -> list[int]:
    """Colunas numéricas da linha da tabela: arquivo, API, iguais, diferentes, faltando, a mais."""
    for linha in saida.splitlines():
        if linha.startswith(rotulo + " "):
            return [int(valor) for valor in linha[len(rotulo):].split()]
    raise AssertionError(f"linha {rotulo!r} não encontrada na saída")


def _arquivo_original(pacote: str) -> dict:
    pasta = "saida" if pacote == "execucao" else "saida_completa"
    return json.loads((DADOS / pasta / DATA / f"{pacote}.json").read_text(encoding="utf-8"))


def _sem_metadados(registro: dict) -> dict:
    return {k: v for k, v in registro.items() if k not in contrato.METADADOS}


def _ordenados(pacote: str, registros: list) -> list:
    return sorted(registros, key=lambda r: contrato.chave_de(pacote, r))


# ---------------------------------------------------------------- conferência

def test_publicacao_igual_ao_arquivo_da_ok(capsys):
    codigo, saida = _conferir_repo(_repo_como_o_publicador(), capsys)
    assert codigo == 0, saida
    assert "CONFERÊNCIA OK" in saida


def test_valor_alterado_da_1_e_cita_ticker_e_campo(capsys):
    repo = _repo_como_o_publicador()
    for linha in repo.fotos["resumo_acao"]:
        if linha["ticker"] == "PETR4":
            assert isinstance(linha["registro"]["pl"]["valor"], float)
            linha["registro"]["pl"]["valor"] += 0.01
    codigo, saida = _conferir_repo(repo, capsys)
    assert codigo == 1
    assert "resumo_acao[2026-10-22, PETR4].pl.valor" in saida
    assert _numeros_da_linha(saida, "resumo_acao (lista)")[3] == 1  # diferentes
    assert "CONFERÊNCIA COM DIFERENÇAS" in saida


def test_registro_removido_da_faltando(capsys):
    repo = _repo_como_o_publicador()
    repo.fotos["resumo_acao"] = [l for l in repo.fotos["resumo_acao"] if l["ticker"] != "PETR4"]
    codigo, saida = _conferir_repo(repo, capsys)
    assert codigo == 1
    assert _numeros_da_linha(saida, "resumo_acao (lista)")[4] == 1  # faltando
    assert "faltando: resumo_acao[2026-10-22, PETR4]" in saida


def test_registro_a_mais_na_api(capsys):
    repo = _repo_como_o_publicador()
    extra = json.loads(json.dumps(
        next(l["registro"] for l in repo.fotos["resumo_acao"] if l["ticker"] == "PETR4")))
    extra["ticker"] = "XPTO4"
    repo.fotos["resumo_acao"].append({"ticker": "XPTO4", "registro": extra,
                                      "data_publicacao": PUB, "data_referencia": REF})
    codigo, saida = _conferir_repo(repo, capsys)
    assert codigo == 1
    assert _numeros_da_linha(saida, "resumo_acao (lista)")[5] == 1  # a mais
    assert "a mais: resumo_acao[2026-10-22, XPTO4]" in saida


def test_metadados_de_versionado_nao_contam(capsys):
    repo = _repo_como_o_publicador()
    for linha in repo.versionadas["acoes"]:
        linha["registro"]["gerado_em"] = "2030-01-01T00:00:00-03:00"
    codigo, saida = _conferir_repo(repo, capsys)
    assert codigo == 0, saida


def test_todas_as_chamadas_levam_data_e_janela(capsys):
    cliente = criar_cliente(_repo_como_o_publicador())
    base = _buscar_do(cliente)
    chamadas = []

    def espiao(caminho, params):
        chamadas.append((caminho, params))
        return base(caminho, params)

    assert cf.conferir(espiao, DADOS, DATA) == 0
    assert chamadas and all(params["data"] == DATA for _, params in chamadas)
    series = [(c, p) for c, p in chamadas if c.startswith("/v1/series_diarias/")]
    assert series and all(p == {"data": DATA, "janela": "1a"} for _, p in series)
    assert any(c == "/v1/series_diarias/SETOR_SEM%3APETR4" for c, _ in series)


def test_pasta_da_publicacao_inexistente_da_2(capsys):
    cliente = criar_cliente(_repo_como_o_publicador())
    assert cf.conferir(_buscar_do(cliente), DADOS, "2001-01-01") == 2
    assert "pasta da publicação não encontrada" in capsys.readouterr().out


# ---------------------------------------------------------------- remontagem

def test_remontar_grava_os_oito_pacotes_iguais_aos_arquivos(tmp_path, capsys):
    destino = tmp_path / "remontada"
    codigo, saida = _conferir_repo(_repo_como_o_publicador(), capsys, remontar=str(destino))
    assert codigo == 0, saida
    assert "remontado: 8 arquivo(s)" in saida

    for pacote in PACOTES_REMONTADOS:
        caminho = destino / f"{pacote}.json"
        assert caminho.is_file(), pacote
        gerado = json.loads(caminho.read_text(encoding="utf-8"))
        original = _arquivo_original(pacote)
        assert set(gerado) == set(contrato.CAMPOS_ENVELOPE), pacote
        assert gerado["pacote"] == original["pacote"] == pacote
        assert gerado["versao_contrato"] == original["versao_contrato"]
        assert gerado["chave"] == original["chave"]
        assert gerado["removidos"] == []

        regs_gerados, regs_originais = gerado["registros"], original["registros"]
        if pacote in contrato.VERSIONADOS:
            regs_gerados = [_sem_metadados(r) for r in regs_gerados]
            regs_originais = [_sem_metadados(r) for r in regs_originais]
        if pacote != "series_diarias":  # séries seguem a ordem do arquivo
            regs_gerados = _ordenados(pacote, regs_gerados)
            regs_originais = _ordenados(pacote, regs_originais)
        assert regs_gerados == regs_originais, pacote


def test_remontar_formato_de_indentacao(tmp_path, capsys):
    destino = tmp_path / "remontada"
    _conferir_repo(_repo_como_o_publicador(), capsys, remontar=str(destino))
    series = (destino / "series_diarias.json").read_text(encoding="utf-8")
    assert "\n " not in series  # sem indentação
    acoes = (destino / "acoes.json").read_text(encoding="utf-8")
    assert '\n "pacote"' in acoes  # indent=1


# ---------------------------------------------------------------- API fora do ar / chave

def test_api_fora_do_ar_da_2(capsys):
    buscar = cf.criar_buscar("http://127.0.0.1:9", "chave-qualquer", timeout=2)
    assert cf.conferir(buscar, DADOS, DATA) == 2
    assert "API não está respondendo em http://127.0.0.1:9" in capsys.readouterr().out


def test_chave_recusada_da_2(capsys):
    def buscar(caminho, params):
        return 401, {"detail": "chave de API ausente ou inválida"}

    assert cf.conferir(buscar, DADOS, DATA) == 2
    assert "/v1/acoes respondeu HTTP 401" in capsys.readouterr().out
