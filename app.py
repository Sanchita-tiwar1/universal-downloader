import os
import sys

# Simply load web_app
from web_app import app

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    print(f"🚀 Starting DownTG-Web Server on http://localhost:{port}")
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
