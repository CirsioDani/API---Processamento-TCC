"""Confere a API contra os pacotes do processamento. Só lê: não grava no processamento nem no banco.

Fonte da verdade: <processamento>/saida_completa/<data>/*.json e <processamento>/saida/<data>/execucao.json.
A execução vem de saida/ porque é ela que o publicador grava (entrada.execucao); o execucao de saida_completa
só serve para conferir pacotes_enviados. Para cada pacote, chama a API com ?data=<data> e compara registro a
registro pela chave natural (contrato.PACOTES).

Uso:
    python ferramentas/conferir_api.py --data AAAA-MM-DD [--url http://127.0.0.1:8000]
        [--processamento PASTA] [--remontar [PASTA]]

Saída: 0 = tudo igual; 1 = há diferença, registro faltando ou a mais; 2 = erro que impede a conferência
(API fora do ar, chave ausente, pasta inexistente, resposta inesperada da API).
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from comum import config, contrato  # noqa: E402 (a raiz precisa estar no sys.path antes)

URL_PADRAO = "http://127.0.0.1:8000"
PASTA_REMONTADA_PADRAO = "conferencia_remontada"
TIMEOUT_SEGUNDOS = 30
JANELA_SERIES = "1a"
MAX_EXEMPLOS = 3
PACOTES_DA_COMPLETA = (
    "acoes", "resumo_acao", "noticias_dia", "series_diarias", "fundamentos_trimestrais", "proventos", "mercado_dia",
)
PACOTES_EXPERIMENTO = {"experimento_top": "/v1/experimento/top", "experimento_historico": "/v1/experimento/historico"}
# Séries: só estes campos entram na comparação (os metadados da série ficam de fora).
CAMPOS_SERIE = ("ticker", "tipo", "nome", "substituir_serie", "inicio_janela", "datas", "valores", "status")
# Ordem da remontagem; sem entrada aqui vale a chave do contrato. Proventos segue a ordem pedida (ticker, data_com, tipo).
ORDEM_REMONTAGEM = {"proventos": ("ticker", "data_com", "tipo")}
AUSENTE = object()  # marca campo que existe de um lado só


class ErroAPI(Exception):
    """A API não respondeu como a conferência precisa (fora do ar, chave recusada, erro do servidor)."""


def _json(corpo: bytes):
    try:
        return json.loads(corpo.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def criar_buscar(url: str, chave: str, timeout: float = TIMEOUT_SEGUNDOS):
    """Devolve buscar(caminho, params) -> (status_http, corpo_json) usando o cabeçalho X-API-Key."""
    base = url.rstrip("/")

    def buscar(caminho: str, params: dict) -> tuple[int, object]:
        endereco = base + caminho
        if params:
            endereco += "?" + urllib.parse.urlencode(params)
        pedido = urllib.request.Request(endereco, headers={"X-API-Key": chave, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
                return resposta.status, _json(resposta.read())
        except urllib.error.HTTPError as erro:  # antes de URLError: HTTPError é subclasse dele
            return erro.code, _json(erro.read())
        except OSError:  # URLError, recusa de conexão, timeout de leitura
            raise ErroAPI(f"API não está respondendo em {url}") from None

    return buscar


class _Api:
    """Chama a API com ?data=<data>. Guarda o envelope da primeira resposta de cada pacote (para a remontagem)."""

    def __init__(self, buscar, data: str):
        self._buscar = buscar
        self._data = data
        self.envelopes: dict[str, dict] = {}

    def get(self, pacote: str, caminho: str, extra: dict | None = None) -> list:
        params = {"data": self._data, **(extra or {})}
        status, corpo = self._buscar(caminho, params)
        if status == 404:
            return []  # 404 = registro faltando na API; não é erro do script
        if status != 200 or not isinstance(corpo, dict):
            detalhe = corpo.get("detail") if isinstance(corpo, dict) else None
            raise ErroAPI(f"{caminho} respondeu HTTP {status}" + (f": {detalhe}" if detalhe else ""))
        self.envelopes.setdefault(pacote, corpo)
        return list(corpo.get("registros", []))


def _por_ticker(api: _Api, pacote: str, caminho_base: str, tickers, extra: dict | None = None) -> list:
    registros = []
    for ticker in tickers:
        registros += api.get(pacote, f"{caminho_base}/{urllib.parse.quote(ticker, safe='')}", extra)
    return registros


def _regs(arquivos: dict, pacote: str) -> list:
    return arquivos[pacote]["registros"] if pacote in arquivos else []


def _tickers(arquivos: dict, pacotes) -> list:
    return sorted({r["ticker"] for pacote in pacotes for r in _regs(arquivos, pacote)})


def _ler(caminho: Path) -> dict:
    return json.loads(caminho.read_text(encoding="utf-8"))


@dataclass
class Linha:
    rotulo: str
    arquivo: int
    api: int
    iguais: int = 0
    diferentes: int = 0
    faltando: int = 0
    a_mais: int = 0
    exemplos: list = field(default_factory=list)  # diferenças (até MAX_EXEMPLOS)
    faltas: list = field(default_factory=list)  # faltando (até MAX_EXEMPLOS)
    sobras: list = field(default_factory=list)  # a mais (até MAX_EXEMPLOS)


def _indexar(registros: list, chave: tuple) -> dict:
    return {tuple(r[campo] for campo in chave): r for r in registros}


def _filtrar(registro: dict, ignorar, campos) -> dict:
    if campos is not None:
        return {k: registro[k] for k in campos if k in registro}
    # Versionados (acoes, fundamentos, proventos): uma versão que não mudou guarda o gerado_em/dados_ate do dia
    # em que nasceu, não o do dia consultado. Por isso versao_contrato/gerado_em/dados_ate não contam na
    # comparação. Vale só no primeiro nível do registro.
    return {k: v for k, v in registro.items() if k not in ignorar}


def _primeira_diferenca(a, b, caminho: str = ""):
    """(caminho, valor_arquivo, valor_api) do primeiro ponto em que a e b diferem, ou None."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in list(a) + [k for k in b if k not in a]:
            if k not in a:
                return f"{caminho}.{k}", AUSENTE, b[k]
            if k not in b:
                return f"{caminho}.{k}", a[k], AUSENTE
            achado = _primeira_diferenca(a[k], b[k], f"{caminho}.{k}")
            if achado is not None:
                return achado
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return caminho, f"{len(a)} itens", f"{len(b)} itens"
        for i, (x, y) in enumerate(zip(a, b)):
            achado = _primeira_diferenca(x, y, f"{caminho}[{i}]")
            if achado is not None:
                return achado
        return None
    if a != b or isinstance(a, bool) != isinstance(b, bool):
        return caminho, a, b
    return None


def _txt(chave: tuple) -> str:
    return ", ".join(str(parte) for parte in chave)


def _resumo(valor) -> str:
    if valor is AUSENTE:
        return "ausente"
    texto = valor if isinstance(valor, str) else json.dumps(valor, ensure_ascii=False)
    return texto if len(texto) <= 60 else texto[:57] + "..."


def _guardar(lista: list, texto: str) -> None:
    if len(lista) < MAX_EXEMPLOS:
        lista.append(texto)


def comparar(rotulo: str, nome: str, chave: tuple, esperados: list, recebidos: list,
             ignorar=(), campos=None) -> Linha:
    """Compara os registros do arquivo (esperados) com os da API pela chave natural."""
    linha = Linha(rotulo, arquivo=len(esperados), api=len(recebidos))
    do_arquivo = _indexar(esperados, chave)
    da_api = _indexar(recebidos, chave)
    for k, registro in do_arquivo.items():
        if k not in da_api:
            linha.faltando += 1
            _guardar(linha.faltas, f"faltando: {nome}[{_txt(k)}]")
            continue
        achado = _primeira_diferenca(_filtrar(registro, ignorar, campos), _filtrar(da_api[k], ignorar, campos))
        if achado is None:
            linha.iguais += 1
            continue
        caminho, valor_arquivo, valor_api = achado
        linha.diferentes += 1
        _guardar(linha.exemplos, f"{nome}[{_txt(k)}]{caminho}: arquivo {_resumo(valor_arquivo)} ≠ API {_resumo(valor_api)}")
    for k in da_api:
        if k not in do_arquivo:
            linha.a_mais += 1
            _guardar(linha.sobras, f"a mais: {nome}[{_txt(k)}]")
    return linha


def _conferir_pacotes(buscar, pasta_completa: Path, pasta_saida: Path, data: str):
    arquivos: dict[str, dict] = {}
    for pacote in (*PACOTES_DA_COMPLETA, *PACOTES_EXPERIMENTO):
        if (pasta_completa / f"{pacote}.json").is_file():
            arquivos[pacote] = _ler(pasta_completa / f"{pacote}.json")
    if (pasta_saida / "execucao.json").is_file():
        arquivos["execucao"] = _ler(pasta_saida / "execucao.json")
    for pacote in (*PACOTES_DA_COMPLETA, "execucao"):
        if pacote not in arquivos:
            print(f"aviso: {pacote} sem arquivo nesta publicação; não conferido")

    api = _Api(buscar, data)
    linhas: list[Linha] = []
    dados: dict[str, list] = {}  # o que a API devolveu, para a remontagem
    tickers_acoes = _tickers(arquivos, ("acoes",))
    tickers_resumo = _tickers(arquivos, ("acoes", "resumo_acao"))

    if "acoes" in arquivos:
        recebidos = api.get("acoes", "/v1/acoes")
        linhas.append(comparar("acoes", "acoes", contrato.PACOTES["acoes"].chave, _regs(arquivos, "acoes"),
                               recebidos, ignorar=contrato.METADADOS))
        dados["acoes"] = recebidos

    for pacote in ("resumo_acao", "noticias_dia"):
        if pacote in arquivos:
            esperados = _regs(arquivos, pacote)
            chave = contrato.PACOTES[pacote].chave
            lista = api.get(pacote, f"/v1/{pacote}")
            por_ticker = _por_ticker(api, pacote, f"/v1/{pacote}", tickers_resumo)
            linhas.append(comparar(f"{pacote} (lista)", pacote, chave, esperados, lista))
            linhas.append(comparar(f"{pacote} (por ticker)", pacote, chave, esperados, por_ticker))
            dados[pacote] = lista

    if "series_diarias" in arquivos:
        esperados = _regs(arquivos, "series_diarias")
        recebidos = _por_ticker(api, "series_diarias", "/v1/series_diarias",
                                [r["ticker"] for r in esperados], {"janela": JANELA_SERIES})
        linhas.append(comparar("series_diarias", "series_diarias", ("ticker",), esperados, recebidos,
                               campos=CAMPOS_SERIE))
        dados["series_diarias"] = recebidos

    for pacote in ("fundamentos_trimestrais", "proventos"):
        if pacote in arquivos:
            # tickers de acoes.json + os do próprio pacote: um ticker só presente no pacote também é conferido
            recebidos = _por_ticker(api, pacote, f"/v1/{pacote}", _tickers(arquivos, ("acoes", pacote)))
            linhas.append(comparar(pacote, pacote, contrato.PACOTES[pacote].chave, _regs(arquivos, pacote),
                                   recebidos, ignorar=contrato.METADADOS))
            dados[pacote] = recebidos

    fotos = (("mercado_dia", "/v1/mercado_dia"), ("execucao", f"/v1/execucao/{data}"), *PACOTES_EXPERIMENTO.items())
    for pacote, caminho in fotos:
        if pacote in arquivos:
            recebidos = api.get(pacote, caminho)
            linhas.append(comparar(pacote, pacote, contrato.PACOTES[pacote].chave, _regs(arquivos, pacote), recebidos))
            dados[pacote] = recebidos

    return linhas, dados, api.envelopes


def _imprimir(linhas: list[Linha]) -> None:
    cabecalho = ["pacote", "arquivo", "API", "iguais", "diferentes", "faltando", "a mais"]
    tabela = [cabecalho] + [
        [l.rotulo, str(l.arquivo), str(l.api), str(l.iguais), str(l.diferentes), str(l.faltando), str(l.a_mais)]
        for l in linhas
    ]
    larguras = [max(len(celula) for celula in coluna) for coluna in zip(*tabela)]

    def formatar(celulas) -> str:
        return "  ".join(c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in enumerate(zip(celulas, larguras)))

    print(formatar(cabecalho))
    print("  ".join("-" * w for w in larguras))
    for celulas in tabela[1:]:
        print(formatar(celulas))
    exemplos = [texto for l in linhas for texto in (*l.exemplos, *l.faltas, *l.sobras)]
    if exemplos:
        print()
        print(f"Exemplos (até {MAX_EXEMPLOS} por tipo e linha):")
        for texto in exemplos:
            print(f"  {texto}")


def _remontar(pasta: Path, dados: dict, envelopes: dict) -> None:
    """Grava um JSON por pacote no formato do contrato, com os dados que a API devolveu."""
    pasta.mkdir(parents=True, exist_ok=True)
    gravados = 0
    for pacote, registros in dados.items():
        envelope = envelopes.get(pacote)
        if envelope is None:
            print(f"aviso: a API não devolveu envelope de {pacote}; não remontado")
            continue
        if pacote in contrato.VERSIONADOS:
            # Versionados: uma versão que não mudou guarda os metadados do dia em que nasceu. Para o pacote
            # remontado ficar coerente com o arquivo do dia, cada registro recebe os metadados do envelope.
            meta = {k: envelope[k] for k in contrato.METADADOS}
            registros = [{**r, **meta} for r in registros]
        if pacote != "series_diarias":  # séries seguem a ordem do arquivo
            ordem = ORDEM_REMONTAGEM.get(pacote, contrato.PACOTES[pacote].chave)
            registros = sorted(registros, key=lambda r: tuple(r[campo] for campo in ordem))
        saida = {
            "pacote": pacote,
            "versao_contrato": envelope["versao_contrato"],
            "chave": envelope["chave"],
            "gerado_em": envelope["gerado_em"],
            "dados_ate": envelope["dados_ate"],
            "registros": registros,
            "removidos": [],
        }
        with (pasta / f"{pacote}.json").open("w", encoding="utf-8") as arquivo:
            if pacote == "series_diarias":
                json.dump(saida, arquivo, ensure_ascii=False)
            else:
                json.dump(saida, arquivo, ensure_ascii=False, indent=1)
        gravados += 1
    print(f"remontado: {gravados} arquivo(s) em {pasta.resolve()}")


def conferir(buscar, dir_processamento, data: str, remontar=None) -> int:
    """Confere a publicação `data` contra a API. buscar(caminho, params) -> (status_http, corpo_json).

    Devolve 0 (tudo igual), 1 (diferença, falta ou sobra) ou 2 (erro que impede a conferência).
    Se `remontar` for uma pasta, grava nela os pacotes como a API devolveu.
    """
    base = Path(dir_processamento)
    pasta_completa = base / "saida_completa" / data
    pasta_saida = base / "saida" / data
    if not pasta_completa.is_dir():
        print(f"pasta da publicação não encontrada: {pasta_completa}")
        return 2
    print(f"Conferindo a publicação {data} ({pasta_completa})")
    try:
        linhas, dados, envelopes = _conferir_pacotes(buscar, pasta_completa, pasta_saida, data)
    except ErroAPI as erro:
        print(f"erro: {erro}")
        return 2
    if not linhas:
        print(f"nenhum pacote para conferir em {pasta_completa}")
        return 2

    _imprimir(linhas)
    if remontar is not None:
        _remontar(Path(remontar), dados, envelopes)
    problemas = sum(l.diferentes + l.faltando + l.a_mais for l in linhas)
    print("CONFERÊNCIA OK" if problemas == 0 else "CONFERÊNCIA COM DIFERENÇAS")
    return 0 if problemas == 0 else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Confere a API de dados do TCC contra o processamento (só leitura).")
    parser.add_argument("--data", required=True, metavar="AAAA-MM-DD",
                        help="pasta da publicação (D), a mesma usada em ?data=")
    parser.add_argument("--url", default=URL_PADRAO, help=f"endereço da API (padrão: {URL_PADRAO})")
    parser.add_argument("--processamento", metavar="PASTA",
                        help="pasta do processamento-tcc (padrão: a da configuração)")
    parser.add_argument("--remontar", nargs="?", const=PASTA_REMONTADA_PADRAO, default=None, metavar="PASTA",
                        help=f"grava os pacotes como a API devolveu (padrão: {PASTA_REMONTADA_PADRAO})")
    args = parser.parse_args(argv)
    try:
        date.fromisoformat(args.data)
    except ValueError:
        parser.error("--data deve ser uma data existente, no formato AAAA-MM-DD")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # o console pode não ter o caractere "≠"

    try:
        cfg = config.carregar()
    except config.ErroConfig as erro:
        print(f"configuração inválida: {erro}")
        return 2
    if not cfg.chaves_api:
        print("sem chave de API na configuração (PROC_API_CHAVES): não dá para consultar a API")
        return 2
    processamento = Path(args.processamento) if args.processamento else cfg.dir_processamento
    buscar = criar_buscar(args.url, cfg.chaves_api[0])
    return conferir(buscar, processamento, args.data, args.remontar)


if __name__ == "__main__":
    sys.exit(main())
