"""Start the Paper Agent web server."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn

if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent
    uvicorn.run(
        "server.app:app",
        host="0.0.0.0",
        port=10086,
        reload=True,
        reload_dirs=[
            str(project_root / "server"),
            str(project_root / "modules"),
            str(project_root / "utils"),
        ],
    )
