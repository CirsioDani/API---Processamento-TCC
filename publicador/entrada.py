"""O que o publicador leu de uma data D (estrutura compartilhada por leitor, validador e planejador)."""
from dataclasses import dataclass, field


@dataclass
class Entrada:
    data: str                                   # D (nome da pasta)
    execucao: dict                              # envelope de saida/D/execucao.json
    completa: dict[str, dict] | None = None     # pacote -> envelope; None se não há carga completa
    execucao_completa: dict | None = None       # envelope de saida_completa/D/execucao.json
    hashes_arquivos: dict[str, str] = field(default_factory=dict)
    pasta_completa: str | None = None
    pasta_saida: str = ""
