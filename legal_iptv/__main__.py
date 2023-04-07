from services import extra_channels_service, iptv_org_service, youtube_channels_service
from converters import m3u_converter
from checkers import m3u8_checker

extra_channels = extra_channels_service.fetch_channels()
iptv_org_channels = iptv_org_service.fetch_channels()
web_channels = youtube_channels_service.fetch_channels()

channels = extra_channels + iptv_org_channels + web_channels
m3u = m3u_converter.convert(channels)

with open('playlist.m3u', 'w', encoding="utf-8") as outfile:
    outfile.write(m3u)
