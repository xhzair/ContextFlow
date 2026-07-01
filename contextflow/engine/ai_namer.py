"""AI-powered workspace name generation via Agnes AI API.

Uses OpenAI-compatible API to generate natural, user-friendly workspace names
from app lists detected by the discovery engine.
"""

import json
import urllib.request
import urllib.error

AGNES_BASE_URL = "https://apihub.agnes-ai.com/v1"
AGNES_API_KEY = "sk-eNACCNZr1axPCQa1GuTF4Qr5Wv1MzoeJZsmauEPF7sLA3Duv"
AGNES_MODEL = "agnes-2.0-flash"


def generate_workspace_name(apps: list[str], timeout: float = 15.0) -> str | None:
    """Use AI to generate a natural workspace name from an app list.

    Returns the AI-generated name, or None if the API call fails.
    """
    if not apps or len(apps) < 2:
        return None

    prompt = (
        f"Name this workspace (2-4 words): {', '.join(apps)}. "
        "Reply ONLY the name, nothing else."
    )

    try:
        data = json.dumps({
            "model": AGNES_MODEL,
            "messages": [
                {"role": "system", "content": "You are a concise workspace naming assistant."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 20,
            "temperature": 0.5,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{AGNES_BASE_URL}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {AGNES_API_KEY}",
            },
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode())
            name = result["choices"][0]["message"]["content"].strip().strip('"').strip("'")
            # Validate: not too long, not empty, not the prompt itself
            if name and len(name) <= 50 and name.lower() != apps[0].lower():
                return name

    except Exception:
        pass

    return None
