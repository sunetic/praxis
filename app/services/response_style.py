from app.services.platform.prompt_loader import PromptLoader


def build_response_style_prompt(*, locale: str | None = None) -> str:
    return PromptLoader.render("chat/prompts/response_style.tpl", locale=locale or "zh-CN")
