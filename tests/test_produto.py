"""Testes de src.produto (dados da página HTML)."""

import json
import re
import shutil
from pathlib import Path

import pandas as pd
import pytest

from src.produto import (
    ESCOPOS,
    FIGS_DIR,
    MANIFEST_PATH,
    OUTPUTS_DIR,
    NOME_CURTO,
    PROCESSED_DIR,
    nome_curto,
    status_por_criticos,
    pct_barra,
    pct_ou_sem_dado,
    status,
    gerar_tudo,
)

STATUS_VALIDOS = {"atingido", "atencao", "critico", "sem_dado"}
STATUS_LINHA = STATUS_VALIDOS | {"neutro"}


@pytest.fixture(scope="module")
def app_data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Gera os JSON num diretório temporário (figuras existentes copiadas para lá)."""
    base = tmp_path_factory.mktemp("produto")
    figs = base / "figs"
    if FIGS_DIR.exists():
        shutil.copytree(FIGS_DIR, figs)
    destino = base / "app_data"
    gerar_tudo(PROCESSED_DIR, MANIFEST_PATH, OUTPUTS_DIR, destino, figs)
    return destino


def _ler(app_data: Path, nome: str) -> dict:
    return json.loads((app_data / f"{nome}.json").read_text(encoding="utf-8"))


def _numeros_crus(obj, caminho="") -> list[str]:
    """Caminhos de valores numéricos fora de ``pct_barra``."""
    if isinstance(obj, dict):
        return [c for k, v in obj.items() if k != "pct_barra"
                for c in _numeros_crus(v, f"{caminho}.{k}")]
    if isinstance(obj, list):
        return [c for i, v in enumerate(obj) for c in _numeros_crus(v, f"{caminho}[{i}]")]
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return [caminho]
    return []


def test_gera_28_escopos_e_regioes(app_data: Path) -> None:
    assert len(ESCOPOS) == 28
    for e in ESCOPOS:
        assert (app_data / f"{e}.json").exists(), e
    assert (app_data / "regioes.json").exists()


@pytest.mark.parametrize("escopo", ESCOPOS)
def test_estrutura_e_sem_numero_cru(app_data: Path, escopo: str) -> None:
    doc = _ler(app_data, escopo)
    assert doc["meta"]["escopo"] == escopo and doc["meta"]["nome"]
    assert len(doc["kpis"]) == 4
    for k in doc["kpis"]:
        assert 0 <= k["pct_barra"] <= 150
        assert k["status"] in STATUS_VALIDOS
        assert {"rotulo", "valor", "comparacao", "fonte"} <= k.keys()
        assert all(l["status"] in STATUS_LINHA for l in k["detalhe"]["linhas"])
    assert doc["grafico"]["figura"] == f"figs/volume_resultado_{escopo}.png"
    assert (app_data.parent / "figs" / f"volume_resultado_{escopo}.png").exists()
    assert doc["grafico"]["manchete"]
    assert (doc["mapa"] is not None) == (escopo == "BR")
    assert _numeros_crus(doc) == []


def test_regioes_sem_numero_cru(app_data: Path) -> None:
    doc = _ler(app_data, "regioes")
    assert [r["regiao"] for r in doc["regioes"]] == [
        "Norte", "Nordeste", "Centro-Oeste", "Sudeste", "Sul"]
    assert sum(len(r["drs"]) for r in doc["regioes"]) == 27
    assert _numeros_crus(doc) == []


def test_kpis_br_batem_com_os_fatos(app_data: Path) -> None:
    kpis = _ler(app_data, "BR")["kpis"]
    assert [k["valor"] for k in kpis] == ["117,8%", "39,8%", "13 de 23", "78,8%"]
    assert [k["status"] for k in kpis] == ["atingido", "atencao", "critico", "critico"]
    fatos = json.loads((OUTPUTS_DIR / "fatos_BR.json").read_text(encoding="utf-8"))
    numeros = {n for f in fatos["fatos"] for n in f["numeros_permitidos"]}
    for v in ("39,8%", "13", "23", "78,8%"):
        assert v in numeros, v


def test_mapa_br_tem_27_ufs(app_data: Path) -> None:
    mapa = _ler(app_data, "BR")["mapa"]
    assert len(mapa) == 27
    rr = next(m for m in mapa if m["uf"] == "RR")
    assert rr["valor"] == "sem dado" and rr["status"] == "sem_dado"


def test_briefing_aprovado_br(app_data: Path) -> None:
    b = _ler(app_data, "BR")["briefing"]
    assert b is not None and b["mensagem_central"]
    assert all({"titulo", "texto", "quem", "quando"} <= r.keys() for r in b["recomendacoes"])


def test_dr_indicadores_do_maior_para_o_menor(app_data: Path) -> None:
    k = _ler(app_data, "AL")["kpis"][2]
    assert k["rotulo"] == "Indicadores atingidos" and k["valor"] == "5 de 12"
    valores = [float(l["valor"].rstrip("%").replace(".", "").replace(",", "."))
               for l in k["detalhe"]["linhas"]]
    assert valores == sorted(valores, reverse=True)


def test_auxiliares() -> None:
    assert pct_barra(200) == 150 and pct_barra(-5) == 0 and pct_barra(float("nan")) == 0
    assert pct_ou_sem_dado(None) == "sem dado" and pct_ou_sem_dado(39.84) == "39,8%"
    assert status(pd.NA) == "sem_dado" and status(100) == "atingido"


# ---------------------------------------------------------------- nome curto
# Código de indicador: "2.1", "13.1", "Indicador 6", "AT-matriculas".
# Milhar pt-BR ("4.722") não casa: o lookahead exige que o código termine ali.
RE_CODIGO = re.compile(r"(?<![\d.,])\d{1,2}\.\d(?![\d,])|[Ii]ndicador \d|AT-")
CAMPOS_NAO_EXIBIDOS = {"nome_oficial", "figura", "uf", "escopo", "status", "regiao"}


def _textos_exibidos(obj, caminho=""):
    """(caminho, texto) de todo campo string exibido na página."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k not in CAMPOS_NAO_EXIBIDOS:
                yield from _textos_exibidos(v, f"{caminho}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _textos_exibidos(v, f"{caminho}[{i}]")
    elif isinstance(obj, str):
        yield caminho, obj


def test_nome_curto_cobre_os_22_indicadores() -> None:
    pes = pd.read_csv(PROCESSED_DIR / "pes.csv", usecols=["cod_indicador"], dtype=str,
                      encoding="utf-8-sig")
    assert set(pes["cod_indicador"].dropna()) == set(NOME_CURTO)
    assert len(NOME_CURTO) == 22
    assert nome_curto("2.1", "x") == "Consultorias de produtividade"
    assert nome_curto("99", "Nome oficial") == "Nome oficial"


@pytest.mark.parametrize("nome", list(ESCOPOS) + ["regioes"])
def test_nenhum_codigo_de_indicador_em_campo_exibido(app_data: Path, nome: str) -> None:
    doc = _ler(app_data, nome)
    achados = [(c, t) for c, t in _textos_exibidos(doc) if RE_CODIGO.search(t)]
    assert achados == []


def test_regex_de_codigo() -> None:
    assert RE_CODIGO.search("Indicador 14.2 (Receitas)")
    assert RE_CODIGO.search("risco em 2.1")
    assert not RE_CODIGO.search("4.722 de 7.990 · 1.166,7% · 3,9 vezes")


def test_detalhe_usa_nome_curto_e_oficial_no_tooltip(app_data: Path) -> None:
    linhas = _ler(app_data, "AL")["kpis"][2]["detalhe"]["linhas"]
    curtos = set(NOME_CURTO.values())
    assert all(l["nome"] in curtos and l["nome_oficial"] for l in linhas)
    risco = _ler(app_data, "AL")["kpis"][3]
    if risco["status"] != "atingido":
        assert risco["nome_oficial"] and risco["detalhe"]["titulo"] in curtos


def test_regioes_textos_prontos(app_data: Path) -> None:
    doc = _ler(app_data, "regioes")
    br = doc["brasil"]
    assert br["titulo"] and len(br["numeros"]) == 3
    assert all(d["status"] == "critico" for d in br["drs"])
    assert [d["uf"] for d in br["drs"]] == sorted(d["uf"] for d in br["drs"])
    assert br["lista_titulo"].startswith(f"Os {len(br['drs'])} DRs")
    for r in doc["regioes"]:
        assert r["titulo"].startswith(r["regiao"]) and len(r["numeros"]) == 3


@pytest.mark.parametrize("escopo", ESCOPOS)
def test_meta_informa_pdf(app_data: Path, escopo: str) -> None:
    meta = _ler(app_data, escopo)["meta"]
    assert meta["tem_pdf"] == (OUTPUTS_DIR / f"Executive_Briefing_SENAI_{escopo}.pdf").exists()


@pytest.mark.parametrize("uf,n_criticos,st,rotulo", [
    ("PE", "2", "atencao", "Atenção"),
    ("CE", "0", "atingido", "Sem críticos"),
    ("AL", "6", "critico", "Trilha de apoio"),
])
def test_indicadores_dr_status_por_criticos(app_data: Path, uf: str, n_criticos: str,
                                            st: str, rotulo: str) -> None:
    k = _ler(app_data, uf)["kpis"][2]
    assert k["comparacao"].startswith(f"{n_criticos} indicadores")
    assert k["status"] == st and k["rotulo_status"] == rotulo
    assert "número de indicadores críticos" in k["detalhe"]["nota"]
    atingidos, total = (int(x) for x in k["valor"].split(" de "))
    assert k["pct_barra"] == pytest.approx(min(atingidos / total * 100, 150), abs=0.1)


def test_status_por_criticos_limites() -> None:
    assert [status_por_criticos(n) for n in (0, 1, 3, 4, 9)] == [
        "atingido", "atencao", "atencao", "critico", "critico"]


# --------------------------------------------------------------------------
# meta.verificacao (logs da edição + DECISOES.md)
# --------------------------------------------------------------------------

def _log(pasta: Path, nome: str, doc: dict) -> None:
    (pasta / nome).write_text(json.dumps(doc), encoding="utf-8")


def test_verificacao_da_edicao_usa_log_mais_recente_e_marcas(tmp_path: Path) -> None:
    from src.produto import verificacao_da_edicao
    logs = tmp_path / "logs"
    logs.mkdir()
    # perguntas BR: log antigo (ignorado) e recente com P6 reprovada e refeita, P2 descartada
    _log(logs, "perguntas_BR_20261125T100000Z.json",
         {"aprovadas": ["P1"], "removidas": [], "tentativas": [{"erros": {"P1": ["x"]}}, {"erros": {}}]})
    _log(logs, "perguntas_BR_20261125T200000Z.json",
         {"aprovadas": ["P1", "P6"], "removidas": ["P2"],
          "tentativas": [{"erros": {"P6": ["x"], "P2": ["y"]}}, {"erros": {"P2": ["y"]}}, {"erros": {"P2": ["y"]}}]})
    _log(logs, "perguntas_SP_20261125T200000Z.json",
         {"aprovadas": ["P1"], "removidas": [], "tentativas": [{"erros": {}}]})
    # briefing: AL refeito (2 tentativas), SP aprovado de primeira, BR em contingência
    _log(logs, "geracao_AL_20261125T200000Z.json", {"status": "aprovado", "tentativas": [{}, {}]})
    _log(logs, "geracao_SP_20261125T200000Z.json", {"status": "aprovado", "tentativas": [{}]})
    _log(logs, "geracao_BR_20261125T200000Z.json", {"status": "contingencia", "tentativas": [{}, {}, {}]})
    decisoes = tmp_path / "DECISOES.md"
    decisoes.write_text("| a | [revisao-humana] x | y | z |\n| b | sem marca | y | z |\n", encoding="utf-8")

    v = verificacao_da_edicao(logs, decisoes)
    assert (v["respostas_refeitas"], v["briefings_refeitos"], v["respostas_descartadas"],
            v["divergencias_revisao_humana"]) == ("1", "1", "1", "1")
    assert v["texto"] == ("Nesta edição, o verificador reprovou e a IA refez 1 resposta e 1 briefing; "
                          "1 resposta foi descartada. A revisão humana registrou 1 divergência entre fatos, "
                          "corrigida na origem.")


def test_verificacao_sem_logs_nem_decisoes(tmp_path: Path) -> None:
    from src.produto import verificacao_da_edicao
    v = verificacao_da_edicao(tmp_path / "nao_existe", tmp_path / "nao_existe.md")
    assert v["respostas_refeitas"] == v["divergencias_revisao_humana"] == "0"
    assert "0 respostas e 0 briefings" in v["texto"]


@pytest.mark.parametrize("escopo", ["BR", "SP"])
def test_meta_traz_verificacao(app_data: Path, escopo: str) -> None:
    verif = _ler(app_data, escopo)["meta"]["verificacao"]
    assert isinstance(verif["texto"], str) and verif["texto"].startswith("Nesta edição")
