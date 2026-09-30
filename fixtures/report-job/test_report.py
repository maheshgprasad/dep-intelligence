from report_job import total


def test_total():
    assert total([1, 2, 3]) == 6
