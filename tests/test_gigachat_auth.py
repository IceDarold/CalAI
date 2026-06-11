"""Quick test script for GigaChat auth."""
import httpx
import base64

client_id = "019dda7f-02d8-7031-9514-4000cfa98ae5"
auth_key = "537db8df-3c9b-4a8e-adc9-72c657f4f6a2"

combos = [
    ("client_id:auth_key", f"{client_id}:{auth_key}"),
]

for label, raw in combos:
    encoded = base64.b64encode(raw.encode()).decode()
    resp = httpx.post(
        "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
        headers={
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": "calai-test",
        },
        data={"scope": "GIGACHAT_API_PERS"},
        verify=False,
        timeout=10,
    )
    print(f"{label}: status={resp.status_code} body={resp.text[:200]}")
