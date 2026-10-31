try:
    from openai import OpenAI  # type: ignore
except ImportError:  # pragma: no cover
    # Fallback stub for environments without the OpenAI package.
    class OpenAI:  # pylint: disable=too-few-public-methods
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "OpenAI package is not installed. Install it to use NebiusProvider, "
                "or mock the provider for testing."
            )


from app.config import Settings
from app.providers.base import ModelProvider


class NebiusProvider(ModelProvider):
    def __init__(self, settings: Settings):
        if not settings.nebius_api_key:
            raise ValueError(
                "NEBIUS_API_KEY is missing. Set the real key in your local .env file; "
                "never place credentials in .env.example."
            )

        self._model = settings.nebius_model
        self._client = OpenAI(
            api_key=settings.nebius_api_key,
            base_url=settings.nebius_base_url,
            timeout=settings.nebius_timeout_seconds,
        )

    @property
    def provider_name(self) -> str:
        return "nebius"

    @property
    def model_name(self) -> str:
        return self._model

    def complete(self, message: str, *, enable_thinking: bool = True) -> str:
        completion = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": "You are the reasoning provider for Jiraya Personal Assistant. Follow the user's instruction precisely.",
                },
                {"role": "user", "content": message},
            ],
            temperature=0,
            max_tokens=256,
            extra_body={
                "chat_template_kwargs": {
                    "enable_thinking": enable_thinking,
                }
            },
        )
        content = completion.choices[0].message.content
        if not content:
            raise RuntimeError("Nebius returned an empty completion.")
        return content.strip()
