"""Testes de src.render_pdf."""

import copy
import json
import shutil
from datetime import date
from pathlib import Path

import pandas as pd
import pdfplumber
import pytest

import matplotlib.pyplot as plt

from src.render_pdf import (
    MAX_PAGINAS,
    OUTPUTS_DIR,
    PROCESSED_DIR,
    ErroRenderizacao,
    carregar_entradas,
    coleta_mais_recente,
    contar_nao_apurados,
    contar_numeros,
    farol,
    gerar_pdf,
    n_volume_ok_resultado_critico,
    posicionar_rotulos,
    separar_limitacoes,
    sha256_curto,
    tabela_apoio,
    tabela_painel,
    tabela_projecao_dr,
    tabela_volume,
    texto_seguro,
    valor_painel,
)

ESCOPO = "BR"
BRIEFING_REAL = OUTPUTS_DIR / f"briefing_{ESCOPO}.json"
FATOS_REAL = OUTPUTS_DIR / f"fatos_{ESCOPO}.json"
TEM_DADOS = BRIEFING_REAL.exists() and FATOS_REAL.exists() and (PROCESSED_DIR / "pes.csv").exists()
requer_dados = pytest.mark.skipif(not TEM_DADOS, reason="saídas do pipeline ausentes")


# --------------------------------------------------------------------------
# Funções puras
# --------------------------------------------------------------------------

@pytest.mark.parametrize("indice, esperado", [
    (100.0, "atingido"), (237.4, "atingido"), (99.9, "atenção"), (80.0, "atenção"),
    (79.99, "crítico"), (-25.2, "crítico"),
])
def test_farol(indice, esperado):
    assert farol(indice) == esperado


def test_texto_seguro_escapa_e_troca_simbolos():
    assert texto_seguro("a < b & c ≥ d → e") == "a &lt; b &amp; c &gt;= d -&gt; e"


def test_valor_painel_moeda_compacta():
    assert valor_painel("R$    1.129.294.509,62", 1129294509.62, "(R$) Monetário") == "R$ 1,13 bi"
    assert valor_painel("45,0%", 45.0, "Percentual (%)") == "45,0%"


def test_contar_numeros_usa_tokens_do_validador():
    briefing = {
        "mensagem_central": {"texto": "Meta 45,0% e realizado 39,8%.", "fatos": ["F001"]},
        "destaques": [{"titulo": "T", "texto": "7.476 contra 6.175.", "fatos": ["F002"]}],
        "recomendacoes": [{"titulo": "R", "acao": "Agir no 4º tri.", "fatos": ["F001"]}],
    }
    assert contar_numeros(briefing) == 4  # o ordinal "4º" não conta


def test_sha256_curto(tmp_path):
    p = tmp_path / "x.json"
    p.write_bytes(b"abc")
    assert sha256_curto(p) == "ba7816bf8f01"


def test_tabela_painel_ignora_sem_dado_e_junta_contagens():
    pes = pd.DataFrame({
        "ano": [2025, 2025, 2025, 2024],
        "tipo_unidade": ["DN", "DN", "DR", "DN"],
        "cod_indicador": ["1", "4.1", "1", "1"],
        "indice_analise": [88.4, None, 50.0, 90.0],
    })
    diag = pd.DataFrame({"cod_indicador": ["1"], "n_critico": [14], "n_atencao": [5], "n_atingido": [7]})
    t = tabela_painel(pes, diag)
    assert list(t["cod_indicador"]) == ["1"]
    assert t.loc[0, "farol"] == "atenção"
    assert t.loc[0, "n_critico"] == 14


def test_tabela_apoio_filtra_e_ordena():
    diag_dr = pd.DataFrame({
        "uf": ["RN", "AC", "SC"], "regiao": ["Nordeste", "Norte", "Sul"],
        "n_critico": [7, 4, 1], "resumo": ["4 de 12 atingidos", "6 de 11 atingidos", "x"],
    })
    t = tabela_apoio(diag_dr)
    assert list(t["uf"]) == ["AC", "RN"]


def test_tabela_volume_ordem_e_farol():
    linhas = [
        ("Tecnologia e Inovação", "QUANTIDADE DE EMPRESAS ATENDIDAS", "350", "679", 194.0),
        ("Programas (matrículas)", "TOTAL", "34.377", "47.452", 138.0),
        ("Programas (matrículas)", "FORMAÇÃO INICIAL E CONTINUADA", "30.755", "-", None),
    ]
    at = pd.DataFrame([{"ano": 2025, "uf": "AL", "mes_referencia": 12, "linha_negocio": n,
                        "acao": a, "previsto_txt": p, "realizado_txt": r, "indice_analise": i}
                       for n, a, p, r, i in linhas])
    t = tabela_volume(at, "AL")
    assert list(t["rotulo"]) == ["Matrículas — total", "Matrículas — FIC", "Empresas atendidas"]
    assert t.loc[1, "farol"] is None  # sem índice: sem farol, nunca zero
    assert t.loc[2, "farol"] == "atingido"


def test_contar_nao_apurados():
    achados = pd.DataFrame({
        "regra_id": ["Q01", "Q01", "Q01", "Q01", "Q02"],
        "fonte": ["pes", "pes", "pes", "atendimento", "pes"],
        "ano": ["2025", "2025", "2026", "2025", "2025"],
        "uf": ["TODOS", "TODOS", "TODOS", "TODOS", "GO"],
        "item": ["10", "8", "10", "x", "13.1"],
    })
    pes = pd.DataFrame({"ano": [2025] * 3, "cod_indicador": ["1", "8", "10"]})
    assert contar_nao_apurados(achados, pes) == (2, 3)


def test_separar_limitacoes_por_categoria_de_fato():
    fatos = [{"id": "F051", "categoria": "qualidade"}, {"id": "F039", "categoria": "projecao_2026"}]
    lim = [{"texto": "a", "fatos": ["F039"]}, {"texto": "b", "fatos": ["F051", "F039"]}]
    fonte, metodo = separar_limitacoes(lim, fatos)
    assert [l["texto"] for l in fonte] == ["b"]
    assert [l["texto"] for l in metodo] == ["a"]


def test_coleta_mais_recente(tmp_path):
    m = tmp_path / "manifest.json"
    m.write_text(json.dumps({
        "a": {"coletado_em_utc": "2026-11-25T13:11:20+00:00"},
        "b": {"coletado_em_utc": "2026-11-25T14:23:05+00:00"},
        "c": {"erro": "falhou"},
    }), encoding="utf-8")
    assert coleta_mais_recente(m) == "25/11/2026 14:23 (UTC)"
    assert coleta_mais_recente(tmp_path / "nao_existe.json") is None


def test_tabela_projecao_dr_aceleracao():
    proj = pd.DataFrame({
        "fonte": ["pes", "pes", "pes"], "uf": ["AL", "AL", "BA"], "mes_referencia": [9, 9, 9],
        "indicador": ["A", "B", "C"], "situacao": ["risco alto", "meta já atingida", "no ritmo"],
        "aceleracao_necessaria": [20.888, 0.0, 1.0],
    })
    t = tabela_projecao_dr(proj, "AL")
    assert list(t["aceleracao_txt"]) == ["20,9x", "—"]


def test_n_volume_ok_resultado_critico():
    c = pd.DataFrame({"indice_matriculas": [100.0, 150.0, 90.0, 120.0, None],
                      "indice_empregabilidade": [79.9, 80.0, 50.0, 40.0, 10.0]})
    assert n_volume_ok_resultado_critico(c) == 2


def test_posicionar_rotulos_evita_sobreposicao():
    fig, ax = plt.subplots(figsize=(3, 3), dpi=100)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    offsets = posicionar_rotulos(ax, [50, 50.5], [50, 50.5], ["AA", "BB"])
    plt.close(fig)
    assert offsets[0] != offsets[1]


# --------------------------------------------------------------------------
# Geração do PDF (com as saídas reais do pipeline, copiadas para tmp_path)
# --------------------------------------------------------------------------

def _preparar(tmp_path: Path, briefing: dict | None = None) -> Path:
    out = tmp_path / "outputs"
    out.mkdir()
    shutil.copy(FATOS_REAL, out / FATOS_REAL.name)
    doc = briefing if briefing is not None else json.loads(BRIEFING_REAL.read_text(encoding="utf-8"))
    (out / BRIEFING_REAL.name).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return out


def _briefing_real() -> dict:
    return json.loads(BRIEFING_REAL.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pdf_gerado(tmp_path_factory):
    if not TEM_DADOS:
        pytest.skip("saídas do pipeline ausentes")
    out = _preparar(tmp_path_factory.mktemp("pdf"))
    caminho = gerar_pdf(ESCOPO, "Revisora Teste", outputs_dir=out, data_ref=date(2026, 11, 25))
    return caminho


@requer_dados
def test_pdf_no_maximo_4_paginas(pdf_gerado):
    with pdfplumber.open(pdf_gerado) as pdf:
        assert 1 <= len(pdf.pages) <= MAX_PAGINAS


@requer_dados
def test_pdf_nome_e_figuras(pdf_gerado):
    assert pdf_gerado.name == f"Executive_Briefing_SENAI_{ESCOPO}.pdf"
    figs = list((pdf_gerado.parent / "figs").glob("*.png"))
    assert len(figs) == 2


@requer_dados
def test_pdf_rodape_e_conteudo(pdf_gerado):
    with pdfplumber.open(pdf_gerado) as pdf:
        textos = [p.extract_text() for p in pdf.pages]
    for i, t in enumerate(textos, start=1):
        assert "Revisado por: Revisora Teste" in t
        assert f"página {i}/4" in t
    assert "Executive Briefing | Sistema SENAI" in textos[0]
    assert "[F001, F006, F026]" in textos[0]
    assert "Painel sistêmico" in textos[1]
    assert "Volume entregue — Atendimento Social, 2025 (DN)" in textos[1]
    assert "dos 22 indicadores do Plano não tiveram apuração em 2025" in textos[1]
    assert "Trilha de apoio" in textos[2]
    assert "12 de 12 valores nacionais" in textos[3]
    assert "DN = 27 DRs + SENAI CETIQT" in textos[3]
    assert "Qualidade da fonte (achados reportados ao DN)" in textos[3]
    assert "Limites do método" in textos[3]
    assert "Termos" in textos[3] and "Glossário" not in textos[3]
    assert "Coleta mais recente:" in textos[3]
    assert "VERSÃO DE CONTINGÊNCIA" not in "".join(textos)


@requer_dados
def test_pdf_corpo_com_fonte_minima_9(pdf_gerado):
    msg = _briefing_real()["briefing"]["mensagem_central"]["texto"]
    primeira = msg.split()[0]
    with pdfplumber.open(pdf_gerado) as pdf:
        palavras = pdf.pages[0].extract_words(extra_attrs=["size"])
        tamanhos = [c["size"] for p in pdf.pages for c in p.chars]
    alvo = next(w for w in palavras if w["text"] == primeira)
    assert alvo["size"] >= 9
    # Só IDs dos fatos e rodapé ficam abaixo de 9 pt.
    assert sum(s >= 9 for s in tamanhos) / len(tamanhos) > 0.8


@requer_dados
def test_status_nao_aprovado_sem_flag_falha(tmp_path):
    doc = _briefing_real()
    doc["status"] = "contingencia"
    out = _preparar(tmp_path, doc)
    with pytest.raises(ErroRenderizacao, match="permitir-contingencia"):
        carregar_entradas(ESCOPO, outputs_dir=out)


@requer_dados
def test_contingencia_com_flag_gera_faixa(tmp_path):
    fatos = json.loads(FATOS_REAL.read_text(encoding="utf-8"))["fatos"]
    grupos: dict[str, list] = {}
    for f in fatos:
        grupos.setdefault(f["categoria"], []).append({"id": f["id"], "frase": f["frase"]})
    doc = {"escopo": ESCOPO, "status": "contingencia", "modelo": "m", "versao_prompt": "v2",
           "aviso": "Briefing gerado em contingência.", "briefing": None,
           "fatos_por_categoria": grupos}
    out = _preparar(tmp_path, doc)
    caminho = gerar_pdf(ESCOPO, "Fulano", permitir_contingencia=True, outputs_dir=out)
    with pdfplumber.open(caminho) as pdf:
        assert len(pdf.pages) <= MAX_PAGINAS
        assert "VERSÃO DE CONTINGÊNCIA" in pdf.pages[0].extract_text()


@requer_dados
def test_conteudo_excessivo_reprova_em_vez_de_passar_de_4_paginas(tmp_path):
    doc = _briefing_real()
    item = copy.deepcopy(doc["briefing"]["pontos_atencao"][0])
    doc["briefing"]["pontos_atencao"] = [item] * 30
    out = _preparar(tmp_path, doc)
    with pytest.raises(ErroRenderizacao):
        gerar_pdf(ESCOPO, "Fulano", outputs_dir=out)


@pytest.mark.skipif(not (OUTPUTS_DIR / "fatos_AL.json").exists(), reason="fatos_AL.json ausente")
@requer_dados
def test_escopo_dr_painel_e_projecao(tmp_path):
    out = tmp_path / "outputs"
    out.mkdir()
    shutil.copy(OUTPUTS_DIR / "fatos_AL.json", out / "fatos_AL.json")
    doc = _briefing_real()  # texto do BR, só para exercitar o layout do escopo DR
    doc["escopo"] = "AL"
    (out / "briefing_AL.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    caminho = gerar_pdf("AL", "Fulano", outputs_dir=out)
    with pdfplumber.open(caminho) as pdf:
        assert len(pdf.pages) <= MAX_PAGINAS
        textos = [p.extract_text() for p in pdf.pages]
    assert "Painel do DR AL" in textos[1]
    assert "Mediana" in textos[1]
    assert "(DR AL)" in textos[1]
    assert "Projeção 2026 do DR AL" in textos[2]
    assert "Trilha de apoio" not in textos[2]


@requer_dados
def test_escopo_desconhecido_falha(tmp_path):
    out = _preparar(tmp_path)
    shutil.copy(out / "briefing_BR.json", out / "briefing_XX.json")
    shutil.copy(out / "fatos_BR.json", out / "fatos_XX.json")
    with pytest.raises(ErroRenderizacao, match="Escopo desconhecido"):
        carregar_entradas("XX", outputs_dir=out)


def test_revisor_obrigatorio(tmp_path):
    with pytest.raises(ErroRenderizacao, match="revisor"):
        gerar_pdf(ESCOPO, "  ", outputs_dir=tmp_path)
