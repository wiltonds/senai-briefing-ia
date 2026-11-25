"""Coleta de dados públicos do Portal da Transparência do SENAI.

Baixa as respostas JSON brutas das APIs públicas (somente GET) do domínio
sistematransparenciaweb.com.br e as salva em ``data/raw/{fonte}/{ano}/{dr}.json``,
mantendo um ``manifest.json`` com metadados de cada coleta.

Este módulo NÃO transforma, converte ou limpa os dados: isso é responsabilidade
de outro módulo.

Uso:
    python -m src.coleta --anos 2025 2026 --fontes pes atendimento
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("coleta")

BASE_URL = "https://sistematransparenciaweb.com.br"

ENDPOINTS: dict[str, str] = {
    "pes": (
        BASE_URL
        + "/api-planejamento-estrategico/publico/planejamento-estrategico/sistemico"
        "?ano={ano}&departamento=SENAI-{dr}&entidade=SENAI"
    ),
    "atendimento": (
        BASE_URL
        + "/api-atendimento-social/publico/atendimento-social"
        "?ano={ano}&departamento=SENAI-{dr}&entidade=SENAI"
    ),
}

DRS: list[str] = [
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE",
    "TO", "DN",
]

RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"
MANIFEST_PATH = RAW_DIR / "manifest.json"

USER_AGENT = "senai-briefing-ia/0.1 (coleta de dados publicos; Python requests)"
TIMEOUT_S = 30
MAX_TENTATIVAS = 3
BACKOFF_BASE_S = 1.0
PAUSA_ENTRE_CHAMADAS_S = 0.3

CAMPOS_PES = ("numeroIndicador", "indicador", "objetivosEstrategicos")


class ErroColeta(Exception):
    """Falha definitiva ao obter uma resposta HTTP após as tentativas."""

    def __init__(self, mensagem: str, status_http: int | None = None) -> None:
        super().__init__(mensagem)
        self.status_http = status_http


@dataclass
class RegistroManifest:
    """Metadados de uma coleta, gravados no manifest."""

    url: str
    coletado_em_utc: str
    sha256: str | None
    status_http: int | None
    num_registros: int | None
    erro: str | None = None


def montar_url(fonte: str, ano: int, dr: str) -> str:
    """Monta a URL do endpoint para a fonte, ano e DR informados."""
    return ENDPOINTS[fonte].format(ano=ano, dr=dr)


def baixar(sessao: requests.Session, url: str) -> requests.Response:
    """Faz GET com até ``MAX_TENTATIVAS`` tentativas e backoff exponencial.

    Repete em caso de erro de rede ou HTTP 5xx. Respostas 4xx não são repetidas.
    Levanta ``ErroColeta`` se não for possível obter uma resposta 2xx.
    """
    ultimo_erro = ""
    ultimo_status: int | None = None
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            resp = sessao.get(url, timeout=TIMEOUT_S)  # verify=True é o padrão
        except requests.RequestException as exc:
            ultimo_erro = f"erro de rede: {exc}"
            ultimo_status = None
        else:
            if resp.status_code < 400:
                return resp
            ultimo_status = resp.status_code
            ultimo_erro = f"HTTP {resp.status_code}"
            if resp.status_code < 500:
                raise ErroColeta(ultimo_erro, ultimo_status)

        if tentativa < MAX_TENTATIVAS:
            espera = BACKOFF_BASE_S * 2 ** (tentativa - 1)
            logger.warning(
                "Tentativa %d/%d falhou (%s). Nova tentativa em %.1fs: %s",
                tentativa, MAX_TENTATIVAS, ultimo_erro, espera, url,
            )
            time.sleep(espera)

    raise ErroColeta(
        f"{ultimo_erro} após {MAX_TENTATIVAS} tentativas", ultimo_status
    )


def validar_esquema(fonte: str, dados: Any) -> int:
    """Valida o esquema mínimo da resposta e retorna o número de registros.

    - ``pes``: lista em que cada item possui os campos de ``CAMPOS_PES``.
    - ``atendimento``: objeto com a chave ``temas``.

    Levanta ``ValueError`` com a descrição do problema se a validação falhar.
    """
    if fonte == "pes":
        if not isinstance(dados, list):
            raise ValueError(f"esperada lista, recebido {type(dados).__name__}")
        for i, item in enumerate(dados):
            if not isinstance(item, dict):
                raise ValueError(f"item {i} não é objeto")
            faltando = [c for c in CAMPOS_PES if c not in item]
            if faltando:
                raise ValueError(f"item {i} sem campos: {', '.join(faltando)}")
        return len(dados)

    if fonte == "atendimento":
        if not isinstance(dados, dict) or "temas" not in dados:
            raise ValueError("chave 'temas' ausente")
        # Conta as ações finalísticas de todas as linhas de negócio de cada tema.
        try:
            return sum(
                len(linha["acoesFinalisticas"])
                for tema in dados["temas"].values()
                for linha in tema["linhasNegocio"].values()
            )
        except (AttributeError, KeyError, TypeError) as exc:
            raise ValueError(
                f"estrutura de temas/linhasNegocio/acoesFinalisticas inesperada: {exc!r}"
            ) from exc

    raise ValueError(f"fonte desconhecida: {fonte}")


def carregar_manifest() -> dict[str, dict[str, Any]]:
    """Lê o manifest existente (ou retorna vazio se não existir)."""
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {}


def salvar_manifest(manifest: dict[str, dict[str, Any]]) -> None:
    """Grava o manifest de forma atômica (arquivo temporário + rename)."""
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(MANIFEST_PATH)


def coletar_um(
    sessao: requests.Session, fonte: str, ano: int, dr: str
) -> tuple[str, RegistroManifest]:
    """Coleta uma combinação fonte/ano/DR e salva o JSON bruto.

    O arquivo é gravado byte a byte como recebido, sempre que a resposta HTTP
    for bem-sucedida, mesmo que a validação de esquema falhe (o erro fica no
    manifest). Retorna a chave do manifest (caminho relativo) e o registro.
    """
    url = montar_url(fonte, ano, dr)
    destino = RAW_DIR / fonte / str(ano) / f"{dr}.json"
    chave = destino.relative_to(RAW_DIR).as_posix()
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        resp = baixar(sessao, url)
    except ErroColeta as exc:
        logger.error("Falha em %s: %s", chave, exc)
        return chave, RegistroManifest(url, agora, None, exc.status_http, None, str(exc))

    conteudo = resp.content
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(conteudo)
    registro = RegistroManifest(
        url=url,
        coletado_em_utc=agora,
        sha256=hashlib.sha256(conteudo).hexdigest(),
        status_http=resp.status_code,
        num_registros=None,
    )

    try:
        registro.num_registros = validar_esquema(fonte, json.loads(conteudo))
    except json.JSONDecodeError as exc:
        registro.erro = f"JSON inválido: {exc}"
    except ValueError as exc:
        registro.erro = f"esquema inválido: {exc}"

    if registro.erro is None and registro.num_registros == 0:
        registro.erro = "resposta vazia (0 registros)"
        logger.warning("%s: %s", chave, registro.erro)
    elif registro.erro:
        logger.error("Validação falhou em %s: %s", chave, registro.erro)
    else:
        logger.info("OK %s (%d registros)", chave, registro.num_registros)
    return chave, registro


def coletar(anos: list[int], fontes: list[str], drs: list[str] = DRS) -> int:
    """Executa a coleta para todas as combinações e atualiza o manifest.

    O manifest é salvo após cada arquivo, para não perder progresso se a
    execução for interrompida. Retorna a quantidade de coletas com erro.
    """
    manifest = carregar_manifest()
    erros = 0
    primeira = True
    with requests.Session() as sessao:
        sessao.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        for fonte in fontes:
            for ano in anos:
                for dr in drs:
                    if not primeira:
                        time.sleep(PAUSA_ENTRE_CHAMADAS_S)
                    primeira = False
                    chave, registro = coletar_um(sessao, fonte, ano, dr)
                    manifest[chave] = asdict(registro)
                    salvar_manifest(manifest)
                    erros += registro.erro is not None
    logger.info("Coleta concluída com %d erro(s). Manifest: %s", erros, MANIFEST_PATH)
    return erros


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada da linha de comando."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--anos", type=int, nargs="+", required=True)
    parser.add_argument(
        "--fontes", nargs="+", choices=list(ENDPOINTS), default=list(ENDPOINTS)
    )
    parser.add_argument(
        "--drs", nargs="+", choices=DRS, default=DRS,
        help="Subconjunto de DRs (padrão: todos os 27 DRs + DN)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    erros = coletar(args.anos, args.fontes, args.drs)
    return 1 if erros else 0


if __name__ == "__main__":
    raise SystemExit(main())
