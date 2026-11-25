"""Renderiza o Executive Briefing em PDF (A4, no máximo 4 páginas).

Entradas:
- ``outputs/briefing_{escopo}.json`` (status "aprovado"; "contingencia" só com
  ``--permitir-contingencia``, e o PDF ganha uma faixa de aviso);
- ``outputs/fatos_{escopo}.json``;
- ``data/processed`` (pes, atendimento, diag_indicador, diag_dr,
  cruzamento_volume_resultado, projecao_2026, qualidade_resumo, qualidade_achados);
- ``data/raw/manifest.json`` (data/hora da coleta).

Páginas:
1) Briefing;
2) Painel: escopo BR = indicadores do DN + volume do Atendimento Social;
   escopo DR = indicadores do DR com medianas da região e nacional dos DRs + volume do DR;
3) Assimetrias: gráficos por DR (no escopo DR, o DR em destaque) + trilha de apoio (BR)
   ou projeção 2026 do DR;
4) Método e segurança.
Gráficos em ``outputs/figs/`` (PNG, 200 dpi).

Uso:
    python -m src.render_pdf --escopo BR --revisado-por "Nome"
    python -m src.render_pdf --escopo AL --revisado-por "Nome"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from reportlab.graphics.shapes import Circle, Drawing  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_CENTER  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    BaseDocTemplate,
    Flowable,
    Frame,
    Image,
    KeepInFrame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from src.coleta import ENDPOINTS  # noqa: E402
from src.formato import eh_monetario, fmt_moeda, fmt_pct, fmt_vezes  # noqa: E402
from src.relatorio import URL_RELATORIO  # noqa: E402
from src.validador import tokens_numericos, validar  # noqa: E402

logger = logging.getLogger("render_pdf")

RAIZ = Path(__file__).resolve().parents[1]
PROCESSED_DIR = RAIZ / "data" / "processed"
RAW_DIR = RAIZ / "data" / "raw"
OUTPUTS_DIR = RAIZ / "outputs"
ENCODING_CSV = "utf-8-sig"

ESCOPO_BR = "BR"
MAX_PAGINAS = 4
ANO = 2025
COD_EMPREGABILIDADE = "1"
MIN_CRITICOS_APOIO = 4
LIMITE_ATINGIDO = 100.0
LIMITE_ATENCAO = 80.0
DPI = 200
REGRA_NAO_APURADO = "Q01"
SIT_META_ATINGIDA = "meta já atingida"

# Cores
AZUL = "#1F3A5F"
VERDE = "#2E7D32"
AMBAR = "#F9A825"
VERMELHO = "#C62828"
CINZA = "#6B6B6B"
CINZA_GRAFICO = "#BFC5CC"
CINZA_CLARO = "#EEF1F5"
COR_REGIAO: dict[str, str] = {  # evita as cores do farol
    "Norte": "#1F3A5F",
    "Nordeste": "#5B8DBE",
    "Centro-Oeste": "#8C6D9E",
    "Sudeste": "#3E8E8E",
    "Sul": "#B07A4F",
}
COR_SITUACAO = {
    "meta já atingida": VERDE, "no ritmo": VERDE, "exige aceleração": AMBAR,
    "risco alto": VERMELHO, "consolidado incompleto": CINZA,
}

FONTE = "Helvetica"
FONTE_NEGRITO = "Helvetica-Bold"
CORPO_PT = 9.5

TITULO_DOC = "Executive Briefing | Sistema SENAI | Resultados 2025 e ritmo 2026"
ETAPAS_FLUXO = (
    "Coleta oficial", "Qualidade ({n_regras} regras)", "Análise", "Ficha de fatos",
    "IA redige", "Validador", "Revisão humana",
)
CATEGORIAS_CONTINGENCIA = ("resultado_nacional", "cruzamento")
CATEGORIA_QUALIDADE = "qualidade"

# (linha_negocio, acao, rótulo) das linhas de volume do Atendimento Social.
VOLUME_ATENDIMENTO: tuple[tuple[str, str, str], ...] = (
    ("Programas (matrículas)", "TOTAL", "Matrículas — total"),
    ("Programas (matrículas)", "EDUCAÇÃO PROFISSIONAL TÉCNICA DE NÍVEL MÉDIO",
     "Matrículas — técnico de nível médio"),
    ("Programas (matrículas)", "FORMAÇÃO INICIAL E CONTINUADA", "Matrículas — FIC"),
    ("Tecnologia e Inovação", "QUANTIDADE DE EMPRESAS ATENDIDAS", "Empresas atendidas"),
)

TERMOS: tuple[tuple[str, str], ...] = (
    ("Índice de realização", "realizado ÷ meta × 100. Quando o índice publicado diverge do "
     "calculado em mais de 1 p.p., usa-se o calculado."),
    ("Farol", "atingido (índice de 100% ou mais), atenção (80% a 99,9%) e crítico (abaixo de 80%)."),
    ("DN, DR e SENAI CETIQT", "Departamento Nacional (consolidado do sistema), Departamentos "
     "Regionais (um por UF) e o centro nacional de tecnologia da indústria química e têxtil."),
    ("Plano Estratégico Sistêmico", "conjunto de indicadores e metas anuais comuns a todo o "
     "sistema SENAI (Mapa Estratégico 2025–2027)."),
    ("Atendimento Social", "previsto e realizado de matrículas e serviços de tecnologia, por unidade."),
    ("Ficha de fatos", "arquivo gerado por código com cada número citável, seu ID (F001…) e a "
     "fonte de origem; é a única entrada da IA."),
    ("Validador", "confere se todo número e toda UF do texto da IA estão nos fatos citados; "
     "se não estiverem, o texto é reprovado."),
)

# Caracteres fora do WinAnsi (Helvetica padrão) -> equivalentes seguros.
_SUBSTITUICOES = {"≥": ">=", "≤": "<=", "→": "->", "−": "-", "≈": "~", "×": "x", "÷": "/"}


class ErroRenderizacao(Exception):
    """Falha ao montar o PDF (entrada inválida ou excesso de páginas)."""


# ---------------------------------------------------------------- utilidades
def texto_seguro(texto: Any) -> str:
    """Escapa XML e troca caracteres que a Helvetica padrão não desenha."""
    s = str(texto)
    for de, para in _SUBSTITUICOES.items():
        s = s.replace(de, para)
    return escape(s)


def fmt_pct_ou_traco(valor: Any) -> str:
    """Percentual pt-BR, ou "—" quando não há valor (nunca zero)."""
    return fmt_pct(float(valor)) if pd.notna(valor) else "—"


def compactar(texto: Any) -> str:
    """Remove espaços repetidos (ex.: "R$    1.129" -> "R$ 1.129")."""
    return " ".join(str(texto).split())


def valor_painel(texto: Any, valor: Any, unidade: Any) -> str:
    """Valor para o painel: moeda compacta ("R$ 1,13 bi"); demais, o texto publicado."""
    if eh_monetario(unidade) and pd.notna(valor):
        return fmt_moeda(float(valor))
    return compactar(texto)


def farol(indice: float) -> str:
    """Classe do farol: atingido (>= 100), atenção (80 a 99,9) ou crítico (< 80)."""
    if indice >= LIMITE_ATINGIDO:
        return "atingido"
    if indice >= LIMITE_ATENCAO:
        return "atenção"
    return "crítico"


COR_FAROL = {"atingido": VERDE, "atenção": AMBAR, "crítico": VERMELHO}


def sha256_curto(caminho: Path, n: int = 12) -> str:
    """Primeiros ``n`` caracteres do sha256 do arquivo."""
    return hashlib.sha256(caminho.read_bytes()).hexdigest()[:n]


def textos_briefing(briefing: dict) -> list[str]:
    """Textos verificáveis do briefing (mesmos campos que o validador lê)."""
    textos: list[str] = []
    mc = briefing.get("mensagem_central") or {}
    if mc.get("texto"):
        textos.append(str(mc["texto"]))
    for secao, campo in (
        ("destaques", "texto"), ("pontos_atencao", "texto"),
        ("recomendacoes", "acao"), ("limitacoes", "texto"),
    ):
        for item in briefing.get(secao) or []:
            partes = [str(item.get("titulo", "")), str(item.get(campo, ""))]
            textos.append(" ".join(p for p in partes if p))
    return textos


def contar_numeros(briefing: dict) -> int:
    """Quantidade de números no briefing (tokens únicos por item, via validador)."""
    return sum(len(tokens_numericos(t)) for t in textos_briefing(briefing))


def coleta_mais_recente(manifest_path: Path) -> str | None:
    """Data/hora da coleta mais recente do manifest, "dd/mm/aaaa hh:mm (UTC)"."""
    if not manifest_path.exists():
        logger.warning("Manifest ausente: %s", manifest_path)
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    datas = []
    for reg in manifest.values():
        valor = reg.get("coletado_em_utc") if isinstance(reg, dict) else None
        if valor:
            datas.append(datetime.fromisoformat(valor).astimezone(timezone.utc))
    if not datas:
        return None
    return max(datas).strftime("%d/%m/%Y %H:%M") + " (UTC)"


def separar_limitacoes(limitacoes: list[dict], fatos: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separa as limitações em (qualidade da fonte, limites do método).

    Vai para "qualidade da fonte" o item que cita ao menos um fato da categoria qualidade.
    """
    ids_qualidade = {f["id"] for f in fatos if f.get("categoria") == CATEGORIA_QUALIDADE}
    fonte = [l for l in limitacoes if ids_qualidade & set(l.get("fatos", []))]
    metodo = [l for l in limitacoes if not ids_qualidade & set(l.get("fatos", []))]
    return fonte, metodo


# ---------------------------------------------------------------- entradas
@dataclass
class Entradas:
    """Tudo o que o PDF precisa, já carregado."""

    escopo: str
    doc_briefing: dict
    doc_fatos: dict
    fatos_path: Path
    pes: pd.DataFrame
    atendimento: pd.DataFrame
    diag_indicador: pd.DataFrame
    diag_regiao: pd.DataFrame
    diag_dr: pd.DataFrame
    cruzamento: pd.DataFrame
    projecao: pd.DataFrame
    achados: pd.DataFrame
    n_regras: int
    coleta_utc: str | None

    @property
    def contingencia(self) -> bool:
        return self.doc_briefing.get("status") != "aprovado"

    @property
    def eh_dr(self) -> bool:
        return self.escopo != ESCOPO_BR

    @property
    def fatos(self) -> list[dict]:
        return self.doc_fatos.get("fatos", [])


def _ler_csv(processed_dir: Path, nome: str) -> pd.DataFrame:
    caminho = processed_dir / f"{nome}.csv"
    if not caminho.exists():
        raise ErroRenderizacao(f"Arquivo ausente: {caminho}")
    return pd.read_csv(caminho, encoding=ENCODING_CSV,
                       dtype={"cod_indicador": str, "item": str, "uf": str})


def carregar_entradas(
    escopo: str,
    permitir_contingencia: bool = False,
    outputs_dir: Path = OUTPUTS_DIR,
    processed_dir: Path = PROCESSED_DIR,
    raw_dir: Path = RAW_DIR,
) -> Entradas:
    """Carrega e confere as entradas.

    Raises:
        ErroRenderizacao: arquivo ausente, DR desconhecido, ou briefing não
            aprovado sem ``permitir_contingencia``.
    """
    briefing_path = outputs_dir / f"briefing_{escopo}.json"
    fatos_path = outputs_dir / f"fatos_{escopo}.json"
    for p in (briefing_path, fatos_path):
        if not p.exists():
            raise ErroRenderizacao(f"Arquivo ausente: {p}")
    doc_briefing = json.loads(briefing_path.read_text(encoding="utf-8"))
    status = doc_briefing.get("status")
    if status != "aprovado":
        if not permitir_contingencia:
            raise ErroRenderizacao(
                f"Briefing {escopo} com status '{status}' (esperado 'aprovado'). "
                "Use --permitir-contingencia para gerar a versão de contingência."
            )
        logger.warning("Briefing %s com status '%s': gerando VERSÃO DE CONTINGÊNCIA.", escopo, status)
    pes = _ler_csv(processed_dir, "pes")
    if escopo != ESCOPO_BR and escopo not in set(pes.loc[pes["tipo_unidade"] == "DR", "uf"]):
        raise ErroRenderizacao(f"Escopo desconhecido: {escopo} (use BR ou a sigla de um DR).")
    qualidade = _ler_csv(processed_dir, "qualidade_resumo")
    return Entradas(
        escopo=escopo,
        doc_briefing=doc_briefing,
        doc_fatos=json.loads(fatos_path.read_text(encoding="utf-8")),
        fatos_path=fatos_path,
        pes=pes,
        atendimento=_ler_csv(processed_dir, "atendimento"),
        diag_indicador=_ler_csv(processed_dir, "diag_indicador"),
        diag_regiao=_ler_csv(processed_dir, "diag_regiao"),
        diag_dr=_ler_csv(processed_dir, "diag_dr"),
        cruzamento=_ler_csv(processed_dir, "cruzamento_volume_resultado"),
        projecao=_ler_csv(processed_dir, "projecao_2026"),
        achados=_ler_csv(processed_dir, "qualidade_achados"),
        n_regras=int(qualidade["regra_id"].nunique()),
        coleta_utc=coleta_mais_recente(raw_dir / "manifest.json"),
    )


# ---------------------------------------------------------------- dados das páginas
def tabela_painel(pes: pd.DataFrame, diag_indicador: pd.DataFrame, ano: int = ANO) -> pd.DataFrame:
    """Indicadores do DN no ano com índice, na ordem do PES, com contagens dos DRs.

    Só entram linhas com ``indice_analise`` preenchido ("-" vira vazio, nunca zero).
    """
    dn = pes[(pes["ano"] == ano) & (pes["tipo_unidade"] == "DN")].copy()
    dn = dn[dn["indice_analise"].notna()]
    cols = ["cod_indicador", "n_critico", "n_atencao", "n_atingido"]
    dn = dn.merge(diag_indicador[cols], on="cod_indicador", how="left")
    dn["farol"] = dn["indice_analise"].map(farol)
    return dn.reset_index(drop=True)


def tabela_painel_dr(
    pes: pd.DataFrame, diag_indicador: pd.DataFrame, diag_regiao: pd.DataFrame,
    uf: str, ano: int = ANO,
) -> pd.DataFrame:
    """Indicadores do DR no ano com índice, mais a mediana do índice na região e no país.

    As medianas vêm do diagnóstico (``diag_regiao`` e ``diag_indicador``), a mesma
    fonte da ficha de fatos, para que PDF e fatos mostrem o mesmo número.
    """
    linha = pes[(pes["ano"] == ano) & (pes["uf"] == uf) & pes["indice_analise"].notna()].copy()
    if linha.empty:
        return linha
    regiao = linha["regiao"].iloc[0]
    med_nac = diag_indicador.set_index("cod_indicador")["mediana_indice"]
    med_reg = diag_regiao[diag_regiao["regiao"] == regiao].set_index("cod_indicador")["mediana_indice"]
    linha["mediana_regiao"] = linha["cod_indicador"].map(med_reg)
    linha["mediana_nacional"] = linha["cod_indicador"].map(med_nac)
    linha["farol"] = linha["indice_analise"].map(farol)
    return linha.reset_index(drop=True)


def tabela_volume(atendimento: pd.DataFrame, uf: str, ano: int = ANO) -> pd.DataFrame:
    """Linhas de volume do Atendimento Social (matrículas e empresas) da unidade no ano."""
    base = atendimento[(atendimento["ano"] == ano) & (atendimento["uf"] == uf)
                       & (atendimento["mes_referencia"] == 12)]
    linhas = []
    for negocio, acao, rotulo in VOLUME_ATENDIMENTO:
        r = base[(base["linha_negocio"] == negocio) & (base["acao"] == acao)]
        if r.empty:
            continue
        r = r.iloc[0]
        indice = r["indice_analise"]
        linhas.append({
            "rotulo": rotulo,
            "previsto": compactar(r["previsto_txt"]),
            "realizado": compactar(r["realizado_txt"]),
            "indice": float(indice) if pd.notna(indice) else None,
            "farol": farol(indice) if pd.notna(indice) else None,
        })
    # dtype=object: ausência explícita fica None (o pandas trocaria por NaN).
    return pd.DataFrame(linhas, columns=["rotulo", "previsto", "realizado", "indice", "farol"],
                        dtype=object)


def contar_nao_apurados(
    achados: pd.DataFrame, pes: pd.DataFrame, uf: str = "DN", ano: int = ANO,
) -> tuple[int, int]:
    """(indicadores do Plano sem apuração no ano pela regra Q01, total de indicadores do Plano)."""
    q = achados[(achados["regra_id"] == REGRA_NAO_APURADO) & (achados["fonte"] == "pes")
                & (achados["ano"].astype(int) == ano) & achados["uf"].isin(["TODOS", uf])]
    total = pes.loc[pes["ano"] == ano, "cod_indicador"].nunique()
    return int(q["item"].nunique()), int(total)


def tabela_apoio(diag_dr: pd.DataFrame, minimo: int = MIN_CRITICOS_APOIO) -> pd.DataFrame:
    """DRs com ``minimo`` ou mais indicadores críticos, em ordem alfabética de UF."""
    apoio = diag_dr[diag_dr["n_critico"] >= minimo]
    return apoio.sort_values("uf")[["uf", "regiao", "n_critico", "resumo"]].reset_index(drop=True)


def tabela_projecao_dr(projecao: pd.DataFrame, uf: str) -> pd.DataFrame:
    """Projeção 2026 do DR: indicador, situação e aceleração necessária (texto)."""
    p = projecao[projecao["uf"] == uf].copy()

    def acel(r: pd.Series) -> str:
        if r["situacao"] == SIT_META_ATINGIDA or pd.isna(r["aceleracao_necessaria"]):
            return "—"
        return fmt_vezes(float(r["aceleracao_necessaria"]))

    p["aceleracao_txt"] = p.apply(acel, axis=1) if not p.empty else pd.Series(dtype=str)
    return p[["fonte", "indicador", "situacao", "aceleracao_txt", "mes_referencia"]].reset_index(drop=True)


def n_volume_ok_resultado_critico(cruzamento: pd.DataFrame) -> int:
    """DRs com índice de matrículas >= 100% e índice de empregabilidade < 80%."""
    d = cruzamento.dropna(subset=["indice_matriculas", "indice_empregabilidade"])
    return int(((d["indice_matriculas"] >= LIMITE_ATINGIDO)
                & (d["indice_empregabilidade"] < LIMITE_ATENCAO)).sum())


def fato_por_categoria(fatos: list[dict], categoria: str) -> dict | None:
    """Primeiro fato da categoria (ou None)."""
    return next((f for f in fatos if f.get("categoria") == categoria), None)


# ---------------------------------------------------------------- gráficos
def _estilo_mpl() -> None:
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica", "Arial", "Liberation Sans", "DejaVu Sans"],
        "font.size": 8,
        "axes.edgecolor": "#9AA5B1",
        "axes.labelcolor": "#333333",
        "xtick.color": "#333333",
        "ytick.color": "#333333",
        "axes.spines.top": False,
        "axes.spines.right": False,
    })


_OFFSETS_ROTULO = ((3, 2), (3, -8), (-11, 3), (-11, -9), (2, 7), (-6, 8), (3, -13), (-12, -14),
                   (8, -3), (-17, -3))


def _sobreposicao(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """Área de interseção entre duas caixas (x0, y0, x1, y1)."""
    dx = min(a[2], b[2]) - max(a[0], b[0])
    dy = min(a[3], b[3]) - max(a[1], b[1])
    return dx * dy if dx > 0 and dy > 0 else 0.0


def posicionar_rotulos(
    ax: Any, xs: list[float], ys: list[float], textos: list[str], fontsize: float = 6.5,
    ocupado: list[tuple[float, float, float, float]] | None = None,
) -> list[tuple[float, float]]:
    """Escolhe, para cada rótulo, o deslocamento (em pontos) com menor sobreposição.

    Deslocamento simples (sem adjustText): testa posições ao redor do ponto e evita
    outros rótulos, os marcadores e as caixas em ``ocupado`` (coordenadas em pontos).
    Os limites dos eixos precisam estar definidos antes da chamada.
    """
    fator = 72.0 / ax.figure.dpi
    pts = [tuple(c * fator for c in ax.transData.transform((x, y))) for x, y in zip(xs, ys)]
    caixas: list[tuple[float, float, float, float]] = list(ocupado or [])
    marcadores = [(px - 2.5, py - 2.5, px + 2.5, py + 2.5) for px, py in pts]
    escolhidos: list[tuple[float, float]] = []
    for i, ((px, py), texto) in enumerate(zip(pts, textos)):
        larg, alt = len(texto) * fontsize * 0.62, fontsize * 0.8
        outros = caixas + marcadores[:i] + marcadores[i + 1:]
        melhor, melhor_custo, melhor_caixa = _OFFSETS_ROTULO[0], float("inf"), None
        for dx, dy in _OFFSETS_ROTULO:
            caixa = (px + dx, py + dy, px + dx + larg, py + dy + alt)
            custo = sum(_sobreposicao(caixa, c) for c in outros)
            if custo < melhor_custo:
                melhor, melhor_custo, melhor_caixa = (dx, dy), custo, caixa
            if custo == 0:
                break
        caixas.append(melhor_caixa)
        escolhidos.append(melhor)
    return escolhidos


def grafico_empregabilidade(
    pes: pd.DataFrame, saida: Path, destaque: str | None = None, ano: int = ANO,
) -> Path:
    """Barras horizontais do realizado de empregabilidade por DR, com a meta nacional.

    Com ``destaque`` (UF), o DR fica em azul forte e os demais em cinza.
    """
    _estilo_mpl()
    base = pes[(pes["ano"] == ano) & (pes["cod_indicador"] == COD_EMPREGABILIDADE)]
    drs = base[(base["tipo_unidade"] == "DR") & base["realizado"].notna()].sort_values("realizado")
    meta_dn = base.loc[base["tipo_unidade"] == "DN", "meta"].dropna()

    fig, ax = plt.subplots(figsize=(3.6, 4.3))
    if destaque:
        cores = [AZUL if uf == destaque else CINZA_GRAFICO for uf in drs["uf"]]
    else:
        cores = [COR_REGIAO.get(r, CINZA) for r in drs["regiao"]]
    ax.barh(drs["uf"], drs["realizado"], color=cores, height=0.7)
    if not meta_dn.empty:
        meta = float(meta_dn.iloc[0])
        ax.axvline(meta, color=VERMELHO, lw=1.2, ls="--")
        ax.text(meta, len(drs) - 0.2, f" meta nacional {fmt_pct(meta)}", color=VERMELHO,
                fontsize=7, va="bottom", ha="left")
    ax.set_xlabel("Egressos empregados na indústria (%)")
    ax.tick_params(axis="y", labelsize=7)
    ax.set_ylim(-0.6, len(drs) + 0.4)
    if destaque:
        for rot in ax.get_yticklabels():
            if rot.get_text() == destaque:
                rot.set_fontweight("bold")
                rot.set_color(AZUL)
    else:
        regioes = [r for r in COR_REGIAO if r in set(drs["regiao"])]
        handles = [plt.Rectangle((0, 0), 1, 1, color=COR_REGIAO[r]) for r in regioes]
        ax.legend(handles, regioes, fontsize=6.5, frameon=False, loc="lower right")
    fig.tight_layout()
    saida.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(saida, dpi=DPI)
    plt.close(fig)
    return saida


def grafico_dispersao(cruzamento: pd.DataFrame, saida: Path, destaque: str | None = None) -> Path:
    """Dispersão índice de matrículas x índice de empregabilidade, com rótulos de UF.

    Sombreia a região "volume na meta, resultado crítico" (x >= 100 e y < 80).
    Com ``destaque`` (UF), o DR fica em azul forte e os demais em cinza.
    """
    _estilo_mpl()
    d = cruzamento.dropna(subset=["indice_matriculas", "indice_empregabilidade"])
    # O DR em destaque vem primeiro: seu rótulo é posicionado antes e os demais o evitam.
    d = d.sort_values("uf", key=lambda s: s != destaque, kind="stable").reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(3.6, 4.3))
    x, y = d["indice_matriculas"], d["indice_empregabilidade"]
    xmin = min(x.min(), LIMITE_ATINGIDO) - 12
    xmax = max(x.max(), LIMITE_ATINGIDO) + 15
    ymin = min(y.min(), LIMITE_ATENCAO) - 10
    ymax = max(y.max(), LIMITE_ATENCAO) + 8
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)

    ax.add_patch(Rectangle((LIMITE_ATINGIDO, ymin), xmax - LIMITE_ATINGIDO,
                           LIMITE_ATENCAO - ymin, color=VERMELHO, alpha=0.08, lw=0, zorder=0))
    n = n_volume_ok_resultado_critico(cruzamento)
    ax.text(xmax - 2, ymin + 2, f"Volume na meta, resultado crítico: {n} DRs",
            color=VERMELHO, fontsize=6.5, ha="right", va="bottom", zorder=4)
    ax.axvline(LIMITE_ATINGIDO, color=CINZA, lw=0.9, ls="--")
    ax.axhline(LIMITE_ATENCAO, color=VERMELHO, lw=0.9, ls="--")

    if destaque:
        e_destaque = d["uf"] == destaque
        cores = [AZUL if v else CINZA_GRAFICO for v in e_destaque]
        tamanhos = [40 if v else 14 for v in e_destaque]
    else:
        e_destaque = pd.Series(False, index=d.index)
        cores = [COR_REGIAO.get(r, CINZA) for r in d["regiao"]]
        tamanhos = [18] * len(d)
    ax.scatter(x, y, c=cores, s=tamanhos, zorder=3)
    ax.set_xlabel("Índice de matrículas (%)")
    ax.set_ylabel("Índice de empregabilidade (%)")
    fig.tight_layout()

    # Caixa do texto da região sombreada, para os rótulos não a cobrirem.
    fator = 72.0 / fig.dpi
    x1, y0 = ax.transData.transform((xmax - 2, ymin + 2))
    texto_caixa = (x1 * fator - 140, y0 * fator, x1 * fator, y0 * fator + 8)
    offsets = posicionar_rotulos(ax, list(x), list(y), list(d["uf"]), ocupado=[texto_caixa])
    for i, (dx, dy) in enumerate(offsets):
        forte = bool(e_destaque.iloc[i])
        ax.annotate(d.loc[i, "uf"], (x.iloc[i], y.iloc[i]), xytext=(dx, dy),
                    textcoords="offset points", fontsize=8 if forte else 6.5,
                    fontweight="bold" if forte else "normal",
                    color=AZUL if forte else ("#8A9099" if destaque else "#222222"), zorder=5)
    saida.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(saida, dpi=DPI)
    plt.close(fig)
    return saida


# ---------------------------------------------------------------- estilos e flowables
def _estilos() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("corpo", fontName=FONTE, fontSize=CORPO_PT, leading=CORPO_PT * 1.25)
    return {
        "corpo": base,
        "negrito": ParagraphStyle("negrito", parent=base, fontName=FONTE_NEGRITO),
        "titulo": ParagraphStyle("titulo", parent=base, fontName=FONTE_NEGRITO, fontSize=13,
                                 leading=16, textColor=colors.HexColor(AZUL)),
        "subtitulo": ParagraphStyle("subtitulo", parent=base, textColor=colors.HexColor(CINZA)),
        "secao": ParagraphStyle("secao", parent=base, fontName=FONTE_NEGRITO, fontSize=11,
                                leading=14, textColor=colors.HexColor(AZUL), spaceBefore=6,
                                spaceAfter=3),
        "subsecao": ParagraphStyle("subsecao", parent=base, fontName=FONTE_NEGRITO,
                                   textColor=colors.HexColor(AZUL), spaceBefore=3, spaceAfter=1),
        "mensagem": ParagraphStyle("mensagem", parent=base, fontSize=10.5, leading=14,
                                   textColor=colors.white),
        "cab_tabela": ParagraphStyle("cab_tabela", parent=base, fontName=FONTE_NEGRITO,
                                     textColor=colors.white),
        "faixa": ParagraphStyle("faixa", parent=base, fontName=FONTE_NEGRITO, fontSize=11,
                                textColor=colors.white, alignment=TA_CENTER),
        "fluxo": ParagraphStyle("fluxo", parent=base, fontName=FONTE_NEGRITO, alignment=TA_CENTER,
                                textColor=colors.HexColor(AZUL), leading=11),
        "seta": ParagraphStyle("seta", parent=base, fontName=FONTE_NEGRITO, fontSize=12,
                               alignment=TA_CENTER, textColor=colors.HexColor(AZUL)),
    }


def ids_fatos(ids: list[str], cor: str = CINZA) -> str:
    """Marcação dos IDs dos fatos em fonte pequena cinza: " [F001, F013]"."""
    if not ids:
        return ""
    return f' <font size="7" color="{cor}">[{escape(", ".join(ids))}]</font>'


def circulo_farol(classe: str | None, diametro: float = 9) -> Drawing | str:
    """Círculo colorido do farol ("" quando não há classe)."""
    if classe not in COR_FAROL:
        return ""
    d = Drawing(diametro + 2, diametro + 2)
    r = diametro / 2
    d.add(Circle(r + 1, r + 1, r, fillColor=colors.HexColor(COR_FAROL[classe]),
                 strokeColor=None))
    return d


class _Marcador(Flowable):
    """Flowable invisível que registra o número de páginas (para teste/controle)."""

    def __init__(self, contador: dict) -> None:
        super().__init__()
        self.contador = contador

    def wrap(self, *args: Any) -> tuple[float, float]:
        return 0, 0

    def draw(self) -> None:
        self.contador["paginas"] = self.canv.getPageNumber()


def _cabecalho_secao(texto: str, est: dict) -> Paragraph:
    return Paragraph(texto_seguro(texto), est["secao"])


def _tabela_estilizada(dados: list[list[Any]], larguras: list[float], extras: list | None = None) -> Table:
    t = Table(dados, colWidths=larguras, repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(AZUL)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#D5DBE3")),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    t.setStyle(TableStyle(estilo + (extras or [])))
    return t


def _p(texto: Any, estilo: ParagraphStyle) -> Paragraph:
    return Paragraph(texto_seguro(texto), estilo)


# ---------------------------------------------------------------- página 1
def _faixa_contingencia(est: dict, largura: float) -> Table:
    t = Table([[Paragraph("VERSÃO DE CONTINGÊNCIA — texto não redigido pela IA; "
                          "fatos apresentados sem síntese", est["faixa"])]],
              colWidths=[largura])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(VERMELHO)),
                           ("TOPPADDING", (0, 0), (-1, -1), 4),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return t


def pagina_briefing(ent: Entradas, est: dict, largura: float, data_ref: date) -> list[Flowable]:
    """Página 1: cabeçalho, mensagem central, destaques, pontos de atenção e recomendações."""
    fl: list[Flowable] = [
        _p(TITULO_DOC, est["titulo"]),
        _p(f"Escopo: {ent.escopo} · Data: {data_ref.strftime('%d/%m/%Y')}", est["subtitulo"]),
        Spacer(1, 6),
    ]
    if ent.contingencia:
        fl += [_faixa_contingencia(est, largura), Spacer(1, 6)]
        aviso = ent.doc_briefing.get("aviso")
        if aviso:
            fl.append(_p(aviso, est["corpo"]))
        grupos = ent.doc_briefing.get("fatos_por_categoria") or {}
        rotulos = {"resultado_nacional": "Resultados nacionais", "cruzamento": "Volume e resultado"}
        for cat in CATEGORIAS_CONTINGENCIA:
            itens = grupos.get(cat) or []
            if not itens:
                continue
            fl.append(_cabecalho_secao(rotulos[cat], est))
            for it in itens:
                fl.append(Paragraph("• " + texto_seguro(it["frase"]) + ids_fatos([it["id"]]),
                                    est["corpo"]))
        return fl

    b = ent.doc_briefing["briefing"]
    mc = b["mensagem_central"]
    caixa = Table([[Paragraph(texto_seguro(mc["texto"]) + ids_fatos(mc.get("fatos", []), "#C9D3DF"),
                              est["mensagem"])]], colWidths=[largura])
    caixa.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(AZUL)),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    fl += [Paragraph("Mensagem central", est["secao"]), caixa]

    item_estilo = ParagraphStyle("item", parent=est["corpo"], spaceAfter=2.5)
    for secao, titulo in (("destaques", "Destaques"), ("pontos_atencao", "Pontos de atenção")):
        fl.append(_cabecalho_secao(titulo, est))
        for item in b.get(secao) or []:
            fl.append(Paragraph(
                f"<b>{texto_seguro(item['titulo'])}.</b> {texto_seguro(item['texto'])}"
                + ids_fatos(item.get("fatos", [])), item_estilo))

    fl.append(_cabecalho_secao("Recomendações", est))
    dados: list[list[Any]] = [[Paragraph(h, est["cab_tabela"])
                               for h in ("Recomendação", "Responsável", "Horizonte")]]
    for r in b.get("recomendacoes") or []:
        dados.append([
            Paragraph(f"<b>{texto_seguro(r['titulo'])}.</b> {texto_seguro(r['acao'])}"
                      + ids_fatos(r.get("fatos", [])), est["corpo"]),
            _p(r["responsavel"], est["corpo"]),
            _p(r["horizonte"], est["corpo"]),
        ])
    fl.append(_tabela_estilizada(dados, [largura * 0.61, largura * 0.16, largura * 0.23]))
    return fl


# ---------------------------------------------------------------- página 2
def _bloco_volume(ent: Entradas, est: dict, largura: float) -> list[Flowable]:
    """Volume entregue no Atendimento Social + contagem de indicadores sem apuração."""
    uf = ent.escopo if ent.eh_dr else "DN"
    unidade = f"DR {ent.escopo}" if ent.eh_dr else "DN"
    fl: list[Flowable] = [_cabecalho_secao(f"Volume entregue — Atendimento Social, {ANO} ({unidade})", est)]
    vol = tabela_volume(ent.atendimento, uf)
    if vol.empty:
        fl.append(_p("Sem dados de Atendimento Social para a unidade.", est["corpo"]))
    else:
        dados: list[list[Any]] = [[Paragraph(h, est["cab_tabela"])
                                   for h in ("Linha", "Previsto", "Realizado", "Índice", "")]]
        for _, r in vol.iterrows():
            dados.append([_p(r["rotulo"], est["corpo"]), _p(r["previsto"], est["corpo"]),
                          _p(r["realizado"], est["corpo"]),
                          _p(fmt_pct_ou_traco(r["indice"]), est["negrito"]),
                          circulo_farol(r["farol"])])
        fl.append(_tabela_estilizada(dados, [largura * w for w in (0.40, 0.18, 0.18, 0.14, 0.10)]))
    n, total = contar_nao_apurados(ent.achados, ent.pes, uf)
    fl += [Spacer(1, 6), _p(f"{n} dos {total} indicadores do Plano não tiveram apuração em {ANO} "
                            f"(regra {REGRA_NAO_APURADO}: sem meta nem realizado no portal).",
                            est["corpo"])]
    return fl


def _texto_farol() -> str:
    return ("Farol: atingido = índice de 100% ou mais; atenção = 80% a 99,9%; "
            "crítico = abaixo de 80%.")


def pagina_painel(ent: Entradas, est: dict, largura: float) -> list[Flowable]:
    """Página 2 (BR): tabela dos indicadores do DN agrupados por perspectiva + volume."""
    painel = tabela_painel(ent.pes, ent.diag_indicador)
    fl: list[Flowable] = [
        Paragraph(f"Painel sistêmico — Departamento Nacional, {ANO}", est["titulo"]),
        _p("Indicadores do Plano Estratégico Sistêmico com dado apurado no consolidado nacional. "
           + _texto_farol() + " A última coluna conta os DRs em cada faixa.", est["corpo"]),
        Spacer(1, 6),
    ]
    cab = ["Indicador", "Meta", "Realizado", "Índice", "", "Situação nos DRs"]
    dados: list[list[Any]] = [[Paragraph(h, est["cab_tabela"]) for h in cab]]
    extras: list = []
    for perspectiva, grupo in painel.groupby("perspectiva", sort=False):
        linha = len(dados)
        dados.append([_p(perspectiva, est["negrito"]), "", "", "", "", ""])
        extras += [("SPAN", (0, linha), (-1, linha)),
                   ("BACKGROUND", (0, linha), (-1, linha), colors.HexColor(CINZA_CLARO))]
        for _, r in grupo.iterrows():
            if pd.notna(r["n_critico"]):
                situacao = (f"DRs: {int(r['n_critico'])} críticos · {int(r['n_atencao'])} atenção"
                            f" · {int(r['n_atingido'])} atingidos")
            else:
                situacao = "DRs: sem dado"
            dados.append([
                _p(r["indicador"], est["corpo"]),
                _p(valor_painel(r["meta_txt"], r["meta"], r["unidade"]), est["corpo"]),
                _p(valor_painel(r["realizado_txt"], r["realizado"], r["unidade"]), est["corpo"]),
                _p(fmt_pct(r["indice_analise"]), est["negrito"]),
                circulo_farol(r["farol"]),
                _p(situacao, est["corpo"]),
            ])
    larguras = [0.36, 0.13, 0.13, 0.09, 0.04, 0.25]
    fl.append(_tabela_estilizada(dados, [largura * w for w in larguras], extras))
    return fl + _bloco_volume(ent, est, largura)


def pagina_painel_dr(ent: Entradas, est: dict, largura: float) -> list[Flowable]:
    """Página 2 (DR): indicadores do DR com medianas da região e nacional dos DRs + volume."""
    painel = tabela_painel_dr(ent.pes, ent.diag_indicador, ent.diag_regiao, ent.escopo)
    regiao = painel["regiao"].iloc[0] if not painel.empty else "-"
    fl: list[Flowable] = [
        _p(f"Painel do DR {ent.escopo} ({regiao}), {ANO}", est["titulo"]),
        _p("Indicadores do Plano Estratégico Sistêmico com dado apurado no DR. " + _texto_farol()
           + " As medianas são do índice dos DRs com dado válido, para referência (não é ranking).",
           est["corpo"]),
        Spacer(1, 6),
    ]
    cab = ["Indicador", "Meta", "Realizado", "Índice", "", "Mediana região", "Mediana DRs"]
    dados: list[list[Any]] = [[Paragraph(h, est["cab_tabela"]) for h in cab]]
    extras: list = []
    for perspectiva, grupo in painel.groupby("perspectiva", sort=False):
        linha = len(dados)
        dados.append([_p(perspectiva, est["negrito"])] + [""] * 6)
        extras += [("SPAN", (0, linha), (-1, linha)),
                   ("BACKGROUND", (0, linha), (-1, linha), colors.HexColor(CINZA_CLARO))]
        for _, r in grupo.iterrows():
            dados.append([
                _p(r["indicador"], est["corpo"]),
                _p(valor_painel(r["meta_txt"], r["meta"], r["unidade"]), est["corpo"]),
                _p(valor_painel(r["realizado_txt"], r["realizado"], r["unidade"]), est["corpo"]),
                _p(fmt_pct(r["indice_analise"]), est["negrito"]),
                circulo_farol(r["farol"]),
                _p(fmt_pct_ou_traco(r["mediana_regiao"]), est["corpo"]),
                _p(fmt_pct_ou_traco(r["mediana_nacional"]), est["corpo"]),
            ])
    larguras = [0.33, 0.13, 0.13, 0.11, 0.04, 0.13, 0.13]
    fl.append(_tabela_estilizada(dados, [largura * w for w in larguras], extras))
    return fl + _bloco_volume(ent, est, largura)


# ---------------------------------------------------------------- página 3
def _tabela_projecao(ent: Entradas, est: dict, largura: float) -> list[Flowable]:
    proj = tabela_projecao_dr(ent.projecao, ent.escopo)
    if proj.empty:
        return [_cabecalho_secao("(c) Projeção 2026", est),
                _p("Sem projeção 2026 para o DR.", est["corpo"])]
    mes = int(proj["mes_referencia"].iloc[0])
    fl: list[Flowable] = [
        _cabecalho_secao(f"(c) Projeção 2026 do DR {ent.escopo}", est),
        _p(f"Ritmo linear com dados até o mês {mes}; não considera sazonalidade. "
           "Aceleração = ritmo mensal necessário ÷ ritmo atual.", est["corpo"]),
        Spacer(1, 3),
    ]
    dados: list[list[Any]] = [[Paragraph(h, est["cab_tabela"])
                               for h in ("Indicador", "Situação", "Aceleração necessária")]]
    extras: list = []
    for i, (_, r) in enumerate(proj.iterrows(), start=1):
        nome = r["indicador"] + (" (Atendimento Social)" if r["fonte"] == "atendimento" else "")
        cor = COR_SITUACAO.get(r["situacao"], CINZA)
        dados.append([_p(nome, est["corpo"]),
                      Paragraph(f'<font color="{cor}"><b>{texto_seguro(r["situacao"])}</b></font>',
                                est["corpo"]),
                      _p(r["aceleracao_txt"], est["corpo"])])
    fl.append(_tabela_estilizada(dados, [largura * w for w in (0.58, 0.22, 0.20)], extras))
    return fl


def _tabela_apoio(ent: Entradas, est: dict, largura: float) -> list[Flowable]:
    fl: list[Flowable] = [_cabecalho_secao(
        f"(c) Trilha de apoio — DRs com {MIN_CRITICOS_APOIO} ou mais indicadores críticos", est)]
    dados: list[list[Any]] = [[Paragraph(h, est["cab_tabela"])
                               for h in ("UF", "Região", "Críticos", "Resumo")]]
    for _, r in tabela_apoio(ent.diag_dr).iterrows():
        dados.append([_p(v, est["corpo"])
                      for v in (r["uf"], r["regiao"], int(r["n_critico"]), r["resumo"])])
    fl.append(_tabela_estilizada(dados, [largura * w for w in (0.12, 0.28, 0.15, 0.45)]))
    return fl


def pagina_assimetrias(ent: Entradas, est: dict, largura: float, figs_dir: Path) -> list[Flowable]:
    """Página 3: empregabilidade por DR, volume x resultado e trilha de apoio (ou projeção do DR)."""
    destaque = ent.escopo if ent.eh_dr else None
    fig_a = grafico_empregabilidade(ent.pes, figs_dir / f"empregabilidade_dr_{ent.escopo}.png", destaque)
    fig_b = grafico_dispersao(ent.cruzamento, figs_dir / f"volume_resultado_{ent.escopo}.png", destaque)
    meia = largura / 2 - 4
    alt = meia * 4.3 / 3.6
    graficos = Table(
        [[Paragraph(f"(a) Empregabilidade de egressos por DR, {ANO}", est["negrito"]),
          Paragraph("(b) Matrículas x empregabilidade (índices, %)", est["negrito"])],
         [Image(str(fig_a), width=meia, height=alt), Image(str(fig_b), width=meia, height=alt)]],
        colWidths=[meia + 4, meia + 4],
    )
    graficos.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    if ent.eh_dr:
        titulo = f"O DR {ent.escopo} entre os Departamentos Regionais"
        legenda = (f"O DR {ent.escopo} aparece em azul; os demais DRs, em cinza. "
                   "Em (a), linha tracejada na meta nacional. ")
    else:
        titulo = "Assimetrias entre os Departamentos Regionais"
        legenda = "Em (a), cores por região e linha tracejada na meta nacional. "
    legenda += ("Em (b), linhas em 100% (matrículas) e "
                "80% (empregabilidade); a área sombreada reúne DRs com volume na meta e resultado "
                "crítico. Associação não indica causa.")
    fl: list[Flowable] = [_p(titulo, est["titulo"]), Spacer(1, 4), graficos, _p(legenda, est["corpo"])]
    fl += _tabela_projecao(ent, est, largura) if ent.eh_dr else _tabela_apoio(ent, est, largura)
    return fl


# ---------------------------------------------------------------- página 4
def _fluxo(est: dict, largura: float, n_regras: int) -> Table:
    celulas: list[Any] = []
    for i, etapa in enumerate(e.format(n_regras=n_regras) for e in ETAPAS_FLUXO):
        if i:
            celulas.append(Paragraph("»", est["seta"]))
        celulas.append(_p(etapa, est["fluxo"]))
    n = len(ETAPAS_FLUXO)
    seta = 12.0
    caixa = (largura - seta * (n - 1)) / n
    larguras = [caixa if i % 2 == 0 else seta for i in range(2 * n - 1)]
    t = Table([celulas], colWidths=larguras)
    estilo = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
              ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]
    for i in range(0, 2 * n - 1, 2):
        estilo += [("BOX", (i, 0), (i, 0), 0.8, colors.HexColor(AZUL)),
                   ("BACKGROUND", (i, 0), (i, 0), colors.HexColor(CINZA_CLARO))]
    t.setStyle(TableStyle(estilo))
    return t


def selo_verificacao(ent: Entradas) -> list[str]:
    """Linhas do selo de verificação (texto puro)."""
    linhas: list[str] = []
    if ent.contingencia:
        linhas.append("Versão de contingência: sem texto da IA; todas as frases vêm da ficha de fatos.")
    else:
        b = ent.doc_briefing["briefing"]
        n = contar_numeros(b)
        erros = validar(b, ent.fatos)
        if erros:
            linhas.append(f"{n} números no briefing; {len(erros)} pendência(s) no validador.")
        else:
            linhas.append(f"{n} números no briefing, todos validados contra a ficha de fatos.")
    tri = fato_por_categoria(ent.fatos, "triangulacao")
    if tri:
        linhas.append(tri["frase"])
    linhas.append("Consolidado nacional reconciliado: DN = 27 DRs + SENAI CETIQT.")
    if ent.contingencia:
        linhas.append(f"Versão do prompt: {ent.doc_briefing.get('versao_prompt', '-')}.")
    else:
        linhas.append(f"Modelo: {ent.doc_briefing.get('modelo', '-')} · versão do prompt: "
                      f"{ent.doc_briefing.get('versao_prompt', '-')}.")
    linhas.append(f"Ficha de fatos ({len(ent.fatos)} fatos) · sha256: {sha256_curto(ent.fatos_path)}.")
    return linhas


def _lista_limitacoes(itens: list[dict], est: dict) -> list[Flowable]:
    if not itens:
        return [_p("Nenhum item nesta categoria.", est["corpo"])]
    return [Paragraph("• " + texto_seguro(l["texto"]) + ids_fatos(l.get("fatos", [])), est["corpo"])
            for l in itens]


def pagina_metodo(ent: Entradas, est: dict, largura: float) -> list[Flowable]:
    """Página 4: fluxo, selo de verificação, limitações, termos e fontes."""
    fl: list[Flowable] = [
        _p("Método e segurança", est["titulo"]), Spacer(1, 6),
        _fluxo(est, largura, ent.n_regras), Spacer(1, 8),
    ]
    selo = [[Paragraph("Selo de verificação", est["cab_tabela"])]]
    selo += [[Paragraph("• " + texto_seguro(l), est["corpo"])] for l in selo_verificacao(ent)]
    t = Table(selo, colWidths=[largura])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(VERDE)),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor(VERDE)),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    fl.append(t)

    fl.append(_cabecalho_secao("Limitações", est))
    if ent.contingencia:
        fl.append(_p("Sem limitações redigidas (versão de contingência).", est["corpo"]))
    else:
        fonte, metodo = separar_limitacoes(
            ent.doc_briefing["briefing"].get("limitacoes") or [], ent.fatos)
        fl.append(_p("Qualidade da fonte (achados reportados ao DN)", est["subsecao"]))
        fl += _lista_limitacoes(fonte, est)
        fl.append(_p("Limites do método", est["subsecao"]))
        fl += _lista_limitacoes(metodo, est)

    fl.append(_cabecalho_secao("Termos", est))
    termo_estilo = ParagraphStyle("termo", parent=est["corpo"], spaceAfter=1.5)
    for termo, definicao in TERMOS:
        fl.append(Paragraph(f"<b>{texto_seguro(termo)}:</b> {texto_seguro(definicao)}", termo_estilo))

    fl.append(_cabecalho_secao("Fontes", est))
    coleta = ent.coleta_utc or "data indisponível"
    fontes = [
        "Plano Estratégico Sistêmico (API): " + ENDPOINTS["pes"],
        "Atendimento Social (API): " + ENDPOINTS["atendimento"],
        "Relatório de Gestão SENAI 2025 (PDF): " + URL_RELATORIO,
        f"Coleta mais recente: {coleta}.",
    ]
    fonte_estilo = ParagraphStyle("fonte", parent=est["corpo"], wordWrap="CJK", leading=11)
    for f in fontes:
        fl.append(Paragraph("• " + texto_seguro(f), fonte_estilo))
    return fl


# ---------------------------------------------------------------- montagem
def _rodape(revisado_por: str):
    def desenhar(canv, doc) -> None:
        canv.saveState()
        canv.setFont(FONTE, 7.5)
        canv.setFillColor(colors.HexColor(CINZA))
        texto = (f"Gerado por pipeline automatizado com IA generativa e validado contra dados "
                 f"oficiais | Revisado por: {revisado_por} | página {doc.page}/{MAX_PAGINAS}")
        canv.drawCentredString(A4[0] / 2, 1.0 * cm, texto)
        canv.setStrokeColor(colors.HexColor(AZUL))
        canv.setLineWidth(0.6)
        canv.line(doc.leftMargin, 1.4 * cm, A4[0] - doc.rightMargin, 1.4 * cm)
        canv.restoreState()
    return desenhar


def _pagina(flowables: list[Flowable], largura: float, altura: float, nome: str) -> KeepInFrame:
    """Prende o conteúdo a uma página: se não couber, a geração falha (sem encolher fonte)."""
    return KeepInFrame(largura, altura, flowables, mode="error", name=nome)


def gerar_pdf(
    escopo: str,
    revisado_por: str,
    permitir_contingencia: bool = False,
    outputs_dir: Path = OUTPUTS_DIR,
    processed_dir: Path = PROCESSED_DIR,
    raw_dir: Path = RAW_DIR,
    data_ref: date | None = None,
) -> Path:
    """Gera ``outputs/Executive_Briefing_SENAI_{escopo}.pdf``.

    Raises:
        ErroRenderizacao: entradas inválidas ou conteúdo que não cabe em 4 páginas.
    """
    if not revisado_por.strip():
        raise ErroRenderizacao("Informe o nome do revisor (--revisado-por).")
    ent = carregar_entradas(escopo, permitir_contingencia, outputs_dir, processed_dir, raw_dir)
    data_ref = data_ref or date.today()
    saida = outputs_dir / f"Executive_Briefing_SENAI_{escopo}.pdf"
    figs_dir = outputs_dir / "figs"

    margem = 1.5 * cm
    doc = BaseDocTemplate(str(saida), pagesize=A4, leftMargin=margem, rightMargin=margem,
                          topMargin=margem, bottomMargin=1.8 * cm, title=TITULO_DOC,
                          author="senai-briefing-ia")
    largura = doc.width
    altura = doc.height
    frame = Frame(doc.leftMargin, doc.bottomMargin, largura, altura, id="corpo",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="pag", frames=[frame], onPage=_rodape(revisado_por))])

    est = _estilos()
    contador: dict = {"paginas": 0}
    paginas = [
        pagina_briefing(ent, est, largura, data_ref),
        pagina_painel_dr(ent, est, largura) if ent.eh_dr else pagina_painel(ent, est, largura),
        pagina_assimetrias(ent, est, largura, figs_dir),
        pagina_metodo(ent, est, largura),
    ]
    historia: list[Flowable] = []
    for i, conteudo in enumerate(paginas, start=1):
        if i > 1:
            historia.append(PageBreak())
        historia.append(_pagina(conteudo, largura, altura, f"página {i}"))
    historia.append(_Marcador(contador))
    try:
        doc.build(historia)
    except Exception as exc:  # LayoutError do KeepInFrame em modo "error"
        raise ErroRenderizacao(f"Conteúdo não coube no layout de {MAX_PAGINAS} páginas: {exc}") from exc
    if contador["paginas"] > MAX_PAGINAS:
        raise ErroRenderizacao(f"PDF com {contador['paginas']} páginas (máximo {MAX_PAGINAS}).")
    logger.info("PDF salvo em %s (%d páginas%s).", saida, contador["paginas"],
                ", CONTINGÊNCIA" if ent.contingencia else "")
    return saida


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m src.render_pdf --escopo BR --revisado-por "Nome"``."""
    parser = argparse.ArgumentParser(description="Gera o Executive Briefing em PDF.")
    parser.add_argument("--escopo", default=ESCOPO_BR, help='"BR" ou sigla do DR')
    parser.add_argument("--revisado-por", required=True, help="Nome de quem revisou o briefing")
    parser.add_argument("--permitir-contingencia", action="store_true",
                        help="Aceita briefing com status diferente de 'aprovado'")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        gerar_pdf(args.escopo, args.revisado_por, args.permitir_contingencia)
    except ErroRenderizacao as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
