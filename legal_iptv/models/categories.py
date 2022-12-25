def __get_element(values, key):
    element = next(filter(lambda element: key in element, values), None)

    if element is not None:
        return element[key]

    return 'Variedades'

def get_categories_ordered():
    return [
        'Variedades',
        'Filmes',
        'Séries',
        'Infantil',
        'Animação',
        'Clássico',
        'Web Live',
        'Notícia',
        'Cultura',
        'Família',
        'Entretenimento',
        'Negócios',
        'Comédia',
        'Documentário',
        'Religião',
        'Ciência',
        'Educação',
        'Estilo de Vida',
        'Esportes',
        'Shop',
        'Viagem',
        'Auto',
        'Cozinha',
        'Legislativo',
        'Música',
        'Outdoor',
        'Relax',
        'Clima',
        'PlutoTV',
        'Rádio'
    ]

def __get_categories():
    return [
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
        { 'music': 'Música' },
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

def localized_category_name(category):
    categories = __get_categories()
    return __get_element(categories, category)
    