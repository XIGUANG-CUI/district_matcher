"""测试 utils/cleanup.py 的 uploads 清理逻辑（均在临时目录中进行，不触碰真实 uploads）。"""
import os
import sys
import tempfile
import shutil
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import cleanup


def _seed(d, files):
    """files: list of (name, mtime_offset_seconds)."""
    for name, off in files:
        fp = os.path.join(d, name)
        with open(fp, "w") as f:
            f.write("x")
        if off is not None:
            t = time.time() + off
            os.utime(fp, (t, t))


class TestCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="uploads_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_protected_source_never_deleted(self):
        _seed(self.tmp, [("全国行政编码数据.xlsx", 0),
                         ("result_export_A_1.xlsx", -100),
                         ("student_input.xlsx", -50),
                         ("old_student.xlsx", -200)])
        di, de = cleanup.prune_uploads(keep="student_input.xlsx", base_dir=self.tmp)
        # 源数据应仍在
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "全国行政编码数据.xlsx")))
        # 当前输入（keep）与导出均保留；旧输入被删
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "student_input.xlsx")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "result_export_A_1.xlsx")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "old_student.xlsx")))
        self.assertEqual(di, 1)

    def test_keep_current_upload_input(self):
        _seed(self.tmp, [("old1.xlsx", -100), ("old2.xlsx", -90),
                         ("current.xlsx", 0)])
        di = cleanup.prune_old_inputs(keep="current.xlsx", base_dir=self.tmp)
        self.assertEqual(di, 2)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "current.xlsx")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "old1.xlsx")))

    def test_prune_exports_keeps_latest_n(self):
        files = [("result_export_%d.xlsx" % i, -i) for i in range(20)]
        _seed(self.tmp, files)
        removed = cleanup.prune_exports(max_keep=10, base_dir=self.tmp)
        self.assertEqual(removed, 10)
        # 最新的 10 个（编号 0..9，mtime 最大）应保留
        for i in range(10):
            self.assertTrue(os.path.exists(os.path.join(self.tmp, "result_export_%d.xlsx" % i)))
        for i in range(10, 20):
            self.assertFalse(os.path.exists(os.path.join(self.tmp, "result_export_%d.xlsx" % i)))

    def test_preview_temp_always_removed(self):
        _seed(self.tmp, [("_preview_foo.xlsx", 0), ("_preview_bar.csv", 0)])
        cleanup.prune_uploads(base_dir=self.tmp)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "_preview_foo.xlsx")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "_preview_bar.csv")))

    def test_clear_all_respects_protected(self):
        _seed(self.tmp, [("全国行政编码数据.xlsx", 0), ("a.xlsx", 0), ("b.csv", 0)])
        n = cleanup.clear_all_uploads(include_protected=False, base_dir=self.tmp)
        self.assertEqual(n, 2)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "全国行政编码数据.xlsx")))

    def test_clear_all_with_protected(self):
        _seed(self.tmp, [("全国行政编码数据.xlsx", 0), ("a.xlsx", 0)])
        n = cleanup.clear_all_uploads(include_protected=True, base_dir=self.tmp)
        self.assertEqual(n, 2)
        self.assertEqual(os.listdir(self.tmp), [])


if __name__ == "__main__":
    unittest.main()
