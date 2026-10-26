"""Testes da API com repositório FALSO em memória (sem banco).

Os dados vêm de tests/dados/saida_completa/2026-10-23 (publicação 23/10, pregão 22/10).
"""
import json
import re
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import repositorio
from api.main import criar_app
from comum import banco, guarda_sql, series, tabelas
from comum.config import Config, Credenciais

PASTA = Path(__file__).resolve().parents[1] / "dados" / "saida_completa" / "2026-10-23"
PUB = "2026-10-23"
REF = "2026-10-22"
CHAVE = {"X-API-Key": "chave-teste"}
PACOTES_ARQUIVO = [
    "acoes", "fundamentos_trimestrais", "proventos", "resumo_acao", "noticias_dia",
    "mercado_dia", "execucao", "series_diarias",
]
ENVELOPE = {
    "pacote", "versao_contrato", "chave", "gerado_em", "dados_ate",
    "data_publicacao", "data_referencia", "registros", "removidos",
}


def _arquivo(pacote: str) -> dict:
    return json.loads((PASTA / f"{pacote}.json").read_text(encoding="utf-8"))


def _registro_arquivo(pacote: str, ticker: str) -> dict:
    return next(r for r in _arquivo(pacote)["registros"] if r["ticker"] == ticker)


def _caminho(registro, campo):
    atual = registro
    for parte in campo.split("."):
        if not isinstance(atual, dict) or parte not in atual:
            return None
        atual = atual[parte]
    return atual


def _caminhos_nulos(obj, caminho=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _caminhos_nulos(v, caminho + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _caminhos_nulos(v, caminho + (i,))
    elif obj is None:
        yield caminho


def _linha_foto(registro: dict, data_publicacao: str) -> dict:
    return {
        "ticker": registro.get("ticker"),
        "registro": registro,
        "data_publicacao": data_publicacao,
        "data_referencia": registro["data_referencia"],
    }


class RepositorioFalso:
    """Mesma interface de api.repositorio.Repositorio, com os dados em listas."""

    def __init__(self):
        self.publicacoes = []
        self.fotos = {p: [] for p in ("resumo_acao", "noticias_dia", "mercado_dia", "experimento_top",
                                      "experimento_historico")}
        self.linhas_execucao = []  # {registro, data_publicacao}
        self.versionadas = {p: [] for p in ("acoes", "fundamentos_trimestrais", "proventos")}
        self.catalogo = {}
        self.pontos = {}
        self.recusas = {}
        self.falha = None  # exceção levantada em toda consulta (teste de 503)

    def _checar(self):
        if self.falha is not None:
            raise self.falha

    def ping(self):
        self._checar()

    def ultima_publicacao(self):
        self._checar()
        return self._pub_ate(None)

    def publicacao_ate(self, data):
        self._checar()
        return self._pub_ate(data)

    def _pub_ate(self, data):
        candidatas = [
            p for p in self.publicacoes
            if p["status"] == "completa" and (data is None or p["data_publicacao"] <= data)
        ]
        if not candidatas:
            return None
        p = max(candidatas, key=lambda x: x["data_publicacao"])
        return {"data_publicacao": p["data_publicacao"], "data_referencia": p["data_referencia"],
                "metadados": p["metadados"]}

    def foto(self, pacote, data, ticker):
        self._checar()
        linhas = [l for l in self.fotos[pacote] if ticker is None or l["ticker"] == ticker]
        if data is not None:
            linhas = [l for l in linhas if l["data_publicacao"] <= data]
            chave = lambda l: (l["data_publicacao"], l["data_referencia"])  # noqa: E731
        else:
            chave = lambda l: l["data_referencia"]  # noqa: E731
        if not linhas:
            return None
        l = max(linhas, key=chave)
        return l["registro"], l["data_publicacao"], l["data_referencia"]

    def fotos_do_dia(self, pacote, data):
        self._checar()
        linhas = self.fotos[pacote]
        if data is not None:
            linhas = [l for l in linhas if l["data_publicacao"] <= data]
        if not linhas:
            return None
        alvo = max(l["data_referencia"] for l in linhas)
        do_pregao = [l for l in self.fotos[pacote] if l["data_referencia"] == alvo
                     and (data is None or l["data_publicacao"] <= data)]
        do_pregao.sort(key=lambda l: l["ticker"])
        registros = [l["registro"] for l in do_pregao]
        return registros, max(l["data_publicacao"] for l in do_pregao), alvo

    def versoes(self, pacote, data, ticker=None):
        self._checar()
        saida = []
        for l in self.versionadas[pacote]:
            if ticker is not None and l["ticker"] != ticker:
                continue
            if data is None:
                if l["valido_ate"] is not None:
                    continue
            elif not (l["valido_desde"] <= data and (l["valido_ate"] is None or l["valido_ate"] > data)):
                continue
            saida.append(l["registro"])
        return saida

    def ticker_existe(self, ticker):
        self._checar()
        return any(l["ticker"] == ticker for l in self.versionadas["acoes"])

    def serie(self, ticker, data_fim):
        self._checar()
        if ticker not in self.catalogo:
            return None
        pontos = [p for p in self.pontos[ticker] if p.data <= data_fim]
        return dict(self.catalogo[ticker]), pontos, dict(self.recusas.get(ticker, {}))

    def inicio_da_janela(self, data_fim, n):
        self._checar()
        datas = sorted({p.data for pontos in self.pontos.values() for p in pontos if p.data <= data_fim})
        if not datas:
            return None
        if n is None:
            return datas[0]
        return datas[-n] if len(datas) >= n else datas[0]

    def historico(self, ticker, campo, inicio=None, fim=None):
        self._checar()
        saida = []
        for l in self.fotos["resumo_acao"]:
            if l["ticker"] != ticker:
                continue
            d = l["data_referencia"]
            if inicio is not None and d < inicio:
                continue
            if fim is not None and d > fim:
                continue
            saida.append((d, _caminho(l["registro"], campo)))
        return sorted(saida, key=lambda x: x[0])

    def execucao_ultima(self):
        self._checar()
        if not self.linhas_execucao:
            return None
        e = max(self.linhas_execucao, key=lambda x: (x["registro"]["data_referencia"], x["registro"]["rodada"]))
        return e["registro"], e["data_publicacao"]

    def execucoes(self, data):
        self._checar()
        achados = [e for e in self.linhas_execucao if e["registro"]["data_referencia"] == data]
        return [(e["registro"], e["data_publicacao"]) for e in sorted(achados, key=lambda x: x["registro"]["rodada"])]


def _serie_curta() -> dict:
    """CURT3: série sintética que começa tarde, com só as 30 últimas datas de PETR4 (valores de PETR4)."""
    petr4 = _registro_arquivo("series_diarias", "PETR4")
    n = 30
    return {
        **petr4,
        "ticker": "CURT3",
        "nome": "Curta sintética",
        "inicio_janela": petr4["datas"][-n],
        "datas": petr4["datas"][-n:],
        "valores": {campo: valores[-n:] for campo, valores in petr4["valores"].items()},
        "status": {},
    }


def criar_repositorio() -> RepositorioFalso:
    repo = RepositorioFalso()
    metadados = {}
    for pacote in PACOTES_ARQUIVO:
        env = _arquivo(pacote)
        metadados[pacote] = {
            "versao_contrato": env["versao_contrato"],
            "chave": env["chave"],
            "gerado_em": env["gerado_em"],
            "dados_ate": env["dados_ate"],
        }
    metadados["series_diarias"]["inicio_janela"] = min(
        r["inicio_janela"] for r in _arquivo("series_diarias")["registros"])
    repo.publicacoes.append({"data_publicacao": PUB, "data_referencia": REF, "status": "completa",
                             "metadados": metadados})
    for pacote in ("resumo_acao", "noticias_dia", "mercado_dia"):
        for reg in _arquivo(pacote)["registros"]:
            repo.fotos[pacote].append(_linha_foto(reg, PUB))
    for reg in _arquivo("execucao")["registros"]:
        repo.linhas_execucao.append({"registro": reg, "data_publicacao": PUB})
    for pacote in ("acoes", "fundamentos_trimestrais", "proventos"):
        for reg in _arquivo(pacote)["registros"]:
            repo.versionadas[pacote].append({"ticker": reg["ticker"], "registro": reg,
                                             "valido_desde": PUB, "valido_ate": None})
    for reg in _arquivo("series_diarias")["registros"] + [_serie_curta()]:
        t = reg["ticker"]
        repo.catalogo[t] = {"ticker": t, "tipo": reg["tipo"], "nome": reg["nome"],
                            "campos": list(reg["valores"].keys())}
        repo.pontos[t] = series.compacto_para_pontos(reg)
    return repo


def criar_config(chaves=("chave-teste",)) -> Config:
    return Config(
        host="127.0.0.1",
        porta=3306,
        banco="tcc_processador",
        escrita=Credenciais(usuario="u", senha="s"),
        leitura=Credenciais(usuario="u", senha="s"),
        prefixo="",
        dir_processamento=Path("nao_usado"),
        chaves_api=tuple(chaves),
        cors_origens=("http://localhost:5173",),
    )


@contextmanager
def _entregar(repo):
    yield repo


def criar_cliente(repo, chaves=("chave-teste",)) -> TestClient:
    app = criar_app(config=criar_config(chaves), fabrica_repositorio=lambda cfg: _entregar(repo))
    return TestClient(app)


@pytest.fixture
def repo() -> RepositorioFalso:
    return criar_repositorio()


@pytest.fixture
def cliente(repo) -> TestClient:
    return criar_cliente(repo)


# ---------------------------------------------------------------- chave de API

def test_sem_chave_da_401(cliente):
    r = cliente.get("/v1/acoes")
    assert r.status_code == 401
    assert r.json() == {"detail": "chave de API ausente ou inválida"}


def test_chave_errada_da_401(cliente):
    r = cliente.get("/v1/acoes", headers={"X-API-Key": "errada"})
    assert r.status_code == 401


def test_sem_chaves_configuradas_da_401(repo):
    cliente = criar_cliente(repo, chaves=())
    r = cliente.get("/v1/acoes", headers={"X-API-Key": "qualquer"})
    assert r.status_code == 401


# ---------------------------------------------------------------- saúde

def test_saude_sem_chave(cliente):
    r = cliente.get("/v1/saude")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "banco": "ok", "ultima_publicacao": PUB}


def test_saude_com_banco_fora_da_503(repo, cliente):
    repo.falha = banco.ErroBanco("falha")
    r = cliente.get("/v1/saude")
    assert r.status_code == 503
    assert r.json() == {"status": "erro", "banco": "fora"}


def test_erro_de_banco_da_503(repo, cliente):
    repo.falha = banco.ErroBanco("falha")
    r = cliente.get("/v1/acoes", headers=CHAVE)
    assert r.status_code == 503
    assert r.json() == {"detail": "banco indisponível"}


# ---------------------------------------------------------------- fotos

def test_resumo_acao_igual_ao_arquivo(cliente):
    r = cliente.get("/v1/resumo_acao/PETR4", headers=CHAVE)
    assert r.status_code == 200
    corpo = r.json()
    assert set(corpo) == ENVELOPE
    assert corpo["pacote"] == "resumo_acao"
    assert corpo["chave"] == ["data_referencia", "ticker"]
    assert corpo["data_publicacao"] == PUB
    assert corpo["data_referencia"] == REF
    assert corpo["removidos"] == []
    assert corpo["registros"][0] == _registro_arquivo("resumo_acao", "PETR4")


def test_ticker_em_minusculas(cliente):
    r = cliente.get("/v1/resumo_acao/petr4", headers=CHAVE)
    assert r.status_code == 200
    assert r.json()["registros"][0] == _registro_arquivo("resumo_acao", "PETR4")


def test_null_do_arquivo_continua_null(cliente):
    arquivo = _registro_arquivo("resumo_acao", "VALE3")
    nulos = list(_caminhos_nulos(arquivo))
    assert nulos, "o registro de VALE3 deveria ter ao menos um null"
    registro = cliente.get("/v1/resumo_acao/VALE3", headers=CHAVE).json()["registros"][0]
    for caminho in nulos:
        atual = registro
        for parte in caminho:
            atual = atual[parte]
        assert atual is None


def test_ticker_inexistente_404(cliente):
    r = cliente.get("/v1/resumo_acao/XPTO4", headers=CHAVE)
    assert r.status_code == 404
    assert r.json() == {"detail": "ticker XPTO4 não encontrado"}


def test_experimento_top_sem_dados_404(cliente):
    r = cliente.get("/v1/experimento_top", headers=CHAVE)
    assert r.status_code == 404
    assert r.json() == {"detail": "pacote experimento_top ainda não publicado"}


def _ordenados_por_ticker(pacote: str) -> list:
    return sorted(_arquivo(pacote)["registros"], key=lambda r: r["ticker"])


@pytest.mark.parametrize("pacote", ["resumo_acao", "noticias_dia"])
def test_do_dia_tres_registros_iguais_ao_arquivo(cliente, pacote):
    r = cliente.get(f"/v1/{pacote}", headers=CHAVE)
    assert r.status_code == 200
    corpo = r.json()
    assert set(corpo) == ENVELOPE
    assert corpo["pacote"] == pacote
    assert corpo["chave"] == ["data_referencia", "ticker"]
    assert len(corpo["registros"]) == 3
    assert corpo["registros"] == _ordenados_por_ticker(pacote)
    assert corpo["data_publicacao"] == PUB
    assert corpo["data_referencia"] == REF
    assert corpo["removidos"] == []


@pytest.mark.parametrize("pacote", ["resumo_acao", "noticias_dia"])
def test_do_dia_com_data_de_publicacao(cliente, pacote):
    corpo = cliente.get(f"/v1/{pacote}", params={"data": PUB}, headers=CHAVE).json()
    assert corpo["registros"] == _ordenados_por_ticker(pacote)
    assert corpo["data_publicacao"] == PUB
    assert corpo["data_referencia"] == REF


@pytest.mark.parametrize("pacote", ["resumo_acao", "noticias_dia"])
def test_do_dia_sem_chave_401(cliente, pacote):
    assert cliente.get(f"/v1/{pacote}").status_code == 401


@pytest.mark.parametrize("pacote", ["resumo_acao", "noticias_dia"])
def test_do_dia_data_anterior_a_tudo_404(cliente, pacote):
    r = cliente.get(f"/v1/{pacote}", params={"data": "2026-10-01"}, headers=CHAVE)
    assert r.status_code == 404
    assert r.json() == {"detail": "nada publicado até 2026-10-01"}


@pytest.mark.parametrize("pacote", ["resumo_acao", "noticias_dia"])
def test_do_dia_sem_nada_publicado_404(repo, cliente, pacote):
    repo.fotos[pacote].clear()
    r = cliente.get(f"/v1/{pacote}", headers=CHAVE)
    assert r.status_code == 404
    assert r.json() == {"detail": "nada publicado ainda"}


def test_resumo_do_dia_pega_o_pregao_mais_recente_e_respeita_data(repo, cliente):
    base = _registro_arquivo("resumo_acao", "PETR4")
    novo = json.loads(json.dumps(base))
    novo["data_referencia"] = "2026-10-23"
    repo.fotos["resumo_acao"].append(_linha_foto(novo, "2026-10-24"))
    corpo = cliente.get("/v1/resumo_acao", headers=CHAVE).json()
    assert corpo["registros"] == [novo]
    assert corpo["data_publicacao"] == "2026-10-24"
    assert corpo["data_referencia"] == "2026-10-23"
    antigo = cliente.get("/v1/resumo_acao", params={"data": PUB}, headers=CHAVE).json()
    assert antigo["registros"] == _ordenados_por_ticker("resumo_acao")
    assert antigo["data_referencia"] == REF


def _registro_experimento() -> dict:
    return {
        "versao_contrato": "1.0",
        "gerado_em": "2026-10-23T09:12:00-03:00",
        "dados_ate": "2026-10-23T08:00:00-03:00",
        "data_referencia": PUB,
        "ranking": [{"posicao": 1, "ticker": "PETR4"}],
    }


def test_experimento_sem_dados_404(cliente):
    r = cliente.get("/v1/experimento/top", headers=CHAVE)
    assert r.status_code == 404
    assert r.json() == {"detail": "pacote experimento_top ainda não publicado"}
    r = cliente.get("/v1/experimento/historico", headers=CHAVE)
    assert r.status_code == 404
    assert r.json() == {"detail": "pacote experimento_historico ainda não publicado"}


@pytest.mark.parametrize("pacote,caminho", [
    ("experimento_top", "/v1/experimento/top"),
    ("experimento_historico", "/v1/experimento/historico"),
])
def test_experimento_com_registro_200(repo, cliente, pacote, caminho):
    reg = _registro_experimento()
    repo.fotos[pacote].append(_linha_foto(reg, PUB))
    corpo = cliente.get(caminho, headers=CHAVE).json()
    assert corpo["pacote"] == pacote
    assert corpo["chave"] == ["data_referencia"]
    assert corpo["registros"] == [reg]
    assert corpo["data_publicacao"] == PUB
    assert corpo["data_referencia"] == PUB
    assert cliente.get(caminho, params={"data": PUB}, headers=CHAVE).status_code == 200
    assert cliente.get(caminho, params={"data": "2026-10-01"}, headers=CHAVE).status_code == 404
    assert cliente.get(caminho).status_code == 401


def test_experimento_top_antigo_continua_funcionando(repo, cliente):
    reg = _registro_experimento()
    repo.fotos["experimento_top"].append(_linha_foto(reg, PUB))
    r = cliente.get("/v1/experimento_top", headers=CHAVE)
    assert r.status_code == 200
    assert r.json()["registros"] == [reg]
    assert cliente.get("/v1/experimento_top").status_code == 401


def test_experimento_top_antigo_fica_fora_do_openapi(cliente):
    caminhos = cliente.get("/openapi.json").json()["paths"]
    assert "/v1/experimento_top" not in caminhos
    assert {"/v1/experimento/top", "/v1/experimento/historico"} <= set(caminhos)


def test_mercado_dia_igual_ao_arquivo(cliente):
    corpo = cliente.get("/v1/mercado_dia", headers=CHAVE).json()
    assert corpo["registros"] == _arquivo("mercado_dia")["registros"]
    assert corpo["data_publicacao"] == PUB
    assert corpo["data_referencia"] == REF


def test_data_sem_publicacao_404(cliente):
    r = cliente.get("/v1/acoes?data=2026-10-01", headers=CHAVE)
    assert r.status_code == 404
    assert r.json() == {"detail": "nada publicado até 2026-10-01"}


def test_data_com_publicacao_200(cliente):
    corpo = cliente.get(f"/v1/acoes?data={PUB}", headers=CHAVE).json()
    assert corpo["data_publicacao"] == PUB


# ---------------------------------------------------------------- versionadas

def test_acoes_tres_registros_iguais_ao_arquivo(cliente):
    r = cliente.get("/v1/acoes", headers=CHAVE)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["registros"] == _arquivo("acoes")["registros"]
    assert len(corpo["registros"]) == 3
    assert corpo["chave"] == ["ticker"]


def test_proventos_e_fundamentos_por_ticker(cliente):
    prov = cliente.get("/v1/proventos/vale3", headers=CHAVE).json()
    esperados = [r for r in _arquivo("proventos")["registros"] if r["ticker"] == "VALE3"]
    assert esperados
    assert prov["registros"] == esperados

    fund = cliente.get("/v1/fundamentos_trimestrais/VALE3", headers=CHAVE).json()
    esperados = [r for r in _arquivo("fundamentos_trimestrais")["registros"] if r["ticker"] == "VALE3"]
    assert esperados
    assert fund["registros"] == esperados


def test_proventos_ticker_inexistente_404(cliente):
    r = cliente.get("/v1/proventos/XPTO4", headers=CHAVE)
    assert r.status_code == 404
    assert r.json() == {"detail": "ticker XPTO4 não encontrado"}


# ---------------------------------------------------------------- séries

def test_series_1a_igual_ao_arquivo(cliente):
    corpo = cliente.get("/v1/series_diarias/PETR4", headers=CHAVE).json()
    reg = corpo["registros"][0]
    arq = _registro_arquivo("series_diarias", "PETR4")
    assert reg == arq
    assert reg["datas"] == arq["datas"]
    assert reg["valores"] == arq["valores"]
    assert reg["status"] == arq["status"]
    assert reg["inicio_janela"] == arq["inicio_janela"]
    assert "inicio_ultima_completa" not in reg
    assert corpo["data_publicacao"] == PUB
    assert corpo["data_referencia"] == REF


def test_series_max_tem_inicio_ultima_completa(cliente):
    reg = cliente.get("/v1/series_diarias/PETR4?janela=max", headers=CHAVE).json()["registros"][0]
    assert len(reg["datas"]) == 253
    assert reg["inicio_ultima_completa"] == reg["inicio_janela"]


def _calendario(repo) -> list[str]:
    """Datas distintas de todas as séries do repositório falso, até o fim da publicação."""
    return sorted({p.data for pontos in repo.pontos.values() for p in pontos if p.data <= REF})


def test_series_curta_1a_comeca_no_inicio_da_janela_do_arquivo(cliente):
    reg = cliente.get("/v1/series_diarias/CURT3", headers=CHAVE).json()["registros"][0]
    assert reg["inicio_janela"] == _registro_arquivo("series_diarias", "PETR4")["inicio_janela"] == "2025-10-16"
    assert len(reg["datas"]) == 30
    assert "inicio_ultima_completa" not in reg


def test_series_curta_1m_inicio_e_a_21a_data_do_fim_no_calendario(repo, cliente):
    reg = cliente.get("/v1/series_diarias/CURT3", params={"janela": "1m"}, headers=CHAVE).json()["registros"][0]
    assert reg["inicio_janela"] == _calendario(repo)[-21]
    assert len(reg["datas"]) == 21


def test_series_curta_max_inicio_e_a_1a_data_do_calendario(repo, cliente):
    reg = cliente.get("/v1/series_diarias/CURT3", params={"janela": "max"}, headers=CHAVE).json()["registros"][0]
    assert reg["inicio_janela"] == _calendario(repo)[0] == "2025-10-16"
    assert reg["inicio_ultima_completa"] == "2025-10-16"
    assert len(reg["datas"]) == 30


def test_serie_sem_pontos_na_janela_da_404(repo, cliente):
    repo.catalogo["VELHA3"] = {"ticker": "VELHA3", "tipo": "acao", "nome": "Velha", "campos": ["fechamento"]}
    repo.pontos["VELHA3"] = [series.Ponto(data="2025-10-01", valores={"fechamento": 1.0}, status={})]
    r = cliente.get("/v1/series_diarias/VELHA3", headers=CHAVE)
    assert r.status_code == 404
    assert r.json() == {"detail": "ticker VELHA3 sem pontos na janela 1a"}


def test_series_janela_invalida_422(cliente):
    r = cliente.get("/v1/series_diarias/PETR4?janela=7x", headers=CHAVE)
    assert r.status_code == 422


def test_series_ticker_inexistente_404(cliente):
    r = cliente.get("/v1/series_diarias/XPTO4", headers=CHAVE)
    assert r.status_code == 404


# ---------------------------------------------------------------- histórico

def test_historico_pl_dois_dias(repo, cliente):
    base = _registro_arquivo("resumo_acao", "PETR4")
    segundo = json.loads(json.dumps(base))
    segundo["data_referencia"] = "2026-10-23"
    segundo["pl"]["valor"] = 6.01
    repo.fotos["resumo_acao"].append(_linha_foto(segundo, "2026-10-24"))
    corpo = cliente.get("/v1/resumo_acao/PETR4/historico?campo=pl", headers=CHAVE).json()
    assert corpo["ticker"] == "PETR4"
    assert corpo["campo"] == "pl"
    assert corpo["datas"] == [REF, "2026-10-23"]
    assert corpo["valores"] == [base["pl"]["valor"], 6.01]
    assert corpo["status"] == []
    assert corpo["data_referencia"] == "2026-10-23"
    assert corpo["data_publicacao"] == PUB


def test_historico_campo_aninhado(cliente):
    base = _registro_arquivo("resumo_acao", "PETR4")
    corpo = cliente.get("/v1/resumo_acao/PETR4/historico?campo=cotacao.fechamento", headers=CHAVE).json()
    assert corpo["datas"] == [REF]
    assert corpo["valores"] == [base["cotacao"]["fechamento"]["valor"]]


def test_historico_status_nao_ok_vira_trecho(repo, cliente):
    base = _registro_arquivo("resumo_acao", "PETR4")
    segundo = json.loads(json.dumps(base))
    segundo["data_referencia"] = "2026-10-23"
    segundo["pl"] = {"valor": None, "status": "indisponivel"}
    repo.fotos["resumo_acao"].append(_linha_foto(segundo, "2026-10-24"))
    corpo = cliente.get("/v1/resumo_acao/PETR4/historico?campo=pl", headers=CHAVE).json()
    assert corpo["valores"] == [base["pl"]["valor"], None]
    assert corpo["status"] == [{"de": "2026-10-23", "ate": "2026-10-23", "status": "indisponivel"}]


def test_historico_campo_sem_valor_422(cliente):
    r = cliente.get("/v1/resumo_acao/PETR4/historico?campo=cotacao", headers=CHAVE)
    assert r.status_code == 422


def test_historico_campo_inexistente_404(cliente):
    r = cliente.get("/v1/resumo_acao/PETR4/historico?campo=xyz", headers=CHAVE)
    assert r.status_code == 404
    assert r.json() == {"detail": "campo xyz não existe no resumo_acao"}


def test_historico_campo_invalido_422(cliente):
    assert cliente.get("/v1/resumo_acao/PETR4/historico?campo=PL", headers=CHAVE).status_code == 422
    assert cliente.get("/v1/resumo_acao/PETR4/historico?campo=pl;drop", headers=CHAVE).status_code == 422


def test_historico_ticker_inexistente_404(cliente):
    r = cliente.get("/v1/resumo_acao/XPTO4/historico?campo=pl", headers=CHAVE)
    assert r.status_code == 404
    assert r.json() == {"detail": "ticker XPTO4 não encontrado"}


# ---------------------------------------------------------------- execução

def test_execucao_ultima_e_do_dia(cliente):
    reg = _arquivo("execucao")["registros"][0]
    corpo = cliente.get("/v1/execucao/ultima", headers=CHAVE).json()
    assert corpo["pacote"] == "execucao"
    assert corpo["registros"] == [reg]
    assert corpo["data_publicacao"] == PUB
    assert corpo["data_referencia"] == reg["data_referencia"]

    do_dia = cliente.get(f"/v1/execucao/{reg['data_referencia']}", headers=CHAVE)
    assert do_dia.status_code == 200
    assert do_dia.json()["registros"] == [reg]

    assert cliente.get("/v1/execucao/2026-10-01", headers=CHAVE).status_code == 404


# ---------------------------------------------------------------- método e documentação

def test_post_da_405(cliente):
    assert cliente.post("/v1/acoes", headers=CHAVE).status_code == 405


def test_nenhuma_rota_de_escrita(cliente):
    metodos = set()
    for rota in cliente.app.routes:
        metodos |= set(getattr(rota, "methods", None) or set())
    assert metodos <= {"GET", "HEAD"}


def test_cors_origem_permitida(cliente):
    r = cliente.get("/v1/saude", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_outra_origem_nao_recebe(cliente):
    r = cliente.get("/v1/saude", headers={"Origin": "http://site-estranho.example"})
    assert "access-control-allow-origin" not in r.headers


def test_docs_e_openapi(cliente):
    assert cliente.get("/docs").status_code == 200
    r = cliente.get("/openapi.json")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["info"]["title"] == "API de dados do TCC"
    assert corpo["info"]["version"] == "1.0"
    esquemas = corpo["components"]["securitySchemes"]
    assert any(e.get("name") == "X-API-Key" and e.get("in") == "header" for e in esquemas.values())


# ---------------------------------------------------------------- SQL do repositório

class ConexaoEspiao:
    """Imita banco.Conexao: t() com o prefixo e consultar() que só registra o SQL e os parâmetros.

    Não passa pela guarda sozinha: o teste chama guarda_sql.verificar em cada SQL registrado.
    """

    def __init__(self, prefixo: str):
        self.prefixo = prefixo
        self.somente_leitura = True
        self.registrados = []

    def t(self, base: str) -> str:
        return tabelas.nome(base, self.prefixo)

    def consultar(self, sql, params=None):
        self.registrados.append((sql, params))
        if "series_catalogo" in sql:
            return [{"ticker": "PETR4", "tipo": "acao", "nome": "Petrobras", "campos": '["fechamento"]'}]
        if "MAX(data_referencia)" in sql:
            return [{"referencia": date.fromisoformat(REF)}]
        return []


def _chamar_todos_os_metodos(repo: repositorio.Repositorio) -> None:
    repo.ultima_publicacao()
    repo.publicacao_ate(PUB)
    repo.ping()
    for pacote in ("resumo_acao", "noticias_dia"):
        repo.foto(pacote, None, "PETR4")
        repo.foto(pacote, PUB, "PETR4")
    for pacote in ("mercado_dia", "experimento_top", "experimento_historico", "execucao"):
        repo.foto(pacote, None, None)
        repo.foto(pacote, PUB, None)
    for pacote in ("resumo_acao", "noticias_dia"):
        repo.fotos_do_dia(pacote, None)
        repo.fotos_do_dia(pacote, PUB)
    repo.versoes("acoes", None)
    repo.versoes("acoes", PUB)
    repo.versoes("fundamentos_trimestrais", PUB, "PETR4")
    repo.versoes("proventos", None, "PETR4")
    repo.ticker_existe("PETR4")
    repo.serie("PETR4", REF)
    repo.inicio_da_janela(REF, 253)
    repo.inicio_da_janela(REF, None)
    repo.historico("PETR4", "pl")
    repo.historico("PETR4", "pl", "2026-10-01")
    repo.historico("PETR4", "pl", None, "2026-10-31")
    repo.historico("PETR4", "pl", "2026-10-01", "2026-10-31")
    repo.execucao_ultima()
    repo.execucoes(REF)


@pytest.mark.parametrize("prefixo", ["", tabelas.PREFIXO_TESTE])
def test_todo_sql_do_repositorio_passa_na_guarda(prefixo):
    espiao = ConexaoEspiao(prefixo)
    _chamar_todos_os_metodos(repositorio.Repositorio(espiao))
    assert len(espiao.registrados) >= 20
    for sql, params in espiao.registrados:
        guarda_sql.verificar(sql, prefixo, somente_leitura=True)
        assert sql.count("%s") == len(params or ()), sql


def test_sql_de_teste_usa_so_tabelas_teste():
    espiao = ConexaoEspiao(tabelas.PREFIXO_TESTE)
    _chamar_todos_os_metodos(repositorio.Repositorio(espiao))
    for sql, _ in espiao.registrados:
        for base in tabelas.NOMES_BASE:
            # Com prefixo, "teste_acoes" não tem fronteira de palavra antes de "acoes": só o nome real casa.
            assert not re.search(rf"\b{base}\b", sql), f"tabela real citada no modo teste: {base} em {sql}"


class ConexaoCalendario(ConexaoEspiao):
    """Devolve um calendário de 3 pregões (22, 21 e 20/10/2026) e o início 16/10/2025 para o MIN."""

    def consultar(self, sql, params=None):
        self.registrados.append((sql, params))
        if "DISTINCT" in sql:  # o LIMIT (último parâmetro) corta as linhas, como no banco
            linhas = [{"data": date(2026, 10, 22)}, {"data": date(2026, 10, 21)}, {"data": date(2026, 10, 20)}]
            return linhas[:params[-1]]
        if "MIN(" in sql:
            return [{"inicio": date(2025, 10, 16)}]
        return []


def test_inicio_da_janela_le_o_calendario_do_banco():
    espiao = ConexaoCalendario(tabelas.PREFIXO_TESTE)
    repo = repositorio.Repositorio(espiao)
    assert repo.inicio_da_janela(REF, 2) == "2026-10-21"  # 2º pregão contado do fim
    assert repo.inicio_da_janela(REF, 5) == "2026-10-20"  # menos pregões que n: o mais antigo
    assert repo.inicio_da_janela(REF, None) == "2025-10-16"
    assert espiao.registrados[0][1] == (REF, 2)
    assert espiao.registrados[2][1] == (REF,)


def test_ping_e_select_simples():
    assert repositorio.SQL_PING == "SELECT 1 AS ok"
    guarda_sql.verificar(repositorio.SQL_PING, "", somente_leitura=True)
    guarda_sql.verificar(repositorio.SQL_PING, tabelas.PREFIXO_TESTE, somente_leitura=True)


def test_data_inexistente_no_calendario_da_422(cliente):
    r = cliente.get("/v1/acoes", params={"data": "2026-13-45"}, headers={"X-API-Key": "chave-teste"})
    assert r.status_code == 422


PROTEGIDOS_ESPERADOS = {
    "/v1/acoes", "/v1/resumo_acao", "/v1/resumo_acao/{ticker}", "/v1/resumo_acao/{ticker}/historico",
    "/v1/series_diarias/{ticker}", "/v1/fundamentos_trimestrais/{ticker}", "/v1/proventos/{ticker}",
    "/v1/noticias_dia", "/v1/noticias_dia/{ticker}", "/v1/mercado_dia", "/v1/experimento/top",
    "/v1/experimento/historico", "/v1/execucao/ultima", "/v1/execucao/{data}",
}


def test_docs_documentam_401_e_404_em_todos_os_endpoints_com_chave(cliente):
    caminhos = cliente.get("/openapi.json").json()["paths"]
    protegidos = [c for c in caminhos if c != "/v1/saude"]
    assert set(protegidos) == PROTEGIDOS_ESPERADOS
    assert len(protegidos) == 14
    for caminho in protegidos:
        respostas = caminhos[caminho]["get"]["responses"]
        for codigo in ("401", "404"):
            assert codigo in respostas, (caminho, codigo)
            esquema = respostas[codigo]["content"]["application/json"]["schema"]
            assert esquema == {"$ref": "#/components/schemas/Erro"}, (caminho, codigo)
