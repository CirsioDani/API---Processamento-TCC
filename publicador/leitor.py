"""Lê a saída de uma data D do processamento-tcc. Só leitura: nunca escreve nada."""
import hashlib
import json
import re
from datetime import date
from pathlib import Path

from comum import contrato
from publicador.entrada import Entrada

_DATA = re.compile(r"\d{4}-\d{2}-\d{2}")


class ErroEntrada(Exception):
    """Dado ausente, malformado ou data inválida para publicar."""


def _data_valida(data: str) -> bool:
    if not isinstance(data, str) or _DATA.fullmatch(data) is None:
        return False
    try:
        date.fromisoformat(data)
    except ValueError:
        return False
    return True


def _ler_json(caminho: Path) -> tuple[dict, str]:
    """Devolve (conteudo, sha256 dos bytes). Lê em binário: o hash é do arquivo, não do texto."""
    try:
        with caminho.open("rb") as arquivo:
            bruto = arquivo.read()
    except OSError as exc:
        raise ErroEntrada(f"não foi possível ler {caminho}: {exc}") from exc
    try:
        conteudo = json.loads(bruto.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ErroEntrada(f"JSON inválido em {caminho}: {exc}") from exc
    if not isinstance(conteudo, dict):
        raise ErroEntrada(f"{caminho} não é um objeto JSON")
    return conteudo, hashlib.sha256(bruto).hexdigest()


def ler(dir_processamento: Path, data: str) -> Entrada:
    if not _data_valida(data):
        raise ErroEntrada(f"data inválida {data!r}: use AAAA-MM-DD")
    raiz = Path(dir_processamento)
    pasta_saida = raiz / "saida" / data
    arq_execucao = pasta_saida / "execucao.json"
    if not arq_execucao.is_file():
        raise ErroEntrada(f"nada para publicar: {arq_execucao} não existe")

    hashes = {}
    execucao, hash_execucao = _ler_json(arq_execucao)
    hashes[f"saida/{data}/execucao.json"] = hash_execucao

    completa = None
    execucao_completa = None
    pasta_completa = raiz / "saida_completa" / data
    if pasta_completa.is_dir():
        pacotes = {}
        for nome in contrato.PACOTES:
            if nome == "execucao":
                continue
            arq = pasta_completa / f"{nome}.json"
            if arq.is_file():
                envelope, h = _ler_json(arq)
                pacotes[nome] = envelope
                hashes[f"saida_completa/{data}/{nome}.json"] = h
        arq_exec_completa = pasta_completa / "execucao.json"
        if arq_exec_completa.is_file():
            execucao_completa, h = _ler_json(arq_exec_completa)
            hashes[f"saida_completa/{data}/execucao.json"] = h
        completa = pacotes or None

    return Entrada(
        data=data,
        execucao=execucao,
        completa=completa,
        execucao_completa=execucao_completa,
        hashes_arquivos=hashes,
        pasta_completa=str(pasta_completa.resolve()) if completa is not None else None,
        pasta_saida=str(pasta_saida.resolve()),
    )
