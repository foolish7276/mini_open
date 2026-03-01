"""LangChain provider 适配层。"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


def build_chat_model(
    provider: str,
    model: str,
    temperature: float,
    base_url: str = "",
    api_key: str = "",
) -> BaseChatModel:
    """按 provider 构造 ChatModel。"""
    provider = provider.lower()

    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as e:
            raise RuntimeError("langchain-openai is required for provider=openai") from e
        # 注意：不同 langchain-openai 版本对 base_url 的参数名可能不同。
        kwargs: dict[str, object] = {"model": model, "temperature": temperature}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
            try:
                return ChatOpenAI(**kwargs)
            except TypeError:
                kwargs.pop("base_url", None)
                kwargs["openai_api_base"] = base_url
                return ChatOpenAI(**kwargs)
        return ChatOpenAI(**kwargs)

    if provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as e:
            raise RuntimeError("langchain-anthropic is required for provider=anthropic") from e
        kwargs: dict[str, object] = {"model": model, "temperature": temperature}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
            try:
                return ChatAnthropic(**kwargs)
            except TypeError:
                if "api_key" in kwargs:
                    kwargs.pop("api_key", None)
                    kwargs["anthropic_api_key"] = api_key
                kwargs.pop("base_url", None)
                kwargs["anthropic_api_url"] = base_url
                return ChatAnthropic(**kwargs)
        if api_key:
            try:
                return ChatAnthropic(**kwargs)
            except TypeError:
                kwargs.pop("api_key", None)
                kwargs["anthropic_api_key"] = api_key
                return ChatAnthropic(**kwargs)
        return ChatAnthropic(**kwargs)

    raise ValueError(f"Unsupported provider: {provider}")


async def complete_text(model: BaseChatModel, system: str, user: str) -> str:
    """使用同一模型做非工具型补全（例如 compaction 摘要）。"""
    # 用同一个 model 做摘要，能保持语气和领域偏好一致。
    out = await model.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
    if isinstance(out, AIMessage):
        content = out.content
    else:
        content = str(out)

    if isinstance(content, str):
        return content.strip()
    return str(content).strip()
