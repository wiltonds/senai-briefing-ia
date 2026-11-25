"""Pergunte ao briefing: respostas pré-geradas a um catálogo fixo de perguntas.

Fluxo: lê a ficha ``outputs/fatos_{escopo}.json`` (gera com ``src.fatos`` se não
existir) e ``prompts/perguntas_v1.md`` (system); faz 1 chamada por escopo com as
6 perguntas do catálogo; valida cada resposta com as regras de ``src.validador``
(números e UFs presentes nos fatos citados, causa só como hipótese, jargão,
código de indicador) e mais: ≤70 palavras, ≥1 fato citado, fatos existentes.
A recusa padrão (``RECUSA``, com ``fatos=[]``) é uma resposta válida.

Se alguma resposta reprovar, reenvia pedindo correção só das reprovadas (no
máximo 3 tentativas no total). Pergunta que não for aprovada sai do catálogo:
resposta não validada nunca é publicada.

Saídas:
- ``outputs/app_data/perguntas_{escopo}.json`` (cada fato citado com texto e fonte);
- ``logs/perguntas_{escopo}_{timestamp}.json`` (hashes, tentativas, tokens).

Uso:
    python -m src.perguntas --escopo BR
    python -m src.perguntas --escopo BR --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src import fatos as mod_fatos
from src.redator import (
    CHARS_POR_TOKEN,
    MAX_TENTATIVAS,
    MODELO_PADRAO,
    _chamar,
    _texto_resposta,
    _uso,
    sha256_arquivo,
)
from src.produto import FONTE_CRUZ, FONTE_PES
from src.validador import (
    HORIZONTES,
    JARGAO_PROIBIDO,
    NUMEROS_SEMPRE_PERMITIDOS,
    RE_CAUSAL,
    RE_CODIGO_INDICADOR,
    tokens_numericos,
    ufs_mencionadas,
)

logger = logging.getLogger("perguntas")

RAIZ = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = RAIZ / "outputs"
APP_DATA_DIR = OUTPUTS_DIR / "app_data"
LOGS_DIR = RAIZ / "logs"
PROMPT_PATH = RAIZ / "prompts" / "perguntas_v1.md"
ENV_PATH = RAIZ / ".env"
VERSAO_PROMPT = "v1"

MAX_PALAVRAS_RESPOSTA = 70
RECUSA = "Os dados oficiais disponíveis não permitem responder com segurança."
PERGUNTA_ACAO = "O que fazer agora?"  # resposta deve citar ao menos um de HORIZONTES

CATALOGO_BR: tuple[tuple[str, str], ...] = (
    ("P1", "Qual é o principal problema hoje?"),
    ("P2", "Quais DRs precisam de apoio prioritário?"),
    ("P3", "Vamos bater as metas de 2026?"),
    ("P4", "Volume alto significa bom resultado?"),
    ("P5", "Quão confiáveis são esses números?"),
    ("P6", PERGUNTA_ACAO),
)
CATALOGO_DR: tuple[tuple[str, str], ...] = (
    ("P1", "Qual é o principal problema hoje?"),
    ("P2", "Como meu DR está frente ao Brasil?"),
    ("P3", "Vamos bater as metas de 2026?"),
    ("P4", "Volume alto significa bom resultado?"),
    ("P5", "Quão confiáveis são esses números?"),
    ("P6", PERGUNTA_ACAO),
)


def catalogo(escopo: str) -> tuple[tuple[str, str], ...]:
    """Catálogo de perguntas do escopo (BR ou DR)."""
    return CATALOGO_BR if escopo.upper() == mod_fatos.ESCOPO_BR else CATALOGO_DR


# ---------------------------------------------------------------- entradas
def carregar_ficha(escopo: str, outputs_dir: Path = OUTPUTS_DIR) -> tuple[list[dict], Path]:
    """Lê ``fatos_{escopo}.json``; se não existir, gera com ``src.fatos``."""
    caminho = outputs_dir / f"fatos_{escopo}.json"
    if not caminho.is_file():
        logger.info("Ficha %s ausente; gerando com src.fatos.", caminho.name)
        dados = mod_fatos.carregar_dados()
        caminho = mod_fatos.salvar(mod_fatos.gerar_fatos(dados, escopo), escopo, dados, outputs_dir)
    with caminho.open(encoding="utf-8") as fh:
        return json.load(fh)["fatos"], caminho


def montar_mensagem(fatos: list[dict], escopo: str, perguntas: list[tuple[str, str]]) -> str:
    """Mensagem do usuário: fatos (id, categoria, frase) e perguntas a responder."""
    linhas_f = [
        json.dumps({"id": f["id"], "categoria": f["categoria"], "frase": f["frase"]}, ensure_ascii=False)
        for f in fatos
    ]
    linhas_p = [json.dumps({"id": i, "pergunta": p}, ensure_ascii=False) for i, p in perguntas]
    return (
        f"Escopo: {escopo}.\n\n"
        "<fatos>\n" + "\n".join(linhas_f) + "\n</fatos>\n\n"
        "<perguntas>\n" + "\n".join(linhas_p) + "\n</perguntas>\n\n"
        "Responda às perguntas seguindo as regras. Responda somente com o array JSON."
    )


def mensagem_correcao(erros: dict[str, list[str]]) -> str:
    """Pedido de correção, só das perguntas reprovadas."""
    linhas = [f"- {pid}: {e}" for pid, lista in erros.items() for e in lista]
    return (
        "Algumas respostas não passaram na validação. Devolva o array JSON somente com as "
        f"perguntas {', '.join(erros)}, corrigidas, sem texto fora do JSON:\n" + "\n".join(linhas)
    )


def extrair_lista(texto: str) -> list:
    """Extrai o array JSON da resposta (tolera cercas ``` e texto em volta).

    Raises:
        ValueError: se não houver um array JSON válido.
    """
    inicio, fim = texto.find("["), texto.rfind("]")
    if inicio == -1 or fim <= inicio:
        raise ValueError("a resposta não contém um array JSON")
    try:
        obj = json.loads(texto[inicio : fim + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido na resposta ({exc.msg}, posição {exc.pos})") from exc
    if not isinstance(obj, list):
        raise ValueError("o JSON da resposta não é um array")
    return obj


# ---------------------------------------------------------------- validação
def validar_resposta(item: Any, pergunta: str, frases: dict[str, str]) -> list[str]:
    """Valida uma resposta contra a ficha. Lista vazia = aprovada.

    Args:
        item: objeto ``{id, pergunta, resposta, fatos}`` devolvido pela IA.
        pergunta: texto oficial da pergunta no catálogo.
        frases: ``{id do fato: frase}``.
    """
    if not isinstance(item, dict):
        return ["Esquema: cada resposta deve ser um objeto JSON."]
    erros: list[str] = []
    faltando = {"id", "pergunta", "resposta", "fatos"} - set(item)
    extras = set(item) - {"id", "pergunta", "resposta", "fatos"}
    if faltando:
        erros.append(f"Esquema: faltam os campos {sorted(faltando)}.")
    if extras:
        erros.append(f"Esquema: campos não permitidos {sorted(extras)}.")
    resposta, citados = item.get("resposta"), item.get("fatos")
    if not isinstance(resposta, str) or not resposta.strip():
        erros.append("Esquema: 'resposta' deve ser texto não vazio.")
        resposta = ""
    if not isinstance(citados, list) or not all(isinstance(f, str) for f in citados):
        erros.append("Esquema: 'fatos' deve ser uma lista de IDs (texto).")
        citados = []
    if item.get("pergunta") != pergunta:
        erros.append(f"Esquema: o campo 'pergunta' deve ser exatamente {pergunta!r}.")
    if erros:
        return erros

    resposta = resposta.strip()
    if resposta == RECUSA:
        return [] if not citados else ["A recusa padrão deve vir com 'fatos' vazio."]
    if not citados:
        return [f"A resposta não cita nenhum fato; se os fatos não sustentam, use exatamente: {RECUSA!r}"]

    n = len(resposta.split())
    if n > MAX_PALAVRAS_RESPOSTA:
        erros.append(f"Resposta com {n} palavras (máximo {MAX_PALAVRAS_RESPOSTA}).")
    for fid in citados:
        if fid not in frases:
            erros.append(f"Cita o fato '{fid}', que não existe na ficha.")
    texto_citado = " ".join(frases[f] for f in citados if f in frases)

    permitidos = tokens_numericos(texto_citado) | NUMEROS_SEMPRE_PERMITIDOS
    for tok in sorted(tokens_numericos(resposta) - permitidos):
        erros.append(f"O número '{tok}' não aparece nos fatos citados ({', '.join(citados)}).")
    for uf in sorted(ufs_mencionadas(resposta) - ufs_mencionadas(texto_citado)):
        erros.append(f"A UF '{uf}' não aparece nos fatos citados.")
    m = RE_CAUSAL.search(resposta)
    if m and "hipótese" not in resposta.lower():
        erros.append(f"Usa linguagem de causa ('{m.group(0)}') sem a palavra 'hipótese'.")
    baixo = resposta.lower()
    for termo in JARGAO_PROIBIDO:
        if termo.lower() in baixo:
            erros.append(f"Contém o termo técnico proibido '{termo}'.")
    for ref in RE_CODIGO_INDICADOR.findall(resposta):
        erros.append(f"Cita o indicador pelo código ('{ref}'); use o nome do indicador.")
    if pergunta == PERGUNTA_ACAO and not any(h in resposta for h in HORIZONTES):
        erros.append("A resposta deve indicar o horizonte, escrito exatamente como "
                     + " ou ".join(f"'{h}'" for h in HORIZONTES) + ".")
    return erros


def validar_lote(lista: list, pendentes: dict[str, str], frases: dict[str, str]
                 ) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """Valida as respostas das perguntas pendentes.

    Returns:
        (aprovadas por id, erros por id). Pergunta pendente sem resposta conta como erro.
    """
    aprovadas: dict[str, dict] = {}
    erros: dict[str, list[str]] = {}
    por_id = {str(i.get("id")): i for i in lista if isinstance(i, dict)}
    for pid, pergunta in pendentes.items():
        item = por_id.get(pid)
        if item is None:
            erros[pid] = ["Pergunta sem resposta no JSON."]
            continue
        e = validar_resposta(item, pergunta, frases)
        if e:
            erros[pid] = e
        else:
            aprovadas[pid] = {"id": pid, "pergunta": pergunta, "resposta": item["resposta"].strip(),
                              "fatos": list(item["fatos"])}
    return aprovadas, erros


# ---------------------------------------------------------------- saída
def fonte_legivel(fonte: dict[str, Any] | None) -> str:
    """Fonte do fato em texto para o leitor (padrão dos KPIs de ``src.produto``)."""
    if not isinstance(fonte, dict):
        return "Fonte: dados oficiais do SENAI"
    arquivo = str(fonte.get("arquivo", ""))
    paginas = ", ".join(map(str, fonte.get("pagina_pdf") or []))
    if fonte.get("tipo") == "pdf":
        return f"Fonte: Relatório de Gestão {mod_fatos.ANO_FECHADO}" + (f", p. {paginas} do PDF" if paginas else "")
    if fonte.get("tipo") == "regra" or "qualidade" in arquivo:
        return "Fonte: verificação de qualidade dos dados do Portal da Transparência"
    if "cruzamento" in arquivo:
        return FONTE_CRUZ
    if "projecao" in arquivo or f"/pes/{mod_fatos.ANO_PROJECAO}/" in arquivo:
        return (f"Fonte: Plano Estratégico Sistêmico {mod_fatos.ANO_PROJECAO} (Portal da Transparência); "
                "projeção para dezembro, no ritmo atual")
    sufixo_pdf = f"; confirmado no Relatório de Gestão, p. {paginas} do PDF" if paginas else ""
    return FONTE_PES + sufixo_pdf


def publicar(aprovadas: dict[str, dict], fatos: list[dict], escopo: str) -> list[dict]:
    """Respostas na ordem do catálogo, com texto e fonte de cada fato citado."""
    por_id = {f["id"]: f for f in fatos}
    saida = []
    for pid, _ in catalogo(escopo):
        if pid not in aprovadas:
            continue
        r = aprovadas[pid]
        saida.append({
            **r,
            "fatos": [{"id": f, "texto": por_id[f]["frase"], "fonte": fonte_legivel(por_id[f].get("fonte"))}
                      for f in r["fatos"]],
        })
    return saida


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "item"):
        return obj.item()
    raise TypeError(f"Tipo não serializável: {type(obj)}")


# ---------------------------------------------------------------- orquestração
def gerar_perguntas(
    escopo: str,
    cliente: Any,
    modelo: str = MODELO_PADRAO,
    outputs_dir: Path = OUTPUTS_DIR,
    app_data_dir: Path = APP_DATA_DIR,
    logs_dir: Path = LOGS_DIR,
    prompt_path: Path = PROMPT_PATH,
) -> dict:
    """Gera, valida (com reenvio) e salva as respostas do catálogo do escopo.

    Returns:
        O documento salvo em ``app_data/perguntas_{escopo}.json``.
    """
    import anthropic  # import tardio: o --dry-run e os testes não dependem da rede

    escopo = escopo.upper()
    fatos, fatos_path = carregar_ficha(escopo, outputs_dir)
    frases = {str(f["id"]): str(f.get("frase", "")) for f in fatos}
    system = prompt_path.read_text(encoding="utf-8")
    pendentes = dict(catalogo(escopo))
    mensagens: list[dict] = [{"role": "user", "content": montar_mensagem(fatos, escopo, list(pendentes.items()))}]

    aprovadas: dict[str, dict] = {}
    tentativas: list[dict] = []
    for n in range(1, MAX_TENTATIVAS + 1):
        registro: dict[str, Any] = {"tentativa": n, "pendentes": list(pendentes), "resposta_bruta": None,
                                    "erros": {}, "uso": {}}
        tentativas.append(registro)
        try:
            resposta = _chamar(cliente, modelo, system, mensagens)
        except anthropic.APIError as exc:
            registro["erros"] = {"_api": [f"Falha na chamada à API: {type(exc).__name__}: {exc}"]}
            logger.warning("Tentativa %d: falha na API.", n)
            continue
        texto = _texto_resposta(resposta)
        registro.update(resposta_bruta=texto, uso=_uso(resposta),
                        stop_reason=getattr(resposta, "stop_reason", None))

        if registro["stop_reason"] == "refusal":
            erros = {pid: ["O modelo recusou a solicitação."] for pid in pendentes}
        else:
            try:
                novas, erros = validar_lote(extrair_lista(texto), pendentes, frases)
                aprovadas.update(novas)
            except ValueError as exc:
                erros = {pid: [f"Formato: {exc}."] for pid in pendentes}
        registro["erros"] = erros
        pendentes = {pid: p for pid, p in pendentes.items() if pid in erros}
        logger.info("Tentativa %d: %d aprovada(s), %d pendente(s).", n, len(aprovadas), len(pendentes))
        if not pendentes:
            break
        mensagens = mensagens + [
            {"role": "assistant", "content": resposta.content},
            {"role": "user", "content": mensagem_correcao(erros)},
        ]

    if pendentes:
        logger.warning("Fora do catálogo (não validadas): %s.", ", ".join(pendentes))

    agora = datetime.now(timezone.utc)
    doc = {
        "escopo": escopo,
        "modelo": modelo,
        "versao_prompt": VERSAO_PROMPT,
        "gerado_em_utc": agora.isoformat(timespec="seconds"),
        "perguntas": publicar(aprovadas, fatos, escopo),
        "removidas": list(pendentes),
    }
    app_data_dir.mkdir(parents=True, exist_ok=True)
    saida = app_data_dir / f"perguntas_{escopo}.json"
    saida.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")

    uso_total: dict[str, int] = defaultdict(int)
    for t in tentativas:
        for k, v in t["uso"].items():
            uso_total[k] += v
    log = {
        "escopo": escopo, "modelo": modelo, "versao_prompt": VERSAO_PROMPT,
        "gerado_em_utc": doc["gerado_em_utc"],
        "prompt_sha256": sha256_arquivo(prompt_path),
        "fatos_sha256": sha256_arquivo(fatos_path),
        "saida_sha256": sha256_arquivo(saida),
        "aprovadas": sorted(aprovadas), "removidas": doc["removidas"],
        "tentativas": tentativas, "uso_total": dict(uso_total),
    }
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"perguntas_{escopo}_{agora.strftime('%Y%m%dT%H%M%SZ')}.json"
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Perguntas salvas em %s (%d publicadas); log em %s.", saida, len(doc["perguntas"]), log_path)
    return doc


def dry_run(escopo: str, outputs_dir: Path = OUTPUTS_DIR, prompt_path: Path = PROMPT_PATH) -> dict[str, int]:
    """Monta a mensagem e estima o tamanho, sem chamar a API."""
    escopo = escopo.upper()
    fatos, _ = carregar_ficha(escopo, outputs_dir)
    system = prompt_path.read_text(encoding="utf-8")
    mensagem = montar_mensagem(fatos, escopo, list(catalogo(escopo)))
    info = {"n_fatos": len(fatos), "n_perguntas": len(catalogo(escopo)), "chars_system": len(system),
            "chars_mensagem": len(mensagem), "tokens_estimados": (len(system) + len(mensagem)) // CHARS_POR_TOKEN}
    logger.info("Dry-run %s: %d fatos, %d perguntas, ~%d tokens de entrada. API não chamada.",
                escopo, info["n_fatos"], info["n_perguntas"], info["tokens_estimados"])
    return info


def main(argv: list[str] | None = None) -> None:
    """Ponto de entrada de linha de comando."""
    parser = argparse.ArgumentParser(description="Gera as respostas do 'Pergunte ao briefing'.")
    parser.add_argument("--escopo", required=True, help='"BR" ou sigla do DR')
    parser.add_argument("--dry-run", action="store_true", help="monta a mensagem sem chamar a API")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    escopo = args.escopo.upper()
    if args.dry_run:
        dry_run(escopo)
        return

    import anthropic
    from dotenv import load_dotenv

    load_dotenv(ENV_PATH)  # define ANTHROPIC_API_KEY sem expor o valor
    gerar_perguntas(escopo, anthropic.Anthropic(), modelo=os.getenv("LLM_MODEL", MODELO_PADRAO))


if __name__ == "__main__":
    main()
