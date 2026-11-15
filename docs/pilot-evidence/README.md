# Companion — piloto de 14/11/2026

Snapshot publicado: 69 canais HTTP na Companion e nove lives na lista Kodi,
contra 1.383 entradas no artefato completo local de referência. O arquivo
completo não foi regenerado nem substituído pela geração deste piloto.

A referência Saimo Android tem 249 canais normais visíveis após os filtros;
o diretório Televisão.TV contém 371 nomes no snapshot. Contagens dependem dos
dados coletados e não são invariantes. Nenhum canal FrostView foi selecionado;
alternativos, pagamentos conhecidos e coincidências Saimo foram descartados.

Fox Kids: `tvg-id=FoxKids.br`, 97 programas atuais/futuros no recorte de três
dias, até 17/11/2026 às 16:00 UTC. Agora e futuro foram verificados individualmente.
O catálogo de agenda foi convertido sem mudar datas antigas. Uma simulação
separada de sete dias, incluindo segunda-feira, não apresentou lacunas no
snapshot da fonte. Títulos são de séries/blocos; episódios não foram deduzidos.

Cinco dos 69 canais têm programa atual no XMLTV recortado desta amostra. Os
demais possuem declaração de canal para manter IDs consistentes, mas não têm
grade inventada. O metadata público mostra cobertura de cada um. Os coletores
HTML foram exercitados em smoke privado; ficam desativados no piloto público.

Verificações do conjunto real:

- Verificador do ops aprovou contagens, IDs, cobertura Fox Kids, URIs Kodi e
  compatibilidade do guia com a playlist.
- Os 69 `tvg-id` são únicos/não vazios. Os IDs declarados no XMLTV correspondem
  aos da M3U. Nenhuma URL FrostView ou credencial conhecida na varredura dos
  quatro artefatos (userinfo/query sensível).
- Fox Kids e Retrô Cartoon **selecionado** responderam com playlist HLS válida
  e amostra recente de segmento MPEG-TS de 64 KiB, em duas requisições por canal.
- Não foi feita reprodução em TV, decodificação nem uma segunda observação
  para provar progressão temporal. Resposta de mídia não comprova direitos de uso.

Os dois JSON anexos contêm somente status, tamanhos, tipos e resultados das
sondagens. Não incluem URLs de mídia ou credenciais. A renovação automática
depende de instalar a versão atualizada do ops; o guia estático tem horizonte
finito, e sucesso deste piloto não comprova atualização em produção.
# Unified lists, 15 November 2026

The [evolution verification](evolution-2026-11-15.json) records the updated
selection and storage checks: 294 main channels, 229 Companion channels, no
duplicate identities or Saimo overlap, and a shared guide of 995,025 bytes.
The guide declares all selected IDs; only channels with actual source schedules
have programmes. Fox Kids has 114 slots, TV Sonata three dated slots, and Nova
TVI no invented schedule. Chespirito was not published.

Companion now subtracts Saimo from the same main winners; it is no longer gated
by membership in the Televisão.TV directory. This intentional change explains
the larger complementary count compared with the first narrow pilot below.

Media was sampled with bounded GETs on 15 November. Subsequent generation reused
those private observations; a fresh successful sample is required for a new or
rejected context. These checks do not prove continuous availability. The public
evidence contains counts and timings, never private status URLs or raw caches.

Warm generation took about 0.54 seconds after the selection cache stabilized.
The separate projection benchmark processed a 96 MB source with a process peak
RSS of approximately 47 MB. Its smaller output also omits long descriptions;
the benchmark is not a server/browser measurement.
