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
