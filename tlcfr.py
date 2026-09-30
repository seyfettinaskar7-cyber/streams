import os
import re
import json
from urllib.parse import urljoin, urlparse, urlunparse
import requests

class VimeoExtractor:
    def __init__(self, url: str):
        self.url = url
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.9",
            "Referer": "https://vimeo.com/",
            "Origin": "https://vimeo.com",
            "Sec-GPC": "1"
        })

    def _clean_url(self, url: str) -> str:
        """URL yolundaki '../' kalıntılarını imza ve token yapılarını bozmadan güvenle temizler."""
        url = re.sub(r'\.\.\.', '', url)
        parsed = urlparse(url)
        segments = parsed.path.split('/')
        new_segments = []
        for seg in segments:
            if seg == '..':
                if new_segments:
                    new_segments.pop()
            elif seg != '.':
                new_segments.append(seg)
        
        new_path = '/'.join(new_segments)
        if not new_path.startswith('/'):
            new_path = '/' + new_path
            
        return urlunparse((parsed.scheme, parsed.netloc, new_path, parsed.params, parsed.query, parsed.fragment))

    def extract(self):
        print(f"[*] İşleniyor: {self.url}")
        parsed_url = urlparse(self.url)
        path_parts = [p for p in parsed_url.path.split("/") if p]
        
        config_url = None

        # 1. Yöntem: Streamlink mantığındaki Event Embed çözümleyicisi
        if "event" in path_parts:
            try:
                idx = path_parts.index("event")
                event_id = path_parts[idx + 1]
                print(f"[*] Event ID tespit edildi: {event_id}, embed sayfası taranıyor...")
                config_url = self._get_config_url_event(event_id)
            except Exception as e:
                print(f"[-] Event embed çözülemedi: {e}")

        # 2. Yöntem: Streamlink mantığındaki Viewer (JWT) + Oembed API zinciri
        if not config_url:
            print("[*] Viewer ve Oembed API üzerinden yetkilendirme deneniyor...")
            config_url = self._get_config_url_via_streamlink_logic()

        # 3. Yöntem: Doğrudan video ID config yapısı
        if not config_url:
            video_id = self._get_video_id()
            if video_id:
                config_url = f"https://player.vimeo.com/video/{video_id}/config"

        if not config_url:
            print("[-] Hiçbir şekilde Config URL oluşturulamadı.")
            return None

        print(f"[+] Config URL sağlandı: {config_url}")
        
        # Config verisini uygun referer ile çek
        config_data = self._get_json_with_referer(config_url, self.url)

        # 4. Yöntem: Sayfa kaynağındaki window.playerConfig değişkenini kazıma
        if not config_data:
            print("[*] JSON doğrudan alınamadı, sayfa kaynağı (window.playerConfig) taranıyor...")
            video_id = self._get_video_id()
            if video_id:
                player_page = f"https://player.vimeo.com/video/{video_id}"
                config_data = self._get_config_from_html(player_page)

        if not config_data:
            print("[-] Konfigürasyon verilerine ulaşılamadı. Video yayından kalkmış, şifreli veya coğrafi kısıtlı olabilir.")
            return None

        # HLS (m3u8) adresini JSON içerisinden ayıkla (Önce akamai_live altındaki json_url aranır)
        try:
            cdns = config_data.get("request", {}).get("files", {}).get("hls", {}).get("cdns", {})
            
            if "akamai_live" in cdns:
                json_url = cdns["akamai_live"].get("json_url")
                if json_url:
                    json_url = self._clean_url(json_url)
                    print(f"[*] akamai_live altından json_url bulundu, içerik indiriliyor...")
                    
                    m3u8_response = self.session.get(json_url, headers={"Referer": self.url}, timeout=10)
                    if m3u8_response.status_code == 200:
                        m3u8_json_content = m3u8_response.json()
                        
                        m3u8_url = m3u8_json_content.get("url") or m3u8_json_content.get("hls_url")
                        if not m3u8_url and isinstance(m3u8_json_content, dict):
                            for k, v in m3u8_json_content.items():
                                if isinstance(v, str) and (".m3u8" in v or "http" in v):
                                    m3u8_url = v
                                    break
                        
                        if m3u8_url:
                            m3u8_url = self._clean_url(m3u8_url)
                            print(f"[+] Başarılı! m3u8_url json_url içeriğinden yakalandı.")
                            return m3u8_url

            for cdn_name, cdn_info in cdns.items():
                hls_url = cdn_info.get("json_url") or cdn_info.get("url")
                if hls_url:
                    hls_url = self._clean_url(hls_url)
                    print(f"[+] Başarılı! CDN: {cdn_name}")
                    return hls_url
        except Exception as e:
            print(f"[-] HLS verisi işlenirken hata oluştu: {e}")

        return None

    def get_processed_playlist(self, hls_url: str):
        """M3U8 içeriğindeki tüm linkleri temizler, ../ kalıntılarını çözer ve base_url uygular."""
        
        hls_url = self._clean_url(hls_url)
        parsed_hls = urlparse(hls_url)
        query_string = parsed_hls.query
        
        # URL yapısına göre base_url oluşturma (Tekil kalite veya Master)
        if "chunklist.m3u8" in hls_url:
            base_url = re.sub(r'[^/]+$', '', hls_url.split('?')[0])
        else:
            base_url = re.sub(r'/avc/hls\.m3u8.*$', '/', hls_url)
            
        print(f"[+] Base URL oluşturuldu: {base_url}")

        headers = {
            "Referer": self.url,
            "Origin": "https://vimeo.com",
            "User-Agent": self.session.headers.get("User-Agent")
        }

        response = self.session.get(hls_url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"[-] M3U8 içeriği indirilemedi. HTTP Kod: {response.status_code}")
            return None

        updated_lines = []
        for line in response.text.splitlines():
            line = line.strip()
            if not line:
                continue
            
            # Satırdaki ../ ve ... kalıntılarını güvenle temizle
            line = self._clean_url(line)
            
            if line.startswith("#"):
                if 'URI="' in line:
                    def replace_uri(match):
                        uri_val = match.group(1)
                        uri_val = self._clean_url(uri_val)
                        
                        if not uri_val.startswith(("http://", "https://")):
                            uri_val = base_url + uri_val
                        
                        if query_string and "?" not in uri_val:
                            uri_val = f"{uri_val}?{query_string}"
                        return f'URI="{uri_val}"'
                    
                    line = re.sub(r'URI="([^"]+)"', replace_uri, line)
                updated_lines.append(line)
            else:
                if not line.startswith(("http://", "https://")):
                    line = base_url + line
                
                if query_string and "?" not in line:
                    line = f"{line}?{query_string}"
                    
                updated_lines.append(line)

        return "\n".join(updated_lines)

    def _get_config_url_event(self, event_id: str):
        event_embed_url = f"https://vimeo.com/event/{event_id}/embed"
        res = self.session.get(event_embed_url, timeout=10)
        if res.status_code != 200:
            return None
        
        match = re.search(r"var\s+htmlString\s*=\s*`([^`]+)`", res.text, re.DOTALL)
        if match:
            html_content = match.group(1)
            config_match = re.search(r'data-config-url="([^"]+)"', html_content)
            if config_match:
                return config_match.group(1).replace("&amp;", "&")
        return None

    def _get_config_url_via_streamlink_logic(self):
        try:
            viewer_res = self.session.get("https://vimeo.com/_next/viewer", timeout=10)
            if viewer_res.status_code != 200:
                return None
            
            viewer_data = viewer_res.json()
            jwt = viewer_data.get("jwt")
            api_url = viewer_data.get("apiUrl")
            
            if not jwt or not api_url:
                return None

            oembed_res = self.session.get("https://vimeo.com/api/oembed.json", params={"url": self.url}, timeout=10)
            if oembed_res.status_code != 200:
                return None
            
            uri = oembed_res.json().get("uri")
            if not uri:
                return None

            base_api = api_url if api_url.startswith("http") else f"https://{api_url}"
            player_config_endpoint = urljoin(base_api, uri)
            
            cfg_res = self.session.get(
                player_config_endpoint,
                params={"fields": "config_url"},
                headers={"Authorization": f"jwt {jwt}"},
                timeout=10
            )
            if cfg_res.status_code == 200:
                return cfg_res.json().get("config_url")
        except Exception:
            pass
        return None

    def _get_video_id(self):
        parsed = urlparse(self.url)
        path_parts = [p for p in parsed.path.split("/") if p]
        if not path_parts:
            return None
        for part in reversed(path_parts):
            if part.isdigit():
                return part
        return path_parts[-1]

    def _get_json_with_referer(self, url, referer_url):
        try:
            headers = {"Referer": referer_url}
            res = self.session.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                return res.json()
        except Exception:
            pass
        return None

    def _get_config_from_html(self, url):
        try:
            res = self.session.get(url, timeout=10)
            if res.status_code == 200:
                match = re.search(r"^\s*window\.playerConfig\s*=\s*(?P<json>{.+?})\s*;?", res.text, re.MULTILINE)
                if match:
                    return json.loads(match.group("json"))
        except Exception:
            pass
        return None

if __name__ == "__main__":
    vimeo_url = "https://vimeo.com/event/3889697"
    extractor = VimeoExtractor(vimeo_url)
    m3u8_link = extractor.extract()
    
    if m3u8_link:
        print(f"\n[M3U8 Linki]:\n{m3u8_link}")
        
        processed_content = extractor.get_processed_playlist(m3u8_link)
        
        if processed_content:
            file_name = "streams/tlcfr.m3u8"
            os.makedirs(os.path.dirname(file_name), exist_ok=True)
            
            with open(file_name, "w", encoding="utf-8") as f:
                f.write(processed_content)
            print(f"[+] Düzenlenmiş M3U8 içerik dosyası başarıyla kaydedildi: {file_name}")
    else:
        print("\n[-] M3U8 linki bulunamadı.")
