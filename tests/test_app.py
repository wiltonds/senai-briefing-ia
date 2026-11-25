"""Testes do app Gradio (funções puras; sem API e sem subir servidor)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import app

FATOS = [
    {"id": "F001", "frase": "Empregabilidade 2025: meta 45,0%, realizado 39,8%."},
    {"id": "F002", "frase": "Consultorias: 7.476 para meta de 6.175."},
]
BRIEFING = {
    "mensagem_central": {"texto": "Realizado de 39,8% para meta de 45,0%.", "fatos": ["F001"]},
    "destaques": [
        {"titulo": "Consultorias", "texto": "Chegaram a 7.476, meta 6.175.", "fatos": ["F002"]},
        {"titulo": "Emprego", "texto": "Realizado 39,8%.", "fatos": ["F001"]},
        {"titulo": "Meta", "texto": "Meta 45,0%.", "fatos": ["F001"]},
    ],
    "pontos_atencao": [
        {"titulo": "A", "texto": "Realizado 39,8%.", "fatos": ["F001"]},
        {"titulo": "B", "texto": "Meta 45,0%.", "fatos": ["F001"]},
        {"titulo": "C", "texto": "Consultorias 7.476.", "fatos": ["F002"]},
    ],
    "recomendacoes": [
        {"titulo": f"R{i}", "acao": "Rever a meta de 45,0%.", "responsavel": "DN",
         "horizonte": "próximo ciclo de planejamento", "fatos": ["F001"]}
        for i in range(3)
    ],
    "limitacoes": [],
}


@pytest.fixture
def outputs(tmp_path: Path) -> Path:
    doc = {"escopo": "BR", "status": "aprovado", "tentativas": 2, "modelo": "m", "versao_prompt": "v2",
           "briefing": BRIEFING}
    (tmp_path / "briefing_BR.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "fatos_BR.json").write_text(json.dumps({"fatos": FATOS}, ensure_ascii=False), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------- configuração
def test_config_padrao_sem_llm_nem_auth():
    cfg = app.configurar({})
    assert cfg == app.Config(allow_llm=False, share=False, hf=False, auth=None)


def test_allow_llm_so_com_1():
    assert app.configurar({"ALLOW_LLM": "1"}).allow_llm
    assert not app.configurar({"ALLOW_LLM": "true"}).allow_llm


def test_share_sem_credenciais_recusa():
    with pytest.raises(app.ErroConfiguracao):
        app.configurar({"SHARE": "1"})
    with pytest.raises(app.ErroConfiguracao):
        app.configurar({"SHARE": "1", "APP_USER": "u"})


def test_share_com_credenciais():
    cfg = app.configurar({"SHARE": "1", "APP_USER": "u", "APP_PASS": "p"})
    assert cfg.share and cfg.auth == ("u", "p")


def test_hf_llm_desligado_por_padrao_e_exige_auth_se_ligado():
    assert not app.configurar({"SPACE_ID": "x/y"}).allow_llm
    with pytest.raises(app.ErroConfiguracao):
        app.configurar({"SPACE_ID": "x/y", "ALLOW_LLM": "1"})
    cfg = app.configurar({"SPACE_ID": "x/y", "ALLOW_LLM": "1", "APP_USER": "u", "APP_PASS": "p"})
    assert cfg.hf and cfg.allow_llm


def test_escopo_invalido_barrado():
    assert app.checar_escopo(" al ") == "AL"
    assert len(app.ESCOPOS) == 28
    with pytest.raises(ValueError):
        app.checar_escopo("../.env")


def test_gerar_com_ia_bloqueado_sem_allow_llm():
    with pytest.raises(app.gr.Error):
        app.gerar_com_ia("BR", "Fulano", app.configurar({}))


def test_pdf_exige_revisor():
    with pytest.raises(app.gr.Error):
        app.gerar_pdf_salvo("BR", "   ")


# ---------------------------------------------------------------- briefing e auditoria
def test_markdown_briefing_aprovado():
    md = app.markdown_briefing({"escopo": "BR", "status": "aprovado", "tentativas": 2, "briefing": BRIEFING})
    assert "aprovado" in md and "Tentativas:** 2" in md
    assert "Mensagem central" in md and "Recomendações" in md and "[F002]" in md


def test_markdown_briefing_contingencia_e_vazio():
    doc = {"status": "contingencia", "aviso": "Sem IA.", "briefing": None,
           "fatos_por_categoria": {"resultado_nacional": [FATOS[0]]}}
    md = app.markdown_briefing(doc)
    assert "contingência" in md and "Sem IA." in md and "F001" in md
    assert "Nenhum briefing" in app.markdown_briefing(None)


def test_fatos_citados_ordem_e_inexistente():
    b = {**BRIEFING, "limitacoes": [{"texto": "x", "fatos": ["F999"]}]}
    df = app.fatos_citados(b, FATOS)
    assert df["ID"].tolist() == ["F001", "F002", "F999"]
    assert df.loc[2, "frase"] == "(fato inexistente na ficha)"


def test_ultimo_log(tmp_path: Path):
    assert app.ultimo_log("BR", tmp_path) is None
    for carimbo in ("20261125T151916Z", "20261125T155133Z"):
        (tmp_path / f"geracao_BR_{carimbo}.json").write_text(json.dumps({"c": carimbo}), encoding="utf-8")
    caminho, log = app.ultimo_log("BR", tmp_path)
    assert log == {"c": "20261125T155133Z"}


def test_markdown_tentativas():
    log = {"status": "aprovado", "tentativas": [
        {"tentativa": 1, "erros": ["destaques[1]: o número '12' não aparece"], "uso": {}},
        {"tentativa": 2, "erros": [], "uso": {"input_tokens": 10, "output_tokens": 5}},
    ]}
    md = app.markdown_tentativas(log, "geracao_BR.json")
    assert "1 erro(s)" in md and "'12'" in md and "Tentativa 2 — ✅ aprovada" in md


# ---------------------------------------------------------------- teste do validador
def test_validar_item_aprovado_e_numero_alterado(outputs: Path):
    ok = app.validar_item("BR", "Chegaram a 7.476, meta 6.175.", "F002", outputs)
    assert "Aprovado" in ok
    barrado = app.validar_item("BR", "Chegaram a 7.900, meta 6.175.", "F002", outputs)
    assert "Barrado" in barrado and "7.900" in barrado


def test_validar_item_sem_briefing(tmp_path: Path):
    assert "Não há briefing aprovado" in app.validar_item("BR", "x", "F001", tmp_path)


# ---------------------------------------------------------------- qualidade
def test_filtrar_achados():
    df = pd.DataFrame({"regra_id": ["Q01", "Q02", "Q01"], "gravidade": ["alta", "media", "alta"],
                       "uf": ["AC", "AL", "BA"]})
    assert len(app.filtrar_achados(df, app.TODAS_REGRAS)) == 3
    assert app.filtrar_achados(df, "Q01")["uf"].tolist() == ["AC", "BA"]


# ---------------------------------------------------------------- gráficos do painel (cache)
def test_precisa_regerar(tmp_path: Path):
    import os

    png, csv = tmp_path / "g.png", tmp_path / "pes.csv"
    csv.write_text("x", encoding="utf-8")
    assert app.precisa_regerar(png, [csv])  # não existe
    png.write_bytes(b"png")
    os.utime(csv, (1_000, 1_000))
    os.utime(png, (2_000, 2_000))
    assert not app.precisa_regerar(png, [csv])  # cache válido
    os.utime(csv, (3_000, 3_000))
    assert app.precisa_regerar(png, [csv])  # dados mais novos


def test_figuras_gera_uma_vez_e_usa_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import os

    proc, figs = tmp_path / "proc", tmp_path / "figs"
    proc.mkdir()
    for nome in ("pes", "cruzamento_volume_resultado"):
        (proc / f"{nome}.csv").write_text("uf\nAL\n", encoding="utf-8-sig")
        os.utime(proc / f"{nome}.csv", (1_000, 1_000))
    chamadas: list[tuple[str, str | None]] = []

    def falso(df, saida, destaque=None):
        chamadas.append((saida.name, destaque))
        saida.write_bytes(b"png")
        return saida

    monkeypatch.setattr(app.render_pdf, "grafico_empregabilidade", falso)
    monkeypatch.setattr(app.render_pdf, "grafico_dispersao", falso)

    res = app.figuras("al", figs, proc)
    assert chamadas == [("empregabilidade_dr_AL.png", "AL"), ("volume_resultado_AL.png", "AL")]
    assert [Path(p).name for p, _ in res] == ["empregabilidade_dr_AL.png", "volume_resultado_AL.png"]
    app.figuras("AL", figs, proc)
    assert len(chamadas) == 2  # cache
    app.figuras("BR", figs, proc)
    assert chamadas[-1] == ("volume_resultado_BR.png", None)  # BR sem destaque
