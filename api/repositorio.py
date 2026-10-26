"""Consultas da API. Só SELECT. Os nomes de tabela entram pelo `conexao.t(base)` e os valores como `%s`.

Os SQL ficam em funções `sql_*` (recebem os nomes reais das tabelas) para o teste poder passá-los pela guarda.
"""
from comum import contrato, series, tabelas
from comum.banco import Conexao, ler_json

SQL_PING = "SELECT 1 AS ok"

# Versionadas: ordem das colunas da chave (acoes: ticker; fundamentos: data_fim_periodo; proventos: data_com, tipo).
ORDEM_VERSIONADAS = {
    "acoes": "ticker",
    "fundamentos_trimestrais": "data_fim_periodo",
    "proventos": "data_com, tipo",
}


def sql_ultima_publicacao(publicacoes: str) -> str:
    return (
        f"SELECT data_publicacao, data_referencia, metadados FROM {publicacoes} "
        "WHERE status = 'completa' ORDER BY data_publicacao DESC LIMIT 1"
    )


def sql_publicacao_ate(publicacoes: str) -> str:
    return (
        f"SELECT data_publicacao, data_referencia, metadados FROM {publicacoes} "
        "WHERE status = 'completa' AND data_publicacao <= %s ORDER BY data_publicacao DESC LIMIT 1"
    )


def sql_foto(tabela: str, com_ticker: bool, com_data: bool) -> str:
    filtros = []
    if com_ticker:
        filtros.append("ticker = %s")
    if com_data:
        filtros.append("data_publicacao <= %s")
    where = f" WHERE {' AND '.join(filtros)}" if filtros else ""
    ordem = "data_publicacao DESC, data_referencia DESC" if com_data else "data_referencia DESC"
    return f"SELECT registro, data_publicacao, data_referencia FROM {tabela}{where} ORDER BY {ordem} LIMIT 1"


def sql_ultima_referencia(tabela: str, com_data: bool) -> str:
    filtro = " WHERE data_publicacao <= %s" if com_data else ""
    return f"SELECT MAX(data_referencia) AS referencia FROM {tabela}{filtro}"


def sql_fotos_da_referencia(tabela: str, com_data: bool) -> str:
    filtro = " AND data_publicacao <= %s" if com_data else ""
    return (
        f"SELECT registro, data_publicacao, data_referencia FROM {tabela} "
        f"WHERE data_referencia = %s{filtro} ORDER BY ticker"
    )


def sql_versoes(tabela: str, ordem: str, com_ticker: bool, com_data: bool) -> str:
    filtros = []
    if com_ticker:
        filtros.append("ticker = %s")
    if com_data:
        filtros.append("valido_desde <= %s")
        filtros.append("(valido_ate IS NULL OR valido_ate > %s)")
    else:
        filtros.append("valido_ate IS NULL")
    return f"SELECT registro FROM {tabela} WHERE {' AND '.join(filtros)} ORDER BY {ordem}"


def sql_ticker_existe(acoes: str) -> str:
    return f"SELECT 1 AS existe FROM {acoes} WHERE ticker = %s LIMIT 1"


def sql_catalogo(series_catalogo: str) -> str:
    return f"SELECT ticker, tipo, nome, campos FROM {series_catalogo} WHERE ticker = %s"


def sql_pontos(series_pontos: str) -> str:
    return (
        f"SELECT data, valores, status FROM {series_pontos} "
        "WHERE ticker = %s AND removido_em IS NULL AND data <= %s ORDER BY data"
    )


def sql_inicio_max(series_pontos: str) -> str:
    return f"SELECT MIN(data) AS inicio FROM {series_pontos} WHERE removido_em IS NULL AND data <= %s"


def sql_calendario_recente(series_pontos: str) -> str:
    return (
        f"SELECT DISTINCT data FROM {series_pontos} "
        "WHERE removido_em IS NULL AND data <= %s ORDER BY data DESC LIMIT %s"
    )


def sql_recusas(series_ajustes: str) -> str:
    return (
        f"SELECT campo, MAX(ate) AS ate FROM {series_ajustes} "
        "WHERE ticker = %s AND classificacao = 'recusado' AND data_publicacao <= %s GROUP BY campo"
    )


def sql_historico(resumo_acao: str, com_inicio: bool, com_fim: bool) -> str:
    filtros = ["ticker = %s"]
    if com_inicio:
        filtros.append("data_referencia >= %s")
    if com_fim:
        filtros.append("data_referencia <= %s")
    return (
        f"SELECT data_referencia, registro FROM {resumo_acao} "
        f"WHERE {' AND '.join(filtros)} ORDER BY data_referencia"
    )


def _extrair(registro: dict, campo: str):
    """Valor do caminho 'a.b.c' no registro; None se algum nível não existir."""
    valor = registro
    for parte in campo.split("."):
        if not isinstance(valor, dict) or parte not in valor:
            return None
        valor = valor[parte]
    return valor


def sql_execucao_ultima(execucao: str) -> str:
    return (
        f"SELECT registro, data_publicacao FROM {execucao} "
        "ORDER BY data_referencia DESC, rodada DESC LIMIT 1"
    )


def sql_execucoes(execucao: str) -> str:
    return (
        f"SELECT registro, data_publicacao FROM {execucao} "
        "WHERE data_referencia = %s ORDER BY rodada"
    )


def _iso(valor):
    return None if valor is None else valor.isoformat()


def _publicacao(linha: dict) -> dict:
    return {
        "data_publicacao": _iso(linha["data_publicacao"]),
        "data_referencia": _iso(linha["data_referencia"]),
        "metadados": ler_json(linha["metadados"]) or {},
    }


class Repositorio:
    def __init__(self, conexao: Conexao):
        self._c = conexao

    def _t(self, base: str) -> str:
        return self._c.t(base)

    def ultima_publicacao(self) -> dict | None:
        linhas = self._c.consultar(sql_ultima_publicacao(self._t("publicacoes")))
        return _publicacao(linhas[0]) if linhas else None

    def publicacao_ate(self, data: str) -> dict | None:
        linhas = self._c.consultar(sql_publicacao_ate(self._t("publicacoes")), (data,))
        return _publicacao(linhas[0]) if linhas else None

    def foto(self, pacote: str, data: str | None, ticker: str | None):
        """(registro, data_publicacao, data_referencia) ou None."""
        if pacote not in tabelas.FOTOS:
            raise ValueError(f"pacote não é uma foto: {pacote!r}")
        com_ticker = "ticker" in contrato.PACOTES[pacote].chave
        if com_ticker and not ticker:
            raise ValueError(f"o pacote {pacote!r} precisa de ticker")
        params = []
        if com_ticker:
            params.append(ticker)
        if data is not None:
            params.append(data)
        linhas = self._c.consultar(
            sql_foto(self._t(pacote), com_ticker, data is not None), tuple(params) or None
        )
        if not linhas:
            return None
        linha = linhas[0]
        return ler_json(linha["registro"]), _iso(linha["data_publicacao"]), _iso(linha["data_referencia"])

    def fotos_do_dia(self, pacote: str, data: str | None):
        """(registros, data_publicacao, data_referencia) das linhas do pacote no pregão alvo, ou None.

        Alvo: a maior data_referencia (com data, só entre as linhas publicadas até ela). Os registros são
        todas as linhas desse pregão, por ticker. data_publicacao = a maior dessas linhas."""
        if pacote not in ("resumo_acao", "noticias_dia"):
            raise ValueError(f"pacote não é diário por ticker: {pacote!r}")
        tabela = self._t(pacote)
        ref = self._c.consultar(sql_ultima_referencia(tabela, data is not None), (data,) if data else None)
        alvo = _iso(ref[0]["referencia"]) if ref else None
        if alvo is None:
            return None
        params = (alvo,) if data is None else (alvo, data)
        linhas = self._c.consultar(sql_fotos_da_referencia(tabela, data is not None), params)
        if not linhas:
            return None
        registros = [ler_json(linha["registro"]) for linha in linhas]
        data_publicacao = max(_iso(linha["data_publicacao"]) for linha in linhas)
        return registros, data_publicacao, alvo

    def versoes(self, pacote: str, data: str | None, ticker: str | None = None) -> list[dict]:
        if pacote not in ORDEM_VERSIONADAS:
            raise ValueError(f"pacote não é versionado: {pacote!r}")
        params = []
        if ticker is not None:
            params.append(ticker)
        if data is not None:
            params.extend([data, data])
        sql = sql_versoes(self._t(pacote), ORDEM_VERSIONADAS[pacote], ticker is not None, data is not None)
        linhas = self._c.consultar(sql, tuple(params) or None)
        return [ler_json(linha["registro"]) for linha in linhas]

    def ticker_existe(self, ticker: str) -> bool:
        return bool(self._c.consultar(sql_ticker_existe(self._t("acoes")), (ticker,)))

    def serie(self, ticker: str, data_fim: str | None):
        """(catalogo, pontos, recusas) ou None se a série não existe no catálogo."""
        cat = self._c.consultar(sql_catalogo(self._t("series_catalogo")), (ticker,))
        if not cat:
            return None
        linha = cat[0]
        catalogo = {
            "ticker": linha["ticker"],
            "tipo": linha["tipo"],
            "nome": linha["nome"],
            "campos": ler_json(linha["campos"]),
        }
        linhas = self._c.consultar(sql_pontos(self._t("series_pontos")), (ticker, data_fim))
        pontos = [
            series.Ponto(
                data=_iso(r["data"]),
                valores=ler_json(r["valores"]),
                status=ler_json(r["status"]) or {},
            )
            for r in linhas
        ]
        recusas = {
            r["campo"]: _iso(r["ate"])
            for r in self._c.consultar(sql_recusas(self._t("series_ajustes")), (ticker, data_fim))
            if r["ate"] is not None
        }
        return catalogo, pontos, recusas

    def inicio_da_janela(self, data_fim: str, n: int | None) -> str | None:
        """Início da janela de n pregões até data_fim, sobre o calendário (datas distintas de todas as séries).

        n None ("max"): a 1ª data do calendário. Com menos de n pregões, a mais antiga."""
        tabela = self._t("series_pontos")
        if n is None:
            linhas = self._c.consultar(sql_inicio_max(tabela), (data_fim,))
            return _iso(linhas[0]["inicio"]) if linhas else None
        linhas = self._c.consultar(sql_calendario_recente(tabela), (data_fim, n))
        return _iso(linhas[-1]["data"]) if linhas else None

    def historico(self, ticker: str, campo: str, inicio: str | None = None, fim: str | None = None) -> list:
        """Lista de (data_referencia, valor decodificado). Campo ausente no registro vem como None.

        O campo é extraído em Python, não com JSON_EXTRACT: o MySQL arredondaria o último dígito dos números."""
        params = [ticker]
        if inicio is not None:
            params.append(inicio)
        if fim is not None:
            params.append(fim)
        sql = sql_historico(self._t("resumo_acao"), inicio is not None, fim is not None)
        linhas = self._c.consultar(sql, tuple(params))
        return [(_iso(r["data_referencia"]), _extrair(ler_json(r["registro"]), campo)) for r in linhas]

    def execucao_ultima(self):
        """(registro, data_publicacao) ou None."""
        linhas = self._c.consultar(sql_execucao_ultima(self._t("execucao")))
        if not linhas:
            return None
        return ler_json(linhas[0]["registro"]), _iso(linhas[0]["data_publicacao"])

    def execucoes(self, data: str) -> list:
        """Lista de (registro, data_publicacao) da data_referencia dada, por rodada."""
        linhas = self._c.consultar(sql_execucoes(self._t("execucao")), (data,))
        return [(ler_json(r["registro"]), _iso(r["data_publicacao"])) for r in linhas]

    def ping(self) -> None:
        self._c.consultar(SQL_PING)
