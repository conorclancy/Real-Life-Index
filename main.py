"""
Entry point for the Real Life Index application.

Run with:
    uv run python main.py

Or with hot-reload during development:
    uv run uvicorn app:app --reload
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app:app",           # Import path: the `app` object inside the `app` package
        host="127.0.0.1",
        port=8000,
        reload=True,         # Auto-reload on file changes (great for development)
        log_level="info",
    )
