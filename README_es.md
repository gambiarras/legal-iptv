# legal-iptv

<!-- hy-mt2-i18n:start -->
[English](./README.md) | [中文](./README_zh-CN.md) | [日本語](./README_ja.md) | **Español**
<!-- hy-mt2-i18n:end -->


Agrega fuentes públicas de IPTV y transmisiones en vivo en una lista de reproducción M3U estática.

Este proyecto combina canales de múltiples fuentes, como:

- IPTV-ORG  
- Canales seleccionados manualmente desde `extra_channels.json`  
- Canales generados por `live-stream-catalog`

El resultado es un archivo estático `playlist.m3u` que se puede publicar en GitHub sin necesidad de alojamiento pagado.

# Restricciones estrictas
1. **Bloqueo estructural**: Se debe mantener intacta por completo la estructura de datos en Markdown original, incluyendo la indentación, los niveles de título, las tablas, los enlaces, las URL, las insignias, los bloques de código y el código dentro de las líneas.
2. **Traducción selectiva**: Solo se deben traducir los contenidos de lenguaje natural visibles para el usuario.
3. **Prohibición de modificaciones**: Está **estrictamente prohibido** traducir o cambiar etiquetas de código, nombres de claves, placeholders de variables (como {{var}}, ${var}, %s, %d, etc.), ejemplos de comandos, rutas de archivos, nombres de proyectos, nombres de API, nombres de paquetes, nombres de modelos, identificadores y símbolos de código; a menos que ya se haya proporcionado una traducción correspondiente en la información de contexto.
4. La traducción de términos, estilos y nombres propios debe ser coherente con la información de contexto proporcionada.

## ⚠️ Aviso legal / Nota legal

Este repositorio **no alberga, transmite, vuelve a transmitir ni redistribuye contenido audiovisual**.

Únicamente hace lo siguiente:

- recopila metadatos y direcciones URL de transmisión de fuentes de acceso público  
- agrega estas fuentes en una única lista de reproducción  
- facilita el acceso a estas direcciones URL de transmisión disponibles públicamente mediante un archivo M3U legible por máquinas

Todo el contenido:

- es servido directamente por las plataformas originales, proveedores, emisoras o CDN.  
- permanece bajo la responsabilidad de los respectivos propietarios del contenido, emisoras y plataformas.  
- puede estar sujeto a cambios en su disponibilidad, bloqueo geográfico, restricciones de licencia, políticas de la plataforma o eliminación en cualquier momento.

Este proyecto:

- no elude muros de pago, sistemas de autenticación, DRM ni controles de acceso  
- no modifica, vuelve a transmitir, refleja, hace proxy ni aloja de nuevo contenido multimedia  
- no garantiza la legalidad, las licencias, la disponibilidad, el tiempo de funcionamiento ni la validez a largo plazo de ninguna transmisión enumerada

La lista de reproducción generada se proporciona **únicamente con fines informativos y de conveniencia**.

Los usuarios son los únicos responsables de garantizar el cumplimiento de:

- las leyes y regulaciones locales  
- las normas relativas a derechos de autor y derechos conexos  
- los términos de servicio de la plataforma  
- cualquier restricción contractual o de licencia aplicable en su jurisdicción

Si algún canal, transmisión o fuente no debe incluirse en la lista, la acción adecuada es eliminarlo de la configuración de la fuente o del catálogo upstream.

# Restricciones estrictas
1. **Bloqueo estructural**: Se debe mantener intacta por completo la estructura de datos en Markdown original, incluyendo el sangrado, los niveles de título, las tablas, los enlaces, las URL, las insignias, los bloques de código y el código dentro de las líneas.
2. **Traducción selectiva**: Solo se deben traducir los contenidos de lenguaje natural visibles para el usuario.
3. **Prohibición de modificaciones**: Está **estrictamente prohibido** traducir o cambiar etiquetas de código, nombres de clave, placeholders de variables (como {{var}}, ${var}, %s, %d, etc.), ejemplos de comandos, rutas de archivos, nombres de proyectos, nombres de API, nombres de paquetes, nombres de modelos, identificadores y símbolos de código; a menos que ya exista una traducción correspondiente en la información de contexto.
4. Las traducciones de términos, estilos y nombres propios deben ser consistentes con la información de contexto proporcionada.

## Objetivos

- generar un `playlist.m3u` público sin necesidad de alojamiento pagado  
- reunir fuentes de streaming legales y accesibles al público  
- utilizar de forma segura los resultados del `live-stream-catalog`  
- mantener el proyecto fácil de mantener y ampliable  
- preparar la base de código para futuras integraciones de fuentes

# Restricciones estrictas
1. **Bloqueo estructural**: Mantener absolutamente intacta la estructura de datos en Markdown original, incluyendo el sangrado, los niveles de título, las tablas, los enlaces, las URL, las insignias, los bloques de código y el código dentro de las líneas.
2. **Traducción selectiva**: Solo traducir el contenido de lenguaje natural visible para el usuario.
3. **Prohibición de modificaciones**: Está **estrictamente prohibido** traducir o alterar etiquetas de código, nombres de claves, placeholders de variables (como {{var}}, ${var}, %s, %d, etc.), ejemplos de comandos, rutas de archivos, nombres de proyectos, nombres de API, nombres de paquetes, nombres de modelos, identificadores y símbolos de código; a menos que ya se haya proporcionado una traducción correspondiente en la información de contexto.
4. La traducción de términos, estilos y nombres propios debe ser coherente con la información de contexto proporcionada.

## Cómo funciona

El proyecto obtiene y combina canales de diferentes fuentes:

### 1. IPTV-ORG
Carga la metadatos de los canales, las URL de transmisión y los logotipos de los conjuntos de datos públicos de IPTV-ORG.

### 2. Canales adicionales
Carga los canales seleccionados manualmente desde `extra_channels.json`.

### 3. live-stream-catalog
Carga los canales resueltos dinámicamente desde el repositorio `live-stream-catalog`.

Esta fuente puede incluir metadatos como:

- `stream_url`
- `status`
- `resolved_at`
- `expires_at`
- `ttl_seconds`

El pipeline de agregación filtra y selecciona los canales, y luego genera la lista de reproducción M3U final.

---

## Uso local

Al ejecutar ambos repositorios en la misma máquina, utilice la salida local de `live-stream-catalog`:

```bash
python3.11 -m legal_iptv \
  --live-catalog-file../live-stream-catalog/channels.json \
  --output playlist.m3u \
  --meta-output playlist.meta.json
```

Cuando se especifica `--live-catalog-file`, el archivo debe existir. Esto evita que las ejecuciones locales recurran silenciosamente a un catálogo remoto.

Los elementos de `live-stream-catalog` tienen una prioridad de selección mayor que los canales adicionales curados manualmente y los canales de IPTV-ORG. Si hay candidatos duplicados que apuntan a la misma URL y tienen un nombre igual o muy similar, solo se conservará el mejor candidato. Si sus URLs son diferentes, se mantendrán ambos y se harán únicos los ID de los canales duplicados.

Opcionalmente, valide las URLs de los streams antes de generar la lista de reproducción:

```bash
python3.11 -m legal_iptv \
  --live-catalog-file../live-stream-catalog/channels.json \
  --validate-streams \
  --validation-max-workers 32 \
  --validation-timeout 6
```

Por defecto, la validación está desactivada ya que realiza comprobaciones de red en cada URL de streaming única. Cuando se activa, escribe en `stream-status.json` el estado más reciente de cada URL.

En entornos programados, ejecute la validación periódicamente, por ejemplo cada 4 horas, fuera de este repositorio. La generación normal de la lista de reproducción lee `stream-status.json` y omite las URLs que hayan sido marcadas recientemente como offline:

```bash
python3.11 -m legal_iptv \
  --live-catalog-file../live-stream-catalog/channels.json \
  --stream-status-file stream-status.json \
  --stream-status-max-age 14400
```

Solo se aplican los estados de fuera de línea más recientes que superen el valor de `--stream-status-max-age`, de modo que los fallos obsoletos no bloquearán los canales de forma permanente.

## Desarrollo

Ejecutar las pruebas unitarias:

```bash
python3.11 -m unittest discover -s tests
```

## Desarrollo

Ejecutar las pruebas unitarias:

```bash
python3.11 -m unittest discover -s tests
```

## Estructura del proyecto

## Estructura del proyecto

legal_iptv/
  models/       # modelos de dominio
  io/           # herramientas para la persistencia de archivos
  clients/      # abstracción del cliente HTTP
  sources/      # incorporación de canales desde cada fuente
  services/     # lógica de agregación, selección y metadatos
  exporters/    # generación de listas de reproducción
  resources/    # recursos estáticos como canales adicionales
