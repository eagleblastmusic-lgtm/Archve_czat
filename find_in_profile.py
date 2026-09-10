from client import ArchivebateSession
from scraper import ArchivebateScraper
import inspect

s = ArchivebateSession()
scraper = ArchivebateScraper(s)
r = s.session.get("https://archivebate.com/profile/transvix")
print("Status:", r.status_code)
# Where in r.text is 9981517?
idx = r.text.find("9981517")
if idx != -1:
    print("Snippet around 9981517:")
    print(r.text[max(0, idx-300):min(len(r.text), idx+500)])
else:
    print("9981517 not in page 1 of profile/transvix, checking page 2...")
    r2 = s.session.get("https://archivebate.com/profile/transvix?page=2")
    idx2 = r2.text.find("9981517")
    if idx2 != -1:
        print("Found in page 2:")
        print(r2.text[max(0, idx2-300):min(len(r2.text), idx2+500)])
