from app.services.response_style import build_response_style_prompt


def test_response_style_prompt_is_concise_first():
    prompt = build_response_style_prompt()
    assert "结论与建议" in prompt
    assert "1-3" in prompt
    assert "下一步" in prompt


def test_response_style_prompt_asks_for_followup_with_scene_based_options():
    prompt = build_response_style_prompt()
    assert "根据场景自动选择" in prompt
    assert "不要固定只给 SQL" in prompt
