from legal_iptv.local_config import runtime_value

PRIMARY_EPG_URL = runtime_value("primary_epg_url", "https://guide.example.invalid/guide.xml")
EPG_URLS = runtime_value("epg_urls", [])
EPG_INDEX_URLS = runtime_value("epg_index_urls", [])
