# Agente de Monitoramento de Promoções (Telegram)

## Objetivo

Criar um agente que monitora, em tempo real, canais do Telegram de promoções (que também têm versão em grupos de WhatsApp) e alerta quando algum item de uma lista de interesse aparecer, idealmente abaixo de um preço-alvo.

## Escopo

### Incluído
- Monitoramento de canais/grupos do **Telegram** aos quais o usuário já está inscrito.
- Execução 100% local, sem dependência de API paga (Claude, OpenAI etc).
- Extração de produto e preço via regex, com fallback opcional para LLM local (Ollama + Qwen2.5) em mensagens mais complexas.
- Lista de itens de interesse configurável pelo usuário (palavras-chave e, opcionalmente, preço-alvo).
- Notificação ao usuário quando houver correspondência.

### Fora do escopo (nesta primeira versão)
- Monitoramento de **WhatsApp**. Não existe API oficial simples para ler canais como usuário comum; as alternativas (Baileys, whatsapp-web.js) são não oficiais e violam os Termos de Uso, com risco de banimento da conta. Ficará para uma fase futura, se necessário.
- Compra automática ou qualquer ação além de notificar.

## Arquitetura

```
[Canais Telegram] --> [Listener Telethon] --> [Extrator produto/preço] --> [Matcher contra lista de interesse] --> [Notificador]
                                                      |
                                          (regex primeiro; LLM local como fallback)
```

### Componentes

1. **Listener (Telethon)**
   - Conecta à conta do usuário via `api_id` / `api_hash` (obtidos gratuitamente em my.telegram.org).
   - Escuta eventos de novas mensagens nos canais/grupos configurados.
   - Passa o texto bruto da mensagem adiante.

2. **Extrator de produto/preço**
   - Primeira tentativa: regex para capturar valores em R$ (ex.: `R\$\s?\d+[.,]\d{2}`) e nome do produto por proximidade textual.
   - Fallback: se a mensagem for muito bagunçada (regex não encontrou padrão claro), envia o texto para um LLM local rodando via Ollama (modelo Qwen2.5 ou similar), pedindo retorno estruturado em JSON: `{produto, preco, loja, link}`.

3. **Lista de interesse (config)**
   - Arquivo simples (JSON ou YAML) com os itens que o usuário quer rastrear.
   - Cada item pode ter: palavras-chave (para casar com o texto), preço-alvo (opcional), categoria (opcional).

4. **Matcher**
   - Compara produto extraído com a lista de interesse (correspondência por palavras-chave, case-insensitive, aceitando variações simples).
   - Se houver preço-alvo definido, só dispara alerta quando o preço encontrado for igual ou menor.

5. **Notificador**
   - Bot próprio do Telegram (separado dos canais monitorados) que envia mensagem direta ao usuário quando há correspondência.
   - Mensagem de alerta deve conter: produto, preço, canal de origem, link (se disponível) e horário.

## Stack técnica sugerida

- **Linguagem**: Python 3.11+
- **Telegram**: Telethon
- **LLM local (opcional)**: Ollama + Qwen2.5 (ou Llama 3.1), acessado via `localhost:11434`
- **Configuração**: arquivo `config.yaml` ou `itens.json` editável pelo usuário
- **Persistência simples**: SQLite ou arquivo JSON para evitar alertas duplicados da mesma promoção
- **Notificação**: Bot API do Telegram (mensagem para o próprio usuário)

## Formato sugerido de configuração de itens

```yaml
itens:
  - nome: "RTX 4070"
    palavras_chave: ["rtx 4070", "rtx4070"]
    preco_alvo: 3500.00
  - nome: "SSD 1TB NVMe"
    palavras_chave: ["ssd 1tb", "ssd nvme 1tb"]
    preco_alvo: null
canais:
  - "@nomeDoCanal1"
  - "@nomeDoCanal2"
```

## Requisitos não funcionais

- Deve rodar continuamente (script de longa duração ou serviço).
- Deve funcionar totalmente offline em relação a serviços pagos de IA (uso de LLM local é opcional e não obrigatório).
- Deve evitar notificações duplicadas para a mesma promoção (checar por hash da mensagem ou id).
- Configuração de itens e canais deve ser fácil de editar sem mexer no código.

## Possíveis melhorias futuras

- Histórico de preços por produto, para saber se o preço atual é realmente bom.
- Painel simples (web local) para ver alertas passados.
- Suporte a WhatsApp via solução oficial, caso a Meta disponibilize API adequada para uso pessoal.
- Deduplicação inteligente entre canais que repostam a mesma promoção.
