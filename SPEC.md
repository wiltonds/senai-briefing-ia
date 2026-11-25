# SPEC — Executive Briefing Automatizado (SENAI · Opção 3)

> Documento-guia do projeto. Toda implementação (inclusive com vibe coding) deve obedecer a este SPEC.
> Dono: Wilton Costa · Versão 0.2 · Entrega: sexta-feira

## 1. Objetivo
Entregar, para um Superintendente / Diretor Regional, uma síntese executiva dos resultados publicados no Portal da Transparência do SENAI, e demonstrar uma solução que **automatiza a leitura, a mineração de texto e a síntese** desses relatórios com IA Generativa, **de forma segura e sem alucinações**.

## 2. Requisitos do desafio → como atendemos

| Requisito (enunciado) | Como atendemos | Evidência na entrega |
|---|---|---|
| Usar dados públicos oficiais do portal | Somente Plano Estratégico Sistêmico, Atendimento Social, Relatório de Gestão DN e Mapa Estratégico | Fonte citada em cada número |
| Narrativa simples para Diretor/Superintendente | Briefing com 1 mensagem central + **Destaques / Pontos de atenção / Recomendações práticas** | PDF de até 4 páginas |
| Automatizar leitura e mineração de texto | Leitura do PDF do Relatório de Gestão (por página) + coleta das APIs do portal | Pipeline em Python |
| Síntese via IA Generativa | LLM redige a narrativa a partir de fatos estruturados | App Streamlit |
| Forma segura e sem alucinações | Números calculados por código; LLM só redige; validador bloqueia número sem fonte; revisão humana | Painel de auditoria no app + teste do validador |

## 3. Público e tom
- **Leitor principal:** Superintendente (visão nacional). **Secundário:** Diretor Regional (briefing por DR, gerado pelo mesmo pipeline).
- **Tom:** DN como parceiro consultivo dos DRs; respeito à autonomia federativa. **Sem ranking público de DRs**: usar grupos de apoio.
- **Formato:** conclusão primeiro (pirâmide). Cada afirmação deve gerar uma decisão ou um acompanhamento.

## 4. Escopo de dados (somente fontes oficiais)
| Fonte | Conteúdo | Uso |
|---|---|---|
| Plano Estratégico Sistêmico 2025–2027 (API) | Indicadores oficiais: meta, realizado e índice por DR — 2025 fechado e 2026 até o 3º tri | Eixo de **resultado** |
| Atendimento Social (API) | Matrículas e serviços de tecnologia e inovação (STI), previsto e realizado, por DR | Eixo de **volume** |
| Relatório de Gestão DN 2025 (PDF) | Contexto qualitativo e números oficiais com página | Mineração de texto e triangulação |
| Mapa Estratégico 2025–2027 (PDF) | Objetivos e perspectivas | Estrutura da narrativa |

**Indicadores:** usamos apenas os indicadores oficiais do Plano. Não criamos indicadores próprios nem índices compostos com pesos; métricas derivadas (ex.: índice de realização recalculado) servem só para auditoria.

**Método analítico:**
- Farol de gestão sobre o índice oficial: atingido (≥ 100%), atenção (80% a 99,9%), crítico (< 80%). Ano fechado (2025).
- Visões: por indicador (problema sistêmico), por DR (contagem simples, sempre com a base: "6 de 11") e por região (assimetrias).
- Cruzamento volume × resultado: matrículas (Atendimento Social) × empregabilidade e fidelização (Plano).
- 2026 (parcial, até setembro): projeção linear e ritmo necessário apenas para indicadores de quantidade acumulada.
- Qualidade: excluir o inválido, sinalizar o questionável (ver DECISOES.md).
- Contexto normativo: Res. CN nº 44/2020 (Programa de Eficiência da Gestão), versão vigente a confirmar.

## 5. Mensagem central (hipótese, a validar com os dados)
"O Sistema entrega volume acima do previsto, mas o resultado finalístico (empregabilidade e fidelização) não acompanha, e o gargalo se concentra em DRs específicos."
Confirmado com os dados de 2025: 23 DRs com matrículas ≥ 100%, 13 deles com empregabilidade crítica (< 80%); correlação de Spearman = −0,26.
Toda relação causal será apresentada como **hipótese**, com o teste que a confirmaria.

## 6. Regras contra alucinação (não negociáveis)
1. Todo número do briefing vem da **ficha de fatos** (JSON gerado por código), com um ID (`F001`…) e a fonte de origem (endpoint e campo, ou PDF e página).
2. O LLM recebe **apenas** a ficha de fatos e os trechos citados do PDF, e devolve JSON estruturado com os IDs citados.
3. **Validador:** extrai todos os números do texto e confere cada um com os fatos citados. Se algum não bater, o texto é rejeitado e reenviado (máx. 3 tentativas). Persistindo, o pipeline volta para o modo template determinístico.
4. **Revisão humana** obrigatória antes de exportar o PDF. Cada geração fica registrada em log (prompt, modelo, resposta, resultado da validação).
5. Divergências entre fontes não são "corrigidas" pela IA: são **reportadas** como ponto de qualidade de dados.

## 7. Arquitetura
```
coleta (APIs + PDF) → normalização → regras de qualidade → ficha de fatos (JSON)
      → redator LLM (prompt versionado) → validador → revisão humana → PDF / app
```
Modo offline: retrato dos dados guardado e verificado por hash SHA-256 (a demonstração não depende do portal estar no ar).

## 8. Fora de escopo
- Dados não públicos ou dados internos da FIEA.
- Previsões estatísticas sofisticadas (no máximo, o ritmo linear de 2026, rotulado como tal).
- Afirmar causas sem evidência.

## 9. Entregáveis e cronograma
| Quando | Entregável |
|---|---|
| Qua | SPEC aprovado · camada de dados (coleta, normalização, qualidade) |
| Qui | Ficha de fatos · mineração do PDF · redator LLM e validador · app Streamlit |
| Sex manhã | Briefing nacional (PDF de até 4 páginas) gerado pelo pipeline · vídeo de 3 min · README |

## 10. Critérios de pronto
- [ ] 100% dos números do briefing rastreáveis até a fonte
- [ ] O validador reprova um texto com número inventado (teste automatizado)
- [ ] O app roda offline a partir do retrato dos dados
- [ ] Briefing com até 4 páginas, lido em 3 minutos por um executivo
- [ ] Nenhum DR exposto em ranking; recomendações compatíveis com a autonomia federativa
