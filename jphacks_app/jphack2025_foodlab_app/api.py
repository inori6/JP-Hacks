# api.py
import httpx, hashlib, base64, mimetypes, pathlib

BACKEND = "https://lab.160.16.126.35.sslip.io/recognize/fresh"

def sha1sum(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1<<20), b""):
            h.update(chunk)
    return h.hexdigest()

async def upload_image(path: str, storage: str = "cool"):
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    files = {"image": (pathlib.Path(path).name, open(path, "rb"), mime)}
    data = {"storage": storage}
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post(BACKEND, files=files, data=data)
        r.raise_for_status()
        return r.json()   # {'class_raw':..., 'class_id':..., 'deadine':..., 'reminders':[...], 'note':...}
