# Monitor de Promoções (Telegram)

Escuta em tempo real os canais/grupos de promoção que você já assina no
Telegram e te avisa quando um item da sua lista aparece — de preferência
abaixo do preço-alvo.

Roda 100% local: nenhuma API paga de IA. O LLM local (Ollama) é **opcional**
e só entra como plano B quando o regex não entende a mensagem.

```
[Canais Telegram] -> [Listener Telethon] -> [Extrator produto/preço] -> [Matcher] -> [Bot notifica você]
                                                    |
                                     regex primeiro; LLM local só no fallback
```

## Instalação

Requer Python 3.11+ e git.

```bash
git clone https://github.com/ViniciusLimaCarvalho/MonitoraPrecos.git
cd MonitoraPrecos

python -m venv .venv           # opcional, mas recomendado
.venv\Scripts\activate         # Windows | source .venv/bin/activate no Linux/Mac

pip install -r requirements.txt
cp config.example.yaml config.yaml     # no Windows: copy config.example.yaml config.yaml
```

`config.yaml`, `.env` e o arquivo `.session` **não vêm no repositório** (estão
no `.gitignore`) porque são credenciais pessoais. Cada pessoa que rodar o
projeto precisa criar as suas próprias — não faz sentido compartilhar as
suas, elas dão acesso à sua conta do Telegram.

### 1. Credenciais da sua conta (api_id / api_hash)

1. Acesse <https://my.telegram.org> → *API development tools*.
2. Crie um app (qualquer nome) e copie `api_id` e `api_hash`.
3. Cole em `config.yaml` (ou em um `.env`, veja `.env.example`).

São gratuitos e servem para **ler** os canais como você mesmo — é o único jeito
de acompanhar canais em que você já é inscrito.

### 2. Bot que te envia os alertas

1. No Telegram, fale com o **@BotFather** → `/newbot` → copie o token.
2. **Envie uma mensagem qualquer para o seu bot** (sem isso ele não pode te
   escrever primeiro).
3. Pegue seu `chat_id` com o **@userinfobot**.
4. Preencha `notificacao.bot_token` e `notificacao.chat_id`.

Sem bot configurado, os alertas aparecem apenas no terminal.

### 3. Primeiro login e escolha dos canais

```bash
python main.py login     # pede telefone + código do Telegram, cria o .session
python main.py canais    # lista o que você assina, com @usuario e ID
python main.py doctor    # confere tudo e manda uma mensagem de teste no bot
```

Copie os canais desejados para a lista `canais:` do `config.yaml`.

## Lista de interesse

```yaml
itens:
  - nome: "Nintendo Switch 2"
    palavras_chave: ["switch 2", "switch2"]   # qualquer uma já casa
    preco_alvo: 3500.00                       # null = alerta em qualquer preço
    preco_minimo: 1800.00                     # piso: abaixo disso não é o console
    excluir: ["capa", "película"]             # ignora acessórios do produto
```

O `preco_minimo` resolve um problema real: jogos e acessórios citam o nome do
console no título (`Mario Kart World - Switch 2 - R$ 349`) e cairiam no alvo
"abaixo de R$ 3.500". Com o piso, só o produto na faixa de preço certa alerta.

A busca é feita no texto inteiro da mensagem, sem acento, sem diferenciar
maiúsculas e ignorando espaços quando a palavra tem 4+ caracteres — por isso
`rtx 4070` casa com `RTX4070` e com `Rtx 4070 Super`.

## Rodando

```bash
python main.py run       # inicia o monitor (Ctrl+C para sair)
```

No Windows dá para usar o `monitorar.bat` (duplo clique).

Outros comandos:

| Comando | O que faz |
|---|---|
| `python main.py doctor` | valida config, sessão, bot e LLM local |
| `python main.py canais` | lista canais/grupos assinados |
| `python main.py testar "texto..."` | mostra o que seria extraído e se casaria com algum item |
| `python main.py varrer -n 300` | roda a config atual no histórico dos canais, sem notificar |
| `python main.py ultimo "switch 2"` | manda pelo bot a promoção mais recente daquele item |
| `python main.py resumo` | manda pelo bot o último preço de cada item da lista |
| `python main.py simular-fim "Cupom X ESGOTADO"` | testa o aviso de encerramento sem esperar acabar |
| `python main.py historico -n 30` | últimos alertas enviados |
| `python main.py run -v` | log em modo DEBUG |

Se um item tem `preco_alvo` mas a mensagem não traz preço nenhum (recado do
canal, aviso de cupom), o alerta depende de `alertar_sem_preco` no
`config.yaml`. Em canais tagarelas, `false` reduz bastante o ruído.

Exemplo de teste rápido:

```bash
python main.py testar "RTX 4070 Super de R$ 4.299 por R$ 3.399,90 - Cupom TECH50"
```

## Como o preço é lido

- Entende `R$ 1.234,56`, `1.999`, `99,90`, `3499 reais`.
- Em `de R$ 4.299 por R$ 3.399`, o preço válido é o segundo (o `de` vira
  "preço original" e o alerta mostra o desconto).
- **Ignora o valor da parcela**: em `12x de R$ 24,90 (R$ 249 à vista)` o preço
  considerado é R$ 249. Se a mensagem só tiver parcelas, usa o total (12 × 24,90).

## LLM local (opcional)

Serve só para mensagens bagunçadas em que o regex não achou produto ou preço.

```bash
ollama pull qwen2.5:7b
```

```yaml
llm:
  habilitado: true
  modelo: "qwen2.5:7b"
```

Se o Ollama estiver fora do ar, o monitor avisa no log e continua rodando
apenas com o regex — nunca fica travado esperando o modelo.

## Alertas duplicados

Cada alerta vira um hash de `item + produto + preço` no SQLite
(`dados/alertas.db`), válido por `janela_dedup_horas` (padrão 72h). Ou seja:

- o mesmo anúncio repostado em vários canais notifica **uma vez**;
- se o produto voltar **mais barato**, é uma promoção nova e notifica de novo.

Cada mensagem também é registrada por `(canal, id)`, então reconexões não
reprocessam o que já passou.

## Rodando sozinho (Agendador de Tarefas)

A tarefa **"Monitor de Promocoes"** sobe o monitor 1 minuto após o logon, sem
janela de console (`pythonw.exe`), e reinicia sozinha em caso de falha
(até 10 vezes, a cada 5 min).

```powershell
Get-ScheduledTask "Monitor de Promocoes"          # ver estado
Stop-ScheduledTask "Monitor de Promocoes"         # parar agora
Start-ScheduledTask "Monitor de Promocoes"        # iniciar agora
Disable-ScheduledTask "Monitor de Promocoes"      # não subir mais no logon
```

Como não há console, acompanhe por `monitor.log` na pasta do projeto.

**Resumo ao iniciar**: assim que sobe, o monitor manda uma mensagem com o
último preço visto de cada item (`notificacao.resumo_ao_iniciar: false`
desliga). Assim você sabe onde cada item está antes de esperar promoção nova.

**Config a quente**: `itens`, `alertar_sem_preco` e as janelas são recarregados
sozinhos até 30s depois de você salvar o `config.yaml` — não precisa reiniciar.
YAML inválido é ignorado com aviso no log, mantendo a configuração anterior.
Mudança na lista de `canais` ainda exige reinício (o log avisa).

⚠️ Não rode o `monitorar.bat` enquanto a tarefa estiver ativa: dois processos
usando o mesmo arquivo `.session` brigam pelo SQLite do Telethon.

## Aviso de promoção encerrada

Quando uma oferta alertada acaba, o monitor **edita o alerta original** com o
carimbo `ENCERRADA` e manda uma mensagem curta em resposta a ele:

```
🛑 ACABOU: Donkey Kong Bananza
Donkey Kong Bananza
era R$ 337,00
cupom MANUGAVASSI15

motivo: cupom MANUGAVASSI15 esgotado (aviso do canal)
```

Para isso o cupom de cada promoção é guardado junto com o alerta. São três
sinais, todos observados nos canais reais:

| Sinal | Como é detectado |
|---|---|
| Mensagem nova | marcador de fim (`ESGOTADO`, `acabou`, `❌`) **mais** um identificador: o cupom guardado, uma resposta à mensagem original ou o nome do item |
| Edição da original | o preço subiu, o cupom sumiu do texto, ou apareceu marcador de fim |
| Remoção da original | a mensagem foi apagada do canal |

O marcador sozinho nunca basta — `15% OFF`, `off white` e `acaba de ganhar um
cupom` apareciam como falso positivo. E como esses canais editam ~80% das
mensagens por rotina (ajuste de link/texto), a edição só encerra a promoção se
o **conteúdo** mudou para pior; edição de rotina não dispara nada.

O acompanhamento vale por `janela_fim_horas` (padrão 72h) depois do alerta.

## Testes

```bash
python -m unittest discover -s tests
```

## Estrutura

```
main.py                  CLI (run, login, canais, testar, doctor, historico)
monitor/
  config.py              leitura do config.yaml + .env
  listener.py            Telethon: resolve canais e escuta mensagens
  extrator.py            regex de produto, preço, loja, cupom e link
  llm.py                 fallback opcional via Ollama
  matcher.py             comparação com a lista de interesse e preço-alvo
  expiracao.py           detecção de promoção/cupom encerrado
  armazenamento.py       SQLite (deduplicação e histórico)
  notificador.py         envio pelo bot do Telegram
  pipeline.py            junta tudo
tests/                   testes de extração, match e deduplicação
```

## Limitações conhecidas

- **WhatsApp está fora**: não há API oficial para ler grupos/canais como
  usuário comum; as bibliotecas não oficiais violam os Termos de Uso e podem
  levar ao banimento da conta.
- Mensagens que são só imagem, sem legenda, não têm texto para analisar.
- O nome do produto é heurístico; o que garante o alerta é a palavra-chave.
- Use uma conta que você não se importe em ter logada por longos períodos e
  não abuse na quantidade de canais — a conta é sua e vale a regra do bom senso.

## Próximos passos possíveis

- Histórico de preços por produto (saber se o preço atual é realmente bom).
- Painel web local para ver os alertas passados.
- Deduplicação inteligente entre canais que repostam a mesma oferta.
