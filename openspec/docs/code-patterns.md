# Code Patterns

Standard templates for common development tasks in this project.

## Adding a New API Endpoint

```python
# backend/app/api/my_feature.py

from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/my-feature", tags=["My Feature"])

# ---------- Pydantic schemas (define here or in models/) ----------
from pydantic import BaseModel, Field

class MyRequest(BaseModel):
    symbol: str = Field(..., description="Stock symbol")
    param1: float = Field(default=0.0, description="Optional parameter")

class MyResponse(BaseModel):
    symbol: str
    result: dict
    updated_at: str

# ---------- Endpoints ----------

@router.get("/status")
async def get_status(symbol: str = Query(...)):
    """Get feature status for a symbol."""
    try:
        # Call service / query DB
        return {"symbol": symbol, "status": "ok"}
    except Exception as e:
        logger.error(f"get_status failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/action", response_model=MyResponse)
async def do_action(req: MyRequest):
    """Execute an action."""
    try:
        # Call service / executor
        result = {}
        return MyResponse(symbol=req.symbol, result=result, updated_at="2024-01-01")
    except Exception as e:
        logger.error(f"do_action failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

Then register in `backend/app/main.py`:
```python
from app.api import my_feature
app.include_router(my_feature.router, prefix=settings.API_V1_PREFIX)
```

## Adding a New Background Monitor

```python
# backend/app/services/my_monitor.py

import threading
import time
import logging

logger = logging.getLogger(__name__)


class MyMonitor:
    """Background daemon thread monitor for X."""

    def __init__(self, executor, interval_seconds: int = 30):
        self.executor = executor
        self.interval = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(f"MyMonitor started (interval={self.interval}s)")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("MyMonitor stopped")

    def is_running(self, check_thread: bool = True) -> bool:
        return (not self._stop_event.is_set()
                and self._thread is not None
                and (not check_thread or self._thread.is_alive()))

    def status(self) -> dict:
        return {
            "running": self.is_running(),
            "interval_seconds": self.interval,
        }

    # ---------- private ----------

    def _run_loop(self):
        logger.info("MyMonitor loop started")
        while not self._stop_event.is_set():
            try:
                if self._should_scan():
                    self._scan()
            except Exception:
                logger.exception("MyMonitor scan error")
            self._stop_event.wait(self.interval)
        logger.info("MyMonitor loop exited")

    def _should_scan(self) -> bool:
        # Check trading day, trading time, etc.
        return True

    def _scan(self):
        # Core logic: query positions/data → condition check → execute
        account = self.executor.get_account()
        positions = self.executor.get_positions()
        for pos in positions:
            if self._condition_met(pos, account):
                self._execute(pos, account)

    def _condition_met(self, pos, account) -> bool:
        # Your condition logic here
        return False

    def _execute(self, pos, account):
        # Your execution logic here (e.g., self.executor.sell(...))
        pass


# ---------- module-level singleton ----------

_monitor_instance: MyMonitor | None = None


def get_my_monitor(executor=None, interval_seconds: int = 30) -> MyMonitor:
    global _monitor_instance
    if _monitor_instance is None and executor is not None:
        _monitor_instance = MyMonitor(executor, interval_seconds)
    return _monitor_instance
```

Then start/stop in `backend/app/main.py` lifespan:
```python
from app.services.my_monitor import get_my_monitor

# in startup:
monitor = get_my_monitor(executor, interval_seconds=30)
monitor.start()

# in shutdown:
monitor.stop()
```

## Adding a New Frontend Page

### Step 1: Create page component

```tsx
// frontend/src/pages/MyFeaturePage.tsx

import { useState, useEffect } from 'react';
import axios from 'axios';

interface MyData {
  symbol: string;
  status: string;
}

export default function MyFeaturePage() {
  const [data, setData] = useState<MyData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    axios.get('/api/v1/my-feature/status?symbol=000001')
      .then(res => setData(res.data))
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div>Loading...</div>;
  if (error) return <div className="text-red-500">Error: {error}</div>;

  return (
    <div className="p-4">
      <h1 className="text-xl font-bold mb-4">My Feature</h1>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </div>
  );
}
```

### Step 2: Register route in `App.tsx`

```tsx
import MyFeaturePage from './pages/MyFeaturePage';

// Inside <Routes>:
<Route path="/my-feature" element={<MyFeaturePage />} />
```

### Step 3: (Optional) Add API client function

```typescript
// In frontend/src/api/client.ts
export const myFeatureApi = {
  getStatus: (symbol: string) =>
    api.get('/my-feature/status', { params: { symbol } }),
  doAction: (data: { symbol: string; param1: number }) =>
    api.post('/my-feature/action', data),
};
```
