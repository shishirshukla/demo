from abc import ABC, abstractmethod

from trading_system.models import Order


class BrokerBase(ABC):
    @abstractmethod
    def submit(self, order: Order):
        """Accept only a sized, risk-approved order from portfolio orchestration."""
