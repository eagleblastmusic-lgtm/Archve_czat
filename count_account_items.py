import requests, re
from client import ArchivebateSession

session = ArchivebateSession()
session.login()

for ep in ['watchlater', 'history', 'following']:
    total = 0
    for page in range(1, 10):
        url = f"https://archivebate.com/{ep}?page={page}"
        r = session.session.get(url)
        sections = re.findall(r'<section class="video_item">.*?</section>', r.text, re.DOTALL)
        if not sections:
            print(f"{ep} ended at page {page-1}. Total items: {total}")
            break
        total += len(sections)
        # Check if page has next page link
        if 'page=' not in r.text or len(sections) < 25:
            print(f"{ep} last page {page}. Total items: {total}")
            break
    print(f"{ep}: fetched {total} items across pages!")
