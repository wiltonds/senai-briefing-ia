"""Dados do produto: gera os JSON que alimentam a página HTML (app/).

Gera ``outputs/app_data/{escopo}.json`` para BR e para os 27 DRs, e
``outputs/app_data/regioes.json``. Lê só ``data/processed``, o manifest da
coleta, ``outputs/briefing_{escopo}.json`` e (para o nome do revisor) o PDF
executivo já gerado. Gera a figura volume x resultado com ``src.render_pdf``
quando ela ainda não existe.

Regras:
- Todo valor exibido é texto já formatado em pt-BR (``src.formato``/``src.fatos``);
  o único número cru é ``pct_barra`` (largura da barra, 0 a 150).
- Status: "atingido" | "atencao" | "critico" (mesmo farol do diagnóstico);
  "sem_dado" quando não há índice e "neutro" em linhas informativas do detalhe.
- Listas de DRs em ordem alfabética de UF (sem ranking).

Uso:
    python -m src.produto
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd

from src import render_pdf
from src.diagnostico import farol
from src.fatos import (
    ANO_FECHADO,
    ANO_PROJECAO,
    COD_EMPREGABILIDADE,
    ESCOPO_BR,
    LIMITE_ATENCAO,
    LIMITE_ATINGIDO,
    UF_DN,
    Dados,
    aceleracao_mediana,
    carregar_dados,
    farol_valido,
    fmt_publicado,
    paginas_triangulacao,
    unidades,
)
from src.formato import fmt_decimal, fmt_int, fmt_pct, fmt_valor
from src.projecao import SIT_ACELERACAO, SIT_ATINGIDA, SIT_NO_RITMO, SIT_RISCO
from src.validador import UFS

logger = logging.getLogger("produto")

RAIZ = Path(__file__).resolve().parents[1]
PROCESSED_DIR = RAIZ / "data" / "processed"
MANIFEST_PATH = RAIZ / "data" / "raw" / "manifest.json"
OUTPUTS_DIR = RAIZ / "outputs"
APP_DATA_DIR = OUTPUTS_DIR / "app_data"
FIGS_DIR = OUTPUTS_DIR / "figs"
ENCODING_CSV = "utf-8-sig"

ESCOPOS: tuple[str, ...] = (ESCOPO_BR,) + UFS
COD_CONSULTORIAS = "2.1"
REGIOES = ("Norte", "Nordeste", "Centro-Oeste", "Sudeste", "Sul")
LIMITE_BARRA = 150.0

NOMES: dict[str, str] = {
    "BR": "Brasil", "AC": "Acre", "AL": "Alagoas", "AM": "Amazonas", "AP": "Amapá",
    "BA": "Bahia", "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo",
    "GO": "Goiás", "MA": "Maranhão", "MG": "Minas Gerais", "MS": "Mato Grosso do Sul",
    "MT": "Mato Grosso", "PA": "Pará", "PB": "Paraíba", "PE": "Pernambuco", "PI": "Piauí",
    "PR": "Paraná", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RO": "Rondônia",
    "RR": "Roraima", "RS": "Rio Grande do Sul", "SC": "Santa Catarina", "SE": "Sergipe",
    "SP": "São Paulo", "TO": "Tocantins",
}
MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
         "setembro", "outubro", "novembro", "dezembro")

FONTE_PES = f"Fonte: Plano Estratégico Sistêmico {ANO_FECHADO} (Portal da Transparência)"
FONTE_AT = f"Fonte: Atendimento Social {ANO_FECHADO} (Portal da Transparência)"
FONTE_CRUZ = f"Cruzamento: Atendimento Social × Plano Estratégico, {ANO_FECHADO}"
NOTA_PROJECAO = "Projeção linear; não considera sazonalidade do fim de ano."

LINHA_MATRICULAS = "Programas (matrículas)"
ACAO_TOTAL = "TOTAL"
ACAO_TEC = "EDUCAÇÃO PROFISSIONAL TÉCNICA DE NÍVEL MÉDIO"
ACAO_FIC = "FORMAÇÃO INICIAL E CONTINUADA"
ACAO_EPT = "EDUCAÇÃO PARA O TRABALHO"
ACAO_SUP = "EDUCAÇÃO SUPERIOR"
ACAO_EMPRESAS = "QUANTIDADE DE EMPRESAS ATENDIDAS"
ROTULO_ACAO = {
    ACAO_TOTAL: "Matrículas — total", ACAO_TEC: "Técnico de nível médio",
    ACAO_FIC: "Formação inicial e continuada", ACAO_EPT: "Educação para o trabalho",
    ACAO_SUP: "Educação superior", ACAO_EMPRESAS: "Empresas atendidas",
}
ACOES_BR = (ACAO_TOTAL, ACAO_TEC, ACAO_FIC, ACAO_EMPRESAS)
ACOES_DR = (ACAO_TOTAL, ACAO_TEC, ACAO_FIC, ACAO_EPT, ACAO_SUP, ACAO_EMPRESAS)

# Nome curto exibido na página (o nome oficial vai em ``nome_oficial``, para tooltip).
NOME_CURTO: dict[str, str] = {
    "1": "Empregabilidade de egressos",
    "2.1": "Consultorias de produtividade",
    "2.2": "Consultorias em descarbonização",
    "2.3": "Pesquisa aplicada",
    "3": "Jovens empregados na indústria",
    "4.1": "Trabalhadores da indústria matriculados",
    "4.2": "Fidelização de alunos",
    "5.1": "Municípios em plataformas digitais",
    "5.2": "Indústrias em plataformas digitais",
    "6": "Empresas atendidas por STI",
    "7": "Tecnologias educacionais",
    "8": "Polos com matrículas ativas",
    "9": "Atendimentos em colaboração na Rede",
    "10": "Projetos de neoindustrialização",
    "11.1": "Recursos com parceiros em inovação",
    "11.2": "Recursos com parceiros em educação",
    "12": "Envio de dados ao Data Lake",
    "13.1": "Colaboradores capacitados",
    "13.2": "Maturidade digital",
    "13.3": "Maturidade ambiental",
    "14.1": "Gratuidade em áreas técnicas",
    "14.2": "Receitas de serviços e convênios",
}
# Linhas do Atendimento Social que entram na projeção 2026.
NOME_CURTO_ATENDIMENTO: dict[str, str] = {
    "AT-matriculas": "Matrículas (Atendimento Social)",
    "AT-empresas_sti": "Empresas atendidas (Atendimento Social)",
}

# KPI "Indicadores atingidos" (DR): status pelo número de indicadores críticos.
CRITICOS_TRILHA = 4  # a partir daqui, "critico" (trilha de apoio do DN)
ROTULO_STATUS_INDICADORES = {"atingido": "Sem críticos", "atencao": "Atenção",
                             "critico": "Trilha de apoio"}
NOTA_INDICADORES = ("Status pelo número de indicadores críticos: nenhum = sem críticos; "
                    f"1 a {CRITICOS_TRILHA - 1} = atenção; {CRITICOS_TRILHA} ou mais = "
                    "trilha de apoio do DN. A barra mostra a parcela de indicadores atingidos.")

SEM_DADO = "sem_dado"
NEUTRO = "neutro"
RE_REVISOR = re.compile(r"Revisado por:\s*(.+?)\s*\|\s*página")


# --------------------------------------------------------------------------
# Auxiliares
# --------------------------------------------------------------------------

def nome_curto(cod: Any, nome_oficial: str) -> str:
    """Nome curto do indicador para exibição (nunca o código)."""
    c = str(cod)
    if c in NOME_CURTO:
        return NOME_CURTO[c]
    if c in NOME_CURTO_ATENDIMENTO:
        return NOME_CURTO_ATENDIMENTO[c]
    logger.warning("Indicador %s sem nome curto; usando o nome oficial", c)
    return nome_oficial


def status(indice: Any) -> str:
    """Farol do índice; "sem_dado" quando vazio."""
    return farol(indice) or SEM_DADO


def pct_barra(indice: Any) -> float:
    """Largura da barra: índice limitado a 0–150 (0 sem dado)."""
    if indice is None or pd.isna(indice):
        return 0.0
    return round(min(max(float(indice), 0.0), LIMITE_BARRA), 1)


def pct_ou_sem_dado(indice: Any) -> str:
    """Índice formatado ou "sem dado" (nunca trata ausência como zero)."""
    return "sem dado" if indice is None or pd.isna(indice) else fmt_pct(indice)


def linha(nome: str, valor: str, st: str, nome_oficial: str | None = None) -> dict[str, str]:
    """Linha do painel de detalhe (``nome_oficial`` só em linhas de indicador)."""
    d = {"nome": nome, "valor": valor, "status": st}
    if nome_oficial:
        d["nome_oficial"] = nome_oficial
    return d


def kpi(rotulo: str, valor: str, comparacao: str, indice: Any, st: str, fonte: str,
        detalhe: dict[str, Any]) -> dict[str, Any]:
    """Cartão-KPI."""
    return {"rotulo": rotulo, "valor": valor, "comparacao": comparacao,
            "pct_barra": pct_barra(indice), "status": st, "fonte": fonte, "detalhe": detalhe}


def detalhe(titulo: str, nota: str, linhas: list[dict[str, str]]) -> dict[str, Any]:
    """Painel de detalhe de um KPI."""
    return {"titulo": titulo, "nota": nota, "linhas": linhas}


def nome_mes(mes: Any) -> str:
    """Nome do mês (1–12)."""
    return MESES[int(mes) - 1]


def ler_atendimento(processed_dir: Path = PROCESSED_DIR) -> pd.DataFrame:
    """Atendimento Social processado."""
    return pd.read_csv(processed_dir / "atendimento.csv", encoding=ENCODING_CSV,
                       dtype={"uf": str}, keep_default_na=False, na_values=[""])


def revisor_do_pdf(escopo: str, outputs_dir: Path = OUTPUTS_DIR) -> str | None:
    """Nome do revisor lido do rodapé do PDF executivo, se o PDF existir."""
    pdf = outputs_dir / f"Executive_Briefing_SENAI_{escopo}.pdf"
    if not pdf.exists():
        return None
    try:
        import pdfplumber
        with pdfplumber.open(pdf) as doc:
            texto = doc.pages[0].extract_text() or ""
    except Exception:  # noqa: BLE001 — PDF ilegível não impede o produto
        logger.warning("Não foi possível ler o revisor em %s", pdf)
        return None
    m = RE_REVISOR.search(" ".join(texto.split()))
    return m.group(1) if m else None


# --------------------------------------------------------------------------
# Contexto (tabelas carregadas uma vez)
# --------------------------------------------------------------------------

class Contexto:
    """Tabelas e derivados usados por todos os escopos."""

    def __init__(self, dados: Dados, atendimento: pd.DataFrame, manifest_path: Path,
                 outputs_dir: Path, figs_dir: Path) -> None:
        self.dados = dados
        self.atendimento = atendimento
        self.validos = farol_valido(dados)
        self.unidades = unidades(dados)
        self.manifest_path = manifest_path
        self.outputs_dir = outputs_dir
        self.figs_dir = figs_dir
        self.cruz = dados["cruzamento_volume_resultado"]
        self.proj = dados["projecao_2026"]

    def pes(self, uf: str, cod: str) -> pd.Series | None:
        """Linha válida do PES 2025 (farol) de uma unidade e indicador."""
        sel = self.validos[(self.validos["uf"] == uf) & (self.validos["cod_indicador"] == cod)]
        return None if sel.empty else sel.iloc[0]

    def atend(self, uf: str, acao: str) -> pd.Series | None:
        """Linha do Atendimento Social 2025 (mês 12) de uma unidade e ação."""
        a = self.atendimento
        sel = a[(a["uf"] == uf) & (a["ano"] == ANO_FECHADO) & (a["mes_referencia"] == 12)
                & (a["acao"] == acao)]
        if acao != ACAO_EMPRESAS:
            sel = sel[sel["linha_negocio"] == LINHA_MATRICULAS]
        return None if sel.empty else sel.iloc[0]

    def mes_projecao(self) -> int:
        """Mês de referência da projeção 2026."""
        return int(self.proj["mes_referencia"].max())


# --------------------------------------------------------------------------
# KPIs comuns
# --------------------------------------------------------------------------

def _texto_volume(l: pd.Series) -> str:
    """"realizado de previsto · índice"."""
    base = f"{fmt_int(l['realizado'])} de {fmt_int(l['previsto'])}"
    return base if pd.isna(l["indice_analise"]) else f"{base} · {fmt_pct(l['indice_analise'])}"


def kpi_matriculas(ctx: Contexto, uf: str, nome: str, acoes: tuple[str, ...]) -> dict[str, Any]:
    """Matrículas (Atendimento Social, TOTAL) e detalhe por ação."""
    total = ctx.atend(uf, ACAO_TOTAL)
    linhas = []
    for acao in acoes:
        l = ctx.atend(uf, acao)
        if l is None or pd.isna(l["previsto"]) or l["previsto"] <= 0:
            continue
        linhas.append(linha(ROTULO_ACAO[acao], _texto_volume(l), status(l["indice_analise"])))
    det = detalhe(f"Volume entregue em {ANO_FECHADO} ({nome})", FONTE_AT, linhas)
    if total is None:
        return kpi("Matrículas (volume)", "sem dado", "sem dado no Atendimento Social",
                   None, SEM_DADO, FONTE_AT, det)
    idx = total["indice_analise"]
    return kpi("Matrículas (volume)", pct_ou_sem_dado(idx),
               f"{fmt_int(total['realizado'])} de {fmt_int(total['previsto'])} previstas",
               idx, status(idx), FONTE_AT, det)


def _comparacao_meta(l: pd.Series, unidade: Any) -> str:
    """"meta X · índice Y"."""
    meta = fmt_publicado(l["meta_txt"], l["meta"], unidade)
    return f"meta {meta} · índice {fmt_pct(l['indice_analise'])}"


# --------------------------------------------------------------------------
# Escopo BR
# --------------------------------------------------------------------------

def kpi_empregabilidade_br(ctx: Contexto) -> dict[str, Any]:
    """Empregabilidade do DN e mediana do realizado por região."""
    u = ctx.unidades.get(COD_EMPREGABILIDADE)
    l = ctx.pes(UF_DN, COD_EMPREGABILIDADE)
    diag = ctx.dados["diag_indicador"].set_index("cod_indicador").loc[COD_EMPREGABILIDADE]
    nota = (f"Entre os DRs: {fmt_int(diag['n_critico'])} críticos, "
            f"{fmt_int(diag['n_atencao'])} em atenção e {fmt_int(diag['n_atingido'])} atingidos.")
    reg = ctx.dados["diag_regiao"]
    reg = reg[reg["cod_indicador"] == COD_EMPREGABILIDADE].sort_values(
        "mediana_realizado", ascending=False)
    meta = None if l is None else l["meta"]
    linhas = [linha(r["regiao"], fmt_valor(r["mediana_realizado"], u),
                    status(r["mediana_realizado"] / meta * 100 if meta else None))
              for _, r in reg.iterrows()]
    det = detalhe("Empregabilidade por região (mediana do realizado)", nota, linhas)
    fonte = FONTE_PES
    paginas = paginas_triangulacao(ctx.dados, COD_EMPREGABILIDADE)
    if paginas:
        fonte += f"; confirmado no Relatório de Gestão, p. {', '.join(paginas)} do PDF"
    if l is None:
        return kpi("Empregabilidade de egressos", "sem dado", "sem dado do DN", None,
                   SEM_DADO, fonte, det)
    return kpi("Empregabilidade de egressos",
               fmt_publicado(l["realizado_txt"], l["realizado"], u), _comparacao_meta(l, u),
               l["indice_analise"], status(l["indice_analise"]), fonte, det)


def volume_sem_resultado(cruz: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(DRs com matrículas >= 100%, destes os com empregabilidade < 80%)."""
    ok = cruz[cruz["indice_matriculas"] >= LIMITE_ATINGIDO]
    return ok, ok[ok["indice_empregabilidade"] < LIMITE_ATENCAO]


def kpi_volume_sem_resultado(ctx: Contexto) -> dict[str, Any]:
    """Contagem do cruzamento e UFs agrupadas por região."""
    ok, crit = volume_sem_resultado(ctx.cruz)
    linhas = [linha(r, ", ".join(sorted(crit.loc[crit["regiao"] == r, "uf"])), "critico")
              for r in REGIOES if (crit["regiao"] == r).any()]
    det = detalhe(f"DRs com matrículas na meta e empregabilidade abaixo de "
                  f"{fmt_pct(LIMITE_ATENCAO, 0)} da meta",
                  "Associação não indica causa; cada caso pede diagnóstico com o DR.", linhas)
    pct = len(crit) / len(ok) * 100 if len(ok) else None
    st = "critico" if len(crit) else "atingido"
    return kpi("DRs com volume sem resultado", f"{fmt_int(len(crit))} de {fmt_int(len(ok))}",
               "matrículas na meta, empregabilidade crítica", pct, st, FONTE_CRUZ, det)


def kpi_consultorias_br(ctx: Contexto) -> dict[str, Any]:
    """Projeção 2026 do DN para consultorias de produtividade."""
    mes = ctx.mes_projecao()
    fonte = f"Fonte: Plano Estratégico Sistêmico {ANO_PROJECAO}, até {nome_mes(mes)}"
    res = ctx.dados["projecao_resumo"].set_index("cod_indicador").loc[COD_CONSULTORIAS]
    dn = ctx.proj[(ctx.proj["uf"] == UF_DN) & (ctx.proj["cod_indicador"] == COD_CONSULTORIAS)]
    u = ctx.unidades.get(COD_CONSULTORIAS)
    linhas = []
    if not dn.empty:
        d = dn.iloc[0]
        linhas.append(linha(f"Realizado até {nome_mes(d['mes_referencia'])}",
                            f"{fmt_publicado(d['realizado_txt'], d['realizado'], u)} de "
                            f"{fmt_publicado(d['meta_txt'], d['meta'], u)}", NEUTRO))
    linhas += [
        linha(f"DRs em {SIT_RISCO}", fmt_int(res["n_risco_alto"]), "critico"),
        linha(f"DRs que {SIT_ACELERACAO}", fmt_int(res["n_exige_aceleracao"]), "atencao"),
        linha(f"DRs {SIT_NO_RITMO}", fmt_int(res["n_no_ritmo"]), "atingido"),
        linha(f"DRs com {SIT_ATINGIDA}", fmt_int(res["n_meta_ja_atingida"]), "atingido"),
    ]
    acel, _ = aceleracao_mediana(ctx.dados, COD_CONSULTORIAS)
    if acel is not None:
        linhas.append(linha("Aceleração mediana necessária",
                            f"{fmt_decimal(acel, 1)} vezes o ritmo atual", NEUTRO))
    det = detalhe(f"Consultorias de produtividade em {ANO_PROJECAO}", NOTA_PROJECAO, linhas)
    idx = res["indice_projetado_dn"]
    return kpi(f"Consultorias de produtividade {ANO_PROJECAO}", pct_ou_sem_dado(idx),
               "projeção para dezembro, no ritmo atual", idx, status(idx), fonte, det)


def mapa(ctx: Contexto) -> list[dict[str, str]]:
    """Índice de empregabilidade por UF (ordem alfabética)."""
    return [{"uf": r["uf"], "nome": NOMES[r["uf"]], "regiao": r["regiao"],
             "valor": pct_ou_sem_dado(r["indice_empregabilidade"]),
             "status": status(r["indice_empregabilidade"])}
            for _, r in ctx.cruz.sort_values("uf").iterrows()]


# --------------------------------------------------------------------------
# Escopo DR
# --------------------------------------------------------------------------

def kpi_empregabilidade_dr(ctx: Contexto, uf: str, regiao: str) -> dict[str, Any]:
    """Empregabilidade do DR e medianas do índice (região e nacional)."""
    nome = NOMES[uf]
    u = ctx.unidades.get(COD_EMPREGABILIDADE)
    l = ctx.pes(uf, COD_EMPREGABILIDADE)
    diag = ctx.dados["diag_indicador"].set_index("cod_indicador")
    reg = ctx.dados["diag_regiao"].set_index(["regiao", "cod_indicador"])
    idx_dr = None if l is None else l["indice_analise"]
    linhas = [linha(nome, pct_ou_sem_dado(idx_dr), status(idx_dr))]
    if (regiao, COD_EMPREGABILIDADE) in reg.index:
        m = reg.loc[(regiao, COD_EMPREGABILIDADE), "mediana_indice"]
        linhas.append(linha(f"Mediana da região {regiao}", pct_ou_sem_dado(m), status(m)))
    if COD_EMPREGABILIDADE in diag.index:
        m = diag.loc[COD_EMPREGABILIDADE, "mediana_indice"]
        linhas.append(linha("Mediana nacional dos DRs", pct_ou_sem_dado(m), status(m)))
    det = detalhe(f"Empregabilidade: {nome} e referências (índice de realização)",
                  "Medianas do índice entre os DRs, para referência; não é ranking.", linhas)
    if l is None:
        return kpi("Empregabilidade de egressos", "sem dado",
                   f"sem dado válido de {ANO_FECHADO}", None, SEM_DADO, FONTE_PES, det)
    return kpi("Empregabilidade de egressos",
               fmt_publicado(l["realizado_txt"], l["realizado"], u), _comparacao_meta(l, u),
               idx_dr, status(idx_dr), FONTE_PES, det)


def status_por_criticos(n_criticos: int) -> str:
    """0 críticos -> "atingido"; 1 a 3 -> "atencao"; 4 ou mais -> "critico"."""
    if n_criticos <= 0:
        return "atingido"
    return "atencao" if n_criticos < CRITICOS_TRILHA else "critico"


def kpi_indicadores_dr(ctx: Contexto, uf: str) -> dict[str, Any]:
    """"x de y" indicadores atingidos e lista do maior para o menor índice."""
    dr = ctx.dados["diag_dr"].set_index("uf").loc[uf]
    ind = ctx.validos[ctx.validos["uf"] == uf].sort_values("indice_analise", ascending=False)
    linhas = [linha(nome_curto(r["cod_indicador"], r["indicador"]), fmt_pct(r["indice_analise"]),
                    status(r["indice_analise"]), r["indicador"])
              for _, r in ind.iterrows()]
    n = int(dr["n_indicadores_com_dado"])
    det = detalhe(f"Os {fmt_int(n)} indicadores de {NOMES[uf]} em {ANO_FECHADO} "
                  "(índice de realização)", f"{NOTA_INDICADORES} {FONTE_PES}.", linhas)
    st = status_por_criticos(int(dr["n_critico"]))
    k = kpi("Indicadores atingidos", f"{fmt_int(dr['n_atingido'])} de {fmt_int(n)}",
            f"{fmt_int(dr['n_critico'])} indicadores em situação crítica",
            dr["pct_atingido"], st, FONTE_PES, det)
    k["rotulo_status"] = ROTULO_STATUS_INDICADORES[st]
    return k


def kpi_risco_dr(ctx: Contexto, uf: str) -> dict[str, Any]:
    """Linha da projeção do DR em risco alto com menor índice projetado."""
    mes = ctx.mes_projecao()
    fonte = f"Fonte: Plano Estratégico Sistêmico {ANO_PROJECAO}, até {nome_mes(mes)}"
    proj = ctx.proj[ctx.proj["uf"] == uf]
    risco = proj[proj["situacao"] == SIT_RISCO].sort_values("indice_projetado")
    rotulo = f"Principal risco {ANO_PROJECAO}"
    if risco.empty:
        linhas = [linha(nome_curto(r["cod_indicador"], r["indicador"]),
                        f"{pct_ou_sem_dado(r['indice_projetado'])} · {r['situacao']}",
                        status(r["indice_projetado"]), r["indicador"])
                  for _, r in proj.sort_values("cod_indicador").iterrows()]
        det = detalhe(f"Projeção {ANO_PROJECAO} de {NOMES[uf]}", NOTA_PROJECAO, linhas)
        return kpi(rotulo, f"sem risco alto em {ANO_PROJECAO}",
                   "nenhum indicador projetado em risco alto", LIMITE_ATINGIDO, "atingido",
                   fonte, det)
    r = risco.iloc[0]
    u = ctx.unidades.get(r["cod_indicador"])
    nome_ind = nome_curto(r["cod_indicador"], r["indicador"])
    linhas = [linha(f"Realizado até {nome_mes(r['mes_referencia'])}",
                    f"{fmt_publicado(r['realizado_txt'], r['realizado'], u)} "
                    f"(meta {fmt_publicado(r['meta_txt'], r['meta'], u)})", NEUTRO)]
    if pd.notna(r["aceleracao_necessaria"]):
        linhas.append(linha("Aceleração necessária",
                            f"{fmt_decimal(r['aceleracao_necessaria'], 1)} vezes o ritmo atual",
                            NEUTRO))
    for _, o in risco.iloc[1:].iterrows():
        linhas.append(linha(nome_curto(o["cod_indicador"], o["indicador"]),
                            f"índice projetado {pct_ou_sem_dado(o['indice_projetado'])}",
                            status(o["indice_projetado"]), o["indicador"]))
    det = detalhe(nome_ind, NOTA_PROJECAO, linhas)
    idx = r["indice_projetado"]
    k = kpi(rotulo, pct_ou_sem_dado(idx),
            f"{nome_ind}: projeção para dezembro, no ritmo atual", idx, status(idx), fonte, det)
    k["nome_oficial"] = r["indicador"]
    return k


# --------------------------------------------------------------------------
# Montagem
# --------------------------------------------------------------------------

def briefing_aprovado(escopo: str, outputs_dir: Path = OUTPUTS_DIR) -> dict[str, Any] | None:
    """Seções do briefing aprovado; ``None`` se não existir ou não estiver aprovado."""
    caminho = outputs_dir / f"briefing_{escopo}.json"
    if not caminho.exists():
        return None
    doc = json.loads(caminho.read_text(encoding="utf-8"))
    if doc.get("status") != "aprovado":
        logger.info("Briefing %s com status %r: omitido", escopo, doc.get("status"))
        return None
    b = doc["briefing"]
    return {
        "mensagem_central": b["mensagem_central"]["texto"],
        "destaques": [{"titulo": d["titulo"], "texto": d["texto"]} for d in b.get("destaques", [])],
        "pontos_atencao": [{"titulo": d["titulo"], "texto": d["texto"]}
                           for d in b.get("pontos_atencao", [])],
        "recomendacoes": [{"titulo": r["titulo"], "texto": r["acao"], "quem": r["responsavel"],
                           "quando": r["horizonte"]} for r in b.get("recomendacoes", [])],
    }


def manchete(ctx: Contexto, escopo: str) -> str:
    """Manchete-conclusão do gráfico volume x resultado."""
    if escopo == ESCOPO_BR:
        _, crit = volume_sem_resultado(ctx.cruz)
        return f"{fmt_int(len(crit))} DRs entregam volume, mas não resultado"
    nome = NOMES[escopo]
    l = ctx.cruz[ctx.cruz["uf"] == escopo]
    if l.empty:
        return f"{nome}: sem dado para o cruzamento volume × resultado"
    leitura = str(l.iloc[0]["leitura"])
    if leitura == "volume ok, resultado crítico":
        return f"{nome} está entre os DRs que entregam volume sem resultado"
    if leitura == "volume ok, resultado ok":
        return f"{nome} entrega volume e resultado"
    return f"{nome}: {leitura}"


def grafico(ctx: Contexto, escopo: str) -> dict[str, str]:
    """Figura volume × resultado (gerada se ausente) e manchete."""
    arquivo = f"volume_resultado_{escopo}.png"
    caminho = ctx.figs_dir / arquivo
    if not caminho.exists():
        destaque = None if escopo == ESCOPO_BR else escopo
        render_pdf.grafico_dispersao(ctx.cruz, caminho, destaque)
        logger.info("Figura gerada: %s", caminho)
    return {"figura": f"figs/{arquivo}", "manchete": manchete(ctx, escopo)}


def montar_meta(ctx: Contexto, escopo: str) -> dict[str, Any]:
    """Metadados do escopo."""
    return {
        "escopo": escopo,
        "nome": NOMES[escopo],
        "mes_referencia": f"{nome_mes(ctx.mes_projecao())} de {ANO_PROJECAO}",
        "data_coleta": render_pdf.coleta_mais_recente(ctx.manifest_path),
        "revisado_por": revisor_do_pdf(escopo, ctx.outputs_dir),
        "tem_pdf": (ctx.outputs_dir / f"Executive_Briefing_SENAI_{escopo}.pdf").exists(),
    }


def montar_escopo(ctx: Contexto, escopo: str) -> dict[str, Any]:
    """Documento completo de um escopo (BR ou sigla do DR)."""
    if escopo not in ESCOPOS:
        raise ValueError(f"Escopo inválido: {escopo!r}")
    doc: dict[str, Any] = {"meta": montar_meta(ctx, escopo),
                           "briefing": briefing_aprovado(escopo, ctx.outputs_dir)}
    if escopo == ESCOPO_BR:
        doc["kpis"] = [kpi_matriculas(ctx, UF_DN, NOMES[escopo], ACOES_BR),
                       kpi_empregabilidade_br(ctx), kpi_volume_sem_resultado(ctx),
                       kpi_consultorias_br(ctx)]
        doc["mapa"] = mapa(ctx)
    else:
        regiao = str(ctx.dados["diag_dr"].set_index("uf").loc[escopo, "regiao"])
        doc["kpis"] = [kpi_matriculas(ctx, escopo, NOMES[escopo], ACOES_DR),
                       kpi_empregabilidade_dr(ctx, escopo, regiao),
                       kpi_indicadores_dr(ctx, escopo), kpi_risco_dr(ctx, escopo)]
        doc["mapa"] = None
    doc["grafico"] = grafico(ctx, escopo)
    return doc


def numero(rotulo: str, valor: str, nota: str) -> dict[str, str]:
    """Um dos 3 números da região na visão do mapa."""
    return {"rotulo": rotulo, "valor": valor, "nota": nota}


def titulo_regiao(regiao: str, n_criticos: int, n_com_dado: int) -> str:
    """Manchete da região (empregabilidade)."""
    if n_criticos == 0:
        return f"{regiao}: nenhum DR crítico em empregabilidade"
    if n_criticos == n_com_dado:
        return f"{regiao}: todos os {fmt_int(n_com_dado)} DRs com dado estão críticos em empregabilidade"
    return (f"{regiao}: {fmt_int(n_criticos)} de {fmt_int(n_com_dado)} DRs com dado estão "
            "críticos em empregabilidade")


def titulo_brasil(criticos_por_regiao: dict[str, int]) -> str:
    """Manchete nacional; cita as duas regiões com mais críticos se somarem mais da metade."""
    total = sum(criticos_por_regiao.values())
    if total == 0:
        return "Nenhum DR crítico em empregabilidade"
    base = f"Empregabilidade crítica em {fmt_int(total)} DRs"
    top = sorted(criticos_por_regiao, key=lambda r: -criticos_por_regiao[r])[:2]
    if len(top) == 2 and criticos_por_regiao[top[1]] and             criticos_por_regiao[top[0]] + criticos_por_regiao[top[1]] > total / 2:
        return f"{base}, concentrados no {top[0]} e no {top[1]}"
    return base


def montar_regioes(ctx: Contexto) -> dict[str, Any]:
    """Resumo por região (e Brasil) para a visão do mapa.

    Traz textos prontos (título, 3 números, lista de DRs) para a página só exibir.
    """
    u = ctx.unidades.get(COD_EMPREGABILIDADE)
    reg = ctx.dados["diag_regiao"]
    reg = reg[reg["cod_indicador"] == COD_EMPREGABILIDADE].set_index("regiao")
    dr = ctx.dados["diag_dr"].set_index("uf")
    cruz = ctx.cruz.set_index("uf")
    dn = ctx.pes(UF_DN, COD_EMPREGABILIDADE)
    meta_nac = "sem dado" if dn is None else fmt_publicado(dn["meta_txt"], dn["meta"], u)
    saida = []
    criticos: list[dict[str, str]] = []
    criticos_por_regiao: dict[str, int] = {}
    for r in REGIOES:
        ufs = sorted(cruz.index[cruz["regiao"] == r])
        drs = []
        for uf in ufs:
            idx = cruz.loc[uf, "indice_empregabilidade"]
            d = dr.loc[uf]
            drs.append({"uf": uf, "nome": NOMES[uf], "empregabilidade": pct_ou_sem_dado(idx),
                        "status": status(idx),
                        "atingidos": f"{fmt_int(d['n_atingido'])} de "
                                     f"{fmt_int(d['n_indicadores_com_dado'])}",
                        "criticos": f"{fmt_int(d['n_critico'])} críticos"})
        crit_reg = [x for x in drs if x["status"] == "critico"]
        sem = [x["uf"] for x in drs if x["status"] == SEM_DADO]
        criticos += crit_reg
        criticos_por_regiao[r] = len(crit_reg)
        n_com_dado = len(drs) - len(sem)
        mediana_mat = cruz.loc[ufs, "indice_matriculas"].median()
        linha_reg = reg.loc[r] if r in reg.index else None
        emp_med = ("sem dado" if linha_reg is None
                   else fmt_valor(linha_reg["mediana_realizado"], u))
        crit_txt = ("sem dado" if linha_reg is None
                    else f"{fmt_int(linha_reg['n_criticos'])} de {fmt_int(linha_reg['n_drs'])}")
        nota_crit = "com dado" + (f"; {', '.join(sem)} sem dado" if sem else "")
        saida.append({
            "regiao": r,
            "titulo": titulo_regiao(r, len(crit_reg), n_com_dado),
            "empregabilidade_mediana": emp_med,
            "criticos_empregabilidade": crit_txt,
            "matriculas_mediana": pct_ou_sem_dado(mediana_mat),
            "numeros": [
                numero("Empregabilidade (mediana)", emp_med, f"meta nacional {meta_nac}"),
                numero("DRs críticos em empregabilidade", crit_txt, nota_crit),
                numero("Matrículas (mediana)", pct_ou_sem_dado(mediana_mat), "da meta"),
            ],
            "drs": drs,
        })
    diag = ctx.dados["diag_indicador"].set_index("cod_indicador").loc[COD_EMPREGABILIDADE]
    mat = ctx.atend(UF_DN, ACAO_TOTAL)
    emp_br = "sem dado" if dn is None else fmt_publicado(dn["realizado_txt"], dn["realizado"], u)
    crit_br = f"{fmt_int(diag['n_critico'])} de {fmt_int(diag['n_drs_com_dado'])}"
    mat_br = "sem dado" if mat is None else pct_ou_sem_dado(mat["indice_analise"])
    criticos.sort(key=lambda x: x["uf"])
    brasil = {
        "titulo": titulo_brasil(criticos_por_regiao),
        "lista_titulo": f"Os {fmt_int(len(criticos))} DRs críticos em empregabilidade "
                        "(clique para abrir o briefing)",
        "empregabilidade": emp_br,
        "meta_empregabilidade": meta_nac,
        "criticos_empregabilidade": crit_br,
        "matriculas": mat_br,
        "numeros": [
            numero("Empregabilidade (Brasil)", emp_br, f"meta {meta_nac}"),
            numero("DRs críticos em empregabilidade", crit_br, f"com dado em {ANO_FECHADO}"),
            numero("Matrículas (Brasil)", mat_br, "da meta"),
        ],
        "drs": criticos,
    }
    return {"brasil": brasil, "regioes": saida}


def _gravar(doc: dict[str, Any], destino: Path) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=_json_default),
                       encoding="utf-8")
    return destino


def _json_default(obj: Any) -> Any:
    """Converte tipos numpy para JSON."""
    if hasattr(obj, "item"):
        return obj.item()
    raise TypeError(f"Tipo não serializável: {type(obj)}")


def gerar_tudo(processed_dir: Path = PROCESSED_DIR, manifest_path: Path = MANIFEST_PATH,
               outputs_dir: Path = OUTPUTS_DIR, app_data_dir: Path = APP_DATA_DIR,
               figs_dir: Path = FIGS_DIR) -> list[Path]:
    """Gera os 28 JSON de escopo e ``regioes.json``; devolve os caminhos gravados."""
    ctx = Contexto(carregar_dados(processed_dir, manifest_path), ler_atendimento(processed_dir),
                   manifest_path, outputs_dir, figs_dir)
    gravados = [_gravar(montar_escopo(ctx, e), app_data_dir / f"{e}.json") for e in ESCOPOS]
    gravados.append(_gravar(montar_regioes(ctx), app_data_dir / "regioes.json"))
    return gravados


def main() -> int:
    """CLI: python -m src.produto."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    gravados = gerar_tudo()
    logger.info("%d arquivos gravados em %s", len(gravados), APP_DATA_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
