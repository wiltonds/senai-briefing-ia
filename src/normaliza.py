"""Normalização dos JSON brutos do Portal da Transparência do SENAI.

Lê os arquivos listados em ``data/raw/manifest.json`` (gerados por
``src.coleta``) e produz duas tabelas em ``data/processed/``:

- ``pes.csv``: uma linha por (ano, DR, indicador) do Plano Estratégico Sistêmico.
- ``atendimento.csv``: uma linha por (ano, DR, tema, linha de negócio, ação
  finalística), incluindo o total de cada linha de negócio (``acao="TOTAL"``).

Os textos originais dos valores são preservados em colunas ``*_txt``; as
colunas numéricas são derivadas deles por ``parse_numero_br``.

Uso:
    python -m src.normaliza
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.formato import fmt_en

logger = logging.getLogger("normaliza")

RAIZ = Path(__file__).resolve().parents[1]
RAW_DIR = RAIZ / "data" / "raw"
MANIFEST_PATH = RAW_DIR / "manifest.json"
PROCESSED_DIR = RAIZ / "data" / "processed"

REGIAO_POR_UF: dict[str, str] = {
    **dict.fromkeys(["AC", "AP", "AM", "PA", "RO", "RR", "TO"], "Norte"),
    **dict.fromkeys(["AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"], "Nordeste"),
    **dict.fromkeys(["DF", "GO", "MT", "MS"], "Centro-Oeste"),
    **dict.fromkeys(["ES", "MG", "RJ", "SP"], "Sudeste"),
    **dict.fromkeys(["PR", "RS", "SC"], "Sul"),
    "DN": "Brasil",
    "CETIQT": "Sudeste",  # SENAI CETIQT (Rio de Janeiro)
}

TIPO_DN = "DN"
TIPO_CETIQT = "CETIQT"
TIPO_DR = "DR"

COLUNAS_PES = [
    "ano", "periodo_fechamento", "mes_referencia", "data_publicacao", "uf", "regiao",
    "tipo_unidade", "cod_indicador", "indicador", "objetivo_estrategico", "perspectiva", "foco_atuacao",
    "unidade", "polaridade", "acumulacao", "periodicidade", "fonte_dado",
    "meta_txt", "realizado_txt", "indice_txt",
    "meta", "realizado", "indice_publicado", "indice_calculado",
    "divergencia_indice", "indice_analise",
    "status_dado", "arquivo_origem", "sha256_origem",
]

COLUNAS_ATENDIMENTO = [
    "ano", "periodo_fechamento", "mes_referencia", "uf", "regiao", "tipo_unidade", "tema",
    "linha_negocio", "acao", "previsto_txt", "realizado_txt", "previsto", "realizado",
    "indice_publicado", "indice_calculado", "divergencia_indice", "indice_analise",
    "status_dado", "arquivo_origem", "sha256_origem",
]

ENCODING_CSV = "utf-8-sig"  # BOM para Excel e Power BI reconhecerem UTF-8

TEXTO_ZERO_SUSPEITO = "R$ - 0"

LIMITE_DIVERGENCIA_PP = 1.0  # pontos percentuais

MES_POR_PERIODO: dict[str, int] = {
    "PRIMEIRO TRIMESTRE": 3,
    "SEGUNDO TRIMESTRE": 6,
    "TERCEIRO TRIMESTRE": 9,
    "QUARTO TRIMESTRE": 12,
    **{
        mes: i
        for i, mes in enumerate(
            ["JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO", "JULHO",
             "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO"],
            start=1,
        )
    },
}

# Após remover espaços: sinal opcional, "R$" opcional, sinal opcional,
# parte inteira com "." como separador de milhar, decimais com "," e "%" opcional.
_RE_NUMERO_BR = re.compile(
    r"^(?P<s1>-?)(?:R\$)?(?P<s2>-?)"
    r"(?P<inteiro>\d{1,3}(?:\.\d{3})+|\d+)"
    r"(?:,(?P<decimal>\d+))?%?$"
)


def parse_numero_br(txt: str | None) -> float | None:
    """Converte um número no formato brasileiro do portal para float.

    - ``"-"``, ``""`` ou ``None`` -> ``None`` (sem dado; nunca zero).
    - ``"R$ - 0"`` -> ``0.0``; ``"-R$ 2.522.387,69"`` -> ``-2522387.69``.
    - ``"45,0%"`` -> ``45.0``; ``"1.635"`` -> ``1635.0``; ``"2824"`` -> ``2824.0``.

    O ponto é sempre separador de milhar e a vírgula, separador decimal.
    Levanta ``ValueError`` (com o texto original) se o formato não for reconhecido.
    """
    if txt is None:
        return None
    compacto = re.sub(r"\s+", "", txt)
    if compacto in ("", "-"):
        return None

    m = _RE_NUMERO_BR.match(compacto)
    if not m or (m["s1"] and m["s2"]):
        raise ValueError(f"número em formato não reconhecido: {txt!r}")

    valor = float(m["inteiro"].replace(".", "") + "." + (m["decimal"] or "0"))
    if m["s1"] or m["s2"]:
        valor = -valor
    return valor + 0.0  # converte -0.0 em 0.0


def _e_zero_suspeito(txt: str | None) -> bool:
    """True se o texto for "R$ - 0" (ignorando a quantidade de espaços)."""
    return txt is not None and " ".join(txt.split()) == TEXTO_ZERO_SUSPEITO


def status_dado(
    meta: float | None,
    realizado: float | None,
    meta_txt: str | None,
    realizado_txt: str | None,
) -> str:
    """Classifica a qualidade do par meta/realizado (ou previsto/realizado).

    "zero_suspeito" tem prioridade e vale quando meta_txt ou realizado_txt
    for "R$ - 0".
    """
    if _e_zero_suspeito(meta_txt) or _e_zero_suspeito(realizado_txt):
        return "zero_suspeito"
    if meta is None and realizado is None:
        return "sem_dado"
    if meta is None:
        return "sem_meta"
    if realizado is None:
        return "sem_realizado"
    return "ok"


def indice_calculado(meta: float | None, realizado: float | None) -> float | None:
    """Calcula realizado/meta*100 quando meta > 0 e há realizado."""
    if meta is None or realizado is None or meta <= 0:
        return None
    return realizado / meta * 100


def divergencia_indice(publicado: float | None, calculado: float | None) -> bool:
    """True quando os dois índices existem e diferem em mais de 1 ponto percentual."""
    if publicado is None or calculado is None:
        return False
    return abs(publicado - calculado) > LIMITE_DIVERGENCIA_PP


def indice_analise(publicado: float | None, calculado: float | None) -> float | None:
    """Índice usado na análise: o calculado, se existir; senão, o publicado."""
    return calculado if calculado is not None else publicado


def mes_referencia(periodo: str | None) -> int:
    """Converte o período de fechamento no mês de referência (1 a 12).

    Aceita trimestres ("QUARTO TRIMESTRE" ou "QUARTO_TRIMESTRE" -> 12) e nomes
    de mês ("SETEMBRO" -> 9, "MARÇO" -> 3). Levanta ``ValueError`` se não
    reconhecer o valor.
    """
    if periodo is None:
        raise ValueError("período de fechamento ausente")
    sem_acento = unicodedata.normalize("NFKD", periodo).encode("ascii", "ignore").decode()
    chave = " ".join(sem_acento.replace("_", " ").upper().split())
    if chave not in MES_POR_PERIODO:
        raise ValueError(f"período de fechamento não reconhecido: {periodo!r}")
    return MES_POR_PERIODO[chave]


def formatar_cod_indicador(numero: Any) -> str:
    """Converte o número do indicador em código texto (1.0 -> "1", 2.1 -> "2.1")."""
    if isinstance(numero, float):
        return format(numero, "g")
    return str(numero).strip()


def converter_data(txt: str | None) -> str | None:
    """Converte "dd/mm/aaaa hh:mm:ss" ou "dd-mm-aaaa hh:mm:ss" para ISO 8601."""
    if not txt:
        return None
    for formato in ("%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
        try:
            return datetime.strptime(txt.strip(), formato).isoformat()
        except ValueError:
            continue
    raise ValueError(f"data em formato não reconhecido: {txt!r}")


def _limpar(txt: Any) -> Any:
    """Remove espaços nas bordas de textos descritivos (não usar em valores)."""
    return txt.strip() if isinstance(txt, str) else txt


def _valores(meta_txt: str | None, realizado_txt: str | None, indice_txt: str | None,
             contexto: str) -> dict[str, Any]:
    """Converte o trio meta/realizado/índice e calcula índice e status.

    Em caso de texto não reconhecido, relança o ``ValueError`` com o contexto
    (arquivo e item) para facilitar a investigação.
    """
    try:
        meta = parse_numero_br(meta_txt)
        realizado = parse_numero_br(realizado_txt)
        indice = parse_numero_br(indice_txt)
    except ValueError as exc:
        raise ValueError(f"{contexto}: {exc}") from exc
    calculado = indice_calculado(meta, realizado)
    return {
        "meta": meta,
        "realizado": realizado,
        "indice_publicado": indice,
        "indice_calculado": calculado,
        "divergencia_indice": divergencia_indice(indice, calculado),
        "indice_analise": indice_analise(indice, calculado),
        "status_dado": status_dado(meta, realizado, meta_txt, realizado_txt),
    }


def tipo_unidade(dr: str) -> str:
    """Tipo da unidade: "DN" (consolidado nacional), "CETIQT" ou "DR"."""
    if dr == TIPO_DN:
        return TIPO_DN
    if dr == TIPO_CETIQT:
        return TIPO_CETIQT
    return TIPO_DR


def linhas_pes(dados: list[dict[str, Any]], dr: str, origem: str, sha256: str) -> list[dict[str, Any]]:
    """Gera as linhas de ``pes.csv`` a partir de um JSON bruto da fonte "pes"."""
    linhas = []
    for item in dados:
        cod = formatar_cod_indicador(item["numeroIndicador"])
        for obj in item["objetivosEstrategicos"]:
            valores = _valores(
                obj.get("estimadoAnual"), obj.get("realizado"), obj.get("indiceRealizacao"),
                contexto=f"{origem} indicador {cod}",
            )
            linhas.append({
                "ano": item["ano"],
                "periodo_fechamento": item.get("periodoFechamento"),
                "mes_referencia": mes_referencia(item.get("periodoFechamento")),
                "data_publicacao": converter_data(item.get("dataPublicacao")),
                "uf": dr,
                "regiao": REGIAO_POR_UF[dr],
                "tipo_unidade": tipo_unidade(dr),
                "cod_indicador": cod,
                "indicador": _limpar(item.get("indicador")),
                "objetivo_estrategico": _limpar(obj.get("objtEstrategico")),
                "perspectiva": _limpar(item.get("perspectiva")),
                "foco_atuacao": _limpar(item.get("focoAtuacao")),
                "unidade": _limpar(item.get("unidadeMedida")),
                "polaridade": _limpar(item.get("polaridade")),
                "acumulacao": _limpar(item.get("acumulacao")),
                "periodicidade": _limpar(item.get("periodicidadeApuracao")),
                "fonte_dado": _limpar(item.get("fontePlanejamento")),
                "meta_txt": obj.get("estimadoAnual"),
                "realizado_txt": obj.get("realizado"),
                "indice_txt": obj.get("indiceRealizacao"),
                "meta": valores["meta"],
                "realizado": valores["realizado"],
                "indice_publicado": valores["indice_publicado"],
                "indice_calculado": valores["indice_calculado"],
                "divergencia_indice": valores["divergencia_indice"],
                "indice_analise": valores["indice_analise"],
                "status_dado": valores["status_dado"],
                "arquivo_origem": origem,
                "sha256_origem": sha256,
            })
    return linhas


def _dado_da_regiao(dados_quant: dict[str, Any], dr: str, contexto: str) -> dict[str, Any]:
    """Seleciona, em ``dadosQuantitativos``, o bloco correspondente ao DR.

    Cada DR traz o bloco da própria região (ex.: SC -> "SUL"); o DN traz
    "BRASIL" e também as cinco regiões, das quais só "BRASIL" é usado.
    Retorna ``{}`` (sem dado) se o bloco esperado não existir.
    """
    chave = REGIAO_POR_UF[dr].upper()
    if chave in dados_quant:
        return dados_quant[chave]
    if len(dados_quant) == 1:
        (unico,) = dados_quant.values()
        logger.warning("%s: região %s ausente; usando %s", contexto, chave, unico.get("regiao"))
        return unico
    logger.warning("%s: sem dados para a região %s", contexto, chave)
    return {}


def linhas_atendimento(dados: dict[str, Any], dr: str, origem: str, sha256: str) -> list[dict[str, Any]]:
    """Gera as linhas de ``atendimento.csv`` a partir de um JSON bruto da fonte "atendimento"."""
    base = {
        "ano": dados["ano"],
        "periodo_fechamento": dados.get("periodoFechamento"),
        "mes_referencia": mes_referencia(dados.get("periodoFechamento")),
        "uf": dr,
        "regiao": REGIAO_POR_UF[dr],
        "tipo_unidade": tipo_unidade(dr),
    }
    linhas = []
    for tema in dados["temas"].values():
        for linha_neg in tema["linhasNegocio"].values():
            blocos = [
                (acao["nomeAcaoFinalistica"], acao["dadosQuantitativos"])
                for acao in linha_neg["acoesFinalisticas"].values()
            ]
            blocos.append(("TOTAL", linha_neg.get("dadosQuantitativos") or {}))
            for nome_acao, dados_quant in blocos:
                contexto = f"{origem} {linha_neg['nomeLinhaNegocio']} / {nome_acao}"
                bloco = _dado_da_regiao(dados_quant, dr, contexto)
                valores = _valores(
                    bloco.get("previsto"), bloco.get("realizado"), bloco.get("indiceRealizacao"),
                    contexto=contexto,
                )
                linhas.append({
                    **base,
                    "tema": _limpar(tema["nomeTema"]),
                    "linha_negocio": _limpar(linha_neg["nomeLinhaNegocio"]),
                    "acao": _limpar(nome_acao),
                    "previsto_txt": bloco.get("previsto"),
                    "realizado_txt": bloco.get("realizado"),
                    "previsto": valores["meta"],
                    "realizado": valores["realizado"],
                    "indice_publicado": valores["indice_publicado"],
                    "indice_calculado": valores["indice_calculado"],
                    "divergencia_indice": valores["divergencia_indice"],
                    "indice_analise": valores["indice_analise"],
                    "status_dado": valores["status_dado"],
                    "arquivo_origem": origem,
                    "sha256_origem": sha256,
                })
    return linhas


def normalizar() -> dict[str, pd.DataFrame]:
    """Lê os arquivos do manifest e retorna as tabelas ``pes`` e ``atendimento``.

    Entradas do manifest com erro registrado são ignoradas (com log).
    """
    manifest: dict[str, dict[str, Any]] = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    linhas: dict[str, list[dict[str, Any]]] = {"pes": [], "atendimento": []}
    geradores = {"pes": linhas_pes, "atendimento": linhas_atendimento}

    for chave, registro in sorted(manifest.items()):
        if registro.get("erro"):
            logger.warning("Ignorando %s (erro na coleta: %s)", chave, registro["erro"])
            continue
        fonte, _ano, nome_arquivo = chave.split("/")
        dr = Path(nome_arquivo).stem
        dados = json.loads((RAW_DIR / chave).read_text(encoding="utf-8"))
        novas = geradores[fonte](dados, dr, chave, registro["sha256"])
        linhas[fonte].extend(novas)
        logger.info("%s: %d linhas", chave, len(novas))

    pes = pd.DataFrame(linhas["pes"], columns=COLUNAS_PES)
    atendimento = pd.DataFrame(linhas["atendimento"], columns=COLUNAS_ATENDIMENTO)

    duplicados = pes.duplicated(["ano", "uf", "cod_indicador"], keep=False)
    if duplicados.any():
        logger.warning("pes: %d linhas com (ano, uf, cod_indicador) repetido", duplicados.sum())

    _logar_divergencias("pes", pes, ["ano", "uf", "cod_indicador", "indicador"])
    _logar_divergencias("atendimento", atendimento, ["ano", "uf", "tema", "linha_negocio", "acao"])

    return {"pes": pes, "atendimento": atendimento}


def _logar_divergencias(nome: str, df: pd.DataFrame, chaves: list[str]) -> None:
    """Loga como warning cada linha em que o índice publicado diverge do calculado."""
    divergentes = df[df["divergencia_indice"]]
    if divergentes.empty:
        return
    detalhes = "\n".join(
        "  " + " | ".join(str(linha[c]) for c in chaves)
        + f" | publicado={fmt_en(linha['indice_publicado'])} calculado={fmt_en(linha['indice_calculado'])}"
        for _, linha in divergentes.iterrows()
    )
    logger.warning(
        "%s: %d divergência(s) entre índice publicado e calculado (> %.0f p.p.):\n%s",
        nome, len(divergentes), LIMITE_DIVERGENCIA_PP, detalhes,
    )


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada da linha de comando."""
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    for nome, df in normalizar().items():
        destino = PROCESSED_DIR / f"{nome}.csv"
        df.to_csv(destino, index=False, encoding=ENCODING_CSV)
        logger.info("%s: %d linhas -> %s", nome, len(df), destino)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
