from rag import tracing


def test_tracing_is_disabled_without_keys():
    assert tracing.ENABLED is False


def test_observe_is_transparent_in_both_forms():
    @tracing.observe
    def plain(x):
        return x + 1

    @tracing.observe(name="named", capture_output=False)
    def named(x):
        return x * 2

    assert plain(1) == 2 and named(3) == 6


def test_helpers_do_nothing_when_disabled():
    with tracing.session("u1", "respond"):
        tracing.annotate(output="x")
    assert tracing.current_trace_id() is None
    tracing.score("t", "like", 1)
    tracing.flush()
