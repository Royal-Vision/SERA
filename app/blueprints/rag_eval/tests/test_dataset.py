"""Unit tests for DatasetVersion — pure logic, no network."""

import pytest


# ───────────────────────── compute_sample_ids ─────────────────────────

class TestComputeSampleIds:
    def test_deterministic_same_input(self, dv):
        assert dv.compute_sample_ids("hello world") == dv.compute_sample_ids("hello world")

    def test_different_input_different_id(self, dv):
        assert dv.compute_sample_ids("q1") != dv.compute_sample_ids("q2")

    def test_returns_uuid5_format(self, dv):
        sid = dv.compute_sample_ids("anything")
        assert len(sid) == 36          # standard UUID string length
        assert sid.count("-") == 4     # 4 hyphens separating 5 groups


# ───────────────────────── _sha256_file ─────────────────────────

class TestSha256File:
    def test_returns_64_char_hex(self, tmp_path, dv):
        f = tmp_path / "x.txt"
        f.write_text("hello")
        h = dv._sha256_file(f)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_changes_with_content(self, tmp_path, dv):
        f = tmp_path / "x.txt"
        f.write_text("hello")
        h1 = dv._sha256_file(f)
        f.write_text("world")
        h2 = dv._sha256_file(f)
        assert h1 != h2

    def test_deterministic_for_same_file(self, tmp_path, dv):
        f = tmp_path / "x.txt"
        f.write_text("same content")
        assert dv._sha256_file(f) == dv._sha256_file(f)


# ───────────────────────── build_eval_dataset ─────────────────────────

class TestBuildEvalDataset:
    """Hits the real SOURCE_PATH (medical_o1_sft.json)."""

    def test_returns_expected_columns(self, dv):
        df = dv.build_eval_dataset()
        assert set(df.columns) == {
            "sample_id", "question", "ground_truth_answer", "gold_doc_id"
        }

    def test_returns_expected_row_count(self, dv):
        df = dv.build_eval_dataset()
        assert len(df) == dv.EVAL_SIZE

    def test_deterministic_across_calls(self, dv):
        df1 = dv.build_eval_dataset()
        df2 = dv.build_eval_dataset()
        assert df1.equals(df2)

    def test_gold_doc_id_equals_sample_id(self, dv):
        df = dv.build_eval_dataset()
        assert (df["sample_id"] == df["gold_doc_id"]).all()

    def test_sample_ids_are_unique(self, dv):
        df = dv.build_eval_dataset()
        assert df["sample_id"].is_unique


# ───────────────────────── freeze + load round trip ─────────────────────────

class TestFreezeAndLoad:
    def test_round_trip_preserves_data(self, isolated_dv):
        df = isolated_dv.build_eval_dataset()
        parquet_path = isolated_dv.freeze_eval_dataset(df)
        loaded_df, meta = isolated_dv.load_eval_dataset(parquet_path)
        assert loaded_df.equals(df)
        assert meta["n_samples"] == len(df)
        assert meta["version"] == "test"

    def test_freeze_refuses_overwrite(self, isolated_dv):
        df = isolated_dv.build_eval_dataset()
        isolated_dv.freeze_eval_dataset(df)
        with pytest.raises(FileExistsError):
            isolated_dv.freeze_eval_dataset(df)

    def test_load_detects_tampering(self, isolated_dv):
        df = isolated_dv.build_eval_dataset()
        parquet_path = isolated_dv.freeze_eval_dataset(df)
        # Append a byte to corrupt the file
        with open(parquet_path, "ab") as f:
            f.write(b"x")
        with pytest.raises(ValueError, match="hash mismatch"):
            isolated_dv.load_eval_dataset(parquet_path)

    def test_load_raises_when_parquet_missing(self, isolated_dv, tmp_path):
        with pytest.raises(FileNotFoundError):
            isolated_dv.load_eval_dataset(tmp_path / "nope.parquet")
