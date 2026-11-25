"""Testes de src.relatorio."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from src.relatorio import (
    baixar_relatorio,
    buscar,
    carregar_dn,
    extrair_paginas,
    inteiro_pequeno,
    normalizar_espacos,
    padrao_valor,
    palavras_chave,
    realizado_valido,
    triangular,
)

PDF_FALSO = b"%PDF-1.4 conteudo de teste"


def paginas(*textos: str) -> pd.DataFrame:
    """DataFrame no formato de relatorio_paginas.csv."""
    return pd.DataFrame({
        "pagina": range(1, len(textos) + 1),
        "texto": list(textos),
        "n_caracteres": [len(t) for t in textos],
    })


# ---------------------------------------------------------------- padrão

@pytest.mark.parametrize("valor, texto", [
    ("39,8%", "taxa de 39,8% em 2025"),
    ("39,8%", "taxa de 39,8 % em 2025"),
    ("39,8%", "taxa de 39,8\n% em 2025"),
    ("7.476", "foram 7.476 consultorias"),
    ("7.476", "foram 7476 consultorias"),
    ("7476", "foram 7.476 consultorias"),
    ("R$    1.057.901.518,57", "total de R$ 1.057.901.518,57 aplicados"),
    ("59", "59 projetos"),
])
def test_padrao_casa_variantes(valor: str, texto: str) -> None:
    padrao = padrao_valor(valor)
    assert padrao is not None
    assert padrao.search(normalizar_espacos(texto))


@pytest.mark.parametrize("valor, texto", [
    ("59", "1.059 alunos"),
    ("59", "2059"),
    ("59", "59,5 mil"),
    ("7.476", "17.476"),
    ("7.476", "7.476,3"),
    ("39,8%", "139,8%"),
    ("39,8%", "39,8 pontos"),
])
def test_padrao_nao_casa_dentro_de_outro_numero(valor: str, texto: str) -> None:
    padrao = padrao_valor(valor)
    assert padrao is not None
    assert not padrao.search(texto)


@pytest.mark.parametrize("valor", ["-", "", None, float("nan"), "A definir"])
def test_valores_invalidos(valor: Any) -> None:
    assert padrao_valor(valor) is None
    assert not realizado_valido(valor)


# ---------------------------------------------------------------- buscar

def test_buscar_trecho_e_limite() -> None:
    texto = "x" * 500 + " Aprendizagem Industrial " + "y" * 500
    df = paginas("nada aqui", texto, "aprendizagem industrial de novo")
    res = buscar(["APRENDIZAGEM  industrial"], paginas=df)
    assert [r["pagina"] for r in res] == [2, 3]
    trecho = res[0]["trecho"]
    assert "Aprendizagem Industrial" in trecho
    assert len(trecho) == 300 + len("Aprendizagem Industrial") + 300

    assert len(buscar(["aprendizagem"], max_resultados=1, paginas=df)) == 1


def test_buscar_termo_nao_e_regex() -> None:
    df = paginas("valor (a+b) aqui", "aab")
    assert [r["pagina"] for r in buscar(["(a+b)"], paginas=df)] == [1]
    assert buscar([], paginas=df) == []
    assert buscar(["  "], paginas=df) == []


# ---------------------------------------------------------------- triangulação

def test_palavras_chave() -> None:
    chaves = palavras_chave("Expansão de consultorias com impacto em descarbonização na indústria")
    assert chaves == ["expansao", "consultorias", "impacto", "descarbonizacao"]
    assert palavras_chave(None) == []


@pytest.mark.parametrize("valor, esperado", [
    ("59", True), ("99", True), ("100", False), ("7.476", False),
    ("26,3%", False), ("5%", False), ("1,5", False),
])
def test_inteiro_pequeno(valor: str, esperado: bool) -> None:
    assert inteiro_pequeno(valor) is esperado


def test_triangular() -> None:
    dn = pd.DataFrame({
        "cod_indicador": ["1", "2.1", "12"],
        "indicador": ["Taxa de ocupação de egressos", "Expansão de consultorias", "Outro"],
        "realizado_txt": ["39,8%", "7.476", "59"],
    })
    df = paginas("Ocupação de 39,8 % dos EGRESSOS", "Foram 7476 e 7.476", "total 1.059")
    tri = triangular(dn, df)
    assert tri["encontrado"].tolist() == [True, True, False]
    assert tri["confianca"].tolist() == ["forte", "fraca", ""]
    assert tri.loc[1, "paginas"] == "2"
    assert tri.loc[0, "trecho"].startswith("Ocupação")
    assert tri.loc[2, "paginas"] == "" and tri.loc[2, "trecho"] == ""


def test_triangular_prefere_ocorrencia_forte() -> None:
    dn = pd.DataFrame({
        "cod_indicador": ["2.2"], "indicador": ["Consultorias em descarbonização"],
        "realizado_txt": ["1.218"],
    })
    df = paginas("tabela 1.218 solta", "x", "1218 ações de descarbonizacao")
    tri = triangular(dn, df)
    assert tri.loc[0, "confianca"] == "forte"
    assert tri.loc[0, "paginas"] == "3"
    assert "descarbonizacao" in tri.loc[0, "trecho"]


def test_triangular_janela_de_300() -> None:
    dn = pd.DataFrame({
        "cod_indicador": ["2.1"], "indicador": ["Consultorias"], "realizado_txt": ["7.476"],
    })
    longe = paginas("consultorias " + "x" * 400 + " 7.476")
    assert triangular(dn, longe).loc[0, "confianca"] == "fraca"


def test_triangular_inteiro_pequeno_so_forte() -> None:
    dn = pd.DataFrame({
        "cod_indicador": ["12", "12"],
        "indicador": ["Projetos de inovação", "Projetos de inovação"],
        "realizado_txt": ["59", "59"],
    })
    fraco = triangular(dn.iloc[[0]], paginas("página 59 do relatório"))
    assert not fraco.loc[0, "encontrado"] and fraco.loc[0, "confianca"] == ""
    forte = triangular(dn.iloc[[0]], paginas("59 projetos de inovacao"))
    assert forte.loc[0, "encontrado"] and forte.loc[0, "confianca"] == "forte"


def test_carregar_dn_filtra(tmp_path: Path) -> None:
    pes = pd.DataFrame({
        "ano": [2025, 2025, 2025, 2026],
        "uf": ["DN", "DN", "SC", "DN"],
        "cod_indicador": ["1", "4.1", "1", "1"],
        "indicador": ["a", "b", "c", "d"],
        "realizado_txt": ["39,8%", "-", "40,0%", "41,0%"],
    })
    caminho = tmp_path / "pes.csv"
    pes.to_csv(caminho, index=False, encoding="utf-8-sig")
    dn = carregar_dn(caminho)
    assert dn["cod_indicador"].tolist() == ["1"]


# ---------------------------------------------------------------- download

class RespostaFalsa:
    """Resposta HTTP mínima."""

    def __init__(self, conteudo: bytes, status: int = 200) -> None:
        self.content = conteudo
        self.status_code = status

    def raise_for_status(self) -> None:
        """Sem erro."""


class SessaoFalsa:
    """Sessão que conta chamadas e devolve um conteúdo fixo."""

    def __init__(self, conteudo: bytes = PDF_FALSO) -> None:
        self.conteudo = conteudo
        self.chamadas = 0

    def get(self, url: str, **_: Any) -> RespostaFalsa:
        self.chamadas += 1
        return RespostaFalsa(self.conteudo)


def test_baixar_registra_manifest_e_nao_rebaixa(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    destino = tmp_path / "relatorio" / "relatorio_gestao_2025.pdf"
    sessao = SessaoFalsa()

    assert baixar_relatorio("http://x/r.pdf", destino, manifest_path, sessao) is True
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    reg = manifest["relatorio/relatorio_gestao_2025.pdf"]
    assert reg["url"] == "http://x/r.pdf"
    assert reg["sha256"] == hashlib.sha256(PDF_FALSO).hexdigest()
    assert reg["coletado_em_utc"].endswith("+00:00")

    assert baixar_relatorio("http://x/r.pdf", destino, manifest_path, sessao) is False
    assert sessao.chamadas == 1

    destino.write_bytes(b"%PDF alterado")  # sha diverge -> baixa de novo
    assert baixar_relatorio("http://x/r.pdf", destino, manifest_path, sessao) is True
    assert sessao.chamadas == 2
    assert destino.read_bytes() == PDF_FALSO


def test_baixar_rejeita_nao_pdf(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        baixar_relatorio(
            "http://x", tmp_path / "r.pdf", tmp_path / "m.json", SessaoFalsa(b"<html>")
        )
    assert not (tmp_path / "r.pdf").exists()


# ---------------------------------------------------------------- extração

def test_extrair_paginas(tmp_path: Path) -> None:
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    caminho = tmp_path / "t.pdf"
    c = canvas.Canvas(str(caminho))
    c.drawString(72, 720, "Pagina um 39,8%")
    c.showPage()
    c.showPage()  # página em branco
    c.save()

    df = extrair_paginas(caminho)
    assert df["pagina"].tolist() == [1, 2]
    assert "39,8%" in df.loc[0, "texto"]
    assert df.loc[1, "texto"] == ""
    assert df.loc[0, "n_caracteres"] == len(df.loc[0, "texto"])
