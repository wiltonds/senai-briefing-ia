"""Testes de src.fatos."""

import re

import pytest

from src.fatos import (
    PROCESSED_DIR,
    Dados,
    TABELAS,
    Num,
    carregar_dados,
    extrair_numeros,
    fmt_decimal,
    fmt_vezes,
    fmt_int,
    fmt_moeda,
    fmt_num,
    fmt_pct,
    fmt_publicado,
    gerar_fatos,
    montar,
)

# --------------------------------------------------------------------------
# Formatação pt-BR
# --------------------------------------------------------------------------

@pytest.mark.parametrize("valor, esperado", [
    (39.8, "39,8%"), (88.444, "88,4%"), (-25.2, "-25,2%"), (100.0, "100,0%"), (1234.5, "1.234,5%"),
])
def test_fmt_pct(valor, esperado):
    assert fmt_pct(valor) == esperado


def test_fmt_pct_sem_casas():
    assert fmt_pct(80.0, 0) == "80%"


@pytest.mark.parametrize("valor, esperado", [
    (7476, "7.476"), (59, "59"), (28345.0, "28.345"), (1234567, "1.234.567"), (0, "0"),
])
def test_fmt_int(valor, esperado):
    assert fmt_int(valor) == esperado


@pytest.mark.parametrize("valor, esperado", [(23.0, "23"), (23.5, "23,5"), (12345.67, "12.345,7")])
def test_fmt_num(valor, esperado):
    assert fmt_num(valor) == esperado


@pytest.mark.parametrize("valor, esperado", [
    (1_057_901_518.57, "R$ 1,06 bi"),
    (336_025_978.19, "R$ 336,0 mi"),
    (62_223_877.93, "R$ 62,2 mi"),
    (796_530.0, "R$ 796,5 mil"),
    (512.0, "R$ 512,00"),
    (-1_112_970.70, "-R$ 1,1 mi"),
])
def test_fmt_moeda(valor, esperado):
    assert fmt_moeda(valor) == esperado


def test_fmt_decimal():
    assert fmt_decimal(-0.2612) == "-0,26"


def test_fmt_vezes():
    assert fmt_vezes(3.7598) == "3,8x"


def test_fmt_publicado_preserva_texto_e_compacta_moeda():
    assert fmt_publicado("39,8%", 39.8, "Percentual (%)") == "39,8%"
    assert fmt_publicado("R$    1.057.901.518,57", 1_057_901_518.57, "(R$) Monetário") == "R$ 1,06 bi"


def test_extrair_numeros_ignora_codigos_alfanumericos():
    assert extrair_numeros("Regra Q01 no F001: 39,8% de 7.476 (R$ 1,06 bi), rho -0,26") == [
        "39,8%", "7.476", "1,06", "-0,26",
    ]


# --------------------------------------------------------------------------
# Montagem de frases
# --------------------------------------------------------------------------

def test_montar_lista_numeros_exatamente_como_na_frase():
    frase, nums = montar("{n} DRs com índice {i} ({nome})", n=Num("23"), i=Num("39,8%"),
                         nome="Indicador de 14 a 24 anos")
    assert frase == "23 DRs com índice 39,8% (Indicador de 14 a 24 anos)"
    assert nums == ["23", "39,8%", "14", "24"]
    assert all(n in frase for n in nums)


def test_montar_rejeita_numero_fixo_no_template():
    with pytest.raises(ValueError):
        montar("DRs com matrícula >= 100%: {n}", n=Num("23"))


# --------------------------------------------------------------------------
# Ficha gerada a partir de data/processed
# --------------------------------------------------------------------------

DADOS_DISPONIVEIS = all((PROCESSED_DIR / f"{t}.csv").exists() for t in TABELAS)
requer_dados = pytest.mark.skipif(not DADOS_DISPONIVEIS, reason="data/processed incompleto")


@pytest.fixture(scope="module")
def dados():
    return carregar_dados()


# Milhar pt-BR ("7.476", "1.234.567,8") ou código de indicador ("2.1", "14.2").
RE_MILHAR_BR = re.compile(r"^-?\d{1,3}(?:\.\d{3})+(?:,\d+)?%?$")
RE_CODIGO = re.compile(r"^\d{1,2}\.\d$")


def _checar_linguagem(f):
    """Frase executiva: sem nomes de coluna e sem decimal no formato americano."""
    frase = f["frase"]
    assert "_txt" not in frase and "_" not in frase, f"{f['id']}: nome técnico na frase: {frase}"
    assert "=" not in frase, f"{f['id']}: formato chave=valor na frase: {frase}"
    for token in extrair_numeros(frase):
        if "." in token:
            assert RE_MILHAR_BR.match(token) or RE_CODIGO.match(token), (
                f"{f['id']}: número fora do padrão pt-BR {token!r}: {frase}"
            )


def _checar_ficha(fatos):
    assert fatos, "ficha vazia"
    ids = [f["id"] for f in fatos]
    assert len(ids) == len(set(ids)), "IDs duplicados"
    assert ids == [f"F{i:03d}" for i in range(1, len(fatos) + 1)]
    for f in fatos:
        assert {"id", "categoria", "titulo", "frase", "numeros_permitidos", "fonte"} <= f.keys()
        assert f["fonte"]["tipo"] in {"api", "pdf", "regra"}
        _checar_linguagem(f)
        for n in f["numeros_permitidos"]:
            assert n in f["frase"], f"{f['id']}: {n!r} não aparece na frase"
        for token in extrair_numeros(f["frase"]):
            assert any(token in n for n in f["numeros_permitidos"]), (
                f"{f['id']}: número {token!r} fora de numeros_permitidos: {f['frase']}"
            )


@requer_dados
def test_ficha_br(dados):
    fatos = gerar_fatos(dados, "BR")
    _checar_ficha(fatos)
    categorias = {f["categoria"] for f in fatos}
    assert categorias == {"resultado_nacional", "assimetria", "cruzamento", "apoio",
                          "projecao_2026", "calibracao", "qualidade", "triangulacao"}
    destaques = {f["fonte"]["regra_id"] for f in fatos if f.get("destaque")}
    assert {"Q01", "Q02", "Q08", "Q12", "Q13"} <= destaques


@requer_dados
def test_ficha_br_valores_conhecidos(dados):
    frases = " | ".join(f["frase"] for f in gerar_fatos(dados, "BR"))
    assert "realizado 39,8%" in frases
    assert "realizado 7.476" in frases
    assert "realizado R$ 1,06 bi" in frases
    assert "Indicador 3 (" in frases and "realizado 2025 de 34,1%; meta 2026 de 14,4%" in frases
    assert "R$ 336,0 mi" in frases and "-20,3%" in frases
    assert "aceleração mediana necessária de" in frases


@requer_dados
def test_calibracao_resumo_q13_por_fonte(dados):
    resumo = [f for f in gerar_fatos(dados, "BR")
              if f["categoria"] == "calibracao" and "por_fonte" in f]
    assert len(resumo) == 1
    q13 = dados["qualidade_achados"]
    q13 = q13[q13["regra_id"] == "Q13"]
    assert resumo[0]["por_fonte"] == {
        "pes": int((q13["fonte"] == "pes").sum()),
        "atendimento": int((q13["fonte"] == "atendimento").sum()),
    }


@requer_dados
def test_calibracao_ignora_polaridade_nao_positiva(dados, caplog):
    pes = dados["pes"].copy()
    dn_3 = (pes["uf"] == "DN") & (pes["cod_indicador"] == "3")
    pes.loc[dn_3, "polaridade"] = "(-) Menor, melhor"
    alterado = Dados({**dados.tabelas, "pes": pes}, dados.sha256_csv, dados.sha256_pdf)
    with caplog.at_level("WARNING"):
        fatos = gerar_fatos(alterado, "BR")
    assert not any(f["categoria"] == "calibracao" and f.get("cod_indicador") == "3" for f in fatos)
    assert any(f["categoria"] == "calibracao" and f.get("cod_indicador") == "7" for f in fatos)
    assert "polaridade" in caplog.text


def test_checar_linguagem_reprova_formato_tecnico():
    for frase in ["meta_txt=60,0%", "DN=336025978.19", "diferença de -20.3%"]:
        with pytest.raises(AssertionError):
            _checar_linguagem({"id": "F999", "frase": frase})


@requer_dados
@pytest.mark.parametrize("uf", ["AC", "GO", "SP"])
def test_ficha_dr(dados, uf):
    fatos = gerar_fatos(dados, uf)
    _checar_ficha(fatos)
    assert {"resultado_dr", "comparacao", "assimetria", "apoio", "calibracao"} <= {
        f["categoria"] for f in fatos}


@requer_dados
def test_ficha_dr_exclui_q02_do_farol_e_da_calibracao(dados):
    fatos = gerar_fatos(dados, "GO")
    assert not any(f["categoria"] in {"resultado_dr", "calibracao"}
                   and f.get("cod_indicador") == "13.1" for f in fatos)


@requer_dados
def test_escopo_invalido(dados):
    with pytest.raises(ValueError):
        gerar_fatos(dados, "XX")


# --------------------------------------------------------------------------
# Consistência: o mesmo número tem o mesmo texto em toda a ficha
# --------------------------------------------------------------------------

_PCT = r"-?[\d.]+(?:,\d+)?%"
RE_RESULTADO = re.compile(
    r"^Indicador (?P<cod>[\d.]+) \(.*\), 2025: meta (?P<meta>.+?), realizado (?P<real>.+?), "
    rf"índice (?P<ind>{_PCT});"
)
RE_COMPARACAO = re.compile(
    rf"^Indicador (?P<cod>[\d.]+): índice de (?P<uf>[A-Z]{{2}}) (?P<ind>{_PCT}); "
    rf"mediana nacional dos DRs (?P<mn>{_PCT}); mediana da região (?P<reg>[\w-]+) (?P<mr>{_PCT})"
)
RE_CRUZAMENTO_DR = re.compile(
    rf"^(?P<uf>[A-Z]{{2}}): índice de matrículas (?P<mat>{_PCT}), índice de empregabilidade "
    rf"(?P<emp>{_PCT}), índice de fidelização (?P<fid>{_PCT})"
)
RE_APOIO = re.compile(r"^(?P<uf>[A-Z]{2}) \([\w-]+\): .*Críticos: (?P<lista>.+?)\.?$")
RE_ITEM_CRITICO = re.compile(rf"([\d.]+) \(({_PCT})\)")
RE_CALIBRACAO = re.compile(
    r"^Indicador (?P<cod>[\d.]+) \(.*\), (?P<uf>[A-Z]{2}): realizado 2025 de (?P<real>.+?); meta 2026"
)


def observacoes(fatos: list[dict], escopo: str) -> list[tuple[tuple[str, str, str], str, str]]:
    """((unidade, indicador, métrica), valor formatado, id do fato) para cada número reconhecido."""
    unidade_escopo = "DN" if escopo == "BR" else escopo
    obs = []
    for f in fatos:
        frase, fid = f["frase"], f["id"]
        if m := RE_RESULTADO.match(frase):
            for metrica, valor in (("meta_2025", m["meta"]), ("realizado_2025", m["real"]),
                                   ("indice_2025", m["ind"])):
                obs.append(((unidade_escopo, m["cod"], metrica), valor, fid))
        if m := RE_COMPARACAO.match(frase):
            obs += [((m["uf"], m["cod"], "indice_2025"), m["ind"], fid),
                    (("DRs", m["cod"], "mediana_indice"), m["mn"], fid),
                    ((m["reg"], m["cod"], "mediana_indice"), m["mr"], fid)]
        if m := RE_CRUZAMENTO_DR.match(frase):
            obs += [((m["uf"], "AT-matriculas", "indice_2025"), m["mat"], fid),
                    ((m["uf"], "1", "indice_2025"), m["emp"], fid),
                    ((m["uf"], "4.2", "indice_2025"), m["fid"], fid)]
        if m := RE_APOIO.match(frase):
            for cod, valor in RE_ITEM_CRITICO.findall(m["lista"]):
                obs.append(((m["uf"], cod, "indice_2025"), valor, fid))
        if m := RE_CALIBRACAO.match(frase):
            obs.append(((m["uf"], m["cod"], "realizado_2025"), m["real"], fid))
    return obs


TODOS_ESCOPOS = ["BR", "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT",
                 "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO"]


def _divergentes(obs: list[tuple[tuple[str, str, str], str, str]]) -> dict:
    por_chave: dict[tuple, dict[str, list[str]]] = {}
    for chave, valor, fid in obs:
        por_chave.setdefault(chave, {}).setdefault(valor, []).append(fid)
    return {c: v for c, v in por_chave.items() if len(v) > 1}


@requer_dados
def test_todos_os_escopos_e_validos(dados):
    from src.fatos import ufs_validas
    assert sorted(TODOS_ESCOPOS[1:]) == ufs_validas(dados)


@requer_dados
def test_mesmo_numero_mesmo_texto_entre_todas_as_fichas(dados):
    """Mesmo indicador/ano/unidade => mesmo texto em BR + 27 DRs (ex.: SP 85,4% x 85,5%)."""
    obs = []
    for escopo in TODOS_ESCOPOS:
        obs += [(c, v, f"{escopo}:{fid}") for c, v, fid in observacoes(gerar_fatos(dados, escopo), escopo)]
    divergentes = _divergentes(obs)
    assert not divergentes, f"mesmo (unidade, indicador, métrica) com textos diferentes: {divergentes}"


@requer_dados
@pytest.mark.parametrize("escopo", TODOS_ESCOPOS)
def test_mesmo_numero_mesmo_texto_em_toda_a_ficha(dados, escopo):
    obs = observacoes(gerar_fatos(dados, escopo), escopo)
    por_chave: dict[tuple, dict[str, list[str]]] = {}
    for chave, valor, fid in obs:
        por_chave.setdefault(chave, {}).setdefault(valor, []).append(fid)
    repetidas = [c for c, v in por_chave.items() if sum(map(len, v.values())) > 1]
    assert repetidas, "nenhum número aparece em mais de uma frase; o teste não verificaria nada"
    divergentes = {c: v for c, v in por_chave.items() if len(v) > 1}
    assert not divergentes, f"mesmo (unidade, indicador, métrica) com textos diferentes: {divergentes}"


@pytest.mark.parametrize("valor, esperado", [
    (77.25, "77,3%"), (77.2511848, "77,3%"), (77.24999, "77,2%"), (-25.25, "-25,3%"), (0.05, "0,1%"),
])
def test_fmt_pct_round_half_up(valor, esperado):
    assert fmt_pct(valor) == esperado
