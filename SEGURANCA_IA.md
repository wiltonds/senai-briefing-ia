# Segurança da IA (esboço)

> Esboço. A completar com exemplos reais de reprovação tirados de `logs/`.

## Princípio

A IA **redige**; não calcula nem decide o que é verdade. Todo número nasce no código, a partir de dados oficiais, e chega à IA já pronto, numa ficha de fatos.

## Camadas

1. **Ficha de fatos** (`src/fatos.py`): frases geradas por código, cada uma com um ID (`F001`…), os números permitidos e a fonte (arquivo, hash e página do PDF, quando houver).
2. **Prompt versionado** (`prompts/`): só a ficha entra no contexto; o conteúdo da ficha é tratado como dado, não como instrução.
3. **Citação obrigatória**: cada item do briefing e cada resposta do "Pergunte ao briefing" lista os IDs dos fatos usados.
4. **Validador** (`src/validador.py`), que reprova o texto quando:
   - um número não aparece nos fatos citados naquele item;
   - uma sigla de estado não aparece nos fatos citados;
   - há linguagem de causa sem a palavra "hipótese";
   - aparece um termo técnico interno ou o código de um indicador;
   - o formato (campos, quantidades, limite de palavras) não é o esperado.
5. **Novas tentativas**: até 3, devolvendo à IA a lista de erros.
6. **Plano B**:
   - no briefing, as frases dos fatos, sem texto da IA (`status: contingencia`);
   - no "Pergunte ao briefing", a pergunta reprovada sai do catálogo.
7. **Revisão humana**: publicação e PDF exigem `--revisado-por`.
8. **Registro**: `logs/` guarda prompt, modelo, respostas brutas, erros, consumo e os sha256 do prompt, da ficha e da saída.

## A completar

- [ ] Taxa de aprovação na 1ª tentativa por escopo (a partir dos logs).
- [ ] Exemplos de erros reais pegos pelo validador.
- [ ] Limites conhecidos (ex.: o validador confere números, não o sentido da frase).
