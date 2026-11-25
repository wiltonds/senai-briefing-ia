"""Testes de src.diagnostico."""

from typing import Any

import pandas as pd
import pytest

from src.diagnostico import (
    aplicar_achados,
    cruzamento_volume_resultado,
    diag_dr,
    diag_indicador,
    diag_regiao,
    diagnosticar,
    farol,
    filtrar_validos,
    leitura_cruzamento,
    spearman,
)


def _com_tipo(linha: dict[str, Any]) -> dict[str, Any]:
    """Preenche ``tipo_unidade`` a partir da uf, como em src.normaliza."""
    padrao = linha["uf"] if linha["uf"] in ("DN", "CETIQT") else "DR"
    return {"tipo_unidade": padrao} | linha


def linha_pes(**campos: Any) -> dict[str, Any]:
    """Linha de pes.csv válida (ok, 2025 fechado) por padrão."""
    base = {
        "ano": 2025, "mes_referencia": 12, "uf": "SC", "regiao": "Sul",
        "cod_indicador": "1", "indicador": "Empregabilidade", "perspectiva": "Clientes",
        "polaridade": "(+) Maior, melhor", "meta_txt": "100", "realizado_txt": "90",
        "meta": 100.0, "realizado": 90.0, "indice_analise": 90.0, "status_dado": "ok",
    }
    return _com_tipo(base | campos)


def linha_atend(**campos: Any) -> dict[str, Any]:
    """Linha TOTAL de matrículas válida por padrão."""
    base = {
        "ano": 2025, "mes_referencia": 12, "uf": "SC", "regiao": "Sul",
        "linha_negocio": "Programas (matrículas)", "acao": "TOTAL",
        "previsto": 100.0, "realizado": 120.0, "indice_analise": 120.0, "status_dado": "ok",
    }
    return _com_tipo(base | campos)


# Farol ---------------------------------------------------------------------

@pytest.mark.parametrize(
    ("indice", "esperado"),
    [
        (150.0, "atingido"),
        (100.0, "atingido"),     # limite inferior de "atingido"
        (99.99, "atencao"),
        (80.0, "atencao"),       # limite inferior de "atencao"
        (79.99, "critico"),
        (0.0, "critico"),
        (None, None),
        (float("nan"), None),
    ],
)
def test_farol_limites(indice: float | None, esperado: str | None) -> None:
    assert farol(indice) == esperado


# Filtro e exclusões -----------------------------------------------------------

@pytest.mark.parametrize(
    "campos",
    [
        {"status_dado": "zero_suspeito", "realizado_txt": "R$ - 0", "realizado": 0.0},
        {"status_dado": "sem_meta", "meta": None},
        {"status_dado": "sem_dado", "meta": None, "realizado": None, "indice_analise": None},
        {"meta": 0.0},
        {"mes_referencia": 9},
        {"ano": 2026},
    ],
)
def test_filtrar_validos_exclui(campos: dict[str, Any]) -> None:
    df = pd.DataFrame([linha_pes(), linha_pes(uf="RS", **campos)])
    validos = filtrar_validos(df, 2025, "meta")
    assert validos["uf"].tolist() == ["SC"]


def test_zero_suspeito_nao_entra_no_farol_do_dr() -> None:
    df = pd.DataFrame([
        linha_pes(cod_indicador="1", indice_analise=120.0),
        linha_pes(cod_indicador="14.2", status_dado="zero_suspeito",
                  realizado=0.0, indice_analise=0.0),
    ])
    r = diag_dr(filtrar_validos(df, 2025, "meta")).iloc[0]
    assert r["n_indicadores_com_dado"] == 1
    assert r["n_critico"] == 0
    assert r["indicadores_criticos"] == ""


def test_dn_fica_fora_das_distribuicoes_e_vira_referencia() -> None:
    df = pd.DataFrame([
        linha_pes(uf="SC", indice_analise=70.0),
        linha_pes(uf="RS", regiao="Sul", indice_analise=110.0),
        linha_pes(uf="DN", regiao="Brasil", indice_analise=5.0,
                  meta_txt="45,0%", realizado_txt="2,3%"),
    ])
    validos = filtrar_validos(df, 2025, "meta")
    ind = diag_indicador(validos, df).iloc[0]
    assert ind["n_drs_com_dado"] == 2
    assert ind["n_critico"] == 1 and ind["n_atingido"] == 1
    assert ind["mediana_indice"] == 90.0
    assert ind["dr_menor_indice"] == "SC" and ind["dr_maior_indice"] == "RS"
    assert ind["indice_dn"] == 5.0
    assert ind["meta_dn_txt"] == "45,0%"

    assert "DN" not in diag_dr(validos)["uf"].tolist()


def test_cetiqt_fica_fora_do_farol_e_do_cruzamento() -> None:
    df = pd.DataFrame([
        linha_pes(uf="SC", indice_analise=70.0),
        linha_pes(uf="CETIQT", regiao="Sudeste", indice_analise=200.0),
    ])
    validos = filtrar_validos(df, 2025, "meta")
    assert diag_indicador(validos, df).iloc[0]["n_drs_com_dado"] == 1
    assert diag_dr(validos)["uf"].tolist() == ["SC"]
    assert diag_regiao(validos)["regiao"].tolist() == ["Sul"]
    atend = filtrar_validos(pd.DataFrame([linha_atend(uf="CETIQT", regiao="Sudeste")]),
                            2025, "previsto")
    assert cruzamento_volume_resultado(validos, atend)["uf"].tolist() == ["SC"]


def test_diag_dr_lista_criticos_formatados() -> None:
    df = pd.DataFrame([
        linha_pes(cod_indicador="10", indice_analise=50.0),
        linha_pes(cod_indicador="2.1", indice_analise=65.34),
        linha_pes(cod_indicador="1", indice_analise=100.0),
    ])
    r = diag_dr(filtrar_validos(df, 2025, "meta")).iloc[0]
    assert r["indicadores_criticos"] == "2.1 (65,3%); 10 (50,0%)"
    assert r["pct_atingido"] == pytest.approx(33.3)


def test_polaridade_negativa_interrompe() -> None:
    df = pd.DataFrame([linha_pes(polaridade="(-) Menor, melhor")])
    with pytest.raises(ValueError, match="polaridade"):
        diagnosticar(df, pd.DataFrame([linha_atend()]), achados_df(), 2025)


# Achados de qualidade (Q02 exclui, Q08 sinaliza) -------------------------------

def achado(regra_id: str, uf: str, item: str, ano: int = 2025, fonte: str = "pes") -> dict[str, Any]:
    return {"regra_id": regra_id, "fonte": fonte, "ano": ano, "uf": uf, "item": item}


def achados_df(*linhas: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(list(linhas), columns=["regra_id", "fonte", "ano", "uf", "item"])


def _farol_com_achados() -> pd.DataFrame:
    df = pd.DataFrame([
        linha_pes(uf="GO", regiao="Centro-Oeste", cod_indicador="13.1",
                  realizado_txt="138,1%", indice_analise=230.2),
        linha_pes(uf="GO", regiao="Centro-Oeste", cod_indicador="7", indice_analise=250.0),
        linha_pes(uf="GO", regiao="Centro-Oeste", cod_indicador="1", indice_analise=70.0),
        linha_pes(uf="SC", cod_indicador="7", indice_analise=150.0),
        linha_pes(uf="SC", cod_indicador="13.1", indice_analise=90.0),
    ])
    achados = achados_df(
        achado("Q02", "GO", "13.1"),
        achado("Q08", "GO", "13.1"),            # Q02 prevalece: linha some
        achado("Q08", "GO", "7"),
        achado("Q08", "GO", "7", ano=2026),     # outro ano: não casa
        achado("Q02", "SC", "13.1", ano=2026),  # outro ano: não casa
        achado("Q08", "SC", "TOTAL", fonte="atendimento"),
    )
    return aplicar_achados(filtrar_validos(df, 2025, "meta"), achados)


def test_q02_exclui_do_farol() -> None:
    farol_df = _farol_com_achados()
    chaves = set(zip(farol_df["uf"], farol_df["cod_indicador"]))
    assert ("GO", "13.1") not in chaves
    assert ("SC", "13.1") in chaves  # achado de outro ano não exclui


def test_q02_excluido_e_logado(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="diagnostico"):
        _farol_com_achados()
    assert "GO" in caplog.text and "13.1" in caplog.text and "138,1%" in caplog.text


def test_q08_sinaliza_e_permanece_no_farol() -> None:
    farol_df = _farol_com_achados().set_index(["uf", "cod_indicador"])
    assert bool(farol_df.loc[("GO", "7"), "meta_subcalibrada"]) is True
    assert farol_df.loc[("GO", "7"), "farol"] == "atingido"
    assert not farol_df.loc[("GO", "1"), "meta_subcalibrada"]
    assert not farol_df.loc[("SC", "7"), "meta_subcalibrada"]


def test_contagens_meta_subcalibrada() -> None:
    farol_df = _farol_com_achados()
    ind = diag_indicador(farol_df, farol_df).set_index("cod_indicador")
    assert ind.loc["7", "n_meta_subcalibrada"] == 1
    assert ind.loc["7", "n_drs_com_dado"] == 2
    assert ind.loc["13.1", "n_drs_com_dado"] == 1  # GO excluído por Q02

    dr = diag_dr(farol_df).set_index("uf")
    assert dr.loc["GO", "n_meta_subcalibrada"] == 1
    assert dr.loc["SC", "n_meta_subcalibrada"] == 0


# Resumo e cobertura -------------------------------------------------------------

def test_diag_dr_resumo_e_cobertura() -> None:
    df = pd.DataFrame([
        linha_pes(uf="SC", cod_indicador="1", indice_analise=120.0),
        linha_pes(uf="SC", cod_indicador="2.1", indice_analise=90.0),
        linha_pes(uf="SC", cod_indicador="3", indice_analise=100.0),
        linha_pes(uf="SC", cod_indicador="6", indice_analise=50.0),
        linha_pes(uf="RS", cod_indicador="1", indice_analise=120.0),
        linha_pes(uf="DN", regiao="Brasil", cod_indicador="99", indice_analise=100.0),
    ])
    dr = diag_dr(filtrar_validos(df, 2025, "meta")).set_index("uf")
    assert dr.loc["SC", "resumo"] == "2 de 4 atingidos"
    assert dr.loc["SC", "cobertura"] == 1.0   # 4 de 4 (o indicador só do DN não conta)
    assert dr.loc["RS", "resumo"] == "1 de 1 atingidos"
    assert dr.loc["RS", "cobertura"] == 0.25


# Cruzamento ------------------------------------------------------------------

@pytest.mark.parametrize(
    ("volume", "resultado", "esperado"),
    [
        (120.0, 70.0, "volume ok, resultado crítico"),
        (100.0, 79.9, "volume ok, resultado crítico"),
        (120.0, 100.0, "volume ok, resultado ok"),
        (90.0, 85.0, "volume em atenção, resultado em atenção"),
        (70.0, 110.0, "volume crítico, resultado ok"),
        (None, 90.0, "sem dado de volume, resultado em atenção"),
        (110.0, None, "volume ok, sem dado de resultado"),
    ],
)
def test_leitura_cruzamento(volume, resultado, esperado: str) -> None:
    assert leitura_cruzamento(volume, resultado) == esperado


def test_cruzamento_volume_resultado() -> None:
    pes = pd.DataFrame([
        linha_pes(uf="SC", cod_indicador="1", indice_analise=70.0, realizado=31.5),
        linha_pes(uf="SC", cod_indicador="4.2", indice_analise=95.0),
        linha_pes(uf="DN", regiao="Brasil", cod_indicador="1", indice_analise=88.0),
    ])
    atend = pd.DataFrame([
        linha_atend(uf="SC", indice_analise=135.0),
        linha_atend(uf="SC", acao="EDUCAÇÃO SUPERIOR", indice_analise=10.0),  # não é TOTAL
        linha_atend(uf="DN", regiao="Brasil", indice_analise=117.0),
    ])
    cruz = cruzamento_volume_resultado(
        filtrar_validos(pes, 2025, "meta"), filtrar_validos(atend, 2025, "previsto")
    )
    assert cruz["uf"].tolist() == ["SC"]  # DN excluído
    r = cruz.iloc[0]
    assert r["indice_matriculas"] == 135.0
    assert r["indice_empregabilidade"] == 70.0
    assert r["realizado_empregabilidade"] == 31.5
    assert r["indice_fidelizacao"] == 95.0
    assert r["leitura"] == "volume ok, resultado crítico"


def test_spearman() -> None:
    rho, n = spearman(pd.Series([1, 2, 3, 4]), pd.Series([10, 20, 30, 40]))
    assert rho == pytest.approx(1.0) and n == 4
    rho, n = spearman(pd.Series([1, 2, None]), pd.Series([3, None, 5]))
    assert rho is None and n == 1
