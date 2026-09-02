from app.services.response_style import build_response_style_prompt


def test_response_style_prompt_is_concise_first():
    prompt = build_response_style_prompt()
    assert "Conclusion and recommendations" in prompt
    assert "1-3" in prompt
    assert "next steps" in prompt


def test_response_style_prompt_asks_for_followup_with_scene_based_options():
    prompt = build_response_style_prompt()
    assert "Choose the recommendation type based on the scenario" in prompt
    assert "do not always default to SQL" in prompt
