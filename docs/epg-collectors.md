# Coletores de programação

Os providers existentes XMLTV, JSON e Solr continuam compatíveis. Novos adapters
não acessam streams nem executam JavaScript das origens. Apenas `requests` e a
biblioteca padrão são necessários. Não se importou código do Saimo.

## Um guia público, selecionado e datado

`ensure_guide(..., selected_channel_ids=ids, selected_channel_names=nomes, days=3)`
filtra os programas enquanto lê cada XMLTV em fluxo. Guarda somente programas dos
IDs selecionados que interceptam a janela atual de três dias, além do pequeno
inventário de canais. Não mantém a árvore inteira de cada upstream em memória.
A leitura usa `iterparse`, limpa cada elemento e remove-o do pai: elementos vazios
não ficam acumulados até o final de um arquivo de 96 MB.

Fontes oficiais/coletores têm precedência onde publicaram intervalos válidos;
as fontes configuradas vêm depois, seguidas dos XMLTV de reserva. O merge aplica
prioridade por canal e intervalo. Programa vencido, sem título, intervalo inválido
ou “No Data” não mascara programação inferior válida. Quando uma fonte inferior
atravessa um intervalo já escolhido, somente as partes descobertas são publicadas:
o título continua sendo o da origem, sem inventar programa ou episódio. O número
de partes com limites recortados aparece no diagnóstico. Não há sobreposições no
resultado. A validade de um intervalo não comprova correspondência ao vídeo real.

`project_guide(source_path, target_path, channel_ids, days=3, ...)` é o recorte final
após a seleção/enriquecimento do catálogo. Suporta `source_path == target_path`,
usa temporário e substituição atômica e preserva o mtime da coleta: projetar não
renova artificialmente TTL ou LKG. Em falha mantém o original e limpa o temporário
em `finally`. As duas listas usam a mesma URL canônica; compatibilidade de nomes
legados deve ser feita por alias, não por uma segunda geração de guia amplo.
Todo ID publicado recebe declaração `channel`, mesmo quando sua programação é
desconhecida; `channel_names={id: nome}` fornece o nome de apresentação nesses
casos. Isso não cria programas: a cobertura continua `missing`. A proteção de
queda de cobertura compara IDs com programas válidos, sem contar declarações
vazias como cobertura anterior. Fox Kids continua exigindo programa atual e futuro.

O XML compacto conserva título, subtítulo/episódio quando realmente publicados,
categoria, ícone, data e classificação. Remove descrições longas e créditos, além
de remover canais/programas não selecionados e dados fora da janela. Portanto a
redução de bytes resulta de **seleção + janela + compactação de metadados**, não
apenas de usar um parser em fluxo. Benchmark do snapshot de 96.291.985 bytes:
1.162.961 bytes de saída, 3,828 s e pico RSS de 46.907.392 bytes no processo Darwin.
Isso mede aquela seleção/snapshot, não uma garantia de tamanho constante.

A seleção, nomes e janela têm fingerprint privado em
`.epg-cache/guide-selection.json`. Mudá-los invalida a reutilização do guia; uma
segunda passagem pode reler os upstreams privados já coletados sem transferi-los
novamente. Um guia legado fresco e válido, ainda sem esse sidecar, é migrado sem
HTTP nem alteração do mtime. A validação temporal continua obrigatória.

`.epg-cache/channel-inventory.json` preserva IDs/nomes e contagens de programação
válida observadas no parse, inclusive dos canais não publicados. Isso permite o
enriquecimento em duas passagens sem publicar todo o XMLTV. Somente nomes unívocos
com programas atuais/futuros na janela viram aliases; um nome compartilhado por
vários IDs não escolhe arbitrariamente o primeiro. O timestamp do índice descreve
o parse, não garante frescor da origem ou disponibilidade de mídia. Diagnósticos
por ID mostram programa atual/futuro, horizonte, lacunas ou ausência. Só Fox Kids
é obrigatório no piloto; ausência de grade de uma TV extra permanece explícita.

## TV Sonata e Nova TVI

O provider oficial `tvsonata` usa
`https://www.tvsonata.com.br/render_grade.php?data=YYYY-MM-DD`, no máximo três
consultas de datas por coleta e somente quando `TVSonata.br` está selecionado.
Lê as horas inicial/final declaradas e o título do HTML; os metadados JSON em
`abrirDetalhes(...)` são analisados como dados inertes, sem executar `onclick`.
Virada de meia-noite respeita o fim declarado. Não atribui episódio/filme a uma
sessão que só publica um título de bloco. Dia vazio e intervalos não publicados
ficam vazios; falha de uma data não apaga os demais dias válidos.

O fuso é uma configuração operacional explícita, `America/Sao_Paulo` por padrão,
via `IPTV_EPG_TVSONATA_TIMEZONE`; a origem examinada não o declara. Na amostra de
15/11 havia três sessões (10:15–12:05, 12:35–14:45, 20:00–23:00) e nenhuma sessão
para 16/11. O estado `offline` da API de agora não foi interpretado como ausência
de transporte ou motivo para fabricar programação 24 horas.

O provider `novatvi` conserva a identidade `NovaTVI.br`/Nova TVI Petrópolis e lê o
HTML em ISO-8859-1. A semana examinada em `/programacao` estava inteiramente vazia.
Não transforma os nomes dos dias ou relógio da página em grade. O adapter só
aceita futuros registros estruturados `BroadcastEvent` com datas/início/fim
explícitos; caso contrário registra grade vazia. A mídia pode estar online mesmo
quando o EPG publicado está vazio. Fuso configurável em
`IPTV_EPG_NOVATVI_TIMEZONE`, com o mesmo default explícito.

## Fox Kids

O canal canônico é **FoxKids.br**. A entrada extra conserva `id=fkids.br` para
preservar a identidade local e agora informa `tvg_id=FoxKids.br`; aliases incluem
Fox Kids, FoxKids e o ID histórico. O provider `foxkids` vem habilitado por padrão;
`IPTV_EPG_FOXKIDS_ENABLED=false` o desativa.

Proveniência observada em 14/11/2026:

- [Página de programação](https://fkidsweb.blogspot.com/p/programacao.html).
- [XMLTV que a página usa no painel “no ar”](https://raw.githubusercontent.com/listamake/iptvbr/main/guide/epg.xml).
- [Catálogo que a tabela da página realmente lê](https://raw.githubusercontent.com/listamake/sitefkweb/main/catlog.xml).
- [Snapshot do catálogo auditado](https://github.com/listamake/sitefkweb/blob/49691486a0f4ae4cc2e0db420a0d9b7cdd7692f1/catlog.xml).

O XMLTV publicado estava vencido: 184 programas de `fkids.br`, entre 31/10 e
04/11, sem cobertura atual em 14/11. O adapter primeiro tenta esse XMLTV,
seleciona apenas Fox Kids e remapeia todos os IDs/referências. Só o aceita com
programa atual **e** futuro. Não desloca datas antigas para o presente.

Quando esse XMLTV falha ou vence, usa o catálogo declarado pela própria tabela:
`prog`, `hour days`, `length`, `start`, `date`, `repeat`, `onair` e categorias.
O fuso é `America/Sao_Paulo`, explicitado no XMLTV. Blocos respeitam dia da semana,
início de vigência e duração; filmes com data têm janela finita; eventos especiais
respeitam a quantidade declarada de ocorrências de origem. Eventos/filmes e blocos
mais recentes podem sobrepor blocos anteriores, sem produzir intervalos sobrepostos.
Virada de dia preserva o instante e o offset. Lacunas ficam como lacunas.

O fallback usa **títulos de séries e blocos declarados**, sem inferir episódios:
por exemplo, “Super Pig” nos seus horários de série e “Maratona: Super Pig” nos
horários do bloco. A simulação histórica controla apenas a vigência: quantidade
de registros `programme[progid]` multiplicada por `repeat`; reprises usam o ciclo
já iniciado; mudança de `<date>` reinicia a contagem. Mirrors herdam a quantidade
de episódios da série associada, respeitando os códigos/subconjuntos declarados.
Cancelamento por uma série mais recente não consome o orçamento da série antiga;
evento associado consome a exibição de origem normalmente. Após esgotar o ciclo,
a próxima tentativa de exibição original encerra também as reprises, impedindo
que o último episódio seja repetido indefinidamente. Horários sem data inicial
não ganham um fim inventado, seguindo a declaração aberta da própria fonte.

A precedência preserva programas novos, eventos e mudanças de data. Séries sem
horários próprios não ganham posição deduzida pela ordem dos nomes associados:
nesse caso permanece o título real do bloco. Se a fonte apresentar lacuna, ela
permanece; se o presente ficar sem cobertura, o collector falha. Diagnósticos
registram `granularity=series-and-block` (ou `block` quando só há blocos) e
`origin=declared-catalog`. Esses metadados internos não viram atributos XMLTV
públicos. Não se publicam `episode-num`/`sub-title` deduzidos.

A validação do catálogo real em 14/11, incluindo séries, gerou 279 intervalos na
janela de sete dias (um atual e 278 futuros), sem lacunas. A mesma fonte avaliada
para segunda-feira 16/11 à meia-noite BRT gerou 278 intervalos, um atual e 277
futuros, também sem lacunas. São evidências sobre esse snapshot, não promessa de
que a programação nunca mude. Amostras da segunda: 00h “Homem Aranha e Seus
Amigos”, 06h “Digimon 3”, 09h/12h/15h “Super Pig” e 18h “Medabots”.

Na amostra real de sábado 14/11, a grade de blocos inclui:

| BRT | Bloco declarado |
|---|---|
| 11:00–13:30 | Maratona: O Fantástico Mundo De Bobby |
| 13:30–16:00 | Maratona: Super Pig |
| 16:00–17:30 | Sábado Aranha |
| 17:30–18:30 | Sábado Verde |
| 18:30–21:00 | Evolusábado |
| 21:00–22:30 | Maratona: Shaman King, vigente desde 14/11 |
| 22:30–23:30 | Maratona: Shinzo, após a sobreposição do bloco mais recente |

Esses horários derivam dos dados publicados, não de alteração do XMLTV vencido.
São grade declarada pelo site; isso não valida os títulos contra o vídeo no ar.

## Cobertura obrigatória no piloto

`ensure_guide(..., required_channel_ids=("FoxKids.br",))` verifica presença e
futuro por canal obrigatório, além da validação global. O default vazio preserva
o comportamento dos consumidores existentes. Um arquivo com mtime recente, mas
Fox Kids ausente/vencido, não satisfaz essa opção. O último guia bom também tem de
passar pela mesma validação antes de ser retido.

Se o guia amplo ainda está fresco e saudável, mas falta apenas Fox Kids obrigatório,
a atualização consulta somente o collector Fox Kids e preserva os demais canais,
em vez de baixar todos os XMLTV grandes de novo. A programação Fox antiga é
removida dessa base em memória antes da junção. Em falha a publicação atômica não
substitui o arquivo existente e os diagnósticos indicam falha; o consumidor deve
respeitar esse status, não assumir que o arquivo antigo virou válido.

## Open EPG

`https://www.open-epg.com/files/brazil3.xml` foi acrescentado aos fallbacks padrão.
É XMLTV direto e usa o adapter existente. IPTV-EPG e Pluto já estavam configurados;
Open EPG não é um guia exclusivo Saimo. Na inspeção de 14/11, os 159 canais e 10.359
programas do Open EPG incluíam 145 canais com programa atual. Esses números não
comprovam cobertura de cada canal da playlist. IDs precisam ser reconciliados com
M3U `tvg-id` e XMLTV `channel id`/`programme channel`, preservando região/afiliada.

## GuiaDeTv e MeuGuia (opt-in)

O recurso `epg_site_mappings.json` contém somente nomes/IDs e identificadores de
páginas publicados no `guia.txt` examinado, sem streams ou grade copiada. O collector
MeuGuia mapeado fica habilitado por padrão, porém só consulta canais efetivamente
selecionados e correspondências exatas de ID/nome; não percorre todo o mapa.
Regiões não são aproximadas por prefixo e mapas Globo ambíguos foram excluídos.
`IPTV_EPG_MEUGUIA_ENABLED=false` o desativa. O máximo é 32 canais mapeados por coleta,
configurável por `IPTV_EPG_HTML_MAX_CHANNELS` até 128; excedentes aparecem como
ignorados por orçamento, sem criar programação. Há uma consulta por página em cache
quando múltiplos IDs a utilizam; não se renova o cache em cada reutilização.

GuiaDeTV mapeado requer `IPTV_EPG_GUIADETV_ENABLED=true`; permanece desativado por
padrão no piloto público por uma condição concreta: os
[termos do GuiaDeTV](https://www.guiadetv.com/info/termo-de-uso), seções 7.2–7.4,
restringem uso pessoal, processamento/cópia e disponibilização pública. A
observação técnica não estabelece autorização de republicação. MeuGuia não
apresentou licença expressa de redistribuição nas páginas examinadas; a coleta
implementada é limitada a horários/títulos/categorias factuais no
escopo desta integração, sem copiar sinopses extensas ou presumir direitos sobre
transmissões. Essas condições e atribuição devem acompanhar a operação.

Manifestos próprios continuam opcionais por `IPTV_EPG_GUIADETV_URL` e/ou
`IPTV_EPG_MEUGUIA_URL`, com URL de JSON controlado pelo operador:

Smoke privado em 14/11 confirmou os parsers para TV Brasil e TV Senado nos dois
sites. Não foi identificado canal exclusivo da Companion coberto nesses sites;
não atribuir programação de uma emissora a outra para elevar cobertura. Os
manifestos de exemplo em `docs/examples/` contêm somente IDs e URLs de páginas,
sem copiar a grade ou habilitar coleta.

```json
{
  "timezone": "America/Sao_Paulo",
  "channels": [
    {"id": "Band.br", "name": "Band", "url": "https://www.guiadetv.com/canal/band"}
  ]
}
```

Para MeuGuia, a mesma estrutura usa URLs como
`https://meuguia.tv/programacao/canal/BAN`. Cada manifesto atende um provider,
aceita de um a 32 canais e permite somente páginas HTTPS dos respectivos hosts.
IDs e nomes são explícitos; não há importação automática de mapas de canais pagos
nem aproximação entre afiliadas. Se forem necessários mais canais, dimensionar
novos lotes conscientemente, com cache e observação de limites.

GuiaDeTv: o HTML atual publica JSON-LD `BroadcastEvent` com `startDate`, `endDate`
e `name`. O adapter lê esses dados estruturados, sem executar scripts nem inferir
duração de elementos visuais. Datas com offset são preservadas; se a origem omite
o offset, o manifesto define o fuso. Eventos sem início/fim válido são descartados.

MeuGuia: lê a data dos cabeçalhos e a hora, título e categoria de cada item.
Infere o fim somente pelo início do programa seguinte. O último programa sem fim
conhecido é omitido; lacunas superiores a doze horas não viram um programa enorme.
A virada dezembro/janeiro é resolvida pela data próxima à coleta no fuso informado.

Coleta é sequencial com pausa de um segundo entre canais. Falha de um canal não
elimina os demais saudáveis e aparece em `failed_channels`; uma grade parcial datada
pode preencher seus intervalos, com cobertura atual/futura informada separadamente. Não se inventam programas para contornar
página alterada, vazia ou desatualizada.

## Cache, atualização e direitos

Cache privado em `output_path.parent/.epg-cache`: arquivos `<sha256-do-contexto>.data`,
bytes da origem e mtime da coleta. O contexto inclui URL e headers quando presentes,
sem salvar os valores no nome do arquivo. HTTP real usa resposta em fluxo e chunks
de 64 KiB; não materializa `response.content` para XMLTV. Gzip é lido em fluxo e o
limite também vale para os bytes decodificados. URLs/contextos idênticos no recurso
de providers são deduplicados antes da coleta. Duas passagens de aliases reutilizam
o mesmo arquivo privado, sem uma nova transferência do XMLTV.

Limites padrão e variáveis operacionais:

| Recurso | Default | Variável |
|---|---:|---|
| Cache privado total | 256 MiB | `IPTV_EPG_CACHE_MAX_BYTES` |
| Arquivos de cache | 96 | `IPTV_EPG_CACHE_MAX_FILES` |
| Idade máxima retida | 48 h | `IPTV_EPG_CACHE_MAX_AGE_SECONDS` |
| Upstream individual/decodificado | 128 MiB | `IPTV_EPG_UPSTREAM_MAX_BYTES` |
| Espaço livre mínimo | 200 MiB | `IPTV_EPG_MIN_FREE_BYTES` |

Um arquivo novo também está limitado à metade do orçamento de cache, para permitir
substituição atômica mantendo o antigo. O pruner roda antes, durante transferências
grandes e depois da gravação; remove os mais antigos até respeitar bytes/quantidade.
Temporários `epg-*.tmp` e `.write-*.tmp` abandonados há mais de uma hora são removidos
somente nesse diretório privado. O espaço livre é verificado antes/durante download;
exceder orçamento falha explicitamente. O buffer de rede e a verificação periódica
podem acrescentar até cerca de 4 MiB transitórios ao orçamento estável; não se criam
cópias persistentes sucessivas do XMLTV amplo.

Conteúdo `.data` nunca é alterado in-place: temporário + `replace`; `utime` somente
no novo inode. Cache hit e LKG não renovam mtime. XMLTV/JSON em fluxo só promovem o temporário
ao cache após o consumidor concluir o parse; um HTTP 200 com XML/JSON malformado
não substitui o payload anterior válido. Isso preserva hardlinks usados em
seed/backup/promoção. JSON e XML usam substituição atômica e limpeza `finally`.

HTML/JSON de collector tem teto adicional de 25 MiB. TTL: Fox/manifestos uma hora,
XMLTV e páginas HTML trinta minutos, Sonata quinze minutos. LKG de upstream/HTML
pode ser usado por até seis horas em falha, com data original nos diagnósticos;
continua sujeito à validação real dos intervalos. Não desloca datas nem preenche
programação ausente. Não publicar `.epg-cache` nos artifacts públicos: ele pode
conter resposta bruta ou manifesto privado. Só o XML compacto é publicado.

O TTL global do `guide.xml` continua sob configuração do pipeline. Além de manter
cache entre execuções, o piloto deve chamar `required_channel_ids` e reagendar
antes que a cobertura obrigatória desapareça; TTL sozinho não garante frescor de
programação. Cobertura contínua deve ser medida a cada versão dos dados, não presumida pelo TTL.

Condições observadas: repositório `listamake/iptvbr` não declarou licença na API
consultada; a página Fox Kids aponta publicamente os dados. O robots de GuiaDeTv
permite páginas públicas e exclui busca; isso não é licença de republicação. O
endpoint robots de MeuGuia retornou HTML do próprio site, sem política legível;
a política de privacidade não foi tratada como autorização para coleta de grades.
Não foi encontrada autorização expressa para redistribuição irrestrita desses
dados. O operador deve confirmar condições de uso para a publicação pretendida.
A autorização de programação e a autorização de retransmitir vídeo são distintas.

## Validação

Fixtures sintéticas exercitam virada de dia e ano, fuso, duração explícita,
eventos/filmes finitos, séries em dias úteis, ciclos finitos e reinício de datas,
precedência e cancelamentos, falta de cobertura, XMLTV vencido,
remapeamento de IDs, cache/outage, falha parcial e reparo seletivo de guia fresco.
Nenhum teste acessa streams, APKs ou executa código baixado.
