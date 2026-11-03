from app.services.ui_automation.ui_explore_engines import explore_task_text


def test_explore_task_text_captcha_split_and_no_repeat():
    text = explore_task_text(goal="登录", username="demo", password="secret")
    assert "{{username}}" in text
    assert "{{password}}" in text
    assert "读图" in text or "验证码" in text
    assert "Midscene" in text or "视觉" in text
    assert "登录/提交" in text or "提交" in text
    assert "刷新" in text
    assert "不要在同一页面无限重复同一操作" in text
    assert "请输入图中字符" not in text
    assert "snapshot" in text.lower() or "ref" in text or "可见" in text
    assert "禁止对验证码图片或刷新按钮做 Input" in text
