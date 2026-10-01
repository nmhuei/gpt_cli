from benchmarks import benchmark_concurrency


def test_live_concurrency_benchmark_is_disabled():
    """Running the retired benchmark must never send upstream prompts."""
    assert benchmark_concurrency.main() == 2
