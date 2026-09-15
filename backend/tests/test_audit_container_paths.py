# -*- coding: utf-8 -*-
"""单测：jobs/audit_container_paths.py —— 容器路径审计的识别规则（round 29）。

覆盖 3 个必须报出来的情形 + 1 个不许误报的情形：
  ① 解析结果不存在 → 报；
  ② **上溯越界**（`parents[k]` 越界，落点必为 `/`）→ 报（旧版 IndexError 静默漏报）；
  ③ 落点本身就是 `/`（`/` 存在但按仓库布局不该算到根）→ 报（旧版 "exists() 为真" 放过）；
  ④ 上溯级数正确、落点存在 → 不报。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "jobs"))

import audit_container_paths as A  # noqa: E402


def _mk(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_missing_target_is_reported(tmp_path):
    """4 级上溯 + "data" → 落点不存在 → 报「解析结果不存在」。"""
    _mk(tmp_path, "app/app/services/m1.py",
        'P = Path(__file__).parent.parent.parent.parent / "data" / "x.json"\n')
    checked, rows = A.scan(tmp_path)
    assert checked == 1
    assert len(rows) == 1
    assert rows[0]["why"] == "解析结果不存在"
    assert rows[0]["target"].endswith("/data/x.json")


def test_over_deep_chain_in_container_like_tree():
    """浅层树（复现容器层级 /app/app/api/x.py）：5 级上溯 → 落点 `/`；7 级 → 越界。两者都必须报。"""
    import shutil
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="auditp_", dir="/tmp"))  # → /tmp/auditp_xxx/app/api/x.py
    try:
        _mk(root, "app/api/m5.py",
            'W = Path(__file__).parent.parent.parent.parent.parent\n')
        _mk(root, "app/api/m7.py",
            'Z = Path(__file__).parent.parent.parent.parent.parent.parent.parent\n')
        checked, rows = A.scan(root)
        assert checked == 2
        by_file = {r["file"]: r for r in rows}
        assert set(by_file) == {"app/api/m5.py", "app/api/m7.py"}, rows
        assert by_file["app/api/m5.py"]["base"] == "/"          # 落点 = 容器根
        assert by_file["app/api/m5.py"]["why"].startswith("落点 = /")
        assert by_file["app/api/m7.py"]["overflow"] is True     # 越界 → 同样落点 /
        assert by_file["app/api/m7.py"]["why"].startswith("上溯越界")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_root_base_is_reported():
    """容器真实路径：/app/app/services/m.py 上四级 = / → 即便 / 存在也要报。"""
    base, target, overflow = A.resolve(Path("/app/app/services/m.py"), 4, ["data", "position_tiers.json"])
    assert (str(base), str(target), overflow) == ("/", "/data/position_tiers.json", False)
    # parents[4] 从 4 层深度的文件取值越界 → 落点同样是 /
    base2, _, overflow2 = A.resolve(Path("/app/app/api/m.py"), 5, ["backend"])
    assert str(base2) == "/" and overflow2 is True


def test_healthy_path_is_not_reported(tmp_path):
    """上溯级数正确且落点存在（仓库布局）→ 不报。"""
    _mk(tmp_path, "backend/app/services/m3.py",
        'P = Path(__file__).parent.parent.parent.parent / "backend" / "app"\n')
    checked, rows = A.scan(tmp_path)
    assert checked == 1
    assert rows == []


def test_parents_index_semantics():
    """`.parents[k]` 是**直接父目录起步**，不能按 `k` 级算（旧版少算一级 → 漏报）。

    `Path('/app/app/services/x.py').parents[3]` == `/`（不是 `/app`）。
    """
    assert A.parents_count(".parents[0]") == 1
    assert A.parents_count(".parents[3]") == 4
    assert A.parents_count(".parent.parent") == 2
    # 与 pathlib 对拍：n → parents[n-1]
    p = Path("/app/app/services/x.py")
    for k in range(4):
        n = A.parents_count(".parents[%d]" % k)
        base, target, _ = A.resolve(p, n, ["data"])
        assert base == p.parents[k], (k, base, p.parents[k])
        assert target == p.parents[k] / "data"


def test_real_world_parents_index_line_is_caught(tmp_path):
    """真实漏报行（direction_prediction.py:813）必须被抓到。

    旧版正则让 `(?:\\.parent)+` 先吃掉 `.parents[3]` 的 `.parent` 前缀 → 当成"上溯 1 级、无字面量"跳过。
    """
    line = '            _data_root = Path(__file__).resolve().parents[3] / "data" / "backtest"\n'
    m = A.EXPR.search(line)
    assert m and m.group(1) == ".parents[3]", m.groups() if m else None
    assert A.parents_count(m.group(1)) == 4
    _mk(tmp_path, "app/services/m5.py", line)
    checked, rows = A.scan(tmp_path)
    assert checked == 1 and len(rows) == 1
    assert rows[0]["target"].endswith("/data/backtest")


def test_resolve_between_is_recognised(tmp_path):
    """`.resolve()` / `.absolute()` 夹在中间的写法也要识别（旧版正则漏掉，实测漏了 api/market.py:22）。"""
    _mk(tmp_path, "backend/app/api/m4.py",
        'A = Path(__file__).resolve().parent.parent.parent.parent\n'
        'B = Path(__file__).absolute().parent.parent.parent.parent / "data"\n')
    checked, rows = A.scan(tmp_path)
    # 两种写法都被识别（旧版正则只认得 `Path(__file__).parent…`，这两条会全漏）
    assert checked == 2, rows
    # A 的落点在临时树里恰好存在 → 不报；B 拼了 "data" 不存在 → 报
    assert len(rows) == 1
    assert rows[0]["line"] == 2 and rows[0]["target"].endswith("/data")
