"""Configuração da API. Lê o ambiente (e o .env da raiz, via python-dotenv) e valida tudo na partida.

Nenhuma mensagem de erro traz senha.
"""
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from comum import guarda_sql, tabelas

RAIZ = Path(__file__).resolve().parent.parent


class ErroConfig(Exception):
    """Configuração ausente ou inválida."""


@dataclass(frozen=True)
class Credenciais:
    usuario: str
    senha: str = field(repr=False)


@dataclass(frozen=True)
class Config:
    host: str
    porta: int
    banco: str
    escrita: Credenciais
    leitura: Credenciais
    prefixo: str
    dir_processamento: Path
    chaves_api: tuple[str, ...]
    cors_origens: tuple[str, ...]


def _ler(env: Mapping[str, str], nome: str) -> str:
    return (env.get(nome) or "").strip()


def _lista(valor: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in valor.split(",") if item.strip())


def _credenciais(env: Mapping[str, str], perfil: str) -> Credenciais:
    """Usa USER_/SENHA_ do perfil se os dois estiverem preenchidos; senão cai em USER_BANCO/SENHA_BANCO."""
    usuario = _ler(env, f"USER_{perfil}")
    senha = _ler(env, f"SENHA_{perfil}")
    if not (usuario and senha):
        usuario = _ler(env, "USER_BANCO")
        senha = _ler(env, "SENHA_BANCO")
    if not usuario:
        raise ErroConfig(f"usuário do banco ({perfil.lower()}) não definido")
    return Credenciais(usuario=usuario, senha=senha)


def carregar(ambiente: Mapping[str, str] | None = None) -> Config:
    if ambiente is None:
        load_dotenv(RAIZ / ".env", override=False)
        env: Mapping[str, str] = os.environ
    else:
        env = ambiente

    banco = env.get("NOME_BANCO")
    if banco != guarda_sql.BANCO_PERMITIDO:
        raise ErroConfig(f"NOME_BANCO deve ser exatamente {guarda_sql.BANCO_PERMITIDO!r}")

    host = _ler(env, "IP_BANCO")
    if not host:
        raise ErroConfig("IP_BANCO não definido")
    try:
        porta = int(_ler(env, "PORTA_BANCO") or "3306")
    except ValueError:
        raise ErroConfig("PORTA_BANCO deve ser um número inteiro") from None

    try:
        prefixo = tabelas.validar_prefixo(_ler(env, "PROC_API_PREFIXO_TABELAS"))
    except ValueError as erro:
        raise ErroConfig(str(erro)) from None

    dir_texto = _ler(env, "PROC_DIR_PROCESSAMENTO")
    dir_processamento = Path(dir_texto) if dir_texto else RAIZ.parent / "processamento-tcc"

    return Config(
        host=host,
        porta=porta,
        banco=banco,
        escrita=_credenciais(env, "ESCRITA"),
        leitura=_credenciais(env, "LEITURA"),
        prefixo=prefixo,
        dir_processamento=dir_processamento,
        chaves_api=_lista(_ler(env, "PROC_API_CHAVES")),
        cors_origens=_lista(_ler(env, "PROC_API_CORS_ORIGENS")),
    )
