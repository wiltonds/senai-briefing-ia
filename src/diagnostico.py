"""Diagnóstico de desempenho de um ano fechado (padrão: 2025).

Lê ``data/processed/pes.csv`` e ``data/processed/atendimento.csv`` e gera, em
``data/processed/``:

1. ``diag_indicador.csv``: distribuição do farol e do índice entre DRs, por indicador.
2. ``diag_dr.csv``: farol consolidado por DR.
3. ``diag_regiao.csv``: medianas por região e indicador.
4. ``cruzamento_volume_resultado.csv``: matrículas (volume) x empregabilidade (resultado).

Princípios:
- Só indicadores oficiais e a coluna ``indice_analise``; nenhum índice composto ou peso.
- Só linhas com ``status_dado == "ok"`` e meta (ou previsto) > 0.
- Achados de qualidade (``qualidade_achados.csv``): excluir o inválido (Q02,
  percentual impossível) e sinalizar o questionável (Q08, ``meta_subcalibrada``).
- O DN é a referência consolidada: aparece como ``indice_dn`` e não entra nas
  distribuições entre DRs. Só ``tipo_unidade == "DR"`` entra nas comparações
  (DN e SENAI CETIQT ficam fora do farol).

Uso:
    python -m src.diagnostico [--ano 2025]
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger("diagnostico")

RAIZ = Path(__file__).resolve().parents[1]
PROCESSED_DIR = RAIZ / "data" / "processed"
ENCODING_CSV = "utf-8-sig"

ANO_PADRAO = 2025
MES_FECHAMENTO = 12
UF_DN = "DN"
TIPO_DR = "DR"  # só DRs entram nas comparações (DN e CETIQT ficam fora)

LIMITE_ATINGIDO = 100.0
LIMITE_ATENCAO = 80.0

COD_EMPREGABILIDADE = "1"
COD_FIDELIZACAO = "4.2"
LINHA_MATRICULAS = "Programas (matrículas)"
ACAO_TOTAL = "TOTAL"

REGRA_INVALIDO = "Q02"      # percentual impossível: excluído do farol
REGRA_SUBCALIBRADA = "Q08"  # índice > 200%: permanece no farol, sinalizado

COLUNAS_DIAG_INDICADOR = [
    "cod_indicador", "indicador", "perspectiva", "n_drs_com_dado",
    "n_atingido", "n_atencao", "n_critico", "n_meta_subcalibrada",
    "mediana_indice", "p25_indice", "p75_indice",
    "indice_dn", "meta_dn_txt", "realizado_dn_txt",
    "dr_menor_indice", "dr_maior_indice",
]
COLUNAS_DIAG_DR = [
    "uf", "regiao", "n_indicadores_com_dado", "cobertura",
    "n_atingido", "n_atencao", "n_critico", "n_meta_subcalibrada",
    "pct_atingido", "resumo", "indicadores_criticos",
]
COLUNAS_DIAG_REGIAO = [
    "regiao", "cod_indicador", "indicador", "n_drs",
    "mediana_realizado", "mediana_indice", "n_criticos",
]
COLUNAS_CRUZAMENTO = [
    "uf", "regiao", "indice_matriculas", "indice_empregabilidade",
    "realizado_empregabilidade", "indice_fidelizacao", "leitura",
]

TEXTO_VOLUME = {"atingido": "volume ok", "atencao": "volume em atenção",
                "critico": "volume crítico", None: "sem dado de volume"}
TEXTO_RESULTADO = {"atingido": "resultado ok", "atencao": "resultado em atenção",
                   "critico": "resultado crítico", None: "sem dado de resultado"}


# --------------------------------------------------------------------------
# Regras básicas
# --------------------------------------------------------------------------

def farol(indice: float | None) -> str | None:
    """Classifica o índice: >= 100 "atingido"; 80 a < 100 "atencao"; < 80 "critico".

    Retorna ``None`` se o índice for vazio.
    """
    if indice is None or pd.isna(indice):
        return None
    if indice >= LIMITE_ATINGIDO:
        return "atingido"
    if indice >= LIMITE_ATENCAO:
        return "atencao"
    return "critico"


def leitura_cruzamento(indice_volume: float | None, indice_resultado: float | None) -> str:
    """Texto que combina o farol de volume (matrículas) e o de resultado (empregabilidade).

    Ex.: matrículas >= 100 e empregabilidade < 80 -> "volume ok, resultado crítico".
    """
    return f"{TEXTO_VOLUME[farol(indice_volume)]}, {TEXTO_RESULTADO[farol(indice_resultado)]}"


def chave_cod(cod: str) -> tuple[int, ...]:
    """Chave de ordenação natural de cod_indicador ("2.1" < "10")."""
    return tuple(int(p) for p in str(cod).split("."))


def _pct_br(valor: float) -> str:
    """Formata um índice como "86,3%"."""
    return f"{valor:.1f}%".replace(".", ",")


def filtrar_validos(df: pd.DataFrame, ano: int, col_meta: str) -> pd.DataFrame:
    """Linhas do ano fechado com ``status_dado == "ok"``, meta > 0 e índice definido.

    Exclui, portanto, zero_suspeito, sem_meta, sem_realizado e sem_dado.
    """
    filtro = (
        (df["ano"] == ano)
        & (df["mes_referencia"] == MES_FECHAMENTO)
        & (df["status_dado"] == "ok")
        & (df[col_meta] > 0)
        & df["indice_analise"].notna()
    )
    validos = df[filtro].copy()
    validos["farol"] = validos["indice_analise"].map(farol)
    validos["meta_subcalibrada"] = False
    return validos


def aplicar_achados(pes_validos: pd.DataFrame, achados: pd.DataFrame) -> pd.DataFrame:
    """Aplica os achados de qualidade às linhas do farol do PES.

    - Q02 (percentual impossível): a linha é EXCLUÍDA do farol (valor inválido).
    - Q08 (índice > 200%): a linha PERMANECE e recebe ``meta_subcalibrada = True``
      (valor questionável).

    O casamento é feito por (ano, uf, cod_indicador) com os achados da fonte "pes".
    """
    def chaves(regra_id: str) -> set[tuple[int, str, str]]:
        a = achados[(achados["regra_id"] == regra_id) & (achados["fonte"] == "pes")]
        return set(zip(a["ano"].astype(int), a["uf"].astype(str), a["item"].astype(str)))

    chave_linha = pd.Series(
        list(zip(pes_validos["ano"].astype(int), pes_validos["uf"], pes_validos["cod_indicador"])),
        index=pes_validos.index,
    )
    q02 = chave_linha.isin(chaves(REGRA_INVALIDO))
    q08 = chave_linha.isin(chaves(REGRA_SUBCALIBRADA))

    for _, linha in pes_validos[q02].iterrows():
        logger.warning(
            "Excluído do farol (%s, percentual impossível): %s %s indicador %s, "
            "realizado_txt=%s, índice=%.1f",
            REGRA_INVALIDO, linha["ano"], linha["uf"], linha["cod_indicador"],
            linha["realizado_txt"], linha["indice_analise"],
        )
    resultado = pes_validos[~q02].copy()
    resultado["meta_subcalibrada"] = q08[~q02]
    logger.info(
        "Farol: %d linha(s) excluída(s) por %s; %d sinalizada(s) como meta subcalibrada (%s)",
        int(q02.sum()), REGRA_INVALIDO, int(resultado["meta_subcalibrada"].sum()),
        REGRA_SUBCALIBRADA,
    )
    return resultado


def _validar_polaridade(pes: pd.DataFrame) -> None:
    """Garante que todos os indicadores são "maior, melhor".

    O farol compara ``indice_analise`` com 100 e 80, o que só faz sentido para
    polaridade positiva. Indicador com outra polaridade interrompe o diagnóstico.
    """
    outras = pes.loc[~pes["polaridade"].fillna("").str.startswith("(+)"),
                     ["cod_indicador", "polaridade"]].drop_duplicates()
    if not outras.empty:
        raise ValueError(
            "farol definido só para polaridade '(+) Maior, melhor'; encontrado: "
            + "; ".join(f"{c} = {p!r}" for c, p in outras.itertuples(index=False))
        )


def _contar_farol(serie: pd.Series) -> dict[str, int]:
    """Conta atingido/atencao/critico em uma série de faróis."""
    return {f"n_{f}": int((serie == f).sum()) for f in ("atingido", "atencao", "critico")}


# --------------------------------------------------------------------------
# Tabelas
# --------------------------------------------------------------------------

def diag_indicador(pes_validos: pd.DataFrame, pes_ano: pd.DataFrame) -> pd.DataFrame:
    """Distribuição entre DRs por indicador, com o DN como referência.

    ``meta_dn_txt`` e ``realizado_dn_txt`` vêm do DN mesmo quando a linha dele
    não é válida (ex.: sem_dado), para mostrar o que foi publicado;
    ``indice_dn`` só é preenchido quando a linha do DN é válida.
    """
    drs = pes_validos[pes_validos["tipo_unidade"] == TIPO_DR]
    dn_valido = pes_validos[pes_validos["uf"] == UF_DN].set_index("cod_indicador")
    dn_publicado = pes_ano[pes_ano["uf"] == UF_DN].set_index("cod_indicador")

    linhas = []
    for cod in sorted(pes_validos["cod_indicador"].unique(), key=chave_cod):
        g = drs[drs["cod_indicador"] == cod]
        ref = pes_validos[pes_validos["cod_indicador"] == cod].iloc[0]
        idx = g["indice_analise"]
        linhas.append({
            "cod_indicador": cod,
            "indicador": ref["indicador"],
            "perspectiva": ref["perspectiva"],
            "n_drs_com_dado": len(g),
            **_contar_farol(g["farol"]),
            "n_meta_subcalibrada": int(g["meta_subcalibrada"].sum()),
            "mediana_indice": idx.median(),
            "p25_indice": idx.quantile(0.25),
            "p75_indice": idx.quantile(0.75),
            "indice_dn": dn_valido["indice_analise"].get(cod),
            "meta_dn_txt": dn_publicado["meta_txt"].get(cod),
            "realizado_dn_txt": dn_publicado["realizado_txt"].get(cod),
            "dr_menor_indice": g.loc[idx.idxmin(), "uf"] if len(g) else None,
            "dr_maior_indice": g.loc[idx.idxmax(), "uf"] if len(g) else None,
        })
    return pd.DataFrame(linhas, columns=COLUNAS_DIAG_INDICADOR).round(2)


def diag_dr(pes_validos: pd.DataFrame) -> pd.DataFrame:
    """Farol consolidado por DR (sem o DN).

    ``cobertura`` = indicadores com dado válido no DR / indicadores com dado
    válido em pelo menos um DR no ano (fração de 0 a 1).
    """
    drs = pes_validos[pes_validos["tipo_unidade"] == TIPO_DR]
    total_indicadores = drs["cod_indicador"].nunique()
    linhas = []
    for uf, g in drs.groupby("uf", sort=True):
        contagem = _contar_farol(g["farol"])
        criticos = g[g["farol"] == "critico"].sort_values(
            "cod_indicador", key=lambda s: s.map(chave_cod)
        )
        linhas.append({
            "uf": uf,
            "regiao": g["regiao"].iloc[0],
            "n_indicadores_com_dado": len(g),
            "cobertura": round(len(g) / total_indicadores, 3),
            **contagem,
            "n_meta_subcalibrada": int(g["meta_subcalibrada"].sum()),
            "pct_atingido": round(100 * contagem["n_atingido"] / len(g), 1),
            "resumo": f"{contagem['n_atingido']} de {len(g)} atingidos",
            "indicadores_criticos": "; ".join(
                f"{c} ({_pct_br(i)})"
                for c, i in zip(criticos["cod_indicador"], criticos["indice_analise"])
            ),
        })
    return pd.DataFrame(linhas, columns=COLUNAS_DIAG_DR)


def diag_regiao(pes_validos: pd.DataFrame) -> pd.DataFrame:
    """Medianas de realizado e índice por região e indicador (sem o DN)."""
    drs = pes_validos[pes_validos["tipo_unidade"] == TIPO_DR]
    out = (
        drs.groupby(["regiao", "cod_indicador"])
        .agg(
            indicador=("indicador", "first"),
            n_drs=("uf", "nunique"),
            mediana_realizado=("realizado", "median"),
            mediana_indice=("indice_analise", "median"),
            n_criticos=("farol", lambda s: int((s == "critico").sum())),
        )
        .reset_index()
    )
    out = out.sort_values(
        ["regiao", "cod_indicador"],
        key=lambda s: s.map(chave_cod) if s.name == "cod_indicador" else s,
    )
    return out[COLUNAS_DIAG_REGIAO].round(2).reset_index(drop=True)


def cruzamento_volume_resultado(
    pes_validos: pd.DataFrame, atend_validos: pd.DataFrame
) -> pd.DataFrame:
    """Cruza volume (índice de matrículas) com resultado (empregabilidade), por DR."""
    def por_uf(df: pd.DataFrame, colunas: dict[str, str]) -> pd.DataFrame:
        return df.set_index("uf")[list(colunas)].rename(columns=colunas)

    matriculas = atend_validos[
        (atend_validos["linha_negocio"] == LINHA_MATRICULAS)
        & (atend_validos["acao"] == ACAO_TOTAL)
    ]
    emp = pes_validos[pes_validos["cod_indicador"] == COD_EMPREGABILIDADE]
    fid = pes_validos[pes_validos["cod_indicador"] == COD_FIDELIZACAO]

    cols = ["uf", "regiao", "tipo_unidade"]
    regioes = pd.concat([pes_validos[cols], atend_validos[cols]])
    base = regioes[regioes["tipo_unidade"] == TIPO_DR][["uf", "regiao"]].drop_duplicates("uf").set_index("uf").sort_index()

    out = base.join([
        por_uf(matriculas, {"indice_analise": "indice_matriculas"}),
        por_uf(emp, {"indice_analise": "indice_empregabilidade",
                     "realizado": "realizado_empregabilidade"}),
        por_uf(fid, {"indice_analise": "indice_fidelizacao"}),
    ])
    out["leitura"] = [
        leitura_cruzamento(v, r)
        for v, r in zip(out["indice_matriculas"], out["indice_empregabilidade"])
    ]
    return out.reset_index()[COLUNAS_CRUZAMENTO].round(2)


def spearman(x: pd.Series, y: pd.Series) -> tuple[float | None, int]:
    """Correlação de Spearman (Pearson sobre postos) nos pares completos.

    Retorna (rho, n); rho é ``None`` se houver menos de 3 pares.
    """
    pares = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(pares) < 3:
        return None, len(pares)
    return float(pares["x"].rank().corr(pares["y"].rank())), len(pares)


def logar_cruzamento(cruz: pd.DataFrame) -> None:
    """Loga o resumo do cruzamento volume x resultado."""
    volume_ok = cruz[cruz["indice_matriculas"] >= LIMITE_ATINGIDO]
    criticos = volume_ok[volume_ok["indice_empregabilidade"] < LIMITE_ATENCAO]
    rho, n = spearman(cruz["indice_matriculas"], cruz["indice_empregabilidade"])
    logger.info("DRs com matrículas >= 100%%: %d", len(volume_ok))
    logger.info(
        "  destes, com empregabilidade < 80%%: %d (%s)",
        len(criticos), ", ".join(criticos["uf"]) or "nenhum",
    )
    if rho is None:
        logger.info("Spearman matrículas x empregabilidade: pares insuficientes (n=%d)", n)
    else:
        logger.info("Spearman matrículas x empregabilidade: rho=%.3f (n=%d DRs)", rho, n)


# --------------------------------------------------------------------------
# Execução
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


def carregar_achados() -> pd.DataFrame:
    """Lê ``qualidade_achados.csv`` (gerado por ``python -m src.qualidade``)."""
    caminho = PROCESSED_DIR / "qualidade_achados.csv"
    if not caminho.exists():
        raise FileNotFoundError(f"{caminho} não encontrado; rode antes: python -m src.qualidade")
    return pd.read_csv(
        caminho, encoding=ENCODING_CSV, dtype={"item": str, "uf": str},
        keep_default_na=False, na_values=[""],
    )


def diagnosticar(
    pes: pd.DataFrame, atend: pd.DataFrame, achados: pd.DataFrame, ano: int
) -> dict[str, pd.DataFrame]:
    """Gera as quatro tabelas do diagnóstico para o ano fechado informado."""
    pes_validos = filtrar_validos(pes, ano, "meta")
    atend_validos = filtrar_validos(atend, ano, "previsto")
    if pes_validos.empty:
        raise ValueError(f"sem dados válidos do PES para {ano} com mês de referência 12")
    _validar_polaridade(pes_validos)
    pes_validos = aplicar_achados(pes_validos, achados)

    pes_ano = pes[(pes["ano"] == ano) & (pes["mes_referencia"] == MES_FECHAMENTO)]
    logger.info(
        "%d: %d de %d linhas do PES no farol (status ok, meta > 0, sem achado Q02)",
        ano, len(pes_validos), len(pes_ano),
    )
    return {
        "diag_indicador": diag_indicador(pes_validos, pes_ano),
        "diag_dr": diag_dr(pes_validos),
        "diag_regiao": diag_regiao(pes_validos),
        "cruzamento_volume_resultado": cruzamento_volume_resultado(pes_validos, atend_validos),
    }


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada da linha de comando."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ano", type=int, default=ANO_PADRAO, help="ano fechado (padrão: 2025)")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    tabelas = diagnosticar(
        carregar_tabela("pes"), carregar_tabela("atendimento"), carregar_achados(), args.ano
    )
    for nome, df in tabelas.items():
        df.to_csv(PROCESSED_DIR / f"{nome}.csv", index=False, encoding=ENCODING_CSV)
        logger.info("%s.csv: %d linhas", nome, len(df))
    logar_cruzamento(tabelas["cruzamento_volume_resultado"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
