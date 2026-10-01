from benchmarks.session_lifecycle import build_deterministic_report


def test_deterministic_lifecycle_benchmark_writes_a_machine_readable_report(tmp_path):
    """Removing a lifecycle measurement must make benchmark evidence incomplete."""
    report = build_deterministic_report(tmp_path)

    assert report["mode"] == "deterministic"
    assert report["fresh_parallel"]["turn_count"] == 5
    assert report["resume_dag"]["conversation_id"] == "conv_bench"
    assert report["queued_steer"]["prompts"] == ["initial", "first", "second"]
    assert report["wire_runtime"]["browser_lifebuoy_invocations"] == 0
    assert (tmp_path / "session-lifecycle.json").is_file()
    assert (tmp_path / "session-lifecycle.md").is_file()
