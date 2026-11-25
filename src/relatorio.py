"""Relatório de Gestão SENAI 2025: download, extração de texto e triangulação.

Etapas:
1. Baixa o PDF oficial para ``data/raw/relatorio/`` e registra no manifest
   (url, data/hora UTC, sha256). Não baixa de novo se o arquivo local já
   existir com o mesmo sha256 registrado.
2. Extrai o texto por página com pdfplumber -> ``relatorio_paginas.csv``.
3. ``buscar``: busca textual simples com trecho de contexto.
4. Triangulação: procura no texto os valores nacionais (DN, 2025) de
   ``pes.csv`` -> ``triangulacao_relatorio.csv``.

Segurança: o texto do PDF é dado NÃO confiável. Este módulo apenas extrai,
indexa e compara strings; nenhum conteúdo do PDF é executado ou tratado
como instrução.

Uso: ``python -m src.relatorio``
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import unicodedata
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger("relatorio")

RAIZ = Path(__file__).resolve().parents[1]
RAW_DIR = RAIZ / "data" / "raw"
PROCESSED_DIR = RAIZ / "data" / "processed"
MANIFEST_PATH = RAW_DIR / "manifest.json"

URL_RELATORIO = (
    "https://static.portaldaindustria.com.br/media/filer_public/80/78/"
    "8078ff34-bb7d-4733-98b6-5a258a82ad37/relatorio_senai_2025_web.pdf"
)
PDF_PATH = RAW_DIR / "relatorio" / "relatorio_gestao_2025.pdf"
PAGINAS_CSV = PROCESSED_DIR / "relatorio_paginas.csv"
PES_CSV = PROCESSED_DIR / "pes.csv"
TRIANGULACAO_CSV = PROCESSED_DIR / "triangulacao_relatorio.csv"

USER_AGENT = "senai-briefing-ia/0.1 (coleta de dados publicos; Python requests)"
TIMEOUT_S = 120
JANELA_TRECHO = 300
ANO_TRIANGULACAO = 2025

_RE_ESPACOS = re.compile(r"\s+")
_RE_NUMERO = re.compile(r"^(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?$")
_RE_PALAVRA = re.compile(r"[^\W\d_]+")

CONF_FORTE = "forte"
CONF_FRACA = "fraca"
MIN_LETRAS_CHAVE = 5
LIMITE_INTEIRO_PEQUENO = 100

# Comparadas já sem acento e em minúsculas. Inclui termos genéricos do
# domínio que aparecem em quase toda página do relatório.
STOPWORDS: frozenset[str] = frozenset({
    "apenas", "atraves", "cada", "como", "desde", "durante", "entre", "essas",
    "esses", "estao", "mesmo", "nossa", "nosso", "outras", "outros", "pelas",
    "pelos", "quais", "quando", "sobre", "todas", "todos", "incluindo",
    "percentual", "numero", "quantidade", "valor", "total", "taxa",
    "industria", "industrias", "industrial", "industriais", "senai",
})


# --------------------------------------------------------------------------
# 1. Download + manifest
# --------------------------------------------------------------------------

def sha256_arquivo(caminho: Path) -> str:
    """Calcula o sha256 de um arquivo em blocos."""
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _carregar_manifest(caminho: Path) -> dict[str, dict[str, Any]]:
    """Lê o manifest (ou retorna vazio se não existir)."""
    if caminho.exists():
        return json.loads(caminho.read_text(encoding="utf-8"))
    return {}


def _salvar_manifest(manifest: dict[str, dict[str, Any]], caminho: Path) -> None:
    """Grava o manifest de forma atômica, no mesmo formato de src.coleta."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(caminho)


def _chave_manifest(destino: Path, raw_dir: Path) -> str:
    """Chave do manifest: caminho relativo a data/raw (com '/')."""
    try:
        return destino.resolve().relative_to(raw_dir.resolve()).as_posix()
    except ValueError:
        return destino.name


def baixar_relatorio(
    url: str = URL_RELATORIO,
    destino: Path = PDF_PATH,
    manifest_path: Path = MANIFEST_PATH,
    sessao: requests.Session | None = None,
) -> bool:
    """Baixa o PDF se necessário e registra no manifest.

    Não baixa de novo quando o arquivo local existe e seu sha256 é igual ao
    registrado no manifest. Retorna True se baixou, False se reaproveitou.
    """
    manifest = _carregar_manifest(manifest_path)
    chave = _chave_manifest(destino, manifest_path.parent)
    registro = manifest.get(chave, {})

    if destino.exists() and registro.get("sha256") == sha256_arquivo(destino):
        logger.info("Relatório já presente com o mesmo sha256; download ignorado.")
        return False

    sessao = sessao or requests.Session()
    resp = sessao.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S)
    resp.raise_for_status()
    conteudo = resp.content
    if not conteudo.startswith(b"%PDF"):
        raise ValueError("Conteúdo baixado não é um PDF (assinatura %PDF ausente).")

    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".pdf.tmp")
    tmp.write_bytes(conteudo)
    tmp.replace(destino)

    manifest[chave] = {
        "url": url,
        "coletado_em_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": hashlib.sha256(conteudo).hexdigest(),
        "status_http": resp.status_code,
        "bytes": len(conteudo),
        "erro": None,
    }
    _salvar_manifest(manifest, manifest_path)
    logger.info("Relatório baixado (%d bytes) -> %s", len(conteudo), destino)
    return True


# --------------------------------------------------------------------------
# 2. Extração de texto
# --------------------------------------------------------------------------

def extrair_paginas(pdf_path: Path = PDF_PATH) -> pd.DataFrame:
    """Extrai o texto de cada página (pagina começa em 1).

    O texto é guardado como veio do PDF (sem interpretação); páginas sem
    texto extraível ficam com string vazia.
    """
    import pdfplumber  # import local: só necessário nesta etapa

    linhas: list[dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, pagina in enumerate(pdf.pages, start=1):
            texto = pagina.extract_text() or ""
            linhas.append({"pagina": i, "texto": texto, "n_caracteres": len(texto)})
    df = pd.DataFrame(linhas, columns=["pagina", "texto", "n_caracteres"])
    logger.info("Texto extraído de %d páginas.", len(df))
    return df


def carregar_paginas(caminho: Path = PAGINAS_CSV) -> pd.DataFrame:
    """Lê relatorio_paginas.csv (texto vazio vira string vazia)."""
    df = pd.read_csv(caminho, encoding="utf-8-sig", dtype={"texto": str})
    df["texto"] = df["texto"].fillna("")
    return df


# --------------------------------------------------------------------------
# 3. Busca
# --------------------------------------------------------------------------

def normalizar_espacos(texto: str) -> str:
    """Colapsa qualquer sequência de espaços/quebras (inclui NBSP) em um espaço."""
    return _RE_ESPACOS.sub(" ", texto.replace(" ", " ").replace(" ", " ")).strip()


def sem_acento(texto: str) -> str:
    """Minúsculas sem acento, preservando o comprimento (1 caractere -> 1)."""
    return "".join(unicodedata.normalize("NFKD", c)[0] for c in texto.lower())


def _janela(norm: str, m: re.Match[str]) -> tuple[int, int]:
    """Limites do trecho de ±JANELA_TRECHO caracteres em volta da ocorrência."""
    return max(0, m.start() - JANELA_TRECHO), min(len(norm), m.end() + JANELA_TRECHO)


def _ocorrencias(
    padrao: re.Pattern[str], paginas: pd.DataFrame
) -> Iterator[tuple[int, str, re.Match[str]]]:
    """Gera (pagina, texto normalizado, match) para cada ocorrência, em ordem."""
    for pagina, texto in zip(paginas["pagina"], paginas["texto"]):
        norm = normalizar_espacos(str(texto))
        for m in padrao.finditer(norm):
            yield int(pagina), norm, m


def _buscar_regex(
    padrao: re.Pattern[str], paginas: pd.DataFrame, max_resultados: int
) -> list[dict[str, Any]]:
    """Procura ``padrao`` no texto normalizado de cada página, em ordem."""
    resultados: list[dict[str, Any]] = []
    for pagina, norm, m in _ocorrencias(padrao, paginas):
        ini, fim = _janela(norm, m)
        resultados.append({"pagina": pagina, "trecho": norm[ini:fim]})
        if len(resultados) >= max_resultados:
            break
    return resultados


def buscar(
    termos: list[str],
    max_resultados: int = 5,
    paginas: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    """Busca literal (sem diferenciar maiúsculas) de qualquer dos ``termos``.

    Retorna até ``max_resultados`` itens ``{pagina, trecho}``, com trecho de
    ±300 caracteres em volta da ocorrência. Os termos são escapados (não são
    tratados como regex). ``paginas`` padrão: relatorio_paginas.csv.
    """
    termos_validos = [normalizar_espacos(t) for t in termos if t and t.strip()]
    if not termos_validos or max_resultados <= 0:
        return []
    if paginas is None:
        paginas = carregar_paginas()
    padrao = re.compile("|".join(re.escape(t) for t in termos_validos), re.IGNORECASE)
    return _buscar_regex(padrao, paginas, max_resultados)


# --------------------------------------------------------------------------
# 4. Triangulação
# --------------------------------------------------------------------------

def realizado_valido(txt: Any) -> bool:
    """True se ``realizado_txt`` tem um número reconhecível ("-" não é zero)."""
    return padrao_valor(txt) is not None


def padrao_valor(realizado_txt: Any) -> re.Pattern[str] | None:
    """Monta a regex que reconhece o valor no texto do relatório.

    - Percentual "39,8%": aceita "39,8%" e "39,8 %".
    - Inteiro "7.476": aceita "7.476" e "7476" (e vice-versa).
    - Monetário "R$ 1.057.901.518,57": procura apenas o número.
    Fronteiras impedem casar dentro de outro número ("59" em "1.059").
    Retorna None para valores ausentes ("-", vazio) ou não numéricos.
    """
    if realizado_txt is None or (isinstance(realizado_txt, float) and pd.isna(realizado_txt)):
        return None
    txt = normalizar_espacos(str(realizado_txt))
    txt = re.sub(r"^R\$\s*", "", txt)
    percentual = txt.endswith("%")
    numero = txt.rstrip("%").strip()
    if not _RE_NUMERO.match(numero):
        return None

    inteiro, _, decimal = numero.partition(",")
    digitos = inteiro.replace(".", "")
    grupos: list[str] = []
    while len(digitos) > 3:
        grupos.insert(0, digitos[-3:])
        digitos = digitos[:-3]
    grupos.insert(0, digitos)

    corpo = r"\.?".join(grupos)
    if decimal:
        corpo += "," + decimal
    if percentual:
        corpo += r" ?%"
        return re.compile(rf"(?<![\d.,]){corpo}")
    return re.compile(rf"(?<![\d.,]){corpo}(?![\d]|[.,]\d)")


def carregar_dn(pes_path: Path = PES_CSV, ano: int = ANO_TRIANGULACAO) -> pd.DataFrame:
    """Linhas do DN no ano dado com ``realizado_txt`` válido."""
    pes = pd.read_csv(pes_path, encoding="utf-8-sig", dtype={"cod_indicador": str, "realizado_txt": str})
    dn = pes[(pes["ano"] == ano) & (pes["uf"] == "DN")]
    return dn[dn["realizado_txt"].map(realizado_valido)].reset_index(drop=True)


def palavras_chave(indicador: Any) -> list[str]:
    """Palavras do nome do indicador com 5+ letras, sem stopwords.

    Retornadas em minúsculas e sem acento, sem repetição, na ordem do nome.
    """
    if not isinstance(indicador, str):
        return []
    chaves: list[str] = []
    for palavra in _RE_PALAVRA.findall(sem_acento(indicador)):
        if len(palavra) >= MIN_LETRAS_CHAVE and palavra not in STOPWORDS and palavra not in chaves:
            chaves.append(palavra)
    return chaves


def inteiro_pequeno(realizado_txt: Any) -> bool:
    """True se o valor é inteiro (sem % e sem decimais) e menor que 100."""
    txt = re.sub(r"^R\$\s*", "", normalizar_espacos(str(realizado_txt)))
    if txt.endswith("%") or "," in txt or not _RE_NUMERO.match(txt):
        return False
    return int(txt.replace(".", "")) < LIMITE_INTEIRO_PEQUENO


def triangular(dn: pd.DataFrame, paginas: pd.DataFrame) -> pd.DataFrame:
    """Procura cada ``realizado_txt`` do DN no texto do relatório.

    Cada ocorrência é "forte" se alguma palavra-chave do indicador aparece a
    até 300 caracteres dela, e "fraca" caso contrário. A confiança da linha é
    "forte" se houver ao menos uma ocorrência forte; inteiros < 100 só são
    aceitos com confiança forte (senão, encontrado=False).

    Saída: cod_indicador, indicador, realizado_txt, encontrado, confianca
    ("forte"/"fraca"/""), paginas (separadas por ';'; só as fortes quando
    houver), trecho (primeira ocorrência forte, ou a primeira fraca).
    """
    linhas: list[dict[str, Any]] = []
    for _, r in dn.iterrows():
        padrao = padrao_valor(r["realizado_txt"])
        chaves = palavras_chave(r["indicador"])
        fortes: list[tuple[int, str]] = []
        fracas: list[tuple[int, str]] = []
        for pagina, norm, m in (_ocorrencias(padrao, paginas) if padrao else ()):
            ini, fim = _janela(norm, m)
            trecho = norm[ini:fim]
            janela = sem_acento(trecho)
            destino = fortes if any(c in janela for c in chaves) else fracas
            destino.append((pagina, trecho))

        if not fortes and inteiro_pequeno(r["realizado_txt"]):
            fracas = []
        aceitas = fortes or fracas
        confianca = CONF_FORTE if fortes else (CONF_FRACA if fracas else "")
        linhas.append({
            "cod_indicador": r["cod_indicador"],
            "indicador": r["indicador"],
            "realizado_txt": r["realizado_txt"],
            "encontrado": bool(aceitas),
            "confianca": confianca,
            "paginas": ";".join(str(p) for p in sorted({p for p, _ in aceitas})),
            "trecho": aceitas[0][1] if aceitas else "",
        })
    return pd.DataFrame(
        linhas,
        columns=[
            "cod_indicador", "indicador", "realizado_txt", "encontrado",
            "confianca", "paginas", "trecho",
        ],
    )


# --------------------------------------------------------------------------
# Execução
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Executa download, extração e triangulação."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", default="INFO", help="nível de log")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    baixar_relatorio()

    paginas = extrair_paginas(PDF_PATH)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    paginas.to_csv(PAGINAS_CSV, index=False, encoding="utf-8-sig")
    logger.info("Gravado %s", PAGINAS_CSV)

    tri = triangular(carregar_dn(), paginas)
    tri.to_csv(TRIANGULACAO_CSV, index=False, encoding="utf-8-sig")
    logger.info(
        "%d de %d valores nacionais encontrados no Relatório de Gestão",
        int(tri["encontrado"].sum()), len(tri),
    )
    logger.info(
        "%d fortes, %d fracas, de %d valores",
        int((tri["confianca"] == CONF_FORTE).sum()),
        int((tri["confianca"] == CONF_FRACA).sum()),
        len(tri),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
