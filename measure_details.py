import time
from client import ArchivebateSession
from scraper import ArchivebateScraper

s = ArchivebateSession()
scraper = ArchivebateScraper(s)

start = time.time()
details = scraper.get_video_details("16438004")
print(f"get_video_details took: {time.time() - start:.2f}s")
print("Has direct_url:", bool(details.get("direct_url")))
