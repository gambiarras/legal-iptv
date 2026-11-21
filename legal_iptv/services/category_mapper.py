"""Shared category names for source ingestion and playlist presentation."""

import unicodedata


def category_key(category: str | None) -> str:
    """Normalize accents, casing and whitespace for category lookup."""
    value = unicodedata.normalize("NFKD", category or "")
    unaccented = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(unaccented.casefold().split())


CATEGORY_MAP = {
    "auto": "Auto",
    "animation": "Animação",
    "business": "Negócios",
    "classic": "Clássico",
    "comedy": "Comédia",
    "cooking": "Cozinha",
    "culture": "Cultura",
    "documentary": "Documentário",
    "education": "Educação",
    "entertainment": "Entretenimento",
    "family": "Família",
    "general": "Variedades",
    "kids": "Infantil",
    "legislative": "Legislativo",
    "lifestyle": "Estilo de Vida",
    "movies": "Filmes",
    "music": "Música",
    "news": "Notícias",
    "outdoor": "Outdoor",
    "relax": "Relax",
    "religious": "Religião",
    "series": "Séries",
    "science": "Ciência",
    "shop": "Shop",
    "sports": "Esportes",
    "travel": "Viagem",
    "weather": "Clima",
    "web": "Web Live",
    "radio": "Rádio",
    "public": "Variedades",
    "recomendados": "Variedades",
    "recommended": "Variedades",
    "infantis": "Infantil",
    "noticia": "Notícias",
    "religioso": "Religião",
    "religiosos": "Religião",
    "aberto": "TV Aberta",
    "abertos": "TV Aberta",
    "canais abertos": "TV Aberta",
    "tv aberta": "TV Aberta",
    "internacional": "Internacional",
}


CATEGORY_ORDER = [
    "TV Aberta",
    "Variedades",
    "Filmes",
    "Séries",
    "Infantil",
    "Animação",
    "Clássico",
    "Web Live",
    "Notícias",
    "Cultura",
    "Família",
    "Entretenimento",
    "Negócios",
    "Comédia",
    "Documentário",
    "Religião",
    "Ciência",
    "Educação",
    "Estilo de Vida",
    "Esportes",
    "Shop",
    "Viagem",
    "Auto",
    "Cozinha",
    "Legislativo",
    "Música",
    "Outdoor",
    "Relax",
    "Clima",
    "Rádio",
    "Internacional",
]

CATEGORY_MAP.update({category_key(value): value for value in CATEGORY_ORDER})


def canonical_category_name(category: str) -> str:
    """Merge known aliases while preserving custom category names."""
    return CATEGORY_MAP.get(category_key(category), " ".join(category.split()))


def localized_category_name(category: str | None) -> str:
    """Localize a source category, using Variedades for unknown source labels."""
    return CATEGORY_MAP.get(category_key(category), "Variedades")
