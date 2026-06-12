from dbaide.charts.labels import category_axis_layout, format_category_label


def test_format_iso_date_compact():
    assert format_category_label("2024-03-15", compact=True) == "03-15"
    assert format_category_label("2024-03-15", compact=False) == "2024-03-15"


def test_format_iso_datetime_compact():
    assert format_category_label("2024-03-15T08:30:00", compact=True) == "03-15 08:30"


def test_dense_dates_rotate_and_compact():
    dates = [f"2024-03-{d:02d}" for d in range(1, 15)]
    display, angle, bottom = category_axis_layout(dates)
    assert len(display) == 14
    assert all(lbl.startswith("03-") for lbl in display)
    assert angle == -60
    assert bottom >= 38


def test_few_short_labels_stay_horizontal():
    display, angle, bottom = category_axis_layout(["A", "B", "C"])
    assert display == ["A", "B", "C"]
    assert angle == 0
    assert bottom == 8
