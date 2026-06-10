import re

with open('saasshorts.py', 'r', encoding='utf-8') as f:
    code = f.read()

new_generate_voiceover = '''def generate_voiceover(
    text: str,
    fishaudio_key: str,
    output_path: str,
    voice_id: str = "",
) -> str:
    """Generate voiceover audio using Fish Audio TTS."""
    print(f"[SaaSShorts] 🎙️ Generating voiceover ({len(text)} chars) with Fish Audio...")

    url = "https://api.fish.audio/v1/tts"

    headers = {
        "Authorization": f"Bearer {fishaudio_key}",
        "Content-Type": "application/json",
    }

    body = {
        "text": text,
        "format": "mp3"
    }
    
    if voice_id:
        body["reference_id"] = voice_id

    with httpx.Client(timeout=120.0) as client:
        resp = client.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            raise Exception(f"Fish Audio TTS error ({resp.status_code}): {resp.text}")

        with open(output_path, "wb") as f:
            f.write(resp.content)

    print(f"[SaaSShorts] ✅ Voiceover: {output_path}")
    return output_path'''

new_get_voices = '''def get_fishaudio_voices(fishaudio_key: str) -> list:
    """Fetch available voices from Fish Audio."""
    url = "https://api.fish.audio/model?page_size=30&page_number=1"
    headers = {"Authorization": f"Bearer {fishaudio_key}"}

    with httpx.Client(timeout=15.0) as client:
        try:
            resp = client.get(url, headers=headers)
            if resp.status_code != 200:
                return []
            data = resp.json()
        except Exception:
            return []

    voices = []
    items = data.get("items", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        items = []

    for v in items:
        voices.append({
            "voice_id": v.get("_id", v.get("id", "")),
            "name": v.get("title", v.get("name", "Unknown Voice")),
            "category": "Fish Audio",
            "labels": {},
            "preview_url": "",
        })

    return voices'''

# We need to find where generate_voiceover starts and ends
start1 = code.find('def generate_voiceover(')
end1 = code.find('def get_fishaudio_voices', start1) # it was renamed by previous replace
if end1 == -1: end1 = code.find('def get_elevenlabs_voices', start1)

# To be safer, use regex
code = re.sub(r'def generate_voiceover\(.*?return output_path', new_generate_voiceover, code, flags=re.DOTALL)
code = re.sub(r'def get_fishaudio_voices\(.*?return voices', new_get_voices, code, flags=re.DOTALL)

# Let's remove the DEFAULT_VOICES map at the top of saasshorts.py since they are elevenlabs IDs
# Or just comment it out
code = re.sub(r'DEFAULT_VOICES = \{.*?\n\}', 'DEFAULT_VOICES = {}', code, flags=re.DOTALL)

with open('saasshorts.py', 'w', encoding='utf-8') as f:
    f.write(code)
print('Replaced Fish Audio functions in saasshorts.py')
