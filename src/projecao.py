"""Projeção linear de 2026 (parcial, até setembro) para quantidades acumuladas.

Lê ``data/processed/pes.csv`` e ``data/processed/atendimento.csv`` e gera, em
``data/processed/``:

1. ``projecao_2026.csv``: ritmo atual, projeção para dezembro, ritmo necessário
   e situação por linha (DRs + DN + CETIQT).
2. ``projecao_resumo.csv``: por indicador, nº de DRs (só tipo_unidade DR) em cada situação + situação do DN.

Escopo (somente quantidades acumuladas):
- PES: ``cod_indicador`` em {2.1, 2.2, 2.3, 6, 14.2}, ``status_dado == "ok"`` e meta > 0.
- Atendimento: "Programas (matrículas)"/TOTAL e "QUANTIDADE DE EMPRESAS ATENDIDAS",
  ``status_dado == "ok"`` e previsto > 0 (o previsto faz o papel de meta).

Método: linear (realizado/m*12), sem sazonalidade. Rotulado na coluna ``metodo``.
O mês m é derivado do dado (valor único de ``mes_referencia`` no ano); ``--mes``
serve apenas para conferir.

Uso:
    python -m src.projecao [--ano 2026] [--mes 9]
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger("projecao")

RAIZ = Path(__file__).resolve().parents[1]
PROCESSED_DIR = RAIZ / "data" / "processed"
ENCODING_CSV = "utf-8-sig"

ANO_PADRAO = 2026
UF_DN = "DN"
TIPO_DR = "DR"  # só DRs entram nas contagens (DN e CETIQT ficam fora)

CODS_PES = ("2.1", "2.2", "2.3", "6", "14.2")
LINHA_MATRICULAS = "Programas (matrículas)"
ACAO_TOTAL = "TOTAL"
ACAO_EMPRESAS = "QUANTIDADE DE EMPRESAS ATENDIDAS"
COD_AT_MATRICULAS = "AT-matriculas"
COD_AT_EMPRESAS = "AT-empresas_sti"

LIMITE_NO_RITMO = 100.0
LIMITE_ACELERACAO = 80.0

SIT_ATINGIDA = "meta já atingida"
SIT_NO_RITMO = "no ritmo"
SIT_ACELERACAO = "exige aceleração"
SIT_RISCO = "risco alto"
SIT_INCOMPLETO = "consolidado incompleto"  # só na linha do DN
STATUS_ZERO_SUSPEITO = "zero_suspeito"
COLUNAS_EM_BRANCO_INCOMPLETO = [
    "projecao_dez", "indice_projetado", "ritmo_necessario_mensal", "aceleracao_necessaria",
]
SITUACOES = (SIT_ATINGIDA, SIT_NO_RITMO, SIT_ACELERACAO, SIT_RISCO)

def metodo_texto(mes: int) -> str:
    """Rótulo do método de projeção para o mês de referência usado."""
    return f"linear (realizado/{mes}*12); não considera sazonalidade"

COLUNAS_PROJECAO = [
    "fonte", "ano", "mes_referencia", "uf", "regiao", "tipo_unidade", "cod_indicador",
    "indicador", "meta_txt", "realizado_txt", "meta", "realizado",
    "ritmo_atual_mensal", "projecao_dez", "indice_projetado", "falta_para_meta",
    "ritmo_necessario_mensal", "aceleracao_necessaria", "situacao", "metodo",
]
COLUNAS_RESUMO = [
    "cod_indicador", "indicador", "n_drs",
    "n_meta_ja_atingida", "n_no_ritmo", "n_exige_aceleracao", "n_risco_alto",
    "n_drs_zero_suspeito", "situacao_dn", "indice_projetado_dn",
]


# --------------------------------------------------------------------------
# Cálculo por linha
# --------------------------------------------------------------------------

def classificar(realizado: float, meta: float, indice_projetado: float) -> str:
    """Situação: meta já atingida > no ritmo (>= 100) > exige aceleração (80 a < 100) > risco alto."""
    if realizado >= meta:
        return SIT_ATINGIDA
    if indice_projetado >= LIMITE_NO_RITMO:
        return SIT_NO_RITMO
    if indice_projetado >= LIMITE_ACELERACAO:
        return SIT_ACELERACAO
    return SIT_RISCO


def projetar_linha(realizado: float, meta: float, mes: int) -> dict[str, float | str | None]:
    """Calcula ritmo, projeção linear para dezembro e situação de uma linha.

    Exige ``meta > 0`` e ``1 <= mes < 12``. ``aceleracao_necessaria`` é ``None``
    quando o ritmo atual é zero (não há base para a razão).
    """
    if meta <= 0:
        raise ValueError(f"meta deve ser > 0 (recebido {meta})")
    if not 1 <= mes < 12:
        raise ValueError(f"mes de referência deve estar entre 1 e 11 (recebido {mes})")
    ritmo_atual = realizado / mes
    projecao_dez = realizado * 12 / mes
    indice_projetado = projecao_dez / meta * 100
    falta = max(meta - realizado, 0.0)
    ritmo_necessario = falta / (12 - mes)
    aceleracao = None if ritmo_atual == 0 else ritmo_necessario / ritmo_atual
    return {
        "ritmo_atual_mensal": ritmo_atual,
        "projecao_dez": projecao_dez,
        "indice_projetado": indice_projetado,
        "falta_para_meta": falta,
        "ritmo_necessario_mensal": ritmo_necessario,
        "aceleracao_necessaria": aceleracao,
        "situacao": classificar(realizado, meta, indice_projetado),
    }


# --------------------------------------------------------------------------
# Seleção do escopo
# --------------------------------------------------------------------------

def _filtrar_ano(df: pd.DataFrame, ano: int, nome: str) -> pd.DataFrame:
    """Linhas do ano pedido; erro se não houver nenhuma."""
    do_ano = df[df["ano"] == ano]
    if do_ano.empty:
        raise ValueError(f"{nome}: nenhuma linha para o ano {ano}")
    return do_ano


def derivar_mes(
    pes_ano: pd.DataFrame, atend_ano: pd.DataFrame, mes_esperado: int | None = None
) -> int:
    """Mês de referência único do ano, lido do próprio dado (pes + atendimento).

    Erro se houver mais de um valor de ``mes_referencia`` (ou nenhum) ou se
    ``mes_esperado`` for informado e não bater com o dado.
    """
    meses = set(pd.concat([pes_ano["mes_referencia"], atend_ano["mes_referencia"]])
                .dropna().astype(int))
    if len(meses) != 1:
        raise ValueError(f"mes_referencia deve ter valor único no ano; encontrado {sorted(meses)}")
    mes = meses.pop()
    if mes_esperado is not None and mes != mes_esperado:
        raise ValueError(f"mes_referencia do dado ({mes}) diferente do esperado ({mes_esperado})")
    return mes


def selecionar_pes(pes_ano: pd.DataFrame) -> pd.DataFrame:
    """Linhas do PES no escopo (indicadores de quantidade acumulada), ainda sem filtro de validade."""
    df = pes_ano[pes_ano["cod_indicador"].astype(str).isin(CODS_PES)]
    return pd.DataFrame({
        "fonte": "pes", "ano": df["ano"], "mes_referencia": df["mes_referencia"],
        "uf": df["uf"], "regiao": df["regiao"], "tipo_unidade": df["tipo_unidade"],
        "cod_indicador": df["cod_indicador"].astype(str), "indicador": df["indicador"],
        "meta_txt": df["meta_txt"], "realizado_txt": df["realizado_txt"],
        "meta": df["meta"], "realizado": df["realizado"], "status_dado": df["status_dado"],
    })


def selecionar_atendimento(atend_ano: pd.DataFrame) -> pd.DataFrame:
    """Matrículas (TOTAL) e empresas atendidas do Atendimento, ainda sem filtro de validade.

    O previsto faz o papel de meta.
    """
    df = atend_ano
    eh_matriculas = (df["linha_negocio"] == LINHA_MATRICULAS) & (df["acao"] == ACAO_TOTAL)
    eh_empresas = df["acao"] == ACAO_EMPRESAS
    df = df[eh_matriculas | eh_empresas].copy()
    df["cod_indicador"] = (df["acao"] == ACAO_EMPRESAS).map(
        {True: COD_AT_EMPRESAS, False: COD_AT_MATRICULAS})
    df["indicador"] = (df["acao"] == ACAO_EMPRESAS).map(
        {True: ACAO_EMPRESAS.capitalize(), False: f"{LINHA_MATRICULAS} - {ACAO_TOTAL}"})
    return pd.DataFrame({
        "fonte": "atendimento", "ano": df["ano"], "mes_referencia": df["mes_referencia"],
        "uf": df["uf"], "regiao": df["regiao"], "tipo_unidade": df["tipo_unidade"],
        "cod_indicador": df["cod_indicador"], "indicador": df["indicador"],
        "meta_txt": df["previsto_txt"], "realizado_txt": df["realizado_txt"],
        "meta": df["previsto"], "realizado": df["realizado"], "status_dado": df["status_dado"],
    })


def filtrar_validos(escopo: pd.DataFrame) -> pd.DataFrame:
    """Mantém só ``status_dado == "ok"`` e meta > 0 (logando quantas linhas saem, por fonte)."""
    validos = escopo[(escopo["status_dado"] == "ok") & (escopo["meta"] > 0)]
    fora = escopo.drop(validos.index)
    for fonte, n in fora["fonte"].value_counts().items():
        logger.info("%s: %d linhas fora da projeção (status_dado != ok ou meta <= 0)", fonte, n)
    return validos


def contar_zero_suspeito(escopo: pd.DataFrame) -> dict[str, int]:
    """Nº de DRs (só ``tipo_unidade == "DR"``) com ``status_dado == "zero_suspeito"``, por indicador."""
    zs = escopo[(escopo["status_dado"] == STATUS_ZERO_SUSPEITO) & (escopo["tipo_unidade"] == TIPO_DR)]
    return {str(cod): int(n) for cod, n in zs.groupby("cod_indicador")["uf"].nunique().items()}


def marcar_consolidado_incompleto(proj: pd.DataFrame, faltantes: dict[str, int]) -> pd.DataFrame:
    """Na linha do DN de indicador com DRs em ``zero_suspeito``: situação
    "consolidado incompleto" e, em branco, projeção, índice projetado, ritmo
    necessário e aceleração (o DN agrega DRs não apurados)."""
    proj = proj.copy()
    for cod, n in faltantes.items():
        alvo = (proj["cod_indicador"] == cod) & (proj["uf"] == UF_DN)
        if alvo.any():
            proj.loc[alvo, "situacao"] = SIT_INCOMPLETO
            proj.loc[alvo, COLUNAS_EM_BRANCO_INCOMPLETO] = None
            logger.warning("%s: DN marcado como '%s'; faltam %d DRs (zero_suspeito)",
                           cod, SIT_INCOMPLETO, n)
        else:
            logger.warning("%s: %d DRs excluídos por zero_suspeito (DN sem linha válida)", cod, n)
    return proj


# --------------------------------------------------------------------------
# Tabelas de saída
# --------------------------------------------------------------------------

def projetar(base: pd.DataFrame, mes: int) -> pd.DataFrame:
    """Aplica ``projetar_linha`` a cada linha da base selecionada."""
    if base.empty:
        return pd.DataFrame(columns=COLUNAS_PROJECAO)
    calculos = pd.DataFrame(
        [projetar_linha(float(r), float(m), mes) for r, m in zip(base["realizado"], base["meta"])],
        index=base.index,
    )
    saida = pd.concat([base, calculos], axis=1)
    saida["metodo"] = metodo_texto(mes)
    return saida[COLUNAS_PROJECAO].reset_index(drop=True)


def resumo(proj: pd.DataFrame, faltantes: dict[str, int] | None = None) -> pd.DataFrame:
    """Por indicador: contagem de DRs em cada situação, DRs excluídos por
    zero_suspeito e a situação do DN."""
    faltantes = faltantes or {}
    linhas = []
    for (cod, nome), grupo in proj.groupby(["cod_indicador", "indicador"], sort=False):
        drs = grupo[grupo["tipo_unidade"] == TIPO_DR]
        dn = grupo[grupo["uf"] == UF_DN]
        contagem = drs["situacao"].value_counts()
        linhas.append({
            "cod_indicador": cod, "indicador": nome, "n_drs": len(drs),
            "n_meta_ja_atingida": int(contagem.get(SIT_ATINGIDA, 0)),
            "n_no_ritmo": int(contagem.get(SIT_NO_RITMO, 0)),
            "n_exige_aceleracao": int(contagem.get(SIT_ACELERACAO, 0)),
            "n_risco_alto": int(contagem.get(SIT_RISCO, 0)),
            "n_drs_zero_suspeito": faltantes.get(str(cod), 0),
            "situacao_dn": dn["situacao"].iloc[0] if not dn.empty else None,
            "indice_projetado_dn": dn["indice_projetado"].iloc[0] if not dn.empty else None,
        })
    return pd.DataFrame(linhas, columns=COLUNAS_RESUMO)


def gerar_projecao(
    pes: pd.DataFrame, atend: pd.DataFrame, ano: int = ANO_PADRAO,
    mes_esperado: int | None = None,
) -> dict[str, pd.DataFrame]:
    """Monta as duas tabelas de projeção a partir de pes e atendimento normalizados.

    O mês de referência (m) é derivado do dado; ``mes_esperado`` só verifica.
    """
    pes_ano = _filtrar_ano(pes, ano, "pes")
    atend_ano = _filtrar_ano(atend, ano, "atendimento")
    mes = derivar_mes(pes_ano, atend_ano, mes_esperado)
    logger.info("ano %d: mes_referencia derivado do dado = %d", ano, mes)
    escopo = pd.concat(
        [selecionar_pes(pes_ano), selecionar_atendimento(atend_ano)],
        ignore_index=True,
    )
    faltantes = contar_zero_suspeito(escopo)
    base = filtrar_validos(escopo).drop(columns="status_dado")
    proj = marcar_consolidado_incompleto(projetar(base, mes), faltantes)
    res = resumo(proj, faltantes)
    for _, r in res.iterrows():
        logger.info("%s: %d DRs | atingida %d, no ritmo %d, aceleração %d, risco %d | DN: %s",
                    r["cod_indicador"], r["n_drs"], r["n_meta_ja_atingida"], r["n_no_ritmo"],
                    r["n_exige_aceleracao"], r["n_risco_alto"], r["situacao_dn"])
    return {f"projecao_{ano}": proj, "projecao_resumo": res}


# --------------------------------------------------------------------------
# E/S
# --------------------------------------------------------------------------

def carregar_tabela(nome: str) -> pd.DataFrame:
    """Lê uma tabela de ``data/processed`` preservando os textos originais."""
    return pd.read_csv(
        PROCESSED_DIR / f"{nome}.csv",
        encoding=ENCODING_CSV,
        dtype={"cod_indicador": str},
        keep_default_na=False,
        na_values=[""],
    )


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada da linha de comando."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ano", type=int, default=ANO_PADRAO, help="ano parcial (padrão: 2026)")
    parser.add_argument("--mes", type=int, default=None,
                        help="opcional: confere se o mes_referencia do dado é este valor")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    tabelas = gerar_projecao(
        carregar_tabela("pes"), carregar_tabela("atendimento"), args.ano, args.mes
    )
    for nome, df in tabelas.items():
        caminho = PROCESSED_DIR / f"{nome}.csv"
        df.to_csv(caminho, index=False, encoding=ENCODING_CSV)
        logger.info("gravado %s (%d linhas)", caminho, len(df))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
