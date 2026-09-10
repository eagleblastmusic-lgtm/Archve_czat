import requests
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
}

# 1. Camwhores watch page inspection for 17456198
print("--- 1. CAMWHORES WATCH PAGE ---")
r_cw = requests.get('https://www.camwhores.tv/videos/17456198/melodyxlove-01-cum-4m40s/', headers=headers)
print("CW status:", r_cw.status_code)
# Search for tags / categories / models / keywords in html
for m in re.finditer(r'<a[^>]+href=["\']([^"\']*(?:tags|categories|models)[^"\']*)["\'][^>]*>(.*?)</a>', r_cw.text, re.DOTALL):
    href, text = m.groups()
    clean_text = re.sub(r'<[^>]+>', '', text).strip()
    print(f"  CW link: {href} -> text: '{clean_text}'")

for m in re.finditer(r'<meta[^>]+(?:keywords|description)[^>]+content=["\']([^"\']+)["\']', r_cw.text, re.IGNORECASE):
    print("  CW meta:", m.group(1))

# Check for gender or trans in Camwhores
for kw in ['trans', 'shemale', 'ts', 'ladyboy', 'female', 'male', 'gender']:
    if re.search(rf'\b{kw}\b', r_cw.text, re.IGNORECASE):
        print(f"  Found '{kw}' in Camwhores page text!")

# 2. Archivebate video details for melodyxlove
print("\n--- 2. ARCHIVEBATE API & PAGES ---")
from scraper import ArchivebateScraper
from client import ArchivebateSession
s = ArchivebateSession()
scraper = ArchivebateScraper(s)

r_ab = s.get('https://archivebate.com/api/v1/search?query=melodyxlove')
print("AB API search status:", r_ab.status_code)
try:
    ab_data = r_ab.json()
    vids = ab_data.get('videos', [])
    print(f"AB vids count: {len(vids)}")
    if vids:
        first_id = vids[0].get('id')
        print("First AB video:", vids[0])
        r_watch = s.get(f"https://archivebate.com/watch/{first_id}")
        print("AB watch page status:", r_watch.status_code)
        for m in re.finditer(r'<a[^>]+href=["\']([^"\']*(?:tag|category|model)[^"\']*)["\'][^>]*>(.*?)</a>', r_watch.text, re.DOTALL):
            href, text = m.groups()
            clean_text = re.sub(r'<[^>]+>', '', text).strip()
            print(f"  AB link: {href} -> text: '{clean_text}'")
        for kw in ['trans', 'shemale', 'ts', 'ladyboy', 'female', 'male', 'gender']:
            if re.search(rf'\b{kw}\b', r_watch.text, re.IGNORECASE):
                print(f"  Found '{kw}' in Archivebate watch page text!")
except Exception as e:
    print("AB error:", e)

# 3. Chaturbate / Stripchat / Cam sites profile check for melodyxlove
print("\n--- 3. CAM PLATFORMS (CHATURBATE / STRIPCHAT) ---")
for site_url in [
    'https://chaturbate.com/melodyxlove/',
    'https://stripchat.com/melodyxlove',
    'https://www.camwhores.tv/models/melodyxlove/'
]:
    try:
        rs = requests.get(site_url, headers=headers, timeout=5)
        print(f"{site_url} -> status {rs.status_code}")
        if rs.status_code == 200:
            for kw in ['trans', 'shemale', 'ts', 'transsexual', 'ladyboy', 'female', 'male', 'gender']:
                matches = re.findall(rf'([^\n.]{{0,40}}\b{kw}\b[^\n.]{{0,40}})', rs.text, re.IGNORECASE)
                if matches:
                    print(f"  Found '{kw}' in {site_url}:")
                    for sm in matches[:3]:
                        print(f"     ...{sm.strip()}...")
    except Exception as e:
        print(f"Error checking {site_url}: {e}")
