import json

with open("xkiro_all_voices.json", "r", encoding="utf-8") as f:
    data = json.load(f)

voices = data.get("voices", [])
print(f"Total voices: {len(voices)}")

# Check Vietnamese
vi_matches = [v for v in voices if any(x in str(v).lower() for x in ["vietnam", "viet", "vi-vn", "vn"])]
print("Vietnamese matches:", len(vi_matches))
for v in vi_matches:
    print(" ", v)

# Check all IDs and names
all_names = [(v.get("id"), v.get("name"), v.get("isVip"), v.get("categories")) for v in voices]
print("\nAll Voices:")
for vid, name, vip, cats in all_names:
    print(f"  ID: {vid:<30} | Name: {name:<35} | VIP: {vip} | Cats: {cats}")
