"""Unit tests for requirement analysis JSON repair."""

from app.services.functional.requirement_analysis import _parse_analysis


def test_parse_analysis_accepts_plain_json():
    parsed = _parse_analysis(
        '{"features":[{"module":"账号","items":[{"name":"登录","detail":"账号密码"}]}],'
        '"ui":{"summary":"登录页","pages":[{"name":"登录","description":"表单","areas":["账号"]}]},'
        '"unclear":[]}'
    )
    assert parsed.features[0].module == "账号"
    assert parsed.features[0].items[0].name == "登录"
    assert parsed.ui.pages[0].name == "登录"
    assert parsed.ui.pages[0].areas == ["账号"]


def test_parse_analysis_normalizes_legacy_flat_features():
    parsed = _parse_analysis(
        '{"features":[{"name":"登录","detail":"账号密码"}],'
        '"ui":{"summary":"","pages":[]},"unclear":[]}'
    )
    assert parsed.features[0].module == "未分组"
    assert parsed.features[0].items[0].name == "登录"


def test_parse_analysis_strips_markdown_fence():
    raw = (
        "```json\n"
        '{"features":[],"ui":{"summary":"","pages":[]},"unclear":[]}\n'
        "```"
    )
    parsed = _parse_analysis(raw)
    assert parsed.features == []
    assert parsed.ui.summary == ""


def test_parse_analysis_hoists_fields_nested_under_ui():
    """LLM often forgets to close ui; sibling keys land inside ui and root lacks }."""
    raw = (
        '{"features":[{"module":"账号","items":[{"name":"登录","detail":"账号密码"}]}],'
        '"ui":{"summary":"登录后进项目","pages":[{"name":"登录页","description":"表单","areas":[]}],'
        '"unclear":[{"item":"密码长度","reason":"未给出具体值"}]}'
    )
    parsed = _parse_analysis(raw)
    assert parsed.features[0].items[0].name == "登录"
    assert parsed.ui.summary == "登录后进项目"
    assert parsed.ui.pages[0].name == "登录页"
    assert "unclear" not in (parsed.ui.model_dump())
    assert parsed.unclear[0].item == "密码长度"


def test_parse_analysis_drops_legacy_sections():
    raw = (
        '{"features":[],"ui":{"summary":"","pages":[]},'
        '"logic_issues":[{"item":"x","detail":"y"}],'
        '"points":[{"title":"a","detail":"b"}],'
        '"boundaries":[{"item":"c","kind":"boundary","detail":"d"}],'
        '"unclear":[]}'
    )
    parsed = _parse_analysis(raw)
    dumped = parsed.model_dump()
    assert "logic_issues" not in dumped
    assert "points" not in dumped
    assert "boundaries" not in dumped
