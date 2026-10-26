"""Regras do contrato 1.0 (seção 8 da ESPECIFICACAO.md do processamento-tcc)."""
import hashlib
import json
from dataclasses import dataclass

VERSAO = "1.0"
STATUS_VALIDOS = frozenset({"ok", "nao_se_aplica", "indisponivel", "historico_insuficiente", "lucro_negativo"})
STATUS_EXTRAS = {"experimento_top": frozenset({"calibracao_nao_conferida"})}
STATUS_EXECUCAO = frozenset({"em_andamento", "ok", "parcial", "falhou"})
TIPOS_SERIE = ("acao", "etf", "indice", "taxa", "setor_sem_empresa")
METADADOS = ("versao_contrato", "gerado_em", "dados_ate")
CAMPOS_ENVELOPE = ("pacote", "versao_contrato", "chave", "gerado_em", "dados_ate", "registros", "removidos")


@dataclass(frozen=True)
class Pacote:
    nome: str
    chave: tuple[str, ...]
    familia: str  # "foto" | "serie" | "versionada"


PACOTES: dict[str, Pacote] = {
    p.nome: p
    for p in (
        Pacote("acoes", ("ticker",), "versionada"),
        Pacote("mercado_dia", ("data_referencia",), "foto"),
        Pacote("resumo_acao", ("data_referencia", "ticker"), "foto"),
        Pacote("series_diarias", ("ticker", "data"), "serie"),
        Pacote("fundamentos_trimestrais", ("ticker", "data_fim_periodo"), "versionada"),
        Pacote("proventos", ("ticker", "tipo", "data_com"), "versionada"),
        Pacote("noticias_dia", ("data_referencia", "ticker"), "foto"),
        Pacote("experimento_top", ("data_referencia",), "foto"),
        Pacote("experimento_historico", ("data_referencia",), "foto"),
        Pacote("execucao", ("data_referencia", "rodada"), "foto"),
    )
}
FOTOS_DA_COMPLETA = ("mercado_dia", "resumo_acao", "noticias_dia", "experimento_top", "experimento_historico")
VERSIONADOS = ("acoes", "fundamentos_trimestrais", "proventos")


def json_canonico(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha256_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def hash_registro(registro: dict) -> str:
    """Hash do conteúdo, sem os metadados que mudam a cada execução (gerado_em, dados_ate, versao_contrato)."""
    conteudo = {k: v for k, v in registro.items() if k not in METADADOS}
    return sha256_texto(json_canonico(conteudo))


def chave_de(pacote: str, registro: dict) -> tuple:
    return tuple(registro[campo] for campo in PACOTES[pacote].chave)
