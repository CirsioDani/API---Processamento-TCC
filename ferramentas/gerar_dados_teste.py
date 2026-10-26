"""Gera dados de teste determinísticos a partir do processamento-tcc.

Gera em --destino (padrão: tests/dados) a mesma estrutura de pastas do
processamento:

A) saida_completa/2026-10-23/  recorte real (PETR4, VALE3, ITUB4 + séries de apoio)
B) saida/2026-10-23/           execucao.json real, cópia exata
C) saida_completa/2026-10-26/  derivado de A, simulando a completa do dia seguinte
D) saida/2026-10-26/           derivado de B, execução do dia 26
E) saida/2026-10-27/           execução do dia 27 que falhou (sem completa)

Uso (a partir da pasta api-tcc):
    ./.venv/Scripts/python ferramentas/gerar_dados_teste.py

Apaga e recria somente o destino. Usa apenas a biblioteca padrão.
"""

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path

RAIZ_API = Path(__file__).resolve().parents[1]
ORIGEM_PADRAO = RAIZ_API.parent / "processamento-tcc"
DESTINO_PADRAO = RAIZ_API / "tests" / "dados"

DATA_PREGAO = "2026-10-23"
DATA_D = "2026-10-26"
DATA_E = "2026-10-27"
GERADO_EM_D = "2026-10-26T12:40:00-03:00"
DADOS_ATE_D = "2026-10-26T08:00:00-03:00"

TICKERS_RECORTE = {"PETR4", "VALE3", "ITUB4"}
SERIES_RECORTE = {
    "PETR4",
    "VALE3",
    "ITUB4",
    "SETOR_SEM:PETR4",
    "SETOR_SEM:VALE3",
    "SETOR_SEM:ITUB4",
    "CDI",
    "IBOV",
}
PACOTES_COMPLETA = [
    "acoes",
    "mercado_dia",
    "resumo_acao",
    "series_diarias",
    "fundamentos_trimestrais",
    "proventos",
    "noticias_dia",
]
CAMPOS_RETORNO = {"fechamento", "indice_retorno_total"}


def ler_json(caminho):
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def escrever_json(caminho, objeto, indent=1):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", encoding="utf-8", newline="\n") as f:
        json.dump(objeto, f, ensure_ascii=False, indent=indent)


def envelope_com(envelope_origem, registros):
    saida = dict(envelope_origem)
    saida["registros"] = registros
    return saida


def recortar_completa(origem_completa):
    """Pacotes de A: só os tickers pedidos, mantendo os envelopes originais."""
    pacotes = {}
    for nome in PACOTES_COMPLETA:
        env = ler_json(origem_completa / f"{nome}.json")
        regs = env["registros"]
        if nome == "mercado_dia":
            filtrados = regs
        elif nome == "series_diarias":
            filtrados = [r for r in regs if r["ticker"] in SERIES_RECORTE]
        else:
            filtrados = [r for r in regs if r["ticker"] in TICKERS_RECORTE]
        if nome == "noticias_dia":
            filtrados = copy.deepcopy(filtrados)
            for r in filtrados:
                r["itens"] = r["itens"][:5]
        pacotes[nome] = envelope_com(env, filtrados)
    return pacotes


def execucao_com_contagens(execucao_origem, pacotes):
    """Copia a execução ajustando pacotes_enviados às contagens dos pacotes."""
    saida = copy.deepcopy(execucao_origem)
    for r in saida["registros"]:
        enviados = []
        for p in r["pacotes_enviados"]:
            if p["pacote"] not in pacotes:
                continue
            p = dict(p)
            p["registros"] = len(pacotes[p["pacote"]]["registros"])
            enviados.append(p)
        r["pacotes_enviados"] = enviados
    return saida


def derivar_serie(reg):
    """Série de C: remove a 1ª data, acrescenta PREGAO e ajusta valores e status."""
    r = copy.deepcopy(reg)
    datas = r["datas"]
    removida, nova_primeira, antiga_ultima = datas[0], datas[1], datas[-1]

    nulos = set()
    for campo, trechos in r["status"].items():
        novos = []
        for t in trechos:
            if t["de"] == removida and t["ate"] == removida:
                continue
            t = dict(t)
            if t["de"] == removida:
                t["de"] = nova_primeira
            if t["ate"] == antiga_ultima:
                t["ate"] = DATA_PREGAO
                nulos.add(campo)
            novos.append(t)
        r["status"][campo] = novos

    for campo, valores in r["valores"].items():
        ultimo = valores[-1]
        if campo in nulos or ultimo is None:
            novo = None
        elif campo in CAMPOS_RETORNO:
            novo = ultimo * 1.002
        else:
            novo = ultimo
        r["valores"][campo] = valores[1:] + [novo]

    r["datas"] = datas[1:] + [DATA_PREGAO]
    r["inicio_janela"] = nova_primeira

    if r["ticker"] == "PETR4":
        r["valores"]["volume"][100] = r["valores"]["volume"][100] * 1.1

    r["gerado_em"] = GERADO_EM_D
    r["dados_ate"] = DADOS_ATE_D
    return r


def derivar_completa_dia_seguinte(pacotes_a):
    """Pacotes de C, derivados de A."""
    pacotes = {}
    for nome, env in pacotes_a.items():
        regs = copy.deepcopy(env["registros"])

        if nome == "series_diarias":
            regs = [derivar_serie(r) for r in regs]

        elif nome == "resumo_acao":
            for r in regs:
                r["data_referencia"] = DATA_PREGAO
                if r["ticker"] == "PETR4":
                    r["pl"]["valor"] = r["pl"]["valor"] * 1.01

        elif nome == "noticias_dia":
            for r in regs:
                r["data_referencia"] = DATA_PREGAO

        elif nome == "mercado_dia":
            for r in regs:
                r["data_referencia"] = DATA_PREGAO

        elif nome == "fundamentos_trimestrais":
            vale3 = [r for r in regs if r["ticker"] == "VALE3"]
            mais_recente = max(vale3, key=lambda r: r["data_fim_periodo"])
            mais_recente["receita_trimestre"]["valor"] = (
                mais_recente["receita_trimestre"]["valor"] * 1.05
            )

        elif nome == "proventos":
            itub4 = [r for r in regs if r["ticker"] == "ITUB4"]
            mais_antigo = min(itub4, key=lambda r: r["data_com"])
            regs = [r for r in regs if r is not mais_antigo]

        for r in regs:
            r["gerado_em"] = GERADO_EM_D
            r["dados_ate"] = DADOS_ATE_D

        novo = envelope_com(env, regs)
        novo["gerado_em"] = GERADO_EM_D
        novo["dados_ate"] = DADOS_ATE_D
        pacotes[nome] = novo
    return pacotes


def execucao_derivada(execucao_origem, data_referencia):
    saida = copy.deepcopy(execucao_origem)
    saida["gerado_em"] = GERADO_EM_D
    saida["dados_ate"] = DADOS_ATE_D
    for r in saida["registros"]:
        r["data_referencia"] = data_referencia
        r["gerado_em"] = GERADO_EM_D
        r["dados_ate"] = DADOS_ATE_D
    return saida


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--origem", type=Path, default=ORIGEM_PADRAO)
    parser.add_argument("--destino", type=Path, default=DESTINO_PADRAO)
    args = parser.parse_args()

    origem = args.origem.resolve()
    destino = args.destino.resolve()
    if destino == origem or origem.is_relative_to(destino):
        print("Erro: o destino não pode conter nem ser a pasta de origem.", file=sys.stderr)
        return 1

    origem_completa = origem / "saida_completa" / DATA_PREGAO
    origem_execucao = origem / "saida" / DATA_PREGAO / "execucao.json"

    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True)

    escritos = []

    def gravar(caminho, objeto, indent=1):
        escrever_json(caminho, objeto, indent=indent)
        escritos.append(caminho)

    def gravar_pacotes(pasta, pacotes):
        for nome, env in pacotes.items():
            indent = None if nome == "series_diarias" else 1
            gravar(pasta / f"{nome}.json", env, indent=indent)

    # A: completa de 2026-10-23 (recorte real)
    pacotes_a = recortar_completa(origem_completa)
    exec_a_origem = ler_json(origem_completa / "execucao.json")
    exec_a = execucao_com_contagens(exec_a_origem, pacotes_a)
    pasta_a = destino / "saida_completa" / DATA_PREGAO
    gravar_pacotes(pasta_a, pacotes_a)
    gravar(pasta_a / "execucao.json", exec_a)

    # B: execução real de 2026-10-23, cópia exata
    pasta_b = destino / "saida" / DATA_PREGAO
    pasta_b.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(origem_execucao, pasta_b / "execucao.json")
    escritos.append(pasta_b / "execucao.json")
    exec_b = ler_json(origem_execucao)

    # C: completa de 2026-10-26, derivada de A
    pacotes_c = derivar_completa_dia_seguinte(pacotes_a)
    exec_c = execucao_com_contagens(exec_a_origem, pacotes_c)
    exec_c = execucao_derivada(exec_c, DATA_D)
    pasta_c = destino / "saida_completa" / DATA_D
    gravar_pacotes(pasta_c, pacotes_c)
    gravar(pasta_c / "execucao.json", exec_c)

    # D: execução de 2026-10-26, derivada de B
    exec_d = execucao_derivada(exec_b, DATA_D)
    for r in exec_d["registros"]:
        r["data_referencia"] = DATA_D
        r["inicio"] = DATA_D + r["inicio"][10:]
        r["fim"] = DATA_D + r["fim"][10:]
        r["status"] = "ok"
    gravar(destino / "saida" / DATA_D / "execucao.json", exec_d)

    # E: execução de 2026-10-27 que falhou, sem completa
    exec_e = copy.deepcopy(exec_b)
    for r in exec_e["registros"]:
        r["data_referencia"] = DATA_E
        r["status"] = "falhou"
        r["pacotes_enviados"] = []
    gravar(destino / "saida" / DATA_E / "execucao.json", exec_e)

    print(f"Origem: {origem}")
    print(f"Destino: {destino}")
    total = 0
    for caminho in escritos:
        tamanho = caminho.stat().st_size
        total += tamanho
        print(f"  {caminho.relative_to(destino)}  {tamanho} bytes")
    print(f"Total: {total} bytes em {len(escritos)} arquivos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
