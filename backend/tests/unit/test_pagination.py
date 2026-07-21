from backend.app.services.read.pagination import Page, resolve_page


def test_offset_computes_from_page_and_size():
    assert Page(page=1, page_size=20).offset == 0
    assert Page(page=3, page_size=20).offset == 40


def test_resolve_clamps_size_and_defaults():
    # default when page_size is None
    assert resolve_page(None, None).page_size == 20
    # clamp above max (100)
    assert resolve_page(1, 500).page_size == 100
    # floor at 1
    assert resolve_page(1, 0).page_size == 1
    # page floors at 1
    assert resolve_page(0, 20).page == 1
