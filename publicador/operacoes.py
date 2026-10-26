"""Estruturas compartilhadas por planejador (lógica pura) e gravador (SQL).

O planejador compara a Entrada com o Estado do banco e produz um Plano: uma lista de operações. O gravador
executa as operações numa transação. `chave` é sempre um dict campo -> valor, na ordem da chave do contrato.
"""
from collections import Counter
from dataclasses import dataclass, field

from comum.series import Ponto
from publicador.emenda import Ajuste


@dataclass
class Versao:
    id: int | None
    chave: tuple
    valido_desde: str
    valido_ate: str | None
    hash: str
    registro: dict


@dataclass
class Estado:
    publicacoes: dict[str, str] = field(default_factory=dict)                      # D -> status
    fotos: dict[str, dict[tuple, tuple[str, dict]]] = field(default_factory=dict)  # pacote -> chave -> (hash, registro)
    pontos: dict[str, dict[str, Ponto]] = field(default_factory=dict)              # ticker -> data -> Ponto (não removidos)
    pontos_removidos: dict[str, dict[str, Ponto]] = field(default_factory=dict)    # ticker -> data -> Ponto (removido_em preenchido)
    catalogo: dict[str, dict] = field(default_factory=dict)                        # ticker -> {tipo, nome, campos}
    versoes: dict[str, dict[tuple, list[Versao]]] = field(default_factory=dict)    # pacote -> chave -> versões
    recusas: dict[str, dict[str, str]] = field(default_factory=dict)               # ticker -> campo -> ate


@dataclass(frozen=True)
class FotoInserir:
    pacote: str; chave: dict; hash: str; registro: dict


@dataclass(frozen=True)
class FotoAtualizar:
    pacote: str; chave: dict; hash: str; registro: dict; hash_anterior: str; registro_anterior: dict


@dataclass(frozen=True)
class PontoInserir:
    ticker: str; ponto: Ponto; hash: str


@dataclass(frozen=True)
class PontoAtualizar:
    ticker: str; ponto: Ponto; hash: str; anterior: Ponto | None; hash_anterior: str  # anterior None = sem correcoes
    ruido: bool = False  # mudança só de ruído numérico (< emenda.TOLERANCIA): grava o valor novo, sem correcoes


@dataclass(frozen=True)
class PontoRemover:
    ticker: str; data: str; anterior: Ponto; hash_anterior: str


@dataclass(frozen=True)
class PontoReabrir:
    ticker: str; ponto: Ponto; hash: str; anterior: Ponto; hash_anterior: str  # anterior = o ponto removido


@dataclass(frozen=True)
class AjusteRegistrar:
    ticker: str; ajuste: Ajuste


@dataclass(frozen=True)
class CatalogoGravar:
    ticker: str; tipo: str; nome: str; campos: list


@dataclass(frozen=True)
class VersaoInserir:
    pacote: str; chave: dict; valido_desde: str; valido_ate: str | None; hash: str; registro: dict


@dataclass(frozen=True)
class VersaoFechar:
    pacote: str; id: int; chave: dict; valido_ate: str


@dataclass(frozen=True)
class VersaoCorrigir:
    pacote: str; id: int; chave: dict; hash: str; registro: dict; hash_anterior: str; registro_anterior: dict


@dataclass(frozen=True)
class VersaoReabrir:
    pacote: str; id: int; chave: dict


@dataclass(frozen=True)
class VersaoAntecipar:
    pacote: str; id: int; chave: dict; valido_desde: str


@dataclass
class Plano:
    data: str
    modo: str                       # "normal" | "republicacao" | "buraco" | "so_execucao"
    status_publicacao: str          # "completa" | "so_execucao"
    data_referencia: str | None
    metadados: dict = field(default_factory=dict)
    operacoes: list = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    execucao_status: str | None = None  # status da rotina daquela manhã (último registro de saida/D/execucao.json)

    def contagens(self) -> dict[str, int]:
        contador: Counter = Counter()
        for op in self.operacoes:
            if isinstance(op, FotoInserir):
                contador[op.pacote] += 1
            elif isinstance(op, FotoAtualizar):
                contador[f"{op.pacote}_atualizadas"] += 1
            elif isinstance(op, PontoInserir):
                contador["series_pontos"] += 1
            elif isinstance(op, PontoAtualizar):
                contador["series_pontos_atualizados"] += 1
                if op.ruido:
                    contador["series_pontos_ruido"] += 1
            elif isinstance(op, PontoRemover):
                contador["series_pontos_removidos"] += 1
            elif isinstance(op, PontoReabrir):
                contador["series_pontos_reabertos"] += 1
            elif isinstance(op, AjusteRegistrar):
                contador["series_recusas" if op.ajuste.classificacao == "recusado" else "series_ajustes"] += 1
            elif isinstance(op, VersaoInserir):
                contador[op.pacote] += 1
            elif isinstance(op, VersaoFechar):
                contador[f"{op.pacote}_fechadas"] += 1
            elif isinstance(op, VersaoCorrigir):
                contador[f"{op.pacote}_corrigidas"] += 1
        return {rotulo: n for rotulo, n in contador.items() if n > 0}
