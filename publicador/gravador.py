"""Gravação no banco: cria as tabelas, carrega o Estado e executa as operações do Plano numa transação.

Cada operação vira comandos SQL por funções puras (`comandos_da_operacao` e `comando_publicacao`), que dá para
testar sem banco. Todo SQL passa por Conexao, que chama a guarda (comum/guarda_sql.py).
"""
import json
import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from comum import config, contrato, tabelas
from comum.banco import Conexao, ler_json
from comum.series import Ponto
from publicador.entrada import Entrada
from publicador.operacoes import (
    AjusteRegistrar,
    CatalogoGravar,
    Estado,
    FotoAtualizar,
    FotoInserir,
    Plano,
    PontoAtualizar,
    PontoInserir,
    PontoReabrir,
    PontoRemover,
    Versao,
    VersaoAntecipar,
    VersaoCorrigir,
    VersaoFechar,
    VersaoInserir,
    VersaoReabrir,
)

TAMANHO_LOTE = 1000
HASH_REMOVIDO = "0" * 64
_NOME_CRIADO = re.compile(r"^CREATE TABLE IF NOT EXISTS\s+(\S+)")


# ---------------------------------------------------------------------------------------------------------------
# Esquema
# ---------------------------------------------------------------------------------------------------------------

def comandos_do_esquema(prefixo: str) -> list[str]:
    """Os comandos de sql/esquema.sql, com {p} trocado pelo prefixo. Linhas de comentário (--) saem."""
    tabelas.validar_prefixo(prefixo)
    texto = (config.RAIZ / "sql" / "esquema.sql").read_text(encoding="utf-8")
    sem_comentarios = "\n".join(linha for linha in texto.splitlines() if not linha.strip().startswith("--"))
    comandos = []
    for bloco in sem_comentarios.split(";"):
        comando = bloco.replace("{p}", prefixo).strip()
        if comando:
            comandos.append(comando)
    return comandos


def criar_tabelas(conexao: Conexao) -> list[str]:
    """Executa o esquema (CREATE TABLE IF NOT EXISTS: não apaga nada). Devolve os nomes reais das tabelas."""
    nomes = []
    for comando in comandos_do_esquema(conexao.prefixo):
        conexao.executar(comando)
        nomes.append(_NOME_CRIADO.match(comando).group(1))
    conexao.confirmar()
    return nomes


def contar_tabelas(conexao: Conexao) -> dict[str, int]:
    """Número de linhas de cada uma das 14 tabelas, pelo nome real (com o prefixo)."""
    contagens = {}
    for base in tabelas.NOMES_BASE:
        nome = conexao.t(base)
        linhas = conexao.consultar(f"SELECT COUNT(*) AS n FROM {nome}")
        contagens[nome] = linhas[0]["n"]
    return contagens


# ---------------------------------------------------------------------------------------------------------------
# Montagem do SQL (funções puras: devolvem (sql, params))
# ---------------------------------------------------------------------------------------------------------------

def _json(valor: Any) -> str:
    return json.dumps(valor, ensure_ascii=False)


def _do_banco(valor: Any) -> Any:
    """Converte datas do pymysql (datetime.date) para o texto ISO usado no código."""
    if isinstance(valor, date):
        return valor.isoformat()
    return valor


def _insert(tabela: str, colunas: list[str], valores: list, final: str = "") -> tuple[str, tuple]:
    marcas = ", ".join(["%s"] * len(colunas))
    lista_colunas = ", ".join(colunas)
    sql = f"INSERT INTO {tabela} ({lista_colunas}) VALUES ({marcas}){final}"
    return sql, tuple(valores)


def _correcao(t: Callable[[str], str], tabela: str, chave: dict, hash_anterior: str,
              conteudo_anterior: Any, hash_novo: str, data: str) -> tuple[str, tuple]:
    colunas = ["tabela", "chave", "data_troca", "hash_anterior", "conteudo_anterior", "hash_novo"]
    valores = [tabela, _json(chave), data, hash_anterior, _json(conteudo_anterior), hash_novo]
    return _insert(t("correcoes"), colunas, valores)


def _conteudo_ponto(ponto: Ponto) -> dict:
    return {"valores": ponto.valores, "status": ponto.status}


def _foto_inserir(op: FotoInserir, data: str, t: Callable[[str], str]) -> tuple[str, tuple]:
    colunas = list(op.chave)
    valores = list(op.chave.values())
    if op.pacote == "execucao":
        colunas.append("status")
        valores.append(op.registro["status"])
    colunas += ["data_publicacao", "hash", "registro"]
    valores += [data, op.hash, _json(op.registro)]
    return _insert(t(op.pacote), colunas, valores)


def _foto_atualizar(op: FotoAtualizar, data: str, t: Callable[[str], str]) -> list[tuple[str, tuple]]:
    atribuicoes = ["hash = %s", "registro = %s"]
    valores: list = [op.hash, _json(op.registro)]
    if op.pacote == "execucao":
        atribuicoes.append("status = %s")
        valores.append(op.registro["status"])
    condicoes = [f"{campo} = %s" for campo in op.chave]
    valores += list(op.chave.values())
    lista_atribuicoes = ", ".join(atribuicoes)
    lista_condicoes = " AND ".join(condicoes)
    sql = f"UPDATE {t(op.pacote)} SET {lista_atribuicoes} WHERE {lista_condicoes}"
    correcao = _correcao(t, op.pacote, op.chave, op.hash_anterior, op.registro_anterior, op.hash, data)
    return [(sql, tuple(valores)), correcao]


def _ponto_inserir(op: PontoInserir, data: str, t: Callable[[str], str]) -> tuple[str, tuple]:
    colunas = ["ticker", "data", "valores", "status", "hash", "data_publicacao"]
    valores = [op.ticker, op.ponto.data, _json(op.ponto.valores), _json(op.ponto.status), op.hash, data]
    return _insert(t("series_pontos"), colunas, valores)


_ATUALIZA_PONTO = "ON DUPLICATE KEY UPDATE valores = VALUES(valores), status = VALUES(status), hash = VALUES(hash)"


def _ponto_atualizar(op: PontoAtualizar, data: str, t: Callable[[str], str]) -> list[tuple[str, tuple]]:
    # INSERT ... ON DUPLICATE KEY UPDATE (o ponto sempre existe) em vez de UPDATE: assim vai em lote. A
    # data_publicacao do INSERT nunca é usada, porque o UPDATE não a altera.
    colunas = ["ticker", "data", "valores", "status", "hash", "data_publicacao"]
    valores = [op.ticker, op.ponto.data, _json(op.ponto.valores), _json(op.ponto.status), op.hash, data]
    comandos = [_insert(t("series_pontos"), colunas, valores, " " + _ATUALIZA_PONTO)]
    if op.anterior is not None:
        comandos.append(_correcao(t, "series_pontos", {"ticker": op.ticker, "data": op.ponto.data},
                                  op.hash_anterior, _conteudo_ponto(op.anterior), op.hash, data))
    return comandos


def _ponto_remover(op: PontoRemover, data: str, t: Callable[[str], str]) -> list[tuple[str, tuple]]:
    sql = f"UPDATE {t('series_pontos')} SET removido_em = %s WHERE ticker = %s AND data = %s"
    comandos = [(sql, (data, op.ticker, op.data))]
    comandos.append(_correcao(t, "series_pontos", {"ticker": op.ticker, "data": op.data},
                              op.hash_anterior, _conteudo_ponto(op.anterior), HASH_REMOVIDO, data))
    return comandos


def _ponto_reabrir(op: PontoReabrir, data: str, t: Callable[[str], str]) -> list[tuple[str, tuple]]:
    """Ponto removido que voltou: grava os valores novos, limpa removido_em e registra a volta em correcoes
    (hash_anterior = HASH_REMOVIDO, conteúdo anterior = o ponto removido)."""
    sql = (f"UPDATE {t('series_pontos')} SET valores = %s, status = %s, hash = %s, removido_em = NULL "
           "WHERE ticker = %s AND data = %s")
    params = (_json(op.ponto.valores), _json(op.ponto.status), op.hash, op.ticker, op.ponto.data)
    correcao = _correcao(t, "series_pontos", {"ticker": op.ticker, "data": op.ponto.data},
                         HASH_REMOVIDO, _conteudo_ponto(op.anterior), op.hash, data)
    return [(sql, params), correcao]


_ATUALIZA_CATALOGO = (
    "ON DUPLICATE KEY UPDATE tipo = VALUES(tipo), nome = VALUES(nome), campos = VALUES(campos), "
    "primeira_publicacao = LEAST(primeira_publicacao, VALUES(primeira_publicacao)), "
    "ultima_publicacao = GREATEST(ultima_publicacao, VALUES(ultima_publicacao))"
)


def _catalogo_gravar(op: CatalogoGravar, data: str, t: Callable[[str], str]) -> tuple[str, tuple]:
    colunas = ["ticker", "tipo", "nome", "campos", "primeira_publicacao", "ultima_publicacao"]
    valores = [op.ticker, op.tipo, op.nome, _json(op.campos), data, data]
    return _insert(t("series_catalogo"), colunas, valores, " " + _ATUALIZA_CATALOGO)


_COLUNAS_AJUSTE = ["ticker", "campo", "data_publicacao", "fator", "de", "ate", "pontos", "origem",
                   "classificacao", "aplicado", "detalhe"]


def _ajuste_registrar(op: AjusteRegistrar, data: str, t: Callable[[str], str]) -> tuple[str, tuple]:
    a = op.ajuste
    valores = [op.ticker, a.campo, data, a.fator, a.de, a.ate, a.pontos, a.origem,
               a.classificacao, int(a.aplicado), _json(a.detalhe)]
    atualiza = ", ".join(f"{coluna} = VALUES({coluna})" for coluna in _COLUNAS_AJUSTE[3:])
    return _insert(t("series_ajustes"), _COLUNAS_AJUSTE, valores, f" ON DUPLICATE KEY UPDATE {atualiza}")


def _versao_inserir(op: VersaoInserir, t: Callable[[str], str]) -> tuple[str, tuple]:
    colunas = list(op.chave) + ["valido_desde", "valido_ate", "hash", "registro"]
    valores = list(op.chave.values()) + [op.valido_desde, op.valido_ate, op.hash, _json(op.registro)]
    return _insert(t(op.pacote), colunas, valores)


def _versao_fechar(op: VersaoFechar, t: Callable[[str], str]) -> tuple[str, tuple]:
    return f"UPDATE {t(op.pacote)} SET valido_ate = %s WHERE id = %s", (op.valido_ate, op.id)


def _versao_corrigir(op: VersaoCorrigir, data: str, t: Callable[[str], str]) -> list[tuple[str, tuple]]:
    sql = f"UPDATE {t(op.pacote)} SET hash = %s, registro = %s WHERE id = %s"
    comandos = [(sql, (op.hash, _json(op.registro), op.id))]
    comandos.append(_correcao(t, op.pacote, op.chave, op.hash_anterior, op.registro_anterior, op.hash, data))
    return comandos


def _versao_reabrir(op: VersaoReabrir, t: Callable[[str], str]) -> tuple[str, tuple]:
    return f"UPDATE {t(op.pacote)} SET valido_ate = NULL WHERE id = %s", (op.id,)


def _versao_antecipar(op: VersaoAntecipar, t: Callable[[str], str]) -> tuple[str, tuple]:
    return f"UPDATE {t(op.pacote)} SET valido_desde = %s WHERE id = %s", (op.valido_desde, op.id)


def comandos_da_operacao(op: Any, data: str, t: Callable[[str], str]) -> list[tuple[str, tuple]]:
    """Todos os (sql, params) de uma operação, na ordem em que devem ser executados.

    `data` é o D da publicação; `t` é Conexao.t (nome base -> nome real com prefixo).
    """
    if isinstance(op, FotoInserir):
        return [_foto_inserir(op, data, t)]
    if isinstance(op, FotoAtualizar):
        return _foto_atualizar(op, data, t)
    if isinstance(op, PontoInserir):
        return [_ponto_inserir(op, data, t)]
    if isinstance(op, PontoAtualizar):
        return _ponto_atualizar(op, data, t)
    if isinstance(op, PontoRemover):
        return _ponto_remover(op, data, t)
    if isinstance(op, PontoReabrir):
        return _ponto_reabrir(op, data, t)
    if isinstance(op, CatalogoGravar):
        return [_catalogo_gravar(op, data, t)]
    if isinstance(op, AjusteRegistrar):
        return [_ajuste_registrar(op, data, t)]
    if isinstance(op, VersaoInserir):
        return [_versao_inserir(op, t)]
    if isinstance(op, VersaoFechar):
        return [_versao_fechar(op, t)]
    if isinstance(op, VersaoCorrigir):
        return _versao_corrigir(op, data, t)
    if isinstance(op, VersaoReabrir):
        return [_versao_reabrir(op, t)]
    if isinstance(op, VersaoAntecipar):
        return [_versao_antecipar(op, t)]
    raise TypeError(f"operação desconhecida: {type(op).__name__}")


_ATUALIZA_PUBLICACAO = (
    "ON DUPLICATE KEY UPDATE status = IF(status = 'completa', 'completa', VALUES(status)), "
    "data_referencia = COALESCE(VALUES(data_referencia), data_referencia), "
    "metadados = COALESCE(VALUES(metadados), metadados), "
    "pasta_completa = COALESCE(VALUES(pasta_completa), pasta_completa), "
    "pasta_saida = VALUES(pasta_saida), "
    "hashes_arquivos = VALUES(hashes_arquivos), "
    "contagens_novas = VALUES(contagens_novas), "
    "execucao_status = VALUES(execucao_status), "
    "erros = VALUES(erros), "
    "inicio = VALUES(inicio), "
    "fim = VALUES(fim), "
    "vezes_publicada = vezes_publicada + 1"
)


def comando_publicacao(t: Callable[[str], str], *, data: str, status: str, data_referencia: str | None,
                       pasta_completa: str | None, pasta_saida: str, hashes_arquivos: dict,
                       contagens_novas: dict, execucao_status: str | None, erros: list[str] | None,
                       metadados: dict | None, inicio: datetime, fim: datetime) -> tuple[str, tuple]:
    """Upsert da linha de publicacoes. `status` vem na primeira atribuição do UPDATE (ver _ATUALIZA_PUBLICACAO).

    metadados vazio ({} ou None) não sobrescreve os já gravados (COALESCE).
    """
    colunas = ["data_publicacao", "status", "data_referencia", "pasta_completa", "pasta_saida",
               "hashes_arquivos", "contagens_novas", "execucao_status", "erros", "metadados",
               "inicio", "fim", "vezes_publicada"]
    valores = [
        data,
        status,
        data_referencia,
        pasta_completa,
        pasta_saida,
        _json(hashes_arquivos),
        _json(contagens_novas),
        execucao_status,
        None if erros is None else _json(erros),
        _json(metadados) if metadados else None,
        inicio,
        fim,
    ]
    tabela = t("publicacoes")
    marcas = ", ".join(["%s"] * len(valores))
    lista_colunas = ", ".join(colunas[:-1])
    sql = (f"INSERT INTO {tabela} ({lista_colunas}, vezes_publicada) VALUES ({marcas}, 1) "
           f"{_ATUALIZA_PUBLICACAO}")
    return sql, tuple(valores)


def _status_execucao(entrada: Entrada) -> str | None:
    """Status do último registro de execucao da entrada (o que vai para publicacoes.execucao_status)."""
    registros = entrada.execucao.get("registros") if isinstance(entrada.execucao, dict) else None
    if isinstance(registros, list) and registros and isinstance(registros[-1], dict):
        return registros[-1].get("status")
    return None


# ---------------------------------------------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------------------------------------------

def _executar_em_lotes(conexao: Conexao, comandos: list[tuple[str, tuple]]) -> None:
    """INSERTs consecutivos com o mesmo SQL vão em executar_muitos, em lotes de até TAMANHO_LOTE.
    Os demais comandos são executados um a um, na ordem."""
    i = 0
    while i < len(comandos):
        sql, params = comandos[i]
        if not sql.startswith("INSERT INTO"):
            conexao.executar(sql, params)
            i += 1
            continue
        lote = []
        while i < len(comandos) and comandos[i][0] == sql and len(lote) < TAMANHO_LOTE:
            lote.append(comandos[i][1])
            i += 1
        conexao.executar_muitos(sql, lote)


def ordenar_para_lotes(comandos: list[tuple[str, tuple]], t: Callable[[str], str]) -> list[tuple[str, tuple]]:
    """Reordena para que os INSERTs de pontos e de correcoes fiquem juntos e vão em lote.

    Fase 0: todo o resto, na ordem original (fotos e versões dependem da ordem). Fase 1: inserções e atualizações
    de pontos, agrupadas pelo SQL (cada uma toca um (ticker, data) diferente, então a ordem entre elas não importa).
    Fase 2: as linhas de correcoes, que nenhum outro comando lê. Tudo continua na mesma transação.
    """
    pontos = f"INSERT INTO {t('series_pontos')} "
    correcoes = f"INSERT INTO {t('correcoes')} "

    def fase(comando: tuple[str, tuple]) -> tuple[int, str]:
        sql = comando[0]
        if sql.startswith(pontos):
            return 1, sql
        if sql.startswith(correcoes):
            return 2, ""
        return 0, ""

    return sorted(comandos, key=fase)  # sorted é estável: dentro de cada grupo, a ordem original fica


def _em_transacao(conexao: Conexao, operacao: Callable[[], None]) -> None:
    conexao.iniciar()
    try:
        operacao()
    except BaseException:
        conexao.desfazer()
        raise
    conexao.confirmar()


def gravar(conexao: Conexao, entrada: Entrada, plano: Plano, inicio: datetime) -> None:
    """Uma publicação = uma transação: operações, depois o upsert de publicacoes. Erro → desfaz e relança."""

    def executar() -> None:
        comandos: list[tuple[str, tuple]] = []
        for op in plano.operacoes:
            comandos.extend(comandos_da_operacao(op, plano.data, conexao.t))
        _executar_em_lotes(conexao, ordenar_para_lotes(comandos, conexao.t))
        sql, params = comando_publicacao(
            conexao.t,
            data=plano.data,
            status=plano.status_publicacao,
            data_referencia=plano.data_referencia,
            pasta_completa=entrada.pasta_completa,
            pasta_saida=entrada.pasta_saida,
            hashes_arquivos=entrada.hashes_arquivos,
            contagens_novas=plano.contagens(),
            execucao_status=_status_execucao(entrada),
            erros=None,
            metadados=plano.metadados,
            inicio=inicio,
            fim=datetime.now(),
        )
        conexao.executar(sql, params)

    _em_transacao(conexao, executar)


def registrar_rejeicao(conexao: Conexao, entrada: Entrada, erros: list[str], inicio: datetime) -> None:
    """Grava só a linha de publicacoes com status 'rejeitada' (transação própria)."""

    def executar() -> None:
        sql, params = comando_publicacao(
            conexao.t,
            data=entrada.data,
            status="rejeitada",
            data_referencia=None,
            pasta_completa=entrada.pasta_completa,
            pasta_saida=entrada.pasta_saida,
            hashes_arquivos=entrada.hashes_arquivos,
            contagens_novas={},
            execucao_status=_status_execucao(entrada),
            erros=list(erros),
            metadados=None,
            inicio=inicio,
            fim=datetime.now(),
        )
        conexao.executar(sql, params)

    _em_transacao(conexao, executar)


# ---------------------------------------------------------------------------------------------------------------
# Carga do Estado
# ---------------------------------------------------------------------------------------------------------------

def _datas_de_referencia(entrada: Entrada) -> list[str]:
    """data_referencia de todas as fotos da entrada (execucao inclusive), sem repetição, em ordem."""
    datas = set()
    for pacote in contrato.FOTOS_DA_COMPLETA:
        envelope = (entrada.completa or {}).get(pacote)
        if envelope:
            for registro in envelope.get("registros", []):
                datas.add(registro["data_referencia"])
    for registro in entrada.execucao.get("registros", []):
        datas.add(registro["data_referencia"])
    return sorted(datas)


def _tickers_da_completa(entrada: Entrada) -> list[str]:
    envelope = (entrada.completa or {}).get("series_diarias")
    if not envelope:
        return []
    return sorted({registro["ticker"] for registro in envelope.get("registros", [])})


def carregar_estado(conexao: Conexao, entrada: Entrada) -> Estado:
    estado = Estado()

    for linha in conexao.consultar(f"SELECT data_publicacao, status FROM {conexao.t('publicacoes')}"):
        estado.publicacoes[_do_banco(linha["data_publicacao"])] = linha["status"]

    estado.fotos = {pacote: {} for pacote in tabelas.FOTOS}
    datas = _datas_de_referencia(entrada)
    if datas:
        marcas = ", ".join(["%s"] * len(datas))
        for pacote in tabelas.FOTOS:
            chave = contrato.PACOTES[pacote].chave
            colunas = ", ".join(chave)
            sql = (f"SELECT {colunas}, hash, registro FROM {conexao.t(pacote)} "
                   f"WHERE data_referencia IN ({marcas})")
            for linha in conexao.consultar(sql, tuple(datas)):
                valor_chave = tuple(_do_banco(linha[campo]) for campo in chave)
                estado.fotos[pacote][valor_chave] = (linha["hash"], ler_json(linha["registro"]))

    tickers = _tickers_da_completa(entrada)
    if tickers:
        marcas = ", ".join(["%s"] * len(tickers))
        sql = (f"SELECT ticker, data, valores, status FROM {conexao.t('series_pontos')} "
               f"WHERE removido_em IS NULL AND ticker IN ({marcas})")
        for linha in conexao.consultar(sql, tuple(tickers)):
            ponto = Ponto(
                data=_do_banco(linha["data"]),
                valores=ler_json(linha["valores"]),
                status=ler_json(linha["status"]) or {},
            )
            estado.pontos.setdefault(linha["ticker"], {})[ponto.data] = ponto
        sql = (f"SELECT ticker, data, valores, status FROM {conexao.t('series_pontos')} "
               f"WHERE removido_em IS NOT NULL AND ticker IN ({marcas})")
        for linha in conexao.consultar(sql, tuple(tickers)):
            ponto = Ponto(
                data=_do_banco(linha["data"]),
                valores=ler_json(linha["valores"]),
                status=ler_json(linha["status"]) or {},
            )
            estado.pontos_removidos.setdefault(linha["ticker"], {})[ponto.data] = ponto

    sql = f"SELECT ticker, tipo, nome, campos FROM {conexao.t('series_catalogo')}"
    for linha in conexao.consultar(sql):
        estado.catalogo[linha["ticker"]] = {
            "tipo": linha["tipo"],
            "nome": linha["nome"],
            "campos": ler_json(linha["campos"]),
        }

    for pacote in tabelas.VERSIONADAS:
        chave = contrato.PACOTES[pacote].chave
        colunas = ", ".join(chave)
        sql = (f"SELECT {colunas}, id, valido_desde, valido_ate, hash, registro FROM {conexao.t(pacote)} "
               "ORDER BY valido_desde, id")
        versoes: dict[tuple, list[Versao]] = {}
        for linha in conexao.consultar(sql):
            valor_chave = tuple(_do_banco(linha[campo]) for campo in chave)
            versoes.setdefault(valor_chave, []).append(Versao(
                id=linha["id"],
                chave=valor_chave,
                valido_desde=_do_banco(linha["valido_desde"]),
                valido_ate=_do_banco(linha["valido_ate"]),
                hash=linha["hash"],
                registro=ler_json(linha["registro"]),
            ))
        estado.versoes[pacote] = versoes

    return estado
