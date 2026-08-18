from calculator import calculate_total


def test_calculate_total_includes_all_items():
    assert calculate_total([1.0, 2.0, 3.0]) == 6.0


def test_calculate_total_empty():
    assert calculate_total([]) == 0.0
