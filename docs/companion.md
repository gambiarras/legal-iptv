# Companion

Playlist complementar ao Saimo TV Android, mantida separada da playlist completa.
O piloto foi solicitado em 14/11/2026. Código e artefatos deste repositório podem
ser publicados por Git; o agendamento Docker depende da versão correspondente
do `gambiarras/iptv-ops`. Nenhuma alteração no aplicativo Saimo é necessária para
importar canais, mas seu EPG de listas pessoais tem o limite descrito abaixo.

## Arquivos públicos

| Arquivo | Uso |
| --- | --- |
| `companion.m3u` | Seleção portátil de canais HTTP, sem FrostView e sem duplicar os canais normais visíveis do Saimo. |
| `companion-guide.xml` | XMLTV recortado para os IDs da seleção, com Fox Kids obrigatório atual e futuro. |
| `companion.meta.json` | Origem lógica, motivos de descarte e cobertura por canal; sem URLs de mídia ou credenciais nos diagnósticos. |
| `companion-youtube.kodi.m3u` | Lives públicas recentes para Kodi com o complemento YouTube configurado. Arquivo separado, sem URLs de mídia assinadas. |

Importação em player IPTV que aceita XMLTV:

- Lista: `https://raw.githubusercontent.com/gambiarras/legal-iptv/main/companion.m3u`
- Guia: `https://raw.githubusercontent.com/gambiarras/legal-iptv/main/companion-guide.xml`

O cabeçalho da M3U aponta somente para o guia Companion; não acrescenta o redirect
legado nem vários EPG conflitantes. Caso o player não importe esse cabeçalho,
configure manualmente o XMLTV e atualize ambos os arquivos. `tvg-id="FoxKids.br"`
é igual ao `channel id` e ao `programme channel`. O guia tem horários com fuso
explícito. Configurar o offset do player corretamente, sem somar três horas.

No Saimo Android inspecionado (`05942fd7551b4cbaaa95564f463eb19b3d53c6f2`),
Minhas listas aceita a M3U, porém o coletor de EPG só processa `Remote.channels` e
o leitor ignora o endereço XMLTV da M3U. Assim, **importar a Companion nesse app
não garante EPG dos canais novos**. Para ver Fox Kids com guia, usar um cliente
que aceite XMLTV externo ou adaptar esse caminho do app. A validação do piloto
é de dados/transporte; não foi realizada aceitação em uma TV física.

## Seleção e procedência

`services/companion_reference.py` reproduz a junção Android de `catalogo.txt` e
`canais.txt`, incluindo prioridade de `tvg-id`, remoção de parênteses dos extras,
normalização específica e filtro de hosts exatos. Preserva registros da base,
como Rede TV! e RedeTV!, mesmo quando o filtro Companion usa outra normalização.
Somente nomes, hashes e datas vão ao cache privado; URLs de transmissão Saimo
podem conter credenciais e nunca são copiadas desse catálogo para a Companion.

A referência Saimo trata os canais normais atualmente visíveis, sem rádios,
restritos ou VOD. Um canal filtrado pelo próprio Saimo pode entrar no complemento
por outra fonte; isso permite Retrô Cartoon. Presença em catálogo não equivale
a disponibilidade da transmissão, e o filtro não testa a saúde dos streams Saimo.

`resources/companion_policy.json` define a curadoria reproduzível:

1. Descarta provedores FrostView/addons não revisados e transportes associados,
   adultos, rádio, nomes/grupos alternativos e sinais identificados como pagos.
2. Preserva o nome original durante enriquecimento de EPG: o mapper não pode
   apagar “Alternativo” ou a região antes do filtro.
3. Remove canais coincidentes com a referência Saimo por identidade/aliases,
   ignorando qualidade, mas preservando diferenças regionais explícitas.
4. Admite Fox Kids/Retrô Cartoon e extras explicitamente revisados. Batista Play
   (`rest_catalog_1`), IPTV-ORG e outros extras precisam também corresponder ao
   diretório Televisão.TV, além de passar pelos bloqueios anteriores. Canal pago
   conhecido continua bloqueado mesmo se listado no diretório.
5. Seleciona um transporte por identidade: Batista Play revisado → Televisão.TV
   revisado → IPTV-ORG → extras, depois qualidade e bitrate. Remove repetição
   de URL. A seleção de fonte não é fallback instantâneo durante reprodução.

A lista de nomes pagos é conservadora e revisável; o diretório não fornece uma
certificação de direitos ou de que todos os canais sejam gratuitos. Candidatos
sem correspondência revisada ficam fora. Não se infere autorização a partir
de uma resposta HTTP pública. Mudanças da origem podem exigir nova revisão.

Televisão.TV `channels-data.php` contém metadados, **não uma M3U**. `exclude`
remove slugs literais: `exclude=sbt,tv-brasil` omite esses dois e preserva os
demais; slug desconhecido não muda a resposta. O índice foi usado para comparar
nomes, não como prova de playback. O adapter opt-in de descoberta no catálogo
limita slugs e hosts revisados. Players amostrados retornaram 404 no piloto;
nenhum HTML/iframe foi publicado como mídia. Retrô Cartoon usa fonte existente,
independente dessa falha. Detalhes: `live-stream-catalog/docs/televisao-source.md`.

## EPG

Open EPG foi acrescentado aos fallbacks padrão. A geração também mantém os
providers já existentes. Os coletores GuiaDeTv/MeuGuia têm parsers, fixtures,
cache e manifestos explícitos; ficam opt-in e desativados no piloto público
pelas condições de uso e pela falta de cobertura adicional confirmada. Ver
[coletores e proveniência](epg-collectors.md).

A programação Fox Kids usa o catálogo real apontado pela página do canal,
rejeitando primeiro o XMLTV publicado quando vencido. Respeita horários, dias,
vigência, ciclos finitos e eventos. Usa títulos genéricos de série/bloco quando
o episódio não é seguro; não desloca datas antigas e não fabrica uma grade para
ocultar falhas. Foram validados dias úteis, virada de dia e uma janela de sete
dias sem lacunas no snapshot de teste. Isso verifica a agenda declarada, não a
identidade de cada desenho transmitido no vídeo.

O guia Companion descarta programas encerrados, recorta até três dias e preserva
os horários da origem. Outros canais podem ficar sem programação; isso aparece
individualmente no metadata. Fox Kids precisa ter agora **e** futuro, mesmo que
o XMLTV agregado ainda tenha milhares de programas válidos de outros canais.

## YouTube

Uma página YouTube não é um stream HLS para Media3/TiviMate. URLs resolvidas
podem expirar e depender do IP; não são tornadas públicas para contornar isso.
O arquivo Kodi usa a [URI oficial do complemento](https://kodi.wiki/view/Add-on:YouTube)
`plugin://plugin.video.youtube/play/?video_id=...` com IDs públicos de lives
resolvidas recentemente. Exige o complemento e sua configuração no cliente;
não foi testado em TV. Não usar essa lista no Saimo ou num player sem resolver.

Seleciona uma live por identidade, exclui CazéTV e aliases de canais já Saimo,
rádios, itens offline e observações com mais de seis horas. O metadata diferencia
os canais Kodi da seleção portátil. As lives podem terminar antes da próxima
rodada; não há promessa de reprodução permanente nem grade inventada para elas.

## Geração e operação

Com `legal-iptv` instalado no ambiente do projeto:

```sh
python -m legal_iptv \
  --live-catalog-file ../live-stream-catalog/channels.json \
  --companion-output companion.m3u \
  --companion-meta-output companion.meta.json \
  --companion-guide-output companion-guide.xml \
  --companion-youtube-output companion-youtube.kodi.m3u
```

Isso mantém a geração normal da playlist principal e acrescenta os quatro
artefatos. Os caminhos precisam ser distintos. Em teste offline é possível
fornecer `--companion-reference-file` com snapshot sanitizado versão 2 válido.
`--companion-guide-url` altera apenas o endereço público do XMLTV no cabeçalho.

Referências: cache privado de seis horas, retenção máxima de 48 horas em falha,
sem renovar o timestamp antigo. Cache inválido é refeito; sem referência válida
a geração recusa publicar uma lista sem filtro. `--companion-reference-cache`
permite controlar o caminho. Caches dos coletores ficam em `.epg-cache/`, ignorados
no Git, com timestamps e limites preservados pelo ops.

O ops mantém geração/verificação/promoção/publicação no mesmo conjunto. Falha de
referência, saída ou EPG obrigatório **bloqueia a rodada inteira**, preservando a
publicação anterior. `LEGAL_COMPANION_ENABLED=false` recupera o contrato anterior
sem gerar/promover Companion. Não há desligamento silencioso de gates. O piloto
versionado não substitui o rebuild/redeploy do ops para renovar automaticamente
o guia; sem agendamento, os programas acabam no horizonte escrito no XMLTV.

## Verificação do piloto

Testes cobrem identidade regional, aliases, alternativos/pagos, dados FrostView,
headers/credenciais, referências incompletas, recuperação de cache, IDs XMLTV,
cobertura presente/futura obrigatória e saída Kodi. O ops também verifica
contagens, schemas, IDs de guia/playlist, URI Kodi estrita e rollback do conjunto.

Antes de publicar um novo piloto, verificar artefatos reais com
`iptv-ops/scripts/pipeline_artifacts.py:verify_companion`, conferir ausência de
URLs/headers sensíveis e registrar cobertura e probes separados. Sucesso de
manifesto/segmento HTTP não prova decodificação ou reprodução no aparelho final.
