from abc import ABC, abstractmethod


class ModelProvider(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def model_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def complete(self, message: str, *, enable_thinking: bool = True) -> str:
        raise NotImplementedError

    def complete_json(self, message: str) -> str:
        """Bounded structured extraction; providers may allow a larger response."""
        return self.complete(message, enable_thinking=False)
