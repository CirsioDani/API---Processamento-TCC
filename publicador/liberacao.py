"""Liberação das recusas já gravadas (lógica pura: sem SQL, sem I/O).

Uma recusa é a linha de series_ajustes com classificacao='recusado': a API esconde os pontos com data <= ate enquanto
ela existir. Para SETOR_SEM (decisão de 09/11/2026) a recusa passa a ser substituição inteira: os pontos antigos
(de <= data <= ate, com valor no campo) são multiplicados pelo fator da recusa, que é a razão do 1º ponto da costura
(novo / antigo). Depois a linha vira 'irregular' com aplicado=1. Quem grava é gravador.gravar_liberacoes.
"""
from dataclasses import dataclass

from comum import series
from comum.series import Ponto
from publicador import emenda, planejador
from publicador.operacoes import PontoAtualizar

TIPOS_LIBERAVEIS = ("setor_sem_empresa",)
MOTIVO_PADRAO = "SETOR_SEM passou a ser substituição inteira (decisão de 09/11/2026)"
FATOR_ALERTA_MIN, FATOR_ALERTA_MAX = 0.5, 2.0
REGRA = "setor_sem_empresa: substituição inteira; antigos encadeados pelo fator da recusa"


class ErroLiberacao(Exception):
    """A recusa não bate com os pontos guardados (a contagem de pontos antigos mudou). Nada é liberado."""


@dataclass(frozen=True)
class Recusa:
    id: int; ticker: str; campo: str; data_publicacao: str; fator: float | None
    de: str | None; ate: str | None; pontos: int; detalhe: dict  # de/ate/pontos = os pontos antigos da recusa


@dataclass
class Liberacao:
    recusa: Recusa
    operacoes: list               # um PontoAtualizar por ponto antigo
    detalhe_novo: dict            # detalhe da recusa + chave "liberacao"
    costura_antes: float | None   # v(ate) / v(1º ponto com valor depois de ate); None se não há como calcular
    costura_depois: float | None  # costura_antes × fator
    alerta: bool                  # fator fora de [FATOR_ALERTA_MIN; FATOR_ALERTA_MAX]


def planejar(recusas: list[Recusa], tipos: dict[str, str], pontos: dict[str, dict[str, Ponto]], motivo: str,
             agora: str) -> tuple[list[Liberacao], list[tuple[Recusa, str]]]:
    """Liberações possíveis e as recusas ignoradas (com o motivo em texto). Contagem de pontos que não bate:
    levanta ErroLiberacao e nada é devolvido."""
    liberacoes: list[Liberacao] = []
    ignoradas: list[tuple[Recusa, str]] = []
    for recusa in recusas:
        motivo_ignorada = _motivo_ignorada(recusa, tipos, pontos)
        if motivo_ignorada is not None:
            ignoradas.append((recusa, motivo_ignorada))
        else:
            liberacoes.append(_liberar(recusa, pontos.get(recusa.ticker, {}), motivo, agora))
    return liberacoes, ignoradas


def _motivo_ignorada(recusa: Recusa, tipos: dict[str, str], pontos: dict[str, dict[str, Ponto]]) -> str | None:
    tipo = tipos.get(recusa.ticker)
    if tipo not in TIPOS_LIBERAVEIS:
        return f"tipo {tipo!r} não é liberável"
    if recusa.campo not in emenda.CAMPOS_PROPORCIONAIS.get(tipo, ()):
        return f"campo {recusa.campo!r} não é proporcional no tipo {tipo!r}"
    if recusa.fator is None or recusa.fator <= 0:
        return "fator ausente ou não positivo"
    if recusa.de is None or recusa.ate is None:
        return "recusa sem período (de/ate)"
    if not _antigos(recusa, pontos.get(recusa.ticker, {})):
        return "nenhum ponto com valor no campo entre de e ate"
    return None


def _antigos(recusa: Recusa, pontos_ticker: dict[str, Ponto]) -> list[Ponto]:
    """Pontos do ticker com de <= data <= ate e valor no campo, por data."""
    return [p for p in sorted(pontos_ticker.values(), key=lambda p: p.data)
            if recusa.de <= p.data <= recusa.ate and p.valores.get(recusa.campo) is not None]


def _liberar(recusa: Recusa, pontos_ticker: dict[str, Ponto], motivo: str, agora: str) -> Liberacao:
    antigos = _antigos(recusa, pontos_ticker)
    if len(antigos) != recusa.pontos:
        raise ErroLiberacao(
            f"{recusa.ticker}: a recusa registra {recusa.pontos} pontos antigos, mas há {len(antigos)} "
            f"com valor entre {recusa.de} e {recusa.ate}")
    fatores = {recusa.campo: recusa.fator}
    operacoes = []
    for antigo in antigos:
        emendado = planejador._ponto_emendado(antigo, fatores)
        operacoes.append(PontoAtualizar(ticker=recusa.ticker, ponto=emendado, hash=series.hash_ponto(emendado),
                                        anterior=antigo, hash_anterior=series.hash_ponto(antigo)))
    antes, depois = _costura(recusa, pontos_ticker)
    detalhe = {**recusa.detalhe, "liberacao": {
        "em": agora,
        "motivo": motivo,
        "classificacao_anterior": "recusado",
        "aplicado_anterior": False,
        "fator_aplicado": recusa.fator,
        "pontos_emendados": len(operacoes),
        "regra": REGRA,
    }}
    alerta = not FATOR_ALERTA_MIN <= recusa.fator <= FATOR_ALERTA_MAX
    return Liberacao(recusa=recusa, operacoes=operacoes, detalhe_novo=detalhe,
                     costura_antes=antes, costura_depois=depois, alerta=alerta)


def _costura(recusa: Recusa, pontos_ticker: dict[str, Ponto]) -> tuple[float | None, float | None]:
    """costura_antes = v(ate) / v(1º ponto com valor depois de ate), no campo.
    costura_depois = costura_antes × fator."""
    campo = recusa.campo
    no_ate = pontos_ticker.get(recusa.ate)
    depois = sorted((p for p in pontos_ticker.values() if p.data > recusa.ate and p.valores.get(campo) is not None),
                    key=lambda p: p.data)
    if no_ate is None or no_ate.valores.get(campo) is None or not depois or depois[0].valores[campo] == 0:
        return None, None
    antes = no_ate.valores[campo] / depois[0].valores[campo]
    return antes, antes * recusa.fator
