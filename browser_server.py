"""Browser launcher for the local Archivebate server."""

from fast_grouped_feed_v2 import install as install_grouped_feed_fast_path

# Install before Uvicorn imports main:app so the existing CatalogService singleton
# uses the V4.3 grouped query path from its first request.
install_grouped_feed_fast_path()

import uvicorn


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False, log_level="info")
