# -*- coding: utf-8 -*-
"""
Marcus Platform Backend - Development Entry Point
Run with: python run.py
Or: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""
import sys
import io
# Fix Windows console encoding BEFORE any prints
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
