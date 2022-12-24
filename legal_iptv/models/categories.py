def __get_element(values, key):
    element = next(filter(lambda element: key in element, values), None)

    if element is not None:
        return element[key]

    return 'Variedades'

def localized_category_name(category):
    categories = [
        { 'auto': 'Auto' },
        { 'animation': 'Animação' },
        { 'business': 'Negócios' },
        { 'classic': 'Clássico' },
        { 'comedy': 'Comédia' },
        { 'cooking': 'Cozinha' },
        { 'culture': 'Cultura' },
        { 'documentary': 'Documentário' },
        { 'education': 'Educação' },
        { 'entertainment': 'Entretenimento' },
        { 'family': 'Família' },
        { 'general': 'Variedades' },
        { 'kids': 'Infantil' },
        { 'legislative': 'Legislativo' },
        { 'lifestyle': 'Estilo de Vida' },
        { 'movies': 'Filmes' },
        { 'music': 'Múscia' },
        { 'news': 'Notícia' },
        { 'outdoor': 'Outdoor' },
        { 'relax': 'Relax' },
        { 'religious': 'Religião' },
        { 'series': 'Séries' },
        { 'science': 'Ciência' },
        { 'shop': 'Shop' },
        { 'sports': 'Esportes' },
        { 'travel': 'Viagem' },
        { 'weather': 'Clima' },
        { 'web': 'Web Live'},
        { 'pluto': 'PlutoTV' },
        { 'radio': 'Rádio' }
    ]

    return __get_element(categories, category)
    