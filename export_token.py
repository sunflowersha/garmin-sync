"""
Run this once locally to print your Garmin token for use as a GitHub Secret.
Copy the output and save it as GARMIN_TOKEN in your repo secrets.
"""
import base64, zipfile, io, os

TOKEN_STORE = os.path.expanduser("~/.garminconnect")

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as zf:
    for fname in os.listdir(TOKEN_STORE):
        zf.write(os.path.join(TOKEN_STORE, fname), fname)

print(base64.b64encode(buf.getvalue()).decode())
