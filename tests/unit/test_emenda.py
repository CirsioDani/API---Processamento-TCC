"""Testes da emenda dos pontos antigos (publicador/emenda.py), com dados sintéticos pequenos."""
from datetime import date, timedelta

import pytest

from comum.series import Ponto
from publicador.emenda import analisar


def datas_uteis(n):
    """n datas ISO consecutivas (dias úteis) a partir de 2025-01-01."""
    datas, i = [], 0
    while len(datas) < n:
        d = date(2025, 1, 1) + timedelta(days=i)
        if d.weekday() < 5:
            datas.append(d.isoformat())
        i += 1
    return datas


DATAS = datas_uteis(60)


def ponto(data, valores, status=None):
    return Ponto(data=data, valores=dict(valores), status=dict(status or {}))


def antigos_de(valores_fn, n=40):
    """Banco com as datas 0..n-1."""
    return {DATAS[i]: ponto(DATAS[i], valores_fn(i)) for i in range(n)}


def novos_de(fn, inicio=10, fim=45):
    """Completa nova com as datas inicio..fim-1 (janela de 35 por padrão)."""
    return [ponto(DATAS[i], fn(i)) for i in range(inicio, fim)]


def valores_acao(i):
    return {
        "fechamento": 10.0 + i,
        "volume": 1000.0 + 3 * i,
        "indice_retorno_total": 100.0 + 2 * i,
        "pl": 5.0 + i,
    }


def completa_dividendo(i):
    return valores_acao(i)


def completa_desdobramento(i):
    v = valores_acao(i)
    if i < 25:
        v["fechamento"] *= 0.5
        v["volume"] *= 2
    return v


def completa_ancora(i):
    v = valores_acao(i)
    v["indice_retorno_total"] *= 0.8
    return v


def completa_absurda(i):
    v = valores_acao(i)
    v["fechamento"] *= 141012.0
    return v


FATORES_NAO_CONCORDAM = [0.5, 0.6, 0.7, 0.5, 0.6]


def completa_nao_concorda(i):
    v = valores_acao(i)
    if 10 <= i < 10 + len(FATORES_NAO_CONCORDAM):
        v["fechamento"] *= FATORES_NAO_CONCORDAM[i - 10]
    return v


def valores_setor(i):
    return {"indice_retorno_total": 100.0 + 2 * i, "n_empresas": 20.0}


def completa_setor(i):
    r = 0.9 if i <= 20 else 0.9 + 0.001 * i
    return {
        "indice_retorno_total": (100.0 + 2 * i) * r,
        "n_empresas": 20.0 if i < 21 else 21.0,
    }


def test_dia_de_dividendo_nao_mexe_em_antigos():
    res = analisar("ABCD3", "acao", antigos_de(valores_acao), novos_de(completa_dividendo))
    assert res.ajustes == []
    assert res.fatores_aplicar == {}
    assert res.avisos == []
    assert res.data_corte == DATAS[10]


def test_desdobramento_1_2_vira_evento_e_emenda_os_antigos():
    antigos = antigos_de(valores_acao)
    novos = novos_de(completa_desdobramento)
    res = analisar("ABCD3", "acao", antigos, novos)

    por_campo = {a.campo: a for a in res.ajustes}
    assert set(por_campo) == {"fechamento", "volume"}

    fech = por_campo["fechamento"]
    assert fech.classificacao == "evento"
    assert fech.fator == pytest.approx(0.5)
    assert fech.detalhe["data_evento"] == DATAS[25]
    assert fech.origem == "emenda"
    assert fech.aplicado is True

    vol = por_campo["volume"]
    assert vol.classificacao == "evento"
    assert vol.fator == pytest.approx(2.0)

    assert res.fatores_aplicar == pytest.approx({"fechamento": 0.5, "volume": 2.0})
    assert res.datas_explicadas["fechamento"] == set(DATAS[10:25])

    # sem degrau: o antigo d0-1 ajustado encaixa no primeiro ponto novo
    antigo_9 = antigos[DATAS[9]].valores["fechamento"]
    antigo_10 = antigos[DATAS[10]].valores["fechamento"]
    novo_10 = novos[0].valores["fechamento"]  # novos[0] é a data 10
    assert antigo_9 * 0.5 / novo_10 == pytest.approx(antigo_9 / antigo_10)


def test_mudanca_de_ancora_e_uniforme_com_um_ajuste():
    res = analisar("ABCD3", "acao", antigos_de(valores_acao), novos_de(completa_ancora))
    assert len(res.ajustes) == 1
    a = res.ajustes[0]
    assert a.campo == "indice_retorno_total"
    assert a.classificacao == "uniforme"
    assert a.fator == pytest.approx(0.8)
    assert a.pontos == 10 + 30
    assert a.origem == "emenda"
    assert a.aplicado is True
    assert res.fatores_aplicar == pytest.approx({"indice_retorno_total": 0.8})


def test_fator_absurdo_e_recusado():
    res = analisar("AZUL3", "acao", antigos_de(valores_acao), novos_de(completa_absurda))
    assert len(res.ajustes) == 1
    a = res.ajustes[0]
    assert a.campo == "fechamento"
    assert a.classificacao == "recusado"
    assert a.aplicado is False
    assert a.de == DATAS[0]
    assert a.ate == DATAS[9]
    assert a.pontos == 10
    assert "fechamento" not in res.fatores_aplicar
    assert len(res.avisos) == 1
    assert "recusada" in res.avisos[0]
    assert "AZUL3" in res.avisos[0]


def test_costura_que_nao_concorda_e_recusada():
    res = analisar("ABCD3", "acao", antigos_de(valores_acao), novos_de(completa_nao_concorda))
    assert len(res.ajustes) == 1
    a = res.ajustes[0]
    assert a.campo == "fechamento"
    assert a.classificacao == "recusado"
    assert a.aplicado is False
    assert "fechamento" not in res.fatores_aplicar
    assert len(res.avisos) == 1


def test_setor_sem_empresa_com_composicao_nova_e_irregular_e_aplicada():
    res = analisar(
        "IXSETOR", "setor_sem_empresa",
        antigos_de(valores_setor), novos_de(completa_setor),
    )
    # n_empresas muda na janela, mas não é campo proporcional: não gera Ajuste
    assert len(res.ajustes) == 1
    a = res.ajustes[0]
    assert a.campo == "indice_retorno_total"
    assert a.classificacao == "irregular"
    assert a.aplicado is True
    assert a.fator == pytest.approx(0.9)
    assert res.fatores_aplicar == pytest.approx({"indice_retorno_total": 0.9})
    assert "n_empresas" not in res.fatores_aplicar


def test_republicar_nao_aplica_o_fator_de_novo():
    antigos = antigos_de(valores_acao)
    novos = novos_de(completa_desdobramento)
    res = analisar("ABCD3", "acao", antigos, novos)
    assert res.fatores_aplicar

    # aplica o resultado à mão: antigos < d0 multiplicados, janela recebe os novos
    aplicados = dict(antigos)
    for data, p in antigos.items():
        if data < res.data_corte:
            v = dict(p.valores)
            for campo, k in res.fatores_aplicar.items():
                if v.get(campo) is not None:
                    v[campo] = v[campo] * k
            aplicados[data] = ponto(data, v)
    for p in novos:
        aplicados[p.data] = p

    res2 = analisar("ABCD3", "acao", aplicados, novos)
    assert res2.ajustes == []
    assert res2.fatores_aplicar == {}


def test_serie_sem_antigos_nao_gera_ajuste():
    res = analisar("ABCD3", "acao", {}, novos_de(completa_desdobramento))
    assert res.ajustes == []
    assert res.fatores_aplicar == {}
    assert res.avisos == []


def test_campo_oficial_de_indice_so_avisa():
    def valores(i):
        return {"fechamento": 10.0 + i, "indice_retorno_total": 100.0 + 2 * i}

    def completa(i):
        v = valores(i)
        v["fechamento"] *= 0.5
        return v

    res = analisar("IBOV", "indice", antigos_de(valores), novos_de(completa))
    assert len(res.ajustes) == 1
    a = res.ajustes[0]
    assert a.campo == "fechamento"
    assert a.classificacao == "uniforme"
    assert a.fator == pytest.approx(0.5)
    assert a.aplicado is False
    assert a.detalhe["motivo"] == "campo oficial: só aviso"
    assert "fechamento" not in res.fatores_aplicar
    assert "fechamento" not in res.datas_explicadas
    assert len(res.avisos) == 1


def test_valor_none_e_antigo_zero_nao_entram_na_razao():
    antigos = antigos_de(valores_acao)
    antigos[DATAS[10]] = ponto(DATAS[10], {**valores_acao(10), "fechamento": 0.0})

    def completa(i):
        v = valores_acao(i)
        if i == 10:
            v["fechamento"] = 0.0
        elif i == 11:
            v["fechamento"] = None
        else:
            v["fechamento"] = v["fechamento"] * 0.5
        return v

    res = analisar("ABCD3", "acao", antigos, novos_de(completa))
    assert [a.campo for a in res.ajustes] == ["fechamento"]
    a = res.ajustes[0]
    assert a.fator == pytest.approx(0.5)
    assert a.classificacao == "uniforme"
    assert a.aplicado is True
    assert res.avisos == []


def test_mudanca_so_no_meio_da_janela_nao_gera_ajuste():
    def completa(i):
        v = valores_acao(i)
        if i in (20, 21, 22):
            v["fechamento"] *= 1.1
        return v

    res = analisar("ABCD3", "acao", antigos_de(valores_acao), novos_de(completa))
    assert res.ajustes == []
    assert res.fatores_aplicar == {}
    assert res.datas_explicadas["fechamento"] == set()
    assert res.avisos == []


def test_um_ponto_corrigido_no_comeco_da_janela_nao_recusa():
    """Correção pontual da coleta na costura: a maioria segue com fator 1 → sem emenda e sem recusa."""
    def completa(i):
        v = valores_acao(i)
        if i == 11:
            v["fechamento"] *= 1.07
        return v

    res = analisar("ABCD3", "acao", antigos_de(valores_acao), novos_de(completa))
    assert res.ajustes == []
    assert res.fatores_aplicar == {}
    assert res.avisos == []


def test_indice_com_lacuna_preenchida_so_avisa_e_conta_a_lacuna():
    """Antigo com fechamento null (datas 10..36 da sobreposição) e novo com número: sem fator, o aviso fala de lacuna."""
    def antigos(i):
        v = {"fechamento": 10.0 + i, "indice_retorno_total": 100.0 + 2 * i}
        if 10 <= i <= 36:
            v["fechamento"] = None
        return v

    def completa(i):
        v = {"fechamento": 10.0 + i, "indice_retorno_total": 100.0 + 2 * i}
        if i >= 37:
            v["fechamento"] = (10.0 + i) * 1.05  # razão ≠ 1 só nas 3 últimas datas da sobreposição
        return v

    res = analisar("IBOV", "indice", antigos_de(antigos), novos_de(completa))
    assert len(res.ajustes) == 1
    a = res.ajustes[0]
    assert a.campo == "fechamento"
    assert a.fator is None
    assert a.aplicado is False
    assert a.detalhe["lacuna_preenchida"] == 27  # datas 10..36 da sobreposição: antigo null, novo com número
    assert "fechamento" not in res.fatores_aplicar
    assert len(res.avisos) == 1
    assert "IBOV" in res.avisos[0]
    assert "lacuna preenchida (27 pontos); 3 ponto(s) já existente(s) com valor diferente" in res.avisos[0]
    assert a.detalhe["pontos_diferentes"] == 3
    assert "fator indefinido" not in res.avisos[0]


def test_indice_com_fator_definido_mantem_aviso_de_fator_sem_lacuna():
    def valores(i):
        return {"fechamento": 10.0 + i, "indice_retorno_total": 100.0 + 2 * i}

    def completa(i):
        v = valores(i)
        v["fechamento"] *= 0.5
        return v

    res = analisar("IBOV", "indice", antigos_de(valores), novos_de(completa))
    a = res.ajustes[0]
    assert a.fator == pytest.approx(0.5)
    assert "lacuna_preenchida" not in a.detalhe
    assert len(res.avisos) == 1
    assert "campo oficial mudou (fator 0.5)" in res.avisos[0]
