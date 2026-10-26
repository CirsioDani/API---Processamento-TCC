"""Texto do resumo gerado a partir de um Plano montado à mão (sem banco)."""
from comum.series import Ponto
from publicador import resumo
from publicador.operacoes import (
    CatalogoGravar,
    FotoInserir,
    Plano,
    PontoInserir,
    VersaoFechar,
    VersaoInserir,
)

HASH = "c" * 64


def _ponto(i):
    return Ponto(data=f"2026-01-{i:05d}", valores={"fechamento": 1.0}, status={})


def _plano_normal(**extra):
    operacoes = [PontoInserir("PETR4", _ponto(i), HASH) for i in range(44671)]
    operacoes += [CatalogoGravar(f"S{i}", "acao", f"Empresa {i}", ["fechamento"]) for i in range(3)]
    operacoes += [
        FotoInserir("resumo_acao", {"data_referencia": "2026-10-22", "ticker": "PETR4"}, HASH, {"pl": 5}),
        FotoInserir("resumo_acao", {"data_referencia": "2026-10-22", "ticker": "VALE3"}, HASH, {"pl": 6}),
        FotoInserir("execucao", {"data_referencia": "2026-10-23", "rodada": 1}, HASH, {"status": "ok"}),
        FotoInserir("experimento_top", {"data_referencia": "2026-10-22"}, HASH, {"ranking": []}),
    ]
    metadados = {"experimento_top": {"versao_contrato": "1.0"}}
    return Plano(data="2026-10-23", modo="normal", status_publicacao="completa", data_referencia="2026-10-22",
                 metadados=metadados, operacoes=operacoes, **extra)


def test_linha_principal_tem_data_brasileira_tempo_e_contagens():
    texto = resumo.formatar(_plano_normal(), 4.2)
    linhas = texto.splitlines()
    assert linhas[0] == "23/10/2026 publicado (modo normal, execução: ok) em 4,2 s"
    assert "44.671 pontos novos (3 séries)" in texto
    assert "resumo_acao" in texto and "2 novos" in texto
    assert "execucao" in texto
    assert "top 5 (experimento_top)" in texto and "1 novo" in texto
    assert "não publicado" not in texto


def test_top_5_ausente_aparece_como_nao_publicado():
    plano = _plano_normal()
    plano.operacoes = [op for op in plano.operacoes if not (isinstance(op, FotoInserir) and op.pacote == "experimento_top")]
    plano.metadados = {}
    texto = resumo.formatar(plano, 1.0)
    assert "top 5" in texto
    assert "não publicado (pacote ausente)" in texto


def test_top_5_nao_aparece_como_ausente_quando_metadados_tem_o_pacote():
    plano = _plano_normal()
    plano.operacoes = [op for op in plano.operacoes if not (isinstance(op, FotoInserir) and op.pacote == "experimento_top")]
    texto = resumo.formatar(plano, 1.0)
    assert "não publicado" not in texto


def test_so_execucao_gera_a_frase_de_so_execucao():
    plano = Plano(data="2026-10-23", modo="so_execucao", status_publicacao="so_execucao", data_referencia=None,
                  operacoes=[FotoInserir("execucao", {"data_referencia": "2026-10-23", "rodada": 1}, HASH,
                                         {"status": "falhou"})])
    texto = resumo.formatar(plano, 0.5)
    assert texto.splitlines()[0] == "23/10/2026: só a execução foi gravada (sem carga completa)"
    assert "execucao" in texto
    assert "top 5" not in texto


def test_republicacao_sem_nada_novo_diz_idempotente():
    plano = Plano(data="2026-10-23", modo="republicacao", status_publicacao="completa",
                  data_referencia="2026-10-22", metadados={"experimento_top": {}})
    texto = resumo.formatar(plano, 0.3)
    assert "nada novo (republicação idempotente)" in texto
    assert texto.splitlines()[0] == "23/10/2026 publicado (modo republicacao, execução: inalterada) em 0,3 s"


def test_versionadas_e_avisos_aparecem():
    plano = Plano(data="2026-10-23", modo="normal", status_publicacao="completa", data_referencia="2026-10-22",
                  metadados={"experimento_top": {}}, avisos=["série PETR4, campo fechamento: emenda recusada"])
    plano.operacoes = [
        VersaoInserir("acoes", {"ticker": "PETR4"}, "2026-10-23", None, HASH, {"nome": "PETR4"}),
        VersaoFechar("acoes", 3, {"ticker": "VALE3"}, "2026-10-23"),
    ]
    texto = resumo.formatar(plano, 2.0)
    assert "acoes" in texto
    assert "1 versões novas, 1 fechadas, 0 corrigidas" in texto
    assert "Avisos:" in texto
    assert "  - série PETR4, campo fechamento: emenda recusada" in texto


def test_numeros_usam_ponto_de_milhar_e_tempo_com_virgula():
    texto = resumo.formatar(_plano_normal(), 12.34)
    assert "em 12,3 s" in texto.splitlines()[0]
