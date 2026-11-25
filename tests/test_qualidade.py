"""Testes de src.qualidade: um caso positivo e um negativo por regra."""

from typing import Any

import pandas as pd
import pytest

from src.qualidade import (
    COLUNAS_ACHADOS,
    REGRAS,
    TODOS_DRS,
    aplicar_regras,
    resumir,
)

FUNCAO = {r["id"]: r["funcao"] for r in REGRAS}


def linha_pes(**campos: Any) -> dict[str, Any]:
    """Linha de pes.csv com valores 'ok' por padrão."""
    base = {
        "ano": 2025, "mes_referencia": 12, "uf": "SC", "cod_indicador": "1",
        "indicador": "Indicador teste", "unidade": "Valor absoluto",
        "meta_txt": "100", "realizado_txt": "90", "indice_txt": "90,0%",
        "meta": 100.0, "realizado": 90.0, "indice_publicado": 90.0,
        "indice_calculado": 90.0, "divergencia_indice": False,
        "indice_analise": 90.0, "status_dado": "ok",
    }
    return base | campos


def linha_atend(**campos: Any) -> dict[str, Any]:
    """Linha de atendimento.csv com valores 'ok' por padrão."""
    base = {
        "ano": 2025, "mes_referencia": 12, "uf": "SC", "tema": "Tema",
        "linha_negocio": "Linha", "acao": "ACAO TESTE",
        "previsto_txt": "100", "realizado_txt": "90",
        "previsto": 100.0, "realizado": 90.0, "indice_publicado": 90.0,
        "indice_calculado": 90.0, "divergencia_indice": False,
        "indice_analise": 90.0, "status_dado": "ok",
    }
    return base | campos


def pes_df(*linhas: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(list(linhas) or [linha_pes()])


def atend_df(*linhas: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(list(linhas) or [linha_atend()])


def rodar(regra_id: str, pes: pd.DataFrame | None = None, atend: pd.DataFrame | None = None) -> pd.DataFrame:
    return FUNCAO[regra_id](pes if pes is not None else pes_df(), atend if atend is not None else atend_df())


VAZIO = {"meta": None, "realizado": None, "meta_txt": "-", "realizado_txt": "-",
         "status_dado": "sem_dado"}


# Q01 -----------------------------------------------------------------------

def test_q01_positivo_todos_drs_sem_dado() -> None:
    pes = pes_df(linha_pes(uf="SC", **VAZIO), linha_pes(uf="RS", **VAZIO))
    r = rodar("Q01", pes)
    assert len(r) == 1
    assert r.iloc[0]["uf"] == TODOS_DRS and r.iloc[0]["item"] == "1"


def test_q01_negativo_um_dr_com_dado() -> None:
    pes = pes_df(linha_pes(uf="SC", **VAZIO), linha_pes(uf="RS"))
    assert rodar("Q01", pes).empty


# Q02 -----------------------------------------------------------------------

def test_q02_positivo() -> None:
    pes = pes_df(linha_pes(unidade="Percentual (%)", realizado=120.0, realizado_txt="120,0%"))
    assert len(rodar("Q02", pes)) == 1


def test_q02_negativo() -> None:
    pes = pes_df(
        linha_pes(unidade="Percentual (%)", realizado=99.0),
        linha_pes(unidade="Valor absoluto", realizado=500.0),
    )
    assert rodar("Q02", pes).empty


# Q03 -----------------------------------------------------------------------

def test_q03_positivo() -> None:
    pes = pes_df(linha_pes(unidade="Valor absoluto", meta_txt="-", realizado_txt="0,4%"))
    assert len(rodar("Q03", pes)) == 1


def test_q03_negativo() -> None:
    pes = pes_df(
        linha_pes(unidade="Valor absoluto", meta_txt="1.635", realizado_txt="2824"),
        linha_pes(unidade="Percentual (%)", meta_txt="45,0%", realizado_txt="39,8%"),
    )
    assert rodar("Q03", pes).empty


# Q04 -----------------------------------------------------------------------

def test_q04_positivo_nas_duas_fontes() -> None:
    r = rodar("Q04", pes_df(linha_pes(divergencia_indice=True)),
              atend_df(linha_atend(divergencia_indice=True)))
    assert sorted(r["fonte"]) == ["atendimento", "pes"]


def test_q04_negativo() -> None:
    assert rodar("Q04").empty


# Q05 -----------------------------------------------------------------------

def test_q05_positivo() -> None:
    pes = pes_df(linha_pes(realizado=-1112970.70, realizado_txt="-R$    1.112.970,70"))
    r = rodar("Q05", pes)
    assert len(r) == 1
    assert "-R$    1.112.970,70" in r.iloc[0]["evidencia"]


def test_q05_negativo() -> None:
    assert rodar("Q05", pes_df(linha_pes(realizado=0.0))).empty


# Q06 -----------------------------------------------------------------------

def test_q06_positivo() -> None:
    pes = pes_df(linha_pes(meta=None, meta_txt="-", status_dado="sem_meta"))
    assert len(rodar("Q06", pes)) == 1


def test_q06_negativo() -> None:
    assert rodar("Q06", pes_df(linha_pes(**VAZIO))).empty


# Q07 -----------------------------------------------------------------------

def test_q07_positivo() -> None:
    pes = pes_df(linha_pes(realizado=0.0, realizado_txt="R$    - 0", status_dado="zero_suspeito"))
    assert len(rodar("Q07", pes)) == 1


def test_q07_negativo() -> None:
    assert rodar("Q07").empty


# Q08 -----------------------------------------------------------------------

def test_q08_positivo_nas_duas_fontes() -> None:
    r = rodar("Q08", pes_df(linha_pes(indice_analise=237.4)),
              atend_df(linha_atend(indice_analise=426.0)))
    assert len(r) == 2


def test_q08_periodo_parcial_acrescenta_aviso_na_evidencia() -> None:
    r = rodar("Q08", pes_df(linha_pes(indice_analise=250.0, mes_referencia=9),
                            linha_pes(indice_analise=250.0, mes_referencia=12, uf="RS")))
    parcial = r[r["uf"] == "SC"].iloc[0]["evidencia"]
    fechado = r[r["uf"] == "RS"].iloc[0]["evidencia"]
    assert parcial.endswith("(acumulado até o mês 9; índice tende a crescer até dezembro)")
    assert "acumulado até o mês" not in fechado


def test_q08_negativo_limite_200() -> None:
    r = rodar("Q08", pes_df(linha_pes(indice_analise=200.0)),
              atend_df(linha_atend(indice_analise=None)))
    assert r.empty


# Q09 -----------------------------------------------------------------------

def test_q09_positivo() -> None:
    atend = atend_df(linha_atend(previsto=0.0, previsto_txt="0", realizado=15.0))
    assert len(rodar("Q09", atend=atend)) == 1


def test_q09_negativo() -> None:
    atend = atend_df(
        linha_atend(previsto=0.0, previsto_txt="0", realizado=0.0),
        linha_atend(previsto=10.0, realizado=15.0),
    )
    assert rodar("Q09", atend=atend).empty


# Q10 -----------------------------------------------------------------------

def _empresas(realizado_pes: float, realizado_atend: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    pes = pes_df(linha_pes(cod_indicador="6", realizado=realizado_pes,
                           realizado_txt=f"{realizado_pes:.0f}"))
    atend = atend_df(linha_atend(acao="QUANTIDADE DE EMPRESAS ATENDIDAS",
                                 realizado=realizado_atend,
                                 realizado_txt=f"{realizado_atend:.0f}"))
    return pes, atend


def test_q10_positivo_registra_ambos_valores() -> None:
    r = rodar("Q10", *_empresas(3189, 4388))
    assert len(r) == 1
    assert "3189" in r.iloc[0]["evidencia"] and "4388" in r.iloc[0]["evidencia"]


def test_q10_negativo_valores_iguais() -> None:
    assert rodar("Q10", *_empresas(4388, 4388)).empty


# Q11 -----------------------------------------------------------------------

def test_q11_positivo() -> None:
    pes = pes_df(linha_pes(realizado=None, realizado_txt="-", status_dado="sem_realizado"))
    assert len(rodar("Q11", pes)) == 1


def test_q11_negativo_ano_aberto() -> None:
    pes = pes_df(linha_pes(mes_referencia=9, realizado=None, realizado_txt="-",
                           status_dado="sem_realizado"))
    assert rodar("Q11", pes).empty


# Q12 -----------------------------------------------------------------------

def _consolidacao_pes(cod: str, dn: float, **extra: Any) -> pd.DataFrame:
    return pes_df(
        linha_pes(uf="DN", cod_indicador=cod, meta=dn, realizado=dn, **extra),
        linha_pes(uf="SC", cod_indicador=cod, meta=60.0, realizado=60.0, **extra),
        linha_pes(uf="CETIQT", cod_indicador=cod, meta=40.0, realizado=40.0, **extra),
    )


def test_q12_positivo_dn_diverge_da_soma_com_cetiqt() -> None:
    r = rodar("Q12", _consolidacao_pes("2.1", 110.0))
    assert len(r) == 2  # meta e realizado
    ev = r.iloc[0]["evidencia"]
    assert "DN=110" in ev and "soma DRs+CETIQT=100" in ev and "+10.0%" in ev
    assert "deduplicado" not in ev


def test_q12_negativo_dentro_de_5pct_e_indicador_fora_da_lista() -> None:
    assert rodar("Q12", _consolidacao_pes("2.1", 104.0)).empty
    assert rodar("Q12", _consolidacao_pes("1", 150.0)).empty


def test_q12_empresas_anota_deduplicacao() -> None:
    r = rodar("Q12", _consolidacao_pes("6", 80.0))
    assert len(r) == 2 and r["evidencia"].str.contains("deduplicado").all()


def test_q12_atendimento_usa_tipo_unidade_e_nao_soma_vazio_como_zero() -> None:
    atend = atend_df(
        linha_atend(uf="DN", tipo_unidade="DN", acao="QUANTIDADE DE EMPRESAS ATENDIDAS",
                    previsto=None, realizado=80.0),
        linha_atend(uf="SC", tipo_unidade="DR", acao="QUANTIDADE DE EMPRESAS ATENDIDAS",
                    previsto=None, realizado=100.0),
    )
    r = rodar("Q12", atend=atend)
    assert len(r) == 1  # previsto vazio não é comparado
    assert r.iloc[0]["fonte"] == "atendimento"
    assert "1 de 1 unidades" in r.iloc[0]["evidencia"]


# Q13 -----------------------------------------------------------------------

def test_q13_positivo_meta_abaixo_do_realizado_anterior() -> None:
    pes = pes_df(
        linha_pes(ano=2025, realizado=120.0),
        linha_pes(ano=2026, mes_referencia=9, meta=100.0, realizado=50.0),
    )
    r = rodar("Q13", pes)
    assert len(r) == 1
    assert r.iloc[0]["evidencia"] == "meta 2026=100; realizado 2025=120; razão=0.83"


def test_q13_positivo_atendimento() -> None:
    atend = atend_df(linha_atend(ano=2025, realizado=200.0), linha_atend(ano=2026, previsto=150.0))
    assert len(rodar("Q13", atend=atend)) == 1


def test_q13_negativo_meta_maior_zero_ou_nao_dr() -> None:
    assert rodar("Q13", pes_df(linha_pes(ano=2025, realizado=90.0),
                               linha_pes(ano=2026, meta=100.0))).empty
    assert rodar("Q13", pes_df(linha_pes(ano=2025, realizado=120.0),
                               linha_pes(ano=2026, meta=0.0))).empty
    for uf in ("DN", "CETIQT"):
        assert rodar("Q13", pes_df(linha_pes(uf=uf, ano=2025, realizado=120.0),
                                   linha_pes(uf=uf, ano=2026, meta=100.0))).empty


# Q14 -----------------------------------------------------------------------

def _linha_negocio(total_realizado: float, realizado_b: float | None = 40.0) -> pd.DataFrame:
    return atend_df(
        linha_atend(acao="A", previsto=50.0, realizado=60.0),
        linha_atend(acao="B", previsto=50.0, realizado=realizado_b),
        linha_atend(acao="TOTAL", previsto=100.0, realizado=total_realizado),
    )


def test_q14_positivo() -> None:
    r = rodar("Q14", atend=_linha_negocio(101.0))
    assert len(r) == 1
    assert r.iloc[0]["evidencia"] == "realizado: TOTAL=101; soma das 2 ações=100; diferença=1"


def test_q14_negativo_soma_confere_ou_acao_vazia() -> None:
    assert rodar("Q14", atend=_linha_negocio(100.0)).empty
    assert rodar("Q14", atend=_linha_negocio(101.0, realizado_b=None)).empty


# Executor ------------------------------------------------------------------

def test_catalogo_ids_unicos_e_gravidades_validas() -> None:
    ids = [r["id"] for r in REGRAS]
    assert ids == [f"Q{i:02d}" for i in range(1, 15)]
    assert {r["gravidade"] for r in REGRAS} <= {"alta", "media", "informativa"}


def test_aplicar_e_resumir() -> None:
    pes = pes_df(
        linha_pes(uf="SC", cod_indicador="1", **VAZIO),
        linha_pes(uf="RS", cod_indicador="1", **VAZIO),
        linha_pes(uf="RS", cod_indicador="2", divergencia_indice=True),
    )
    achados = aplicar_regras(pes, atend_df())
    assert list(achados.columns) == COLUNAS_ACHADOS
    assert achados.iloc[0]["regra_id"] == "Q01"  # alta vem primeiro
    assert not achados["periodo_parcial"].any()  # mes_referencia == 12

    parciais = aplicar_regras(pes.assign(mes_referencia=9), atend_df())
    assert parciais["periodo_parcial"].all()

    resumo = resumir(achados, pes).set_index("regra_id")
    assert len(resumo) == len(REGRAS)
    assert resumo.loc["Q01", "n_ocorrencias"] == 1
    assert resumo.loc["Q01", "n_drs_afetados"] == 2  # "TODOS" = SC e RS
    assert resumo.loc["Q04", "n_drs_afetados"] == 1
    assert resumo.loc["Q09", "n_ocorrencias"] == 0


@pytest.mark.parametrize("regra", REGRAS, ids=lambda r: r["id"])
def test_regras_aceitam_tabelas_sem_ocorrencia(regra) -> None:
    r = regra["funcao"](pes_df(), atend_df())
    assert r.empty
