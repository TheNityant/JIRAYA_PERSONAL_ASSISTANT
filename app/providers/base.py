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
    def complete(self, message: str) -> str:
        raise NotImplementedError
