"""Catálogo de regras de qualidade de dados sobre as tabelas normalizadas.

Lê ``data/processed/pes.csv`` e ``data/processed/atendimento.csv`` (gerados por
``src.normaliza``), aplica as regras declaradas em ``REGRAS`` e grava em
``data/processed/``:

- ``qualidade_achados.csv``: uma linha por ocorrência encontrada.
- ``qualidade_resumo.csv``: uma linha por regra, com contagens.

Cada regra recebe os DataFrames ``pes`` e ``atendimento`` e devolve as
ocorrências já no formato de achado (ver ``COLUNAS_OCORRENCIA``); o executor
completa regra_id, gravidade e recomendação.

Uso:
    python -m src.qualidade
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

import pandas as pd

logger = logging.getLogger("qualidade")

RAIZ = Path(__file__).resolve().parents[1]
PROCESSED_DIR = RAIZ / "data" / "processed"
ENCODING_CSV = "utf-8-sig"

TODOS_DRS = "TODOS"  # valor de "uf" para achados que valem para todos os DRs do ano
ORDEM_GRAVIDADE = {"alta": 0, "media": 1, "informativa": 2}

COD_EMPRESAS_PES = "6"
ACAO_EMPRESAS_ATENDIMENTO = "QUANTIDADE DE EMPRESAS ATENDIDAS"
ACAO_TOTAL = "TOTAL"

UF_DN = "DN"
UF_CETIQT = "CETIQT"
TIPO_DN = "DN"
TIPO_DR = "DR"
TIPO_CETIQT = "CETIQT"
INDICADORES_CONSOLIDACAO = ["2.1", "2.2", "2.3", "6", "14.2"]
LIMITE_CONSOLIDACAO_PCT = 5.0
PREFIXO_POLARIDADE_POSITIVA = "(+)"  # "(+) Maior, melhor"
TOLERANCIA_SOMA = 0.005  # meio centavo

COLUNAS_OCORRENCIA = [
    "fonte", "ano", "mes_referencia", "uf", "item", "descricao_item", "evidencia",
]
COLUNAS_ACHADOS = [
    "regra_id", "gravidade", "fonte", "ano", "mes_referencia", "periodo_parcial", "uf", "item",
    "descricao_item", "evidencia", "recomendacao",
]
COLUNAS_RESUMO = ["regra_id", "nome", "gravidade", "n_ocorrencias", "n_drs_afetados"]

FuncaoRegra = Callable[[pd.DataFrame, pd.DataFrame], pd.DataFrame]


class Regra(TypedDict):
    """Declaração de uma regra de qualidade."""

    id: str
    nome: str
    gravidade: str
    descricao: str
    recomendacao: str
    funcao: FuncaoRegra


# --------------------------------------------------------------------------
# Conversão de linhas afetadas em ocorrências
# --------------------------------------------------------------------------

def _txt(valor: Any) -> str:
    """Representa um texto original na evidência (vazio vira "<vazio>")."""
    return "<vazio>" if pd.isna(valor) else str(valor)


def ocorrencias_pes(df: pd.DataFrame) -> pd.DataFrame:
    """Converte linhas de ``pes`` em ocorrências, com os textos originais como evidência."""
    out = pd.DataFrame(index=df.index)
    out["fonte"] = "pes"
    out["ano"] = df["ano"]
    out["mes_referencia"] = df["mes_referencia"]
    out["uf"] = df["uf"]
    out["item"] = df["cod_indicador"]
    out["descricao_item"] = df["indicador"]
    out["evidencia"] = [
        f"meta_txt={_txt(m)}; realizado_txt={_txt(r)}; indice_txt={_txt(i)}; unidade={_txt(u)}"
        for m, r, i, u in zip(df["meta_txt"], df["realizado_txt"], df["indice_txt"], df["unidade"])
    ]
    return out.reset_index(drop=True)


def ocorrencias_atendimento(df: pd.DataFrame) -> pd.DataFrame:
    """Converte linhas de ``atendimento`` em ocorrências, com os textos originais como evidência."""
    out = pd.DataFrame(index=df.index)
    out["fonte"] = "atendimento"
    out["ano"] = df["ano"]
    out["mes_referencia"] = df["mes_referencia"]
    out["uf"] = df["uf"]
    out["item"] = df["acao"]
    out["descricao_item"] = df["tema"] + " / " + df["linha_negocio"]
    out["evidencia"] = [
        f"previsto_txt={_txt(p)}; realizado_txt={_txt(r)}; indice_publicado={_txt(i)}"
        for p, r, i in zip(df["previsto_txt"], df["realizado_txt"], df["indice_publicado"])
    ]
    return out.reset_index(drop=True)


def _ambas(pes_afetado: pd.DataFrame, atend_afetado: pd.DataFrame) -> pd.DataFrame:
    """Junta ocorrências das duas fontes em um único DataFrame."""
    return pd.concat(
        [ocorrencias_pes(pes_afetado), ocorrencias_atendimento(atend_afetado)],
        ignore_index=True,
    )


# --------------------------------------------------------------------------
# Regras
# --------------------------------------------------------------------------

def q01_indicador_nao_apurado(pes: pd.DataFrame, atend: pd.DataFrame) -> pd.DataFrame:
    """Indicador sem meta nem realizado em todos os DRs do ano."""
    vazio = pes["meta"].isna() & pes["realizado"].isna()
    grupos = pes.assign(_vazio=vazio).groupby(["ano", "cod_indicador"], sort=True)
    linhas = []
    for (ano, cod), g in grupos:
        if not g["_vazio"].all():
            continue
        linhas.append({
            "fonte": "pes",
            "ano": ano,
            "mes_referencia": g["mes_referencia"].max(),
            "uf": TODOS_DRS,
            "item": cod,
            "descricao_item": g["indicador"].iloc[0],
            "evidencia": (
                f"{g['uf'].nunique()} DRs sem meta nem realizado; "
                f"meta_txt={sorted(map(_txt, g['meta_txt'].unique()))}; "
                f"realizado_txt={sorted(map(_txt, g['realizado_txt'].unique()))}"
            ),
        })
    return pd.DataFrame(linhas, columns=COLUNAS_OCORRENCIA)


def q02_percentual_acima_100(pes: pd.DataFrame, atend: pd.DataFrame) -> pd.DataFrame:
    """Realizado acima de 100 em indicador de unidade percentual."""
    return ocorrencias_pes(pes[(pes["unidade"] == "Percentual (%)") & (pes["realizado"] > 100)])


def q03_absoluto_com_percentual(pes: pd.DataFrame, atend: pd.DataFrame) -> pd.DataFrame:
    """Unidade "Valor absoluto", mas meta_txt ou realizado_txt contém "%"."""
    tem_pct = (
        pes["meta_txt"].str.contains("%", regex=False, na=False)
        | pes["realizado_txt"].str.contains("%", regex=False, na=False)
    )
    return ocorrencias_pes(pes[(pes["unidade"] == "Valor absoluto") & tem_pct])


def q04_divergencia_indice(pes: pd.DataFrame, atend: pd.DataFrame) -> pd.DataFrame:
    """Índice publicado diverge do calculado em mais de 1 p.p."""
    return _ambas(pes[pes["divergencia_indice"]], atend[atend["divergencia_indice"]])


def q05_realizado_negativo(pes: pd.DataFrame, atend: pd.DataFrame) -> pd.DataFrame:
    """Realizado negativo."""
    return _ambas(pes[pes["realizado"] < 0], atend[atend["realizado"] < 0])


def q06_sem_meta(pes: pd.DataFrame, atend: pd.DataFrame) -> pd.DataFrame:
    """Realizado informado sem meta (ou previsto)."""
    return _ambas(pes[pes["status_dado"] == "sem_meta"], atend[atend["status_dado"] == "sem_meta"])


def q07_zero_suspeito(pes: pd.DataFrame, atend: pd.DataFrame) -> pd.DataFrame:
    """Valores "R$ - 0" marcados como zero suspeito."""
    return _ambas(
        pes[pes["status_dado"] == "zero_suspeito"],
        atend[atend["status_dado"] == "zero_suspeito"],
    )


def q08_indice_acima_200(pes: pd.DataFrame, atend: pd.DataFrame) -> pd.DataFrame:
    """Índice de análise acima de 200%.

    Em período parcial (mês < 12), a meta é anual e o realizado é acumulado
    até o mês, então o índice ainda tende a crescer: a evidência avisa disso.
    """
    out = _ambas(pes[pes["indice_analise"] > 200], atend[atend["indice_analise"] > 200])
    parcial = out["mes_referencia"].astype(int) < 12
    out.loc[parcial, "evidencia"] = [
        f"{ev} (acumulado até o mês {mes}; índice tende a crescer até dezembro)"
        for ev, mes in zip(out.loc[parcial, "evidencia"], out.loc[parcial, "mes_referencia"])
    ]
    return out


def q09_entrega_nao_planejada(pes: pd.DataFrame, atend: pd.DataFrame) -> pd.DataFrame:
    """Atendimento com previsto igual a zero e realizado positivo."""
    return ocorrencias_atendimento(atend[(atend["previsto"] == 0) & (atend["realizado"] > 0)])


def q10_empresas_entre_fontes(pes: pd.DataFrame, atend: pd.DataFrame) -> pd.DataFrame:
    """Empresas atendidas: PES indicador 6 x Atendimento, com valores diferentes."""
    p = pes.loc[pes["cod_indicador"] == COD_EMPRESAS_PES,
                ["ano", "uf", "mes_referencia", "realizado", "realizado_txt", "meta_txt"]]
    a = atend.loc[atend["acao"] == ACAO_EMPRESAS_ATENDIMENTO,
                  ["ano", "uf", "realizado", "realizado_txt", "previsto_txt"]]
    m = p.merge(a, on=["ano", "uf"], suffixes=("_pes", "_atend"))
    m = m[m["realizado_pes"].notna() & m["realizado_atend"].notna()
          & (m["realizado_pes"] != m["realizado_atend"])]
    out = pd.DataFrame({
        "fonte": "pes x atendimento",
        "ano": m["ano"],
        "mes_referencia": m["mes_referencia"],
        "uf": m["uf"],
        "item": f"{COD_EMPRESAS_PES} x {ACAO_EMPRESAS_ATENDIMENTO}",
        "descricao_item": "Empresas atendidas (STI) segundo cada fonte",
        "evidencia": [
            f"PES: meta_txt={_txt(mp)}; realizado_txt={_txt(rp)} | "
            f"Atendimento: previsto_txt={_txt(pa)}; realizado_txt={_txt(ra)}"
            for mp, rp, pa, ra in zip(
                m["meta_txt"], m["realizado_txt_pes"], m["previsto_txt"], m["realizado_txt_atend"]
            )
        ],
    }, columns=COLUNAS_OCORRENCIA)
    return out.reset_index(drop=True)


def q11_ano_fechado_sem_realizado(pes: pd.DataFrame, atend: pd.DataFrame) -> pd.DataFrame:
    """Ano fechado (mês 12) com meta definida e realizado vazio."""
    def filtro(df: pd.DataFrame, col_meta: str) -> pd.DataFrame:
        return df[(df["mes_referencia"] == 12) & df[col_meta].notna() & df["realizado"].isna()]

    return _ambas(filtro(pes, "meta"), filtro(atend, "previsto"))


def tipo_unidade(df: pd.DataFrame) -> pd.Series:
    """Tipo de unidade de cada linha ("DN", "DR" ou "CETIQT").

    Usa a coluna ``tipo_unidade`` quando existe; senão, deduz pela ``uf``
    (DN e CETIQT pelo nome; o resto é DR).
    """
    if "tipo_unidade" in df.columns:
        return df["tipo_unidade"]
    return df["uf"].map({UF_DN: TIPO_DN, UF_CETIQT: TIPO_CETIQT}).fillna(TIPO_DR)


def _fmt(valor: float) -> str:
    """Formata número para evidência (sem casas decimais quando inteiro)."""
    return f"{valor:.0f}" if float(valor).is_integer() else f"{valor:.2f}"


def _consolidacao(
    df: pd.DataFrame, fonte: str, chaves: list[str], campos: list[str], item: Any, descricao: Any
) -> pd.DataFrame:
    """Compara, por ``chaves`` e campo, o valor do DN com a soma das demais unidades.

    Células vazias ficam fora da soma (nunca viram zero); a evidência informa
    quantas unidades tinham dado. Só compara quando DN e soma existem e a soma
    é diferente de zero.
    """
    tipo = tipo_unidade(df)
    dn = df[tipo == TIPO_DN]
    partes = df[tipo != TIPO_DN]
    linhas = []
    for chave, g_dn in dn.groupby(chaves, sort=True):
        chave = chave if isinstance(chave, tuple) else (chave,)
        g = partes[(partes[chaves] == pd.Series(chave, index=chaves)).all(axis=1)]
        if g.empty:
            continue
        linha_dn = g_dn.iloc[0]
        for campo in campos:
            valor_dn = linha_dn[campo]
            soma = g[campo].sum(min_count=1)
            if pd.isna(valor_dn) or pd.isna(soma) or soma == 0:
                continue
            dif_pct = (valor_dn - soma) / abs(soma) * 100
            if abs(dif_pct) <= LIMITE_CONSOLIDACAO_PCT:
                continue
            nota = (
                "; total nacional deduplicado (esperado DN < soma)"
                if linha_dn["_empresas"] else ""
            )
            linhas.append({
                "fonte": fonte,
                "ano": linha_dn["ano"],
                "mes_referencia": linha_dn["mes_referencia"],
                "uf": linha_dn["uf"],
                "item": item(linha_dn),
                "descricao_item": descricao(linha_dn),
                "evidencia": (
                    f"{campo}: DN={_fmt(valor_dn)}; soma DRs+CETIQT={_fmt(soma)} "
                    f"({g[campo].notna().sum()} de {len(g)} unidades com dado); "
                    f"diferença={dif_pct:+.1f}%{nota}"
                ),
            })
    return pd.DataFrame(linhas, columns=COLUNAS_OCORRENCIA)


def q12_consolidacao_dn(pes: pd.DataFrame, atend: pd.DataFrame) -> pd.DataFrame:
    """DN difere mais de 5% da soma de DRs + CETIQT (quantidades)."""
    p = pes[pes["cod_indicador"].isin(INDICADORES_CONSOLIDACAO)]
    p = p.assign(_empresas=p["cod_indicador"] == COD_EMPRESAS_PES)
    a = atend.assign(_empresas=atend["acao"] == ACAO_EMPRESAS_ATENDIMENTO)
    return pd.concat([
        _consolidacao(p, "pes", ["ano", "cod_indicador"], ["meta", "realizado"],
                      lambda r: r["cod_indicador"], lambda r: r["indicador"]),
        _consolidacao(a, "atendimento", ["ano", "tema", "linha_negocio", "acao"],
                      ["previsto", "realizado"],
                      lambda r: r["acao"], lambda r: f"{r['tema']} / {r['linha_negocio']}"),
    ], ignore_index=True)


def _meta_abaixo(
    df: pd.DataFrame, fonte: str, chaves: list[str], col_meta: str, item: str, descricao: Any
) -> pd.DataFrame:
    """Meta do ano N menor que o realizado de N-1 (ano N-1 fechado; ambos > 0)."""
    drs = df[tipo_unidade(df) == TIPO_DR]
    atual = drs[drs[col_meta] > 0]
    anterior = drs[(drs["mes_referencia"] == 12) & (drs["realizado"] > 0)]
    anterior = anterior[["ano", "uf", *chaves, "realizado"]].assign(ano=anterior["ano"] + 1)
    m = atual.merge(anterior, on=["ano", "uf", *chaves], suffixes=("", "_ant"))
    m = m[m[col_meta] < m["realizado_ant"]]
    return pd.DataFrame({
        "fonte": fonte,
        "ano": m["ano"],
        "mes_referencia": m["mes_referencia"],
        "uf": m["uf"],
        "item": m[item],
        "descricao_item": descricao(m),
        "evidencia": [
            f"meta {ano}={_fmt(meta)}; realizado {ano - 1}={_fmt(real)}; razão={meta / real:.2f}"
            for ano, meta, real in zip(m["ano"], m[col_meta], m["realizado_ant"])
        ],
    }, columns=COLUNAS_OCORRENCIA).reset_index(drop=True)


def so_polaridade_positiva(pes: pd.DataFrame, contexto: str) -> pd.DataFrame:
    """Mantém só linhas com polaridade "(+) Maior, melhor"; as demais são logadas e ignoradas.

    "Meta abaixo do realizado anterior" só indica meta frouxa quando maior é melhor;
    com outra polaridade (ou vazia) a conclusão sairia invertida.
    """
    pol = pes["polaridade"] if "polaridade" in pes.columns else pd.Series("", index=pes.index)
    positiva = pol.fillna("").astype(str).str.startswith(PREFIXO_POLARIDADE_POSITIVA)
    ignoradas = pes.loc[~positiva].assign(_pol=pol[~positiva].fillna("(vazia)"))
    for (cod, p), g in ignoradas.groupby(["cod_indicador", "_pol"], sort=True):
        logger.warning("%s: indicador %s ignorado (polaridade %r; %d linha(s))",
                       contexto, cod, p, len(g))
    return pes[positiva]


def q13_meta_abaixo_realizado_anterior(pes: pd.DataFrame, atend: pd.DataFrame) -> pd.DataFrame:
    """Meta do ano N menor que o realizado de N-1 no mesmo DR e indicador.

    No PES, só indicadores "(+) Maior, melhor" (ver ``so_polaridade_positiva``).
    O Atendimento não tem polaridade: são volumes (maior, melhor).
    """
    pes = so_polaridade_positiva(pes, "Q13")
    return pd.concat([
        _meta_abaixo(pes, "pes", ["cod_indicador"], "meta", "cod_indicador",
                     lambda m: m["indicador"]),
        _meta_abaixo(atend, "atendimento", ["tema", "linha_negocio", "acao"], "previsto", "acao",
                     lambda m: m["tema"] + " / " + m["linha_negocio"]),
    ], ignore_index=True)


def q14_total_diferente_soma(pes: pd.DataFrame, atend: pd.DataFrame) -> pd.DataFrame:
    """TOTAL da linha de negócio diferente da soma das ações.

    Só compara quando o TOTAL e todas as ações têm valor (vazio não vira zero).
    """
    chaves = ["ano", "uf", "tema", "linha_negocio"]
    eh_total = atend["acao"] == ACAO_TOTAL
    linhas = []
    for chave, g in atend.groupby(chaves, sort=True):
        total = g[eh_total.loc[g.index]]
        acoes = g[~eh_total.loc[g.index]]
        if total.empty or acoes.empty:
            continue
        linha_total = total.iloc[0]
        for campo in ("previsto", "realizado"):
            valor_total = linha_total[campo]
            if pd.isna(valor_total) or acoes[campo].isna().any():
                continue
            soma = acoes[campo].sum()
            if abs(valor_total - soma) <= TOLERANCIA_SOMA:
                continue
            linhas.append({
                "fonte": "atendimento",
                "ano": linha_total["ano"],
                "mes_referencia": linha_total["mes_referencia"],
                "uf": linha_total["uf"],
                "item": ACAO_TOTAL,
                "descricao_item": f"{linha_total['tema']} / {linha_total['linha_negocio']}",
                "evidencia": (
                    f"{campo}: TOTAL={_fmt(valor_total)}; soma das {len(acoes)} ações={_fmt(soma)}; "
                    f"diferença={_fmt(valor_total - soma)}"
                ),
            })
    return pd.DataFrame(linhas, columns=COLUNAS_OCORRENCIA)


REGRAS: list[Regra] = [
    {
        "id": "Q01", "nome": "Indicador não apurado", "gravidade": "alta",
        "descricao": "Indicador do PES sem meta nem realizado em todos os DRs no ano.",
        "recomendacao": "Confirmar com a área responsável se o indicador foi descontinuado, "
                        "ainda não foi apurado ou está com falha de carga no portal.",
        "funcao": q01_indicador_nao_apurado,
    },
    {
        "id": "Q02", "nome": "Percentual acima de 100", "gravidade": "media",
        "descricao": "Realizado acima de 100 em indicador com unidade \"Percentual (%)\".",
        "recomendacao": "Verificar se o indicador é de fato uma proporção (limitada a 100%) "
                        "ou se a unidade está cadastrada incorretamente.",
        "funcao": q02_percentual_acima_100,
    },
    {
        "id": "Q03", "nome": "Unidade absoluta com valor percentual", "gravidade": "informativa",
        "descricao": "Unidade \"Valor absoluto\", mas meta ou realizado publicados com \"%\".",
        "recomendacao": "Corrigir o cadastro da unidade de medida do indicador.",
        "funcao": q03_absoluto_com_percentual,
    },
    {
        "id": "Q04", "nome": "Índice publicado divergente", "gravidade": "media",
        "descricao": "Índice publicado difere do calculado (realizado/meta) em mais de 1 p.p.",
        "recomendacao": "Usar o índice calculado na análise e reportar a divergência à fonte.",
        "funcao": q04_divergencia_indice,
    },
    {
        "id": "Q05", "nome": "Realizado negativo", "gravidade": "informativa",
        "descricao": "Valor realizado negativo.",
        "recomendacao": "Verificar se é retração real (ex.: estorno) ou erro de lançamento.",
        "funcao": q05_realizado_negativo,
    },
    {
        "id": "Q06", "nome": "Realizado sem meta", "gravidade": "media",
        "descricao": "Realizado informado sem meta (ou previsto) pactuada.",
        "recomendacao": "Confirmar se houve pactuação de meta; sem ela o desempenho não é avaliável.",
        "funcao": q06_sem_meta,
    },
    {
        "id": "Q07", "nome": "Zero suspeito", "gravidade": "media",
        "descricao": "Meta/previsto ou realizado publicado como \"R$ - 0\".",
        "recomendacao": "Tratar como possível valor não apurado; confirmar com a fonte antes de usar.",
        "funcao": q07_zero_suspeito,
    },
    {
        "id": "Q08", "nome": "Índice acima de 200%", "gravidade": "media",
        "descricao": "Índice de análise acima de 200%.",
        "recomendacao": "Avaliar se a meta está subcalibrada e revisar o planejamento.",
        "funcao": q08_indice_acima_200,
    },
    {
        "id": "Q09", "nome": "Entrega não planejada", "gravidade": "media",
        "descricao": "Atendimento com previsto igual a zero e realizado positivo.",
        "recomendacao": "Verificar se a ação deveria ter previsto ou se houve remanejamento.",
        "funcao": q09_entrega_nao_planejada,
    },
    {
        "id": "Q10", "nome": "Empresas atendidas diferem entre fontes", "gravidade": "informativa",
        "descricao": "PES indicador 6 (empresas industriais atendidas por STI) e Atendimento "
                     "(QUANTIDADE DE EMPRESAS ATENDIDAS) com números diferentes no mesmo ano e DR.",
        "recomendacao": "As definições são distintas; harmonizar o glossário e citar a fonte "
                        "ao usar cada número.",
        "funcao": q10_empresas_entre_fontes,
    },
    {
        "id": "Q11", "nome": "Ano fechado sem realizado", "gravidade": "media",
        "descricao": "Ano fechado (mês de referência 12) com meta definida e realizado vazio.",
        "recomendacao": "Cobrar a apuração do realizado junto ao DR.",
        "funcao": q11_ano_fechado_sem_realizado,
    },
    {
        "id": "Q12", "nome": "DN difere da soma das unidades", "gravidade": "informativa",
        "descricao": "Valor do DN difere mais de 5% da soma de DRs + SENAI CETIQT (PES 2.1, 2.2, "
                     "2.3, 6 e 14.2; todas as ações do Atendimento). Para empresas atendidas, "
                     "o total nacional é deduplicado e o esperado é DN < soma.",
        "recomendacao": "Conferir a regra de consolidação nacional antes de comparar DN com DRs.",
        "funcao": q12_consolidacao_dn,
    },
    {
        "id": "Q13", "nome": "Meta abaixo do realizado anterior", "gravidade": "media",
        "descricao": "Meta (ou previsto) do ano N menor que o realizado de N-1 no mesmo DR e "
                     "indicador (ambos > 0; só tipo_unidade DR).",
        "recomendacao": "meta abaixo do já entregue: revisar calibração ou registrar justificativa",
        "funcao": q13_meta_abaixo_realizado_anterior,
    },
    {
        "id": "Q14", "nome": "TOTAL diferente da soma das ações", "gravidade": "alta",
        "descricao": "No Atendimento, o TOTAL da linha de negócio difere da soma das suas ações.",
        "recomendacao": "Guarda de integridade: verificar a carga/normalização antes de usar o TOTAL.",
        "funcao": q14_total_diferente_soma,
    },
]


# --------------------------------------------------------------------------
# Execução
# --------------------------------------------------------------------------

def carregar_tabela(nome: str) -> pd.DataFrame:
    """Lê uma tabela de ``data/processed`` preservando os textos originais.

    Só células vazias viram NaN ("-" e "N/A" continuam como texto).
    """
    return pd.read_csv(
        PROCESSED_DIR / f"{nome}.csv",
        encoding=ENCODING_CSV,
        dtype={"cod_indicador": str},
        keep_default_na=False,
        na_values=[""],
    )


def aplicar_regras(
    pes: pd.DataFrame, atend: pd.DataFrame, regras: list[Regra] = REGRAS
) -> pd.DataFrame:
    """Aplica as regras e retorna todos os achados, ordenados por gravidade."""
    partes = []
    for regra in regras:
        ocorrencias = regra["funcao"](pes, atend)
        if ocorrencias.empty:
            continue
        partes.append(ocorrencias.assign(
            regra_id=regra["id"],
            gravidade=regra["gravidade"],
            recomendacao=regra["recomendacao"],
        ))
    if not partes:
        return pd.DataFrame(columns=COLUNAS_ACHADOS)
    achados = pd.concat(partes, ignore_index=True)
    achados["periodo_parcial"] = achados["mes_referencia"].astype(int) < 12
    achados = achados[COLUNAS_ACHADOS]
    return (
        achados.assign(_ordem=achados["gravidade"].map(ORDEM_GRAVIDADE))
        .sort_values(["_ordem", "regra_id", "ano", "uf", "item"], kind="stable")
        .drop(columns="_ordem")
        .reset_index(drop=True)
    )


def resumir(
    achados: pd.DataFrame, pes: pd.DataFrame, regras: list[Regra] = REGRAS
) -> pd.DataFrame:
    """Conta ocorrências e DRs afetados por regra (inclui regras sem ocorrência).

    Achados com ``uf == "TODOS"`` contam todos os DRs presentes no PES naquele ano.
    """
    drs_por_ano = pes.groupby("ano")["uf"].agg(set).to_dict()
    linhas = []
    for regra in regras:
        da_regra = achados[achados["regra_id"] == regra["id"]]
        drs: set[str] = set()
        for ano, uf in zip(da_regra["ano"], da_regra["uf"]):
            drs |= drs_por_ano.get(ano, set()) if uf == TODOS_DRS else {uf}
        linhas.append({
            "regra_id": regra["id"],
            "nome": regra["nome"],
            "gravidade": regra["gravidade"],
            "n_ocorrencias": len(da_regra),
            "n_drs_afetados": len(drs),
        })
    return pd.DataFrame(linhas, columns=COLUNAS_RESUMO)


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada da linha de comando."""
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    pes = carregar_tabela("pes")
    atend = carregar_tabela("atendimento")

    achados = aplicar_regras(pes, atend)
    resumo = resumir(achados, pes)

    achados.to_csv(PROCESSED_DIR / "qualidade_achados.csv", index=False, encoding=ENCODING_CSV)
    resumo.to_csv(PROCESSED_DIR / "qualidade_resumo.csv", index=False, encoding=ENCODING_CSV)

    linhas_log = "\n".join(
        f"  {r.regra_id} [{r.gravidade:<11}] {r.nome:<42} "
        f"ocorrências={r.n_ocorrencias:<4} DRs={r.n_drs_afetados}"
        for r in resumo.itertuples()
    )
    logger.info("Resumo de qualidade (%d achados):\n%s", len(achados), linhas_log)
    logger.info("Arquivos gravados em %s", PROCESSED_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
