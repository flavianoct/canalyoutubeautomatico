# Canal infantil automático no YouTube

Robô que cria e publica **YouTube Shorts infantis** sozinho, todos os dias — com
**músicas originais cantadas** e vídeos narrados, pensados para viralizar:

```
tema novo ─► letra ou roteiro (Claude) ─► revisão de segurança infantil ─► ilustrações (IA)
   ─► música CANTADA (ElevenLabs Music) ou narração com voz neural
   ─► legenda karaokê sincronizada ─► vídeo 9:16 ─► upload ("feito para crianças")
   ─► playlist da série ─► histórico (nunca repete tema)
```

O canal de exemplo é o **Mundo do Pipo**: a girafinha Pipo apresenta 7 séries —
duas cantadas (**Musiquinhas do Pipo** e **Dança do Pipo**) e cinco narradas
(curiosidades de animais, adivinhas, historinhas, aprender brincando e "por que será?").
Tudo isso — nome, mascote, séries, voz, estilo musical, horários — é editável no
[`config.yaml`](config.yaml).

### Como funcionam os Shorts cantados

1. O Claude compõe uma **música original** (sem paródias) com refrão-chiclete logo no
   primeiro segundo, onomatopeias, gestos para a criança imitar e um final que puxa o
   refrão de novo, para o vídeo rodar em loop.
2. A **ElevenLabs Music** canta a letra em português, com a duração exata de cada
   parte (refrão, verso...), e devolve o tempo de cada palavra cantada.
3. O vídeo troca de ilustração a cada verso e destaca em amarelo a palavra que está
   sendo cantada (karaokê). As repetições do refrão reaproveitam as mesmas imagens.

---

## O que é automático e o que você faz uma única vez

| Etapa | Quem faz |
|---|---|
| Criar a conta Google e o canal no YouTube | **Você, uma vez** (o YouTube não permite criar canais por API) |
| Criar o projeto no Google Cloud e autorizar o robô | **Você, uma vez** (~15 min, passo a passo abaixo) |
| Gerar mascote, foto de perfil e banner | Robô (`channel-kit`) |
| Aplicar banner, descrição, palavras-chave, público infantil e playlists | Robô (`setup-channel`) |
| Enviar a foto de perfil | **Você, uma vez** (a API não permite) |
| Escolher tema, compor/escrever, revisar, ilustrar, cantar/narrar, editar e publicar cada Short | Robô, todo dia (GitHub Actions) |

---

## Passo a passo

### 1. Crie o canal

1. Crie (ou use) uma conta Google só para o canal.
2. Acesse <https://www.youtube.com/create_channel> e crie o canal com o nome desejado.
3. No YouTube Studio → **Configurações → Canal → Configurações avançadas**, marque
   **"Sim, defina este canal como conteúdo para crianças"** (o robô também tenta fazer isso).

### 2. Libere a API do YouTube (Google Cloud)

1. Em <https://console.cloud.google.com>, crie um projeto (ex.: `canal-pipo`).
2. **APIs e serviços → Biblioteca** → ative a **YouTube Data API v3**.
3. **APIs e serviços → Tela de consentimento OAuth**: tipo **Externo**, preencha nome e
   e-mail, adicione o e-mail da conta do canal como **usuário de teste**.
4. Ainda na tela de consentimento, clique em **Publicar app** (status *Em produção*).
   > Se o app ficar em modo *Teste*, o acesso expira a cada **7 dias** e o robô para.
   > Para uso próprio não é preciso concluir a verificação do Google; basta aceitar o
   > aviso "app não verificado" ao autorizar.
5. **Credenciais → Criar credenciais → ID do cliente OAuth → App para computador**.
   Baixe o JSON e salve na pasta do projeto como `client_secret.json`.
6. **Muito importante — auditoria da API:** vídeos enviados pela API por projetos novos
   ficam **travados como privados** até o projeto passar pela auditoria do YouTube.
   Peça em <https://support.google.com/youtube/contact/yt_api_form> (formulário
   *YouTube API Services – Audit and Quota Extension*). Explique que é um canal próprio
   que publica Shorts educativos gerados pelo seu sistema. Enquanto a auditoria não sai,
   o robô funciona, mas os vídeos ficam privados.

### 3. Pegue as chaves das IAs

| Chave | Para quê | Obrigatória? |
|---|---|---|
| `ANTHROPIC_API_KEY` (<https://console.anthropic.com>) | Roteiro, letras e revisão de segurança | **Sim** |
| `ELEVENLABS_API_KEY` (<https://elevenlabs.io>) | **Músicas cantadas** (e voz premium opcional) | **Sim, para as séries cantadas** — sem ela o robô só faz vídeos narrados |
| `OPENAI_API_KEY` (<https://platform.openai.com>) | Ilustrações de melhor qualidade e mascote consistente | Recomendada |
| `POLLINATIONS_TOKEN` | Ilustrações gratuitas (usado quando não há chave da OpenAI) | Não |
| `ELEVENLABS_VOICE_ID` | Voz da narração na ElevenLabs (`voice.provider: elevenlabs`) | Não — a voz padrão (Microsoft Edge) é gratuita |

> **Plano da ElevenLabs:** o plano gratuito tem poucos créditos por mês e não permite
> uso comercial. Para um canal publicando todo dia (e que pode ser monetizado), use um
> plano pago. Como referência, uma música de 26 segundos custou cerca de 390 créditos
> no teste deste projeto — confira os preços e termos atuais no site.

### 4. Rode no seu computador (uma vez)

Requisitos: Python 3.10+ e FFmpeg 6 ou mais novo (`sudo apt install ffmpeg` /
`brew install ffmpeg` / no Windows, `winget install ffmpeg`).

```bash
pip install -r requirements.txt
cp .env.example .env          # e preencha as chaves

# Teste sem gastar nada (sem chaves): gera Shorts de exemplo em output/
python -m kidschannel make --offline --series curiosidades   # narrado (voz grátis do Edge, se houver internet)
python -m kidschannel make --offline --series musica         # cantado: no offline só toca uma base instrumental

# Autoriza o robô a publicar no seu canal (abre o navegador)
python -m kidschannel auth --client-secrets client_secret.json
#  -> mostra YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET e YOUTUBE_REFRESH_TOKEN

# Identidade visual: mascote de referência, foto de perfil e banner
python -m kidschannel channel-kit
#  -> confira output/channel_kit/ e assets/mascot.png; se não gostar,
#     rode de novo com --refresh-mascot

# Aplica banner, descrição, palavras-chave, público infantil e cria as playlists
python -m kidschannel setup-channel

# Gera Shorts de verdade sem publicar, para conferir a qualidade
python -m kidschannel make --series musica
python -m kidschannel make --series curiosidades

# Gera e publica
python -m kidschannel run
```

Depois, envie `output/channel_kit/avatar.png` como foto do canal no YouTube Studio.
**Faça commit do `assets/mascot.png`** para o GitHub Actions usar o mesmo personagem.

### 5. Deixe no piloto automático (GitHub Actions)

1. No GitHub: **Settings → Secrets and variables → Actions → New repository secret** e
   cadastre `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `YOUTUBE_CLIENT_ID`,
   `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` e, se tiver, `OPENAI_API_KEY`,
   `POLLINATIONS_TOKEN`, `ELEVENLABS_VOICE_ID`.
2. (Opcional) Na aba **Variables**, crie `SHORTS_PER_RUN` com quantos vídeos por execução.
3. Leve este código para a branch principal (`main`) — o agendamento do GitHub só roda nela.
4. Pronto: o workflow [`publicar-shorts.yml`](.github/workflows/publicar-shorts.yml) roda
   todo dia às 10:07 (Brasília), publica o Short, guarda o vídeo como *artifact* por
   14 dias e salva o histórico em `data/state.json` (para nunca repetir tema).
5. Para rodar na hora: aba **Actions → Publicar Shorts → Run workflow** (dá para escolher
   série, tema ou só gerar sem publicar).

---

## Personalização (`config.yaml`)

- **Canal e mascote** (`channel`): nome, descrição, palavras-chave, bordão e a descrição
  visual do personagem (em inglês, usada em todas as ilustrações).
- **Séries** (`series`): proposta, exemplos de temas, hashtags, peso (frequência) e
  formato — `format: song` (cantada) ou narrada. Crie quantas quiser. Hoje cerca de
  metade dos vídeos sai cantada; ajuste os pesos para mudar isso.
- **Músicas** (`song`): modelo da ElevenLabs, duração (20–40 s), estilos musicais
  aplicados a todas as músicas e nuances por parte (refrão, verso...).
- **Voz** (`voice`): vozes gratuitas em pt-BR, como `pt-BR-FranciscaNeural`,
  `pt-BR-AntonioNeural` e `pt-BR-ThalitaMultilingualNeural`; ajuste velocidade (`rate`)
  e tom (`pitch`).
- **Música** (`music`): coloque faixas livres de direitos em `assets/music/` (ex.:
  Biblioteca de Áudio do YouTube). Sem arquivos, o robô compõe uma melodia original
  para cada vídeo.
- **Publicação** (`publishing`):
  - `privacy_status: private` → o robô envia como privado e você revisa e publica à mão.
  - `schedule_times: ["11:00", "17:30"]` → cada execução gera um vídeo por horário e
    agenda a publicação.
- **Modelo do roteiro** (`llm.model`): padrão `claude-opus-5-5`.

## Revisão manual (opcional)

Se preferir aprovar antes de publicar:

```bash
python -m kidschannel make                 # gera em output/<pasta>/short.mp4
python -m kidschannel upload output/<pasta> # publica depois de assistir
```

---

## Regras do YouTube que você precisa conhecer

- **Conteúdo feito para crianças (COPPA)**: todo vídeo é enviado com
  `selfDeclaredMadeForKids = true`. Isso é exigido por lei e desativa comentários,
  anúncios personalizados, o sino de notificações, cards e telas finais. Por isso os
  roteiros não pedem "curta e se inscreva".
- **Monetização**: o Programa de Parcerias exige, entre outros, 1.000 inscritos e
  10 milhões de visualizações de Shorts em 90 dias. Canais infantis ganham menos por
  visualização (sem anúncios personalizados).
- **Conteúdo inautêntico / produzido em massa**: o YouTube não monetiza canais com
  vídeos repetitivos feitos em série sem valor próprio. Para se proteger: mantenha
  as séries variadas, revise os vídeos de vez em quando, melhore o mascote e as vozes,
  e publique com frequência moderada (1–2 por dia é mais que suficiente).
- **Selo de conteúdo sintético**: obrigatório só para conteúdo realista. Para desenho
  animado não é exigido; se mudar para estilo realista, ponha
  `contains_synthetic_media: true`.
- **Cota da API**: 10.000 unidades por dia por projeto. Um upload custa muito mais que
  uma consulta (historicamente ~1.600 unidades, ou seja, uns 6 envios por dia). Se
  precisar de mais, peça aumento no mesmo formulário da auditoria.

## Dicas para viralizar (já aplicadas nos roteiros)

- **Gancho no 1º segundo**: o refrão (ou uma pergunta surpreendente) começa imediatamente.
- **Repetição**: refrão repetido, onomatopeias e gestos — crianças pedem para ver de novo.
- **Loop**: o final puxa o começo, então o Short "recomeça" sem a criança perceber.
- **Curto**: 20–40 s nas músicas e 25–45 s nos narrados.
- **Personagem fixo**: o Pipo aparece sempre igual (use `assets/mascot.png` com a OpenAI).
- **Constância**: 1–2 vídeos por dia, sempre no mesmo horário.

## Custos aproximados por Short

| Item | Custo |
|---|---|
| Roteiro/letra + revisão (Claude) | alguns centavos de dólar |
| Música cantada (ElevenLabs Music, ~30 s) | créditos do plano da ElevenLabs (~390 créditos por 26 s no teste) |
| 4–8 ilustrações (OpenAI, qualidade média) | algumas dezenas de centavos de dólar; Pollinations: grátis |
| Voz da narração (Edge) / música de fundo / edição | grátis |
| GitHub Actions (~5 min por vídeo) | grátis em repositório público; 2.000 min/mês grátis em privado |

Os preços mudam; confira nas páginas de cada serviço.

---

## Estrutura do projeto

```
config.yaml                 # tudo que dá para personalizar
kidschannel/
  __main__.py               # comandos: auth, channel-kit, setup-channel, make, run, upload
  script_writer.py          # roteiro, letra das músicas + revisão de segurança (Claude)
  song.py                   # música cantada (ElevenLabs Music) + sincronia da letra
  images.py                 # ilustrações (OpenAI / Pollinations / local para testes)
  tts.py                    # narração com tempo de cada palavra (Edge / ElevenLabs)
  music.py                  # música de fundo original
  captions.py               # legendas animadas (ASS)
  video.py                  # montagem com FFmpeg (zoom suave, transições, mixagem)
  youtube.py                # login, upload, playlists e perfil do canal
  channel_kit.py            # mascote, foto de perfil e banner
  pipeline.py               # orquestra tudo
data/state.json             # histórico (criado automaticamente)
assets/mascot.png           # referência do personagem (gerada pelo channel-kit)
assets/music/               # suas músicas (opcional)
tests/                      # testes automatizados (python -m pytest)
```

## Problemas comuns

| Mensagem | Solução |
|---|---|
| `Não foi possível renovar o acesso ao YouTube` | Publique o app OAuth (*Em produção*) e rode `auth` de novo; atualize o secret `YOUTUBE_REFRESH_TOKEN`. |
| Vídeos ficam privados e não dá para tornar públicos | Falta a auditoria da API (passo 2.6). |
| `quotaExceeded` | Cota diária esgotada; renova à meia-noite do horário do Pacífico. |
| `Todos os provedores de imagem falharam` | Confira `OPENAI_API_KEY` ou tente mais tarde (Pollinations tem limite de uso). |
| `edge-tts falhou` | Instabilidade do serviço de voz; o workflow pode ser rodado de novo. |
| `Séries de música puladas` | Falta `ELEVENLABS_API_KEY`; sem ela só saem vídeos narrados. |
| `ElevenLabs Music falhou` / `quota_exceeded` | Créditos da ElevenLabs acabaram ou o plano não inclui música pela API. |
| Vídeo de teste (`--offline`) sem voz | Normal sem internet: o modo offline não chama nenhum serviço. Os vídeos de verdade falam/cantam. |
| Legenda com fonte diferente | Instale a fonte (`fonts-comic-neue`) ou troque `video.caption_font`. |
