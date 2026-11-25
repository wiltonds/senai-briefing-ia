"""Redator: gera o briefing executivo com a IA a partir da ficha de fatos.

Fluxo: lê ``outputs/fatos_{escopo}.json`` e ``prompts/briefing_{versao}.md`` (system,
padrão v2),
envia os fatos (id, categoria, frase) entre ``<fatos>`` e ``</fatos>``, extrai o
JSON da resposta e valida com ``src.validador``. Se houver erros, reenvia a lista
pedindo correção (no máximo 3 tentativas). Se todas falharem, entra em "modo
contingência": briefing sem texto da IA, só com as frases dos fatos agrupadas
por categoria.

Saídas:
- ``outputs/briefing_{escopo}.json`` (status "aprovado" ou "contingencia");
- ``logs/geracao_{escopo}_{timestamp}.json`` (hashes, tentativas, tokens).

Uso:
    python -m src.redator --escopo BR
    python -m src.redator --escopo BR --dry-run
    python -m src.redator --escopo BR --prompt v1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.validador import validar

logger = logging.getLogger("redator")

RAIZ = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = RAIZ / "outputs"
LOGS_DIR = RAIZ / "logs"
PROMPTS_DIR = RAIZ / "prompts"
VERSAO_PROMPT_PADRAO = "v2"
ENV_PATH = RAIZ / ".env"

MODELO_PADRAO = "claude-sonnet-5-5"
MAX_TOKENS = 6000
MAX_TENTATIVAS = 3
CHARS_POR_TOKEN = 4  # estimativa grosseira para o --dry-run

# Modelos que aceitam o fallback de recusa no servidor (fallbacks="default").
BETA_FALLBACK = "server-side-fallback-2026-07-01"
MODELOS_COM_FALLBACK = frozenset(
    {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-fable-5-2"}
)

AVISO_CONTINGENCIA = (
    "Briefing em modo contingência: a redação automática não passou na validação. "
    "O conteúdo abaixo reproduz as frases da ficha de fatos, sem texto da IA."
)


# ---------------------------------------------------------------- entradas
def versao_prompt(caminho: Path) -> str:
    """Versão do prompt a partir do nome do arquivo (``briefing_v1.md`` -> ``v1``)."""
    m = re.search(r"_(v\d+)$", caminho.stem)
    return m.group(1) if m else caminho.stem


def caminho_prompt(versao: str, prompts_dir: Path = PROMPTS_DIR) -> Path:
    """Caminho do prompt da versão (``v2`` -> ``prompts/briefing_v2.md``).

    Raises:
        FileNotFoundError: se o arquivo da versão não existir.
    """
    caminho = prompts_dir / f"briefing_{versao}.md"
    if not caminho.is_file():
        raise FileNotFoundError(f"prompt da versão '{versao}' não encontrado: {caminho}")
    return caminho


PROMPT_PATH = PROMPTS_DIR / f"briefing_{VERSAO_PROMPT_PADRAO}.md"


def sha256_arquivo(caminho: Path) -> str:
    """Hash sha256 do conteúdo do arquivo."""
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def carregar_fatos(caminho: Path) -> list[dict]:
    """Lê a lista de fatos da ficha JSON."""
    with caminho.open(encoding="utf-8") as fh:
        return json.load(fh)["fatos"]


def montar_mensagem(fatos: list[dict], escopo: str) -> str:
    """Mensagem do usuário com os fatos (id, categoria, frase) entre <fatos>."""
    linhas = [
        json.dumps({"id": f["id"], "categoria": f["categoria"], "frase": f["frase"]}, ensure_ascii=False)
        for f in fatos
    ]
    return (
        f"Escopo do briefing: {escopo}.\n\n"
        "<fatos>\n" + "\n".join(linhas) + "\n</fatos>\n\n"
        "Redija o briefing seguindo as regras e a estrutura. Responda somente com o JSON."
    )


def mensagem_correcao(erros: list[str]) -> str:
    """Pedido de correção com a lista de erros do validador."""
    return (
        "O briefing não passou na validação. Corrija todos os problemas abaixo e devolva "
        "o JSON completo, sem texto fora do JSON:\n" + "\n".join(f"- {e}" for e in erros)
    )


# ---------------------------------------------------------------- resposta
def extrair_json(texto: str) -> dict:
    """Extrai o objeto JSON da resposta (tolera cercas ``` e texto em volta).

    Raises:
        ValueError: se não houver um objeto JSON válido.
    """
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio == -1 or fim <= inicio:
        raise ValueError("a resposta não contém um objeto JSON")
    try:
        obj = json.loads(texto[inicio : fim + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido na resposta ({exc.msg}, posição {exc.pos})") from exc
    if not isinstance(obj, dict):
        raise ValueError("o JSON da resposta não é um objeto")
    return obj


def _texto_resposta(resposta: Any) -> str:
    return "".join(getattr(b, "text", "") for b in resposta.content if getattr(b, "type", "") == "text")


def _uso(resposta: Any) -> dict[str, int]:
    uso = getattr(resposta, "usage", None)
    campos = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    return {c: int(getattr(uso, c, 0) or 0) for c in campos}


def _chamar(cliente: Any, modelo: str, system: str, mensagens: list[dict]) -> Any:
    """Uma chamada à API; usa fallback de recusa no servidor quando o modelo aceita."""
    params: dict[str, Any] = {
        "model": modelo,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "messages": mensagens,
    }
    if modelo in MODELOS_COM_FALLBACK:
        return cliente.beta.messages.create(betas=[BETA_FALLBACK], fallbacks="default", **params)
    return cliente.messages.create(**params)


# ---------------------------------------------------------------- contingência
def briefing_contingencia(fatos: list[dict]) -> dict[str, list[dict]]:
    """Frases dos fatos agrupadas por categoria (ordem original preservada)."""
    grupos: dict[str, list[dict]] = defaultdict(list)
    for f in fatos:
        grupos[f["categoria"]].append({"id": f["id"], "frase": f["frase"]})
    return dict(grupos)


# ---------------------------------------------------------------- orquestração
def gerar_briefing(
    escopo: str,
    cliente: Any,
    modelo: str = MODELO_PADRAO,
    outputs_dir: Path = OUTPUTS_DIR,
    logs_dir: Path = LOGS_DIR,
    prompt_path: Path = PROMPT_PATH,
) -> dict:
    """Gera, valida (com reenvio) e salva o briefing do escopo.

    Args:
        escopo: "BR" ou sigla do DR.
        cliente: cliente ``anthropic.Anthropic`` (ou falso, nos testes).
        modelo: ID do modelo.
        outputs_dir, logs_dir, prompt_path: caminhos (injetáveis para testes).

    Returns:
        O documento salvo em ``outputs/briefing_{escopo}.json``.
    """
    import anthropic  # import tardio: o --dry-run e os testes não dependem da rede

    fatos_path = outputs_dir / f"fatos_{escopo}.json"
    fatos = carregar_fatos(fatos_path)
    system = prompt_path.read_text(encoding="utf-8")
    logger.info("Escopo %s: modelo %s, prompt %s.", escopo, modelo, versao_prompt(prompt_path))
    mensagens: list[dict] = [{"role": "user", "content": montar_mensagem(fatos, escopo)}]

    tentativas: list[dict] = []
    aprovado: dict | None = None
    for n in range(1, MAX_TENTATIVAS + 1):
        registro: dict[str, Any] = {"tentativa": n, "resposta_bruta": None, "erros": [], "uso": {}}
        tentativas.append(registro)
        try:
            resposta = _chamar(cliente, modelo, system, mensagens)
        except anthropic.APIError as exc:
            registro["erros"] = [f"Falha na chamada à API: {type(exc).__name__}: {exc}"]
            logger.warning("Tentativa %d: %s", n, registro["erros"][0])
            continue

        texto = _texto_resposta(resposta)
        registro["resposta_bruta"] = texto
        registro["uso"] = _uso(resposta)
        registro["stop_reason"] = getattr(resposta, "stop_reason", None)

        if registro["stop_reason"] == "refusal":
            erros = ["O modelo recusou a solicitação."]
        elif registro["stop_reason"] == "max_tokens":
            erros = [f"Resposta cortada no limite de {MAX_TOKENS} tokens; seja mais conciso."]
        else:
            try:
                briefing = extrair_json(texto)
                erros = validar(briefing, fatos)
            except ValueError as exc:
                erros = [f"Formato: {exc}."]
        registro["erros"] = erros

        if not erros:
            aprovado = briefing
            logger.info("Tentativa %d: briefing aprovado.", n)
            break
        logger.warning("Tentativa %d: %d erro(s) de validação.", n, len(erros))
        mensagens = mensagens + [
            {"role": "assistant", "content": resposta.content},
            {"role": "user", "content": mensagem_correcao(erros)},
        ]

    agora = datetime.now(timezone.utc)
    doc: dict[str, Any] = {
        "escopo": escopo,
        "status": "aprovado" if aprovado is not None else "contingencia",
        "modelo": modelo,
        "versao_prompt": versao_prompt(prompt_path),
        "gerado_em_utc": agora.isoformat(timespec="seconds"),
        "tentativas": len(tentativas),
    }
    if aprovado is not None:
        doc["briefing"] = aprovado
    else:
        logger.error("As %d tentativas falharam: modo contingência.", MAX_TENTATIVAS)
        doc["aviso"] = AVISO_CONTINGENCIA
        doc["briefing"] = None
        doc["fatos_por_categoria"] = briefing_contingencia(fatos)

    outputs_dir.mkdir(parents=True, exist_ok=True)
    saida = outputs_dir / f"briefing_{escopo}.json"
    saida.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    uso_total = defaultdict(int)
    for t in tentativas:
        for k, v in t["uso"].items():
            uso_total[k] += v
    log = {
        "escopo": escopo,
        "status": doc["status"],
        "modelo": modelo,
        "versao_prompt": doc["versao_prompt"],
        "gerado_em_utc": doc["gerado_em_utc"],
        "prompt_sha256": sha256_arquivo(prompt_path),
        "fatos_sha256": sha256_arquivo(fatos_path),
        "tentativas": tentativas,
        "uso_total": dict(uso_total),
    }
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"geracao_{escopo}_{agora.strftime('%Y%m%dT%H%M%SZ')}.json"
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(
        "Briefing salvo em %s (status %s, prompt %s); log em %s.",
        saida, doc["status"], doc["versao_prompt"], log_path,
    )
    return doc


def dry_run(escopo: str, outputs_dir: Path = OUTPUTS_DIR, prompt_path: Path = PROMPT_PATH) -> dict[str, int]:
    """Monta a mensagem e estima o tamanho, sem chamar a API."""
    fatos = carregar_fatos(outputs_dir / f"fatos_{escopo}.json")
    system = prompt_path.read_text(encoding="utf-8")
    mensagem = montar_mensagem(fatos, escopo)
    chars = len(system) + len(mensagem)
    info = {"n_fatos": len(fatos), "chars_system": len(system), "chars_mensagem": len(mensagem),
            "tokens_estimados": chars // CHARS_POR_TOKEN}
    logger.info(
        "Dry-run %s (prompt %s): %d fatos; system %d caracteres; mensagem %d caracteres; ~%d tokens de entrada "
        "(estimativa %d caracteres/token). API não chamada.",
        escopo, versao_prompt(prompt_path), info["n_fatos"], info["chars_system"], info["chars_mensagem"],
        info["tokens_estimados"], CHARS_POR_TOKEN,
    )
    return info


def main(argv: list[str] | None = None) -> None:
    """Ponto de entrada de linha de comando."""
    parser = argparse.ArgumentParser(description="Gera o briefing executivo com a IA.")
    parser.add_argument("--escopo", required=True, help='"BR" ou sigla do DR')
    parser.add_argument("--dry-run", action="store_true", help="monta a mensagem sem chamar a API")
    parser.add_argument(
        "--prompt", default=VERSAO_PROMPT_PADRAO,
        help=f'versão do prompt (lê prompts/briefing_{{versao}}.md; padrão "{VERSAO_PROMPT_PADRAO}")',
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    escopo = args.escopo.upper()
    try:
        prompt_path = caminho_prompt(args.prompt)
    except FileNotFoundError as exc:
        parser.error(str(exc))

    if args.dry_run:
        dry_run(escopo, prompt_path=prompt_path)
        return

    import anthropic
    from dotenv import load_dotenv

    load_dotenv(ENV_PATH)  # define ANTHROPIC_API_KEY sem expor o valor
    modelo = os.getenv("LLM_MODEL", MODELO_PADRAO)
    gerar_briefing(escopo, anthropic.Anthropic(), modelo=modelo, prompt_path=prompt_path)


if __name__ == "__main__":
    main()
