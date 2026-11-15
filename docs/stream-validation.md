# Validação de transporte e readmissão

O estado HTTP sozinho não comprova vídeo. A versão 2 da sonda usa GET, confere
manifesto HLS e busca imediatamente o último segmento anunciado; se ele ainda responde 404/410, tenta uma vez o
penúltimo segmento. A ausência das duas amostras é inconclusiva, sem tombstone
da URL raiz. Em playlists
master escolhe somente a variante de menor BANDWIDTH; fMP4 exige inicialização e
fragmento com payload. TS direto também admite amostra. HTML/JSON 200 e mídia
vazia reprovam. Nenhuma mídia é escrita no disco e a sonda não decodifica vídeo,
mede qualidade audiovisual ou comprova atualidade dos quadros.

Cada contexto tem prazo compartilhado entre as requisições, até 8 GETs, até
256 KiB por manifesto e 64 KiB por inicialização/segmento. O leitor limita bytes
mesmo quando o servidor ignora Range. Cada connect/read tem no máximo 3 segundos
ou o prazo restante. DNS e uma leitura já iniciada dependem dos limites do socket;
o prazo compartilhado é conferido entre leituras e não é um cancelamento rígido
do sistema operacional. Não há retries automáticos. No máximo 32 trabalhadores,
com sessões reutilizadas dentro do lote, pools de 4 origens/1 conexão por origem,
e fechamento dos pools ao terminar o lote.

Os headers declarados chegam à mesma origem. Redirecionamentos e referências
HLS para outra origem recebem somente headers públicos da lista segura
(User-Agent, Accept e Range, por exemplo); Authorization, Cookie, Referer e
headers particulares não atravessam origens. Não se executam players, JavaScript
ou aquisição de chaves. DASH, HLS criptografado, byte ranges e amostras sem formato
reconhecido são inconclusivos, com motivo próprio. HTTP 429/5xx e falhas de rede
também são inconclusivos. Limitações do sampler não incrementam falhas nem
suprimem um contexto ainda não reprovado, mesmo após várias tentativas; voltam
a ser verificadas na cadência base. Um contexto já reprovado permanece retido. O erro gravado contém somente o tipo, sem texto da
exceção ou URLs de requisições intermediárias.

## Cache, falhas e recuperação

`refresh_stream_status(..., force=False)` preserva a agenda adaptativa existente:
sonda somente contextos devidos; `force=True` solicita nova observação de todos os
contextos não expirados. `filter_cached_offline_channels` apenas lê estado e não
faz rede. A agenda operacional precisa executar validação para permitir recuperação.

`next_check_at` vencido significa que é hora de repetir a sonda. Não significa
que uma URL reprovada voltou a funcionar. Falhas ficam suprimidas até uma nova
observação real de mídia válida. Um retry inconclusivo mantém a supressão anterior.
O fingerprint separa URL, headers, qualidade e validade; um contexto novo não
herda a falha de outro. Isso também vale para URLs resolvidas de YouTube/Twitch/Kick.
Falhas inconclusivas de contextos ainda não reprovados mantêm a política existente
de limiar de tentativas antes da supressão temporária.

Para publicar somente transportes comprovados, o consumidor chama
`filter_media_confirmed_channels(channels, status_file=..., max_age_seconds=86400)`
antes de escolher prioridade e qualidade. Esse filtro exige contexto exato,
`probe_version=2`, `validation_level=transport_sample` e sucesso real recente.
Um inconclusivo inicial fica no estado para novos testes, mas não disputa a
seleção com uma alternativa comprovada. A API genérica de cache negativo continua
disponível para consumidores antigos e não impõe essa prova positiva sozinha.

Cada sucesso real grava `last_media_success_at`. Uma primeira tentativa posterior
inconclusiva pode reutilizar essa prova até completar 24 horas, sem renovar o
timestamp; duas falhas ou rejeição definitiva impedem reutilização. Um novo
sucesso real permite recuperação. Registros de sucesso amostrado v2 produzidos
antes desse campo usam `checked_at`, que já identifica a observação real. HEAD
legado e cache desconhecido sem sucesso anterior nunca qualificam como prova.
Reutilizar o cache não altera nenhum desses timestamps. O prazo do helper pode
ser reduzido pelo chamador; a rotina não faz requisições.

Sucessos HEAD antigos são revalidados uma vez (`probe_version=2`), sem herdar sua
estabilidade. Cache reutilizado não renova `checked_at`, histórico ou contadores.
Contextos negativos omitidos por um perfil permanecem no estado. Ao contrário,
registros positivos omitidos podem ser descartados; ausência não comprova sucesso.

`update_extra_removals(..., validation_by_channel={id: UrlValidation(...)})`
usa a observação do contexto de cada extra: 404/410 da URL raiz criam tombstone;
404 de segmento não condena definitivamente o manifesto. HTTP 200 sem mídia válida
não remove tombstone. IDs filtrados/omitidos são preservados; uma URL configurada
nova substitui somente o tombstone da URL anterior. Sucesso real da mesma URL
permite readmissão. O chamador da validação deve incluir extras removidos para
que voltem a ser sondados; geração comum continua respeitando o estado persistido.

## Armazenamento e operação

O estado privado contém URLs completas necessárias ao cache/tombstones. Headers
são identificados por fingerprint e não são persistidos. Não publicar esses
arquivos de estado: rankings/histórico usam identificadores e rótulos sanitizados.
Há limites de 10.000 contextos de transporte e 5.000 tombstones de extras. Ao exceder,
a operação falha antes de sobrescrever o arquivo correspondente; não há descarte
silencioso que possa ressuscitar uma URL. Revisar/remover apenas estado obsoleto
com identidade e origem conhecidas, mantendo backup privado. Apagar o estado
inteiro equivale a perder as rejeições e requer nova validação antes de publicar.

A sonda positiva comprova somente transporte reconhecível nessa amostra. Não
comprova direitos de redistribuição nem garante funcionamento posterior ou EPG.

Testes sem rede: `python -m unittest tests.unit.test_media_proof tests.unit.test_transport_probe
 tests.unit.test_link_validator tests.unit.test_adaptive_validation
 tests.unit.test_stream_status tests.unit.test_extra_removal` (em uma linha).
