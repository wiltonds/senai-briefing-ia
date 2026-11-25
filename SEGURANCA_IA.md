# Segurança da IA

A IA **redige**; não calcula nem decide o que é verdade. Todo número nasce no código, a partir de dados oficiais, e chega à IA já pronto, numa ficha de fatos. Cada camada abaixo existe para que um número errado seja barrado antes de chegar ao leitor.

## Camadas contra alucinação

| # | Camada | O que faz | Onde |
|---|---|---|---|
| 1 | O código calcula | Índices, contagens, medianas e projeções são calculados em Python sobre `indice_analise`, com uma única função de arredondamento. | `src/diagnostico.py`, `src/projecao.py`, `src/formato.py` |
| 2 | A IA só vê a ficha de fatos | A IA recebe apenas frases prontas, cada uma com um ID (`F001`…), os números permitidos e a fonte (arquivo, hash e página do PDF). O conteúdo da ficha é tratado como dado, não como instrução. | `src/fatos.py`, `prompts/` |
| 3 | Citação obrigatória | Cada item do briefing e cada resposta do "Perguntas verificadas" lista os IDs dos fatos que o sustentam. | `prompts/briefing_v4.md`, `prompts/perguntas_v4.md` |
| 4 | Validador | Reprova o texto se um número ou uma sigla de estado não estiver nos fatos citados, se houver afirmação de causa sem a palavra "hipótese", termo técnico interno, código de indicador ou formato fora do esperado. Antes de publicar, todos os textos são revalidados contra a ficha atual. | `src/validador.py`, `src/perguntas.py`, `src/revalidar.py` |
| 5 | Novas tentativas e plano B | Texto reprovado volta à IA com a lista de erros (até 3 vezes). Se não passar: o briefing sai só com as frases dos fatos, e a pergunta sai do catálogo. | `src/redator.py`, `src/perguntas.py` |
| 6 | Teste de consistência | O mesmo indicador, ano e unidade precisa ter o mesmo número em todas as fichas (BR + 27 DRs), sem chamar a API. | `tests/test_fatos.py` |
| 7 | Revisão humana | Nada é publicado sem o nome de quem revisou (`--revisado-por`). | `src/publicar.py`, `src/render_pdf.py` |
| 8 | Registro com hash | Cada geração guarda prompt, modelo, respostas brutas, erros, consumo e o sha256 do prompt, da ficha e da saída; o site publica o sha256 de cada arquivo. | `logs/`, `site/MANIFESTO.json` |

## Registro de verificações

Ocorrências reais pegas pelas camadas acima.

| Data | Ocorrência | Camada que detectou | Ação | Evidência |
|---|---|---|---|---|
| 2026-11-25 | O mesmo índice de AL aparecia como 77,2% em um fato e 77,3% em outro (77,2511… arredondado duas vezes). | Teste de consistência (6) | Arredondamento único (`src/formato.py`, meio para cima) e tabelas do diagnóstico em precisão total. | `tests/test_fatos.py::test_mesmo_numero_mesmo_texto_em_toda_a_ficha`, `test_fmt_pct_round_half_up` |
| 2026-11-25 | Pergunta "O que fazer agora?" de SP reprovada na 1ª tentativa: a resposta trazia o número "3", que não está nos fatos citados. | Validador (4) | Erro devolvido à IA; resposta refeita e aprovada na 2ª tentativa. | `logs/perguntas_SP_20261125T194522Z.json` |
| 2026-11-25 | A fidelização de SP aparecia com 85,4% (Plano Estratégico) e 85,5% (cruzamento volume × resultado). | Revisão humana (7) | Corrigido na origem: tabela do cruzamento regenerada a partir de `indice_analise` em precisão total; teste de consistência ampliado a todos os escopos e entre fichas. | `DECISOES.md` (`[revisao-humana]`), `tests/test_fatos.py::test_mesmo_numero_mesmo_texto_entre_todas_as_fichas` |
| 2026-11-25 | GO, indicador 13.1: percentual com realizado de 138,1%, valor impossível para uma proporção. | Regras de qualidade (Q02) | Excluído do farol e sinalizado como ponto de qualidade de dados. | `data/processed/qualidade_achados.csv`, `DECISOES.md`, `tests/test_diagnostico.py::test_q02_exclui_do_farol` |
| 2026-11-25 | Falso positivo: o validador reprovava "4º trimestre" por tratar o "4" como número fora dos fatos. | Validador (4) | Ordinais ("4º", "1ª", "3°") deixaram de contar como número. | `tests/test_validador.py::test_ordinais_nao_contam_como_numero`, `test_ordinal_no_texto_nao_gera_erro` |
| 2026-11-25 | Correção de arredondamento alterou medianas nacionais em 0,1 p.p.; revalidação detectou 2 números desatualizados no briefing de AL. | Validador (4), via revalidação | Briefing de AL regerado. Publicação agora bloqueia texto desatualizado (`src/publicar.py` chama `src/revalidar.py` e aborta). | `tests/test_revalidar.py::test_reprova_numero_desatualizado_por_escopo`, `test_publicar_aborta_com_texto_desatualizado` |

## Limites conhecidos

- O validador confere números, siglas e palavras proibidas, não o sentido da frase: uma conclusão mal formulada com números corretos passa. Por isso a revisão humana é obrigatória.
- A projeção de 2026 é linear e não considera a sazonalidade do fim do ano.
