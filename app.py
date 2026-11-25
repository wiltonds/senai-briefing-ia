"""App Gradio: Inteligência de Gestão SENAI — Briefing com IA verificada.

Abas: Briefing, Painel, Auditoria da IA, Teste o validador, Qualidade dos dados.
Reutiliza as funções de ``src/`` (fatos, redator, render_pdf, validador); este
módulo só lê arquivos já salvos e orquestra a geração.

Segurança:
- ``launch`` em 127.0.0.1 (fora do Hugging Face).
- Geração com IA só com ``ALLOW_LLM=1`` (checado no servidor, não só no botão)
  e com ``concurrency_limit=1``.
- ``SHARE=1`` exige ``APP_USER``/``APP_PASS``; sem eles o app não inicia.
- Hugging Face (``SPACE_ID``): só dados já salvos; IA desligada por padrão e,
  se ligada, exige autenticação.
- O ``.env`` é carregado só em variáveis de ambiente e bloqueado para servir.

CLI: ``python app.py``
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gradio as gr
import pandas as pd

from src import fatos as mod_fatos
from src import redator, render_pdf
from src.validador import UFS, validar

logger = logging.getLogger("app")

RAIZ = Path(__file__).resolve().parent
OUTPUTS_DIR = RAIZ / "outputs"
LOGS_DIR = RAIZ / "logs"
PROCESSED_DIR = RAIZ / "data" / "processed"
FIGS_DIR = OUTPUTS_DIR / "figs"
ENV_PATH = RAIZ / ".env"
ENCODING_CSV = "utf-8-sig"

TITULO = "Inteligência de Gestão SENAI — Briefing com IA verificada"
ESCOPOS: tuple[str, ...] = ("BR",) + UFS
AVISO_LLM_OFF = "geração com IA desativada neste ambiente"
TODAS_REGRAS = "Todas"
ICONE_FAROL = {"atingido": "🟢 atingido", "atenção": "🟡 atenção", "crítico": "🔴 crítico"}
_LOCK_GRAFICOS = threading.Lock()
SECOES = (
    ("destaques", "Destaques", "texto"),
    ("pontos_atencao", "Pontos de atenção", "texto"),
    ("recomendacoes", "Recomendações", "acao"),
    ("limitacoes", "Limitações", "texto"),
)


# ---------------------------------------------------------------- configuração
class ErroConfiguracao(Exception):
    """Configuração de ambiente insegura ou incompleta."""


@dataclass(frozen=True)
class Config:
    """Modo de execução derivado das variáveis de ambiente."""

    allow_llm: bool
    share: bool
    hf: bool
    auth: tuple[str, str] | None


def configurar(env: Mapping[str, str]) -> Config:
    """Lê ALLOW_LLM, SHARE, SPACE_ID, APP_USER e APP_PASS.

    ALLOW_LLM só liga com o valor exato "1" (no Hugging Face também, portanto
    desligado por padrão). Autenticação é exigida com SHARE=1 ou com IA ligada
    no Hugging Face.

    Raises:
        ErroConfiguracao: autenticação exigida sem APP_USER/APP_PASS.
    """
    hf = bool(env.get("SPACE_ID"))
    allow_llm = env.get("ALLOW_LLM", "0").strip() == "1"
    share = env.get("SHARE", "0").strip() == "1"
    usuario = env.get("APP_USER", "").strip()
    senha = env.get("APP_PASS", "")
    auth = (usuario, senha) if usuario and senha else None
    if (share or (hf and allow_llm)) and auth is None:
        motivo = "SHARE=1" if share else "ALLOW_LLM=1 no Hugging Face"
        raise ErroConfiguracao(f"{motivo} exige APP_USER e APP_PASS definidos; o app não será iniciado.")
    return Config(allow_llm=allow_llm, share=share, hf=hf, auth=auth)


def checar_escopo(escopo: str) -> str:
    """Normaliza e valida o escopo (impede caminhos arbitrários vindos da API)."""
    escopo = str(escopo or "").strip().upper()
    if escopo not in ESCOPOS:
        raise ValueError(f"Escopo inválido: {escopo!r}.")
    return escopo


# ---------------------------------------------------------------- leitura de arquivos salvos
def _ler_json(caminho: Path) -> dict | None:
    if not caminho.exists():
        return None
    return json.loads(caminho.read_text(encoding="utf-8"))


def carregar_briefing(escopo: str, outputs_dir: Path = OUTPUTS_DIR) -> dict | None:
    """Documento ``outputs/briefing_{escopo}.json`` ou None."""
    return _ler_json(outputs_dir / f"briefing_{escopo}.json")


def carregar_lista_fatos(escopo: str, outputs_dir: Path = OUTPUTS_DIR) -> list[dict]:
    """Lista de fatos de ``outputs/fatos_{escopo}.json`` (vazia se ausente)."""
    doc = _ler_json(outputs_dir / f"fatos_{escopo}.json")
    return doc.get("fatos", []) if doc else []


def caminho_pdf(escopo: str, outputs_dir: Path = OUTPUTS_DIR) -> Path | None:
    """PDF salvo do escopo, se existir."""
    p = outputs_dir / f"Executive_Briefing_SENAI_{escopo}.pdf"
    return p if p.exists() else None


def ultimo_log(escopo: str, logs_dir: Path = LOGS_DIR) -> tuple[Path, dict] | None:
    """Log de geração mais recente do escopo (o nome traz o carimbo UTC ordenável)."""
    logs = sorted(logs_dir.glob(f"geracao_{escopo}_*.json"))
    if not logs:
        return None
    return logs[-1], json.loads(logs[-1].read_text(encoding="utf-8"))


def ler_csv(nome: str, processed_dir: Path = PROCESSED_DIR) -> pd.DataFrame:
    """Lê uma tabela de ``data/processed`` ("-" permanece texto, nunca zero)."""
    return pd.read_csv(processed_dir / f"{nome}.csv", encoding=ENCODING_CSV,
                       dtype={"cod_indicador": str, "item": str, "uf": str})


# ---------------------------------------------------------------- aba Briefing
def _ids(item: dict) -> str:
    ids = item.get("fatos") or []
    return f" `[{', '.join(map(str, ids))}]`" if ids else ""


def markdown_briefing(doc: dict | None) -> str:
    """Status, tentativas, mensagem central, destaques, atenção, recomendações e limitações."""
    if not doc:
        return "_Nenhum briefing salvo para este escopo._"
    aprovado = doc.get("status") == "aprovado"
    status = "✅ aprovado" if aprovado else "⚠️ contingência"
    linhas = [
        f"**Escopo:** {doc.get('escopo', '-')} · **Status:** {status} · "
        f"**Tentativas:** {doc.get('tentativas', '-')} · **Modelo:** {doc.get('modelo', '-')} · "
        f"**Prompt:** {doc.get('versao_prompt', '-')} · **Gerado em (UTC):** {doc.get('gerado_em_utc', '-')}",
        "",
    ]
    b = doc.get("briefing")
    if not aprovado or not b:
        linhas += [f"> {doc.get('aviso', 'Briefing sem texto aprovado da IA.')}", ""]
        for cat, itens in (doc.get("fatos_por_categoria") or {}).items():
            linhas.append(f"### {cat}")
            linhas += [f"- {i['frase']} `[{i['id']}]`" for i in itens]
            linhas.append("")
        return "\n".join(linhas)

    mc = b.get("mensagem_central", {})
    linhas += ["### Mensagem central", f"{mc.get('texto', '')}{_ids(mc)}", ""]
    for chave, titulo, campo in SECOES:
        itens = b.get(chave) or []
        if not itens:
            continue
        linhas.append(f"### {titulo}")
        for it in itens:
            cab = f"**{it['titulo']}** — " if it.get("titulo") else ""
            extra = ""
            if chave == "recomendacoes":
                extra = f" _(responsável: {it.get('responsavel', '-')}; horizonte: {it.get('horizonte', '-')})_"
            linhas.append(f"- {cab}{it.get(campo, '')}{extra}{_ids(it)}")
        linhas.append("")
    return "\n".join(linhas)


def mostrar_briefing(escopo: str) -> tuple[str, str | None]:
    """Markdown do último briefing salvo e o PDF salvo do escopo."""
    escopo = checar_escopo(escopo)
    pdf = caminho_pdf(escopo)
    return markdown_briefing(carregar_briefing(escopo)), str(pdf) if pdf else None


def _exigir_revisor(revisado_por: str) -> str:
    nome = str(revisado_por or "").strip()
    if not nome:
        raise gr.Error("Preencha o campo 'Revisado por' para gerar o PDF.")
    return nome


def gerar_pdf_salvo(escopo: str, revisado_por: str) -> tuple[str, str | None]:
    """Gera o PDF a partir do briefing já salvo (sem chamar a IA)."""
    escopo = checar_escopo(escopo)
    nome = _exigir_revisor(revisado_por)
    if carregar_briefing(escopo) is None:
        raise gr.Error(f"Não há briefing salvo para {escopo}.")
    try:
        pdf = render_pdf.gerar_pdf(escopo, nome, permitir_contingencia=True)
    except render_pdf.ErroRenderizacao as exc:
        raise gr.Error(str(exc)) from exc
    return markdown_briefing(carregar_briefing(escopo)), str(pdf)


def gerar_com_ia(escopo: str, revisado_por: str, cfg: Config) -> tuple[str, str | None]:
    """Pipeline fatos -> redator -> render_pdf (só com ALLOW_LLM=1)."""
    if not cfg.allow_llm:  # checagem no servidor: o botão desativado não basta
        raise gr.Error(AVISO_LLM_OFF)
    escopo = checar_escopo(escopo)
    nome = _exigir_revisor(revisado_por)
    import anthropic  # import tardio: só é necessário com a IA ligada

    dados = mod_fatos.carregar_dados()
    lista = mod_fatos.gerar_fatos(dados, escopo)
    mod_fatos.salvar(lista, escopo, dados)
    modelo = os.getenv("LLM_MODEL", redator.MODELO_PADRAO)
    doc = redator.gerar_briefing(escopo, anthropic.Anthropic(), modelo=modelo)
    try:
        pdf = render_pdf.gerar_pdf(escopo, nome, permitir_contingencia=True)
    except render_pdf.ErroRenderizacao as exc:
        raise gr.Error(f"Briefing gerado, mas o PDF falhou: {exc}") from exc
    return markdown_briefing(doc), str(pdf)


# ---------------------------------------------------------------- aba Painel
def tabela_farol(escopo: str, processed_dir: Path = PROCESSED_DIR) -> pd.DataFrame:
    """Farol: BR usa ``diag_indicador``; DR usa os indicadores do DR com a mediana regional."""
    escopo = checar_escopo(escopo)
    diag_ind = ler_csv("diag_indicador", processed_dir)
    if escopo == "BR":
        df = diag_ind[diag_ind["indice_dn"].notna()].copy()
        df["farol"] = df["indice_dn"].map(render_pdf.farol).map(ICONE_FAROL)
        return df[["cod_indicador", "indicador", "meta_dn_txt", "realizado_dn_txt", "indice_dn", "farol",
                   "n_atingido", "n_atencao", "n_critico", "mediana_indice"]].rename(columns={
            "cod_indicador": "código", "meta_dn_txt": "meta", "realizado_dn_txt": "realizado",
            "indice_dn": "índice DN (%)", "n_atingido": "DRs atingido", "n_atencao": "DRs atenção",
            "n_critico": "DRs crítico", "mediana_indice": "mediana DRs (%)"}).round(1)
    df = render_pdf.tabela_painel_dr(ler_csv("pes", processed_dir), diag_ind,
                                     ler_csv("diag_regiao", processed_dir), escopo)
    if df.empty:
        return pd.DataFrame({"aviso": [f"Sem indicadores com índice para {escopo}."]})
    df["farol"] = df["farol"].map(ICONE_FAROL)
    return df[["cod_indicador", "indicador", "meta_txt", "realizado_txt", "indice_analise", "farol",
               "mediana_regiao", "mediana_nacional"]].rename(columns={
        "cod_indicador": "código", "meta_txt": "meta", "realizado_txt": "realizado",
        "indice_analise": "índice (%)", "mediana_regiao": "mediana regional (%)",
        "mediana_nacional": "mediana nacional (%)"}).round(1)


def precisa_regerar(saida: Path, fontes: list[Path]) -> bool:
    """True se o gráfico não existe ou se alguma tabela de origem é mais nova que ele."""
    if not saida.exists():
        return True
    mtime = saida.stat().st_mtime
    return any(f.stat().st_mtime > mtime for f in fontes if f.exists())


def figuras(escopo: str, figs_dir: Path = FIGS_DIR,
            processed_dir: Path = PROCESSED_DIR) -> list[tuple[str, str]]:
    """Gráficos do escopo, gerados com ``render_pdf.grafico_*`` e cacheados em ``outputs/figs``.

    Regera só quando o PNG não existe ou os CSVs de origem são mais novos.
    Mesmos nomes de arquivo e destaque do PDF (DR em destaque; BR sem destaque).
    """
    escopo = checar_escopo(escopo)
    destaque = None if escopo == "BR" else escopo
    graficos = (
        ("empregabilidade_dr", "pes", render_pdf.grafico_empregabilidade, "Empregabilidade por DR"),
        ("volume_resultado", "cruzamento_volume_resultado", render_pdf.grafico_dispersao,
         "Volume x resultado"),
    )
    figs_dir.mkdir(parents=True, exist_ok=True)
    saida_figs: list[tuple[str, str]] = []
    with _LOCK_GRAFICOS:  # matplotlib (pyplot) não é seguro entre threads
        for prefixo, tabela, funcao, legenda in graficos:
            saida = figs_dir / f"{prefixo}_{escopo}.png"
            if precisa_regerar(saida, [processed_dir / f"{tabela}.csv"]):
                logger.info("Gerando gráfico %s.", saida.name)
                funcao(ler_csv(tabela, processed_dir), saida, destaque)
            saida_figs.append((str(saida), legenda))
    return saida_figs


def mostrar_painel(escopo: str) -> tuple[pd.DataFrame, list[tuple[str, str]], str]:
    """Tabela de farol, galeria e aviso se um gráfico falhar."""
    try:
        figs, aviso = figuras(escopo), ""
    except Exception as exc:  # o farol continua visível mesmo sem gráficos
        logger.exception("Falha ao gerar gráficos de %s.", escopo)
        figs, aviso = [], f"_Gráficos indisponíveis: {type(exc).__name__}._"
    return tabela_farol(escopo), figs, aviso


# ---------------------------------------------------------------- aba Auditoria
def fatos_citados(briefing: dict | None, lista_fatos: list[dict]) -> pd.DataFrame:
    """IDs citados no briefing (ordem de primeira citação) com a frase do fato."""
    ids: list[str] = []
    if briefing:
        itens = [briefing.get("mensagem_central") or {}]
        for chave, _, _ in SECOES:
            itens += briefing.get(chave) or []
        for it in itens:
            for fid in it.get("fatos") or []:
                if fid not in ids:
                    ids.append(str(fid))
    frases = {str(f["id"]): f.get("frase", "") for f in lista_fatos}
    return pd.DataFrame({"ID": ids, "frase": [frases.get(i, "(fato inexistente na ficha)") for i in ids]})


def markdown_tentativas(log: dict | None, nome: str = "") -> str:
    """Cada tentativa do log com os erros barrados pelo validador."""
    if not log:
        return "_Nenhum log de geração para este escopo._"
    linhas = [f"**Log:** `{nome}` · **Status final:** {log.get('status', '-')} · "
              f"**Modelo:** {log.get('modelo', '-')} · **Prompt:** {log.get('versao_prompt', '-')}", ""]
    for t in log.get("tentativas", []):
        erros = t.get("erros") or []
        res = "✅ aprovada" if not erros else f"❌ {len(erros)} erro(s) barrado(s)"
        uso = t.get("uso") or {}
        tokens = f" · tokens: {uso.get('input_tokens', '-')} entrada / {uso.get('output_tokens', '-')} saída" if uso else ""
        linhas.append(f"### Tentativa {t.get('tentativa')} — {res}{tokens}")
        linhas += [f"- {e}" for e in erros]
        linhas.append("")
    return "\n".join(linhas)


def selo(escopo: str) -> str:
    """Selo de verificação (mesmo texto do PDF)."""
    try:
        ent = render_pdf.carregar_entradas(escopo, permitir_contingencia=True)
    except render_pdf.ErroRenderizacao as exc:
        return f"_Selo indisponível: {exc}_"
    return "\n".join(f"- {linha}" for linha in render_pdf.selo_verificacao(ent))


def mostrar_auditoria(escopo: str) -> tuple[str, pd.DataFrame, str]:
    """Tentativas do último log, fatos citados e selo."""
    escopo = checar_escopo(escopo)
    achado = ultimo_log(escopo)
    nome, log = (achado[0].name, achado[1]) if achado else ("", None)
    doc = carregar_briefing(escopo) or {}
    citados = fatos_citados(doc.get("briefing"), carregar_lista_fatos(escopo))
    return markdown_tentativas(log, nome), citados, selo(escopo)


# ---------------------------------------------------------------- aba Teste o validador
def item_exemplo(escopo: str) -> tuple[str, str]:
    """Texto e IDs do 1º destaque aprovado do escopo (para pré-preencher)."""
    escopo = checar_escopo(escopo)
    doc = carregar_briefing(escopo) or {}
    b = doc.get("briefing") if doc.get("status") == "aprovado" else None
    if not b or not b.get("destaques"):
        return "", ""
    it = b["destaques"][0]
    return it["texto"], ", ".join(it.get("fatos", []))


def validar_item(escopo: str, texto: str, ids: str,
                 outputs_dir: Path = OUTPUTS_DIR) -> str:
    """Troca o 1º destaque do briefing aprovado pelo texto editado e chama ``validar``."""
    escopo = checar_escopo(escopo)
    doc = carregar_briefing(escopo, outputs_dir) or {}
    b = doc.get("briefing") if doc.get("status") == "aprovado" else None
    if not b or not b.get("destaques"):
        return "_Não há briefing aprovado para este escopo; escolha outro escopo._"
    lista_ids = [i.strip().upper() for i in str(ids or "").replace(";", ",").split(",") if i.strip()]
    teste = json.loads(json.dumps(b))  # cópia profunda
    teste["destaques"][0] = {**teste["destaques"][0], "texto": str(texto or ""), "fatos": lista_ids}
    erros = validar(teste, carregar_lista_fatos(escopo, outputs_dir))
    if not erros:
        return "### ✅ Aprovado\nTodos os números e UFs aparecem nos fatos citados."
    return "### ❌ Barrado pelo validador\n" + "\n".join(f"- {e}" for e in erros)


# ---------------------------------------------------------------- aba Qualidade
COLUNAS_ACHADOS = ["regra_id", "gravidade", "fonte", "ano", "uf", "item", "descricao_item",
                   "evidencia", "recomendacao"]


def filtrar_achados(achados: pd.DataFrame, regra: str) -> pd.DataFrame:
    """Achados da regra escolhida (ou todos)."""
    if regra and regra != TODAS_REGRAS:
        achados = achados[achados["regra_id"] == regra]
    return achados[[c for c in COLUNAS_ACHADOS if c in achados.columns]].reset_index(drop=True)


# ---------------------------------------------------------------- interface
def construir_app(cfg: Config) -> gr.Blocks:
    """Monta a interface com as cinco abas."""
    resumo = ler_csv("qualidade_resumo")
    achados = ler_csv("qualidade_achados")
    regras = [TODAS_REGRAS] + sorted(achados["regra_id"].dropna().unique().tolist())

    with gr.Blocks(title=TITULO) as demo:
        gr.Markdown(f"# {TITULO}")
        escopo = gr.Dropdown(list(ESCOPOS), value="BR", label="Escopo (BR ou DR)")

        with gr.Tab("Briefing"):
            with gr.Row():
                revisor = gr.Textbox(label="Revisado por (obrigatório para gerar o PDF)")
                btn_ia = gr.Button("Gerar com IA", variant="primary", interactive=cfg.allow_llm)
                btn_pdf = gr.Button("Gerar PDF do briefing salvo")
            if not cfg.allow_llm:
                gr.Markdown(f"⚠️ {AVISO_LLM_OFF}. Exibindo o último briefing salvo do escopo.")
            md_briefing = gr.Markdown()
            arq_pdf = gr.File(label="PDF (download)", interactive=False)

        with gr.Tab("Painel"):
            tab_farol = gr.Dataframe(label="Farol dos indicadores (2025)", interactive=False, wrap=True)
            aviso_figs = gr.Markdown()
            galeria = gr.Gallery(label="Gráficos (outputs/figs)", columns=2, height="auto")

        with gr.Tab("Auditoria da IA"):
            md_tentativas = gr.Markdown()
            tab_citados = gr.Dataframe(label="Fatos citados no briefing", interactive=False, wrap=True)
            gr.Markdown("### Selo de verificação")
            md_selo = gr.Markdown()

        with gr.Tab("Teste o validador"):
            gr.Markdown("Edite o 1º destaque do briefing aprovado (ex.: altere um número) e valide. "
                        "Não chama a API.")
            txt_item = gr.Textbox(label="Texto do destaque", lines=4)
            txt_ids = gr.Textbox(label="IDs dos fatos citados (separados por vírgula)")
            btn_validar = gr.Button("Validar")
            md_validacao = gr.Markdown()

        with gr.Tab("Qualidade dos dados"):
            gr.Dataframe(resumo, label="Resumo por regra", interactive=False)
            dd_regra = gr.Dropdown(regras, value=TODAS_REGRAS, label="Filtrar achados por regra")
            tab_achados = gr.Dataframe(filtrar_achados(achados, TODAS_REGRAS), label="Achados",
                                       interactive=False, wrap=True)

        def atualizar(esc: str):
            md, pdf = mostrar_briefing(esc)
            farol_df, figs, aviso = mostrar_painel(esc)
            tent, citados, sl = mostrar_auditoria(esc)
            texto, ids = item_exemplo(esc)
            return md, pdf, farol_df, figs, aviso, tent, citados, sl, texto, ids, ""

        saidas = [md_briefing, arq_pdf, tab_farol, galeria, aviso_figs, md_tentativas, tab_citados,
                  md_selo, txt_item, txt_ids, md_validacao]
        escopo.change(atualizar, escopo, saidas, api_visibility="private")
        demo.load(atualizar, escopo, saidas, api_visibility="private")

        btn_ia.click(lambda e, r: gerar_com_ia(e, r, cfg), [escopo, revisor], [md_briefing, arq_pdf],
                     concurrency_limit=1, concurrency_id="geracao", api_visibility="private"
                     ).success(atualizar, escopo, saidas, api_visibility="private")
        btn_pdf.click(gerar_pdf_salvo, [escopo, revisor], [md_briefing, arq_pdf],
                      concurrency_limit=1, concurrency_id="geracao", api_visibility="private"
                      ).success(mostrar_painel, escopo, [tab_farol, galeria, aviso_figs], api_visibility="private")
        btn_validar.click(validar_item, [escopo, txt_item, txt_ids], md_validacao, api_visibility="private")
        dd_regra.change(lambda r: filtrar_achados(achados, r), dd_regra, tab_achados, api_visibility="private")
    return demo


def main() -> int:
    """CLI: ``python app.py``."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if ENV_PATH.exists():
        from dotenv import load_dotenv

        load_dotenv(ENV_PATH)  # só popula o ambiente; nada do .env é exibido
    try:
        cfg = configurar(os.environ)
    except ErroConfiguracao as exc:
        logger.error("%s", exc)
        return 1
    logger.info("Modo: %s; IA %s; share %s; autenticação %s.",
                "Hugging Face" if cfg.hf else "local", "ligada" if cfg.allow_llm else "desligada",
                "sim" if cfg.share else "não", "sim" if cfg.auth else "não")
    demo = construir_app(cfg)
    demo.queue(default_concurrency_limit=1)
    demo.launch(
        # No Hugging Face o host vem de GRADIO_SERVER_NAME (definido pelo Space).
        server_name=None if cfg.hf else "127.0.0.1",
        share=cfg.share and not cfg.hf,
        auth=cfg.auth,  # obrigatório com SHARE=1 ou IA no HF; se houver credenciais, sempre usadas
        allowed_paths=[str(OUTPUTS_DIR)],
        blocked_paths=[str(ENV_PATH)],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
