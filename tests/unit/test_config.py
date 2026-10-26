from pathlib import Path

import pytest

from comum.config import ErroConfig, carregar

BASE = {
    "NOME_BANCO": "tcc_processador",
    "IP_BANCO": "10.0.0.5",
    "USER_BANCO": "usuario_base",
    "SENHA_BANCO": "senha_base_123",
}


def _ambiente(mudancas: dict) -> dict:
    """Copia BASE aplicando mudanças; valor None remove a chave."""
    env = dict(BASE)
    for chave, valor in mudancas.items():
        if valor is None:
            env.pop(chave, None)
        else:
            env[chave] = valor
    return env


def test_ambiente_completo_carrega():
    cfg = carregar(_ambiente({}))
    assert cfg.banco == "tcc_processador"
    assert cfg.host == "10.0.0.5"
    assert cfg.porta == 3306
    assert cfg.prefixo == ""


@pytest.mark.parametrize("nome", ["tcc_scraper", "TCC_PROCESSADOR", "", None])
def test_recusa_nome_de_banco_que_nao_e_o_permitido(nome):
    with pytest.raises(ErroConfig):
        carregar(_ambiente({"NOME_BANCO": nome}))


def test_recusa_ip_ausente():
    with pytest.raises(ErroConfig):
        carregar(_ambiente({"IP_BANCO": None}))


def test_fallback_escrita_quando_ausentes():
    cfg = carregar(_ambiente({}))
    assert cfg.escrita.usuario == "usuario_base"
    assert cfg.escrita.senha == "senha_base_123"


@pytest.mark.parametrize(
    "mudancas",
    [
        {"USER_ESCRITA": "u_esc"},
        {"SENHA_ESCRITA": "s_esc"},
    ],
    ids=["so_usuario", "so_senha"],
)
def test_fallback_escrita_quando_so_um_dos_dois_preenchido(mudancas):
    cfg = carregar(_ambiente(mudancas))
    assert cfg.escrita.usuario == "usuario_base"
    assert cfg.escrita.senha == "senha_base_123"


def test_escrita_usa_user_escrita_quando_os_dois_preenchidos():
    cfg = carregar(_ambiente({"USER_ESCRITA": "u_esc", "SENHA_ESCRITA": "s_esc"}))
    assert cfg.escrita.usuario == "u_esc"
    assert cfg.escrita.senha == "s_esc"


@pytest.mark.parametrize(
    "mudancas",
    [
        {},
        {"USER_LEITURA": "u_lei"},
        {"SENHA_LEITURA": "s_lei"},
    ],
    ids=["ausentes", "so_usuario", "so_senha"],
)
def test_fallback_leitura(mudancas):
    cfg = carregar(_ambiente(mudancas))
    assert cfg.leitura.usuario == "usuario_base"
    assert cfg.leitura.senha == "senha_base_123"


def test_leitura_usa_user_leitura_quando_os_dois_preenchidos():
    cfg = carregar(_ambiente({"USER_LEITURA": "u_lei", "SENHA_LEITURA": "s_lei"}))
    assert cfg.leitura.usuario == "u_lei"
    assert cfg.leitura.senha == "s_lei"


def test_usuario_final_vazio_recusa():
    with pytest.raises(ErroConfig):
        carregar(_ambiente({"USER_BANCO": "", "USER_ESCRITA": None}))


@pytest.mark.parametrize("prefixo", ["", "teste_"])
def test_prefixos_validos(prefixo):
    cfg = carregar(_ambiente({"PROC_API_PREFIXO_TABELAS": prefixo}))
    assert cfg.prefixo == prefixo


def test_prefixo_invalido_recusa():
    with pytest.raises(ErroConfig):
        carregar(_ambiente({"PROC_API_PREFIXO_TABELAS": "outro_"}))


def test_porta_padrao_3306():
    assert carregar(_ambiente({"PORTA_BANCO": None})).porta == 3306


def test_porta_informada_vira_inteiro():
    assert carregar(_ambiente({"PORTA_BANCO": "3307"})).porta == 3307


def test_chaves_e_cors_separados_por_virgula_sem_vazios():
    cfg = carregar(
        _ambiente(
            {
                "PROC_API_CHAVES": " chave_a , chave_b ,, chave_c ",
                "PROC_API_CORS_ORIGENS": "http://a.test,http://b.test, ",
            }
        )
    )
    assert cfg.chaves_api == ("chave_a", "chave_b", "chave_c")
    assert cfg.cors_origens == ("http://a.test", "http://b.test")


def test_chaves_e_cors_padrao_vazios():
    cfg = carregar(_ambiente({}))
    assert cfg.chaves_api == ()
    assert cfg.cors_origens == ()


def test_repr_nao_contem_senha():
    cfg = carregar(_ambiente({"SENHA_ESCRITA": "segredo_escrita_xyz", "SENHA_LEITURA": "segredo_leitura_xyz"}))
    texto = repr(cfg)
    assert "segredo_escrita_xyz" not in texto
    assert "segredo_leitura_xyz" not in texto
    assert "senha_base_123" not in texto


def test_dir_processamento_padrao():
    cfg = carregar(_ambiente({}))
    assert Path(cfg.dir_processamento).name == "processamento-tcc"
