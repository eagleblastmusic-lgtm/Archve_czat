"""Browser launcher for the local Archivebate server."""

import uvicorn


if __name__ == "__main__":
    uvicorn.run("runtime_app:app", host="127.0.0.1", port=8000, reload=False, log_level="info")
