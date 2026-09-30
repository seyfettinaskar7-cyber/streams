import os
import re
import requests

if __name__ == "__main__":
    vimeo_url = "https://vimeo.com/event/3889697"
    extractor = VimeoExtractor(vimeo_url)
    m3u8_link = extractor.extract()
    
    if m3u8_link:
        print(f"\n[M3U8 Linki]:\n{m3u8_link}")
        
        # 1. re.sub ile /avc/hls.m3u8 kısmını silerek base_url oluşturma
        base_url = re.sub(r'/avc/hls\.m3u8.*$', '/', m3u8_link)
        print(f"[+] Base URL oluşturuldu: {base_url}")
        
        headers = {
            "Referer": "https://vimeo.com/",
            "Origin": "https://vimeo.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0"
        }
        
        response = requests.get(m3u8_link, headers=headers)
        if response.status_code == 200:
            # 2. M3U8 içeriğini işleme ve göreceli yollara base_url ekleme
            updated_lines = []
            for line in response.text.splitlines():
                line = line.strip()
                if not line:
                    continue
                
                if line.startswith("#"):
                    # Etiketlerin içinde (örneğin #EXT-X-KEY veya #EXT-X-MAP) URI="..." geçiyorsa kontrol et
                    if 'URI="' in line:
                        def replace_uri(match):
                            uri_val = match.group(1)
                            if not uri_val.startswith(("http://", "https://")):
                                uri_val = base_url + uri_val
                            return f'URI="{uri_val}"'
                        line = re.sub(r'URI="([^"]+)"', replace_uri, line)
                    updated_lines.append(line)
                else:
                    # # ile başlamayan (dosya/segment) satırlarda http/https yoksa base_url ekle
                    if not line.startswith(("http://", "https://")):
                        line = base_url + line
                    updated_lines.append(line)
            
            updated_content = "\n".join(updated_lines)
            
            # Kayıt dizininin var olduğundan emin olalım
            file_name = "streams/tlcfr.m3u8"
            os.makedirs(os.path.dirname(file_name), exist_ok=True)
            
            with open(file_name, "w", encoding="utf-8") as f:
                f.write(updated_content)
            print(f"[+] Düzenlenmiş M3U8 dosyası başarıyla kaydedildi: {file_name}")
        else:
            print(f"[-] M3U8 içeriği indirilemedi. HTTP Kod: {response.status_code}")
    else:
        print("\n[-] M3U8 linki bulunamadı.")
