from client import ArchivebateSession
from scraper import ArchivebateScraper

s = ArchivebateSession()
scraper = ArchivebateScraper(s)

home_vids = scraper.get_home_videos(page=1)
print("--- HOME VIDEOS DATES ---")
for v in home_vids[:15]:
    print(f"ID: {v['id']} | Date string: '{v['date']}'")

print("\n--- MODEL VIDEOS (e.g. transaubrey) ---")
model_vids = scraper.get_model_videos("transaubrey", page=1)
for v in model_vids[:15]:
    print(f"ID: {v['id']} | Date string: '{v['date']}'")
