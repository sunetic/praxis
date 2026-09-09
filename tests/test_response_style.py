from app.services.response_style import build_response_style_prompt


def test_response_style_prompt_does_not_force_a_fixed_conversation_shape():
    prompt = build_response_style_prompt()
    assert "Answer directly" in prompt
    assert "do not force a plan" in prompt
    assert "Outline your approach" not in prompt
    assert "Conclusion and recommendations" not in prompt
    assert "approach outline → key evidence → conclusion → next steps" not in prompt


def test_response_style_prompt_keeps_material_ambiguity_and_evidence_rules():
    prompt = build_response_style_prompt()
    assert "materially change the target, result, or safety" in prompt
    assert "returned tool evidence" in prompt
    assert "Include recommendations only when they help" in prompt
    assert "Respect an explicit request for brevity" in prompt


def test_response_style_prompt_rejects_repeated_recap_and_internal_monologue():
    prompt = build_response_style_prompt()
    assert "State each material fact once" in prompt
    assert "not an internal monologue" in prompt
    assert "let me summarize" in prompt
    assert "names the fields to return" in prompt
    assert "do not replay the process" in prompt
