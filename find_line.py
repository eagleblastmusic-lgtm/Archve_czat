from client import ArchivebateSession
import re
import json

s = ArchivebateSession()
url = "https://archivebate.com/profile/transvix"
r = s.session.get(url)
print("r.url:", r.url)
print("Has 9981517 in r.text:", "9981517" in r.text)

# Let's check where 9981517 is in r.text
lines = r.text.splitlines()
for i, l in enumerate(lines):
    if "9981517" in l:
        print(f"Line {i+1}: {l}")
        # print context
        for j in range(max(0, i-5), min(len(lines), i+15)):
            print(f"[{j+1}] {lines[j]}")
        break
