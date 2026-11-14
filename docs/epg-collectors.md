# Coletores de programação

Os providers existentes XMLTV, JSON e Solr continuam compatíveis. Novos adapters
não acessam streams nem executam JavaScript das origens. Apenas `requests` e a
biblioteca padrão são necessários. Não se importou código do Saimo.

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

Não são habilitados em massa por padrão. Configure `IPTV_EPG_GUIADETV_URL` e/ou
`IPTV_EPG_MEUGUIA_URL` com URL de manifesto JSON controlado pelo operador:

**No piloto público os dois coletores HTML ficam desativados.** A verificação
posterior encontrou [termos do GuiaDeTV](https://www.guiadetv.com/info/termo-de-uso),
seções 7.2–7.4, que restringem uso ao âmbito pessoal e vedam processamento/cópia
e disponibilização pública. Os testes técnicos não autorizam republicação.
MeuGuia não apresentou licença de redistribuição nas páginas inspecionadas.
Habilitar coleta pública exige resolver essas condições com a origem.

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
elimina os demais saudáveis e aparece em `failed_channels`; cada canal admitido
precisa ter programa atual e futuro. Não se inventam programas para contornar
página alterada, vazia ou desatualizada.

## Cache, atualização e direitos

Cache privado em `output_path.parent/.epg-cache`: arquivos `<sha256-da-url>.data`,
bytes da origem e mtime de coleta, substituídos atomicamente. Limite de payload:
25 MiB. TTL uma hora para Fox Kids e manifestos; trinta minutos para páginas HTML.
Cache vencido não é usado em falha e não tem mtime renovado em outage. Mesmo bytes
recentes precisam gerar cobertura temporal válida. Não publicar esse diretório
como parte dos artifacts públicos. Pode conter conteúdo bruto ou manifesto privado.

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
