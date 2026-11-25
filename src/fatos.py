"""Ficha de fatos: transforma as tabelas de ``data/processed`` em fatos citáveis.

Gera ``outputs/fatos_{escopo}.json`` (escopo = "BR" ou sigla do DR). Cada fato:

    {"id": "F001", "categoria": ..., "titulo": ..., "frase": ...,
     "numeros_permitidos": [...], "fonte": {...}}

Regras contra alucinação (SPEC, seção 6):
- As frases são templates preenchidos só com valores lidos dos CSVs. O texto
  fixo de um template não pode conter dígitos (``montar`` rejeita): todo número
  entra por um campo e vai para ``numeros_permitidos`` exatamente como aparece
  na frase.
- Números no padrão brasileiro: 39,8%; 7.476; R$ 1,06 bi.
- Sem ranking: listas de DRs sempre em ordem alfabética de UF.

Uso:
    python -m src.fatos --escopo BR
    python -m src.fatos --escopo AC
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.formato import (  # noqa: F401  (reexportadas: API pública de src.fatos)
    arredondar,
    eh_monetario,
    eh_percentual,
    fmt_decimal,
    fmt_int,
    fmt_moeda,
    fmt_num,
    fmt_pct,
    fmt_valor,
    fmt_vezes,
)
from src.diagnostico import aplicar_achados, chave_cod, farol, filtrar_validos, spearman
from src.projecao import SIT_ACELERACAO, SIT_RISCO
from src.qualidade import so_polaridade_positiva

logger = logging.getLogger("fatos")

RAIZ = Path(__file__).resolve().parents[1]
PROCESSED_DIR = RAIZ / "data" / "processed"
RAW_DIR = RAIZ / "data" / "raw"
OUTPUTS_DIR = RAIZ / "outputs"
MANIFEST_PATH = RAW_DIR / "manifest.json"
PDF_CHAVE_MANIFEST = "relatorio/relatorio_gestao_2025.pdf"
ENCODING_CSV = "utf-8-sig"

ESCOPO_BR = "BR"
UF_DN = "DN"
TIPO_DR = "DR"
ANO_FECHADO = 2025
ANO_PROJECAO = 2026
COD_EMPREGABILIDADE = "1"
LIMITE_ATINGIDO = 100.0
LIMITE_ATENCAO = 80.0
MIN_CRITICOS_APOIO = 4
REGRAS_DESTAQUE = ("Q01", "Q02", "Q08", "Q12", "Q13")
STATUS_ZERO_SUSPEITO = "zero_suspeito"
SIT_INCOMPLETO = "consolidado incompleto"

TEXTO_FAROL = {"atingido": "atingido", "atencao": "atenção", "critico": "crítico"}
PREFIXO_ATENDIMENTO = "AT-"  # códigos internos da projeção para o Atendimento Social
FONTES_Q13 = {"pes": "Plano Estratégico (PES)", "atendimento": "Atendimento Social"}
CAMPOS_Q12 = {"meta": "meta", "realizado": "realizado", "previsto": "previsto"}
# Evidência gerada por src.qualidade._consolidacao (Q12).
RE_EVIDENCIA_Q12 = re.compile(
    r"^(?P<campo>\w+): DN=(?P<dn>-?\d+(?:\.\d+)?); soma DRs\+CETIQT=(?P<soma>-?\d+(?:\.\d+)?) "
    r"\((?P<k>\d+) de (?P<n>\d+) unidades com dado\); diferença=(?P<dif>[+-]?\d+(?:\.\d+)?)%"
    r"(?P<nota>.*)$"
)

TABELAS = (
    "pes", "diag_indicador", "diag_dr", "diag_regiao", "cruzamento_volume_resultado",
    "projecao_2026", "projecao_resumo", "qualidade_resumo", "qualidade_achados",
    "triangulacao_relatorio",
)

# Token numérico: não pode estar colado a letra/dígito à esquerda (evita "Q01", "F001").
RE_NUMERO = re.compile(r"(?<![\w.,])-?\d+(?:[.,]\d+)*%?")


# --------------------------------------------------------------------------
# Formatação pt-BR
# --------------------------------------------------------------------------

def fmt_publicado(texto: Any, valor: float, unidade: Any) -> str:
    """Valor publicado: texto original (espaços normalizados) ou moeda compacta.

    Para unidade monetária usa ``fmt_moeda`` (o texto do portal tem centavos e
    espaços); nos demais casos preserva o texto publicado, já em pt-BR.
    """
    if eh_monetario(unidade) or not isinstance(texto, str) or not texto.strip():
        return fmt_valor(valor, unidade)
    return " ".join(texto.split())


def extrair_numeros(texto: str) -> list[str]:
    """Tokens numéricos de um texto (mesma regra usada no teste e no validador)."""
    return RE_NUMERO.findall(texto)


# --------------------------------------------------------------------------
# Montagem de frases
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Num:
    """Valor numérico já formatado: entra inteiro em ``numeros_permitidos``."""

    texto: str


def montar(template: str, **campos: Num | str) -> tuple[str, list[str]]:
    """Preenche um template e devolve (frase, numeros_permitidos).

    - O texto fixo do template não pode ter dígitos (ValueError): todo número
      deve vir de um campo.
    - ``Num``: o texto formatado inteiro é permitido.
    - ``str`` (texto vindo de CSV, ex. nome de indicador): os tokens numéricos
      que ele contiver são permitidos.
    """
    fixo = re.sub(r"\{[^{}]*\}", "", template)
    if re.search(r"\d", fixo):
        raise ValueError(f"Template com número fixo: {template!r}")
    valores: dict[str, str] = {}
    numeros: list[str] = []
    for nome, campo in campos.items():
        if isinstance(campo, Num):
            valores[nome] = campo.texto
            if re.search(r"\d", campo.texto):
                numeros.append(campo.texto)
        else:
            valores[nome] = str(campo)
            numeros.extend(extrair_numeros(str(campo)))
    frase = template.format(**valores)
    return frase, list(dict.fromkeys(numeros))


def juntar(frases: list[tuple[str, list[str]]], sep: str = "; ") -> tuple[str, list[str]]:
    """Concatena frases montadas, unindo os números permitidos."""
    texto = sep.join(f for f, _ in frases)
    numeros = [n for _, ns in frases for n in ns]
    return texto, list(dict.fromkeys(numeros))


def lista_ufs(ufs: pd.Series | list[str]) -> str:
    """Lista de UFs em ordem alfabética (sem ranking)."""
    return ", ".join(sorted(str(u) for u in ufs)) or "nenhum"


# --------------------------------------------------------------------------
# Dados de entrada
# --------------------------------------------------------------------------

@dataclass
class Dados:
    """Tabelas de entrada e metadados de origem."""

    tabelas: dict[str, pd.DataFrame]
    sha256_csv: dict[str, str] = field(default_factory=dict)
    sha256_pdf: str | None = None

    def __getitem__(self, nome: str) -> pd.DataFrame:
        return self.tabelas[nome]


def sha256_arquivo(caminho: Path) -> str:
    """SHA-256 de um arquivo, lido em blocos."""
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def carregar_dados(processed_dir: Path = PROCESSED_DIR,
                   manifest_path: Path = MANIFEST_PATH) -> Dados:
    """Lê as tabelas de ``data/processed`` e o hash do PDF no manifest."""
    tabelas: dict[str, pd.DataFrame] = {}
    hashes: dict[str, str] = {}
    for nome in TABELAS:
        caminho = processed_dir / f"{nome}.csv"
        if not caminho.exists():
            raise FileNotFoundError(f"{caminho} não encontrado; rode as etapas anteriores do pipeline")
        tabelas[nome] = pd.read_csv(
            caminho, encoding=ENCODING_CSV,
            dtype={"cod_indicador": str, "item": str, "uf": str, "paginas": str},
            keep_default_na=False, na_values=[""],
        )
        hashes[nome] = sha256_arquivo(caminho)
    sha_pdf = None
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sha_pdf = manifest.get(PDF_CHAVE_MANIFEST, {}).get("sha256")
    return Dados(tabelas, hashes, sha_pdf)


# --------------------------------------------------------------------------
# Fontes
# --------------------------------------------------------------------------

def fonte_csv(dados: Dados, nome: str, tipo: str = "api", **extra: Any) -> dict[str, Any]:
    """Fonte = tabela derivada de ``data/processed`` (com hash do CSV)."""
    fonte: dict[str, Any] = {"tipo": tipo, "arquivo": f"data/processed/{nome}.csv"}
    fonte.update({k: v for k, v in extra.items() if v is not None})
    if nome in dados.sha256_csv:
        fonte["sha256"] = dados.sha256_csv[nome]
    return fonte


def fonte_linha_api(linha: pd.Series, **extra: Any) -> dict[str, Any]:
    """Fonte = arquivo bruto da API de onde veio a linha (com seu hash)."""
    fonte: dict[str, Any] = {
        "tipo": "api",
        "arquivo": f"data/raw/{linha['arquivo_origem']}",
        "tabela": "data/processed/pes.csv",
    }
    if isinstance(linha.get("sha256_origem"), str):
        fonte["sha256"] = linha["sha256_origem"]
    fonte.update({k: v for k, v in extra.items() if v is not None})
    return fonte


def fonte_pdf(dados: Dados, paginas: list[str]) -> dict[str, Any]:
    """Fonte = Relatório de Gestão DN (PDF) e páginas."""
    fonte: dict[str, Any] = {"tipo": "pdf", "arquivo": f"data/raw/{PDF_CHAVE_MANIFEST}",
                             "pagina_pdf": paginas}
    if dados.sha256_pdf:
        fonte["sha256"] = dados.sha256_pdf
    return fonte


def _encontrado(tri: pd.DataFrame) -> pd.Series:
    """Máscara booleana da coluna ``encontrado`` (aceita bool ou texto "True")."""
    return tri["encontrado"].astype(str).str.strip().str.lower() == "true"


def paginas_triangulacao(dados: Dados, cod: str) -> list[str] | None:
    """Páginas do PDF onde o realizado nacional do indicador foi encontrado."""
    tri = dados["triangulacao_relatorio"]
    linha = tri[(tri["cod_indicador"] == cod) & _encontrado(tri)]
    if linha.empty or pd.isna(linha.iloc[0]["paginas"]):
        return None
    return str(linha.iloc[0]["paginas"]).split(";")


# --------------------------------------------------------------------------
# Construtor de fatos
# --------------------------------------------------------------------------

class Ficha:
    """Acumula fatos e atribui IDs sequenciais (F001, F002...)."""

    def __init__(self) -> None:
        self.fatos: list[dict[str, Any]] = []

    def add(self, categoria: str, titulo: str, montado: tuple[str, list[str]],
            fonte: dict[str, Any], **extra: Any) -> None:
        """Registra um fato."""
        frase, numeros = montado
        fato = {"id": f"F{len(self.fatos) + 1:03d}", "categoria": categoria, "titulo": titulo,
                "frase": frase, "numeros_permitidos": numeros, "fonte": fonte}
        fato.update(extra)
        self.fatos.append(fato)


def _ordenar_cod(df: pd.DataFrame) -> pd.DataFrame:
    """Ordena por código de indicador (ordem natural; códigos não numéricos ao fim)."""
    def chave(cod: str) -> tuple[int, Any]:
        try:
            return (0, chave_cod(cod))
        except ValueError:
            return (1, cod)
    return df.iloc[sorted(range(len(df)), key=lambda i: chave(df.iloc[i]["cod_indicador"]))]


def farol_valido(dados: Dados) -> pd.DataFrame:
    """Linhas do PES 2025 que entram no farol (mesmos critérios do diagnóstico)."""
    validos = filtrar_validos(dados["pes"], ANO_FECHADO, "meta")
    return aplicar_achados(validos, dados["qualidade_achados"])


def unidades(dados: Dados) -> dict[str, str]:
    """Unidade de medida por código de indicador."""
    pes = dados["pes"]
    return dict(zip(pes["cod_indicador"], pes["unidade"]))


# --------------------------------------------------------------------------
# Blocos — escopo BR
# --------------------------------------------------------------------------

def _frase_resultado(linha: pd.Series) -> tuple[str, list[str]]:
    """Meta, realizado, índice e farol de uma linha do PES."""
    return montar(
        "Indicador {cod} ({nome}), {ano}: meta {meta}, realizado {real}, "
        "índice {idx}; farol {farol}.",
        cod=Num(linha["cod_indicador"]), nome=linha["indicador"], ano=Num(str(linha["ano"])),
        meta=Num(fmt_publicado(linha["meta_txt"], linha["meta"], linha["unidade"])),
        real=Num(fmt_publicado(linha["realizado_txt"], linha["realizado"], linha["unidade"])),
        idx=Num(fmt_pct(linha["indice_analise"])),
        farol=TEXTO_FAROL[farol(linha["indice_analise"])],
    )


def bloco_resultado_nacional(ficha: Ficha, dados: Dados, validos: pd.DataFrame) -> None:
    """Um fato por indicador do DN 2025 com farol."""
    dn = _ordenar_cod(validos[validos["uf"] == UF_DN])
    for _, linha in dn.iterrows():
        paginas = paginas_triangulacao(dados, linha["cod_indicador"])
        ficha.add("resultado_nacional", f"Resultado nacional — indicador {linha['cod_indicador']}",
                  _frase_resultado(linha), fonte_linha_api(linha, pagina_pdf=paginas),
                  cod_indicador=linha["cod_indicador"])


def bloco_assimetria(ficha: Ficha, dados: Dados) -> None:
    """Distribuição do farol entre DRs por indicador e mediana regional do indicador 1."""
    diag = _ordenar_cod(dados["diag_indicador"])
    for _, l in diag.iterrows():
        montado = montar(
            "Indicador {cod} ({nome}): dos {n} DRs com dado, {c} críticos, {a} em atenção "
            "e {t} atingidos.",
            cod=Num(l["cod_indicador"]), nome=l["indicador"], n=Num(fmt_int(l["n_drs_com_dado"])),
            c=Num(fmt_int(l["n_critico"])), a=Num(fmt_int(l["n_atencao"])),
            t=Num(fmt_int(l["n_atingido"])),
        )
        ficha.add("assimetria", f"Farol entre DRs — indicador {l['cod_indicador']}", montado,
                  fonte_csv(dados, "diag_indicador"), cod_indicador=l["cod_indicador"])

    reg = dados["diag_regiao"]
    reg1 = reg[reg["cod_indicador"] == COD_EMPREGABILIDADE].sort_values("regiao")
    if reg1.empty:
        return
    unidade = unidades(dados).get(COD_EMPREGABILIDADE)
    partes = [montar("{r}: {m} ({n} DRs)", r=l["regiao"],
                     m=Num(fmt_valor(l["mediana_realizado"], unidade)),
                     n=Num(fmt_int(l["n_drs"])))
              for _, l in reg1.iterrows()]
    cab = montar("Mediana do realizado do indicador {cod} ({nome}) por região: ",
                 cod=Num(COD_EMPREGABILIDADE), nome=reg1.iloc[0]["indicador"])
    corpo = juntar(partes)
    ficha.add("assimetria", f"Mediana regional — indicador {COD_EMPREGABILIDADE}",
              juntar([cab, (corpo[0] + ".", corpo[1])], sep=""),
              fonte_csv(dados, "diag_regiao"), cod_indicador=COD_EMPREGABILIDADE)


def bloco_cruzamento(ficha: Ficha, dados: Dados) -> None:
    """Volume (matrículas) x resultado (empregabilidade) e correlação de Spearman."""
    cruz = dados["cruzamento_volume_resultado"]
    volume_ok = cruz[cruz["indice_matriculas"] >= LIMITE_ATINGIDO]
    criticos = volume_ok[volume_ok["indice_empregabilidade"] < LIMITE_ATENCAO]
    montado = montar(
        "{n} DRs têm índice de matrículas maior ou igual a {lim}; destes, {c} têm "
        "índice de empregabilidade abaixo de {lim_c} ({ufs}).",
        n=Num(fmt_int(len(volume_ok))), lim=Num(fmt_pct(LIMITE_ATINGIDO, 0)),
        c=Num(fmt_int(len(criticos))), lim_c=Num(fmt_pct(LIMITE_ATENCAO, 0)),
        ufs=lista_ufs(criticos["uf"]),
    )
    ficha.add("cruzamento", "Volume alto, resultado crítico", montado,
              fonte_csv(dados, "cruzamento_volume_resultado"),
              ufs=sorted(criticos["uf"].tolist()))

    rho, n = spearman(cruz["indice_matriculas"], cruz["indice_empregabilidade"])
    if rho is None:
        logger.warning("Spearman sem pares suficientes (n=%d); fato omitido", n)
        return
    montado = montar(
        "Correlação de Spearman entre o índice de matrículas e o índice de empregabilidade: "
        "{rho} ({n} DRs). Correlação não indica causa.",
        rho=Num(fmt_decimal(rho)), n=Num(fmt_int(n)),
    )
    ficha.add("cruzamento", "Correlação volume x resultado", montado,
              fonte_csv(dados, "cruzamento_volume_resultado", calculo="spearman"))


def _frase_apoio_dr(l: pd.Series) -> tuple[str, list[str]]:
    """Frase de um DR do grupo de apoio."""
    return montar(
        "{uf} ({reg}): {c} indicadores críticos; {resumo}. Críticos: {lista}.",
        uf=l["uf"], reg=l["regiao"], c=Num(fmt_int(l["n_critico"])), resumo=l["resumo"],
        lista=l["indicadores_criticos"],
    )


def bloco_apoio(ficha: Ficha, dados: Dados) -> None:
    """DRs com 4 ou mais indicadores críticos (grupo de apoio, ordem alfabética)."""
    dr = dados["diag_dr"]
    grupo = dr[dr["n_critico"] >= MIN_CRITICOS_APOIO].sort_values("uf")
    montado = montar(
        "{n} de {total} DRs têm {lim} ou mais indicadores críticos ({ufs}).",
        n=Num(fmt_int(len(grupo))), total=Num(fmt_int(len(dr))),
        lim=Num(fmt_int(MIN_CRITICOS_APOIO)), ufs=lista_ufs(grupo["uf"]),
    )
    ficha.add("apoio", "Grupo de apoio", montado, fonte_csv(dados, "diag_dr"),
              ufs=grupo["uf"].tolist())
    for _, l in grupo.iterrows():
        ficha.add("apoio", f"Grupo de apoio — {l['uf']}", _frase_apoio_dr(l),
                  fonte_csv(dados, "diag_dr"), uf=l["uf"])


def metodo_projecao(dados: Dados) -> str:
    """Texto do método de projeção (coluna ``metodo`` de projecao_2026.csv)."""
    metodos = dados["projecao_2026"]["metodo"].dropna().unique()
    if len(metodos) != 1:
        logger.warning("Métodos de projeção diferentes: %s", list(metodos))
    return "; ".join(str(m) for m in metodos)


def rotulo_indicador(cod: str, nome: str) -> tuple[str, list[str]]:
    """Rótulo executivo: "indicador 2.1 (nome)" ou "Atendimento Social: nome" (sem código interno)."""
    if str(cod).startswith(PREFIXO_ATENDIMENTO):
        return montar("Atendimento Social: {nome}", nome=nome)
    return montar("indicador {cod} ({nome})", cod=Num(str(cod)), nome=nome)


def aceleracao_mediana(dados: Dados, cod: str) -> tuple[float | None, int]:
    """Mediana de ``aceleracao_necessaria`` entre DRs em risco alto ou que exigem aceleração."""
    p = dados["projecao_2026"]
    sel = p[(p["cod_indicador"] == cod) & (p["tipo_unidade"] == TIPO_DR)
            & p["situacao"].isin([SIT_RISCO, SIT_ACELERACAO])]["aceleracao_necessaria"].dropna()
    return (float(sel.median()), len(sel)) if len(sel) else (None, 0)


def bloco_projecao_nacional(ficha: Ficha, dados: Dados) -> None:
    """Contagem por situação, aceleração mediana e situação do DN, por indicador projetado."""
    metodo = metodo_projecao(dados)
    mes = dados["projecao_2026"]["mes_referencia"].max()
    for _, l in _ordenar_cod(dados["projecao_resumo"]).iterrows():
        rotulo = rotulo_indicador(l["cod_indicador"], l["indicador"])
        partes = [juntar([montar("Projeção {ano} — ", ano=Num(str(ANO_PROJECAO))), rotulo,
                          montar(", com dados até o mês {mes}", mes=Num(str(mes)))], sep=""),
                  montar(
            "base de {n} DRs; meta já atingida: {at}; no ritmo: {nr}; exige aceleração: {ea}; "
            "risco alto: {ra}",
            n=Num(fmt_int(l["n_drs"])),
            at=Num(fmt_int(l["n_meta_ja_atingida"])), nr=Num(fmt_int(l["n_no_ritmo"])),
            ea=Num(fmt_int(l["n_exige_aceleracao"])), ra=Num(fmt_int(l["n_risco_alto"])),
        )]
        acel, n_acel = aceleracao_mediana(dados, l["cod_indicador"])
        if acel is not None:
            partes.append(montar(
                "aceleração mediana necessária de {a} o ritmo atual (entre os {k} DRs em risco "
                "alto ou que exigem aceleração)",
                a=Num(fmt_vezes(acel)), k=Num(fmt_int(n_acel))))
        if l["n_drs_zero_suspeito"] > 0:
            partes.append(montar("DRs fora da base por realizado zero suspeito: {z}",
                                 z=Num(fmt_int(l["n_drs_zero_suspeito"]))))
        if l["situacao_dn"] == SIT_INCOMPLETO or pd.isna(l["indice_projetado_dn"]):
            partes.append(montar("situação do DN: {s}", s=l["situacao_dn"]))
        else:
            partes.append(montar("situação do DN: {s} (índice projetado {i})",
                                 s=l["situacao_dn"], i=Num(fmt_pct(l["indice_projetado_dn"]))))
        partes.append(montar("método: {m}.", m=metodo))
        ficha.add("projecao_2026", f"Projeção {ANO_PROJECAO} — {rotulo[0]}",
                  juntar(partes),
                  {**fonte_csv(dados, "projecao_resumo"),
                   "arquivos_complementares": ["data/processed/projecao_2026.csv"]},
                  cod_indicador=l["cod_indicador"])


def _achados_regra(dados: Dados, regra: str, uf: str | None = None) -> pd.DataFrame:
    """Achados de uma regra (opcionalmente de uma UF)."""
    a = dados["qualidade_achados"]
    a = a[a["regra_id"] == regra]
    return a if uf is None else a[a["uf"] == uf]


def _linha_pes(dados: Dados, uf: str, cod: str, ano: int) -> pd.Series | None:
    """Linha do PES (ano fechado no mês 12, ou a mais recente do ano) de uma unidade e indicador."""
    pes = dados["pes"]
    sel = pes[(pes["uf"] == uf) & (pes["cod_indicador"] == cod) & (pes["ano"] == ano)]
    return None if sel.empty else sel.sort_values("mes_referencia").iloc[-1]


def _achado_q02(dados: Dados, a: pd.Series) -> tuple[str, list[str]] | None:
    """Q02 em linguagem executiva, com os valores publicados na linha do PES."""
    linha = _linha_pes(dados, str(a["uf"]), str(a["item"]), int(a["ano"]))
    if linha is None:
        return None
    u = linha["unidade"]
    return montar(
        "caso: {uf}, indicador {cod} ({ano}), indicador em percentual com realizado de {real} "
        "(meta {meta}; índice {idx}), valor impossível para uma proporção e por isso fora do farol",
        uf=a["uf"], cod=Num(str(a["item"])), ano=Num(str(a["ano"])),
        real=Num(fmt_publicado(linha["realizado_txt"], linha["realizado"], u)),
        meta=Num(fmt_publicado(linha["meta_txt"], linha["meta"], u)),
        idx=Num(fmt_publicado(linha["indice_txt"], linha["indice_publicado"], "%")),
    )


def _achado_q12(dados: Dados, a: pd.Series) -> tuple[str, list[str]] | None:
    """Q12 em linguagem executiva, a partir da evidência estruturada de src.qualidade."""
    m = RE_EVIDENCIA_Q12.match(str(a["evidencia"]))
    if m is None:
        logger.warning("Evidência da Q12 em formato inesperado: %s", a["evidencia"])
        return None
    unidade = unidades(dados).get(str(a["item"])) if a["fonte"] == "pes" else None
    periodo = (montar("{ano}, até o mês {mes}", ano=Num(str(a["ano"])), mes=Num(str(a["mes_referencia"])))
               if str(a["periodo_parcial"]).lower() == "true"
               else montar("{ano}", ano=Num(str(a["ano"]))))
    item = (montar("indicador {cod}", cod=Num(str(a["item"]))) if a["fonte"] == "pes"
            else montar("{acao}", acao=str(a["item"]).capitalize()))
    corpo = montar(
        "{campo} nacional (DN) de {dn} contra {soma} na soma dos DRs e do SENAI CETIQT "
        "({k} de {n} unidades com dado), diferença de {dif}",
        campo=CAMPOS_Q12.get(m["campo"], m["campo"]).capitalize(),
        dn=Num(fmt_valor(float(m["dn"]), unidade)), soma=Num(fmt_valor(float(m["soma"]), unidade)),
        k=Num(m["k"]), n=Num(m["n"]), dif=Num(fmt_pct(float(m["dif"]))),
    )
    partes = [montar("caso: "), item, montar(" ("), periodo, montar("): "), corpo]
    if m["nota"].strip():
        partes.append(montar(" (o total nacional é deduplicado, então ficar abaixo da soma é esperado)"))
    return juntar(partes, sep="")


def descrever_achado(dados: Dados, a: pd.Series) -> tuple[str, list[str]] | None:
    """Frase executiva de um achado de destaque (Q02, Q12); ``None`` se não houver template."""
    descritores = {"Q02": _achado_q02, "Q12": _achado_q12}
    func = descritores.get(str(a["regra_id"]))
    return None if func is None else func(dados, a)


def bloco_qualidade_nacional(ficha: Ficha, dados: Dados) -> None:
    """Um fato por regra de qualidade com ocorrência."""
    res = dados["qualidade_resumo"]
    for _, l in res[res["n_ocorrencias"] > 0].sort_values("regra_id").iterrows():
        destaque = l["regra_id"] in REGRAS_DESTAQUE
        partes = [montar(
            "Regra {r} ({nome}, gravidade {g}): ocorrências: {n}; unidades afetadas: {d}",
            r=l["regra_id"], nome=l["nome"], g=l["gravidade"],
            n=Num(fmt_int(l["n_ocorrencias"])), d=Num(fmt_int(l["n_drs_afetados"])),
        )]
        achados = _achados_regra(dados, l["regra_id"])
        if destaque and len(achados) == 1:
            frase_achado = descrever_achado(dados, achados.iloc[0])
            if frase_achado is not None:
                partes.append(frase_achado)
        if destaque and not achados.empty:
            partes.append(montar("recomendação: {rec}", rec=achados.iloc[0]["recomendacao"]))
        frase, nums = juntar(partes)
        ficha.add("qualidade", f"Qualidade — {l['regra_id']}", (frase.rstrip(".") + ".", nums),
                  fonte_csv(dados, "qualidade_resumo", tipo="regra", regra_id=l["regra_id"]),
                  destaque=destaque)


def metas_abaixo_realizado(dados: Dados, uf: str) -> pd.DataFrame:
    """Indicadores do PES em que a meta de 2026 é menor que o realizado de 2025.

    Mesmo critério da regra Q13 (src.qualidade): realizado de 2025 fechado (mês 12)
    e meta de 2026, ambos > 0, só polaridade "(+) Maior, melhor" (outras: warning e
    linha ignorada). Colunas ``*_ant`` = linha de 2025.
    """
    pes = dados["pes"]
    pes = so_polaridade_positiva(pes[pes["uf"] == uf], f"calibracao {uf}")
    atual = pes[(pes["ano"] == ANO_PROJECAO) & (pes["meta"] > 0)]
    atual = atual.sort_values("mes_referencia").drop_duplicates("cod_indicador", keep="last")
    anterior = pes[(pes["ano"] == ANO_FECHADO) & (pes["mes_referencia"] == 12) & (pes["realizado"] > 0)]
    # Excluir o inválido: realizado com percentual impossível (Q02) não serve de base.
    q02 = _achados_regra(dados, "Q02", uf)
    invalidos = set(q02.loc[q02["ano"].astype(int) == ANO_FECHADO, "item"].astype(str))
    anterior = anterior[~anterior["cod_indicador"].isin(invalidos)]
    m = atual.merge(anterior[["cod_indicador", "realizado", "realizado_txt", "arquivo_origem",
                              "sha256_origem"]],
                    on="cod_indicador", suffixes=("", "_ant"))
    return _ordenar_cod(m[m["meta"] < m["realizado_ant"]])


def bloco_calibracao(ficha: Ficha, dados: Dados, uf: str) -> None:
    """Metas de 2026 abaixo do realizado de 2025 (unidade do escopo) e resumo da Q13 por fonte."""
    for _, l in metas_abaixo_realizado(dados, uf).iterrows():
        u = l["unidade"]
        montado = montar(
            "Indicador {cod} ({nome}), {uf}: realizado {ano_ant} de {real}; meta {ano} de {meta}, "
            "abaixo do que já foi entregue.",
            cod=Num(l["cod_indicador"]), nome=l["indicador"], uf=uf,
            ano_ant=Num(str(ANO_FECHADO)), ano=Num(str(ANO_PROJECAO)),
            real=Num(fmt_publicado(l["realizado_txt_ant"], l["realizado_ant"], u)),
            meta=Num(fmt_publicado(l["meta_txt"], l["meta"], u)),
        )
        fonte = fonte_linha_api(l)
        fonte["arquivos_complementares"] = [f"data/raw/{l['arquivo_origem_ant']}"]
        ficha.add("calibracao", f"Meta {ANO_PROJECAO} abaixo do realizado {ANO_FECHADO} — "
                  f"{uf} indicador {l['cod_indicador']}", montado, fonte,
                  cod_indicador=l["cod_indicador"])

    q13 = _achados_regra(dados, "Q13", None if uf == UF_DN else uf)
    contagem = q13["fonte"].value_counts()
    partes = [montar("{rotulo}: {n}", rotulo=rotulo, n=Num(fmt_int(contagem.get(fonte, 0))))
              for fonte, rotulo in FONTES_Q13.items()]
    onde = "nos DRs" if uf == UF_DN else f"em {uf}"
    alvo = montar("Metas de {ano} abaixo do realizado de {ano_ant} {onde} (regra {r}), por fonte: ",
                  ano=Num(str(ANO_PROJECAO)), ano_ant=Num(str(ANO_FECHADO)), onde=onde, r="Q13")
    corpo = juntar(partes)
    total = montar("; total: {t}.", t=Num(fmt_int(len(q13))))
    ficha.add("calibracao", "Resumo da regra Q13 por fonte",
              juntar([alvo, corpo, total], sep=""),
              fonte_csv(dados, "qualidade_achados", tipo="regra", regra_id="Q13"),
              por_fonte={f: int(contagem.get(f, 0)) for f in FONTES_Q13})


def bloco_triangulacao(ficha: Ficha, dados: Dados) -> None:
    """Quantos valores nacionais foram confirmados no Relatório de Gestão."""
    tri = dados["triangulacao_relatorio"]
    achados = tri[_encontrado(tri)]
    paginas = sorted({p for ps in achados["paginas"].dropna() for p in str(ps).split(";")},
                     key=int)
    montado = montar(
        "{x} de {y} valores nacionais de {ano} confirmados no Relatório de Gestão.",
        x=Num(fmt_int(len(achados))), y=Num(fmt_int(len(tri))), ano=Num(str(ANO_FECHADO)),
    )
    ficha.add("triangulacao", "Triangulação com o Relatório de Gestão", montado,
              fonte_pdf(dados, paginas),
              nao_confirmados=sorted(tri.loc[~_encontrado(tri), "cod_indicador"]))


# --------------------------------------------------------------------------
# Blocos — escopo DR
# --------------------------------------------------------------------------

def bloco_resultado_dr(ficha: Ficha, dados: Dados, validos: pd.DataFrame, uf: str) -> None:
    """Resultado do DR por indicador e comparação com as medianas nacional e regional."""
    linhas = _ordenar_cod(validos[validos["uf"] == uf])
    diag = dados["diag_indicador"].set_index("cod_indicador")
    reg = dados["diag_regiao"].set_index(["regiao", "cod_indicador"])
    for _, linha in linhas.iterrows():
        cod = linha["cod_indicador"]
        ficha.add("resultado_dr", f"Resultado {uf} — indicador {cod}",
                  _frase_resultado(linha), fonte_linha_api(linha), cod_indicador=cod)

        partes = [montar("Indicador {cod}: índice de {uf} {idx}", cod=Num(cod), uf=uf,
                         idx=Num(fmt_pct(linha["indice_analise"])))]
        if cod in diag.index:
            partes.append(montar("mediana nacional dos DRs {m}",
                                 m=Num(fmt_pct(diag.loc[cod, "mediana_indice"]))))
        if (linha["regiao"], cod) in reg.index:
            partes.append(montar("mediana da região {r} {m}", r=linha["regiao"],
                                 m=Num(fmt_pct(reg.loc[(linha["regiao"], cod), "mediana_indice"]))))
        frase, nums = juntar(partes)
        ficha.add("comparacao", f"Comparação {uf} — indicador {cod}", (frase + ".", nums),
                  {**fonte_csv(dados, "diag_indicador"),
                   "arquivos_complementares": ["data/processed/diag_regiao.csv"]},
                  cod_indicador=cod)


def bloco_assimetria_dr(ficha: Ficha, dados: Dados, dr: pd.Series) -> None:
    """Farol consolidado do DR e mediana regional do indicador 1."""
    montado = montar(
        "{uf}: {t} atingidos, {a} em atenção e {c} críticos, de {n} indicadores com dado ({resumo}).",
        uf=dr["uf"], t=Num(fmt_int(dr["n_atingido"])), a=Num(fmt_int(dr["n_atencao"])),
        c=Num(fmt_int(dr["n_critico"])), n=Num(fmt_int(dr["n_indicadores_com_dado"])),
        resumo=dr["resumo"],
    )
    ficha.add("assimetria", f"Farol de {dr['uf']}", montado, fonte_csv(dados, "diag_dr"))

    reg = dados["diag_regiao"]
    l = reg[(reg["cod_indicador"] == COD_EMPREGABILIDADE) & (reg["regiao"] == dr["regiao"])]
    if l.empty:
        return
    l = l.iloc[0]
    montado = montar(
        "Mediana do realizado do indicador {cod} ({nome}) na região {r}: {m} ({n} DRs).",
        cod=Num(COD_EMPREGABILIDADE), nome=l["indicador"], r=l["regiao"],
        m=Num(fmt_valor(l["mediana_realizado"], unidades(dados).get(COD_EMPREGABILIDADE))),
        n=Num(fmt_int(l["n_drs"])),
    )
    ficha.add("assimetria", f"Mediana regional — indicador {COD_EMPREGABILIDADE}", montado,
              fonte_csv(dados, "diag_regiao"), cod_indicador=COD_EMPREGABILIDADE)


def bloco_cruzamento_dr(ficha: Ficha, dados: Dados, uf: str) -> None:
    """Volume x resultado do DR."""
    cruz = dados["cruzamento_volume_resultado"]
    l = cruz[cruz["uf"] == uf]
    if l.empty:
        return
    l = l.iloc[0]

    def pct(v: Any) -> Num | str:
        return "sem dado" if pd.isna(v) else Num(fmt_pct(v))

    montado = montar(
        "{uf}: índice de matrículas {m}, índice de empregabilidade {e}, índice de "
        "fidelização {f}; leitura: {leitura}.",
        uf=uf, m=pct(l["indice_matriculas"]), e=pct(l["indice_empregabilidade"]),
        f=pct(l["indice_fidelizacao"]), leitura=l["leitura"],
    )
    ficha.add("cruzamento", f"Volume x resultado — {uf}", montado,
              fonte_csv(dados, "cruzamento_volume_resultado"))


def bloco_apoio_dr(ficha: Ficha, dados: Dados, dr: pd.Series) -> None:
    """Situação do DR em relação ao grupo de apoio."""
    if dr["n_critico"] >= MIN_CRITICOS_APOIO:
        montado = _frase_apoio_dr(dr)
        titulo = f"Grupo de apoio — {dr['uf']}"
    else:
        montado = montar(
            "{uf} tem {c} indicadores críticos e não está no grupo de apoio "
            "({lim} ou mais críticos); {resumo}.",
            uf=dr["uf"], c=Num(fmt_int(dr["n_critico"])),
            lim=Num(fmt_int(MIN_CRITICOS_APOIO)), resumo=dr["resumo"],
        )
        titulo = f"Fora do grupo de apoio — {dr['uf']}"
    ficha.add("apoio", titulo, montado, fonte_csv(dados, "diag_dr"), uf=dr["uf"])


def bloco_projecao_dr(ficha: Ficha, dados: Dados, uf: str) -> None:
    """Projeção 2026 do DR por indicador, e indicadores fora por zero suspeito."""
    proj = _ordenar_cod(dados["projecao_2026"][dados["projecao_2026"]["uf"] == uf])
    for _, l in proj.iterrows():
        u = unidades(dados).get(l["cod_indicador"])
        rotulo = rotulo_indicador(l["cod_indicador"], l["indicador"])
        partes = [
            juntar([montar("Projeção {ano} de {uf} — ", ano=Num(str(l["ano"])), uf=uf), rotulo,
                    montar(": meta {meta}, realizado até o mês {mes}: {real}",
                           meta=Num(fmt_publicado(l["meta_txt"], l["meta"], u)),
                           mes=Num(str(l["mes_referencia"])),
                           real=Num(fmt_publicado(l["realizado_txt"], l["realizado"], u)))],
                   sep=""),
            montar("índice projetado {idx}", idx=Num(fmt_pct(l["indice_projetado"]))),
            montar("situação: {sit}", sit=l["situacao"]),
        ]
        if l["situacao"] in (SIT_RISCO, SIT_ACELERACAO) and pd.notna(l["aceleracao_necessaria"]):
            partes.append(montar("aceleração necessária de {a} o ritmo atual",
                                 a=Num(fmt_vezes(l["aceleracao_necessaria"]))))
        partes.append(montar("método: {met}.", met=l["metodo"]))
        ficha.add("projecao_2026", f"Projeção {ANO_PROJECAO} {uf} — {rotulo[0]}",
                  juntar(partes), fonte_csv(dados, "projecao_2026"),
                  cod_indicador=l["cod_indicador"])

    pes = dados["pes"]
    suspeitos = pes[(pes["uf"] == uf) & (pes["ano"] == ANO_PROJECAO)
                    & (pes["status_dado"] == STATUS_ZERO_SUSPEITO)]
    projetados = set(dados["projecao_resumo"]["cod_indicador"])
    for _, l in _ordenar_cod(suspeitos[suspeitos["cod_indicador"].isin(projetados)]).iterrows():
        montado = montar(
            "{uf} ficou fora da projeção {ano} do indicador {cod}: realizado publicado "
            "\"{txt}\", tratado como zero suspeito.",
            uf=uf, ano=Num(str(ANO_PROJECAO)), cod=Num(l["cod_indicador"]),
            txt=" ".join(str(l["realizado_txt"]).split()),
        )
        ficha.add("projecao_2026", f"Fora da projeção — {uf} indicador {l['cod_indicador']}",
                  montado, fonte_linha_api(l), cod_indicador=l["cod_indicador"])


def bloco_qualidade_dr(ficha: Ficha, dados: Dados, uf: str) -> None:
    """Um fato por regra com achado na UF (itens listados)."""
    a = dados["qualidade_achados"]
    a = a[a["uf"] == uf]
    nomes = dict(zip(dados["qualidade_resumo"]["regra_id"], dados["qualidade_resumo"]["nome"]))
    for regra, grupo in a.groupby("regra_id", sort=True):
        itens = ", ".join(dict.fromkeys(f"{i} ({ano})" for i, ano in zip(grupo["item"], grupo["ano"])))
        partes = [montar("Regra {r} ({nome}) em {uf}: ocorrências: {n}; itens: {itens}",
                         r=regra, nome=nomes.get(regra, ""), uf=uf,
                         n=Num(fmt_int(len(grupo))), itens=itens)]
        destaque = regra in REGRAS_DESTAQUE
        if destaque and len(grupo) == 1:
            frase_achado = descrever_achado(dados, grupo.iloc[0])
            if frase_achado is not None:
                partes.append(frase_achado)
        if destaque:
            partes.append(montar("recomendação: {rec}", rec=grupo.iloc[0]["recomendacao"]))
        frase, nums = juntar(partes)
        ficha.add("qualidade", f"Qualidade {uf} — {regra}", (frase.rstrip(".") + ".", nums),
                  fonte_csv(dados, "qualidade_achados", tipo="regra", regra_id=regra),
                  destaque=destaque)


# --------------------------------------------------------------------------
# Orquestração
# --------------------------------------------------------------------------

def ufs_validas(dados: Dados) -> list[str]:
    """Siglas de DR aceitas como escopo."""
    return sorted(dados["diag_dr"]["uf"].astype(str))


def gerar_fatos(dados: Dados, escopo: str) -> list[dict[str, Any]]:
    """Gera a lista de fatos para o escopo ("BR" ou sigla do DR)."""
    escopo = escopo.upper()
    ficha = Ficha()
    validos = farol_valido(dados)
    if escopo == ESCOPO_BR:
        bloco_resultado_nacional(ficha, dados, validos)
        bloco_assimetria(ficha, dados)
        bloco_cruzamento(ficha, dados)
        bloco_apoio(ficha, dados)
        bloco_projecao_nacional(ficha, dados)
        bloco_calibracao(ficha, dados, UF_DN)
        bloco_qualidade_nacional(ficha, dados)
        bloco_triangulacao(ficha, dados)
        return ficha.fatos

    if escopo not in ufs_validas(dados):
        raise ValueError(f"Escopo inválido: {escopo!r}. Use BR ou um de {ufs_validas(dados)}")
    dr = dados["diag_dr"].set_index("uf").loc[escopo].copy()
    dr["uf"] = escopo
    bloco_resultado_dr(ficha, dados, validos, escopo)
    bloco_assimetria_dr(ficha, dados, dr)
    bloco_cruzamento_dr(ficha, dados, escopo)
    bloco_apoio_dr(ficha, dados, dr)
    bloco_projecao_dr(ficha, dados, escopo)
    bloco_calibracao(ficha, dados, escopo)
    bloco_qualidade_dr(ficha, dados, escopo)
    return ficha.fatos


def salvar(fatos: list[dict[str, Any]], escopo: str, dados: Dados,
           outputs_dir: Path = OUTPUTS_DIR) -> Path:
    """Grava ``outputs/fatos_{escopo}.json``."""
    outputs_dir.mkdir(parents=True, exist_ok=True)
    destino = outputs_dir / f"fatos_{escopo.upper()}.json"
    doc = {
        "escopo": escopo.upper(),
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_fatos": len(fatos),
        "entradas_sha256": {f"data/processed/{k}.csv": v for k, v in dados.sha256_csv.items()},
        "fatos": fatos,
    }
    destino.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=_json_default),
                       encoding="utf-8")
    return destino


def _json_default(obj: Any) -> Any:
    """Converte tipos numpy para JSON."""
    if hasattr(obj, "item"):
        return obj.item()
    raise TypeError(f"Tipo não serializável: {type(obj)}")


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m src.fatos --escopo BR."""
    parser = argparse.ArgumentParser(description="Gera a ficha de fatos (JSON).")
    parser.add_argument("--escopo", default=ESCOPO_BR, help='"BR" ou sigla do DR (ex.: AC)')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    dados = carregar_dados()
    fatos = gerar_fatos(dados, args.escopo)
    destino = salvar(fatos, args.escopo, dados)
    por_cat = pd.Series([f["categoria"] for f in fatos]).value_counts().to_dict()
    logger.info("%d fatos gravados em %s: %s", len(fatos), destino, por_cat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
