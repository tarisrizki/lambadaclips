import re

with open('saasshorts.py', 'r', encoding='utf-8') as f:
    code = f.read()

# Change model to gemini-2.5-flash-lite to get 10 RPM instead of 5
code = code.replace('GEMINI_MODEL = "gemini-3-flash-preview"', 'GEMINI_MODEL = "gemini-2.5-flash-lite"')

# Inject retry logic helper
helper = """
import time
def generate_with_retry(client, **kwargs):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(**kwargs)
        except Exception as e:
            if '429' in str(e) or 'RESOURCE_EXHAUSTED' in str(e) or 'quota' in str(e).lower():
                if attempt < max_retries - 1:
                    print(f"[SaaSShorts] ⚠️ Rate limit hit (429). Retrying in 20 seconds (Attempt {attempt+1}/{max_retries})...")
                    time.sleep(20)
                else:
                    raise e
            else:
                raise e
"""

# Find the import block and add it there
if 'def generate_with_retry' not in code:
    code = code.replace('from concurrent.futures import ThreadPoolExecutor, as_completed', 
                        'from concurrent.futures import ThreadPoolExecutor, as_completed\n' + helper)

# Replace client.models.generate_content( with generate_with_retry(client, 
code = code.replace('client.models.generate_content(', 'generate_with_retry(client, ')

with open('saasshorts.py', 'w', encoding='utf-8') as f:
    f.write(code)
print('Patched saasshorts.py with retry logic and changed model to gemini-2.5-flash-lite')
