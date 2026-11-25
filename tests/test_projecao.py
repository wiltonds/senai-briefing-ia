"""Testes de src.projecao."""

from typing import Any

import pandas as pd
import pytest

from src.projecao import (
    SIT_ACELERACAO,
    SIT_ATINGIDA,
    SIT_INCOMPLETO,
    SIT_NO_RITMO,
    SIT_RISCO,
    gerar_projecao,
    metodo_texto,
    projetar_linha,
)


def _com_tipo(linha: dict[str, Any]) -> dict[str, Any]:
    """Preenche ``tipo_unidade`` a partir da uf, como em src.normaliza."""
    padrao = linha["uf"] if linha["uf"] in ("DN", "CETIQT") else "DR"
    return {"tipo_unidade": padrao} | linha


def linha_pes(**campos: Any) -> dict[str, Any]:
    """Linha de pes.csv válida (2026, mês 9, indicador 2.1) por padrão."""
    base = {
        "ano": 2026, "mes_referencia": 9, "uf": "SC", "regiao": "Sul",
        "cod_indicador": "2.1", "indicador": "Consultorias", "meta_txt": "100",
        "realizado_txt": "75", "meta": 100.0, "realizado": 75.0, "status_dado": "ok",
    }
    return _com_tipo(base | campos)


def linha_atend(**campos: Any) -> dict[str, Any]:
    """Linha de atendimento.csv válida (matrículas TOTAL, 2026, mês 9) por padrão."""
    base = {
        "ano": 2026, "mes_referencia": 9, "uf": "SC", "regiao": "Sul",
        "linha_negocio": "Programas (matrículas)", "acao": "TOTAL",
        "previsto_txt": "1.000", "realizado_txt": "750",
        "previsto": 1000.0, "realizado": 750.0, "status_dado": "ok",
    }
    return _com_tipo(base | campos)


# --------------------------------------------------------------------------
# projetar_linha
# --------------------------------------------------------------------------

def test_calculos_basicos() -> None:
    r = projetar_linha(realizado=60.0, meta=100.0, mes=9)
    assert r["ritmo_atual_mensal"] == pytest.approx(60 / 9)
    assert r["projecao_dez"] == pytest.approx(80.0)
    assert r["indice_projetado"] == pytest.approx(80.0)
    assert r["falta_para_meta"] == pytest.approx(40.0)
    assert r["ritmo_necessario_mensal"] == pytest.approx(40 / 3)
    assert r["aceleracao_necessaria"] == pytest.approx((40 / 3) / (60 / 9))


def test_meta_ja_atingida() -> None:
    r = projetar_linha(realizado=120.0, meta=100.0, mes=9)
    assert r["situacao"] == SIT_ATINGIDA
    assert r["falta_para_meta"] == 0.0
    assert r["ritmo_necessario_mensal"] == 0.0
    assert r["aceleracao_necessaria"] == 0.0


def test_realizado_igual_meta_e_atingida() -> None:
    assert projetar_linha(100.0, 100.0, 9)["situacao"] == SIT_ATINGIDA


@pytest.mark.parametrize(
    ("realizado", "situacao"),
    [
        (75.0, SIT_NO_RITMO),       # projeção 100,0 -> limite inferior de "no ritmo"
        (74.9, SIT_ACELERACAO),     # projeção 99,87
        (60.0, SIT_ACELERACAO),     # projeção 80,0 -> limite inferior de "exige aceleração"
        (59.9, SIT_RISCO),          # projeção 79,87
    ],
)
def test_limites_80_e_100(realizado: float, situacao: str) -> None:
    assert projetar_linha(realizado, 100.0, 9)["situacao"] == situacao


def test_ritmo_atual_zero() -> None:
    r = projetar_linha(realizado=0.0, meta=30.0, mes=9)
    assert r["ritmo_atual_mensal"] == 0.0
    assert r["indice_projetado"] == 0.0
    assert r["ritmo_necessario_mensal"] == pytest.approx(10.0)
    assert r["aceleracao_necessaria"] is None
    assert r["situacao"] == SIT_RISCO


@pytest.mark.parametrize(("meta", "mes"), [(0.0, 9), (-5.0, 9), (100.0, 12), (100.0, 0)])
def test_entradas_invalidas(meta: float, mes: int) -> None:
    with pytest.raises(ValueError):
        projetar_linha(10.0, meta, mes)


# --------------------------------------------------------------------------
# gerar_projecao
# --------------------------------------------------------------------------

def test_cetiqt_fica_na_tabela_mas_fora_das_contagens() -> None:
    pes = pd.DataFrame([
        linha_pes(uf="SC", realizado=80.0),
        linha_pes(uf="CETIQT", regiao="Sudeste", realizado=10.0),
        linha_pes(uf="CETIQT", regiao="Sudeste", cod_indicador="14.2",
                  status_dado="zero_suspeito"),
    ])
    tabelas = gerar_projecao(pes, pd.DataFrame([linha_atend()]), 2026)
    proj, res = tabelas["projecao_2026"], tabelas["projecao_resumo"]
    assert set(proj[proj["fonte"] == "pes"]["tipo_unidade"]) == {"DR", "CETIQT"}
    r21 = res[res["cod_indicador"] == "2.1"].iloc[0]
    assert r21["n_drs"] == 1 and r21["n_risco_alto"] == 0
    assert "14.2" not in res["cod_indicador"].tolist()  # zero_suspeito do CETIQT não conta


def test_gerar_projecao_escopo_e_resumo() -> None:
    pes = pd.DataFrame([
        linha_pes(uf="SC", realizado=80.0),                         # no ritmo
        linha_pes(uf="PR", realizado=110.0),                        # atingida
        linha_pes(uf="DN", regiao=None, realizado=50.0),            # DN, risco
        linha_pes(uf="RS", status_dado="sem_dado"),                 # fora: status
        linha_pes(uf="SP", meta=0.0),                               # fora: meta 0
        linha_pes(uf="MG", cod_indicador="1"),                      # fora: não acumulado
    ])
    atend = pd.DataFrame([
        linha_atend(uf="SC"),                                       # matrículas, no ritmo
        linha_atend(uf="SC", linha_negocio="Tecnologia e Inovação",
                    acao="QUANTIDADE DE EMPRESAS ATENDIDAS", previsto=100.0, realizado=65.0),
        linha_atend(uf="SC", acao="EDUCAÇÃO PARA O TRABALHO"),      # fora: não é TOTAL
        linha_atend(uf="PR", previsto=0.0),                         # fora: previsto 0
    ])
    tabelas = gerar_projecao(pes, atend, 2026)
    proj, res = tabelas["projecao_2026"], tabelas["projecao_resumo"]

    assert len(proj) == 5
    assert set(proj["metodo"]) == {"linear (realizado/9*12); não considera sazonalidade"}
    assert set(proj[proj["fonte"] == "pes"]["uf"]) == {"SC", "PR", "DN"}

    r21 = res[res["cod_indicador"] == "2.1"].iloc[0]
    assert r21["n_drs"] == 2
    assert r21["n_meta_ja_atingida"] == 1 and r21["n_no_ritmo"] == 1
    assert r21["situacao_dn"] == SIT_RISCO

    emp = proj[proj["cod_indicador"] == "AT-empresas_sti"].iloc[0]
    assert emp["situacao"] == SIT_ACELERACAO
    r_mat = res[res["cod_indicador"] == "AT-matriculas"].iloc[0]
    assert r_mat["n_no_ritmo"] == 1 and pd.isna(r_mat["situacao_dn"])


def test_dn_consolidado_incompleto_com_zero_suspeito(caplog: pytest.LogCaptureFixture) -> None:
    pes = pd.DataFrame([
        linha_pes(uf="SC", cod_indicador="14.2", realizado=80.0),
        linha_pes(uf="RJ", cod_indicador="14.2", status_dado="zero_suspeito", realizado=0.0),
        linha_pes(uf="BA", cod_indicador="14.2", status_dado="zero_suspeito", realizado=0.0),
        linha_pes(uf="DN", regiao=None, cod_indicador="14.2", realizado=90.0),
        linha_pes(uf="DN", regiao=None, cod_indicador="2.1", realizado=90.0),  # sem faltantes
    ])
    atend = pd.DataFrame([linha_atend()])
    with caplog.at_level("WARNING", logger="projecao"):
        tabelas = gerar_projecao(pes, atend, 2026)
    proj, res = tabelas["projecao_2026"], tabelas["projecao_resumo"]

    dn142 = proj[(proj["cod_indicador"] == "14.2") & (proj["uf"] == "DN")].iloc[0]
    assert dn142["situacao"] == SIT_INCOMPLETO
    for col in ("projecao_dez", "indice_projetado", "ritmo_necessario_mensal",
                "aceleracao_necessaria"):
        assert pd.isna(dn142[col]), col
    assert dn142["ritmo_atual_mensal"] == pytest.approx(10.0)   # 90/9, mantido
    assert set(proj[proj["cod_indicador"] == "14.2"]["uf"]) == {"SC", "DN"}

    dn21 = proj[(proj["cod_indicador"] == "2.1") & (proj["uf"] == "DN")].iloc[0]
    assert dn21["situacao"] == SIT_NO_RITMO

    r142 = res[res["cod_indicador"] == "14.2"].iloc[0]
    assert r142["n_drs"] == 1 and r142["n_drs_zero_suspeito"] == 2
    assert r142["situacao_dn"] == SIT_INCOMPLETO and pd.isna(r142["indice_projetado_dn"])
    assert "faltam 2 DRs" in caplog.text


def test_mes_derivado_do_dado() -> None:
    pes = pd.DataFrame([linha_pes(mes_referencia=6, realizado=50.0)])
    atend = pd.DataFrame([linha_atend(mes_referencia=6)])
    proj = gerar_projecao(pes, atend, 2026)["projecao_2026"]
    linha = proj[proj["fonte"] == "pes"].iloc[0]
    assert linha["projecao_dez"] == pytest.approx(100.0)        # 50/6*12
    assert linha["ritmo_necessario_mensal"] == pytest.approx(50 / 6)
    assert linha["metodo"] == metodo_texto(6) == "linear (realizado/6*12); não considera sazonalidade"


def test_mes_com_mais_de_um_valor() -> None:
    pes = pd.DataFrame([linha_pes(mes_referencia=6)])
    atend = pd.DataFrame([linha_atend(mes_referencia=9)])
    with pytest.raises(ValueError, match="valor único"):
        gerar_projecao(pes, atend, 2026)


def test_mes_esperado_diferente_do_dado() -> None:
    pes = pd.DataFrame([linha_pes()])
    atend = pd.DataFrame([linha_atend()])
    with pytest.raises(ValueError, match="diferente do esperado"):
        gerar_projecao(pes, atend, 2026, mes_esperado=6)
    assert len(gerar_projecao(pes, atend, 2026, mes_esperado=9)["projecao_2026"]) == 2
